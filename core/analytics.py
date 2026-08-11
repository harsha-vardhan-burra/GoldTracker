"""
core/analytics.py
Institutional Gold price buy/sell signal and reasoning engine for GoldTracker.

Signals & Weights (Total Core Weight = 100 pts)
------------------------------------------------
  S1  Price vs 7-day MA           (Weight: 25 pts)
  S2  Price vs 30-day MA          (Weight: 25 pts)
  S3  Momentum (5-period)         (Weight: 20 pts)
  S4  Support / Resistance        (Weight: 15 pts)
  S5  Retail Premium Divergence   (Weight: 15 pts)
  ──  Trend Strength (ADX 14)     (Confidence & Directional Conviction)
  ──  Volatility & Noise Filter   (Signal Quality & Confidence Penalty)
  ──  Market Session & Time       (Execution Timing & Reliability Factor)
"""

from __future__ import annotations

import math
import datetime
import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Path bootstrap — only needed when running this file directly as a script.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import os

    def _project_root() -> str:
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    sys.path.append(_project_root())

from database.db_manager import get_price_history

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ── CONFIGURATION & WEIGHTS ────────────────────────────────────────────────
# ---------------------------------------------------------------------------

# Signal Weights (Sum = 100 for clean mathematical normalization)
WEIGHT_MA7        = 25.0
WEIGHT_MA30       = 25.0
WEIGHT_MOMENTUM   = 20.0
WEIGHT_SR         = 15.0
WEIGHT_PREMIUM    = 15.0

# Buy-label thresholds (0-100 scale)
BUY_LABEL_GREAT = 75
BUY_LABEL_GOOD  = 55
BUY_LABEL_WAIT  = 35

# Sell-label thresholds (applied to sell_score = 100 − buy_score)
SELL_LABEL_GREAT = 75
SELL_LABEL_GOOD  = 55
SELL_LABEL_HOLD  = 35

# Minimum required history depth
MIN_PREMIUM_HISTORY = 7
HISTORY_DAYS = 30

# Simple in-process cache TTL (seconds)
_CACHE_TTL_SECONDS = 60


# ---------------------------------------------------------------------------
# ── STRUCTURED RESULT DATACLASSES ──────────────────────────────────────────
# ---------------------------------------------------------------------------

@dataclass
class IndicatorContribution:
    """
    Detailed institutional breakdown for a single technical signal.
    """
    name: str                  # Display name, e.g. "30-Day Moving Average"
    weight: float              # Allocated points ceiling
    raw_value: Optional[float] # Raw calculated value (e.g. 13850.0)
    raw_fmt: str               # Formatted string representation
    score: float               # Actual buy points awarded (0.0 to weight)
    normalized_score: float    # Bounded [-1.0, +1.0] (+1 = Max Buy, -1 = Max Sell)
    reason: str                # Institutional natural language reasoning
    confidence: str            # "Very High" | "High" | "Medium" | "Low" | "Very Low"
    influence: str             # "Supports BUY" | "Supports SELL" | "Neutral" | "Ignored"
    status: str                # "active" | "insufficient_data" | "weakened_by_noise" | "overridden"


@dataclass
class ConflictResolution:
    """
    Explicit reasoning when technical indicators contradict each other.
    """
    has_conflict: bool
    dominant_factor: str
    opposing_factor: str
    resolution_reason: str


@dataclass
class PremiumResult:
    """Output of retail-premium divergence analysis."""
    modifier: float
    label: str
    reason: str
    current: Optional[float] = None
    current_pct: Optional[float] = None
    avg_7d: Optional[float] = None
    avg_30d: Optional[float] = None
    deviation_pct: Optional[float] = None


@dataclass
class MarketContext:
    """Session timing, market activity, and reliability context."""
    session: str
    modifier: float
    reason: str
    time_ist: str
    day: str
    day_modifier: float
    reliability_factor: float  # 0.5 to 1.0


@dataclass
class TrendStrengthResult:
    """Output of trend strength (ADX) calculation."""
    adx: float
    label: str
    modifier: float
    direction: str  # 'bullish' | 'bearish' | 'neutral'
    reasoning: str


@dataclass
class SupportResistanceResult:
    """Output of price cluster support/resistance calculation."""
    support: Optional[float]
    resistance: Optional[float]
    nearest_support: Optional[float]
    nearest_resist: Optional[float]
    at_support: bool
    at_resistance: bool
    modifier: float
    reasoning: str


