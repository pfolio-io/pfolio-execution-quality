"""
Order-execution quality runner.

Loops `(side × instrument × strategy)` cells serially, submitting each
strategy directly (no escalation chain), and appends one trial row per cell
to the configured store. Cells run serially per instrument so spread/state
is ~constant across the four strategies, enabling apples-to-apples
comparison.

Examples (run from `order-execution/`):
    python -m quality.runner                                  # AAPL/SPY/ES/EURUSD, BUY
    python -m quality.runner --instruments all --side BUY SELL --outside-rth
    python -m quality.runner --instruments ES,EURUSD --strategies LMT_MID MKT_RAW

`--mode` controls only which result store (paper vs live) the row is written
to and the `paper_account` field; it does not change the TWS port. The
harness warns if `--mode` disagrees with the connected account's DU/U prefix.

For the per-instrument list and per-strategy timeout/retry policy see the
constants block below.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import math
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nest_asyncio
from ib_insync import CFD, IB, Contract, Forex, Order, Stock, Trade

# Reuse shared primitives. Harness is independent of the production
# executor (`ib_order_executor`)—only contract-resolution helpers are
# shared via `contract_helpers`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contract_helpers import _get_tick_size, _qualify_contract  # noqa: E402

import eligibility  # noqa: E402
import order_builders  # noqa: E402
from quality import instruments, results  # noqa: E402
from quality.metrics import TickRecorder  # noqa: E402
from quote_snapshot import Quote, slip_vs_mid_bps, snapshot_quote  # noqa: E402

nest_asyncio.apply()

IB_HOST = "127.0.0.1"
IB_PORT = 7496  # TWS API port, same for paper & live accounts in this setup
IB_CLIENT_ID = 41  # distinct from production clientId 33

# Per-strategy timeouts (seconds). MIDPRICE has IB's internal ~30s wait, so
# allow a small buffer. LMT_MID retries 3× at 10s each = 30s budget; the
# overall cap (LMT_MID_TOTAL_TIMEOUT_S) bounds the whole retry loop.
MIDPRICE_TIMEOUT_S = 35.0
LMT_MID_PER_TRY_S = 10.0
LMT_MID_RETRY_COUNT = 3
LMT_MID_TOTAL_TIMEOUT_S = LMT_MID_PER_TRY_S * LMT_MID_RETRY_COUNT + 5.0
ADAPTIVE_TIMEOUT_S = 30.0
MKT_RAW_TIMEOUT_S = 10.0

FILL_POLL_INTERVAL_S = 0.25
CANCEL_TIMEOUT_S = 5.0
COMMISSION_WAIT_S = 2.0  # how long to wait for commissionReport events post-fill


@dataclass
class SubmitResult:
    """Result of one submit-and-wait attempt (or accumulated retries).
    `commission_total` and `realized_pnl_total` are in `commission_currency`
    (raw—analyze.py normalizes to bps). `realized_pnl_total` is non-zero
    only on closing fills (the auto-flatten exit leg, typically). `exec_ids`
    is a comma-joined string of IB execIds so it round-trips cleanly through
    parquet."""
    ib_status: str
    filled_qty: float
    avg_fill_px: float
    n_fills: int
    commission_total: float = 0.0
    commission_currency: str = ""
    realized_pnl_total: float = 0.0
    exec_ids: str = ""
    notes: str = ""


ALL_STRATEGIES = ("MIDPRICE_NATIVE", "LMT_MID", "MKT_ADAPTIVE", "MKT_RAW")

# Tier 1—high liquidity. Tier 2—medium liquidity / wider spread.
# Tier 3—low liquidity / structurally wider. Order matters only for
# run-time logs; analysis treats each (instrument × strategy) cell
# independently.
TIER1 = ("AAPL", "SPY", "ES", "EURUSD")
TIER2 = ("LQD", "EFA", "VIX", "CFD_USD_CHF")
TIER3 = ("DX", "VIX_FAR", "SMALL_CAP")
TIERS = {
    "tier1": TIER1,
    "tier2": TIER2,
    "tier3": TIER3,
    "all": TIER1 + TIER2 + TIER3,
}
KNOWN_SYMBOLS = TIER1 + TIER2 + TIER3

# Tier-3 small-cap default. The spec leaves the small-cap "TBD"—swap
# this constant if the chosen ticker becomes illiquid or delists.
SMALL_CAP_SYMBOL = "PRIM"  # Primoris Services (NYSE-listed small-cap)

# Per-instrument tiny notional. FX needs IDEALPRO minimum (≥20k base);
# CFD on USD/CHF needs at least ~1k base. Single-share/contract for STK/FUT.
DEFAULT_QTY = {
    "AAPL": 1.0, "SPY": 1.0, "ES": 1.0, "EURUSD": 20000.0,
    "LQD": 1.0, "EFA": 1.0, "VIX": 1.0, "CFD_USD_CHF": 1000.0,
    "DX": 1.0, "VIX_FAR": 1.0, "SMALL_CAP": 1.0,
}

# Live-mode quantities. IDEALPRO live minimum is typically 25k base for
# EURUSD; CFD on USD/CHF lives at the broker's live minimum (also ~25k
# typical). All others stay at 1 unit—explicit live overrides only
# where the live floor differs from paper.
DEFAULT_QTY_LIVE = {
    **DEFAULT_QTY,
    "EURUSD": 25000.0,
    "CFD_USD_CHF": 25000.0,
}

# Auto-flatten default per mode. Live runs ON to keep exposure ~zero;
# paper OFF since paper positions don't matter (and would distort the
# convergence dataset with extra rows).
AUTO_FLATTEN_DEFAULT_BY_MODE = {"paper": False, "live": True}

# Coarse pre-flight commission estimate per asset class (USD per fill).
# Used only for the live-mode pre-flight banner. Real commissions are
# captured per fill from `trade.commissionReport`.
ROUGH_COMMISSION_USD_PER_FILL = {
    "STK": 1.00, "ETF": 1.00, "FUT": 2.25, "CASH": 2.00, "CFD": 2.00,
}


async def _resolve_contract(ib: IB, symbol: str) -> Contract:
    """Equities/ETFs use SMART/USD; futures resolve front-month via IB;
    FX uses IDEALPRO; CFDs use SMART. Tier-3 names follow the same routing
    with `VIX_FAR` skipping the front contract."""
    if symbol in ("AAPL", "SPY", "LQD", "EFA"):
        return Stock(symbol, "SMART", "USD")
    if symbol == "SMALL_CAP":
        return Stock(SMALL_CAP_SYMBOL, "SMART", "USD")
    if symbol == "ES":
        return await instruments.resolve_front_month(ib, "ES", "CME")
    if symbol == "VIX":
        return await instruments.resolve_front_month(ib, "VIX", "CFE")
    if symbol == "VIX_FAR":
        return await instruments.resolve_front_month(ib, "VIX", "CFE", skip=1)
    if symbol == "DX":
        return await instruments.resolve_front_month(ib, "DX", "NYBOT")
    if symbol == "EURUSD":
        return Forex("EURUSD")
    if symbol == "CFD_USD_CHF":
        return CFD("USD", "SMART", "CHF")
    raise ValueError(f"unknown symbol {symbol!r}; known: {KNOWN_SYMBOLS}")


def _expand_instruments(spec: str) -> list[str]:
    """Resolve a --instruments spec to a concrete symbol list.
    Accepts tier names (tier1) or a comma-separated symbol list."""
    out: list[str] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if token in TIERS:
            out.extend(TIERS[token])
        elif token in KNOWN_SYMBOLS:
            out.append(token)
        else:
            raise ValueError(
                f"unknown instrument token {token!r}; "
                f"valid: {sorted(KNOWN_SYMBOLS) + list(TIERS)}"
            )
    if not out:
        raise ValueError(f"no instruments resolved from {spec!r}")
    return out


# ---------------------------------------------------------------------------
# Generic submit/wait/cancel helpers
# ---------------------------------------------------------------------------
async def _wait_for_fill_or_timeout(trade: Trade, timeout_s: float) -> str:
    """Poll trade until done/cancelled/timeout. Returns final IB status string."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if trade.isDone():
            return trade.orderStatus.status
        await asyncio.sleep(FILL_POLL_INTERVAL_S)
    return "Timeout"


