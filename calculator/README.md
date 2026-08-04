# Trading-cost calculator

UI-agnostic engine that turns `(instrument, qty, price, side)` into a bps
breakdown of expected trading cost. Pairs with the harness in
[`order-execution/quality/`](../order-execution/quality/), which produces
the empirical spread and slippage data the engine consumes. See
[`../METHODOLOGY.md`](../METHODOLOGY.md) for the cost decomposition.

## Status: V0.5 (engine + CLI + harness-backed spread + slippage)

What's implemented:

- ✅ Commission via `broker_ibkr.json`
- ✅ Regulatory fees via `reg_fees.json` (SEC, FINRA TAF, NFA, exchange clearing, PTM)
- ✅ Transaction taxes via `tax_rules.json` (UK stamp, FR/IT FTT, CH stamp, BE TOB)
- ✅ FX conversion to user's `base_currency` via `fx_rates.json`
- ✅ Round-trip (`side=BOTH`) sums both legs using the same policy strategy.
- ✅ **Realized execution cost** from harness median
  (`harness_data.median_slip_bps_by_strategy`). `slip_vs_mid_t0_bps` already
  captures both the half-spread that MKT pays and the ≈0 cost of LMT/MIDPRICE
  filled at mid; no separate spread-cost line is added (the previous design
  double-counted). When a strategy has no fills (e.g. MKT_ADAPTIVE in paper),
  the line is emitted as a placeholder so the breakdown stays structurally
  complete. Negative measured slippage is capped at zero in the total to
  avoid promising guaranteed price improvement; the `note` field records
  the raw measurement.

⚠️ Paper slippage is **not actionable** for limit-style strategies—IB
sim fills LMT/MIDPRICE at the mid deterministically, producing a
spuriously negative median. Source string flags this; switch to
`harness_mode='live'` once Phase 6.5 data is in.

Not yet:

- Impact model `impact_bps(size)`—needs live size sweep
- Carry / financing for shorts and CFDs
- Live FX-rate snapshot at fill (currently uses static `fx_rates.json`)

## Lookup tables

All in `order-execution/quality/cost_tables/`:

| File                       | Purpose                                       |
|----------------------------|-----------------------------------------------|
| `broker_ibkr.json`         | IBKR Pro tiered commission schedule           |
| `reg_fees.json`            | Per-market regulatory fees (SEC, FINRA, …)    |
| `tax_rules.json`           | Per-jurisdiction transaction taxes            |
| `fx_rates.json`            | USD-anchor FX rates                           |
| `asset_class_buckets.json` | asset_class → harness instrument selectors     |

These are version-controlled with the calculator. Refresh from
authoritative sources (linked in each JSON's `_doc` field) when rates
drift more than ~5%.

### ⚑ A priced class is not a measured class

`broker_ibkr.json` prices more asset classes than the harness has ever traded.
Where that is true the execution term has **no measurement**, and until
2026-08-04 the calculator returned it as `0.00 bps` inside a total that looked
complete — a European round-trip priced at 61.43 bps with commission, PTM levy
and stamp duty present and **execution counted as zero**.

The breakdown now says so. `CostLine.unmeasured` marks a placeholder,
`CostBreakdown.is_complete` is false when one is present, and `render()` prints
`PARTIAL TOTAL` with the missing components named. The **arithmetic is
deliberately unchanged** — a placeholder still contributes 0, because the defect
was never the number, it was an incomplete total presenting as a complete one.

`harness_data.measurement_state(asset_class)` returns `measured` ·
`unmeasured` · `undeclared`. Check it before quoting a total.

## Usage

```bash
# US large-cap, round-trip
python -m calculator --asset-class US_STK --side BOTH --qty 100 --price 180

# ES futures round-trip
python -m calculator --asset-class FUT_CME --side BOTH --qty 1 --price 7250 --multiplier 50

# UK stock with stamp duty (notice the 50 bps tax line)
python -m calculator --asset-class EU_STK_LSE --side BOTH \
    --qty 1000 --price 7 --base-currency USD --contract-currency GBP

# Programmatic
from calculator import compute_cost, CostInput
b = compute_cost(CostInput(
    symbol="AAPL", asset_class="US_STK", side="BOTH",
    qty=100, price=180, base_currency="USD",
))
print(b.total_bps)        # 2.68
print(b.render())         # full breakdown table
```

## CLI flags

| Flag                  | Required | Notes                                                    |
|-----------------------|----------|----------------------------------------------------------|
| `--asset-class`       | yes      | Key in `broker_ibkr.json` (e.g. `US_STK`, `FUT_CME`)     |
| `--qty`               | yes      | Unsigned magnitude                                       |
| `--price`             | yes      | Reference price (mid or last)                            |
| `--side`              | no       | `BUY` / `SELL` / `BOTH` (default `BOTH`)                 |
| `--multiplier`        | no       | Contract multiplier (default 1)                          |
| `--strategy`          | no       | Reserved for slippage lookup (default `LMT_MID`)         |
| `--base-currency`     | no       | Output currency (default `USD`)                          |
| `--contract-currency` | no       | Notional denomination (default = broker rule's currency) |
| `--jurisdiction`      | no       | ISO-2 code override for tax lookup                       |
| `--holding-days`      | no       | Reserved for carry (default 0)                           |

## Architecture

```
calculator/
  __init__.py           # public API: compute_cost, CostInput, CostBreakdown
  __main__.py           # CLI entry point
  cost_model.py         # all cost component computers, table loader
  README.md
```

Pure functions throughout—`compute_cost(CostInput, CostTables)` is a
deterministic mapping from inputs + tables to a `CostBreakdown`. Tables
are loaded once via `CostTables.load()`; the result is reusable across
multiple `compute_cost` calls.

## Matrix CSV export

For the V1 public matrix view, run the harness analyzer with
`--export-matrix-csv`:

```bash
cd order-execution
python -m quality.analyze --mode live  --export-matrix-csv quality/results/matrix_live.csv
python -m quality.analyze --mode paper --export-matrix-csv quality/results/matrix_paper.csv
```

Output schema: rows = asset-class bucket, columns =
`<STRATEGY>_median_bps` and `<STRATEGY>_n` per executed strategy.
Filtered to FILLED rows only; null cells mean the strategy never
filled (or wasn't eligible) in this slice. Bucket map comes from
`asset_class_buckets.json` so the calculator and the matrix CSV
share the same vocabulary.

## Cross-references

- [`../METHODOLOGY.md`](../METHODOLOGY.md)—cost decomposition,
  what's measured vs. looked up, caveats, reproducing the matrix.
- [`../order-execution/quality/`](../order-execution/quality/)—execution
  quality harness; produces the empirical spread / slippage / commission
  data the calculator pulls from.
