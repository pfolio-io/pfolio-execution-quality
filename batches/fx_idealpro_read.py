"""IDEALPRO mid quotes for every currency `cost_tables/fx_rates.json` carries.

**READ-ONLY. This script places no orders and cannot: it imports no order
builder, constructs no `Order`, and never calls `placeOrder`.** It opens one API
connection, asks for live level-1 FX quotes, prints them, and disconnects.

Why it exists. `fx_rates.json`'s own `_doc` promises **IDEALPRO mid quotes**, and
three of its twelve rows (MXN, SEK, SGD, added 2026-09-03) are Riksbank fixings
instead — SEK natively, MXN and SGD as SEK crosses re-anchored to USD. The other
nine are IDEALPRO mids from 2026-05-04. The table is read by
`calculator/cost_model.py` to express every cost component in the user's base
currency, and is fetched from `@main` by the public tool, so a stale or
off-source rate biases a published bps figure for every non-USD base currency.
This reads the declared source, so the table can be refreshed from it.

**It does not write the table.** `cost_tables/` is canon and read-only to every
agent (hq convention 8, S1-33). The output goes to Marcel; the edit is his call
or a measured refresh, and whether a central-bank fixing is admissible here at
all is a question this script deliberately leaves open by answering it from
IDEALPRO instead.

Usage, from the repo root or anywhere:

    python batches/fx_idealpro_read.py                 # IB Gateway, live (4001)
    python batches/fx_idealpro_read.py --port 4002     # IB Gateway, paper
    python batches/fx_idealpro_read.py --port 7496     # TWS, if one is running

The default is the live IB Gateway socket, matching
`quality/runner.py::IB_PORT_BY_MODE["live"]` (4001). The Gateway is permanent on
this machine and its sockets are shared with another workspace — see the batch
spec beside this
file. FX quotes are identical either way; the account only decides entitlement.

A pair that returns no two-sided quote prints `—` and is reported as such. It is
never back-filled from the existing table, from a cross, or from anywhere else:
blank beside n = 0 is the honest rendering, a carried number is not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path

import nest_asyncio
from ib_insync import IB, Forex

nest_asyncio.apply()

CLIENT_ID = 61  # distinct from runner's 41 and preflight's 42
QUOTE_TIMEOUT_S = 8.0

# (currency, IDEALPRO pair, invert) — `fx_rates.json` stores the USD price of one
# unit of the currency, so XXXUSD pairs are taken as-is and USDXXX pairs inverted.
PAIRS: tuple[tuple[str, str, bool], ...] = (
    ("EUR", "EURUSD", False),
    ("GBP", "GBPUSD", False),
    ("AUD", "AUDUSD", False),
    ("NZD", "NZDUSD", False),
    ("CAD", "USDCAD", True),
    ("CHF", "USDCHF", True),
    ("JPY", "USDJPY", True),
    ("HKD", "USDHKD", True),
    ("MXN", "USDMXN", True),
    ("SEK", "USDSEK", True),
    ("SGD", "USDSGD", True),
)

_TABLE = (Path(__file__).resolve().parent.parent / "order-execution" / "quality"
          / "cost_tables" / "fx_rates.json")


async def _mid(ib: IB, pair: str) -> tuple[float | None, float | None, float | None]:
    """(bid, ask, mid) or three Nones. Live data only — no delayed fallback, so a
    missing entitlement is a blank rather than a quietly wrong number."""
    contract = Forex(pair)
    await ib.qualifyContractsAsync(contract)
    ib.reqMarketDataType(1)
    ticker = ib.reqMktData(contract, "", False, False)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QUOTE_TIMEOUT_S
    try:
        while loop.time() < deadline:
            bid, ask = ticker.bid, ticker.ask
            if (bid is not None and ask is not None
                    and not math.isnan(bid) and not math.isnan(ask)
                    and bid > 0 and ask > 0):
                return bid, ask, (bid + ask) / 2
            await asyncio.sleep(0.2)
        return None, None, None
    finally:
        ib.cancelMktData(contract)


async def main() -> None:
    p = argparse.ArgumentParser(description="Read IDEALPRO mids. Places no orders.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4001,
                   help="4001 Gateway live (harness default) · 7496 TWS · "
                        "4002 Gateway paper")
    args = p.parse_args()

    try:
        current = json.loads(_TABLE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        current = {}

    print("fx_idealpro_read — READ-ONLY. No orders will be placed.")
    ib = IB()
    await ib.connectAsync(args.host, args.port, clientId=CLIENT_ID)
    try:
        accounts = ib.managedAccounts()
        account = accounts[0] if accounts else "(none reported)"
        kind = "PAPER" if account.startswith("DU") else "LIVE"
        print(f"connected: {args.host}:{args.port}  account={account} ({kind})\n")

        header = (f"{'ccy':<5}{'pair':<9}{'bid':>12}{'ask':>12}"
                  f"{'USD per 1 unit':>16}{'in table':>12}{'drift':>9}")
        print(header)
        print("-" * len(header))
        read: dict[str, float | None] = {}
        for ccy, pair, invert in PAIRS:
            bid, ask, mid = await _mid(ib, pair)
            if mid is None:
                read[ccy] = None
                print(f"{ccy:<5}{pair:<9}{'—':>12}{'—':>12}{'—':>16}"
                      f"{current.get(ccy, '—'):>12}{'—':>9}")
                continue
            value = (1.0 / mid) if invert else mid
            read[ccy] = value
            old = current.get(ccy)
            drift = (f"{(value / old - 1) * 100:+.2f}%"
                     if isinstance(old, (int, float)) and old else "—")
            print(f"{ccy:<5}{pair:<9}{bid:>12.5f}{ask:>12.5f}{value:>16.6f}"
                  f"{old if old is not None else '—':>12}{drift:>9}")

        missing = [c for c, v in read.items() if v is None]
        print()
        if missing:
            print(f"⚑ NO QUOTE: {missing} — reported blank, not carried forward. "
                  f"Either the market is shut for that pair or the account has no "
                  f"entitlement for it.")
        else:
            print("All pairs quoted.")
        print("\nRows as they would read (USD price of 1 unit), for whoever edits "
              "the table — this script does not:")
        print(json.dumps({"USD": 1.0, **{c: (round(v, 6) if v is not None else None)
                                         for c, v in read.items()}}, indent=2))
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
