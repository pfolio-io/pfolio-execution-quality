"""
Accessor over the order-execution quality harness's parquet store.

Purpose: surface empirical median spread (and, later, slippage and fill
rates) so the cost model can replace its static fallbacks. Read-only—
this module never writes to the harness's parquet store.

Asset-class bucketing is config-driven via
`order-execution/quality/cost_tables/asset_class_buckets.json`, and the matcher
that reads it lives in `quality/buckets.py`, shared with `quality/analyze.py`.
**This module used to carry its own copy of `_instrument_key` and its own
matcher.** Two hand-maintained copies of the rule that decides which trials back
a published median is a drift that never raises — it silently empties a bucket —
so both consumers now import the one implementation.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
_QUALITY_DIR = _REPO_ROOT / "order-execution" / "quality"
_RESULTS_DIR = _QUALITY_DIR / "results"
_BUCKETS_PATH = _QUALITY_DIR / "cost_tables" / "asset_class_buckets.json"

# `order-execution` has a hyphen and cannot be a package name, so its directory
# goes on the path and `quality` is imported from it. The coupling is not new:
# this module already read two files out of that tree.
if str(_QUALITY_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_QUALITY_DIR.parent))

from quality import buckets  # noqa: E402


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
def _load_buckets() -> dict[str, buckets.Selector]:
    """asset_class → Selector. Excludes `_doc` / `_aliases` keys."""
    return buckets.load_bucket_map(_BUCKETS_PATH)


_instrument_key = buckets.instrument_key


def _filter_by_asset_class(df: pd.DataFrame, asset_class: str) -> pd.DataFrame:
    """Rows backing `asset_class`, venue constraints included.

    ⚑ An unknown class and a known-but-unmeasured class both return an empty
    frame here, and the two are NOT the same thing to a caller — see
    `is_declared` / `measurement_state`. `EU_STK_LSE` is the second kind: the
    cost model has a full commission, levy and stamp-duty rule for it and no
    execution measurement at all.
    """
    return buckets.rows_for(df, asset_class, _load_buckets())


def median_slip_bps_by_strategy(
        asset_class: str, strategy: str, mode: str = "paper",
) -> Optional[float]:
    """Median `slip_vs_mid_t0_bps` for FILLED entry-leg rows of `strategy`
    in `asset_class`. Returns None when no qualifying rows exist.

    Filters:
      - asset_class via the bucket map
      - strategy_label == strategy
      - status == FILLED
      - leg ∈ {NaN, 'entry'}—exit-leg slippage is always MKT_RAW and
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


def is_declared(asset_class: str) -> bool:
    """Is this class in the bucket map at all?"""
    return asset_class in _load_buckets()


def measurement_state(asset_class: str, mode: str = "paper") -> str:
    """`measured` · `unmeasured` · `undeclared`.

    **The distinction this function exists for.** `median_slip_bps_by_strategy`
    returns `None` for a class with no data and for a class nobody has heard of,
    and the cost model then emitted a 0.00 bps slippage line for both — which
    lands in a TOTAL that reads as complete. On 2026-08-04 that is what a
    European trade got: `EU_STK_LSE` priced a round-trip at 61.43 bps with
    commission, PTM levy and stamp duty all present and **execution counted as
    zero**, and the only trace was a `source` string.

    That is an assumed spread arriving by omission, and S1-33 — the workspace's
    "`cost_tables/` is the only admissible source of execution costs, never
    assume a spread" — is precisely the rule it walks through. A caller that
    wants a total it can stand behind checks this first.
    """
    if not is_declared(asset_class):
        return "undeclared"
    try:
        df = _load_trials(mode)
    except FileNotFoundError:
        return "unmeasured"
    rows = _filter_by_asset_class(df, asset_class)
    if rows.empty:
        return "unmeasured"
    usable = rows[(rows["status"] == "FILLED") & rows["slip_vs_mid_t0_bps"].notna()]
    return "measured" if not usable.empty else "unmeasured"
