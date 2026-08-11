"""
Contract resolution helpers—shared between the production executor and the
quality harness. Stdlib-only logic; depends only on `ib_insync`.
"""

import copy
import logging

from ib_insync import IB, Contract

log = logging.getLogger(__name__)


async def _qualify_contract(
        ib: IB, contract: Contract, *, allow_exchange_fallback: bool = True,
) -> Contract:
    """
    Resolve a partial contract against IB's database.
    Fills in missing fields (conId, multiplier, tradingClass, currency, etc.).

    If the first attempt fails and an exchange was specified, retries with
    exchange='' so IB searches all venues—useful for diagnosing whether
    the exchange code is the problem.

    ⚑ `allow_exchange_fallback=False` disables that retry, and callers measuring
    a **named venue** must pass it. The retry is a helpful diagnostic when the
    exchange is routing (SMART), and a correctness hole when the exchange is the
    measurement: it can return a SMART or other-venue contract, which then trades
    and records an `exchange` that belongs to a different bucket—or to none. The
    European cells are defined by venue, so for them a contract that will not
    qualify on its own venue is a finding, not something to route around.

    Raises ValueError if IB cannot find a unique match.
    """
    qualified = await ib.qualifyContractsAsync(contract)

    if not qualified and contract.exchange and allow_exchange_fallback:
        log.warning(
            "%s: qualification failed with exchange=%r—retrying with exchange='' to search all venues",
            contract.symbol, contract.exchange,
        )
        probe = copy.copy(contract)
        probe.exchange = ""
        qualified = await ib.qualifyContractsAsync(probe)
        if qualified:
            log.info(
                "%s: found on exchange=%s—update your contract definition",
                contract.symbol, qualified[0].exchange,
            )

    if not qualified:
        raise ValueError(
            f"No security definition found for {contract.symbol} "
            f"(secType={contract.secType!r}, exchange={contract.exchange!r}, "
            f"lastTradeDateOrContractMonth={contract.lastTradeDateOrContractMonth!r}, "
            f"currency={contract.currency!r})—verify the contract fields"
            + ("" if allow_exchange_fallback else
               "; the all-venues retry is disabled for this contract because it "
               "is measuring a named venue")
        )
    if len(qualified) > 1:
        log.warning(
            "%s: %d contracts matched—using first (conId=%s exchange=%s)",
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


async def _get_price_magnifier(ib: IB, contract: Contract) -> int:
    """How many quoted units make one unit of `contract.currency`.

    **1 almost everywhere, and 100 on the London lines that quote in pence** —
    IBKR reports `currency = GBP` and then prices in GBX, so `CSP1` comes back as
    61917 meaning GBP 619.17. Anything that divides a commission by
    `qty × price` is then 100× out.

    Found 2026-08-11 by comparing a live GBP commission against the notional it
    was charged on. IBKR publishes the factor as `ContractDetails.priceMagnifier`,
    so this is read rather than inferred from the currency or the venue — a
    heuristic on 'GBP + LSE' would be a guess, and would miss the next instrument
    that does the same thing.

    ⚑ Slippage in bps is a ratio of two prices in the same units and is immune.
    Only figures denominated in *notional* are affected."""
    details = await ib.reqContractDetailsAsync(contract)
    if not details:
        raise ValueError(f"No contract details returned for {contract.symbol}")
    magnifier = int(getattr(details[0], "priceMagnifier", 1) or 1)
    if magnifier != 1:
        log.info(
            "%s quotes in 1/%d of %s — prices are magnified",
            contract.symbol, magnifier, contract.currency,
        )
    return magnifier
