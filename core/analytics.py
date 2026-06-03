"""
core/analytics.py
Gold price buy/sell signal engine for GoldTracker.

Signals
-------
  S1  Price vs 7-day MA          (0–30 pts)
  S2  Price vs 30-day MA         (0–30 pts)
  S3  Momentum                   (0–25 pts)
  S4  Volatility                 (0–15 pts)
  ──  Time-of-day modifier       (−13 to +5 pts)
  ──  Retail-premium modifier    (−10 to +8 pts)
  ──  Clamp to [0, 100]
"""

from __future__ import annotations

import bisect
import datetime
import logging
import statistics
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

# ---------------------------------------------------------------------------
# Path bootstrap — only needed when running this file directly as a script.
# In production the package is installed / PYTHONPATH is set externally.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import get_price_history

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ── CONFIG ─────────────────────────────────────────────────────────────────
# All magic numbers live here.  Change once, applies everywhere.
# ---------------------------------------------------------------------------

# Signal weights (must sum to 100 for a clean score ceiling)
WEIGHT_MA7        = 30
WEIGHT_MA30       = 30
WEIGHT_MOMENTUM   = 25
WEIGHT_VOLATILITY = 15

# MA thresholds (% deviation from moving average)
MA_HOT_UPPER   =  3.0   # > this → overpriced
MA_WARM_UPPER  =  1.0
MA_COOL_LOWER  = -1.0
MA_DIP_LOWER   = -3.0   # < this → strong dip

# Momentum thresholds (% per period)
MOM_HOT_UPPER  =  0.5
MOM_WARM_UPPER =  0.1
MOM_COOL_LOWER = -0.1
MOM_DIP_LOWER  = -0.5

# Volatility thresholds (std-dev of price, in ₹/gram)
VOL_HIGH   = 300
VOL_MED    = 150
VOL_LOW    = 50

# Retail-premium thresholds (% deviation from 30-day avg premium)
PREMIUM_THRESHOLDS = [-20, -10, 10, 20]

# Buy-label thresholds
BUY_LABEL_GREAT = 75
BUY_LABEL_GOOD  = 55
BUY_LABEL_WAIT  = 35

# Sell-label thresholds (applied to sell_score = 100 − buy_score)
SELL_LABEL_GREAT = 75
SELL_LABEL_GOOD  = 55
SELL_LABEL_HOLD  = 35

# Minimum premium history required for divergence signal
MIN_PREMIUM_HISTORY = 7

# DB look-back window
HISTORY_DAYS = 30

# Simple in-process cache TTL (seconds)
_CACHE_TTL_SECONDS = 60


# ---------------------------------------------------------------------------
# ── RESULT TYPES ───────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@dataclass
class SignalResult:
    """Score + human-readable reason for a single signal."""
    score:  int
    reason: str


@dataclass
class PremiumResult:
    """Output of the retail-premium divergence calculation."""
    modifier:    int
    label:       str
    reason:      str
    current:     Optional[float] = None
    current_pct: Optional[float] = None
    avg_7d:      Optional[float] = None
    avg_30d:     Optional[float] = None
    deviation_pct: Optional[float] = None


@dataclass
class MarketContext:
    """Time-of-day and day-of-week context."""
    session:      str
    modifier:     int
    reason:       str
    time_ist:     str
    day:          str
    day_modifier: int


@dataclass
class AnalyticsResult:
    """Full output returned by run_analytics()."""
    # Indicators
    ma7:        Optional[float]
    ma30:       Optional[float]
    momentum:   Optional[float]
    volatility: Optional[float]

    # Scores
    buy_score:  int
    sell_score: int

    # Labels
    buy_label:  str
    sell_label: str

    # Explanation
    explanation: str

    # Context
    session:  str
    time_ist: str
    day:      str

    # Premium
    premium_label: str
    premium_stats: dict = field(default_factory=dict)

    trend_adx:       float = 0.0
    trend_label:     str   = ""
    trend_direction: str   = "neutral"

    def to_dict(self) -> dict:
        """Serialise to a plain dict (for backward-compat with callers)."""
        return {
            "ma7":           self.ma7,
            "ma30":          self.ma30,
            "momentum":      self.momentum,
            "volatility":    self.volatility,
            "buy_score":     self.buy_score,
            "sell_score":    self.sell_score,
            "buy_label":     self.buy_label,
            "sell_label":    self.sell_label,
            "explanation":   self.explanation,
            "session":       self.session,
            "time_ist":      self.time_ist,
            "day":           self.day,
            "premium_label": self.premium_label,
            "premium_stats": self.premium_stats,
            "trend_adx":       self.trend_adx,
            "trend_label":     self.trend_label,
            "trend_direction": self.trend_direction,
        }


