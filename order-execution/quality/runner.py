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
to and the `paper_account` field; it does not change the TWS port. The harness
**refuses** to start when `--mode` disagrees with the connected account's DU/U
prefix, in either direction — `--mode live` on a DU account would corrupt the
live calibration dataset, and `--mode paper` on a live account would spend real
money and file the result as synthetic. `--allow-live-account` overrides the
second when that is genuinely intended.

For the per-instrument list and per-strategy timeout/retry policy see the
constants block below.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nest_asyncio
import pandas as pd
from ib_insync import CFD, IB, Contract, Forex, Order, Stock, Trade

# Reuse shared primitives. Harness is independent of the production
# executor (`ib_order_executor`)—only contract-resolution helpers are
# shared via `contract_helpers`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contract_helpers import (  # noqa: E402
    _get_price_magnifier, _get_tick_size, _qualify_contract,
)

import eligibility  # noqa: E402
import order_builders  # noqa: E402
from quality import buckets, instruments, results  # noqa: E402
from quality.metrics import TickRecorder  # noqa: E402
from quote_snapshot import Quote, slip_vs_mid_bps, snapshot_quote  # noqa: E402

nest_asyncio.apply()

IB_HOST = "127.0.0.1"
# IB Gateway is permanent on this machine (2026-09-08, Marcel's ruling):
# 4001 live, 4002 paper. Those sockets are shared with the personal-investing
# workspace, so the client moves, not the Gateway. The old default was TWS's
# 7496, which served both accounts on one socket; the Gateway does not, which
# is why the port is now keyed by mode rather than a single constant.
IB_PORT_BY_MODE = {"paper": 4002, "live": 4001}
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
    #: Venue(s) the fills actually executed on, comma-joined, from
    #: `execution.exchange`. NOT the exchange that was requested — see
    #: `_exec_exchanges`.
    exec_exchange: str = ""
    notes: str = ""


ALL_STRATEGIES = ("MIDPRICE_NATIVE", "LMT_MID", "MKT_ADAPTIVE", "MKT_RAW")

# Tier 1—high liquidity. Tier 2—medium liquidity / wider spread.
# Tier 3—low liquidity / structurally wider. Order matters only for
# run-time logs; analysis treats each (instrument × strategy) cell
# independently.
TIER1 = ("AAPL", "SPY", "ES", "EURUSD")
TIER2 = ("LQD", "EFA", "VIX", "CFD_USD_CHF")
TIER3 = ("DX", "VIX_FAR", "SMALL_CAP")

# European venues. A separate tier rather than an addition to tier 2, because
# they are the only cells that need European market data and a European trading
# permission, and a run that lacks either should be able to skip them by name.
TIER_EU = ("EU_ETF_EUR", "EU_ETF_GBP", "EU_ETF_CHF")

TIERS = {
    "tier1": TIER1,
    "tier2": TIER2,
    "tier3": TIER3,
    "eu": TIER_EU,
    "all": TIER1 + TIER2 + TIER3 + TIER_EU,
}

