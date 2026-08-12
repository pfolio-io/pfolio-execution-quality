"""
Post-hoc aggregation over the trial store. Reads
`results/trials_{paper,live}.parquet`, prints a summary, and writes
`results/REPORT.md`.

Run from `order-execution/`:
    python -m quality.analyze                               # paper store, all rows
    python -m quality.analyze --mode live                   # live store
    python -m quality.analyze --last-run                    # only the most recent run
    python -m quality.analyze --run-id 20260504T133000Z-abc # one specific run (prefix ok)
    python -m quality.analyze --since 2026-05-04T13:30      # ISO UTC cutoff
    python -m quality.analyze --mode live \\
        --export-matrix-csv quality/results/matrix_live.csv   # bucket × strategy CSV
                                                             # (CWD-relative, and
                                                             #  CWD is order-execution/)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from quality import buckets

RESULTS_DIR = Path(__file__).parent / "results"
COST_TABLES_DIR = Path(__file__).parent / "cost_tables"
FX_RATES_PATH = COST_TABLES_DIR / "fx_rates.json"
BUCKETS_PATH = COST_TABLES_DIR / "asset_class_buckets.json"

# USD-anchor fallback if the JSON config is missing. Each value is the USD
# price of 1 unit of that currency.
FX_RATES_FALLBACK: dict[str, float] = {
    "USD": 1.0, "EUR": 1.10, "GBP": 1.27, "CHF": 1.20, "JPY": 0.0066,
}


def _load_fx_rates() -> dict[str, float]:
    """Read USD-anchor FX rates from cost_tables/fx_rates.json, falling
    back to a small built-in table if the file is missing or unreadable.
    Returned dict maps currency code → USD value of 1 unit."""
    try:
        raw = json.loads(FX_RATES_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(FX_RATES_FALLBACK)
    return {k: float(v) for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, (int, float))}


def _to_usd(amount: float, ccy: str, rates: dict[str, float]) -> float:
    """Convert `amount` denominated in `ccy` to USD using `rates`.
    Returns NaN if the currency isn't in the table."""
    if not ccy or ccy not in rates or rates[ccy] <= 0:
        return float("nan")
    return amount * rates[ccy]


PRIMARY_METRIC = "slip_vs_mid_t0_bps"
SECONDARY_METRIC = "slip_vs_vwap_bps"
TIEBREAK_METRIC = "time_to_fill_s"
STATUS_ORDER = ("FILLED", "PARTIAL", "TIMEOUT", "CANCELLED", "FAILED", "SKIPPED")


def _store_path(mode: str) -> Path:
    suffix = "live" if mode == "live" else "paper"
    parquet = RESULTS_DIR / f"trials_{suffix}.parquet"
    if parquet.exists():
        return parquet
    csv = RESULTS_DIR / f"trials_{suffix}.csv"
    if csv.exists():
        return csv
    raise FileNotFoundError(f"no trials store for mode={mode} in {RESULTS_DIR}")


