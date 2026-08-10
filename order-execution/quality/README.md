# Order-execution quality harness

A repeatable test harness that measures execution quality of four distinct
order strategies across a curated set of IB instruments. Output: an empirical
"optimal policy per instrument class" recommendation, backed by multi-run
statistics and a Markdown report.

## What it measures

Each trial submits one strategy on one contract at tiny notional and records:

- **Pre-trade**: `bid`, `ask`, `mid`, `spread_t0_bps`, `spread_t0_ticks`
- **Fill**: `avg_fill_px`, `time_to_fill_s`, `status`, `n_fills`
- **Post-fill**: `bid_tfill`, `ask_tfill`, `mid_tfill`
- **Quality**: `slip_vs_mid_t0_bps` (primary), `slip_vs_vwap_bps`,
  `slip_vs_mid_tfill_bps`, `vwap_window`

Sign convention for slippage: `slip = side × (avg_fill_px − mid_t0) / mid_t0
× 1e4` with `side = +1 for BUY, −1 for SELL`. **Positive = cost. Negative =
price improvement.** Alternating BUY/SELL across runs cancels first-order
drift bias.

## The four strategies

Each is built and submitted **directly** by the harness—bypassing
production's spread-driven chain (TIGHT/WIDE/NO_QUOTE in
`ib_order_executor.submit_order`) so no fallback contaminates the
per-strategy measurement.

| Label             | Construction                                | Eligibility                               |
|-------------------|---------------------------------------------|-------------------------------------------|
| `MIDPRICE_NATIVE` | IB native MIDPRICE algo                     | `MIDPRICE` in contract `orderTypes`       |
| `LMT_MID`         | LimitOrder at live mid; up to 3×10s retries | live bid/ask available at T0              |
| `MKT_ADAPTIVE`    | Adaptive algo, `adaptivePriority=Normal`    | `secType ∉ {CASH, CFD}` and MKT supported |
| `MKT_RAW`         | Plain MarketOrder                           | universal                                 |

Eligibility is checked per `(instrument × strategy)` at runtime; ineligible
cells are recorded as `SKIPPED` (with `skip_reason`) rather than failed.

## Instrument matrix

| Tier | Symbols                                                    |
|------|------------------------------------------------------------|
| 1    | `AAPL`, `SPY`, `ES` (CME front), `EURUSD`                  |
| 2    | `LQD`, `EFA`, `VIX` (CFE front), `CFD_USD_CHF`             |
| 3    | `DX` (NYBOT front), `VIX_FAR` (CFE 2nd-month), `SMALL_CAP` |
| eu   | `EU_XETRA`, `EU_LSE`, `EU_SIX`                             |

### The European tier (added 2026-08-04)

`broker_ibkr.json` has carried `EU_STK_XETRA`, `EU_STK_LSE` and `EU_STK_SIX`
commission rules since 2026-05-04, and `reg_fees.json` and `tax_rules.json`
carry the PTM levy and the two stamp duties. **Nothing had ever traded on any of
the three**, so the execution term had no measurement — and the calculator
returned it as `0.00 bps` inside a total that read as complete. These three cells
are what makes it measurable. They are a separate tier because they are the only
ones needing European market data and a European trading permission; a run
without either should be able to skip them by name.

**Resolution is by ISIN, not by ticker.** A UCITS ETF trades under a different
local ticker on every venue, and nothing here — or in the pfolio universe screen
these funds came from — records which ticker belongs to which venue. The
candidates in `EU_ISIN_CANDIDATES` are the largest broad-market UCITS equity ETFs
in that screen, tried in order; the first IBKR can qualify on the venue wins, and
which one it was is recorded per trial row.

**The exchange is explicit (`IBIS` / `LSE` / `EBS`), not `SMART`.** A
SMART-routed order records `exchange = SMART`, and the bucket for these three is
*defined* by venue — measuring the router is not measuring the venue.

⚑ **Live European runs can attract a transaction tax** (SIX and the UK both
levy; the exemptions depend on the specific line that resolves). The live
pre-flight banner says so; the commission estimate does not model it.