# ---------------------------------------------------------------------------
# European instruments — resolved by ISIN, never by a venue ticker
# ---------------------------------------------------------------------------
# `broker_ibkr.json` has carried EU_STK_XETRA / EU_STK_LSE / EU_STK_SIX
# commission rules since 2026-05-04, and `reg_fees.json` and `tax_rules.json`
# carry the PTM levy and the stamp duties. Nothing has ever traded on any of the
# three, so the execution term — the one component S1-33 says may never be
# assumed — has no measurement, and the calculator was returning it as 0.00.
# These three cells are what makes it measurable.
#
# ⚑ RESOLUTION IS BY ISIN, and that is not a stylistic choice. A UCITS ETF is
# listed under a different local ticker on every venue, and NOTHING in this
# workspace records which ticker belongs to which venue — the pfolio universe
# screen's `venues` column is null on all 1,851 rows. A hardcoded ticker here
# would be a guess, and a wrong guess fails as "contract not found" on a venue
# we would then wrongly believe we had tested.
#
# ⚑ AND THE EXCHANGE IS EXPLICIT, NOT SMART. The bucket these rows land in is
# defined by venue; a SMART-routed order records `exchange = SMART`, so the row
# could not say which venue it measured. Measuring the router is not measuring
# the venue.
#
# Candidates are the broadest, largest UCITS equity ETFs in the pfolio universe
# screen, taken from `pfolio-apps/pfolio-app/research/universe-screen/data/
# instruments.json` (issuer feed, iShares CH screener) rather than from memory.
# They are tried in order and the first that IBKR can qualify on the venue wins;
# which one that is, is recorded per trial row, so the report says what it
# actually traded.
# ⚑ ORDER CHANGED 2026-08-11, after asking IBKR what it actually lists. The
# criterion is unchanged — largest, broadest UCITS equity lines — but one fact
# only visible from the listings decides between them: **IBKR bills market data
# on the PRIMARY exchange, not on the venue you route to.**
#
#   IE00B4L5Y983  EUR line = conId 100292038, primary **AEB** (Amsterdam),
#                 merely *routable* to IBIS2. So a Xetra order in it is an
#                 Amsterdam-primary instrument sent to Xetra, and its quote is
#                 billed as Amsterdam data, which no German subscription covers.
#   IE00B5BMR087  EUR line = conId 75776072, symbol SXR8, primary **IBIS2**.
#                 Genuinely German-primary. It is what "measuring XETRA" means,
#                 and it is what a German market-data subscription pays for.
#
# It also keeps one fund across all three venues, which is what §2 of the record
# is protecting: SXR8 on IBIS2 (EUR) · CSPX on LSEETF (USD) · CSPX on EBS (USD).
# The MSCI World line stays as the first fallback — same issuer, same shape, and
# it is the larger of the two by a hair on the screen's own AUM column.
EU_ISIN_CANDIDATES = (
    ("IE00B5BMR087", "iShares Core S&P 500 UCITS ETF"),         # ~USD 151bn
    ("IE00B4L5Y983", "iShares Core MSCI World UCITS ETF"),      # ~USD 144bn
    ("IE00BKM4GZ66", "iShares Core MSCI EM IMI UCITS ETF"),     # ~USD 41bn
)

# IBKR venue codes and the currency each venue's line is expected in. The
# currency is NOT asserted — IBKR reports what the listing actually is, and the
# bucket map matches on that. It is a resolution *preference* (tried first, then
# dropped) so repeated sessions land on the same line, and it is recorded here so
# a surprise is visible.
#
# `bucket` is the calculator asset class each venue's trials must land in. It is
# checked against `asset_class_buckets.json` before the first order rather than
# discovered at analysis time: a European cell that resolves to the wrong venue
# still trades, still costs the commission, and then falls out of the matrix as
# an unmapped instrument with nothing but a printed warning.
# ⚑ THE EUROPEAN CELLS ROUTE **SMART**, AND ARE LISTING LINES, NOT VENUES
# (E-14, Marcel, 2026-08-11 — record §7e). This supersedes the "explicit
# exchange, never SMART" rule **for these cells only**; the FUT/FX cells have one
# venue each and the US cells have always been SMART.
#
# Why. The question being answered is *what does a user actually pay*, and no
# retail client directs orders — ours cannot even do it (the account is refused
# with error 10311 on every venue, including the one SMART itself chose). Routing
# SMART measures what users realise; directing would price a counterfactual.
#
# Why LINES and not venues. Three venue requests cannot survive SMART, because
# the LSE and SIX lines of this fund are the **same conId** — there is nothing to
# ask for. What a user does choose between is the listing currency, so the cells
# are the three lines of one fund and the venue becomes an observation recorded
# in `exec_exchange`.
#
# `bucket` is the venue bucket each line is EXPECTED to land in, from the primary
# listing — a prior for the coverage report, never a filter. Where it really goes
# is measured.
# ⚑ The CHF cell uses a DIFFERENT FUND, and that is forced, not chosen
# (measured 2026-08-11). The three global candidates have **zero** CHF listings
# between them — 0 of 26, 28 and 26 — and none is SIX-primary. The only ETFs that
# are natively SIX-listed in CHF are Swiss-domiciled trackers, so measuring SIX at
# all means measuring a Swiss-equity fund.
#
# That breaks the one-fund-across-cells property §2 was protecting, and under
# E-14 it costs less than it would have: cross-venue comparability was already
# given up when the venue stopped being chosen. What is left is cost per user, and
# a Swiss client buying a Swiss-listed CHF tracker is a real population. The
# confound is real and is stated rather than hidden — the CHF cell measures a
# different asset class, so its spread is not comparable with the other two.
#
# Ordered by the same criterion as the global chain — broadest first, then
# liquidity. `CSBGC0` is deliberately absent: 17.4 bps against 4.0–8.8 for the
# others on the same morning.
EU_CHF_ISIN_CANDIDATES = (
    ("CH0237935652", "iShares Core SPI (CH) — broad Swiss market"),
    ("CH0017142719", "UBS ETF (CH) SMI — 20 blue chips, tightest quote"),
    ("CH0130595124", "UBS ETF (CH) SPI Mid"),
)

