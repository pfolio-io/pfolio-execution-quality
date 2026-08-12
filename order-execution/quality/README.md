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
| eu   | `EU_ETF_EUR`, `EU_ETF_GBP`, `EU_ETF_CHF` — listing lines, SMART-routed |

### The European tier

*Added 2026-08-04 as three **venues**. Rebuilt 2026-08-11 as three **listing
lines** — see "Why it stopped being about venues" below.*

`broker_ibkr.json` has carried `EU_STK_XETRA`, `EU_STK_LSE` and `EU_STK_SIX`
commission rules since 2026-05-04, and `reg_fees.json` and `tax_rules.json` carry
the PTM levy and the two stamp duties. **Nothing had ever traded on any of the
three**, so the execution term had no measurement — and the calculator returned it
as `0.00 bps` inside a total that read as complete. These cells are what makes it
measurable. They are a separate tier because they are the only ones needing
European market data and a European trading permission; a run without either
should be able to skip them by name.

#### The three cells

| Cell | Line | Resolves to | Expected bucket |
|------|------|-------------|-----------------|
| `EU_ETF_EUR` | EUR, Xetra-primary | `SXR8` | `EU_STK_XETRA` |
| `EU_ETF_GBP` | GBP, LSE-primary | `CSP1` | `EU_STK_LSE` |
| `EU_ETF_CHF` | CHF, SIX-primary | `CHSPI` | `EU_STK_SIX` |

`EU_ETF_USD` exists and is **not in the tier**: IBKR publishes no SMART listing
for the USD line, so a user routing SMART cannot buy it.

**Resolution is by ISIN, not by ticker.** A UCITS ETF trades under a different
local ticker on every venue, and nothing here — or in the pfolio universe screen
these funds came from — records which ticker belongs to which venue. Candidates
are tried in order, the listing currency is a hard filter (it is the only thing
separating the cells), and the ISIN that resolved is recorded per row in
`sec_id`.

⚑ **The EUR and GBP cells are the same fund; the CHF cell is not, and cannot be.**
The global candidates have **zero** CHF listings between them, so the only
natively SIX-listed CHF ETFs are Swiss-domiciled trackers. The CHF cell therefore
measures **Swiss equity** and its spread is not comparable with the other two.

#### Study status — CLOSED 2026-08-12, and what "closed" does and does not mean

Five batches, **29 runs · 195 trials · 137 fills · 11 venues · zero unbucketed**,
over **two sessions** (2026-08-11 and 2026-08-12).

| Bucket | `MKT_RAW` | `MKT_ADAPTIVE` | `LMT_MID` | fill rate (RAW / ADPT / LMT) |
|---|---|---|---|---|
| `EU_STK_XETRA` | **0.138** | −0.000 | −0.138 | 10/10 · 11/16 · 10/10 |
| `EU_STK_LSE` | **1.053** | 0.444 | 0.000 | 10/10 · 6/16 · 5/10 |
| `EU_STK_SIX` | **1.732** | 0.573 | −0.000 | 10/10 · 4/16 · **3/16** |

Medians are bps per leg, entry legs only. **`MKT_RAW` is the number to quote** —
it is the line the shipped executor reaches on these instruments and the only one
that fills every time. See the `strategy` note in `../../calculator/README.md`.

⚑ **CLOSED IS NOT CONVERGED.** The target is n ≥ 20 per cell across **≥ 5
sessions**; this closes at **2 sessions**, on Marcel's call (2026-08-12), with n
waived explicitly. Two sessions cannot distinguish a per-line property from a
two-day condition — and batches 3 and 4 showed that distinction is real: SIX moved
1.146 → 2.234 while XETRA sat at 0.138 throughout. **Anything published from this
must carry the session count, not just the n.**

What the study *did* establish, and would not have without trading: the venue map
(11 venues, `EUDARK` and `TRWBEN` reachable only by trading), the per-line fill
rates, the pence bug, the crossed-book behaviour, and `EBS` at 2.39× the modelled
commission.

⚑ **A batch left a position open, and the guard that exists did not stop it.**
2026-08-12: `LMT_MID` BUY on `SXR8` filled at €723.30 and its auto-flatten
`MKT_RAW` exit **timed out**, leaving 1 share long for ~18 minutes until it was
found by auditing the store. `_report_open_positions` worked exactly as written
and printed its warning — but it prints **per run**, and that batch was **20 runs
fired in an unattended loop**. A correct warning on run 14 of 20, in a console
nobody is watching, is not a control. The residue was found by pairing
`round_trip_id` in the parquet, which is what actually caught it.
**If you fire more than one run unattended, audit the store afterwards** — pair
entries against exits and check net exposure per line — rather than trusting that
someone read the output. Sizing a batch is not only counting orders; it is also
asking who is watching when it ends.

#### Why it stopped being about venues

