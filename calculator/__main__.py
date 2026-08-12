"""
Calculator CLI.

Examples (run from repo root):
    python -m calculator --asset-class US_STK --side BUY  --qty 100  --price 180
    python -m calculator --asset-class US_STK --side BOTH --qty 100  --price 180
    python -m calculator --asset-class FUT_CME --side BOTH --qty 1   --price 7250 --multiplier 50
    python -m calculator --asset-class EU_STK_LSE --side BOTH --qty 1000 --price 7 \
        --base-currency USD --contract-currency GBP

The CLI is intentionally thin: it constructs a `CostInput`, calls
`compute_cost`, and prints the rendered table. UI work (matrix view,
parametric form) is downstream from this.
"""

from __future__ import annotations

import argparse
import sys

from calculator.cost_model import CostInput, compute_cost


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Trading-cost calculator")
    p.add_argument("--symbol", default="?", help="Display symbol (free-text).")
    p.add_argument(
        "--asset-class", required=True,
        help="Key into broker_ibkr.json (e.g. US_STK, FUT_CME, FX_IDEALPRO).",
    )
    p.add_argument(
        "--side", choices=("BUY", "SELL", "BOTH"), default="BOTH",
        help="BOTH = round-trip (default).",
    )
    p.add_argument("--qty", type=float, required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--multiplier", type=float, default=1.0)
    p.add_argument(
        "--strategy", required=True,
        choices=("MIDPRICE_NATIVE", "LMT_MID", "MKT_ADAPTIVE", "MKT_RAW"),
        help="Order type to price slippage against. REQUIRED — there is no "
             "default, because the previous default (LMT_MID) priced every "
             "call against the order type that mostly does not execute and "
             "returned 0.00 bps for all three European buckets. The shipped "
             "policy reaches MKT_RAW.",
    )
    p.add_argument("--base-currency", default="USD")
    p.add_argument(
        "--contract-currency", default=None,
        help="Currency the contract notional is denominated in. "
             "Defaults to broker_ibkr.json[asset_class].currency.",
    )
    p.add_argument(
        "--jurisdiction", default=None,
        help="Override ISO-2 country code for transaction-tax lookup.",
    )
    p.add_argument("--holding-days", type=int, default=0)
    p.add_argument(
        "--harness-mode", choices=("paper", "live"), default="paper",
        help="Which trials store to read empirical spread from (default paper).",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    inp = CostInput(
        symbol=args.symbol,
        asset_class=args.asset_class,
        side=args.side,
        qty=args.qty,
        price=args.price,
        multiplier=args.multiplier,
        strategy=args.strategy,
        base_currency=args.base_currency,
        contract_currency=args.contract_currency,
        jurisdiction=args.jurisdiction,
        holding_days=args.holding_days,
    )
    breakdown = compute_cost(inp, harness_mode=args.harness_mode)
    print()
    print(f"  {inp.symbol}  {inp.asset_class}  {inp.side}  qty={inp.qty}  "
          f"price={inp.price}  base={inp.base_currency}")
    print()
    print(breakdown.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
