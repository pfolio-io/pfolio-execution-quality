"""
Per-strategy eligibility checks. Shared between the production executor
(`ib_order_executor.submit_order`) and the harness runner.

A strategy is `eligible` if it can be honestly submitted for the contract;
production prunes ineligible steps from the chain, harness records
ineligible cells as SKIPPED.

LMT_MID         — needs live bid/ask. Eligible at this layer; the runtime
                  quote snapshot is the actual gate.
MIDPRICE_NATIVE — needs MIDPRICE listed in contract `orderTypes`, with a
                  rule-based fallback for US/SMART contracts.
MKT_ADAPTIVE    — secType ∉ {CASH, CFD} and MKT in contract `orderTypes`.
MKT_RAW         — universal.
"""

from __future__ import annotations

from dataclasses import dataclass

from ib_insync import IB, Contract

ADAPTIVE_INELIGIBLE_SECTYPES = {"CASH", "CFD"}
MIDPRICE_RULE_EXCHANGES = {"SMART", "NASDAQ", "NYSE", "ARCA", "BATS", "EDGX"}
MIDPRICE_RULE_CURRENCIES = {"USD"}


@dataclass
class Eligibility:
    eligible: bool
    reason: str = ""


def lmt_mid(contract: Contract) -> Eligibility:
    """Eligible at this layer; runtime quote snapshot is the actual gate."""
    return Eligibility(eligible=True)


def mkt_raw(contract: Contract) -> Eligibility:
    """Universal."""
    return Eligibility(eligible=True)


async def fetch_order_types(ib: IB, contract: Contract) -> list[str]:
    """Return the contract's supported order-type strings, or [] on failure.
    Prefer calling this **once per instrument** and threading the result
    through eligibility checks rather than refetching per cell."""
    try:
        details = await ib.reqContractDetailsAsync(contract)
    except Exception:  # noqa: BLE001
        return []
    if not details or not details[0].orderTypes:
        return []
    return [o.strip() for o in details[0].orderTypes.split(",")]


async def midprice_native(
        ib: IB, contract: Contract, order_types: list[str] | None = None,
) -> Eligibility:
    """MIDPRICE eligible iff listed in contract orderTypes, with US/SMART rule fallback."""
    types = order_types if order_types is not None else await fetch_order_types(ib, contract)
    if "MIDPRICE" in types:
        return Eligibility(eligible=True)
    if (
            getattr(contract, "currency", "") in MIDPRICE_RULE_CURRENCIES
            and getattr(contract, "exchange", "") in MIDPRICE_RULE_EXCHANGES
    ):
        return Eligibility(eligible=True, reason="rule_based_fallback")
    return Eligibility(eligible=False, reason="MIDPRICE_not_supported")


async def mkt_adaptive(
        ib: IB, contract: Contract, order_types: list[str] | None = None,
) -> Eligibility:
    """Adaptive eligible iff secType allows it and MKT is in orderTypes (with rule fallback)."""
    sec = getattr(contract, "secType", "")
    if sec in ADAPTIVE_INELIGIBLE_SECTYPES:
        return Eligibility(eligible=False, reason=f"secType_{sec}_excluded")
    types = order_types if order_types is not None else await fetch_order_types(ib, contract)
    if not types or "MKT" in types:
        return Eligibility(eligible=True)
    return Eligibility(eligible=False, reason="MKT_not_supported")