**The candidate order is load-bearing** *(2026-08-10)*. `EU_ISIN_CANDIDATES` is
tried in order on **every** venue, so the primary fund is asked for on all three
before any of them falls through to a fallback. A chain that resolves a different
fund per venue measures three funds on three venues, and the cross-venue
difference is then confounded by the instrument with nothing saying so. Which
ISIN resolved is recorded per trial in the **`sec_id`** column and printed at
resolution time, so cross-venue comparability is a fact in the data rather than
an assumption. Within a venue, the venue's expected currency is tried first and
then dropped, so repeated sessions land on the same line.

#### Running the European tier — read this before the first order

**1. `python -m quality.preflight` first. It places no orders and answers the
question that costs money.** `snapshot_quote` asks for live data only. Without
the venue's market-data subscription, `LMT_MID` is SKIPPED for free — but
`MIDPRICE_NATIVE`, `MKT_ADAPTIVE` and `MKT_RAW` **fill and record a null
`mid_t0`**, so `slip_vs_mid_t0_bps` is null, `bucket_strategy_matrix` drops the
row, and the bucket stays UNMEASURED. Full commission, both legs, no
measurement. The preflight reports, per venue: the account TWS is pointed at,
which ISIN resolved, the bucket the trials would land in, whether a two-sided
quote arrives, the touch **and its sizes**, the eligible strategies, and what one
full pass would cost.

**2. Three guards run before any European order**, and all three decline to
trade rather than route around the problem:

| Guard | What it stops |
|---|---|
| no all-venues retry (`allow_exchange_fallback=False`) | a failed venue qualification silently returning a **SMART** or other-venue contract |
| bucket asserted pre-trade (`venue_guard`, reading `asset_class_buckets.json`) | a cell trading on the wrong venue and landing in the wrong bucket — or none |
| `--outside-rth` refused for `EU_*` | a limit resting unfilled for its whole retry budget on a venue with no extended session |

**3. Session window.** XETRA 09:00–17:30 · SIX 09:00–17:20 · LSE 08:00–16:30
London. **All three trade only between 09:00 and 17:20 CEST**, which overlaps the
US session by less than two hours. Holiday calendars are per venue.

**4. Commission is shaped the other way round from US.** Per-order minimums of
€1.25 / £1.00 / CHF 1.50 bind at one share, so a batch costs *orders × minimum*
and raising the notional does not reduce it. Sizing a European batch is counting
orders. The live banner prints the per-venue figure from `broker_ibkr.json`.

**5. Convergence targets for these cells** (all three numbers already exist in
this repo — see the Convergence target section): paper **n ≥ 10** per
(venue × strategy) before the paper figure is usable at all; live **n ≥ 5** as
the publication gate; live **n ≥ 20** across ≥ 5 sessions as the target. Sides
balanced, always `--side BUY SELL`.

⚑ **Live European runs are approved per batch by Marcel, in writing, before they
fire** (hq convention 0b(vii)). After every live run the harness reads back open
positions and names anything the batch left open; it does not auto-correct,
because an unattended corrective order is a second uncontrolled order.

`SMALL_CAP` defaults to `PRIM` (Primoris Services). Swap
`SMALL_CAP_SYMBOL` in `runner.py` if it delists or you want a different
small-cap.

Front/far-month resolution (`ES`, `VIX`, `VIX_FAR`, `DX`) goes via IB
`reqContractDetailsAsync`, picking the n-th-earliest expiry ≥ today + 5
calendar days. `VIX_FAR` uses `skip=1` to land on the second-front
contract. No `investing_tools` dependency, so the package runs standalone.

**Caveats**:

- `VIX_FAR` may resolve very close to `VIX` front because CFE has weekly
  VIX expiries. The skip is by sort order, not by-month, so the spacing
  between front and far depends on which weeklies are listed.
