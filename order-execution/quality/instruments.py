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

from ib_insync import IB, Future

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