The original design routed to an explicit venue — `IBIS2` / `LSEETF` / `EBS`,
never `SMART` — because the bucket is defined by venue and a SMART fill records
`exchange = SMART`. Two things killed that on 2026-08-11:

1. **The account cannot place directed orders at all.** Every directed order comes
   back `Cancelled` with error **10311**, including to the very venue SMART itself
   had just filled on. It is not a venue permission and not European.
2. **Cost per user beat cost per venue.** No retail client directs orders, so
   directing would price a counterfactual.

⇒ **The European cells route `SMART`, and the bucket is keyed on where the fill
actually executed** (`exec_exchange`, from `execution.exchange`), which
`buckets.venue_series` prefers over `exchange`. Each bucket therefore covers the
venues SMART can reach for that line, not a single venue. Record:
`pfolio-hq/docs/2026-08-10-decision-eu-execution-measurement.md` §7e.

⚑ **"Measuring the router is not measuring the venue" is now measured, not
argued**: a SMART order in `SXR8`, a Xetra-*primary* ETF, executed on `GETTEX2` —
a different German venue on a different fee schedule. That is why `exec_exchange`
exists.

#### Venue discovery is part of the job

SMART chooses among everything in a contract's `validExchanges` — **and beyond
it**: `EUDARK`, IB's European dark pool, took 9 of 12 live fills and appears in no
`validExchanges` list at all. It was reachable only by trading.

So after every run the harness prints **`venue_coverage`**: which venues took
fills, which bucket each maps to, and **which have no commission rule**. A fill on
an unpriced venue is reported, not absorbed. Growing `cost_tables/` to match is
how the map catches up — and `cost_tables/` is canon and read-only to agents.

#### Running it

**1. `python -m quality.preflight` first. It places no orders and answers the
question that costs money.** `snapshot_quote` asks for live data only. Without the
venue's market-data subscription, `LMT_MID` is SKIPPED for free — but the others
**fill and record a null `mid_t0`**, so `slip_vs_mid_t0_bps` is null, the row is
dropped from the matrix, and the bucket stays UNMEASURED. Full commission, both
legs, no measurement.

**2. Guards that still apply.** The pre-trade `venue_guard` is retired — under
SMART there is no intended venue to check against. What remains:

| Guard | What it stops |
|---|---|
| `--outside-rth` refused for `EU_*` | a limit resting unfilled for its whole retry budget on a venue with no extended session |
| account/mode gate, both directions | `--mode live` on a `DU` account, and `--mode paper` on a live one |
| post-run position readback (live) | a batch quietly leaving a position open |
| `venue_coverage` | a fill on a venue nothing can price |

**3. Session window.** XETRA 09:00–17:30 · SIX 09:00–17:20 · LSE 08:00–16:30
London. **All three trade only between 09:00 and 17:20 CEST.** Holiday calendars
are per venue.

**4. Which strategies actually work** *(entry legs, live, cumulative over
2026-08-11 and 2026-08-12 — 24 entries per eligible strategy)*:

| Strategy | European verdict |
|---|---|
| `MIDPRICE_NATIVE` | **unsupported** — absent from `orderTypes`. 6 of 6 SKIPPED, both sessions |
| `LMT_MID` | eligible, **14 of 24 (58%)**. In paper it "fills" 100% of the time at the mid, which is a property of the simulator |
| `MKT_ADAPTIVE` | **12 of 24 (50%)**. Rejected outright (error 442) on *directed* orders only |
| `MKT_RAW` | **24 of 24 (100%)** |

⚑ **`LMT_MID` "filled 0 of 4 live" was this table's figure until 2026-08-12, and
it was wrong as a verdict rather than as arithmetic** — it was true of the four
entries then in the store, and generalised from them. At 24 entries the two
limit-style strategies fill roughly half the time, which is a different
operational conclusion: a European batch should expect to buy ~50% of its
`LMT_MID` and `MKT_ADAPTIVE` cells per pass, not zero. The unfilled ones cost
nothing but the retry budget. Non-fills are `TIMEOUT`, never partial.

**5. Commission is shaped the other way round from US.** Per-order minimums of
€1.25 / £1.00 / CHF 1.50 bind at one share, so a batch costs *orders × minimum*
and raising the notional does not reduce it. Sizing a European batch is counting
orders. ⚑ It also means `commission_bps` from this harness does not generalise —
see the caveat under **Caveats**.

⚑ **Live European runs can attract a transaction tax** (SIX and the UK both levy;
exemptions depend on the line that resolves). The live banner says so; the
estimate does not model it.

⚑ **Live European runs are approved per batch by Marcel, in writing, before they
fire** (hq convention 0b(vii)). After every live run the harness reads back open
positions and names anything left open; it does not auto-correct, because an
unattended corrective order is a second uncontrolled order.