- `CFD_USD_CHF` requires CFD trading permissions on the IBKR account. If
  not enabled, orders are rejected with Error 201 ("No Trading
  Permission, Regulatory Restriction") and recorded in the `notes`
  column with `status=TIMEOUT` (post-rejection cancel).
- `DX` (NYBOT) tick-by-tick subscription is typically not granted with a
  basic data package; the harness will fail-soft on VWAP and continue.

## Layout

```
order-execution/
  eligibility.py       # per-strategy eligibility checks (shared with production)
  order_builders.py    # 4 isolated builders, each returns an Order (shared)
  quote_snapshot.py    # Quote, snapshot_quote, slip_vs_mid_bps (shared)
  quality/
    __init__.py
    runner.py          # entry point—sweeps (side × instrument × strategy)
    preflight.py       # READ-ONLY readiness check—places no orders. Resolution,
                       # bucket, market data, touch + sizes, eligible strategies
                       # and the cost of one pass, before anything is submitted
    instruments.py     # front-month and by-ISIN resolution helpers
    metrics.py         # TickRecorder/VWAP (harness-only) + re-exports of the
                       # shared shapes for backward-compatible imports
    results.py         # parquet/csv append + canonical schema
    buckets.py         # asset-class selectors — the ONE reader of
                       # cost_tables/asset_class_buckets.json, shared with
                       # calculator/harness_data.py
    analyze.py         # post-hoc aggregation → REPORT.md, --export-matrix-csv
    results/           # gitignored except .gitkeep
      trials_paper.parquet
      trials_live.parquet
      REPORT.md
      matrix_{paper,live}.csv  # --export-matrix-csv output, calculator V1 input
```

## Running

All commands assume CWD = `order-execution/`.

```bash
# Read-only readiness check—no orders. Run this before any European batch.
python -m quality.preflight                    # the three European venues
python -m quality.preflight --instruments all

# The European tier, both legs. Inside 09:00–17:20 CEST; never --outside-rth.
python -m quality.runner --instruments eu --side BUY SELL

# Full Tier-1 sweep, BUY leg
python -m quality.runner

# Full Tier-1 + Tier-2, both BUY and SELL legs in one invocation
python -m quality.runner --instruments all --side BUY SELL --outside-rth

# Single instrument
python -m quality.runner --instruments ES
python -m quality.runner --instruments EURUSD --qty 20000

# Subset of strategies
python -m quality.runner --strategies LMT_MID MKT_RAW

# Live mode (writes to trials_live.parquet; same TWS port)
python -m quality.runner --mode live --instruments ES
```

After running, regenerate the report:

```bash
python -m quality.analyze              # paper store → REPORT.md
python -m quality.analyze --mode live  # live store

# Slice the report:
python -m quality.analyze --last-run                       # only the most recent run
python -m quality.analyze --run-id 20260504T133000          # one run (prefix match)
python -m quality.analyze --since 2026-05-04T13:30          # ISO UTC cutoff
python -m quality.analyze --last-run --report-path RTH.md   # write to a separate file
```

### CLI flags

| Flag                | Default                    | Notes                                                                                                                |
|---------------------|----------------------------|----------------------------------------------------------------------------------------------------------------------|
| `--mode`            | `paper`                    | `paper` → `trials_paper.parquet`; `live` → `trials_live.parquet`                                                     |
| `--side`            | `BUY`                      | One or more of `BUY` `SELL`. `BUY SELL` runs both legs                                                               |
| `--instruments`     | `tier1`                    | Tier name (`tier1`, `tier2`, `tier3`, `all`) or comma list of symbols                                                |
| `--strategies`      | all four                   | Subset of `MIDPRICE_NATIVE LMT_MID MKT_ADAPTIVE MKT_RAW`                                                             |
| `--qty`             | per-symbol                 | Override; default uses `DEFAULT_QTY` (FX = 20000, others = 1)                                                        |
| `--outside-rth`     | off                        | Allow pre/post-market fills for US equities                                                                          |
| `--auto-flatten`    | live=on, paper=off         | After each FILLED entry leg, fire MKT_RAW exit at same qty so net exposure stays ~0. Both legs share `round_trip_id` |
| `--no-auto-flatten` |—                        | Force-disable auto-flatten (positions accumulate)                                                                    |
| `--yes-live`        | required for `--mode=live` | Confirms real orders will be placed against a live account                                                           |
| `--allow-live-account` | off                     | Permits `--mode=paper` against a LIVE (non-DU) account. Without it the runner **refuses**—see below                  |

## Live mode

`--mode live` writes to `trials_live.parquet` and is gated by three
safeguards:

1. **Pre-flight banner** prints the cell count and a rough max-commission
   estimate before any orders fire.
2. **`--yes-live` is required**—without it, the runner exits before
   connecting to TWS.
3. **Account-prefix gate**—refuses to start if the connected account
   starts with `DU` (paper). Prevents paper fills from polluting the
   live calibration dataset.

**The gate now runs in both directions** *(2026-08-10)*. `--mode paper` against a
**live** (non-`DU`) account is also a refusal, with `--allow-live-account` as the
escape hatch. It used to print *"Orders WILL fire on a real account"* and then
fire them, writing the fills to `trials_paper.parquet` — where this README's own
caveat says paper fills are synthetic and unquotable. Real money spent, and
filed in the one place that would not show it. **A paper run needs TWS pointed at
the `DU` account, not just `--mode paper` on the command line.**

Auto-flatten is **on by default** in live mode: after each FILLED entry
leg, a `MKT_RAW` exit is fired at the same qty in the opposite direction
so net exposure stays ~zero. Both legs share a `round_trip_id` for later
round-trip cost computation.

Live qty defaults differ from paper where the broker minimum is higher:

| Symbol            | Paper qty | Live qty |
|-------------------|-----------|----------|
| EURUSD            | 20000     | 25000    |
| CFD_USD_CHF       | 1000      | 25000    |
| (everything else) | same      | same     |

Override per-call with `--qty N`. Tier-1-only is the recommended live
scope (per the original spec, Phase 6.5).

## Per-strategy timeouts

Defined in `runner.py`:

| Strategy          | Timeout | Notes                                          |
|-------------------|---------|------------------------------------------------|
| `MIDPRICE_NATIVE` | 35s     | IB algo waits ~30s internally before giving up |
| `LMT_MID`         | 3 × 10s | Re-snapshots mid each retry; ~30s budget       |
| `MKT_ADAPTIVE`    | 30s     | Adaptive's price-improvement loop              |
| `MKT_RAW`         | 10s     | Should fill instantly on liquid contracts      |

## Result schema

41 columns. Key groups:

- **Identification**: `schema_version`, `run_id`, `trial_idx`, `timestamp_utc`
- **Contract**: `symbol`, `secType`, `exchange`, `currency`, `conId`,
  `sec_id`, `expiry`, `multiplier`. `sec_id` is the **ISIN** when the contract
  was resolved by ISIN (the European cells) and null otherwise — a UCITS ETF's
  local ticker differs per venue, so `symbol` alone cannot say which fund a
  published European figure measured
- **Strategy**: `strategy_label`, `eligible`, `skip_reason`
- **Request**: `side` (±1), `requested_qty`, `tick_size`
- **T0 snapshot**: `t0`, `bid_t0`, `ask_t0`, `mid_t0`, `spread_t0_bps`,
  `spread_t0_ticks`
- **Fill**: `t_fill`, `filled_qty`, `avg_fill_px`, `n_fills`,
  `time_to_fill_s`, `status` (FILLED / PARTIAL / TIMEOUT / CANCELLED /
  FAILED / SKIPPED)
- **T_fill snapshot**: `bid_tfill`, `ask_tfill`, `mid_tfill`
- **Quality**: `slip_vs_mid_t0_bps`, `slip_vs_vwap_bps`,
  `slip_vs_mid_tfill_bps`, `vwap_window`
- **Commission**: `commission` (raw, in `commission_currency`),
  `commission_currency`, `exec_ids` (comma-joined IB execIds for audit).
  `analyze.py` computes `commission_bps` only when `commission_currency`
  matches `currency`—cross-currency commission/notional cases require
  an FX rate model that isn't built yet.
- **Round-trip pairing**: `round_trip_id`, `leg` (`entry`|`exit`).
  Populated when `--auto-flatten` is on. Both legs of one cell share the
  same `round_trip_id`; the entry row carries the test strategy, the
  exit row is always `MKT_RAW` in the opposite direction. Filter
  `leg='entry'` to compute strategy slippage; pair on `round_trip_id` to
  compute realized round-trip P&L.
- **Environment**: `paper_account`, `session`, `ib_server_version`, `notes`

Schema is forward-compatible: parquet append uses `promote=True`, so missing
columns become null on read. Bump `SCHEMA_VERSION` in `results.py` only on
breaking changes (renames, type changes).

## Caveats

- **Paper fills are synthetic.** IB paper sim fills LMT-at-mid at the mid
  and MKT at the touch with no book depth, queue, or impact modelling. Use
  `spread_t0_bps` as the realistic-cost lower bound for MKT-style strategies
  under live conditions; ship `--mode live` micro-notional runs as the
  honest dataset.
- **Adaptive algo doesn't fill on FUT in paper.** Orders queue in
  `PreSubmitted` indefinitely. Expect TIMEOUTs for `MKT_ADAPTIVE` × futures
  rows in `trials_paper.parquet`. Real signal arrives only in live mode.
- **Fill probability is the live-only differentiator.** In paper, all
  limit-style strategies "fill" at the requested price 100% of the time.
  The `status` column is meaningful only in `--mode live`.
- **Market-data subscriptions matter.** Without a CFE subscription, VIX
  futures bid/ask is null and `LMT_MID` will be `SKIPPED` with
  `skip_reason=no_live_quote_at_t0`. The eligibility module distinguishes
  "strategy not supported" from "data not available."
- **Tiny notional only.** 1 share / 1 lot / 20000 EUR. Size effects are out
  of scope.
- **VWAP is opportunistic.** During pre-market or thin sessions, the
  `[t0, t_fill]` window may catch zero `AllLast` ticks; `vwap_window` will
  be null. Spec calls this out: "Null if subscription refused (level-1-only
  data permission) or window <1s."

