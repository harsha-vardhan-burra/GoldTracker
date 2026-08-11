"""
core/history_loader.py
Stage 5 — Phase 1: Historical Data Loader & Benchmark Framework Dataset Preparation.

Provides deterministic data ingestion, validation, chronological normalization,
and benchmark dataset preparation (Buy & Hold, DCA, SMA Crossover, RSI)
for the GoldTracker Historical Validation Framework (HVCF Layer 4).
"""

from __future__ import annotations
import csv
import datetime
import os
import sys
import math
import statistics
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Iterator

# Ensure project root is accessible
def _project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.append(_project_root())

try:
    from database.db_manager import get_connection, get_price_history
except ImportError:
    pass


# ---------------------------------------------------------------------------
# ── IMMUTABLE DATA STRUCTURES ──────────────────────────────────────────────
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceRecord:
    """
    Immutable representation of a single validated price observation.
    
    Attributes:
        timestamp: ISO-8601 formatted datetime string (or YYYY-MM-DD).
        price_24k: 24K gold spot price per gram in INR.
        retail_price: Jeweller retail price per gram in INR.
        price_22k: 22K gold spot price per gram in INR (optional).
        spot_usd: USD gold spot price per troy oz (optional).
        usd_inr: USD/INR exchange rate (optional).
        data_source: Identifier for data origin ('historical_csv', 'db_history', etc.).
    """
    timestamp: str
    price_24k: float
    retail_price: float
    price_22k: Optional[float] = None
    spot_usd: Optional[float] = None
    usd_inr: Optional[float] = None
    data_source: str = "historical_record"

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary compatible with run_analytics()."""
        return {
            "timestamp": self.timestamp,
            "price_24k": self.price_24k,
            "retail_price": self.retail_price,
            "price_22k": self.price_22k or round(self.price_24k * 0.9167, 2),
            "spot_usd": self.spot_usd,
            "usd_inr": self.usd_inr,
            "data_source": self.data_source,
        }


@dataclass(frozen=True)
class BenchmarkDataset:
    """
    Prepared dataset and configuration metadata for benchmark strategy evaluation.
    
    Attributes:
        strategy_name: Name of benchmark strategy ('BuyAndHold', 'DCA', 'SMACrossover', 'RSI').
        price_series: Chronologically ordered list of PriceRecord objects.
        metadata: Configuration parameters for the strategy.
    """
    strategy_name: str
    price_series: List[PriceRecord]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HistoricalValidationDataset:
    """
    Complete replay-ready dataset output by HistoricalDataLoader.
    
    Attributes:
        records: Validated, sorted, deduplicated PriceRecord list.
        start_date: Earliest record timestamp.
        end_date: Latest record timestamp.
        total_sessions: Number of active price observations.
        detected_gaps: List of missing date gap ranges.
    """
    records: List[PriceRecord]
    start_date: str
    end_date: str
    total_sessions: int
    detected_gaps: List[Tuple[str, str, int]] = field(default_factory=list)

    def get_slice(self, end_index: int) -> List[PriceRecord]:
        """Return point-in-time historical slice up to end_index (No lookahead bias)."""
        if end_index <= 0 or end_index > len(self.records):
            raise ValueError(f"get_slice: invalid end_index={end_index} for dataset length {len(self.records)}")
        return self.records[:end_index]


# ---------------------------------------------------------------------------
# ── CUSTOM EXCEPTIONS ──────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class HistoryLoaderError(Exception):
    """Base exception for historical data loading errors."""
    pass


class InvalidRecordError(HistoryLoaderError):
    """Raised when a price record fails schema or sanity validation."""
    pass


class EmptyDatasetError(HistoryLoaderError):
    """Raised when dataset contains zero valid price records."""
    pass


# ---------------------------------------------------------------------------
# ── HISTORICAL DATA LOADER CLASS ──────────────────────────────────────────
# ---------------------------------------------------------------------------

class HistoricalDataLoader:
    """
    Production-grade historical data ingestion, validation, and normalization engine.
    Prepares datasets for point-in-time replay and benchmark strategy comparison.
    """

    MIN_REASONABLE_PRICE: float = 1000.0   # INR / gram
    MAX_REASONABLE_PRICE: float = 50000.0  # INR / gram

    @classmethod
    def validate_record(cls, record: PriceRecord) -> None:
        """
        Validate single PriceRecord for non-null timestamps and realistic price bounds.
        
        Raises:
            InvalidRecordError: If price bounds or timestamps are invalid.
        """
        if not record.timestamp or not isinstance(record.timestamp, str):
            raise InvalidRecordError(f"Invalid timestamp: {record.timestamp!r}")

        if record.price_24k is None or not isinstance(record.price_24k, (int, float)) or math.isnan(record.price_24k):
            raise InvalidRecordError(f"Invalid price_24k: {record.price_24k!r} at {record.timestamp}")

        if record.price_24k < cls.MIN_REASONABLE_PRICE or record.price_24k > cls.MAX_REASONABLE_PRICE:
            raise InvalidRecordError(
                f"Price out of bounds: ₹{record.price_24k:,.2f} at {record.timestamp} "
                f"(Expected ₹{cls.MIN_REASONABLE_PRICE} – ₹{cls.MAX_REASONABLE_PRICE})"
            )

        if record.retail_price is not None and record.retail_price <= 0:
            raise InvalidRecordError(f"Invalid retail_price: {record.retail_price!r} at {record.timestamp}")

    @classmethod
    def load_from_dict_list(cls, raw_list: List[Dict[str, Any]], source_name: str = "raw_dict") -> HistoricalValidationDataset:
        """
        Ingest, validate, sort, and deduplicate a list of price dictionaries.
        
        Parameters:
            raw_list: List of dicts containing 'timestamp', 'price_24k', optional 'retail_price'.
            source_name: Data source identifier label.
            
        Returns:
            HistoricalValidationDataset: Normalized, replay-ready dataset.
            
        Raises:
            EmptyDatasetError: If no valid records remain after sanitization.
        """
        if not raw_list:
            raise EmptyDatasetError("load_from_dict_list: provided raw_list is empty")

        valid_records: List[PriceRecord] = []
        for i, row in enumerate(raw_list):
            try:
                ts = str(row.get("timestamp") or "")
                p24 = float(row.get("price_24k", 0.0))
                ret = float(row.get("retail_price") or (p24 + 400.0))
                p22 = float(row["price_22k"]) if row.get("price_22k") else None
                spot_usd = float(row["spot_usd"]) if row.get("spot_usd") else None
                usd_inr = float(row["usd_inr"]) if row.get("usd_inr") else None

                rec = PriceRecord(
                    timestamp=ts,
                    price_24k=p24,
                    retail_price=ret,
                    price_22k=p22,
                    spot_usd=spot_usd,
                    usd_inr=usd_inr,
                    data_source=source_name,
                )
                cls.validate_record(rec)
                valid_records.append(rec)
            except (ValueError, TypeError, InvalidRecordError):
                continue

        if not valid_records:
            raise EmptyDatasetError(f"load_from_dict_list: 0 valid records found in {len(raw_list)} input dicts")

        return cls._normalize_and_build_dataset(valid_records)

    @classmethod
    def load_from_csv(cls, csv_path: str) -> HistoricalValidationDataset:
        """
        Load price history from a CSV file.
        CSV must contain columns: 'timestamp' (or 'date'), 'price_24k' (or 'price').
        
        Parameters:
            csv_path: Absolute path to CSV file.
            
        Returns:
            HistoricalValidationDataset: Normalized, replay-ready dataset.
        """
        if not os.path.exists(csv_path):
            raise HistoryLoaderError(f"CSV file not found: {csv_path}")

        raw_list: List[Dict[str, Any]] = []
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = row.get("timestamp") or row.get("date") or row.get("Date")
                p24 = row.get("price_24k") or row.get("price") or row.get("Close")
                ret = row.get("retail_price") or row.get("retail")
                if ts and p24:
                    raw_list.append({
                        "timestamp": ts.strip(),
                        "price_24k": p24.strip(),
                        "retail_price": ret.strip() if ret else None
                    })

        return cls.load_from_dict_list(raw_list, source_name=f"csv:{os.path.basename(csv_path)}")

    @classmethod
    def load_from_database(cls, days: int = 365) -> HistoricalValidationDataset:
        """
        Fetch price history from GoldTracker SQLite database.
        
        Parameters:
            days: Number of historical days to fetch.
            
        Returns:
            HistoricalValidationDataset: Normalized, replay-ready dataset.
        """
        rows = get_price_history(days=days)
        if not rows:
            raise EmptyDatasetError(f"No database price history found for the last {days} days")
        return cls.load_from_dict_list(rows, source_name="sqlite_db")

    @classmethod
    def _normalize_and_build_dataset(cls, records: List[PriceRecord]) -> HistoricalValidationDataset:
        """
        Sort chronologically, eliminate duplicate timestamps, detect date gaps, and build dataset.
        """
        # Sort chronologically by timestamp
        records_sorted = sorted(records, key=lambda r: r.timestamp)

        # Deduplicate timestamps (keep latest entry if duplicate exists)
        dedup_dict: Dict[str, PriceRecord] = {}
        for r in records_sorted:
            dedup_dict[r.timestamp] = r
        
        unique_records = list(dedup_dict.values())

        # Detect gaps > 3 days
        detected_gaps: List[Tuple[str, str, int]] = []
        for i in range(1, len(unique_records)):
            prev_ts = unique_records[i - 1].timestamp
            curr_ts = unique_records[i].timestamp
            try:
                dt_prev = datetime.datetime.fromisoformat(prev_ts.replace("Z", ""))
                dt_curr = datetime.datetime.fromisoformat(curr_ts.replace("Z", ""))
                gap_days = (dt_curr - dt_prev).days
                if gap_days > 3:
                    detected_gaps.append((prev_ts, curr_ts, gap_days))
            except Exception:
                continue

        start_date = unique_records[0].timestamp
        end_date = unique_records[-1].timestamp

        return HistoricalValidationDataset(
            records=unique_records,
            start_date=start_date,
            end_date=end_date,
            total_sessions=len(unique_records),
            detected_gaps=detected_gaps,
        )


# ---------------------------------------------------------------------------
# ── BENCHMARK DATASET PREPARATION ──────────────────────────────────────────
# ---------------------------------------------------------------------------

class BenchmarkFramework:
    """
    Prepares benchmark strategy datasets and metadata for comparative evaluation.
    Strategies supported: Buy & Hold, Periodic DCA, 7/30 SMA Crossover, RSI Oscillator.
    """

    @staticmethod
    def prepare_buy_and_hold(dataset: HistoricalValidationDataset) -> BenchmarkDataset:
        """
        Prepare Buy & Hold benchmark dataset.
        Strategy: Purchase 100% allocation on session 0 and hold until end date.
        """
        return BenchmarkDataset(
            strategy_name="BuyAndHold",
            price_series=dataset.records,
            metadata={
                "initial_entry_date": dataset.start_date,
                "initial_entry_price": dataset.records[0].price_24k if dataset.records else 0.0,
                "allocation": 1.0,
            }
        )

    @staticmethod
    def prepare_periodic_dca(dataset: HistoricalValidationDataset, interval_days: int = 7, amount_inr: float = 5000.0) -> BenchmarkDataset:
        """
        Prepare Periodic Dollar-Cost Averaging (DCA) benchmark dataset.
        Strategy: Allocate fixed amount_inr every interval_days sessions.
        """
        dca_schedule = []
        for i, rec in enumerate(dataset.records):
            if i % interval_days == 0:
                dca_schedule.append({
                    "step": i,
                    "date": rec.timestamp,
                    "price": rec.price_24k,
                    "amount_inr": amount_inr,
                })

        return BenchmarkDataset(
            strategy_name="PeriodicDCA",
            price_series=dataset.records,
            metadata={
                "interval_days": interval_days,
                "amount_inr": amount_inr,
                "total_installments": len(dca_schedule),
                "schedule": dca_schedule,
            }
        )

    @staticmethod
    def prepare_sma_crossover(dataset: HistoricalValidationDataset, fast_period: int = 7, slow_period: int = 30) -> BenchmarkDataset:
        """
        Prepare 7/30 Simple Moving Average (SMA) Crossover benchmark dataset.
        Strategy: Emit BUY signal when fast_ma > slow_ma, SELL signal when fast_ma < slow_ma.
        """
        signals = []
        prices = [r.price_24k for r in dataset.records]

        for i in range(len(prices)):
            if i < slow_period - 1:
                signals.append("HOLD")
                continue

            fast_ma = sum(prices[i - fast_period + 1 : i + 1]) / fast_period
            slow_ma = sum(prices[i - slow_period + 1 : i + 1]) / slow_period

            if fast_ma > slow_ma:
                signals.append("BUY")
            else:
                signals.append("SELL")

        return BenchmarkDataset(
            strategy_name="SMACrossover",
            price_series=dataset.records,
            metadata={
                "fast_period": fast_period,
                "slow_period": slow_period,
                "signals": signals,
            }
        )

    @staticmethod
    def prepare_rsi(dataset: HistoricalValidationDataset, period: int = 14, oversold: float = 30.0, overbought: float = 70.0) -> BenchmarkDataset:
        """
        Prepare Relative Strength Index (RSI) Mean-Reversion benchmark dataset.
        Strategy: Emit BUY when RSI < oversold, SELL when RSI > overbought, else HOLD.
        """
        prices = [r.price_24k for r in dataset.records]
        signals = []
        rsi_values = []

        if len(prices) < period + 1:
            return BenchmarkDataset("RSI", dataset.records, {"signals": ["HOLD"] * len(prices)})

        gains = []
        losses = []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i - 1]
            gains.append(max(0.0, diff))
            losses.append(max(0.0, -diff))

        for i in range(len(prices)):
            if i < period:
                signals.append("HOLD")
                rsi_values.append(50.0)
                continue

            avg_gain = sum(gains[i - period : i]) / period
            avg_loss = sum(losses[i - period : i]) / period

            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))

            rsi_values.append(round(rsi, 2))

            if rsi < oversold:
                signals.append("BUY")
            elif rsi > overbought:
                signals.append("SELL")
            else:
                signals.append("HOLD")

        return BenchmarkDataset(
            strategy_name="RSI",
            price_series=dataset.records,
            metadata={
                "period": period,
                "oversold_threshold": oversold,
                "overbought_threshold": overbought,
                "rsi_values": rsi_values,
                "signals": signals,
            }
        )


# ---------------------------------------------------------------------------
# ── CLI SMOKE & UNIT TEST RUNNER ──────────────────────────────────────────
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("======================================================================")
    print("RUNNING CORE/HISTORY_LOADER.PY SMOKE & INTEGRITY TEST")
    print("======================================================================")

    # 1. Test Synthetic Ingestion
    raw_synthetic = [
        {"timestamp": "2025-01-01", "price_24k": 13000.0, "retail_price": 13400.0},
        {"timestamp": "2025-01-03", "price_24k": 13100.0, "retail_price": 13500.0},
        {"timestamp": "2025-01-02", "price_24k": 13050.0, "retail_price": 13450.0}, # Out of order
        {"timestamp": "2025-01-04", "price_24k": 13200.0, "retail_price": 13600.0},
        {"timestamp": "2025-01-04", "price_24k": 13200.0, "retail_price": 13600.0}, # Duplicate
        {"timestamp": "2025-01-10", "price_24k": 13500.0, "retail_price": 13900.0}, # Gap > 3 days
        {"timestamp": "2025-01-11", "price_24k": -500.0,  "retail_price": 13900.0}, # Bad price (should filter)
    ]

    ds = HistoricalDataLoader.load_from_dict_list(raw_synthetic, "smoke_test")
    print(f"Loaded Records Count : {ds.total_sessions} (Expected: 5 valid unique records)")
    print(f"Date Range           : {ds.start_date} to {ds.end_date}")
    print(f"Detected Gaps Count  : {len(ds.detected_gaps)}")
    if ds.detected_gaps:
        print(f"  Gap Detail         : {ds.detected_gaps[0]}")

    # Assertions
    assert ds.total_sessions == 5, f"Expected 5 records, got {ds.total_sessions}"
    assert ds.records[0].timestamp == "2025-01-01", "Chronological sorting failed"
    assert ds.records[1].timestamp == "2025-01-02", "Chronological sorting failed"
    print("✔️ Ingestion & Normalization Assertion PASSED")

    # 2. Test Benchmark Dataset Preparation
    b_bah = BenchmarkFramework.prepare_buy_and_hold(ds)
    b_dca = BenchmarkFramework.prepare_periodic_dca(ds, interval_days=2)
    b_sma = BenchmarkFramework.prepare_sma_crossover(ds, fast_period=2, slow_period=3)
    b_rsi = BenchmarkFramework.prepare_rsi(ds, period=2)

    print("\nBenchmark Datasets Prepared Successfully:")
    print(f"  • {b_bah.strategy_name:<15} | Entry Price: ₹{b_bah.metadata['initial_entry_price']:,.2f}")
    print(f"  • {b_dca.strategy_name:<15} | Installments: {b_dca.metadata['total_installments']}")
    print(f"  • {b_sma.strategy_name:<15} | Signals Generated: {len(b_sma.metadata['signals'])}")
    print(f"  • {b_rsi.strategy_name:<15} | Signals Generated: {len(b_rsi.metadata['signals'])}")

    print("======================================================================")
    print("CORE/HISTORY_LOADER.PY VERIFIED SUCCESSFULLY (STAGE 5 PHASE 1 COMPLETE)")
    print("======================================================================")
