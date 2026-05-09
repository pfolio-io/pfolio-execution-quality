"""
One-shot quote snapshot + slippage helper. Shared between the production
executor (used at submit time to route TIGHT/WIDE) and the harness runner
(used at T0 and T_fill to grade execution quality).

Returning `None` from `snapshot_quote` is normal and expected — markets
closed, no subscription, instrument with sparse quotes (CFD, near-expiry
futures). Callers must handle null.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Optional

from ib_insync import IB, Contract, Ticker

QUOTE_TIMEOUT_S = 10.0


@dataclass
class Quote:
    bid: float
    ask: float
    mid: float
    timestamp: float  # event-loop time at capture

    @property
    def spread_abs(self) -> float:
        return self.ask - self.bid

    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4 if self.mid > 0 else float("nan")

    def spread_ticks(self, tick_size: float) -> float:
        if tick_size <= 0:
            return float("nan")
        return (self.ask - self.bid) / tick_size


async def snapshot_quote(
        ib: IB,
        contract: Contract,
        timeout_s: float = QUOTE_TIMEOUT_S,
) -> Optional[Quote]:
    """
    Subscribe, poll up to `timeout_s` for a valid bid/ask, capture, unsubscribe.
    Returns None if no valid quote arrives in time (closed market, no
    subscription, sparse-quote instrument). Production callers pass a short
    timeout (~2s) for fast routing; the harness uses the default 10s.
    """
    ib.reqMarketDataType(1)
    ticker: Ticker = ib.reqMktData(contract, "", False, False)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        while loop.time() < deadline:
            bid, ask = ticker.bid, ticker.ask
            if (
                    bid is not None and ask is not None
                    and not math.isnan(bid) and not math.isnan(ask)
                    and bid > 0 and ask > 0
            ):
                return Quote(bid=bid, ask=ask, mid=(bid + ask) / 2, timestamp=loop.time())
            await asyncio.sleep(0.25)
        return None
    finally:
        ib.cancelMktData(contract)


def slip_vs_mid_bps(side: int, avg_fill_px: float, mid: float) -> float:
    """
    Signed slippage in bps. side=+1 BUY, -1 SELL.
    Positive = cost (paid above mid for BUY, sold below mid for SELL).
    Negative = price improvement.
    """
    if mid <= 0 or not math.isfinite(avg_fill_px):
        return float("nan")
    return side * (avg_fill_px - mid) / mid * 1e4