async def _cancel(ib: IB, trade: Trade) -> None:
    if trade.isDone():
        return
    ib.cancelOrder(trade.order)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CANCEL_TIMEOUT_S
    while loop.time() < deadline:
        if trade.isDone():
            return
        await asyncio.sleep(0.2)


def _classify_status(ib_status: str, filled: float, requested: float) -> str:
    """Map IB status + fill ratio → harness status enum."""
    if filled >= requested * 0.999:
        return "FILLED"
    if filled > 0:
        return "PARTIAL"
    if ib_status == "Timeout":
        return "TIMEOUT"
    if ib_status in ("Cancelled", "ApiCancelled", "Inactive"):
        return "CANCELLED"
    return "FAILED"


def _collect_trade_notes(trade: Trade) -> str:
    """Pull errorCode:message entries from trade.log, dedup, join."""
    seen: list[str] = []
    for entry in trade.log:
        code = getattr(entry, "errorCode", 0)
        msg = (getattr(entry, "message", "") or "").strip()
        if code and msg:
            tag = f"{code}:{msg}"
            if tag not in seen:
                seen.append(tag)
    return " | ".join(seen) if seen else ""


def _final_fill(trade: Trade) -> tuple[float, float]:
    filled = float(trade.orderStatus.filled or 0)
    avg_px = float(trade.orderStatus.avgFillPrice or 0.0)
    if math.isnan(avg_px):
        avg_px = 0.0
    return filled, avg_px