EU_LINES = {
    "EU_ETF_EUR": {"currency": "EUR", "label": "EUR line (Xetra-primary)",
                   "expect_bucket": "EU_STK_XETRA"},
    "EU_ETF_GBP": {"currency": "GBP", "label": "GBP line (LSE-primary)",
                   "expect_bucket": "EU_STK_LSE"},
    "EU_ETF_CHF": {"currency": "CHF", "label": "CHF line (SIX-primary, Swiss fund)",
                   "expect_bucket": "EU_STK_SIX",
                   "isins": EU_CHF_ISIN_CANDIDATES},
    # ⚑ NOT IN `TIER_EU`, and the reason is a finding rather than an omission
    # (2026-08-11): **IBKR publishes no SMART listing for the USD line.** It
    # resolves only on direct venues — LSEETF, EBS and the MTFs — so a user
    # routing SMART cannot buy it, and a cost-per-user measurement of it would be
    # of something no user can reach. Kept addressable by name so
    # `--instruments EU_ETF_USD` still says that out loud, and so it is one line
    # away from runnable if directed routing is ever enabled.
    "EU_ETF_USD": {"currency": "USD", "label": "USD line (LSE-primary, direct only)",
                   "expect_bucket": "EU_STK_LSE"},
}

# `EU_LINES` is a superset of `TIER_EU`: the USD line is addressable by name
# but excluded from the tier, because IBKR publishes no SMART listing for it.
KNOWN_SYMBOLS = TIER1 + TIER2 + TIER3 + tuple(EU_LINES)

# European regular trading hours, for the guard below and for the operator.
# XETRA 09:00–17:30 CET/CEST · SIX 09:00–17:20 · LSE 08:00–16:30 London.
# Common window: 09:00–17:20 CEST. These venues have no meaningful extended
# session on UCITS ETF lines, so `--outside-rth` cannot help and can only make an
# unfilled limit rest for its whole retry budget.
EU_COMMON_WINDOW_CEST = "09:00–17:20"

# Tier-3 small-cap default. The spec leaves the small-cap "TBD"—swap
# this constant if the chosen ticker becomes illiquid or delists.
SMALL_CAP_SYMBOL = "PRIM"  # Primoris Services (NYSE-listed small-cap). 2026-05-11: ran a one-off BBSI sweep as small-cap-class replication test; BBSI fills aggregate via asset_class_buckets.json.

