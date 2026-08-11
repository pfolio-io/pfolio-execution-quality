"""
Trial results store. One row per (instrument, strategy) trial.

Writes parquet by default; falls back to CSV if pyarrow is not installed.
Paper and live runs are stored in separate files so they never mix.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

RESULTS_DIR = Path(__file__).parent / "results"

# Canonical row schema. Order matters for CSV writes.
COLUMNS: list[str] = [
    "schema_version",
    "run_id", "trial_idx", "timestamp_utc",
    # contract
    "symbol", "secType", "exchange", "currency", "conId", "expiry", "multiplier",
    # `sec_id` — the ISIN, when the contract was resolved by ISIN (the European
    # cells). Null for every US cell, which resolves by ticker. Added 2026-08-10:
    # a UCITS ETF trades under a different local ticker on every venue, so
    # `symbol` alone cannot say which fund a published European figure measured.
    # Additive and nullable, so no SCHEMA_VERSION bump — parquet append uses
    # promote=True and older rows read back as null.
    "sec_id",
    # strategy
    "strategy_label", "eligible", "skip_reason",
    # request
    "side", "requested_qty",
    # T0 snapshot
    "t0", "bid_t0", "ask_t0", "mid_t0", "spread_t0_bps", "spread_t0_ticks", "tick_size",
    # fill
    "t_fill", "filled_qty", "avg_fill_px", "n_fills", "time_to_fill_s", "status",
    # T_fill snapshot
    "bid_tfill", "ask_tfill", "mid_tfill",
    # quality
    "slip_vs_mid_t0_bps", "slip_vs_vwap_bps", "slip_vs_mid_tfill_bps", "vwap_window",
    # environment
    "paper_account", "session", "ib_server_version",
    # commission (raw IB commissionReport data; analyze.py normalizes to bps)
    "commission", "commission_currency", "exec_ids",
    # `exec_exchange` — where the fills ACTUALLY executed, from
    # `execution.exchange`, not the exchange that was requested. Added
    # 2026-08-11 after a measured surprise: a SMART-routed order in a
    # Xetra-primary ETF executed on GETTEX2, so attributing by the requested
    # exchange would have published a Gettex fill as XETRA and priced it against
    # XETRA's commission rule. Direct routing keeps the two equal; this column
    # is what proves it, and the only field that would catch a silent re-route.
    "exec_exchange",
    # realized P&L from commissionReport.realizedPNL on closing fills,
    # in commission_currency. NOTE: IB computes this against the
    # *account's* position average cost basis at fill time—NOT against
    # the entry leg of this round-trip. On accounts with accumulated
    # positions (e.g. our paper account), realized_pnl can diverge
    # significantly from a clean (exit_px − entry_px) × qty × multiplier
    # calculation. Use the paired entry+exit rows (joined on
    # round_trip_id) for clean per-cycle P&L; this column is for IB-side
    # cross-check.
    "realized_pnl",
    # round-trip pairing (set when --auto-flatten is on; null otherwise)
    "round_trip_id", "leg",
    # debug—IB error codes / cancellation reasons captured from trade.log
    "notes",
]


def _store_path(mode: str) -> Path:
    suffix = "live" if mode == "live" else "paper"
    try:
        import pyarrow  # noqa: F401
        return RESULTS_DIR / f"trials_{suffix}.parquet"
    except ImportError:
        return RESULTS_DIR / f"trials_{suffix}.csv"


def append_row(row: dict[str, Any], mode: str = "paper") -> Path:
    """
    Append one trial to the configured store. Creates the store if missing.
    Missing columns are written as None; extra columns are dropped (and warned).
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _store_path(mode)

    extras = set(row.keys()) - set(COLUMNS)
    if extras:
        print(f"[results] dropping extra columns not in schema: {sorted(extras)}")

    aligned = {col: row.get(col) for col in COLUMNS}
    aligned["schema_version"] = SCHEMA_VERSION

    if path.suffix == ".parquet":
        _append_parquet(path, aligned)
    else:
        _append_csv(path, aligned)
    return path


def _append_parquet(path: Path, row: dict[str, Any]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    new_table = pa.Table.from_pylist([row])
    if path.exists():
        existing = pq.read_table(path)
        # Tolerate schema growth: missing columns on either side become null.
        try:
            combined = pa.concat_tables(
                [existing, new_table], promote_options="default"
            )
        except TypeError:
            combined = pa.concat_tables([existing, new_table], promote=True)
    else:
        combined = new_table
    pq.write_table(combined, path)


def _append_csv(path: Path, row: dict[str, Any]) -> None:
    write_header = not path.exists() or os.path.getsize(path) == 0
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