async def _wait_for_commission_reports(trade: Trade, timeout_s: float) -> None:
    """commissionReport events arrive separately from execDetails (typically
    within ~100-500ms after the fill). Wait until every fill has its execId
    populated, or until the budget expires."""
    if not trade.fills:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if all(getattr(f.commissionReport, "execId", "") for f in trade.fills):
            return
        await asyncio.sleep(0.1)


def _extract_commissions(trade: Trade) -> tuple[float, str, list[str], float]:
    """Sum commissions and realized P&L across fills.
    Returns (commission_total, currency, exec_ids, realized_pnl_total).

    `realized_pnl` from `CommissionReport` is non-zero only on closing
    fills (e.g. the auto-flatten exit leg). Entry legs return 0.0. Both
    fields are denominated in `commission_currency`."""
    total = 0.0
    realized = 0.0
    currency = ""
    exec_ids: list[str] = []
    for f in trade.fills:
        cr = getattr(f, "commissionReport", None)
        if cr is None or not getattr(cr, "execId", ""):
            continue
        total += float(getattr(cr, "commission", 0) or 0)
        rpnl = getattr(cr, "realizedPNL", 0) or 0
        try:
            realized += float(rpnl)
        except (TypeError, ValueError):
            pass
        if not currency:
            currency = getattr(cr, "currency", "") or ""
        exec_ids.append(cr.execId)
    return total, currency, exec_ids, realized


# ---------------------------------------------------------------------------
# Per-strategy submit functions. Each returns a SubmitResult.
# ---------------------------------------------------------------------------
async def _submit_simple(
        ib: IB, contract: Contract, order: Order, timeout_s: float,
) -> SubmitResult:
    """Submit one order, wait, cancel on timeout. Used by MIDPRICE / Adaptive / MKT."""
    trade = ib.placeOrder(contract, order)
    ib_status = await _wait_for_fill_or_timeout(trade, timeout_s)
    if ib_status == "Timeout":
        await _cancel(ib, trade)
    filled, avg_px = _final_fill(trade)
    if filled > 0:
        await _wait_for_commission_reports(trade, COMMISSION_WAIT_S)
    comm_total, comm_ccy, exec_ids, realized = _extract_commissions(trade)
    return SubmitResult(
        ib_status=ib_status,
        filled_qty=filled,
        avg_fill_px=avg_px,
        n_fills=len(trade.fills),
        commission_total=comm_total,
        commission_currency=comm_ccy,
        realized_pnl_total=realized,
        exec_ids=",".join(exec_ids),
        notes=_collect_trade_notes(trade),
    )