# ---------------------------------------------------------------------------
# ── TIME / MARKET CONTEXT ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------

# IST = UTC + 5:30  (India Standard Time has no DST)
_IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# (time-modifier, session-label, session-reason)
_SESSION_WINDOWS: list[tuple[tuple[float, float], int, str, str]] = [
    ((0.0,  9.0),  -8, "off-hours",  "off-hours — low volume, prices may be stale"),
    ((9.0,  9.5),  -5, "mcx-open",   "MCX just opened — expect volatility"),
    ((23.0, 23.5), -5, "mcx-close",  "MCX closing soon — possible price swing"),
    ((19.5, 20.0), -5, "us-open",    "US market opening — international price moving"),
    ((23.5, 24.0), -8, "off-hours",  "off-hours — low volume, prices may be stale"),
]

_DAY_PATTERNS: dict[int, tuple[int, str]] = {
    0: (-3, "Monday — weekend gap effect, prices often lower"),
    1: (+2, "Tuesday — post-Monday recovery, typically stable"),
    2: (-2, "Wednesday — Fed announcement risk day"),
    3: (+2, "Thursday — typically stable mid-week"),
    4: (-3, "Friday — pre-weekend positioning, higher volatility"),
    5: (-5, "Saturday — MCX closed, international prices only"),
    6: (-8, "Sunday — markets closed, prices may be stale"),
}


def get_market_context(*, _now: Optional[datetime.datetime] = None) -> MarketContext:
    """
    Return the current market-session context based on IST time.

    Parameters
    ----------
    _now:
        Inject a datetime for testing.  Production code leaves this as None.
    """
    now_ist: datetime.datetime = _now or datetime.datetime.now(_IST_OFFSET)
    hour    = now_ist.hour
    minute  = now_ist.minute
    t       = hour + minute / 60.0          # decimal hour, e.g. 9.5 = 09:30

    in_mcx   = 9.0 <= t <= 23.5
    in_us    = t >= 19.5 or t <= 2.0

    # Determine session + base modifier
    session  = "normal"
    modifier = 0
    reason   = ""

    for (lo, hi), mod, sess, sess_reason in _SESSION_WINDOWS:
        if lo <= t < hi:
            modifier = mod
            session  = sess
            reason   = sess_reason
            break
    else:
        # Not in any special window
        if t < 9.0 or t > 23.5:
            modifier, session, reason = -8, "off-hours", "off-hours — low volume, prices may be stale"
        elif in_mcx and in_us:
            modifier, session, reason = +5, "peak",      "peak hours — MCX and US markets both active"
        elif in_mcx:
            modifier, session, reason = +3, "mcx",       "MCX active — signal reliable"
        elif in_us:
            modifier, session, reason = +2, "us",        "US market active — spot price moving"

    # Day-of-week modifier
    day_modifier, day_reason = _DAY_PATTERNS.get(now_ist.weekday(), (0, ""))
    modifier += day_modifier
    if day_reason:
        reason = f"{reason} · {day_reason}" if reason else day_reason

    return MarketContext(
        session      = session,
        modifier     = modifier,
        reason       = reason,
        time_ist     = now_ist.strftime("%H:%M IST"),
        day          = now_ist.strftime("%A"),
        day_modifier = day_modifier,
    )


