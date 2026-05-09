"""
Accessor over the order-execution quality harness's parquet store.

Purpose: surface empirical median spread (and, later, slippage and fill
rates) so the cost model can replace its static fallbacks. Read-only —
this module never writes to the harness's parquet store.

Asset-class bucketing is config-driven via
`order-execution/quality/cost_tables/asset_class_buckets.json`, which maps
calculator keys (e.g. `US_STK`, `FUT_CME`) to lists of harness
instrument-key patterns. Patterns match `_instrument_key(df)` from
`quality/analyze.py` — the canonical `<symbol>/<secType>` or
`<symbol>/<secType>/<expiry>` form.
"""

from __future__ import annotations

import fnmatch
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
_QUALITY_DIR = _REPO_ROOT / "order-execution" / "quality"
_RESULTS_DIR = _QUALITY_DIR / "results"
_BUCKETS_PATH = _QUALITY_DIR / "cost_tables" / "asset_class_buckets.json"


def _store_path(mode: str) -> Path:
    suffix = "live" if mode == "live" else "paper"
    parquet = _RESULTS_DIR / f"trials_{suffix}.parquet"
    if parquet.exists():
        return parquet
    csv = _RESULTS_DIR / f"trials_{suffix}.csv"
    if csv.exists():
        return csv
    raise FileNotFoundError(f"no trials store for mode={mode!r} in {_RESULTS_DIR}")


@lru_cache(maxsize=4)
def _load_trials(mode: str) -> pd.DataFrame:
    """Load trial rows for `mode`. Cached per mode for the process lifetime."""
    path = _store_path(mode)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


@lru_cache(maxsize=1)
def _load_buckets() -> dict[str, list[str]]:
    """Read the asset_class → patterns map. Excludes `_doc` / `_aliases` keys."""
    raw = json.loads(_BUCKETS_PATH.read_text())
    return {
        k: list(v) for k, v in raw.items()
        if not k.startswith("_") and isinstance(v, list)
    }


def _instrument_key(df: pd.DataFrame) -> pd.Series:
    """Mirrors quality/analyze.py::_instrument_key.
    `<symbol>/<secType>` plus `/<expiry>` for futures."""
    expiry = df["expiry"].fillna("").astype(str)
    return (
            df["symbol"].astype(str)
            + df["secType"].apply(lambda s: f"/{s}" if s else "")
            + expiry.apply(lambda e: f"/{e}" if e else "")
    )


def _filter_by_asset_class(df: pd.DataFrame, asset_class: str) -> pd.DataFrame:
    buckets = _load_buckets()
    patterns = buckets.get(asset_class)
    if not patterns:
        return df.iloc[0:0]
    keys = _instrument_key(df)
    mask = pd.Series(False, index=df.index)
    for pat in patterns:
        mask = mask | keys.apply(lambda k: fnmatch.fnmatchcase(k, pat))
    return df[mask]


def median_half_spread_bps(
        asset_class: str, mode: str = "paper",
) -> Optional[float]:
    """Median half of `spread_t0_bps` across harness rows in `asset_class`.

    Half-spread is what a one-leg trade pays vs the mid; the cost model
    multiplies by `legs` (1 for single-leg, 2 for round-trip) to compute
    spread cost. Returns None when no qualifying rows exist so the caller
    can fall back.
    """
    try:
        df = _load_trials(mode)
    except FileNotFoundError:
        return None
    rows = _filter_by_asset_class(df, asset_class)
    if rows.empty:
        return None
    full_spread = rows["spread_t0_bps"].dropna()
    if full_spread.empty:
        return None
    return float(full_spread.median()) / 2.0


def median_slip_bps_by_strategy(
        asset_class: str, strategy: str, mode: str = "paper",
) -> Optional[float]:
    """Median `slip_vs_mid_t0_bps` for FILLED entry-leg rows of `strategy`
    in `asset_class`. Returns None when no qualifying rows exist.

    Filters:
      - asset_class via the bucket map
      - strategy_label == strategy
      - status == FILLED
      - leg ∈ {NaN, 'entry'}  — exit-leg slippage is always MKT_RAW and
        a function of the entry, not of the entry strategy

    NOTE on paper data: paper sim fills LMT_MID at the mid and MKT_RAW
    at the touch deterministically, so the resulting median understates
    real-life slippage for limit-style strategies and overstates the
    cleanliness of MKT_RAW. The cost model treats paper slippage as
    *not actionable* and surfaces a placeholder line; switch to
    `mode='live'` once Phase 6.5 data is in.
    """
    try:
        df = _load_trials(mode)
    except FileNotFoundError:
        return None
    rows = _filter_by_asset_class(df, asset_class)
    if rows.empty:
        return None
    leg_mask = rows["leg"].isna() | (rows["leg"] == "entry")
    rows = rows[
        (rows["strategy_label"] == strategy)
        & (rows["status"] == "FILLED")
        & leg_mask
        ]
    slip = rows["slip_vs_mid_t0_bps"].dropna()
    if slip.empty:
        return None
    return float(slip.median())


def coverage_by_strategy(
        asset_class: str, strategy: str, mode: str = "paper",
) -> dict[str, int]:
    """Diagnostic for slippage accessor: count entry-leg FILLED rows
    matching the asset_class × strategy pair, plus how many have a
    populated slip_vs_mid_t0_bps."""
    try:
        df = _load_trials(mode)
    except FileNotFoundError:
        return {"total": 0, "filled": 0, "with_slip": 0}
    rows = _filter_by_asset_class(df, asset_class)
    if rows.empty:
        return {"total": 0, "filled": 0, "with_slip": 0}
    leg_mask = rows["leg"].isna() | (rows["leg"] == "entry")
    rows = rows[(rows["strategy_label"] == strategy) & leg_mask]
    filled = rows[rows["status"] == "FILLED"]
    return {
        "total": int(len(rows)),
        "filled": int(len(filled)),
        "with_slip": int(filled["slip_vs_mid_t0_bps"].notna().sum()),
    }


def coverage(asset_class: str, mode: str = "paper") -> dict[str, int]:
    """Diagnostic: how many harness rows back this asset_class.
    Returns counts of total rows, rows with a populated spread, and rows
    with a populated commission. Useful for debugging / surfacing sample
    size in the calculator output."""
    try:
        df = _load_trials(mode)
    except FileNotFoundError:
        return {"total": 0, "with_spread": 0, "with_commission": 0}
    rows = _filter_by_asset_class(df, asset_class)
    return {
        "total": int(len(rows)),
        "with_spread": int(rows["spread_t0_bps"].notna().sum()) if not rows.empty else 0,
        "with_commission": int(rows["commission"].notna().sum()) if not rows.empty else 0,
    }


def list_asset_classes() -> list[str]:
    """Return the asset_class keys the bucket map knows about."""
    return list(_load_buckets().keys())