# Per-instrument tiny notional. FX needs IDEALPRO minimum (≥20k base);
# CFD on USD/CHF needs at least ~1k base. Single-share/contract for STK/FUT.
DEFAULT_QTY = {
    "AAPL": 1.0, "SPY": 1.0, "ES": 1.0, "EURUSD": 20000.0,
    "LQD": 1.0, "EFA": 1.0, "VIX": 1.0, "CFD_USD_CHF": 1000.0,
    "DX": 1.0, "VIX_FAR": 1.0, "SMALL_CAP": 1.0,
    # One share, like every other equity cell. The European commission rules are
    # bps-of-value with a per-order MINIMUM (EUR 1.25 / GBP 1.00 / CHF 1.50), so
    # a one-share order is dominated by that minimum — which is a fact about the
    # schedule, not a distortion of the measurement: `slip_vs_mid_t0_bps` is the
    # quantity these cells exist to measure and it is size-independent at this
    # notional. The commission column is read from the fill either way.
    "EU_ETF_EUR": 1.0, "EU_ETF_GBP": 1.0, "EU_ETF_USD": 1.0, "EU_ETF_CHF": 1.0,
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


async def _resolve_contract(ib: IB, symbol: str) -> tuple[Contract, str | None]:
    """Resolve one harness symbol to a contract, plus the ISIN it resolved from
    when resolution went by ISIN (European cells) and `None` otherwise.

    Equities/ETFs use SMART/USD; futures resolve front-month via IB; FX uses
    IDEALPRO; CFDs use SMART. Tier-3 names follow the same routing with
    `VIX_FAR` skipping the front contract. The ISIN is returned rather than
    logged because it is provenance for a published figure: the local ticker
    differs per venue, so `symbol` alone does not say which fund was measured."""
    if symbol in ("AAPL", "SPY", "LQD", "EFA"):
        return Stock(symbol, "SMART", "USD"), None
    if symbol == "SMALL_CAP":
        return Stock(SMALL_CAP_SYMBOL, "SMART", "USD"), None
    if symbol == "ES":
        return await instruments.resolve_front_month(ib, "ES", "CME"), None
    if symbol == "VIX":
        return await instruments.resolve_front_month(ib, "VIX", "CFE"), None
    if symbol == "VIX_FAR":
        return await instruments.resolve_front_month(ib, "VIX", "CFE", skip=1), None
    if symbol == "DX":
        return await instruments.resolve_front_month(ib, "DX", "NYBOT"), None
    if symbol == "EURUSD":
        return Forex("EURUSD"), None
    if symbol == "CFD_USD_CHF":
        return CFD("USD", "SMART", "CHF"), None
    if symbol in EU_LINES:
        # SMART, with the LISTING CURRENCY as the discriminator — that is what
        # separates the three cells now that the venue is an observation. The
        # currency is a real filter here, unlike the old venue lookup where it
        # was only a preference: two lines of one fund differ by nothing else.
        line = EU_LINES[symbol]
        return await instruments.resolve_by_isin(
            ib, line.get("isins", EU_ISIN_CANDIDATES), "SMART",
            currency=line["currency"], require_currency=True,
        )
    raise ValueError(f"unknown symbol {symbol!r}; known: {KNOWN_SYMBOLS}")


# ---------------------------------------------------------------------------
# The venue guard
# ---------------------------------------------------------------------------
def bucket_of(symbol: str, sec_type: str, exchange: str, currency: str) -> str | None:
    """Which calculator asset class a contract's trials would land in, or None.

    Asks `quality/buckets.py` — the same reader `analyze.py` uses — so the guard
    cannot drift from the map it is enforcing. Pure: no IB, no I/O beyond the
    bucket JSON, which is why it is testable without a broker connection."""
    row = pd.DataFrame([{
        "symbol": symbol, "secType": sec_type,
        "exchange": exchange, "currency": currency, "expiry": None,
    }])
    return buckets.bucket_series(row).iloc[0]


def venue_coverage(rows: list[dict]) -> dict[str, dict]:
    """Where SMART actually sent the European fills, and what canon knows about
    those venues. `{venue: {"fills": n, "bucket": name|None, "priced": bool}}`.

    ⚑ **This replaced a pre-trade guard** (E-14, §7e). `venue_guard` refused to
    trade when the resolved contract would not land in the intended bucket; under
    SMART there is no intended bucket to check before the fill, because the venue
    is chosen by the router at execution time. So the check moves after the fact
    and changes job: it no longer prevents a bad row, it **reports which venues
    the router used and which of them canon cannot price**.

    That report is the discovery mechanism. European commission varies by venue,
    `broker_ibkr.json` has rules for three of them, and SMART is under no
    obligation to use those three — it sent a Xetra-primary ETF to GETTEX2 on
    2026-08-11. A fill on a venue with no rule is priced by nothing, and this is
    what says so out loud instead of leaving a hole in a total."""
    bucket_map = buckets.load_bucket_map()
    try:
        broker = json.loads(
            (Path(__file__).parent / "cost_tables" / "broker_ibkr.json").read_text()
        )
    except (FileNotFoundError, json.JSONDecodeError):
        broker = {}
    eu_rows = [r for r in rows
               if r.get("status") == "FILLED" and r.get("exec_exchange")]
    out: dict[str, dict] = {}
    for row in eu_rows:
        for venue in str(row["exec_exchange"]).split(","):
            venue = venue.strip()
            if not venue:
                continue
            entry = out.setdefault(venue, {"fills": 0, "bucket": None, "priced": False})
            entry["fills"] += 1
            probe = pd.DataFrame([{
                "symbol": row.get("symbol", ""), "secType": row.get("secType", ""),
                "exchange": venue, "exec_exchange": venue,
                "currency": row.get("currency", ""), "expiry": None,
            }])
            bucket = buckets.bucket_series(probe, bucket_map).iloc[0]
            entry["bucket"] = bucket
            entry["priced"] = bool(bucket and isinstance(broker.get(bucket), dict))
    return out


def _print_venue_coverage(rows: list[dict]) -> None:
    coverage = venue_coverage(rows)
    if not coverage:
        return
    print("\n" + "=" * 60)
    print("WHERE SMART ACTUALLY EXECUTED")
    print("=" * 60)
    unpriced = []
    for venue, info in sorted(coverage.items(), key=lambda kv: -kv[1]["fills"]):
        mark = "✓" if info["priced"] else "⚑"
        print(f"  {mark} {venue:<12} fills={info['fills']:<4} "
              f"bucket={info['bucket'] or '— none —'}")
        if not info["priced"]:
            unpriced.append(venue)
    if unpriced:
        print(f"\n  ⚑ NO COMMISSION RULE FOR: {unpriced}")
        print("    Those fills are priced by nothing. `cost_tables/` needs a "
              "bucket and a")
        print("    commission rule per venue before any total that includes "
              "them is complete.")
    print("=" * 60)


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


def _exec_exchanges(trade: Trade) -> str:
    """Where the fills actually executed, comma-joined, in order of appearance.

    ⚑ **This is not the same thing as the exchange that was requested**, and on
    2026-08-11 the difference was measured rather than imagined: a SMART-routed
    order in `SXR8` — a Xetra-primary ETF — executed on **`GETTEX2`**, a different
    German venue with a different fee schedule. Had the row been attributed by
    the requested exchange, a Gettex fill would have been published as XETRA and
    priced against XETRA's commission rule.

    Direct routing keeps the two identical, which is exactly why the European
    cells route directly. This column is what proves it stayed true, and it is
    the only field that would catch a silent re-route."""
    seen: list[str] = []
    for f in trade.fills:
        venue = getattr(getattr(f, "execution", None), "exchange", "") or ""
        if venue and venue not in seen:
            seen.append(venue)
    return ",".join(seen)


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
        exec_exchange=_exec_exchanges(trade),
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
    exec_venues: list[str] = []

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
            for v in _exec_exchanges(trade).split(","):
                if v and v not in exec_venues:
                    exec_venues.append(v)
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
        exec_exchange=",".join(exec_venues),
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
        sec_id: str | None = None,
        price_magnifier: int = 1,
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
        # The ISIN when resolution went by ISIN. A UCITS ETF's local ticker
        # differs per venue, so `symbol` does not say which fund was measured
        # and a published European figure needs that on the row, not in a log.
        "sec_id": sec_id,
        # 100 on pence-quoted London lines, 1 elsewhere. Recorded per row so
        # anything computing a notional can divide by it; see
        # contract_helpers._get_price_magnifier.
        "price_magnifier": price_magnifier,
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
        "exec_exchange": result.exec_exchange or None,
        "realized_pnl": (
            result.realized_pnl_total if result.filled_qty > 0 else None
        ),
        "notes": result.notes or None,
    })

    # Where it executed. Under SMART a venue different from the request is the
    # normal case and says nothing — the alarm is a venue **canon cannot price**,
    # because that fill enters no bucket and no commission rule.
    if result.exec_exchange:
        if qualified.exchange != "SMART" and result.exec_exchange != qualified.exchange:
            print(f"    ⚑ ROUTED AWAY from a directed order: requested "
                  f"{qualified.exchange!r}, executed on {result.exec_exchange!r}")
        unpriced = [v for v, info in venue_coverage([row | {
            "status": "FILLED", "exec_exchange": result.exec_exchange,
        }]).items() if not info["priced"]]
        if unpriced:
            print(f"    ⚑ executed on {unpriced} — no commission rule; this fill "
                  f"is priced by nothing")

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
async def _connect(mode: str, *, allow_live_account: bool = False) -> IB:
    """Connect and enforce mode/account consistency, in both directions.

    Hard rule: refuse to start when `mode=live` is paired with a paper
    account (DU prefix). Writing paper-account fills into trials_live.parquet
    would silently corrupt the live calibration dataset.

    ⚑ **The reverse is now also a refusal** *(2026-08-10)*, with
    `--allow-live-account` as the escape hatch. It used to be a `print` reading
    "Orders WILL fire on a real account" — and then they did, with the results
    written to the paper store, where the repo's own caveat says fills are
    synthetic and unquotable. That was survivable while nobody had a reason to
    be logged into the live account. Both halves changed the day the European
    market-data subscriptions were bought in the live account and the first
    European paper run was scheduled for the next morning: a run intended to
    cost nothing would have spent real commission, and the store it landed in is
    the one place that would not show it."""
    ib = IB()
    await ib.connectAsync(IB_HOST, IB_PORT_BY_MODE[mode], clientId=IB_CLIENT_ID)
    accounts = ib.managedAccounts()
    if not accounts:
        return ib
    account = accounts[0]
    refusal = account_mode_refusal(
        mode, account, allow_live_account=allow_live_account,
    )
    if refusal:
        ib.disconnect()
        raise SystemExit(refusal)
    if mode == "paper" and not account.startswith("DU"):
        print(
            f"[warn] --mode=paper against LIVE account {account!r} with "
            f"--allow-live-account. REAL ORDERS WILL FIRE and the fills go to "
            f"the paper store."
        )
    return ib


