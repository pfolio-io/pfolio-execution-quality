# Order-execution quality study + trading-cost calculator

Two paired packages:

- **[`order-execution/quality/`](order-execution/quality/)** — a repeatable
  test harness that submits four order strategies across a curated set of
  Interactive Brokers instruments and records execution-quality metrics
  (slippage vs. mid, time-to-fill, commissions). Outputs parquet/CSV trial
  rows and a Markdown report.
- **[`calculator/`](calculator/)** — a UI-agnostic engine that turns
  `(asset_class, qty, price, side)` into a bps-of-notional cost breakdown.
  Pulls empirical spread + slippage from the harness's matrix CSV and falls
  back to static lookup tables for commissions, regulatory fees, taxes,
  and FX.

The harness produces the empirical evidence; the calculator consumes it.
[`METHODOLOGY.md`](METHODOLOGY.md) explains the cost model and the
caveats you should keep in mind.

## Quick start

Requires Python 3.10+ and a running TWS / IB Gateway instance for the
harness (paper account by default, port 7496).

```bash
git clone https://github.com/pfolio-io/pfolio-execution-quality.git
cd pfolio-execution-quality
pip install -r requirements.txt
```

**Run the calculator** (no IB connection required):

```bash
python -m calculator --asset-class US_STK --side BOTH --qty 100 --price 180
python -m calculator --asset-class FUT_CME --side BOTH --qty 1 --price 7250 --multiplier 50
```

**Run the harness** against an IB paper account (one tier-1 sweep,
~10 minutes):

```bash
cd order-execution
python -m quality.runner --instruments tier1 --side BUY SELL --auto-flatten
```

**Generate the report and matrix CSV** from accumulated trials:

```bash
cd order-execution
python -m quality.analyze --mode paper                        # writes REPORT.md
python -m quality.analyze --mode paper --export-matrix-csv \
    quality/results/matrix_paper.csv                          # bucket × strategy medians
```

## Repo layout

```
.
├── order-execution/
│   ├── eligibility.py         shared: per-strategy eligibility checks
│   ├── order_builders.py      shared: build the 4 IB order objects
│   ├── quote_snapshot.py      shared: snap a one-shot bid/ask + slippage math
│   ├── contract_helpers.py    shared: contract qualification + tick size
│   └── quality/               the harness package (runner, analyze, results, …)
├── calculator/                cost-model engine + CLI
├── quality/cost_tables/       static lookup tables (broker, reg fees, tax, FX, buckets)
├── METHODOLOGY.md             cost decomposition + caveats
└── requirements.txt
```

Per-package details live in
[`order-execution/quality/README.md`](order-execution/quality/README.md)
and [`calculator/README.md`](calculator/README.md).

## What the harness measures

For each `(instrument × strategy)` cell:

- Pre-trade: bid/ask/mid, `spread_t0_bps`, `spread_t0_ticks`, tick size
- Fill: avg fill price, time-to-fill, status (FILLED / TIMEOUT / CANCELLED / SKIPPED)
- Post-fill: bid/ask/mid drift
- Quality: `slip_vs_mid_t0_bps` (primary), `slip_vs_vwap_bps`, `slip_vs_mid_tfill_bps`
- Commission (raw + currency, all-in including reg fees per IBKR convention)

Sign convention: `slip = side × (avg_fill_px − mid_t0) / mid_t0 × 1e4`
with `side = +1 for BUY, −1 for SELL`. Positive = cost,
negative = price improvement. Alternating BUY/SELL across runs cancels
first-order drift bias.

## Caveats up front

Three things the data does **not** tell you, and you should know before
acting on it:

1. **Paper fills are synthetic.** IB paper fills LMT/MIDPRICE at the mid
   deterministically and MKT at the touch with no book depth or impact.
   Live fills routinely show price improvement (e.g. measured live
   MKT_RAW = +0.13 bps median vs. paper modeling +0.49 bps for the same
   universe). Use paper for ranking *strategies in aggregate*, not for
   point estimates of cost.
2. **Per-cell winners at low n are illustrative, not definitive.** The
   per-instrument "winner" tables in the report flip across sessions
   when fewer than ~10 fills are available per cell. The bucket-level
   medians (used by the calculator) are stable; the per-cell rankings
   are not.
3. **Market-data subscriptions affect eligibility.** Without a CFE
   subscription, VIX futures bid/ask is null and limit-style strategies
   skip. Without `AllLast` tick-by-tick on a venue, VWAP is null. The
   `skip_reason` column distinguishes "strategy not supported" from
   "data not available."

See [`METHODOLOGY.md`](METHODOLOGY.md) for the full caveat list and the
reasoning behind each cost component.

## License

MIT. See [`LICENSE`](LICENSE).