## Convergence target

Per spec: ≥20 trials per `(instrument × strategy)` cell across ≥5
sessions. Manually triggered (no cron). Re-run `analyze.py` after each
session to track convergence.

Two lower thresholds are operative before that one, and both come from
`tool/src/02-data.js::bestGuess`, which decides what the public matrix shows:
**live n ≥ 5** per (bucket × strategy) is trusted standalone (between 2 and 4 it
needs a paper cross-check within `OUTLIER_BPS`), and **paper n ≥ 10** is the
floor below which paper is not used at all. So: paper to 10, live to 5 as the
publication gate, live to 20 as the target — sides balanced, since the sign
convention only cancels drift when the BUY and SELL legs are equal in number.

## Unmeasured is a row, not an omission

`--export-matrix-csv` emits **one explicit all-zero-`n` row per declared but
unmeasured bucket**, medians blank:

```
EU_STK_LSE,,0,,0,,0,,0
```

`cost_model.py` floors slippage at zero, so a measured bucket can publish
`0.00 bps` from a **measured negative** — fills at or inside the mid, capped so
the total is not a promise of price improvement — while an unmeasured bucket
would publish the same `0.00` from nothing at all. The two look identical and
mean opposite things, and only one of them can move: **an unmeasured figure can
only go up.** The Python calculator already distinguishes them
(`measurement_state`, `unmeasured=True`, `PARTIAL TOTAL`); the matrix CSV did
not, because an unmeasured bucket was simply an absent row and absence reads as
*not applicable*. A blank median beside `n = 0` cannot be read as zero cost.

Downstream-neutral: every consumer already treats `n = 0` as no data.

⚑ **`tool/src/02-data.js::cellState` maps "no trials either side" to
`ineligible`, which the public matrix renders as *not supported*.** For the EU
buckets that label would be false — they are supported and unmeasured. It is
harmless only because `BUCKET_ORDER` lists six US buckets. **Adding an EU row to
the public tool requires a fourth cell state first** (measured · thin ·
unmeasured · not supported).

## Related files

- [`../../METHODOLOGY.md`](../../METHODOLOGY.md)—cost decomposition
  and caveats; the harness's empirical outputs feed sections 2 and 4.
- [`../../calculator/`](../../calculator/)—UI-agnostic cost engine
  that consumes the matrix CSVs this harness exports.
- `eligibility.py`, `order_builders.py`, `quote_snapshot.py`,
  `contract_helpers.py` (one level up in `order-execution/`)—the
  shared primitives the harness builds on top of.