async def _submit_lmt_mid_with_retries(
        ib: IB,
        contract: Contract,
        action: str,
        qty: float,
        tick_size: float,
        *,
        outside_rth: bool,
) -> SubmitResult:
    """LMT-at-mid with up to LMT_MID_RETRY_COUNT attempts. Each attempt re-snapshots
    the mid, posts a fresh LMT, waits LMT_MID_PER_TRY_S, cancels if unfilled. The
    most-recent attempt's outcome wins; notes accumulate across attempts;
    commissions sum across all attempts."""
    notes_all: list[str] = []
    total_filled = 0.0
    weighted_px = 0.0
    last_status = "Failed"
    n_fills_total = 0
    comm_total = 0.0
    comm_ccy = ""
    realized_total = 0.0
    exec_ids_all: list[str] = []

    for attempt in range(LMT_MID_RETRY_COUNT):
        q = await snapshot_quote(ib, contract)
        if q is None:
            notes_all.append(f"attempt{attempt + 1}:no_quote")
            last_status = "Cancelled"
            continue
        order = order_builders.build_lmt_mid(
            action, qty - total_filled, q.mid, tick_size, outside_rth=outside_rth,
        )
        trade = ib.placeOrder(contract, order)
        ib_status = await _wait_for_fill_or_timeout(trade, LMT_MID_PER_TRY_S)
        if ib_status == "Timeout":
            await _cancel(ib, trade)
        filled, avg_px = _final_fill(trade)
        if filled > 0:
            weighted_px += filled * avg_px
            total_filled += filled
            await _wait_for_commission_reports(trade, COMMISSION_WAIT_S)
            t_comm, t_ccy, t_ids, t_realized = _extract_commissions(trade)
            comm_total += t_comm
            realized_total += t_realized
            if not comm_ccy:
                comm_ccy = t_ccy
            exec_ids_all.extend(t_ids)
        n_fills_total += len(trade.fills)
        attempt_notes = _collect_trade_notes(trade)
        if attempt_notes:
            notes_all.append(f"attempt{attempt + 1}:{attempt_notes}")
        last_status = ib_status
        if total_filled >= qty * 0.999:
            break

    avg_px_combined = (weighted_px / total_filled) if total_filled > 0 else 0.0
    return SubmitResult(
        ib_status=last_status,
        filled_qty=total_filled,
        avg_fill_px=avg_px_combined,
        n_fills=n_fills_total,
        commission_total=comm_total,
        commission_currency=comm_ccy,
        realized_pnl_total=realized_total,
        exec_ids=",".join(exec_ids_all),
        notes=" | ".join(notes_all),
    )


# ---------------------------------------------------------------------------
# Trial orchestration
# ---------------------------------------------------------------------------
async def _check_eligibility(
        ib: IB, contract: Contract, strategy: str, order_types: list[str] | None,
) -> eligibility.Eligibility:
    if strategy == "LMT_MID":
        return eligibility.lmt_mid(contract)
    if strategy == "MIDPRICE_NATIVE":
        return await eligibility.midprice_native(ib, contract, order_types)
    if strategy == "MKT_ADAPTIVE":
        return await eligibility.mkt_adaptive(ib, contract, order_types)
    if strategy == "MKT_RAW":
        return eligibility.mkt_raw(contract)
    raise ValueError(f"unknown strategy: {strategy}")


