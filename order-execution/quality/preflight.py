"""
Read-only readiness check. **Places no orders, in either mode.**

    cd order-execution && python -m quality.preflight            # the eu tier
    python -m quality.preflight --instruments eu,SPY

**Why this exists.** For US cells, "can the harness trade this?" and "will the
trial produce a measurement?" are the same question, because the market data has
been subscribed since the harness was built. For the three European venues they
come apart, and they come apart in the expensive direction:

`snapshot_quote` asks for live data only (`reqMarketDataType(1)`), no delayed
fallback. Without a subscription for the venue, IB answers error 354 and the
snapshot returns None. `LMT_MID` then records SKIPPED and costs nothing — but
`MIDPRICE_NATIVE`, `MKT_ADAPTIVE` and `MKT_RAW` **submit, fill, and record
`mid_t0 = null`**, so `slip_vs_mid_t0_bps` is null, `bucket_strategy_matrix`
drops the row, and the bucket stays UNMEASURED. Full commission on both legs,
no measurement, and nothing louder than a printed warning.

So the questions this answers, for free, before any order:

1. Which account is TWS pointed at, and is it paper (DU) or live (U)?
2. Does the contract resolve **on its own venue**, and from which ISIN?
3. Would its trials land in the intended `EU_STK_*` bucket?
4. Does a two-sided quote arrive — i.e. is the market data actually there?
5. How deep is the touch, so "one share is representative" is checked against
   the book rather than argued?
6. Which strategies are eligible, so a live batch is priced on the strategies
   that exist rather than on four?

It is also the only honest input to the live batch estimate: at one share every
European order pays the per-order minimum, so the batch cost is orders ×
minimum, and the number of orders depends on answer 6.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import nest_asyncio
from ib_insync import IB, Contract, Ticker

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contract_helpers import _get_tick_size, _qualify_contract  # noqa: E402

import eligibility  # noqa: E402
from quality import runner  # noqa: E402

nest_asyncio.apply()

PREFLIGHT_CLIENT_ID = 42  # distinct from the runner's 41, so both can be open
QUOTE_TIMEOUT_S = 10.0


@dataclass
class VenueReport:
    symbol: str
    ok: bool = False
    detail: str = ""
    sec_id: str | None = None
    contract_symbol: str = ""
    exchange: str = ""
    currency: str = ""
    con_id: int = 0
    bucket: str | None = None
    tick_size: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    spread_bps: float | None = None
    eligible: list[str] = field(default_factory=list)
    ineligible: dict[str, str] = field(default_factory=dict)


async def _quote_with_sizes(ib: IB, contract: Contract) -> tuple[
        float | None, float | None, float | None, float | None]:
    """(bid, ask, bidSize, askSize) or four Nones. Sizes are the point: the plan
    trades one share, and whether that is representative depends on the touch
    being deeper than one share — which is a fact about the book, not an
    argument. `snapshot_quote` does not return sizes, so this reads the ticker
    directly rather than widening a shape production also uses."""
    ib.reqMarketDataType(1)
    ticker: Ticker = ib.reqMktData(contract, "", False, False)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QUOTE_TIMEOUT_S
    try:
        while loop.time() < deadline:
            bid, ask = ticker.bid, ticker.ask
            if (bid is not None and ask is not None
                    and not math.isnan(bid) and not math.isnan(ask)
                    and bid > 0 and ask > 0):
                return bid, ask, ticker.bidSize, ticker.askSize
            await asyncio.sleep(0.25)
        return None, None, None, None
    finally:
        ib.cancelMktData(contract)


async def check_symbol(ib: IB, symbol: str) -> VenueReport:
    """One instrument, read-only. Never raises: a failure is the finding."""
    rep = VenueReport(symbol=symbol)
    is_eu = symbol in runner.EU_LINES

    try:
        contract, sec_id = await runner._resolve_contract(ib, symbol)
        qualified = await _qualify_contract(
            ib, contract, allow_exchange_fallback=not is_eu,
        )
    except Exception as exc:  # noqa: BLE001
        rep.detail = (
            f"NO CONTRACT — {exc}. On a European venue this is as likely to be a "
            f"missing trading permission (IB error 201) as a wrong contract."
        )
        return rep

    rep.sec_id = sec_id
    rep.contract_symbol = qualified.symbol
    rep.exchange = qualified.exchange
    rep.currency = qualified.currency
    rep.con_id = qualified.conId
    rep.bucket = runner.bucket_of(
        qualified.symbol, qualified.secType, qualified.exchange, qualified.currency,
    )

    # ⚑ No pre-trade venue check any more (E-14): these cells route SMART, so
    # the venue is chosen at execution and is not knowable here. `rep.bucket` is
    # what the row would bucket as if it executed where it was sent — for a SMART
    # cell that is `None`, and correctly so. Where it really goes is reported
    # after the run by `runner.venue_coverage`.

    try:
        rep.tick_size = await _get_tick_size(ib, qualified)
    except Exception as exc:  # noqa: BLE001
        rep.detail = f"no tick size: {exc}"

    order_types = await eligibility.fetch_order_types(ib, qualified)
    for strategy in runner.ALL_STRATEGIES:
        elig = await runner._check_eligibility(ib, qualified, strategy, order_types)
        if elig.eligible:
            rep.eligible.append(strategy)
        else:
            rep.ineligible[strategy] = elig.reason

    bid, ask, bid_sz, ask_sz = await _quote_with_sizes(ib, qualified)
    rep.bid, rep.ask, rep.bid_size, rep.ask_size = bid, ask, bid_sz, ask_sz
    if bid is None:
        rep.detail = (
            "NO MARKET DATA — no two-sided quote in "
            f"{QUOTE_TIMEOUT_S:.0f}s. ⚑ DO NOT RUN THIS VENUE: LMT_MID would be "
            "SKIPPED, and the other three would FILL and record a null "
            "slippage — full commission, no measurement. Either the venue's "
            "market-data subscription is missing (IB error 354) or the market "
            "is closed (all three trade "
            f"{runner.EU_COMMON_WINDOW_CEST} CEST)."
        )
        return rep

    mid = (bid + ask) / 2
    rep.spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else None
    rep.ok = True
    rep.detail = "READY"
    return rep


def _fmt(value, spec: str = "") -> str:
    if value is None:
        return "—"
    return format(value, spec) if spec else str(value)


def render(reports: list[VenueReport]) -> str:
    lines: list[str] = []
    for rep in reports:
        mark = "✓" if rep.ok else "✗"
        lines.append(f"\n{mark} {rep.symbol}")
        if rep.con_id:
            lines.append(
                f"    contract  : {rep.contract_symbol} on {rep.exchange} "
                f"in {rep.currency}  conId={rep.con_id}"
                + (f"  ISIN={rep.sec_id}" if rep.sec_id else "")
            )
            lines.append(
                f"    bucket    : {rep.bucket or 'decided at fill (SMART)'}")
        if rep.tick_size is not None:
            lines.append(f"    tick      : {rep.tick_size}")
        if rep.bid is not None:
            lines.append(
                f"    quote     : {_fmt(rep.bid)} / {_fmt(rep.ask)}  "
                f"spread={_fmt(rep.spread_bps, '.2f')} bps  "
                f"sizes={_fmt(rep.bid_size)} × {_fmt(rep.ask_size)}"
            )
        if rep.eligible or rep.ineligible:
            lines.append(f"    eligible  : {rep.eligible or '—'}")
            if rep.ineligible:
                lines.append(f"    not       : {rep.ineligible}")
        lines.append(f"    verdict   : {rep.detail}")
    return "\n".join(lines)


def render_batch_estimate(reports: list[VenueReport], sides: int = 2) -> str:
    """What one full pass over the ready European venues would cost.

    Commission only, from `cost_tables/broker_ibkr.json`, because at one share
    the per-order minimum binds and everything else is cents. Transaction taxes
    are NOT included: SIX and the UK both levy, and whether they apply depends
    on the IBKR entity and on the line that resolved."""
    eu = [r for r in reports if r.ok and r.symbol in runner.EU_LINES]
    if not eu:
        return "\nNo European venue is ready — no batch to price."
    try:
        broker = json.loads(
            (Path(__file__).parent / "cost_tables" / "broker_ibkr.json").read_text()
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return "\n⚑ broker_ibkr.json unreadable — cannot price the batch."

    lines = [
        "\n" + "=" * 60,
        f"ONE FULL PASS over the ready European venues ({sides} sides, "
        f"auto-flatten on)",
        "=" * 60,
    ]
    total_trials = total_orders = 0
    for rep in eu:
        line = runner.EU_LINES[rep.symbol]
        rule = broker.get(line["expect_bucket"], {})
        minimum = rule.get("min_per_order")
        ccy = rule.get("currency", "")
        trials = len(rep.eligible) * sides
        orders = trials * 2  # entry + flattening exit
        total_trials += trials
        total_orders += orders
        cost = f"{ccy} {orders * minimum:.2f}" if minimum is not None else "?"
        lines.append(
            f"  {line['label']:<26}: {len(rep.eligible)} strategies × {sides} sides "
            f"= {trials} trials, {orders} orders → {cost}"
        )
    lines += [
        f"  TOTAL : {total_trials} trials, {total_orders} orders",
        "  ⚑ Commission only. Transaction taxes (SIX, UK) are NOT included and "
        "depend on the",
        "    IBKR entity and the resolved line — see cost_tables/tax_rules.json.",
        "  ⚑ At one share the per-order minimum binds, so the cost is orders × "
        "minimum and",
        "    raising the notional does not reduce it.",
        "=" * 60,
    ]
    return "\n".join(lines)


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Read-only readiness check. Places no orders.",
    )
    p.add_argument(
        "--instruments", default="eu",
        help="Tier name (tier1, tier2, tier3, eu, all) or comma-separated "
             "symbols. Default: the three European venues.",
    )
    args = p.parse_args()
    symbols = runner._expand_instruments(args.instruments)

    print("preflight — READ-ONLY. No orders will be placed.")
    ib = IB()
    await ib.connectAsync(runner.IB_HOST, runner.IB_PORT, clientId=PREFLIGHT_CLIENT_ID)
    try:
        accounts = ib.managedAccounts()
        account = accounts[0] if accounts else "(none reported)"
        kind = "PAPER" if account.startswith("DU") else "LIVE"
        print(f"connected: account={account} ({kind})  "
              f"serverVersion={ib.client.serverVersion()}")
        print(f"instruments: {symbols}")

        reports = [await check_symbol(ib, s) for s in symbols]
        print(render(reports))
        print(render_batch_estimate(reports))

        blocked = [r.symbol for r in reports if not r.ok]
        if blocked:
            print(f"\n⚑ NOT READY: {blocked} — do not include these in a live "
                  f"batch. Each verdict above says why.")
        else:
            print("\nAll requested instruments are ready.")
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