def account_mode_refusal(
        mode: str, account: str, *, allow_live_account: bool = False,
) -> str:
    """`""` when `mode` may run against `account`, else the reason it may not.

    Pure, so the two directions can be tested without a broker connection —
    which matters because both of them exist to prevent something that is only
    discovered after it has already happened."""
    is_paper_account = account.startswith("DU")
    if mode == "live" and is_paper_account:
        return (
            f"refusing to run --mode=live against paper account {account!r}. "
            f"Switch TWS to a live account (U-prefixed) and try again."
        )
    if mode == "paper" and not is_paper_account and not allow_live_account:
        return (
            f"refusing to run --mode=paper against LIVE account {account!r}. "
            f"Real orders would fire and the fills would be written to the "
            f"paper store, which is documented as synthetic and unquotable. "
            f"Point TWS at the paper account (DU-prefixed), or pass "
            f"--allow-live-account if spending real money into the paper store "
            f"is genuinely what you want."
        )
    return ""


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
        help="Tier name (tier1, tier2, tier3, eu, all) or comma-separated "
             "symbols (e.g. ES,EURUSD). `eu` is the three European venues and "
             "needs European market data plus a trading permission.",
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
    p.add_argument(
        "--allow-live-account", action="store_true",
        help="Permit --mode=paper against a LIVE (non-DU) account. Real orders "
             "fire and the fills land in the paper store, which is documented "
             "as synthetic. Without this the runner refuses.",
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


def check_outside_rth(symbols: list[str], outside_rth: bool) -> str:
    """`""` when the flag combination is runnable, else the reason it is not.

    `--outside-rth` reaches only `build_lmt_mid`, and the European venues have no
    meaningful extended session on UCITS ETF lines: the limit would rest unfilled
    for its whole 30 s retry budget and the cell would record a TIMEOUT that says
    nothing about execution quality. Refused rather than documented, because a
    caveat in a README does not survive `--instruments all --outside-rth`."""
    if not outside_rth:
        return ""
    eu = [s for s in symbols if s in EU_LINES]
    if not eu:
        return ""
    return (
        f"--outside-rth cannot be combined with European cells {eu}: these "
        f"venues have no extended session on these lines, so the flag can only "
        f"produce TIMEOUTs. Run the European tier inside "
        f"{EU_COMMON_WINDOW_CEST} CEST in its own invocation."
    )


def _eu_commission_estimate(symbols: list[str], n_orders_per_cell: int,
                            n_cells_per_symbol: int) -> list[str]:
    """Per-venue expected commission for the European cells, from the measured
    schedule in `broker_ibkr.json` rather than from the flat cross-asset guess.

    At one share every European order pays the per-order MINIMUM, so the cost of
    a European batch is (orders × minimum) and is independent of notional — the
    opposite of the usual instinct that a smaller trial is a cheaper trial. That
    makes the estimate exact enough to approve against, which the $3-per-fill
    heuristic is not."""
    eu = [s for s in symbols if s in EU_LINES]
    if not eu:
        return []
    try:
        broker = json.loads(
            (Path(__file__).parent / "cost_tables" / "broker_ibkr.json").read_text()
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return ["  ⚑ EU commission estimate unavailable (broker_ibkr.json unreadable)"]
    lines = []
    for symbol in eu:
        rule = broker.get(EU_LINES[symbol]["expect_bucket"], {})
        minimum = rule.get("min_per_order")
        ccy = rule.get("currency", "")
        if minimum is None:
            continue
        orders = n_cells_per_symbol * n_orders_per_cell
        lines.append(
            f"  {EU_LINES[symbol]['label']:<26}: {orders} orders × "
            f"{ccy} {minimum:.2f} min/order = {ccy} {orders * minimum:.2f} "
            f"(at 1 share the minimum binds; notional does not reduce it)"
        )
    return lines


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
    eu = [s for s in symbols if s in EU_LINES]
    if eu:
        # The flat estimate above is a cross-asset guess. For the European cells
        # the schedule is known exactly at this size, so print it.
        print(f"  EU cells      : {eu} — expected commission, from "
              f"cost_tables/broker_ibkr.json:")
        for line in _eu_commission_estimate(
                eu, n_orders_per_cell=2,  # entry + auto-flatten exit
                n_cells_per_symbol=len(strategies) * len(sides),
        ):
            print(line)
        # The US cells cost commission and negligible reg fees. The European
        # ones can attract a transaction TAX, which the estimate above does not
        # model and which is charged on notional rather than per order — SIX and
        # the UK both levy, and the exemptions (Irish-domiciled UCITS on the LSE;
        # the PTM levy's GBP 10k floor) depend on the specific line that
        # resolves, which is only known after IBKR answers.
        print(f"  ⚑ EU cells    : {eu} — these venues may levy a TRANSACTION TAX "
              f"on notional, which the estimates above do NOT include")
        print("                  (see cost_tables/tax_rules.json and reg_fees.json)")
        print(f"  ⚑ EU window   : {EU_COMMON_WINDOW_CEST} CEST is the only window "
              f"in which all three venues trade")
    print("============================================================")
    print()


async def main() -> None:
    args = _parse_args()
    run_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    symbols = _expand_instruments(args.instruments)
    sides = args.side

    rth_refusal = check_outside_rth(symbols, args.outside_rth)
    if rth_refusal:
        raise SystemExit(rth_refusal)

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

    ib = await _connect(args.mode, allow_live_account=args.allow_live_account)
    try:
        path = None
        trial_idx = 0
        # Pre-qualify each instrument once: contract details, tick size, and
        # supported order types. Threaded through run_trial below so we don't
        # call reqContractDetailsAsync per cell (avoids the KeyError storm
        # we saw in the earlier full sweep).
        all_rows: list[dict[str, Any]] = []
        prepared: dict[str, tuple[Contract, float, list[str], str | None, int]] = {}
        for symbol in symbols:
            is_eu = symbol in EU_LINES
            try:
                contract, sec_id = await _resolve_contract(ib, symbol)
                # ⚑ No all-venues retry for a European cell: the venue IS the
                # measurement, so a contract that will not qualify on its own
                # venue is a finding, not something to route around.
                qualified = await _qualify_contract(
                    ib, contract, allow_exchange_fallback=not is_eu,
                )
                tick_size = await _get_tick_size(ib, qualified)
                magnifier = await _get_price_magnifier(ib, qualified)
                order_types = await eligibility.fetch_order_types(ib, qualified)
            except Exception as e:  # noqa: BLE001
                print(f"\n[{symbol}] resolve/qualify failed: {e}")
                continue
            if is_eu:
                print(
                    f"[{symbol}] resolved ISIN={sec_id} → symbol={qualified.symbol} "
                    f"routing={qualified.exchange} currency={qualified.currency} "
                    f"conId={qualified.conId} "
                    f"(expect ~{EU_LINES[symbol]['expect_bucket']}; "
                    f"the venue is measured, not requested)"
                )
            prepared[symbol] = (qualified, tick_size, order_types, sec_id,
                                magnifier)

        for side in sides:
            print(f"\n#### LEG: side={side} ####")
            for symbol in symbols:
                if symbol not in prepared:
                    continue
                (qualified, tick_size, order_types, sec_id,
                 magnifier) = prepared[symbol]
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
                        round_trip_id=rt_id, leg=entry_leg, sec_id=sec_id,
                        price_magnifier=magnifier,
                    )
                    path = results.append_row(entry_row, mode=args.mode)
                    all_rows.append(entry_row)
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
                            round_trip_id=rt_id, leg="exit", sec_id=sec_id,
                            price_magnifier=magnifier,
                        )
                        path = results.append_row(exit_row, mode=args.mode)
                        all_rows.append(exit_row)
                        _print_summary(exit_row)
                        trial_idx += 1
        print(f"\nappended → {path}")
        _print_venue_coverage(all_rows)
        if args.mode == "live":
            await _report_open_positions(ib, prepared)
    finally:
        if ib.isConnected():
            ib.disconnect()