@dataclass
class AnalyticsResult:
    """
    Complete output returned by run_analytics().
    Backward-compatible with original fields while exposing full institutional breakdown.
    """
    # Core indicators
    ma7: Optional[float]
    ma30: Optional[float]
    momentum: Optional[float]
    volatility: Optional[float]

    # Scores
    buy_score: int
    sell_score: int

    # Labels
    buy_label: str
    sell_label: str

    # Explanations & Conflict Resolution
    explanation: str
    conflict_resolution: Optional[ConflictResolution] = None

    # Market Session Context
    session: str = "normal"
    time_ist: str = ""
    day: str = ""

    # Premium stats
    premium_label: str = "neutral"
    premium_stats: dict = field(default_factory=dict)

    # Technical Levels & ADX
    trend_adx: float = 0.0
    trend_label: str = ""
    trend_direction: str = "neutral"
    support: Optional[float] = None
    resistance: Optional[float] = None
    nearest_support: Optional[float] = None
    nearest_resist: Optional[float] = None
    at_support: bool = False
    at_resistance: bool = False

    # Confidence Metrics (5-tier: Very High, High, Medium, Low, Very Low)
    confidence: int = 0
    confidence_label: str = "Low"

    # Per-indicator attribution map
    contributions: Dict[str, IndicatorContribution] = field(default_factory=dict)
    data_quality_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary for caller compatibility."""
        return {
            "ma7": self.ma7,
            "ma30": self.ma30,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "buy_score": self.buy_score,
            "sell_score": self.sell_score,
            "buy_label": self.buy_label,
            "sell_label": self.sell_label,
            "explanation": self.explanation,
            "session": self.session,
            "time_ist": self.time_ist,
            "day": self.day,
            "premium_label": self.premium_label,
            "premium_stats": self.premium_stats,
            "trend_adx": self.trend_adx,
            "trend_label": self.trend_label,
            "trend_direction": self.trend_direction,
            "support": self.support,
            "resistance": self.resistance,
            "nearest_support": self.nearest_support,
            "nearest_resist": self.nearest_resist,
            "at_support": self.at_support,
            "at_resistance": self.at_resistance,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "contributions": {
                k: {
                    "name": v.name,
                    "weight": v.weight,
                    "raw_value": v.raw_value,
                    "raw_fmt": v.raw_fmt,
                    "score": v.score,
                    "normalized_score": v.normalized_score,
                    "reason": v.reason,
                    "confidence": v.confidence,
                    "influence": v.influence,
                    "status": v.status,
                }
                for k, v in self.contributions.items()
            },
            "conflict_resolution": (
                {
                    "has_conflict": self.conflict_resolution.has_conflict,
                    "dominant_factor": self.conflict_resolution.dominant_factor,
                    "opposing_factor": self.conflict_resolution.opposing_factor,
                    "resolution_reason": self.conflict_resolution.resolution_reason,
                }
                if self.conflict_resolution
                else None
            ),
            "data_quality_notes": self.data_quality_notes,
        }


# ---------------------------------------------------------------------------
# ── HELPER MATHEMATICAL NORMALIZATION FUNCTIONS ───────────────────────────
# ---------------------------------------------------------------------------

def _clamp(val: float, min_val: float = -1.0, max_val: float = +1.0) -> float:
    """Clamp float to [min_val, max_val]."""
    return max(min_val, min(max_val, val))


def _linear_interpolate(x: float, x_min: float, x_max: float, y_min: float, y_max: float) -> float:
    """Continuous linear interpolation between (x_min, y_min) and (x_max, y_max)."""
    if x_max == x_min:
        return y_min
    t = (x - x_min) / (x_max - x_min)
    t_clamped = max(0.0, min(1.0, t))
    return y_min + t_clamped * (y_max - y_min)


# ---------------------------------------------------------------------------
# ── TIME / MARKET CONTEXT ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------

_IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_SESSION_WINDOWS: list[tuple[tuple[float, float], float, str, str, float]] = [
    # ((start_hour, end_hour), modifier, session_code, session_reason, reliability_factor)
    ((0.0, 9.0), -4.0, "off-hours", "off-hours session — low volume, prices may reflect overnight lag", 0.6),
    ((9.0, 9.5), -2.0, "mcx-open", "MCX market open — opening price discovery and initial volatility", 0.8),
    ((19.5, 20.0), -2.0, "us-open", "US market opening window — heightened international liquidity", 0.85),
    ((23.0, 23.5), -2.0, "mcx-close", "MCX closing window — potential end-of-day position unwinding", 0.8),
    ((23.5, 24.0), -4.0, "off-hours", "off-hours session — low volume, prices may reflect overnight lag", 0.6),
]

_DAY_PATTERNS: dict[int, tuple[float, str]] = {
    0: (-2.0, "Monday weekend gap adjustment"),
    1: (+1.0, "Tuesday post-open price stabilization"),
    2: (-1.0, "Wednesday mid-week macroeconomic release risk"),
    3: (+1.0, "Thursday institutional position stability"),
    4: (-2.0, "Friday pre-weekend position squaring"),
    5: (-4.0, "Saturday MCX closure — static international spot rate"),
    6: (-4.0, "Sunday market closure — static international spot rate"),
}


def get_market_context(*, _now: Optional[datetime.datetime] = None) -> MarketContext:
    """Return current market session context and execution timing metrics in IST."""
    now_ist: datetime.datetime = _now or datetime.datetime.now(_IST_OFFSET)
    hour = now_ist.hour
    minute = now_ist.minute
    t = hour + minute / 60.0

    in_mcx = 9.0 <= t <= 23.5
    in_us = t >= 19.5 or t <= 2.0

    session = "normal"
    modifier = 0.0
    reason = "standard market session"
    reliability = 1.0

    for (lo, hi), mod, sess, sess_reason, rel in _SESSION_WINDOWS:
        if lo <= t < hi:
            modifier = mod
            session = sess
            reason = sess_reason
            reliability = rel
            break
    else:
        if t < 9.0 or t > 23.5:
            modifier, session, reason, reliability = -4.0, "off-hours", "off-hours session — low volume, prices may reflect overnight lag", 0.6
        elif in_mcx and in_us:
            modifier, session, reason, reliability = +2.0, "peak", "peak market window — active liquidity on MCX and international exchanges", 1.0
        elif in_mcx:
            modifier, session, reason, reliability = +1.0, "mcx", "MCX active trading session — price discovery reliable", 0.95
        elif in_us:
            modifier, session, reason, reliability = +1.0, "us", "US active trading session — spot gold moving on global volume", 0.9

    day_mod, day_reason = _DAY_PATTERNS.get(now_ist.weekday(), (0.0, ""))
    modifier += day_mod
    if day_reason:
        reason = f"{reason} · {day_reason}" if reason else day_reason

    return MarketContext(
        session=session,
        modifier=modifier,
        reason=reason,
        time_ist=now_ist.strftime("%H:%M IST"),
        day=now_ist.strftime("%A"),
        day_modifier=day_mod,
        reliability_factor=reliability,
    )


# ---------------------------------------------------------------------------
# ── CORE MATHEMATICAL INDICATOR CALCULATIONS ──────────────────────────────
# ---------------------------------------------------------------------------

def compute_ma(prices: list[float], window: int) -> Optional[float]:
    """Simple moving average of the last *window* prices."""
    if len(prices) < window:
        return None
    return round(sum(prices[-window:]) / window, 2)


def compute_momentum(prices: list[float], window: int = 5) -> Optional[float]:
    """Average period-over-period percentage change over the last *window* periods."""
    if len(prices) < window + 1:
        return None
    window_slice = prices[-(window + 1):]
    changes = [
        ((window_slice[i] - window_slice[i - 1]) / window_slice[i - 1]) * 100
        for i in range(1, len(window_slice))
        if window_slice[i - 1] > 0
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
    except statistics.StatisticsError:
        return None


def compute_adx(prices: list[float], period: int = 14) -> Optional[float]:
    """
    Compute simplified Average Directional Index (ADX) over close prices.
    Requires at least period * 2 data points for smooth calculation.
    """
    if len(prices) < period * 2:
        return None

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr: list[float] = []

    for i in range(1, len(prices)):
        move = prices[i] - prices[i - 1]
        plus_dm.append(max(move, 0.0))
        minus_dm.append(max(-move, 0.0))
        tr.append(abs(move))

    def _smooth(values: list[float], n: int) -> list[float]:
        if len(values) < n:
            return []
        smoothed = [sum(values[:n])]
        for v in values[n:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / n + v)
        return smoothed

    smooth_tr = _smooth(tr, period)
    smooth_plus_dm = _smooth(plus_dm, period)
    smooth_minus_dm = _smooth(minus_dm, period)

    if not smooth_tr:
        return None

    dx_values: list[float] = []
    for p, m, t in zip(smooth_plus_dm, smooth_minus_dm, smooth_tr):
        if t <= 0:
            continue
        p_di = 100 * p / t
        m_di = 100 * m / t
        di_sum = p_di + m_di
        di_diff = abs(p_di - m_di)
        dx_values.append(100 * di_diff / di_sum if di_sum > 0 else 0.0)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[-period:]) / period
    return round(adx, 2)


def score_trend_strength(prices: list[float], momentum: Optional[float]) -> TrendStrengthResult:
    """Evaluate ADX trend strength and direction conviction."""
    adx = compute_adx(prices)
    if adx is None:
        return TrendStrengthResult(
            adx=0.0,
            label="insufficient data for ADX trend analysis",
            modifier=0.0,
            direction="neutral",
            reasoning="trend strength calculation pending — requires 28+ observations",
        )

    direction = "neutral"
    if momentum is not None:
        if momentum > 0.08:
            direction = "bullish"
        elif momentum < -0.08:
            direction = "bearish"

    if adx < 20.0:
        label = "weak trend — sideways consolidation"
        modifier = 0.0
    elif adx < 40.0:
        label = "moderate trend — reliable direction"
        modifier = 2.0 if direction == "bearish" else -2.0
    else:
        label = "strong trend — high directional conviction"
        modifier = 4.0 if direction == "bearish" else -4.0

    reasoning = f"ADX {adx:.1f} indicates {label} ({direction} posture)"
    return TrendStrengthResult(
        adx=adx,
        label=label,
        modifier=modifier,
        direction=direction,
        reasoning=reasoning,
    )


def compute_support_resistance(prices: list[float], current_price: float) -> SupportResistanceResult:
    """
    Identify support & resistance levels using dynamic ATR/percentage price clustering.
    Dynamic binning ensures reliability across any gold price scale.
    """
    _EMPTY = SupportResistanceResult(
        support=None,
        resistance=None,
        nearest_support=None,
        nearest_resist=None,
        at_support=False,
        at_resistance=False,
        modifier=0.0,
        reasoning="insufficient historical depth for support/resistance clustering",
    )

    if len(prices) < 15 or not current_price or current_price <= 0:
        return _EMPTY

    # Stable bin width based on historical median price (prevents bin boundary jitter)
    base_ref = statistics.median(prices) if prices else current_price
    bin_width = max(20.0, base_ref * 0.005)

    bin_counts: dict[float, int] = {}
    for price in prices:
        bin_floor = round(price / bin_width) * bin_width
        bin_counts[bin_floor] = bin_counts.get(bin_floor, 0) + 1

    if not bin_counts:
        return _EMPTY

    avg_visits = sum(bin_counts.values()) / len(bin_counts)
    min_visits = max(2, avg_visits * 0.75)

    significant = sorted([p for p, count in bin_counts.items() if count >= min_visits])
    if not significant:
        significant = sorted(bin_counts.keys())

    support_levels = [p for p in significant if p <= current_price]
    resistance_levels = [p for p in significant if p >= current_price]

    nearest_support = max(support_levels) if support_levels else None
    nearest_resist = min(resistance_levels) if resistance_levels else None

    strongest_support = max(support_levels, key=lambda p: bin_counts.get(p, 0)) if support_levels else None
    strongest_resist = max(resistance_levels, key=lambda p: bin_counts.get(p, 0)) if resistance_levels else None

    # Proximity scale: 0.8% of spot price
    sigma = current_price * 0.008

    def _smooth_level_influence(d: float, s: float) -> float:
        """
        C1 smooth signed proximity influence:
        h(d) = sign(d) * (3*u^2 - 2*u^3) * 6.0 for u = min(1, |d|/s).
        Returns +6.0 for d >= s (above support), -6.0 for d <= -s (below resistance), 0.0 at d=0.
        """
        if s <= 0:
            return 0.0
        abs_d = abs(d)
        if abs_d >= s:
            return 6.0 if d > 0 else -6.0
        u = abs_d / s
        factor = 3.0 * (u ** 2) - 2.0 * (u ** 3)
        return (factor * 6.0) if d > 0 else (-factor * 6.0)

    # Base range position modifier
    base_modifier = 0.0
    if nearest_support and nearest_resist and nearest_resist > nearest_support:
        range_size = nearest_resist - nearest_support
        position = (current_price - nearest_support) / range_size
        base_modifier = (0.5 - max(0.0, min(1.0, position))) * 6.0

    # Calculate smooth proximity modifier
    d_sup = (current_price - nearest_support) if nearest_support is not None else 0.0
    d_res = (current_price - nearest_resist) if nearest_resist is not None else 0.0

    if nearest_support and nearest_resist and nearest_resist > nearest_support:
        mod_sup = _smooth_level_influence(d_sup, sigma)
        mod_res = _smooth_level_influence(d_res, sigma)
        modifier = max(-6.0, min(6.0, (mod_sup + mod_res) * 0.5))
    elif nearest_support is not None:
        modifier = _smooth_level_influence(d_sup, sigma)
    elif nearest_resist is not None:
        modifier = _smooth_level_influence(d_res, sigma)
    else:
        modifier = 0.0

    at_support = nearest_support is not None and abs(d_sup) <= sigma
    at_resistance = nearest_resist is not None and abs(d_res) <= sigma

    parts: list[str] = []
    if at_support and nearest_support:
        parts.append(f"spot price (₹{current_price:,.0f}) is testing support near ₹{nearest_support:,.0f} (-{abs(d_sup)/current_price*100:.2f}% distance)")
    elif at_resistance and nearest_resist:
        parts.append(f"spot price (₹{current_price:,.0f}) is testing overhead resistance near ₹{nearest_resist:,.0f} (+{abs(d_res)/current_price*100:.2f}% distance)")
    elif nearest_support and nearest_resist:
        range_size = nearest_resist - nearest_support
        if range_size > 0:
            position = (current_price - nearest_support) / range_size
            parts.append(f"price is positioned at {position*100:.0f}% of the ₹{nearest_support:,.0f}–₹{nearest_resist:,.0f} trading range")
        else:
            parts.append(f"support at ₹{nearest_support:,.0f}, resistance at ₹{nearest_resist:,.0f}")
    elif nearest_support:
        parts.append(f"nearest support detected at ₹{nearest_support:,.0f}")
    elif nearest_resist:
        parts.append(f"nearest resistance detected at ₹{nearest_resist:,.0f}")

    reasoning = " · ".join(parts) if parts else "no distinct price clusters identified"

    return SupportResistanceResult(
        support=strongest_support,
        resistance=strongest_resist,
        nearest_support=nearest_support,
        nearest_resist=nearest_resist,
        at_support=at_support,
        at_resistance=at_resistance,
        modifier=modifier,
        reasoning=reasoning,
    )


def compute_premium_history(history: list[dict]) -> list[float]:
    """Extract retail premiums (retail_price - spot_price) from history."""
    premiums: list[float] = []
    for row in history:
        spot = row.get("price_24k")
        retail = row.get("retail_price")
        if spot and retail and spot > 0:
            premiums.append(retail - spot)
    return premiums


def score_retail_divergence(
    current_spot: Optional[float],
    current_retail: Optional[float],
    premium_history: list[float],
) -> PremiumResult:
    """Analyze current retail premium against historical 30-day baseline."""
    if not current_spot or not current_retail or current_spot <= 0:
        return PremiumResult(
            modifier=0.0,
            label="neutral",
            reason="retail jeweller pricing data unavailable for premium divergence analysis",
        )

    current_premium = current_retail - current_spot
    current_premium_pct = (current_premium / current_spot) * 100

    if len(premium_history) < MIN_PREMIUM_HISTORY:
        return PremiumResult(
            modifier=0.0,
            label="neutral",
            reason=f"retail premium is ₹{current_premium:,.0f}/g ({current_premium_pct:.1f}%) — baseline developing ({len(premium_history)}/{MIN_PREMIUM_HISTORY} days)",
            current=round(current_premium, 2),
            current_pct=round(current_premium_pct, 2),
        )

    avg_7d = sum(premium_history[-7:]) / 7.0
    avg_30d = sum(premium_history[-30:]) / min(30, len(premium_history))

    deviation_pct = ((current_premium - avg_30d) / avg_30d * 100) if avg_30d > 0 else 0.0

    # Continuous mapping: deviation_pct from -20% (Compressed) to +20% (Inflated)
    norm = _linear_interpolate(deviation_pct, -20.0, 20.0, +1.0, -1.0)
    modifier = norm * 5.0  # -5 to +5 modifier

    if deviation_pct < -15.0:
        label = "compressed"
        reason = f"retail premium (₹{current_premium:,.0f}/g) is compressed {abs(deviation_pct):.1f}% below 30-day average (₹{avg_30d:,.0f}/g), indicating jeweller discount pricing"
    elif deviation_pct < -5.0:
        label = "low"
        reason = f"retail premium (₹{current_premium:,.0f}/g) is {abs(deviation_pct):.1f}% below average (₹{avg_30d:,.0f}/g), creating favorable physical buy terms"
    elif deviation_pct <= 5.0:
        label = "normal"
        reason = f"retail premium (₹{current_premium:,.0f}/g) aligns with 30-day baseline average (₹{avg_30d:,.0f}/g)"
    elif deviation_pct <= 15.0:
        label = "elevated"
        reason = f"retail premium (₹{current_premium:,.0f}/g) is {deviation_pct:.1f}% above 30-day average, signaling strong local physical demand"
    else:
        label = "extreme"
        reason = f"retail premium (₹{current_premium:,.0f}/g) is inflated +{deviation_pct:.1f}% above average — jewellers pricing in rapid spot appreciation"

    return PremiumResult(
        modifier=round(modifier, 2),
        label=label,
        reason=reason,
        current=round(current_premium, 2),
        current_pct=round(current_premium_pct, 2),
        avg_7d=round(avg_7d, 2),
        avg_30d=round(avg_30d, 2),
        deviation_pct=round(deviation_pct, 2),
    )


# ---------------------------------------------------------------------------
# ── INSTITUTIONAL SIGNAL EVALUATORS ────────────────────────────────────────
# ---------------------------------------------------------------------------

def evaluate_ma7(current_price: float, ma7: Optional[float]) -> IndicatorContribution:
    """Evaluate 7-Day Moving Average with continuous linear normalization."""
    name = "7-Day Moving Average (MA7)"
    weight = WEIGHT_MA7

    if not ma7 or not current_price:
        return IndicatorContribution(
            name=name,
            weight=weight,
            raw_value=None,
            raw_fmt="N/A",
            score=weight * 0.5,
            normalized_score=0.0,
            reason="7-day moving average unavailable — insufficient price history",
            confidence="Low",
            influence="Ignored",
            status="insufficient_data",
        )

    diff_pct = ((current_price - ma7) / ma7) * 100.0
    # Continuous norm: diff_pct from -3.0% (Full Buy +1.0) to +3.0% (Full Sell -1.0)
    norm = _clamp(-diff_pct / 3.0, -1.0, +1.0)
    score = ((norm + 1.0) / 2.0) * weight

    if diff_pct < -2.0:
        reason = f"spot price (₹{current_price:,.2f}) is trading {abs(diff_pct):.2f}% below the 7-day average (₹{ma7:,.2f}), signaling a short-term mean-reversion discount"
        influence = "Supports BUY"
    elif diff_pct < -0.5:
        reason = f"spot price (₹{current_price:,.2f}) is moderately below the 7-day average (₹{ma7:,.2f}, -{abs(diff_pct):.2f}%)"
        influence = "Supports BUY"
    elif diff_pct <= 0.5:
        reason = f"spot price (₹{current_price:,.2f}) is hovering near the 7-day average (₹{ma7:,.2f})"
        influence = "Neutral"
    elif diff_pct <= 2.0:
        reason = f"spot price (₹{current_price:,.2f}) is trading {diff_pct:.2f}% above the 7-day average (₹{ma7:,.2f})"
        influence = "Supports SELL"
    else:
        reason = f"spot price (₹{current_price:,.2f}) is extended {diff_pct:.2f}% above the 7-day average (₹{ma7:,.2f}), reflecting short-term overbought conditions"
        influence = "Supports SELL"

    return IndicatorContribution(
        name=name,
        weight=weight,
        raw_value=ma7,
        raw_fmt=f"₹{ma7:,.2f} ({diff_pct:+.2f}% vs spot)",
        score=round(score, 2),
        normalized_score=round(norm, 3),
        reason=reason,
        confidence="High",
        influence=influence,
        status="active",
    )


def evaluate_ma30(current_price: float, ma30: Optional[float]) -> IndicatorContribution:
    """Evaluate 30-Day Moving Average with continuous linear normalization."""
    name = "30-Day Moving Average (MA30)"
    weight = WEIGHT_MA30

    if not ma30 or not current_price:
        return IndicatorContribution(
            name=name,
            weight=weight,
            raw_value=None,
            raw_fmt="N/A",
            score=weight * 0.5,
            normalized_score=0.0,
            reason="30-day moving average unavailable — requires minimum 30 daily price observations",
            confidence="Low",
            influence="Ignored",
            status="insufficient_data",
        )

    diff_pct = ((current_price - ma30) / ma30) * 100.0
    # Continuous norm: diff_pct from -3.5% (Full Buy +1.0) to +3.5% (Full Sell -1.0)
    norm = _clamp(-diff_pct / 3.5, -1.0, +1.0)
    score = ((norm + 1.0) / 2.0) * weight

    if diff_pct < -2.5:
        reason = f"gold spot price is trading {abs(diff_pct):.2f}% below the 30-day structural average (₹{ma30:,.2f}), historically representing a prime long-term accumulation window"
        influence = "Supports BUY"
    elif diff_pct < -0.5:
        reason = f"spot price is {abs(diff_pct):.2f}% below the 30-day baseline (₹{ma30:,.2f}), offering favorable long-term entry terms"
        influence = "Supports BUY"
    elif diff_pct <= 0.5:
        reason = f"spot price matches the 30-day structural baseline (₹{ma30:,.2f})"
        influence = "Neutral"
    elif diff_pct <= 2.5:
        reason = f"spot price is elevated {diff_pct:.2f}% above the 30-day trend average (₹{ma30:,.2f})"
        influence = "Supports SELL"
    else:
        reason = f"spot price is significantly elevated {diff_pct:.2f}% above the 30-day average (₹{ma30:,.2f}), indicating medium-term overbought risk"
        influence = "Supports SELL"

    return IndicatorContribution(
        name=name,
        weight=weight,
        raw_value=ma30,
        raw_fmt=f"₹{ma30:,.2f} ({diff_pct:+.2f}% vs spot)",
        score=round(score, 2),
        normalized_score=round(norm, 3),
        confidence="High",
        reason=reason,
        influence=influence,
        status="active",
    )


def evaluate_momentum(
    momentum: Optional[float],
    sr: SupportResistanceResult,
    ma30_contrib: IndicatorContribution,
) -> IndicatorContribution:
    """
    Evaluate 5-period Momentum with contextual mean-reversion and trend continuation logic.
    """
    name = "5-Period Price Momentum"
    weight = WEIGHT_MOMENTUM

    if momentum is None:
        return IndicatorContribution(
            name=name,
            weight=weight,
            raw_value=None,
            raw_fmt="N/A",
            score=weight * 0.5,
            normalized_score=0.0,
            reason="short-term momentum unavailable — insufficient consecutive period data",
            confidence="Low",
            influence="Ignored",
            status="insufficient_data",
        )

    # Contextual check: negative momentum during a pullback into support or below MA30 is a DIP BUY opportunity (+1.0)
    # Negative momentum during a structural breakdown (far below support) is weaker
    is_dip = sr.at_support or (ma30_contrib.normalized_score > 0.2)

    if momentum < -0.3:
        if is_dip:
            norm = +0.85
            reason = f"recent 5-period price decline (avg {momentum:.2f}%/period) represents a controlled pullback into support, favoring dip buyers"
            influence = "Supports BUY"
        else:
            norm = +0.5
            reason = f"short-term price momentum is negative ({momentum:.2f}%/period), reflecting downside selling pressure"
            influence = "Supports BUY"
    elif momentum < -0.05:
        norm = +0.4
        reason = f"price momentum is mildly negative ({momentum:.2f}%/period), indicating short-term consolidation"
        influence = "Supports BUY"
    elif momentum <= 0.05:
        norm = 0.0
        reason = f"price momentum is flat ({momentum:.2f}%/period), indicating price equilibrium"
        influence = "Neutral"
    elif momentum <= 0.3:
        norm = -0.4
        reason = f"short-term momentum is positive (+{momentum:.2f}%/period), prices moving higher"
        influence = "Supports SELL"
    else:
        norm = -0.85
        reason = f"strong upward momentum (+{momentum:.2f}%/period) indicates fast price advance — caution against chasing rallies"
        influence = "Supports SELL"

    score = ((norm + 1.0) / 2.0) * weight

    return IndicatorContribution(
        name=name,
        weight=weight,
        raw_value=momentum,
        raw_fmt=f"{momentum:+.2f}% / period",
        score=round(score, 2),
        normalized_score=round(norm, 3),
        confidence="Medium" if abs(momentum) < 0.1 else "High",
        reason=reason,
        influence=influence,
        status="active",
    )


def evaluate_sr(sr: SupportResistanceResult) -> IndicatorContribution:
    """Evaluate Support & Resistance proximity as a core signal."""
    name = "Support / Resistance Proximity"
    weight = WEIGHT_SR

    if sr.nearest_support is None and sr.nearest_resist is None:
        return IndicatorContribution(
            name=name,
            weight=weight,
            raw_value=None,
            raw_fmt="N/A",
            score=weight * 0.5,
            normalized_score=0.0,
            reason=sr.reasoning,
            confidence="Low",
            influence="Ignored",
            status="insufficient_data",
        )

    # Convert modifier (-6 to +6) to normalized score [-1.0, +1.0]
    norm = _clamp(sr.modifier / 6.0, -1.0, +1.0)
    score = ((norm + 1.0) / 2.0) * weight

    influence = "Supports BUY" if norm > 0.15 else ("Supports SELL" if norm < -0.15 else "Neutral")

    return IndicatorContribution(
        name=name,
        weight=weight,
        raw_value=sr.nearest_support or sr.nearest_resist,
        raw_fmt=f"S: ₹{sr.nearest_support:,.0f} | R: ₹{sr.nearest_resist:,.0f}" if sr.nearest_support and sr.nearest_resist else f"Level: ₹{(sr.nearest_support or sr.nearest_resist):,.0f}",
        score=round(score, 2),
        normalized_score=round(norm, 3),
        confidence="High" if (sr.at_support or sr.at_resistance) else "Medium",
        reason=sr.reasoning,
        influence=influence,
        status="active",
    )


def evaluate_premium(premium: PremiumResult) -> IndicatorContribution:
    """Evaluate Retail Premium Divergence as a core signal."""
    name = "Retail Premium Divergence"
    weight = WEIGHT_PREMIUM

    if premium.deviation_pct is None:
        return IndicatorContribution(
            name=name,
            weight=weight,
            raw_value=None,
            raw_fmt="N/A",
            score=weight * 0.5,
            normalized_score=0.0,
            reason=premium.reason,
            confidence="Low",
            influence="Ignored",
            status="insufficient_data",
        )

    # Convert modifier (-5 to +5) to norm [-1.0, +1.0]
    norm = _clamp(premium.modifier / 5.0, -1.0, +1.0)
    score = ((norm + 1.0) / 2.0) * weight

    influence = "Supports BUY" if norm > 0.15 else ("Supports SELL" if norm < -0.15 else "Neutral")

    return IndicatorContribution(
        name=name,
        weight=weight,
        raw_value=premium.current,
        raw_fmt=f"₹{premium.current:,.0f}/g ({premium.deviation_pct:+.1f}% vs 30d avg)",
        score=round(score, 2),
        normalized_score=round(norm, 3),
        confidence="High" if premium.deviation_pct is not None else "Low",
        reason=premium.reason,
        influence=influence,
        status="active",
    )


# ---------------------------------------------------------------------------
# ── CONFLICT RESOLUTION & SYNTHESIS ENGINE ────────────────────────────────
# ---------------------------------------------------------------------------

def resolve_conflicts(
    contributions: Dict[str, IndicatorContribution],
    trend: TrendStrengthResult,
    buy_score: int,
) -> ConflictResolution:
    """
    Detect opposing technical forces and synthesize explicit analyst conflict resolution.
    """
    bullish_signals = [c for c in contributions.values() if c.normalized_score > 0.25 and c.status == "active"]
    bearish_signals = [c for c in contributions.values() if c.normalized_score < -0.25 and c.status == "active"]

    if not bullish_signals or not bearish_signals:
        return ConflictResolution(
            has_conflict=False,
            dominant_factor="Aligned Signals",
            opposing_factor="None",
            resolution_reason="Technical indicators show consistent directional alignment without major conflicts.",
        )

    # Identify top bullish & bearish drivers
    top_bull = max(bullish_signals, key=lambda c: c.score)
    top_bear = min(bearish_signals, key=lambda c: c.score)

    if buy_score >= 50:
        dominant = top_bull.name
        opposing = top_bear.name
        reason = (
            f"Although {top_bear.name.lower()} is cautious ({top_bear.reason}), "
            f"the final recommendation favors BUY because {top_bull.name.lower()} provides stronger structural value "
            f"({top_bull.reason})."
        )
        if trend.adx >= 25.0 and trend.direction == "bearish":
            reason += f" High trend conviction (ADX {trend.adx:.1f}) reinforces the discount accumulation thesis."
    else:
        dominant = top_bear.name
        opposing = top_bull.name
        reason = (
            f"Although {top_bull.name.lower()} shows positive factors ({top_bull.reason}), "
            f"the final recommendation cautions against buying because {top_bear.name.lower()} indicates overhead risk "
            f"({top_bear.reason})."
        )

    return ConflictResolution(
        has_conflict=True,
        dominant_factor=dominant,
        opposing_factor=opposing,
        resolution_reason=reason,
    )


# ---------------------------------------------------------------------------
# ── 5-TIER CONFIDENCE MODEL ────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def compute_confidence_5tier(
    contributions: Dict[str, IndicatorContribution],
    prices: list[float],
    volatility: Optional[float],
    market_ctx: MarketContext,
    trend: TrendStrengthResult,
) -> Tuple[int, str]:
    """
    Compute rigorous 5-tier confidence (0-100):
    1. Data Completeness (35 pts max)
    2. Historical Coverage (25 pts max)
    3. Indicator Agreement / Coherence (20 pts max)
    4. Volatility / Noise Penalty (10 pts max)
    5. Market Session Quality (10 pts max)
    """
    total_weight = sum(c.weight for c in contributions.values())
    active_weight = sum(c.weight for c in contributions.values() if c.status == "active")
    completeness_pts = (active_weight / total_weight) * 35.0 if total_weight > 0 else 0.0

    coverage_ratio = min(1.0, len(prices) / 30.0)
    coverage_pts = coverage_ratio * 25.0

    active_norms = [c.normalized_score for c in contributions.values() if c.status == "active"]
    if len(active_norms) >= 2:
        # Variance of normalized scores: low variance = high agreement
        try:
            var = statistics.variance(active_norms)
            agreement_pts = max(0.0, (1.0 - (var / 2.0)) * 20.0)
        except statistics.StatisticsError:
            agreement_pts = 10.0
    else:
        agreement_pts = 5.0

    current_price = prices[-1] if prices else 1.0
    if volatility and current_price > 0:
        vol_pct = (volatility / current_price) * 100.0
        vol_pts = max(0.0, _linear_interpolate(vol_pct, 0.2, 2.5, 10.0, 0.0))
    else:
        vol_pts = 5.0

    session_pts = market_ctx.reliability_factor * 10.0

    # Cap confidence if strong opposing signal conflict exists
    has_active_conflict = any(c.normalized_score > 0.3 for c in contributions.values() if c.status == "active") and \
                          any(c.normalized_score < -0.3 for c in contributions.values() if c.status == "active")

    raw_confidence = completeness_pts + coverage_pts + agreement_pts + vol_pts + session_pts
    confidence = int(round(raw_confidence))

    # Apply conflict cap safeguard
    if has_active_conflict:
        confidence = min(confidence, 69)

    # Strict Cap Safeguards: Never output High/Very High if key history is missing
    if len(prices) < 14:
        confidence = min(confidence, 45)
    if contributions.get("ma30") and contributions["ma30"].status != "active":
        confidence = min(confidence, 49)

    confidence = max(0, min(100, confidence))

    if confidence >= 85:
        label = "Very High"
    elif confidence >= 70:
        label = "High"
    elif confidence >= 50:
        label = "Medium"
    elif confidence >= 30:
        label = "Low"
    else:
        label = "Very Low"

    return confidence, label


# ---------------------------------------------------------------------------
# ── LABELS & EXPLANATION GENERATOR ─────────────────────────────────────────
# ---------------------------------------------------------------------------

def get_buy_label(score: int) -> str:
    """Return buy decision label based on buy_score."""
    if score >= BUY_LABEL_GREAT:
        return "PERFECT TIME TO BUY"
    if score >= BUY_LABEL_GOOD:
        return "GOOD TIME TO BUY"
    if score >= BUY_LABEL_WAIT:
        return "WAIT A BIT MORE"
    return "BAD TIME TO BUY"


def get_sell_label(sell_score: int) -> str:
    """Return sell decision label based on sell_score (100 - buy_score)."""
    if sell_score >= SELL_LABEL_GREAT:
        return "PERFECT TIME TO SELL"
    if sell_score >= SELL_LABEL_GOOD:
        return "GOOD TIME TO SELL"
    if sell_score >= SELL_LABEL_HOLD:
        return "HOLD FOR NOW"
    return "BAD TIME TO SELL"


def build_explanation_text(
    contributions: Dict[str, IndicatorContribution],
    conflict: ConflictResolution,
    market_ctx: MarketContext,
) -> str:
    """Build a cohesive, institutional-grade narrative explanation."""
    reasons = [c.reason for c in contributions.values() if c.status == "active" and c.influence != "Neutral"]
    if not reasons:
        reasons = [c.reason for c in contributions.values() if c.status == "active"]

    if not reasons:
        return "Insufficient price history to compute reliable technical buy/sell signals."

    parts = [reasons[0]]
    if len(reasons) > 1:
        parts.append(reasons[1])

    if conflict.has_conflict:
        parts.append(conflict.resolution_reason)
    elif market_ctx.session not in ("normal", "peak"):
        parts.append(market_ctx.reason)

    return " · ".join(parts)


# ---------------------------------------------------------------------------
# ── HISTORY LOADER & CACHE ────────────────────────────────────────────────
# ---------------------------------------------------------------------------

_history_cache: dict = {}


def _fetch_history() -> list[dict]:
    """Fetch price history from DB with 60-second in-process caching."""
    global _history_cache
    now = datetime.datetime.now(datetime.timezone.utc)
    cached_at: Optional[datetime.timezone] = _history_cache.get("fetched_at")

    if cached_at and (now - cached_at).total_seconds() < _CACHE_TTL_SECONDS:
        logger.debug("_fetch_history: serving from cache")
        return _history_cache["data"]

    try:
        data = get_price_history(days=HISTORY_DAYS)
    except Exception as exc:
        logger.error("_fetch_history: DB error — %s", exc, exc_info=True)
        return _history_cache.get("data", [])

    _history_cache = {"data": data, "fetched_at": now}
    logger.debug("_fetch_history: loaded %d rows from DB", len(data))
    return data


# ---------------------------------------------------------------------------
# ── SCORE CHANGE EXPLAINER ─────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def explain_score_change(prev: AnalyticsResult, curr: AnalyticsResult) -> str:
    """
    Compare two successive AnalyticsResult objects and generate an explicit
    natural language explanation for why the score changed.
    """
    delta = curr.buy_score - prev.buy_score
    if delta == 0:
        return "Buy Score remains unchanged."

    direction = "increased" if delta > 0 else "decreased"
    drivers: list[str] = []

    for name, curr_c in curr.contributions.items():
        prev_c = prev.contributions.get(name)
        if prev_c:
            c_delta = curr_c.score - prev_c.score
            if abs(c_delta) >= 1.0:
                verb = "improved" if c_delta > 0 else "softened"
                drivers.append(f"{curr_c.name} {verb} ({c_delta:+.1f} pts)")

    if prev.session != curr.session:
        drivers.append(f"market session transitioned from {prev.session} to {curr.session}")

    if not drivers:
        drivers.append("minor intraday price adjustment")

    return f"Buy Score {direction} from {prev.buy_score} to {curr.buy_score} ({delta:+} pts) primarily because: " + "; ".join(drivers) + "."


# ---------------------------------------------------------------------------
# ── MAIN ENGINE ENTRY POINT ────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _apply_hampel_filter(prices: list[float], k: float = 3.0) -> tuple[list[float], int]:
    """
    Apply a Hampel Filter using Median Absolute Deviation (MAD) to clean single-point price outliers.
    Replaces corrupted prints with the robust median while leaving valid price series unchanged.
    Returns (cleaned_prices, num_outliers_cleaned).
    """
    if len(prices) < 5:
        return prices.copy(), 0

    med = statistics.median(prices)
    abs_devs = [abs(x - med) for x in prices]
    mad = statistics.median(abs_devs)
    
    scale = 1.4826 * mad
    threshold = max(med * 0.05, k * scale) if med > 0 else max(100.0, k * scale)

    cleaned: list[float] = []
    outliers_count = 0
    for x in prices:
        if abs(x - med) > threshold:
            cleaned.append(round(med, 2))
            outliers_count += 1
        else:
            cleaned.append(x)
            
    return cleaned, outliers_count


def run_analytics(
    current_price: float,
    retail_price: Optional[float] = None,
    history_override: Optional[list[dict]] = None,
) -> AnalyticsResult:
    """
    Main quantitative analytics reasoning pipeline for GoldTracker.

    Parameters
    ----------
    current_price: Current spot 24K gold price per gram in INR.
    retail_price: Current jeweller retail price per gram, if available.
    history_override: Optional explicit list of price history dicts for testing/simulation.
    """
    if not current_price or current_price <= 0:
        raise ValueError(f"run_analytics: invalid current_price={current_price!r}")

    # 1. Load History & Extract Clean Series
    history = history_override if history_override is not None else _fetch_history()
    raw_prices: list[float] = [
        row["price_24k"] for row in history
        if row.get("price_24k") and row["price_24k"] > 0
    ]
    # Ensure current spot price is appended to evaluation window if not already present
    if not raw_prices or raw_prices[-1] != current_price:
        raw_prices.append(current_price)

    # Apply Hampel outlier filter to clean single-point API corruptions
    prices, outliers_cleaned = _apply_hampel_filter(raw_prices)

    premium_history = compute_premium_history(history)

    # 2. Compute Raw Technical Indicators
    ma7 = compute_ma(prices, 7)
    ma30 = compute_ma(prices, 30)
    momentum = compute_momentum(prices, 5)
    volatility = compute_volatility(prices, 10)
    market_ctx = get_market_context()
    trend = score_trend_strength(prices, momentum)
    sr = compute_support_resistance(prices, current_price)
    premium = score_retail_divergence(current_price, retail_price, premium_history)

    # 3. Evaluate Signal Contributions
    c_ma7 = evaluate_ma7(current_price, ma7)
    c_ma30 = evaluate_ma30(current_price, ma30)
    c_mom = evaluate_momentum(momentum, sr, c_ma30)
    c_sr = evaluate_sr(sr)
    c_prem = evaluate_premium(premium)

    contributions = {
        "ma7": c_ma7,
        "ma30": c_ma30,
        "momentum": c_mom,
        "support_resistance": c_sr,
        "retail_premium": c_prem,
    }

    # 4. Aggregate Weighted Core Score
    raw_buy_score = sum(c.score for c in contributions.values())

    # Apply Session Timing & Trend ADX Modifiers
    adjusted_score = raw_buy_score + market_ctx.modifier + trend.modifier
    buy_score = int(round(_clamp(adjusted_score, 0.0, 100.0)))
    sell_score = 100 - buy_score

    # 5. Resolve Signal Conflicts
    conflict = resolve_conflicts(contributions, trend, buy_score)

    # 6. Compute 5-Tier Confidence
    confidence, confidence_label = compute_confidence_5tier(
        contributions, prices, volatility, market_ctx, trend
    )

    # Apply small confidence penalty if corrupted outliers were cleaned from raw prices
    if outliers_cleaned > 0:
        confidence = max(0, confidence - 10 * outliers_cleaned)
        if confidence >= 85:
            confidence_label = "Very High"
        elif confidence >= 70:
            confidence_label = "High"
        elif confidence >= 50:
            confidence_label = "Medium"
        elif confidence >= 30:
            confidence_label = "Low"
        else:
            confidence_label = "Very Low"

    # 7. Generate Institutional Narrative
    explanation = build_explanation_text(contributions, conflict, market_ctx)

    # Data Quality Audit Notes
    quality_notes: list[str] = []
    if outliers_cleaned > 0:
        quality_notes.append(f"Hampel filter cleaned {outliers_cleaned} single-point price outlier(s).")
    if len(prices) < 30:
        quality_notes.append(f"History window developing ({len(prices)}/30 observations available).")
    if volatility and current_price > 0 and (volatility / current_price) > 0.02:
        quality_notes.append(f"Elevated price volatility (stdev ₹{volatility:,.2f}) detected.")
    if market_ctx.session == "off-hours":
        quality_notes.append("Off-hours market session — lower volume liquidity.")

    return AnalyticsResult(
        ma7=ma7,
        ma30=ma30,
        momentum=momentum,
        volatility=volatility,
        buy_score=buy_score,
        sell_score=sell_score,
        buy_label=get_buy_label(buy_score),
        sell_label=get_sell_label(sell_score),
        explanation=explanation,
        conflict_resolution=conflict,
        session=market_ctx.session,
        time_ist=market_ctx.time_ist,
        day=market_ctx.day,
        premium_label=premium.label,
        premium_stats={
            "current_premium": premium.current,
            "current_premium_pct": premium.current_pct,
            "avg_premium_7d": premium.avg_7d,
            "avg_premium_30d": premium.avg_30d,
            "deviation_pct": premium.deviation_pct,
        },
        trend_adx=trend.adx,
        trend_label=trend.label,
        trend_direction=trend.direction,
        support=sr.support,
        resistance=sr.resistance,
        nearest_support=sr.nearest_support,
        nearest_resist=sr.nearest_resist,
        at_support=sr.at_support,
        at_resistance=sr.at_resistance,
        confidence=confidence,
        confidence_label=confidence_label,
        contributions=contributions,
        data_quality_notes=quality_notes,
    )


# ---------------------------------------------------------------------------
# ── CLI SMOKE & AUDIT TEST ────────────────────────────────────────────────
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import io
    # Safe UTF-8 output handling for Windows console
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    TEST_PRICE = 13_920.0
    TEST_RETAIL = 14_350.0

    print("=" * 60)
    print("RUNNING INSTITUTIONAL ANALYTICS ENGINE AUDIT TEST")
    print("=" * 60)

    res = run_analytics(TEST_PRICE, retail_price=TEST_RETAIL)

    print(f"Buy Score       : {res.buy_score}/100 ({res.buy_label})")
    print(f"Sell Score      : {res.sell_score}/100 ({res.sell_label})")
    print(f"Confidence      : {res.confidence}/100 ({res.confidence_label})")
    print(f"Session         : {res.session} ({res.time_ist}, {res.day})")
    print(f"Explanation     : {res.explanation}")
    print("-" * 60)
    print("INDICATOR ATTRIBUTION BREAKDOWN:")
    for key, c in res.contributions.items():
        print(f"  [{c.influence:<13}] {c.name:<30} | Score: {c.score:5.2f}/{c.weight:<4} | Norm: {c.normalized_score:+5.2f} | Conf: {c.confidence:<8}")
        print(f"                 Reason: {c.reason}")
    print("-" * 60)
    if res.conflict_resolution and res.conflict_resolution.has_conflict:
        print(f"CONFLICT RESOLUTION: Dominant={res.conflict_resolution.dominant_factor} vs Opposing={res.conflict_resolution.opposing_factor}")
        print(f"                     Reasoning: {res.conflict_resolution.resolution_reason}")
    print("=" * 60)