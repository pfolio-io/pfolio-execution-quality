# Trading-cost methodology

This document explains the cost model the calculator implements, what the
harness measures empirically, what comes from static tables, and the
caveats every consumer of the matrix should know.

## 1. Cost decomposition

Every executed trade incurs costs from several distinct sources. The
calculator decomposes total cost into the same components for every
asset class so the breakdown is interpretable:

```
Total cost (bps) =
    slippage_bps              # realized fill vs mid_t0 — captures the
                              # half-spread MKT pays and the ≈0 cost of
                              # LMT/MIDPRICE filled at mid (capped at 0
                              # in the calculator's total)
  + commission_bps            # broker commission per fill
  + reg_fees_bps              # SEC, FINRA, exchange, clearing, PTM, etc.
  + impact_bps(size)          # size-dependent slippage beyond half-spread
  + fx_conv_bps               # if commission/notional currencies differ
  + carry_bps_per_day × days  # financing for shorts, CFDs, leverage
  + tax_bps                   # FTT (FR/IT), stamp duty (UK/CH), etc.
```

_Note: earlier versions of the engine had a separate `spread_cost_bps`
line. That double-counted with `slippage_bps`, which measures the
realized fill price vs mid_t0 and already includes the half-spread that
MKT crossed (or the ≈0 cost of LMT/MIDPRICE filled at mid). Removed
2026-05-11._

Several of these cost lines are **side-asymmetric**—they apply only to
one leg of a round-trip:

| Cost                 | When it applies                         |
|----------------------|-----------------------------------------|
| US SEC fee           | sells only                              |
| US FINRA TAF         | sells only                              |
| UK stamp duty        | buys of UK equities ≥ £1000             |
| French / Italian FTT | buys of covered names                   |
| Swiss stamp          | both legs                               |
| Carry / financing    | only on positions held > 1 calendar day |

For a round-trip (`side=BOTH`) the calculator sums entry and exit
separately; one-way (`side=BUY` or `side=SELL`) returns just one leg.

## 2. What the harness measures empirically

The harness submits real (paper or live) IB orders at tiny notional, one
strategy per cell, and records per-trial:

| Metric                   | Source                       | Reliability                     |
|--------------------------|------------------------------|---------------------------------|
| `spread_t0_bps` (median) | live bid/ask snapshot        | clean in both paper and live    |
| `slip_vs_mid_t0_bps`     | fill price vs T0 mid         | distorted in paper for limits   |
| `slip_vs_vwap_bps`       | fill price vs trades VWAP    | sparse—needs TBT subscription |
| `time_to_fill_s`         | submit → first fill          | realistic in both               |
| `commission` (raw)       | `trade.commissionReport`     | identical in paper and live     |
| `n_fills`, `exec_ids`    | per-fill audit trail         | clean                           |
| `status` distribution    | FILLED / TIMEOUT / CANCELLED | paper limit fills are 100%      |

The primary ranking metric is `slip_vs_mid_t0_bps`. Sign convention:

```
slip_bps = side × (avg_fill_px − mid_t0) / mid_t0 × 1e4
side = +1 for BUY, −1 for SELL
```

Positive = cost (you paid worse than mid). Negative = price improvement.
Alternating BUY / SELL across runs cancels first-order drift bias in
the median.

### Bucket aggregation

Per-cell winners are noisy at the sample sizes we collect (typically
n=2–10 fills per `(instrument, strategy)` cell in a single sweep). The
calculator therefore aggregates over **buckets**—coarse asset-class
groupings—instead of presenting per-instrument winners as gospel.
Buckets are defined in `quality/cost_tables/asset_class_buckets.json`
and shared between the harness's matrix export and the calculator —
literally shared as of 2026-08-04: `quality/buckets.py` is the single
reader, where each side previously kept its own copy of the matcher. The
bucket distributions converge to stable medians at the sample sizes
the harness reaches in a few sessions; per-cell winners do not.

A selector may constrain **venue** as well as symbol. `EU_STK_XETRA`,
`EU_STK_LSE` and `EU_STK_SIX` are the same `secType` on three different
exchanges, and the instrument key (`<symbol>/<secType>`) carries no venue;
they are keyed on the trial row's own `exchange` and `currency` instead.