async def _report_open_positions(
        ib: IB, prepared: dict[str, tuple],
) -> None:
    """After a live run, name anything the batch left open.

    Auto-flatten fires a MKT_RAW exit after every filled entry, but an exit can
    time out and the trial row records that quietly, one row among dozens. These
    are measurement trials, not positions: a residue is a thing to close, not a
    view. Read-only — it reports, it does not trade, because an automatic
    corrective order at the end of a batch is a second uncontrolled order."""
    con_ids = {c.conId: sym for sym, (c, *_rest) in prepared.items() if c.conId}
    try:
        positions = await ib.reqPositionsAsync()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[positions] could not read positions: {exc!r}. "
              f"CHECK THE ACCOUNT MANUALLY before leaving the batch.")
        return
    left_open = [
        p for p in positions
        if getattr(p.contract, "conId", None) in con_ids and p.position
    ]
    print("\n============================================================")
    if not left_open:
        print("FLAT — no traded contract carries a position. Batch left nothing open.")
    else:
        print("⚑ OPEN POSITIONS LEFT BY THIS BATCH — FLATTEN THESE")
        for p in left_open:
            print(f"  {con_ids[p.contract.conId]:<10} {p.contract.symbol} "
                  f"{p.contract.exchange or p.contract.primaryExchange} "
                  f"position={p.position} avgCost={p.avgCost}")
        print("  Not auto-corrected on purpose: an unattended corrective order "
              "is a second uncontrolled order.")
    print("============================================================")


if __name__ == "__main__":
    asyncio.run(main())