# ---------------------------------------------------------------------------
# ── INDICATOR COMPUTATION ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def compute_ma(prices: list[float], window: int) -> Optional[float]:
    """Simple moving average of the last *window* prices."""
    if len(prices) < window:
        return None
    window_prices = prices[-window:]
    return round(sum(window_prices) / window, 2)


def compute_momentum(prices: list[float], window: int = 5) -> Optional[float]:
    """
    Average of period-over-period % changes over the last *window* periods.
    Requires at least window+1 data points.
    """
    if len(prices) < window + 1:
        return None
    window_slice = prices[-(window + 1):]
    changes = [
        ((window_slice[i] - window_slice[i - 1]) / window_slice[i - 1]) * 100
        for i in range(1, len(window_slice))
        if window_slice[i - 1] != 0          # guard against zero-price rows
    ]
    if not changes:
        return None
    return round(sum(changes) / len(changes), 4)


def compute_volatility(prices: list[float], window: int = 10) -> Optional[float]:
    """Standard deviation of the last *window* prices."""
    if len(prices) < window:
        return None
    try:
        return round(statistics.stdev(prices[-window:]), 2)
    except statistics.StatisticsError as exc:
        logger.warning("compute_volatility: stdev failed — %s", exc)
        return None

# ---------------------------------------------------------------------------
# ── TREND STRENGTH (Simplified ADX) ───────────────────────────────────────
# ---------------------------------------------------------------------------

# Minimum data points needed for a meaningful ADX calculation
_ADX_MIN_PERIODS = 14

# ADX strength thresholds → (label, buy_modifier, sell_modifier)
_ADX_OUTCOMES: list[tuple[float, str, int]] = [
    # (threshold, label, score_modifier)
    # bisect_left on thresholds maps index → outcome
    (25.0, "no trend — signal less reliable",          -5),
    (50.0, "moderate trend — signal moderately reliable", 0),
    (75.0, "strong trend — high conviction signal",    +5),
]
_ADX_THRESHOLDS = [o[0] for o in _ADX_OUTCOMES]


@dataclass
class TrendStrengthResult:
    """Output of the trend strength calculation."""
    adx:          float
    label:        str
    modifier:     int
    direction:    str   # 'bullish' | 'bearish' | 'neutral'
    reasoning:    str


def compute_adx(prices: list[float], period: int = 14) -> Optional[float]:
    """
    Compute a simplified ADX (Average Directional Index).

    Standard ADX requires high/low/close candles.
    We approximate using close-only data by treating the absolute
    period-over-period change as a proxy for True Range.

    Parameters
    ----------
    prices : list of closing prices, oldest first
    period : smoothing period (default 14, industry standard)

    Returns
    -------
    ADX value 0-100, or None if insufficient data.
    """
    if len(prices) < period * 2:
        return None

    # Step 1 — Directional movements
    # +DM = upward movement, -DM = downward movement
    plus_dm:  list[float] = []
    minus_dm: list[float] = []

    for i in range(1, len(prices)):
        move = prices[i] - prices[i - 1]
        plus_dm.append(max(move, 0.0))
        minus_dm.append(max(-move, 0.0))

    # Step 2 — True Range proxy (absolute price change)
    tr = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]

    def _smooth(values: list[float], n: int) -> list[float]:
        """Wilder smoothing — industry standard for ADX."""
        if len(values) < n:
            return []
        smoothed = [sum(values[:n])]
        for v in values[n:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / n + v)
        return smoothed

    # Step 3 — Smooth all three series
    smooth_tr       = _smooth(tr,       period)
    smooth_plus_dm  = _smooth(plus_dm,  period)
    smooth_minus_dm = _smooth(minus_dm, period)

    if not smooth_tr:
        return None

    # Step 4 — Directional indicators
    # Guard against division by zero
    plus_di  = [
        100 * p / t if t > 0 else 0.0
        for p, t in zip(smooth_plus_dm, smooth_tr)
    ]
    minus_di = [
        100 * m / t if t > 0 else 0.0
        for m, t in zip(smooth_minus_dm, smooth_tr)
    ]

    # Step 5 — DX then ADX
    dx_values: list[float] = []
    for p, m in zip(plus_di, minus_di):
        di_sum  = p + m
        di_diff = abs(p - m)
        dx_values.append(100 * di_diff / di_sum if di_sum > 0 else 0.0)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[-period:]) / period
    return round(adx, 2)