**A priced bucket is not a measured bucket.** Every European class is
declared, priced and — as of 2026-08-04 — **unmeasured**: no trial has run
on any of the three venues. The calculator reports such a total as a
`PARTIAL TOTAL` and names the missing component rather than presenting a
zero as a measurement.

### Commission, regulatory fees, and pass-throughs

The harness records `commission` straight from
`trade.commissionReport`. For IBKR specifically, this value is **all-in**:
broker commission + SEC/FINRA + venue + clearing fees rolled up. We
verified this empirically by comparing BUY vs SELL fills on a 1-share
AAPL round-trip—the SELL is ~$0.008 higher than the BUY, consistent
with the SEC fee being baked in. Other brokers may surface separate
fee fields; the calculator's `reg_fees.json` table supports modelling
hypothetical broker switches even though IBKR's own logs don't need it.

## 3. What comes from static tables

Some cost components cannot be measured per-trade and must be looked up:

1. **Broker commission schedule**—per broker, per asset class.
   IBKR Pro tiered (e.g. US stocks $0.0035/share, $0.35 min, capped at
   1% notional), IBKR Lite ($0 commission US stocks but worse fills),
   IBKR FX (0.20 bps with $2 min), IBKR futures (per-contract by
   exchange: $0.85 ES, $1.50 VX, etc.). Stored in
   `quality/cost_tables/broker_ibkr.json`.

2. **Regulatory fees per market**—
    - US: SEC fee (~$0.00278 per $1000 sold), FINRA TAF (~$0.000166/share
      sold, capped)
    - UK: 0.5% stamp duty on buys ≥ £1000, plus PTM levy ~£1.00 per
      trade ≥ £10k
    - FR / IT: ~0.30% / 0.10% FTT on buys of covered names
    - CH: 0.075% (domestic) / 0.15% (foreign) stamp on both legs
    - CME / CBOT: NFA fee ($0.02/contract) + exchange fee
    - CFE, NYBOT: similar per-contract fee schedules

   Stored in `quality/cost_tables/reg_fees.json` (uses `_inherits` to
   deduplicate jurisdictions).

3. **Transaction taxes**—separate file
   (`quality/cost_tables/tax_rules.json`) covering UK stamp, FR/IT FTT,
   CH stamp, BE TOB.

4. **FX conversion rates**—USD-anchor static snapshot
   (`quality/cost_tables/fx_rates.json`), used to express commission
   in the user's `base_currency` when commission currency differs from
   contract currency. The calculator converts at presentation time—
   harness rows stay in their native asset currency, which keeps bps
   numbers FX-invariant within a row.

5. **Carry / financing**—not yet implemented. Will be a per-instrument
   benchmark + spread (e.g. CFDs at SARON + 2.5%) when shipped.

These tables are broker- and jurisdiction-specific. Refresh from the
authoritative source linked in each JSON's `_doc` field when rates drift
more than ~5%.

## 4. Caveats

These are the data-quality caveats every consumer of the matrix should
keep in mind. They drive how confident you can be in any given number.

### 4.1 Paper fills are synthetic

IB paper fills LMT and MIDPRICE orders **at the mid deterministically**
and MKT orders at the touch, with no book depth, queue position, or
impact modelling. We deliberately do not run a shadow fill model on top
of paper—that would test our model, not IB's execution.

Practical consequences:

- Paper `slip_vs_mid_t0_bps` for limit-style strategies is artificially
  low (often slightly negative—"price improvement"—because the sim
  fills exactly at mid).
- Paper MKT cost is artificially high. Measured live MKT_RAW for tier-1
  US instruments was **+0.13 bps median (n=34)** vs. paper modelling
  **+0.49 bps (n=218)**—paper overstates MKT cost ~4× because real
  SMART/IEX fills get price improvement that the sim doesn't model.
- Paper `status` distribution is unreliable for limit strategies—they
  always FILL. Real fill probability is the differentiator that paper
  hides.

