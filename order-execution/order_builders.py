"""
Strategy order builders. Each returns an ib_insync Order configured for a
single in-isolation submission (no escalation chain). Callers orchestrate
submission, fill-watch, timeout, and cancellation per strategy. Shared
between production (`ib_order_executor`) and harness (`quality.runner`).

Builders: `build_lmt_mid`, `build_midprice_native`, `build_mkt_adaptive`,
`build_mkt_raw`. All take `action` (BUY|SELL) and `qty` (positive float);
LMT_MID also needs the live `mid` and `tick_size`.
"""

from __future__ import annotations

import math

from ib_insync import LimitOrder, MarketOrder, Order, TagValue


def round_to_tick(price: float, tick_size: float) -> float:
    """Round a price to the nearest valid tick."""
    if tick_size <= 0 or not math.isfinite(price):
        return price
    return round(price / tick_size) * tick_size


def build_lmt_mid(
        action: str,
        qty: float,
        mid: float,
        tick_size: float,
        *,
        outside_rth: bool = False,
) -> Order:
    """Plain LMT at the rounded mid. No algo, no Adaptive, no escalation.
    `outside_rth=True` allows pre/post-market fills (US equities)."""
    px = round_to_tick(mid, tick_size)
    return LimitOrder(action, qty, px, tif="DAY", outsideRth=outside_rth)


def build_midprice_native(action: str, qty: float) -> Order:
    """IB native MIDPRICE algo. IB chooses the price; no lmtPrice."""
    order = Order()
    order.action = action
    order.orderType = "MIDPRICE"
    order.totalQuantity = qty
    order.tif = "DAY"
    return order


def build_mkt_adaptive(action: str, qty: float, *, priority: str = "Normal") -> Order:
    """Adaptive algo wrapping a MKT order. Priority ∈ {Urgent, Normal, Patient}."""
    order = Order()
    order.action = action
    order.orderType = "MKT"
    order.totalQuantity = qty
    order.tif = "DAY"
    order.algoStrategy = "Adaptive"
    order.algoParams = [TagValue("adaptivePriority", priority)]
    return order


def build_mkt_raw(action: str, qty: float) -> Order:
    """Plain MarketOrder with no algo."""
    return MarketOrder(action, qty, tif="DAY")