def score_trend_strength(
    prices:   list[float],
    momentum: Optional[float],
) -> TrendStrengthResult:
    """
    Compute trend strength using ADX and classify it.

    Parameters
    ----------
    prices   : historical price list, oldest first
    momentum : pre-computed momentum value (reused, not recomputed)

    Returns
    -------
    TrendStrengthResult with ADX score, label, modifier and reasoning.
    """
    adx = compute_adx(prices)

    if adx is None:
        return TrendStrengthResult(
            adx       = 0.0,
            label     = "insufficient data for trend strength",
            modifier  = 0,
            direction = "neutral",
            reasoning = "trend strength unavailable — need 28+ data points",
        )

    # Map ADX to outcome using bisect
    idx            = bisect.bisect_left(_ADX_THRESHOLDS, adx)
    idx            = min(idx, len(_ADX_OUTCOMES) - 1)
    _, label, modifier = _ADX_OUTCOMES[idx]

    # Direction from momentum
    if momentum is None:
        direction = "neutral"
    elif momentum > 0.1:
        direction = "bullish"
    elif momentum < -0.1:
        direction = "bearish"
    else:
        direction = "neutral"

    # Flip modifier: strong bearish trend = good buy signal
    # Strong bullish trend = good sell signal
    if direction == "bearish":
        modifier = +abs(modifier)   # strong downtrend → buy confidence boost
    elif direction == "bullish":
        modifier = -abs(modifier)   # strong uptrend → caution for buyers

    # Build reasoning
    adx_label = (
        "weak"     if adx < 25 else
        "moderate" if adx < 50 else
        "strong"   if adx < 75 else
        "very strong"
    )

    reasoning = f"{adx_label} {direction} trend (ADX {adx:.1f})"

    return TrendStrengthResult(
        adx       = adx,
        label     = label,
        modifier  = modifier,
        direction = direction,
        reasoning = reasoning,
    )

def compute_premium_history(history: list[dict]) -> list[float]:
    """
    For each history row where both spot and retail prices exist,
    compute the absolute premium (retail − spot).
    """
    premiums: list[float] = []
    for row in history:
        spot   = row.get("price_24k")
        retail = row.get("retail_price")
        if spot and retail and spot > 0:
            premiums.append(retail - spot)
    return premiums


