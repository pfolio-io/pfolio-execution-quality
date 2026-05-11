"""Harness-only execution-quality helpers: VWAP via tick-by-tick recorder.

`Quote`, `snapshot_quote`, and `slip_vs_mid_bps` were promoted to the
shared `quote_snapshot` module—re-exported here for harness call-site
stability.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from ib_insync import IB, Contract, Ticker
from quote_snapshot import Quote, slip_vs_mid_bps, snapshot_quote  # noqa: F401  re-export

VWAP_MIN_WINDOW_S = 1.0  # window shorter than this → null (per spec)


class TickRecorder:
    """
    Wrap `reqTickByTickData("AllLast")` to compute trade-VWAP over a
    `[t0, t_fill]` wall-clock window. Designed to fail-soft: if IB refuses
    the subscription (e.g. errorCode 354—no market data permission), the
    accumulator just stays empty and `vwap()` returns None.

    Usage:
        rec = TickRecorder(ib, contract)
        rec.start()                                   # at T0
        ...
        rec.stop()                                    # after fill
        v = rec.vwap(t0_dt, t_fill_dt)                # → Optional[float]
    """

    def __init__(self, ib: IB, contract: Contract) -> None:
        self.ib = ib
        self.contract = contract
        self._ticker: Optional[Ticker] = None
        self._started: bool = False

    def start(self) -> None:
        try:
            self._ticker = self.ib.reqTickByTickData(
                self.contract, "AllLast", numberOfTicks=0, ignoreSize=False,
            )
            self._started = True
        except Exception:  # noqa: BLE001—fail-soft; null VWAP is acceptable
            self._ticker = None
            self._started = False

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self.ib.cancelTickByTickData(self.contract, "AllLast")
        except Exception:  # noqa: BLE001
            pass

    def vwap(self, start_dt: dt.datetime, end_dt: dt.datetime) -> Optional[float]:
        """Qty-weighted average price across all received `AllLast` ticks
        in `[start_dt, end_dt]`. Returns None if subscription was refused,
        no ticks in window, or window <VWAP_MIN_WINDOW_S."""
        if self._ticker is None:
            return None
        if (end_dt - start_dt).total_seconds() < VWAP_MIN_WINDOW_S:
            return None
        ticks = getattr(self._ticker, "tickByTicks", None) or []
        weighted = 0.0
        size_total = 0.0
        for t in ticks:
            t_time = getattr(t, "time", None)
            t_price = float(getattr(t, "price", 0) or 0)
            t_size = float(getattr(t, "size", 0) or 0)
            if t_time is None or t_price <= 0 or t_size <= 0:
                continue
            if start_dt <= t_time <= end_dt:
                weighted += t_price * t_size
                size_total += t_size
        if size_total <= 0:
            return None
        return weighted / size_total