⚑ **AND MARCEL TYPES THE COMMAND. An agent specifies the batch and prices it; the
operator fires it; the agent takes over again at the store.** *(Recorded
2026-08-12. Batches 1–3 were fired by the agent on Marcel's written yes, and the
wording above — "approved … before they fire" — described exactly that split.
Batch 4 hit an agent that would not place live orders at all, which stalled a
batch mid-window on a trading day while the protocol was re-derived from first
principles. Whether any given agent will is not a thing this repo controls, so the
division of labour is written down at the level that always holds.)*

The practical shape, and what a session should expect to do:

| Step | Who | Notes |
|---|---|---|
| Batch spec — lines, sides, strategies, cost | agent | Options + trade-off + a recommendation, per hq convention 0 |
| `python -m quality.preflight` | agent | READ-ONLY, places no orders |
| Open-position readback before firing | agent | Confirms the previous batch left the lines flat |
| Written approval of the batch | Marcel | 0b(vii) |
| **`python -m quality.runner --mode live --yes-live …`** | **Marcel** | The only step that places orders |
| Analysis, bucketing, `venue_coverage`, report, commit | agent | Reads the store directly; nothing needs pasting |

**Everything either side of the firing is the agent's**, so the handover costs one
command and nothing else waits on it.

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

43 columns. Key groups:

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
- **⚑ `commission_bps` FROM THIS HARNESS DOES NOT GENERALISE, AND
  `slip_vs_mid_t0_bps` DOES.** At one share the per-order **minimum** dominates
  every European schedule: `CHSPI` at CHF 174 with a CHF 1.50 minimum measures
  **86 bps** of commission, where a user trading CHF 5,000 of it pays the 6 bps
  rate. Slippage is a ratio against the mid and is size-independent while the
  order sits inside the displayed quote, which at these sizes it does. So the
  slippage columns are the harness's product; the commission columns are a
  *record of what this account was charged on these fills*, useful for correcting
  `broker_ibkr.json` and misleading if read as a cost per user.
- **⚑ THE PRIMARY EXCHANGES CHARGE ABOVE THE MODELLED MINIMUM; THE MTFs AND THE
  DARK POOL CHARGE IT EXACTLY.** Measured over 100 live European fills across both
  sessions, and the split is clean:

  | Venue | Charged | `broker_ibkr.json` min | n |
  |---|---|---|---|
  | `EUDARK` · `GETTEX2` · `TRWBCH` · `TRWBUKETF` · `TRWBEN` | exactly 1.25 / 1.00 / 1.50 | same | 77 |
  | `IBIS2` (Xetra) | EUR 1.276–1.302 | 1.25 | 10 |
  | `LSEETF` (LSE) | GBP 1.16 | 1.00 | 8 |
  | `BATECH` | CHF 1.531 | 1.50 | 1 |
  | **`EBS` (SIX)** | **CHF 3.58** | **1.50** | 4 |

  `EBS` is **2.39× the modelled minimum** and is the one that matters: at one share
  the minimum binds, so a SIX fill that lands on the primary costs CHF 3.58 where
  `broker_ibkr.json` predicts CHF 1.50. **Not fixed here — `cost_tables/` is canon
  and read-only to agents** (S1-33), and this is reported for the same reason
  `EUDARK` and `TRWBEN` were reported rather than added.

  ⚑ **What is NOT known is whether the excess is a flat add-on or a rate**, because
  every fill in the store is at one share. A flat CHF 2.08 exchange fee and a
  percentage component are indistinguishable at a single notional, and they imply
  opposite corrections at a user's size. Settling it needs fills at a second
  notional — a different experiment, with its own cost, and Marcel's call.
- **⚑ Pence-quoted lines.** IBKR reports `currency = GBP` for London lines that
  quote in **GBX** — `CSP1` arrives as 61917 meaning GBP 619.17. `price_magnifier`
  (from `ContractDetails.priceMagnifier`, 100 there and 1 almost everywhere)
  is recorded per row and divided out of every notional. Before 2026-08-11 it was
  not, and `commission_bps` for those lines was 100× too small. Slippage was
  never affected: it is a ratio of two prices in the same units.
- **⚑ The SMART book crosses, and `spread_t0_bps` goes negative when it does.**
  4 of 100 quoted European rows carry a **negative** spread — bid above ask — and
  8 more carry exactly zero. Every crossed one is `SXR8`, on both sessions
  (−0.276 bps on 08-11, −0.553 bps on 08-12), so it is a property of that line and
  not a one-off. The cause is structural: SMART aggregates across venues with no
  consolidated tape and no locked/crossed protection, so one venue's bid can sit
  above another's ask for the moment the snapshot is taken. `mid_t0` is still the
  midpoint of those two prices and is not obviously wrong, but two things break:
  the **"MKT_RAW slippage ≈ half the spread" sanity check is meaningless** on those
  rows, and a crossed mid can show price improvement on *both* legs of a round
  trip, which is arithmetic rather than execution quality. At 4% of rows and with
  the signs going both ways (+0.414, −0.277) it does not move a median; it is left
  in the store, flagged here, and worth filtering explicitly before any published
  spread statistic.
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