# ---------------------------------------------------------------------------
# ── SCORING FUNCTIONS ──────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def score_vs_ma7(current_price: float, ma7: Optional[float]) -> SignalResult:
    """Score current price vs 7-day MA (max {WEIGHT_MA7} pts)."""
    if not ma7 or not current_price:
        return SignalResult(WEIGHT_MA7 // 2, "insufficient data for 7-day average")

    diff_pct = ((current_price - ma7) / ma7) * 100

    if diff_pct > MA_HOT_UPPER:
        return SignalResult(0,               f"price is {diff_pct:.1f}% above 7-day average — overpriced short term")
    if diff_pct > MA_WARM_UPPER:
        return SignalResult(8,               f"price is {diff_pct:.1f}% above 7-day average")
    if diff_pct >= MA_COOL_LOWER:
        return SignalResult(15,              "price is near 7-day average (±1%)")
    if diff_pct >= MA_DIP_LOWER:
        return SignalResult(23,              f"price is {abs(diff_pct):.1f}% below 7-day average")
    return     SignalResult(WEIGHT_MA7,      f"price is {abs(diff_pct):.1f}% below 7-day average — good dip")


def score_vs_ma30(current_price: float, ma30: Optional[float]) -> SignalResult:
    """Score current price vs 30-day MA (max {WEIGHT_MA30} pts)."""
    if not ma30 or not current_price:
        return SignalResult(WEIGHT_MA30 // 2, "insufficient data for 30-day average")

    diff_pct = ((current_price - ma30) / ma30) * 100

    if diff_pct > MA_HOT_UPPER:
        return SignalResult(0,               f"price is {diff_pct:.1f}% above 30-day average — elevated")
    if diff_pct > MA_WARM_UPPER:
        return SignalResult(8,               f"price is {diff_pct:.1f}% above 30-day average")
    if diff_pct >= MA_COOL_LOWER:
        return SignalResult(15,              "price is near 30-day average")
    if diff_pct >= MA_DIP_LOWER:
        return SignalResult(23,              f"price is {abs(diff_pct):.1f}% below 30-day average")
    return     SignalResult(WEIGHT_MA30,     f"price is {abs(diff_pct):.1f}% below 30-day average — historically low")


def score_momentum(momentum: Optional[float]) -> SignalResult:
    """Score recent price momentum (max {WEIGHT_MOMENTUM} pts)."""
    if momentum is None:
        return SignalResult(WEIGHT_MOMENTUM // 2, "trend direction unavailable")

    if momentum > MOM_HOT_UPPER:
        return SignalResult(0,                "price rising fast — consider waiting")
    if momentum > MOM_WARM_UPPER:
        return SignalResult(8,                "price trending upward")
    if momentum >= MOM_COOL_LOWER:
        return SignalResult(15,               "price is stable")
    if momentum >= MOM_DIP_LOWER:
        return SignalResult(20,               "downward trend detected — potential buy window")
    return     SignalResult(WEIGHT_MOMENTUM,  "price falling sharply — strong buy signal")


def score_volatility(volatility: Optional[float]) -> SignalResult:
    """Score price volatility (max {WEIGHT_VOLATILITY} pts). Low vol = higher confidence."""
    if volatility is None:
        return SignalResult(WEIGHT_VOLATILITY // 2, "volatility unknown")

    if volatility > VOL_HIGH:
        return SignalResult(0,                "high volatility — signal less reliable")
    if volatility > VOL_MED:
        return SignalResult(5,                "moderate volatility")
    if volatility > VOL_LOW:
        return SignalResult(10,               "low-moderate volatility")
    return     SignalResult(WEIGHT_VOLATILITY, "price is stable — signal reliable")


def score_retail_divergence(
    current_spot:    Optional[float],
    current_retail:  Optional[float],
    premium_history: list[float],
) -> PremiumResult:
    """
    Compare the current retail premium against its 30-day historical average.

    This is an *additive modifier*, not a standalone signal.
    Range: −10 to +8 pts.
    """
    if not current_spot or not current_retail:
        return PremiumResult(modifier=0, label="neutral", reason="retail data unavailable")

    current_premium     = current_retail - current_spot
    current_premium_pct = (current_premium / current_spot) * 100

    if len(premium_history) < MIN_PREMIUM_HISTORY:
        return PremiumResult(
            modifier     = 0,
            label        = "neutral",
            reason       = f"retail premium ₹{current_premium:,.0f}/gram ({current_premium_pct:.1f}%) — insufficient history",
            current      = round(current_premium, 2),
            current_pct  = round(current_premium_pct, 2),
        )

    avg_7d  = sum(premium_history[-7:])  / 7
    avg_30d = sum(premium_history[-30:]) / min(30, len(premium_history))

    deviation_pct: float = ((current_premium - avg_30d) / avg_30d * 100) if avg_30d else 0.0

    # Map deviation → (modifier, label, reason) using bisect for clean thresholds
    _OUTCOMES = [
        (+8,  "compressed", "retail premium unusually low — potential discount buying opportunity"),
        (+4,  "low",        "retail premium below average — favourable buying conditions"),
        ( 0,  "normal",     "retail premium normal"),
        (-5,  "elevated",   "retail premium elevated"),
        (-10, "extreme",    "retail premium extremely high — jewellers pricing in significant price rise"),
    ]
    idx = bisect.bisect_left(PREMIUM_THRESHOLDS, deviation_pct)
    modifier, label, reason = _OUTCOMES[idx]

    return PremiumResult(
        modifier      = modifier,
        label         = label,
        reason        = reason,
        current       = round(current_premium, 2),
        current_pct   = round(current_premium_pct, 2),
        avg_7d        = round(avg_7d, 2),
        avg_30d       = round(avg_30d, 2),
        deviation_pct = round(deviation_pct, 2),
    )


# ---------------------------------------------------------------------------
# ── EXPLANATION / LABEL HELPERS ───────────────────────────────────────────
# ---------------------------------------------------------------------------

_NOISE_PHRASES = ("unavailable", "unknown", "insufficient")


def build_explanation(reasons: list[str]) -> str:
    """
    Join meaningful signal reasons into a human-readable sentence.
    Low-signal phrases are demoted rather than silently dropped.
    """
    meaningful = [r for r in reasons if not any(p in r for p in _NOISE_PHRASES)]
    if not meaningful:
        return "Not enough historical data yet — check back after a few hours"
    return " · ".join(meaningful)


def get_buy_label(score: int) -> str:
    if score >= BUY_LABEL_GREAT:
        return "PERFECT TIME TO BUY"
    if score >= BUY_LABEL_GOOD:
        return "GOOD TIME TO BUY"
    if score >= BUY_LABEL_WAIT:
        return "WAIT A BIT MORE"
    return "BAD TIME TO BUY"


def get_sell_label(sell_score: int) -> str:
    """
    Sell label derived from the *sell* score (100 − buy_score).
    Note: sell_score is passed in directly; the inversion happens in run_analytics().
    """
    if sell_score >= SELL_LABEL_GREAT:
        return "PERFECT TIME TO SELL"
    if sell_score >= SELL_LABEL_GOOD:
        return "GOOD TIME TO SELL"
    if sell_score >= SELL_LABEL_HOLD:
        return "HOLD FOR NOW"
    return "BAD TIME TO SELL"


# ---------------------------------------------------------------------------
# ── HISTORY LOADER (with simple time-based cache) ─────────────────────────
# ---------------------------------------------------------------------------

_history_cache: dict = {}   # {"data": [...], "fetched_at": datetime}


def _fetch_history() -> list[dict]:
    """
    Wrapper around get_price_history() with a 60-second in-process cache
    to avoid hammering the DB on rapid successive calls.
    """
    global _history_cache
    now = datetime.datetime.now(datetime.timezone.utc)
    cached_at: Optional[datetime.datetime] = _history_cache.get("fetched_at")

    if cached_at and (now - cached_at).total_seconds() < _CACHE_TTL_SECONDS:
        logger.debug("_fetch_history: serving from cache")
        return _history_cache["data"]

    try:
        data = get_price_history(days=HISTORY_DAYS)
    except Exception as exc:
        logger.error("_fetch_history: DB error — %s", exc, exc_info=True)
        return _history_cache.get("data", [])   # serve stale on failure

    _history_cache = {"data": data, "fetched_at": now}
    logger.debug("_fetch_history: loaded %d rows from DB", len(data))
    return data


# ---------------------------------------------------------------------------
# ── MAIN ENTRY POINT ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def run_analytics(
    current_price: float,
    retail_price:  Optional[float] = None,
) -> AnalyticsResult:
    """
    Compute buy/sell signals for the given spot price.

    Parameters
    ----------
    current_price:
        Current 24K spot price in INR per gram.
    retail_price:
        Current retail (jewellery shop) price per gram, if available.

    Returns
    -------
    AnalyticsResult
        All indicators, scores, labels and explanations.
        Call .to_dict() for a plain dict if needed.
    """
    if not current_price or current_price <= 0:
        raise ValueError(f"run_analytics: invalid current_price={current_price!r}")

    # ── 1. Fetch & clean history ──────────────────────────────────────────
    history  = _fetch_history()
    prices: list[float] = [
        row["price_24k"] for row in history
        if row.get("price_24k") and row["price_24k"] > 0
    ]
    premium_history = compute_premium_history(history)

    logger.debug(
        "run_analytics: %d clean price rows, %d premium rows",
        len(prices), len(premium_history),
    )

    # ── 2. Compute indicators ─────────────────────────────────────────────
    ma7        = compute_ma(prices, 7)
    ma30       = compute_ma(prices, 30)
    momentum   = compute_momentum(prices)
    volatility = compute_volatility(prices)

    # ── 3. Score each signal ──────────────────────────────────────────────
    sig1  = score_vs_ma7(current_price, ma7)
    sig2  = score_vs_ma30(current_price, ma30)
    sig3  = score_momentum(momentum)
    sig4  = score_volatility(volatility)
    trend = score_trend_strength(prices, momentum)

    # ── 4. Time context ───────────────────────────────────────────────────
    time_ctx = get_market_context()

    # ── 5. Retail-premium divergence modifier ─────────────────────────────
    premium = score_retail_divergence(current_price, retail_price, premium_history)

    # ── 6. Assemble score ─────────────────────────────────────────────────
    raw_score  = sig1.score + sig2.score + sig3.score + sig4.score
    buy_score  = max(0, min(100,
        raw_score
        + time_ctx.modifier
        + premium.modifier
        + trend.modifier          # ← add this
    ))
    sell_score = 100 - buy_score

    logger.info(
        "run_analytics: raw=%d time_mod=%d premium_mod=%d → buy=%d sell=%d  [%s]",
        raw_score, time_ctx.modifier, premium.modifier,
        buy_score, sell_score, time_ctx.time_ist,
    )

    # ── 7. Build explanation ──────────────────────────────────────────────
    reasons = [sig1.reason, sig2.reason, sig3.reason, sig4.reason]
    if time_ctx.reason:
        reasons.append(time_ctx.reason)
    if premium.reason and premium.label not in ("neutral",):
        reasons.append(premium.reason)
    if trend.reasoning and "unavailable" not in trend.reasoning:
        reasons.append(trend.reasoning)

    explanation = build_explanation(reasons)

    # ── 8. Return structured result ───────────────────────────────────────
    return AnalyticsResult(
        ma7           = ma7,
        ma30          = ma30,
        momentum      = momentum,
        volatility    = volatility,
        buy_score     = buy_score,
        sell_score    = sell_score,
        buy_label     = get_buy_label(buy_score),
        sell_label    = get_sell_label(sell_score),
        explanation   = explanation,
        session       = time_ctx.session,
        time_ist      = time_ctx.time_ist,
        day           = time_ctx.day,
        premium_label  = premium.label,
        premium_stats  = {
            "current_premium":     premium.current,
            "current_premium_pct": premium.current_pct,
            "avg_premium_7d":      premium.avg_7d,
            "avg_premium_30d":     premium.avg_30d,
            "deviation_pct":       premium.deviation_pct,
        },
        trend_adx      = trend.adx,
        trend_label    = trend.label,
        trend_direction= trend.direction,
    )


# ---------------------------------------------------------------------------
# ── CLI smoke-test ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s — %(message)s")

    TEST_PRICE  = 13_920.0
    TEST_RETAIL = 14_350.0

    result = run_analytics(TEST_PRICE, retail_price=TEST_RETAIL)

    WIDTH = 50
    print("\n" + "=" * WIDTH)
    print("ANALYTICS RESULT")
    print("=" * WIDTH)
    print(f"{'MA7':<20}: {result.ma7}")
    print(f"{'MA30':<20}: {result.ma30}")
    print(f"{'Momentum':<20}: {result.momentum}")
    print(f"{'Volatility':<20}: {result.volatility}")
    print(f"{'Session':<20}: {result.session} ({result.time_ist}, {result.day})")
    print(f"{'Premium label':<20}: {result.premium_label}")
    print(f"{'Buy Score':<20}: {result.buy_score}/100")
    print(f"{'Sell Score':<20}: {result.sell_score}/100")
    print(f"{'Buy Signal':<20}: {result.buy_label}")
    print(f"{'Sell Signal':<20}: {result.sell_label}")
    print(f"{'Explanation':<20}: {result.explanation}")
    print(f"{'Trend ADX':<20}: {result.trend_adx}")
    print(f"{'Trend Label':<20}: {result.trend_label}")
    print(f"{'Trend Direction':<20}: {result.trend_direction}")
    print("=" * WIDTH)