def _load(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _apply_slice(
        df: pd.DataFrame, *, run_id: str | None, since: str | None, last_run: bool,
) -> tuple[pd.DataFrame, str]:
    """Filter `df` per the slice flags. Returns (filtered_df, label).
    `run_id` accepts a prefix match for convenience. `since` parses ISO 8601
    and filters on `timestamp_utc`. Flags are mutually exclusive (caller
    enforces). Returns label `"all rows"` if no slice was requested."""
    if run_id is not None:
        matches = df[df["run_id"].astype(str).str.startswith(run_id)]
        if matches.empty:
            raise ValueError(f"no rows match run_id prefix {run_id!r}")
        unique_ids = matches["run_id"].unique()
        if len(unique_ids) == 1:
            return matches, f"run_id={unique_ids[0]}"
        return matches, f"run_id prefix {run_id!r} ({len(unique_ids)} runs)"
    if since is not None:
        parsed = dt.datetime.fromisoformat(since)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        ts = pd.to_datetime(df["timestamp_utc"], errors="coerce", utc=True)
        matches = df[ts >= parsed]
        if matches.empty:
            raise ValueError(f"no rows since {parsed.isoformat()}")
        return matches, f"since={parsed.isoformat()}"
    if last_run:
        # Pick the run_id with the latest first-row timestamp.
        ts = pd.to_datetime(df["timestamp_utc"], errors="coerce", utc=True)
        latest_id = df.loc[ts.idxmax(), "run_id"]
        matches = df[df["run_id"] == latest_id]
        return matches, f"last-run={latest_id}"
    return df, "all rows"


# `_instrument_key`, the bucket map reader and the matcher used to live here and
# a second copy of each lived in `calculator/harness_data.py`. They are now in
# `quality/buckets.py`, imported by both: two hand-maintained copies of a matcher
# that decides which rows back a published median is a drift that would not
# raise, it would silently empty a bucket. These aliases keep the local names.
_instrument_key = buckets.instrument_key
_load_bucket_map = buckets.load_bucket_map


def bucket_strategy_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Bucket × strategy median `slip_vs_mid_t0_bps` + sample counts.

    One row per (calculator) asset-class bucket; columns are pairs of
    `<STRATEGY>_median_bps` and `<STRATEGY>_n` per executed strategy.
    Filters to FILLED rows with non-null primary metric. Instruments
    not mapped to any bucket are dropped (with a warning printed).
    Returns empty DataFrame if there are no rows after filtering.

    This is the V1 input for the public matrix view at
    `/tools/order-execution-costs`. See `tasks/CALCULATOR_DESIGN.md`.
    """
    bucket_map = _load_bucket_map()
    if not bucket_map:
        return pd.DataFrame()

    fills = df[(df["status"] == "FILLED") & df[PRIMARY_METRIC].notna()].copy()
    if fills.empty:
        return pd.DataFrame()

    fills["inst_key"] = _instrument_key(fills)
    fills["bucket"] = buckets.bucket_series(fills, bucket_map)

    # A row that satisfies two selectors is silently resolved by file order, and
    # the row would land in the wrong venue's bucket without anything raising.
    # Loud is the only safe setting for something that moves a published median.
    collisions = buckets.check_ambiguity(fills, bucket_map)
    if collisions:
        print(f"[matrix] ⚑ AMBIGUOUS bucket membership, resolved by file order: {collisions}")

    unmapped = fills[fills["bucket"].isna()]["inst_key"].unique()
    if len(unmapped):
        print(f"[matrix] dropping unmapped instruments: {sorted(unmapped)}")

    # Declared but unmeasured classes are reported by name. Measured against
    # `fills`, so this means "no FILLED row carrying a slippage measurement" —
    # which is the condition the cost model cares about, and is broader than "no
    # trial ever ran" (FUT_NYBOT has trials and no usable VWAP-side data).
    # On 2026-08-04 the three EU venues are here because nothing has ever traded
    # them: they exist in `broker_ibkr.json` and now in the bucket map, and the
    # harness had no European instrument until this change set.
    absent = buckets.unmeasured_classes(fills, bucket_map)
    if absent:
        print(f"[matrix] declared but UNMEASURED (no FILLED row with slippage): {sorted(absent)}")

    fills = fills[fills["bucket"].notna()]
    if fills.empty:
        return pd.DataFrame()

    grouped = (
        fills.groupby(["bucket", "strategy_label"])[PRIMARY_METRIC]
        .agg(median_bps="median", n="count")
        .reset_index()
    )
    wide_med = grouped.pivot(index="bucket", columns="strategy_label", values="median_bps").round(4)
    wide_n = grouped.pivot(index="bucket", columns="strategy_label", values="n").fillna(0).astype(int)

    # ⚑ `_n` counts BOTH legs; `_entry_n` counts entry legs only, and the two
    # disagree by ~3.5x on MKT_RAW in every bucket.
    #
    # Auto-flatten exits are always MKT_RAW, so only that strategy accumulates
    # exit legs — every other strategy's `_n` is already entry-only. A reader
    # comparing "MKT_RAW n=28" against "LMT_MID n=8" therefore reads a 3.5x
    # confidence difference that is an artifact of the test fixture, not of
    # sampling. An exit leg is a real market-order execution, but it is not an
    # independent sample of *choosing* MKT_RAW: its side is fixed by whatever
    # entry preceded it and it fires seconds later. `cost_model.py` already
    # excludes them for exactly this reason ("a harness test-fixture choice, not
    # a user pattern") and counts entry legs only — so the calculator and this
    # matrix have been reporting different n for the same store.
    #
    # Published values are deliberately NOT changed here. Switching `_n` and
    # `_median_bps` to the entry-only basis moves US_STK's MKT_RAW median from
    # -0.264 to +0.527 — a SIGN FLIP on an already-published figure, from price
    # improvement to cost — and US_ETF by 0.233. That is a call about a public
    # number, not a reconciliation, so it is surfaced rather than taken.
    # `leg` may be absent entirely: the schema is forward-compatible (parquet
    # append uses promote=True) and pre-auto-flatten stores predate the column.
    leg = (fills["leg"] if "leg" in fills.columns
           else pd.Series(pd.NA, index=fills.index, dtype="object"))
    entry_only = fills[leg.isna() | (leg == "entry")]
    entry_n = (
        entry_only.groupby(["bucket", "strategy_label"])[PRIMARY_METRIC].count()
        .unstack(fill_value=0)
        .reindex(index=wide_n.index, columns=wide_n.columns, fill_value=0)
        .fillna(0).astype(int)
    )

    # Interleave median + n columns per strategy for easier reading.
    out = pd.DataFrame(index=wide_med.index)
    for strat in sorted(wide_med.columns):
        out[f"{strat}_median_bps"] = wide_med[strat]
        out[f"{strat}_n"] = wide_n[strat]
        out[f"{strat}_entry_n"] = entry_n[strat]

    divergent = sorted({
        strat for strat in wide_med.columns
        if (wide_n[strat] != entry_n[strat]).any()
    })
    if divergent:
        print(f"[matrix] ⚑ `_n` includes auto-flatten exit legs and `_entry_n` "
              f"does not; they differ for: {divergent}. The calculator uses the "
              f"entry-only basis.")

    return _with_unmeasured_rows(out, absent).reset_index()


def _with_unmeasured_rows(out: pd.DataFrame, absent: list[str]) -> pd.DataFrame:
    """Add one explicit all-zero-n row per declared-but-unmeasured bucket.

    **Why a row and not an omission.** `cost_model.py` floors slippage at zero,
    so a US bucket can publish `0.00 bps` from a *measured* negative — fills at
    or inside the mid, capped so the total is not a promise of price improvement
    — while an unmeasured bucket would publish the same `0.00` from nothing at
    all. The two totals look identical and mean opposite things, and only one of
    them can move: an unmeasured figure can only go **up**.

    The Python calculator already distinguishes them (`measurement_state`,
    `unmeasured=True`, `PARTIAL TOTAL`). The matrix CSV did not: an unmeasured
    bucket was simply an **absent row**, and absence reads as *not applicable*
    rather than *never measured*. A blank median beside `n = 0` cannot be read as
    zero cost, and it puts the answer in the file instead of in whoever remembers.

    Downstream-neutral by construction: every consumer already treats `n = 0` as
    no data (`tool/src/02-data.js::bestGuess` returns null below its thresholds),
    so this changes what a reader sees and no number anywhere.
    """
    if not absent:
        return out
    n_cols = [c for c in out.columns if c.endswith("_n")]
    med_cols = [c for c in out.columns if c.endswith("_median_bps")]
    # NaN rather than pd.NA: it keeps the median columns float, so they still
    # render through `--export-matrix-csv`'s `%.4f` and read back as numbers.
    blank = pd.DataFrame(
        [{**{c: float("nan") for c in med_cols}, **{c: 0 for c in n_cols}}
         for _ in absent],
        index=pd.Index(sorted(absent), name=out.index.name),
    )
    combined = pd.concat([out, blank]).sort_index()
    # `concat` with NA promotes the counts to float; they are counts.
    for col in n_cols:
        combined[col] = combined[col].fillna(0).astype(int)
    return combined


def coverage_table(df: pd.DataFrame) -> pd.DataFrame:
    """Trials count per (instrument × strategy)."""
    return (
        df.assign(instrument=_instrument_key(df))
        .pivot_table(
            index="instrument", columns="strategy_label",
            values="run_id", aggfunc="count", fill_value=0,
        )
    )


def status_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Per-strategy count of statuses—fill-quality view."""
    out = df.pivot_table(
        index="strategy_label", columns="status",
        values="run_id", aggfunc="count", fill_value=0,
    )
    # Order columns by canonical sequence, keep any unexpected statuses at end.
    cols = [c for c in STATUS_ORDER if c in out.columns] + [
        c for c in out.columns if c not in STATUS_ORDER
    ]
    return out[cols]


def metric_summary(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Per-strategy median / p90 / count for a numeric metric (fills only)."""
    fills = df[(df["status"] == "FILLED") & df[metric].notna()]
    if fills.empty:
        return pd.DataFrame(columns=["count", "median", "p90"])
    return (
        fills.groupby("strategy_label")[metric]
        .agg(count="count", median="median", p90=lambda s: s.quantile(0.90))
        .round(4)
    )


def per_instrument_recommendation(
        df: pd.DataFrame, *, metric: str = PRIMARY_METRIC,
) -> pd.DataFrame:
    """Lowest median `metric` among eligible+filled cells per instrument;
    tiebreak on lowest median time_to_fill_s. Cells with zero fills (or no
    metric value) are excluded from ranking."""
    fills = df[(df["status"] == "FILLED") & df[metric].notna()].copy()
    fills["instrument"] = _instrument_key(fills)
    if fills.empty:
        return pd.DataFrame(
            columns=["instrument", "best_strategy", "median_slip_bps",
                     "median_ttf_s", "n_fills"]
        )

    grouped = (
        fills.groupby(["instrument", "strategy_label"])
        .agg(
            median_slip_bps=(metric, "median"),
            median_ttf_s=(TIEBREAK_METRIC, "median"),
            n_fills=(metric, "count"),
        )
        .reset_index()
    )
    # Sort so primary metric ascending, tiebreak ascending.
    grouped = grouped.sort_values(
        ["instrument", "median_slip_bps", "median_ttf_s"]
    )
    best = grouped.groupby("instrument").head(1).reset_index(drop=True)
    return best.rename(columns={"strategy_label": "best_strategy"}).round(4)


def _multiplier_int(m: Any) -> float:
    """Contract multiplier as a positive number; default 1 for STK/FX/CFD."""
    try:
        v = float(m)
        return v if v > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def _price_magnifier(row: pd.Series) -> float:
    """Quoted units per unit of currency; 1 when absent or unusable.

    ⚑ **100 on pence-quoted London lines.** IBKR reports `currency = GBP` and
    then quotes in GBX, so `CSP1` arrives as 61917 meaning GBP 619.17. Dividing a
    GBP commission by that notional gives a figure 100× too small — measured
    2026-08-11 on the first live GBP fill: 0.2 bps reported where the truth is
    ~16. Rows written before the column existed default to 1, which is right for
    every instrument traded until then."""
    try:
        value = float(row.get("price_magnifier") or 1)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def _commission_bps_row(row: pd.Series, rates: dict[str, float]) -> float:
    """Commission as bps of notional. Notional = qty × price × multiplier ÷
    price_magnifier (multiplier defaults to 1 for non-derivatives, magnifier to
    1 outside the pence-quoted London lines). When commission_currency
    differs from contract.currency, both are converted to USD via the FX
    table—bps is dimensionless so any common pivot works. Returns NaN
    when commission is missing, qty/price are non-positive, or any
    relevant currency is missing from the FX table."""
    commission = row.get("commission")
    if commission is None or pd.isna(commission):
        return float("nan")
    qty = row.get("filled_qty") or 0
    price = row.get("avg_fill_px") or 0
    if qty <= 0 or price <= 0:
        return float("nan")
    comm_ccy = row.get("commission_currency") or ""
    contract_ccy = row.get("currency") or ""
    notional = (qty * price * _multiplier_int(row.get("multiplier"))
                / _price_magnifier(row))
    if not comm_ccy or not contract_ccy:
        return float("nan")
    if comm_ccy == contract_ccy:
        return float(commission) / notional * 1e4
    # Cross-currency: pivot through USD so both sides are in the same unit.
    commission_usd = _to_usd(float(commission), comm_ccy, rates)
    notional_usd = _to_usd(notional, contract_ccy, rates)
    if pd.isna(commission_usd) or pd.isna(notional_usd) or notional_usd <= 0:
        return float("nan")
    return commission_usd / notional_usd * 1e4


def commission_summary(df: pd.DataFrame, rates: dict[str, float]) -> pd.DataFrame:
    """Per-instrument median raw commission and median commission_bps
    (FILLED only). Raw commission is in `commission_currency`; bps uses
    USD-pivot conversion when commission and contract currencies differ.
    `fx_converted` flags rows where the conversion path was used."""
    fills = df[(df["status"] == "FILLED") & df["commission"].notna()].copy()
    if fills.empty:
        return pd.DataFrame(columns=[
            "median_commission", "currency", "median_commission_bps",
            "fx_converted", "n",
        ])
    fills["instrument"] = _instrument_key(fills)
    fills["commission_bps"] = fills.apply(
        lambda r: _commission_bps_row(r, rates), axis=1,
    )
    fills["_xccy"] = (
            fills["commission_currency"].fillna("") != fills["currency"].fillna("")
    )
    return (
        fills.groupby("instrument")
        .agg(
            median_commission=("commission", "median"),
            currency=("commission_currency",
                      lambda s: s.dropna().iloc[0] if not s.dropna().empty else ""),
            median_commission_bps=("commission_bps", "median"),
            fx_converted=("_xccy", "any"),
            n=("commission", "count"),
        )
        .round(4)
    )


def t0_spread_report(df: pd.DataFrame) -> pd.DataFrame:
    """Per-instrument median T0 spread in bps—realistic-cost lower bound
    for MKT-style strategies under live conditions."""
    rows = df[df["spread_t0_bps"].notna()].copy()
    if rows.empty:
        return pd.DataFrame(columns=["median_spread_bps", "p90_spread_bps", "n"])
    rows["instrument"] = _instrument_key(rows)
    return (
        rows.groupby("instrument")["spread_t0_bps"]
        .agg(
            median_spread_bps="median",
            p90_spread_bps=lambda s: s.quantile(0.90),
            n="count",
        )
        .round(4)
    )


def _md_table(df: pd.DataFrame) -> str:
    """Render DataFrame as a Markdown table. Empty → italic notice."""
    if df.empty:
        return "_no rows_\n"
    return df.to_markdown(index=True) + "\n"


def render_report(df: pd.DataFrame, mode: str, slice_label: str = "all rows") -> str:
    rates = _load_fx_rates()
    parts: list[str] = []
    parts.append(f"# Execution Quality Report—`{mode}`\n")
    parts.append(f"Slice: **{slice_label}**\n")
    parts.append(f"Trials: **{len(df)}**  ·  runs: **{df['run_id'].nunique()}**  "
                 f"·  instruments: **{_instrument_key(df).nunique()}**\n")

    parts.append("\n## Coverage (trials per instrument × strategy)\n")
    parts.append(_md_table(coverage_table(df)))

    parts.append("\n## Fill-quality (status distribution per strategy)\n")
    parts.append(_md_table(status_distribution(df)))

    parts.append(f"\n## Slippage distribution—`{PRIMARY_METRIC}` (FILLED only)\n")
    parts.append(_md_table(metric_summary(df, PRIMARY_METRIC)))

    parts.append(f"\n## Slippage distribution—`{SECONDARY_METRIC}` (FILLED only)\n")
    parts.append("_Reported only—primary ranking still uses "
                 f"`{PRIMARY_METRIC}`. Null when VWAP unavailable._\n\n")
    parts.append(_md_table(metric_summary(df, SECONDARY_METRIC)))

    parts.append(f"\n## Time-to-fill distribution—`{TIEBREAK_METRIC}` (FILLED only)\n")
    parts.append(_md_table(metric_summary(df, TIEBREAK_METRIC)))

    parts.append("\n## T0 spread per instrument (realistic-cost lower bound)\n")
    parts.append(_md_table(t0_spread_report(df)))

    parts.append("\n## Commission per instrument (FILLED only)\n")
    parts.append("Raw commission in `commission_currency`. `median_commission_bps` "
                 "= commission / notional × 1e4. When `commission_currency` "
                 "differs from `contract.currency`, both are converted to USD "
                 f"using `cost_tables/fx_rates.json` (`fx_converted=True` flags "
                 "those rows). Edit the JSON when rates drift.\n\n")
    parts.append(_md_table(commission_summary(df, rates)))

    parts.append("\n## Per-instrument recommendation (primary)\n")
    parts.append(f"Ranking: lowest median `{PRIMARY_METRIC}` among FILLED cells; "
                 f"tiebreak on lowest median `{TIEBREAK_METRIC}`.\n\n")
    parts.append(_md_table(per_instrument_recommendation(df, metric=PRIMARY_METRIC)))

    parts.append("\n## Per-instrument recommendation (VWAP secondary view)\n")
    parts.append(f"Ranking: lowest median `{SECONDARY_METRIC}` among FILLED cells; "
                 f"tiebreak on lowest median `{TIEBREAK_METRIC}`. "
                 "Excludes cells without VWAP. _Reported, not used for primary "
                 "ranking._\n\n")
    parts.append(_md_table(
        per_instrument_recommendation(df, metric=SECONDARY_METRIC)
    ))

    parts.append("\n---\n")
    parts.append(f"_Source: `{_store_path(mode).name}` · "
                 f"primary metric: `{PRIMARY_METRIC}` · "
                 f"tiebreak: `{TIEBREAK_METRIC}`_\n")
    return "".join(parts)


def main() -> None:
    p = argparse.ArgumentParser(description="Aggregate trial results")
    p.add_argument("--mode", choices=("paper", "live"), default="paper")
    p.add_argument(
        "--report-path", type=Path, default=None,
        help=f"Override REPORT.md output path. Default: {RESULTS_DIR}/REPORT.md",
    )
    slice_group = p.add_mutually_exclusive_group()
    slice_group.add_argument(
        "--run-id", default=None,
        help="Filter to a single run_id (prefix match supported).",
    )
    slice_group.add_argument(
        "--since", default=None,
        help="ISO 8601 UTC cutoff. Naive timestamps assumed UTC. "
             "Example: 2026-05-04T13:30",
    )
    slice_group.add_argument(
        "--last-run", action="store_true",
        help="Filter to the most recent run only.",
    )
    p.add_argument(
        "--export-matrix-csv", type=Path, default=None,
        help="Write asset-class-bucket × strategy median-bps matrix as CSV "
             "to this path, IN ADDITION to rendering REPORT.md. The path is "
             "resolved against the CWD, not against results/ — from "
             "order-execution/ that means quality/results/matrix_live.csv.",
    )
    args = p.parse_args()

    path = _store_path(args.mode)
    df = _load(path)
    print(f"loaded {len(df)} rows from {path}")

    sliced, slice_label = _apply_slice(
        df, run_id=args.run_id, since=args.since, last_run=args.last_run,
    )
    if slice_label != "all rows":
        print(f"slice → {slice_label}: {len(sliced)} rows, "
              f"{sliced['run_id'].nunique()} run(s)")

    if args.export_matrix_csv:
        matrix = bucket_strategy_matrix(sliced)
        if matrix.empty:
            print("[matrix] no rows to export—empty filter or no fills")
        else:
            args.export_matrix_csv.parent.mkdir(parents=True, exist_ok=True)
            matrix.to_csv(args.export_matrix_csv, index=False,
                          float_format="%.4f")
            print(f"\nwrote matrix CSV → {args.export_matrix_csv}")
            print(matrix.to_string(index=False))

    # The matrix export used to `return` here, so `--export-matrix-csv` silently
    # suppressed the report. Every European batch passed that flag, so REPORT.md
    # went unregenerated from before the first European measurement until
    # 2026-08-12 — four batches during which the documented "regenerate the
    # report after running" step ran and did nothing. The two outputs are not
    # alternatives; asking for one is not a reason to skip the other.
    report = render_report(sliced, args.mode, slice_label=slice_label)
    out_path = args.report_path or (RESULTS_DIR / "REPORT.md")
    out_path.write_text(report)
    print(f"\nwrote {out_path}\n")
    print(report)


if __name__ == "__main__":
    main()
