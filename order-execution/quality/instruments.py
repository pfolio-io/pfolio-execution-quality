"""
Instrument helpers for the quality harness.

Front-month resolution is done **directly via IB** so the public repo runs
standalone (no `investing_tools` dependency). Approach: query
`reqContractDetailsAsync` with an empty `lastTradeDateOrContractMonth`,
sort returned contracts by expiry, return the earliest one ≥ today + N
calendar days (buffer to avoid the active roll window).
"""

from __future__ import annotations

import datetime as dt
import logging

from ib_insync import IB, Contract, Future

log = logging.getLogger(__name__)

# Buffer in calendar days. Plan says "≥ today + 5 trading days"; 5 calendar
# days is close enough for monthly/quarterly futures and avoids needing a
# trading-calendar dep.
ROLL_BUFFER_DAYS = 5


async def resolve_front_month(
        ib: IB,
        symbol: str,
        exchange: str,
        *,
        currency: str = "USD",
        buffer_days: int = ROLL_BUFFER_DAYS,
        skip: int = 0,
) -> Future:
    """Return a Future for `symbol` on `exchange` from the sorted list of
    expiries ≥ today + `buffer_days`. `skip=0` returns the front month
    (default), `skip=1` returns the second-front (e.g. VIX_FAR), etc.
    Raises ValueError if not enough qualifying contracts."""
    template = Future(symbol=symbol, exchange=exchange, currency=currency)
    details = await ib.reqContractDetailsAsync(template)
    if not details:
        raise ValueError(f"no contract details for Future({symbol}, {exchange})")

    cutoff = dt.date.today() + dt.timedelta(days=buffer_days)
    candidates: list[tuple[dt.date, Future]] = []
    for d in details:
        c = d.contract
        raw = c.lastTradeDateOrContractMonth or ""
        try:
            if len(raw) == 8:
                expiry = dt.datetime.strptime(raw, "%Y%m%d").date()
            elif len(raw) == 6:
                # YYYYMM form—treat as last day of that month
                year, month = int(raw[:4]), int(raw[4:6])
                expiry = (
                        dt.date(year + (month // 12), (month % 12) + 1, 1)
                        - dt.timedelta(days=1)
                )
            else:
                continue
        except ValueError:
            continue
        if expiry >= cutoff:
            candidates.append((expiry, c))

    candidates.sort(key=lambda t: t[0])
    if len(candidates) <= skip:
        raise ValueError(
            f"only {len(candidates)} qualifying {symbol}/{exchange} contracts "
            f"≥ today+{buffer_days}d; cannot skip {skip}"
        )
    return candidates[skip][1]


async def resolve_by_isin(
        ib: IB,
        candidates: "tuple[tuple[str, str], ...]",
        exchange: str,
        *,
        currency: str = "",
) -> Contract:
    """First of `candidates` that IBKR can qualify as a stock on `exchange`.

    `candidates` is `((isin, human_name), ...)`, tried in order.

    **Why ISIN and not a ticker.** A UCITS ETF trades under a different local
    ticker on every venue it is listed on, and nothing in this repo — or in the
    pfolio universe screen that sourced these funds — records which ticker
    belongs to which venue. The ISIN is the same everywhere and is what the
    screen actually carries, so it is the only identifier here that is a fact
    rather than a guess.

    **Why the exchange is explicit.** The caller is measuring a named venue. A
    SMART-routed contract reports `exchange = SMART` on every trial row, and the
    venue-partitioned buckets in `asset_class_buckets.json` would then have
    nothing to key on. Measuring the router is not measuring the venue.

    `currency` is passed through when the caller knows it and left empty
    otherwise: IBKR will report the listing's own currency, and the bucket map
    matches on what came back rather than on what anyone expected.

    Raises ValueError naming every candidate tried, because "no European
    instrument resolved" is a finding about the account's permissions or market
    data as often as it is about the contract.
    """
    tried = []
    for isin, name in candidates:
        template = Contract(
            secType="STK", exchange=exchange, currency=currency,
            secIdType="ISIN", secId=isin,
        )
        try:
            details = await ib.reqContractDetailsAsync(template)
        except Exception as exc:  # noqa: BLE001 — IB raises several unrelated types
            tried.append(f"{isin} ({name}): {exc!r}")
            continue
        if not details:
            tried.append(f"{isin} ({name}): no contract details on {exchange}")
            continue
        contract = details[0].contract
        log.info(
            "Resolved %s on %s: symbol=%s currency=%s conId=%s (%s)",
            isin, exchange, contract.symbol, contract.currency, contract.conId, name,
        )
        return contract

    raise ValueError(
        f"no ISIN candidate resolved on exchange={exchange!r}. Tried:\n  "
        + "\n  ".join(tried)
        + "\nThis is as likely to be a missing European market-data subscription "
          "or trading permission as a wrong contract — check the account before "
          "changing the candidate list."
    )
