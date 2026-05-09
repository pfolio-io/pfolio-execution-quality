"""
Trading-cost calculator.

Public API:
    from calculator import compute_cost, CostBreakdown, CostInput

    breakdown = compute_cost(CostInput(
        symbol="AAPL", asset_class="US_STK", side="BUY", qty=100,
        price=180.0, strategy="LMT_MID", base_currency="USD",
    ))
    print(breakdown.total_bps)

The calculator is broker-aware (`broker_ibkr.json`), market-aware
(`reg_fees.json`), and jurisdiction-aware (`tax_rules.json`). Empirical
spread/slippage data comes from the order-execution quality harness's
parquet store. See `tasks/CALCULATOR_DESIGN.md` for the broader
architecture.
"""

from calculator.cost_model import (
    CostBreakdown,
    CostInput,
    CostLine,
    compute_cost,
)

__all__ = ["compute_cost", "CostBreakdown", "CostInput", "CostLine"]