def _opposite_side(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY"


async def run_trial(
        ib: IB,
        qualified: Contract,
        strategy: str,
        side: str,
        qty: float,
        *,
        tick_size: float,
        order_types: list[str],
        run_id: str,
        trial_idx: int,
        mode: str,
        outside_rth: bool = False,
        round_trip_id: str | None = None,
        leg: str | None = None,
) -> dict[str, Any]:
    """Submit one strategy on one already-qualified contract and capture the
    full metrics row. `tick_size` and `order_types` are pre-fetched per
    instrument by the caller to avoid hammering reqContractDetailsAsync
    once per cell."""
    elig = await _check_eligibility(ib, qualified, strategy, order_types)

    row: dict[str, Any] = {
        "run_id": run_id,
        "trial_idx": trial_idx,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "symbol": qualified.symbol,
        "secType": qualified.secType,
        "exchange": qualified.exchange,
        "currency": qualified.currency,
        "conId": qualified.conId,
        "expiry": qualified.lastTradeDateOrContractMonth or None,
        "multiplier": qualified.multiplier or None,
        "strategy_label": strategy,
        "eligible": elig.eligible,
        "skip_reason": elig.reason or None,
        "side": 1 if side == "BUY" else -1,
        "requested_qty": qty,
        "tick_size": tick_size,
        "paper_account": (mode == "paper"),
        "ib_server_version": ib.client.serverVersion(),
        "round_trip_id": round_trip_id,
        "leg": leg,
    }

    if not elig.eligible:
        row["status"] = "SKIPPED"
        return row

    # T0 snapshot—used for slippage measurement when available. Only LMT_MID
    # actually *requires* a live mid to construct its order; the others can
    # submit without one (IB picks the price for MIDPRICE; MKT/Adaptive don't
    # care). When there's no quote, those strategies still run, with T0
    # columns left null (slip_vs_mid_t0_bps will be null too).
    q0: Quote | None = await snapshot_quote(ib, qualified)
    if q0 is None and strategy == "LMT_MID":
        row["status"] = "SKIPPED"
        row["skip_reason"] = "no_live_quote_at_t0"
        return row

    if q0 is not None:
        row.update({
            "t0": q0.timestamp,
            "bid_t0": q0.bid,
            "ask_t0": q0.ask,
            "mid_t0": q0.mid,
            "spread_t0_bps": q0.spread_bps(),
            "spread_t0_ticks": q0.spread_ticks(tick_size),
        })

    # Wall-clock T0 anchors the VWAP window; loop-time anchors time_to_fill_s.
    t0_dt = dt.datetime.now(dt.timezone.utc)
    recorder = TickRecorder(ib, qualified)
    recorder.start()

    submit_loop_t = asyncio.get_running_loop().time()

    if strategy == "LMT_MID":
        result = await _submit_lmt_mid_with_retries(
            ib, qualified, side, qty, tick_size, outside_rth=outside_rth,
        )
    elif strategy == "MIDPRICE_NATIVE":
        order = order_builders.build_midprice_native(side, qty)
        result = await _submit_simple(
            ib, qualified, order, MIDPRICE_TIMEOUT_S,
        )
    elif strategy == "MKT_ADAPTIVE":
        order = order_builders.build_mkt_adaptive(side, qty)
        result = await _submit_simple(
            ib, qualified, order, ADAPTIVE_TIMEOUT_S,
        )
    elif strategy == "MKT_RAW":
        order = order_builders.build_mkt_raw(side, qty)
        result = await _submit_simple(
            ib, qualified, order, MKT_RAW_TIMEOUT_S,
        )
    else:
        raise ValueError(f"unknown strategy: {strategy}")

    t_fill_loop = asyncio.get_running_loop().time()
    t_fill_dt = dt.datetime.now(dt.timezone.utc)
    recorder.stop()

    status = _classify_status(result.ib_status, result.filled_qty, qty)
    row.update({
        "t_fill": t_fill_loop,
        "filled_qty": result.filled_qty,
        "avg_fill_px": result.avg_fill_px,
        "n_fills": result.n_fills,
        "time_to_fill_s": t_fill_loop - submit_loop_t,
        "status": status,
        "commission": result.commission_total if result.filled_qty > 0 else None,
        "commission_currency": result.commission_currency or None,
        "exec_ids": result.exec_ids or None,
        "realized_pnl": (
            result.realized_pnl_total if result.filled_qty > 0 else None
        ),
        "notes": result.notes or None,
    })

    # T_fill snapshot
    q_fill: Quote | None = await snapshot_quote(ib, qualified)
    if q_fill is not None:
        row.update({
            "bid_tfill": q_fill.bid,
            "ask_tfill": q_fill.ask,
            "mid_tfill": q_fill.mid,
        })

    # VWAP window—qty-weighted across AllLast ticks in [t0, t_fill].
    vwap = recorder.vwap(t0_dt, t_fill_dt)
    if vwap is not None:
        row["vwap_window"] = vwap

    if result.filled_qty > 0 and result.avg_fill_px > 0:
        avg_px = result.avg_fill_px
        if q0 is not None:
            row["slip_vs_mid_t0_bps"] = slip_vs_mid_bps(row["side"], avg_px, q0.mid)
        if q_fill is not None:
            row["slip_vs_mid_tfill_bps"] = slip_vs_mid_bps(
                row["side"], avg_px, q_fill.mid,
            )
        if vwap is not None and vwap > 0:
            row["slip_vs_vwap_bps"] = slip_vs_mid_bps(row["side"], avg_px, vwap)

    return row


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
async def _connect(mode: str) -> IB:
    """Connect and enforce mode/account consistency.

    Hard rule: refuse to start when `mode=live` is paired with a paper
    account (DU prefix). Writing paper-account fills into trials_live.parquet
    would silently corrupt the live calibration dataset. The reverse
    (`mode=paper` on a live account) is just a warning—paper-store rows
    don't drive any live decisions, but the flag mismatch is suspicious."""
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    accounts = ib.managedAccounts()
    if not accounts:
        return ib
    account = accounts[0]
    is_paper_account = account.startswith("DU")
    if mode == "live" and is_paper_account:
        ib.disconnect()
        raise SystemExit(
            f"refusing to run --mode=live against paper account {account!r}. "
            f"Switch TWS to a live account (U-prefixed) and try again."
        )
    if mode == "paper" and not is_paper_account:
        print(
            f"[warn] --mode=paper but account={account!r} is a live account. "
            f"Orders WILL fire on a real account. Results write to the paper "
            f"store; consider --mode live instead."
        )
    return ib


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Order-execution quality runner")
    p.add_argument("--mode", choices=("paper", "live"), default="paper")
    p.add_argument(
        "--side", nargs="+", choices=("BUY", "SELL"), default=["BUY"],
        help="One or more sides. Pass `BUY SELL` to run both legs in one "
             "invocation—first-order drift bias cancels across legs.",
    )
    p.add_argument(
        "--qty", type=float, default=None,
        help="Override per-instrument default qty. Default uses DEFAULT_QTY.",
    )
    p.add_argument(
        "--instruments", default="tier1",
        help="Tier name (tier1) or comma-separated symbols (e.g. ES,EURUSD).",
    )
    p.add_argument(
        "--strategies", nargs="+", default=list(ALL_STRATEGIES),
        choices=ALL_STRATEGIES,
        help="Subset of strategies to run (default: all four).",
    )
    p.add_argument(
        "--outside-rth", action="store_true",
        help="Allow pre/post-market fills for US equities (dev convenience).",
    )
    p.add_argument(
        "--yes-live", action="store_true",
        help="Required when --mode=live. Acknowledges that real orders will "
             "be sent against a live account.",
    )
    flatten = p.add_mutually_exclusive_group()
    flatten.add_argument(
        "--auto-flatten", dest="auto_flatten", action="store_true",
        default=None,
        help="After each FILLED entry leg, immediately submit a MKT_RAW "
             "exit at the same qty so net position stays ~0. Default ON in "
             "live mode, OFF in paper.",
    )
    flatten.add_argument(
        "--no-auto-flatten", dest="auto_flatten", action="store_false",
        help="Disable auto-flatten (positions accumulate).",
    )
    return p.parse_args()


def _print_summary(row: dict[str, Any]) -> None:
    print(f"  [{row['strategy_label']}]")
    for key in (
            "status", "filled_qty", "avg_fill_px", "mid_t0", "spread_t0_bps",
            "time_to_fill_s", "slip_vs_mid_t0_bps", "skip_reason", "notes",
    ):
        val = row.get(key)
        if val is not None:
            print(f"    {key}: {val}")


def _preflight_live(symbols: list[str], strategies: list[str], sides: list[str],
                    qty_table: dict[str, float], qty_override: float | None) -> None:
    """Pre-flight banner for --mode=live. Counts cells, estimates max
    commissions, requires --yes-live to have been passed by main(). Prints
    a hard-to-miss warning so accidental live invocations stand out."""
    n_cells = len(symbols) * len(strategies) * len(sides)
    # Coarse cost estimate: assume ~50% fill rate, plus 1× commission per
    # fill on entry and (conservatively) 1× on the auto-flatten exit.
    est_max_per_fill_usd = 3.0  # conservative across asset classes
    est_max_total = n_cells * est_max_per_fill_usd * 2  # entry + exit
    print()
    print("============================================================")
    print("LIVE MODE—real orders will be placed")
    print("============================================================")
    print(f"  instruments   : {symbols}")
    print(f"  strategies    : {strategies}")
    print(f"  sides         : {sides}")
    print(f"  cells         : {n_cells}")
    qty_summary = {s: (qty_override if qty_override is not None else qty_table[s])
                   for s in symbols}
    print(f"  qty per cell  : {qty_summary}")
    print(f"  est max comm  : ~${est_max_total:.0f} USD "
          f"(~$3 × 2-leg × {n_cells} cells, before fill-rate discount)")
    print("============================================================")
    print()


async def main() -> None:
    args = _parse_args()
    run_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    symbols = _expand_instruments(args.instruments)
    sides = args.side

    # Resolve auto-flatten default: explicit flag wins, else mode default.
    auto_flatten = (
        args.auto_flatten
        if args.auto_flatten is not None
        else AUTO_FLATTEN_DEFAULT_BY_MODE[args.mode]
    )
    qty_table = DEFAULT_QTY_LIVE if args.mode == "live" else DEFAULT_QTY

    if args.mode == "live":
        _preflight_live(symbols, args.strategies, sides, qty_table, args.qty)
        if not args.yes_live:
            raise SystemExit(
                "refusing to run --mode=live without --yes-live confirmation."
            )

    print(
        f"run_id={run_id}  mode={args.mode}  instruments={symbols}  "
        f"sides={sides}  qty_override={args.qty}  "
        f"outside_rth={args.outside_rth}  auto_flatten={auto_flatten}  "
        f"strategies={args.strategies}"
    )

    ib = await _connect(args.mode)
    try:
        path = None
        trial_idx = 0
        # Pre-qualify each instrument once: contract details, tick size, and
        # supported order types. Threaded through run_trial below so we don't
        # call reqContractDetailsAsync per cell (avoids the KeyError storm
        # we saw in the earlier full sweep).
        prepared: dict[str, tuple[Contract, float, list[str]]] = {}
        for symbol in symbols:
            try:
                contract = await _resolve_contract(ib, symbol)
                qualified = await _qualify_contract(ib, contract)
                tick_size = await _get_tick_size(ib, qualified)
                order_types = await eligibility.fetch_order_types(ib, qualified)
            except Exception as e:  # noqa: BLE001
                print(f"\n[{symbol}] resolve/qualify failed: {e}")
                continue
            prepared[symbol] = (qualified, tick_size, order_types)

        for side in sides:
            print(f"\n#### LEG: side={side} ####")
            for symbol in symbols:
                if symbol not in prepared:
                    continue
                qualified, tick_size, order_types = prepared[symbol]
                qty = args.qty if args.qty is not None else qty_table[symbol]
                print(
                    f"\n=== {symbol} ({side}) === secType={qualified.secType} "
                    f"exchange={qualified.exchange} "
                    f"expiry={getattr(qualified, 'lastTradeDateOrContractMonth', '') or '-'} "
                    f"qty={qty}"
                )
                for strategy in args.strategies:
                    rt_id = uuid.uuid4().hex[:12] if auto_flatten else None
                    entry_leg = "entry" if auto_flatten else None
                    entry_row = await run_trial(
                        ib, qualified, strategy, side, qty,
                        tick_size=tick_size, order_types=order_types,
                        run_id=run_id, trial_idx=trial_idx, mode=args.mode,
                        outside_rth=args.outside_rth,
                        round_trip_id=rt_id, leg=entry_leg,
                    )
                    path = results.append_row(entry_row, mode=args.mode)
                    _print_summary(entry_row)
                    trial_idx += 1

                    # Auto-flatten: if the entry filled, immediately fire a
                    # MKT_RAW exit at the same qty in the opposite direction
                    # so net exposure stays ~0. The exit row is a full
                    # parquet row, paired via round_trip_id.
                    if (
                            auto_flatten
                            and entry_row.get("status") == "FILLED"
                            and entry_row.get("filled_qty", 0) > 0
                    ):
                        exit_qty = float(entry_row["filled_qty"])
                        exit_side = _opposite_side(side)
                        print(f"  → flatten: MKT_RAW {exit_side} {exit_qty}")
                        exit_row = await run_trial(
                            ib, qualified, "MKT_RAW", exit_side, exit_qty,
                            tick_size=tick_size, order_types=order_types,
                            run_id=run_id, trial_idx=trial_idx, mode=args.mode,
                            outside_rth=args.outside_rth,
                            round_trip_id=rt_id, leg="exit",
                        )
                        path = results.append_row(exit_row, mode=args.mode)
                        _print_summary(exit_row)
                        trial_idx += 1
        print(f"\nappended → {path}")
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
