"""
Contract resolution helpers — shared between the production executor and the
quality harness. Stdlib-only logic; depends only on `ib_insync`.
"""

import copy
import logging

from ib_insync import IB, Contract

log = logging.getLogger(__name__)


async def _qualify_contract(ib: IB, contract: Contract) -> Contract:
    """
    Resolve a partial contract against IB's database.
    Fills in missing fields (conId, multiplier, tradingClass, currency, etc.).

    If the first attempt fails and an exchange was specified, retries with
    exchange='' so IB searches all venues — useful for diagnosing whether
    the exchange code is the problem.

    Raises ValueError if IB cannot find a unique match.
    """
    qualified = await ib.qualifyContractsAsync(contract)

    if not qualified and contract.exchange:
        log.warning(
            "%s: qualification failed with exchange=%r — retrying with exchange='' to search all venues",
            contract.symbol, contract.exchange,
        )
        probe = copy.copy(contract)
        probe.exchange = ""
        qualified = await ib.qualifyContractsAsync(probe)
        if qualified:
            log.info(
                "%s: found on exchange=%s — update your contract definition",
                contract.symbol, qualified[0].exchange,
            )

    if not qualified:
        raise ValueError(
            f"No security definition found for {contract.symbol} "
            f"(secType={contract.secType!r}, exchange={contract.exchange!r}, "
            f"lastTradeDateOrContractMonth={contract.lastTradeDateOrContractMonth!r}, "
            f"currency={contract.currency!r}) — verify the contract fields"
        )
    if len(qualified) > 1:
        log.warning(
            "%s: %d contracts matched — using first (conId=%s exchange=%s)",
            contract.symbol, len(qualified), qualified[0].conId, qualified[0].exchange,
        )
    c = qualified[0]
    log.info(
        "Qualified: %s secType=%s exchange=%s conId=%s multiplier=%s tradingClass=%s",
        c.symbol, c.secType, c.exchange, c.conId, c.multiplier, c.tradingClass,
    )
    return c


async def _get_tick_size(ib: IB, contract: Contract) -> float:
    """Query IB contract details for minimum tick size."""
    details = await ib.reqContractDetailsAsync(contract)
    if not details:
        raise ValueError(f"No contract details returned for {contract.symbol}")
    tick = details[0].minTick
    log.info("Tick size for %s: %s", contract.symbol, tick)
    return tick
