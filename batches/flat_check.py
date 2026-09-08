"""Is the account flat in the contracts this harness has ever traded?

**READ-ONLY. Places no orders, cancels nothing, imports no order builder.** It
reads positions, filters them to the symbols the live store shows the harness has
traded, prints those, and counts everything else without naming it.

Why it exists, and why it runs BEFORE a batch rather than after. The harness
already reads positions back **after** a live run
(`quality/runner.py::_report_open_positions`). Nothing reads them before one, so
a residue from a previous session is invisible at the moment it matters most —
when the next batch's round-trip pairing is about to be computed against an
opening position that is not zero.

There is a concrete residue to check. `trials_live.parquet` records **31 SXR8
entry fills and 30 exit fills**: the 2026-08-12 incident in which an auto-flatten
`MKT_RAW` exit timed out and left 1 share long (`quality/README.md`, "A batch
left a position open"). The store cannot say whether it was later closed by hand
in TWS, because a manual close never passes through the harness. Only the account
can say. That is this script's first question.

Usage:

    python batches/flat_check.py                 # IB Gateway, live (4001)
    python batches/flat_check.py --port 4002     # IB Gateway, paper
    python batches/flat_check.py --port 7496     # TWS, if one is running

Positions outside the harness's traded set are counted, never printed: this is a
pre-flight check on one experiment, not a portfolio read.
"""

from __future__ import annotations

import argparse
import asyncio

import nest_asyncio
from ib_insync import IB

nest_asyncio.apply()

CLIENT_ID = 62  # distinct from runner's 41, preflight's 42, fx read's 61

# Every symbol `trials_live.parquet` carries, as IBKR reports it. The European
# lines resolve by ISIN, so the local ticker is what a position shows up under.
HARNESS_SYMBOLS = {
    # European listing lines — the ones a batch can leave open
    "SXR8", "CSP1", "CHSPI",
    # US and other cells
    "AAPL", "SPY", "LQD", "EFA", "PRIM", "BBSI",
    "ES", "VIX", "DX",
    "EUR", "USD",  # FX / CFD legs report as the base currency symbol
}
WATCH_FIRST = ("SXR8", "CSP1", "CHSPI")


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Read-only pre-batch flat check. Places no orders.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4001,
                   help="4001 Gateway live (harness default) · 4002 Gateway "
                        "paper · 7496 TWS")
    args = p.parse_args()

    print("flat_check — READ-ONLY. No orders will be placed.")
    ib = IB()
    await ib.connectAsync(args.host, args.port, clientId=CLIENT_ID)
    try:
        accounts = ib.managedAccounts()
        account = accounts[0] if accounts else "(none reported)"
        kind = "PAPER" if account.startswith("DU") else "LIVE"
        print(f"connected: {args.host}:{args.port}  account={account} ({kind})\n")

        positions = await ib.reqPositionsAsync()
        mine, other = [], 0
        for pos in positions:
            if not pos.position:
                continue
            symbol = getattr(pos.contract, "symbol", "") or ""
            if symbol in HARNESS_SYMBOLS:
                mine.append(pos)
            else:
                other += 1

        print("=" * 62)
        for symbol in WATCH_FIRST:
            hits = [p for p in mine if p.contract.symbol == symbol]
            if hits:
                for pos in hits:
                    print(f"⚑ {symbol:<8} position={pos.position} "
                          f"avgCost={pos.avgCost} "
                          f"{pos.contract.currency} "
                          f"{pos.contract.primaryExchange or pos.contract.exchange}")
            else:
                print(f"  {symbol:<8} FLAT")
        rest = [p for p in mine if p.contract.symbol not in WATCH_FIRST]
        for pos in rest:
            print(f"⚑ {pos.contract.symbol:<8} position={pos.position} "
                  f"avgCost={pos.avgCost} (harness symbol, not a European line)")
        print("=" * 62)
        if not mine:
            print("FLAT in every contract this harness has traded. Safe to fire.")
        else:
            print("⚑ NOT FLAT. Close these by hand before the batch, or the "
                  "round-trip pairing starts from a non-zero position.")
        print(f"positions outside the harness's set: {other} (not shown — this is "
              f"a pre-flight check, not a portfolio read)")
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
