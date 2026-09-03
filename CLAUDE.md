# pfolio-execution-quality

Measurement, not modelling. Three packages, one direction of flow: the harness
(`order-execution/quality/`) places tiny real orders at IBKR and records what
they cost; `calculator/` turns those measurements plus `cost_tables/` into a
bps-of-notional breakdown; `tool/` is the browser bundle behind
<https://pfolio.io/tools/order-execution-costs>, which fetches `cost_tables/`
and the matrix CSVs **from `@main` via jsDelivr**. Read `README.md` for the
harness, `METHODOLOGY.md` for the cost decomposition and caveats.

⚑ **A commit to `main` here is a publication.** The public page reads these
files directly; there is no build step and no staging copy between them.

## The rule this repo carries

> **S1-33.** *"COST IS NOT A DRIVER OF n. Measured from
> `pfolio-execution-quality` … one extra sleeve ≈ CHF 1.50/yr … a whole
> portfolio's annual trading CHF 42–48"* · *"Costs come from
> `pfolio-execution-quality`, not from assumptions."*
> — `pfolio-hq/docs/2026-07-30-decision-engine-sitting1-part2.md`, register row
> S1-33 and §closing.

Rendered workspace-wide, and this is the clause that binds work **in** this repo:

> *"`order-execution/quality/cost_tables/` + the live matrix are the ONLY
> admissible source of execution costs (S1-33) — **never assume a spread**."*
> — workspace `CLAUDE.md`, repo map; `ARCHITECTURE.md` §pfolio-execution-quality.

The failure mode is not only a made-up number. **An assumed spread arrives by
omission too**: an undeclared or unmeasured class returning `0.00 bps` inside a
total that reads as complete (`calculator/harness_data.py::measurement_state`,
and the 2026-08-04 `EU_STK_LSE` case in its docstring). Blank beside `n = 0` is
the honest rendering; zero is not.

## Table vintages, and how they refresh

| File | `_as_of` | Refresh from |
|---|---|---|
| `broker_ibkr.json` | 2026-05-04 · EU minimums re-measured 2026-08-12 | IBKR published schedule, or a measured live batch |
| `asset_class_buckets.json` | 2026-08-11 | venue codes read off IBKR `validExchanges` |
| `reg_fees.json` · `tax_rules.json` | 2026-05-04 | official regulator/tax schedules |
| `fx_rates.json` | **2026-05-04, stale — see below** | its own `_doc`: **IDEALPRO mid quotes** |

⚑ **`fx_rates.json` is uncommitted and its stated vintage is wrong.** Today's
day-desk item **DD-4** added `MXN`, `SEK`, `SGD` — **Riksbank fixings**, which
are *not* IDEALPRO mids, and only SEK is a Riksbank quote natively; the other
two are SEK crosses re-anchored to USD. `_as_of` still reads `2026-05-04`, so
the file understates its own age and misdescribes its own source. Three things
are owed before this commits: bump `_as_of` to 2026-09-03, record the Riksbank
provenance **in the file** (`_doc` currently promises IDEALPRO), and let Marcel
decide whether a fixing is admissible here at all. The SEK row was owed —
`pfolio-app/research/one-tier-measurement` emitted `NO_ADMISSIBLE_FX_RATE`
rather than convert at a rate found elsewhere (F-R6), which is S1-33 working.

## What a Claude session may and may not write here

**May, unasked:** read anything · run `python -m quality.preflight` (READ-ONLY,
places no orders) · run `analyze.py` over the committed stores · run the tests ·
edit `README.md`, `METHODOLOGY.md`, this file · write code in `calculator/`,
`quality/`, `tool/src/` · report a venue with no commission rule
(`venue_coverage` prints them; reporting is the correct outcome, not adding one).

**May not:** edit any file under `cost_tables/` (hq convention 8; read-only to
every agent) · place a live order — **Marcel types
`quality.runner --mode live --yes-live`, always; the agent specs and prices the
batch, he fires it, the agent resumes at the store** · commit or push.

**A cost table changes by exactly two routes: a measured run, or Marcel's call.**
Both prior edits (`asset_class_buckets.json` 2026-08-11,
`broker_ibkr.json` 2026-08-12) carried explicit per-edit authorisation recorded
in the file's own `_edit_authority`, and both files say in terms that they are
**not a precedent**. Measurement is a lane's job; canon is his.

## Tests

`python3 -m pytest tests -q` from the repo root. No IB connection, no network —
but `tests/test_eu_venue_guards.py` imports `quality.runner`, so **the whole
suite needs `ib_insync` and `nest_asyncio` installed**, a broker-API wrapper
pulled in purely to make an import resolve. `tests/test_buckets.py` needs only
`pandas` and runs anywhere. `pip install -r requirements.txt` covers both.