What paper *is* reliable for: spread, commissions, latency, eligibility
behavior, schema correctness, and aggregate distribution shape across
strategies. Use it for ranking strategies in aggregate, not for point
estimates of cost.

### 4.2 Per-cell winners are noisy at low n

Across multiple paper sweeps, per-`(instrument × strategy)` winners flip
session-to-session (e.g. LMT_MID vs MIDPRICE on the same instrument)
even though aggregate distributions stay stable. With ~2–10 fills per
cell per sweep, the per-cell ranking is dominated by sample noise.

The calculator only consumes **bucket-level medians**, not per-cell
winners. Per-cell winner tables in the report are illustrative—useful
for spotting paper-vs-live disagreements that warrant a follow-up
sweep, not as direct policy input.

### 4.3 Market-data subscriptions affect eligibility

The eligibility module distinguishes "strategy not supported by this
contract" from "data not available right now":

- Without a CFE market-data subscription, VIX futures bid/ask is null
  and LMT_MID skips with `skip_reason=no_live_quote_at_t0`.
- Without an `AllLast` tick-by-tick subscription on a venue, VWAP stays
  null even during RTH; `slip_vs_vwap_bps` is null for those rows.
- CFD instruments require a CFD trading permission on the IB account;
  without it, IB returns Error 201 (captured in `notes`).

If a strategy is showing low fill rates in your local data, check
`skip_reason` and `notes` before concluding the strategy doesn't work.

### 4.4 Tiny-notional bias

The harness submits the smallest tradable unit (1 share, 1 lot, 1
contract) per trial. Two consequences:

- **Commission dwarfs spread.** AAPL/EFA/LQD/SPY commissions are
  14–99 bps of notional at 1-share size, vs < 1 bps spread. The
  calculator handles this by bucketing commissions by *notional band*
  rather than per-share—but anyone reading raw harness rows should
  remember that commission_bps is wildly inflated at this size.
- **No size impact captured.** Slippage-vs-notional (impact model) is
  not in the harness output. For real-world trading at meaningful size,
  add an impact estimate on top. Production execution logging at real
  notional is the intended source for this calibration.

### 4.5 Bimodal spread distribution

Across our universe, instruments split cleanly into TIGHT (spread < 5
bps: AAPL, SPY, ES, EURUSD, etc.) and WIDE (spread > 25 bps: VIX_FAR,
PRIM, etc.)—almost nothing lives in the 5–25 bps band. This shapes
how the harness's per-strategy rankings should be read: a strategy
that wins on TIGHT instruments may lose decisively on WIDE ones, and
the bucket map reflects this split.

## 5. Reproducing the matrix

To regenerate the bucket × strategy matrix CSV from scratch:

```bash
# 1. Run the harness against your IB paper account.
#    Repeat across multiple sessions to firm up sample sizes.
cd order-execution
python -m quality.runner --instruments all --side BUY SELL --auto-flatten

# 2. (Optional) Repeat against a live account for honest slippage data.
#    Tier-1 only; ~$30-50 in real commissions per sweep.
python -m quality.runner --mode live --yes-live \
    --instruments tier1 --side BUY SELL --auto-flatten

# 3. Generate the report and bucket × strategy matrix.
python -m quality.analyze --mode paper                                    # writes REPORT.md
python -m quality.analyze --mode paper --export-matrix-csv \
    quality/results/matrix_paper.csv                                      # bucket × strategy medians
python -m quality.analyze --mode live  --export-matrix-csv \
    quality/results/matrix_live.csv

# 4. Calculator picks up the matrix automatically.
python -m calculator --asset-class US_STK --side BOTH --qty 100 --price 180
```

`--mode` controls which result store the analysis reads (and where the
matrix is sourced from). The calculator prefers live data over paper for
slippage when both are present.

For interpreting individual rows, see the per-package READMEs:

- [`order-execution/quality/README.md`](order-execution/quality/README.md)
  for the harness's CLI, schema, and per-strategy timeouts.
- [`calculator/README.md`](calculator/README.md) for the cost engine's
  inputs, lookup-table format, and CLI flags.
