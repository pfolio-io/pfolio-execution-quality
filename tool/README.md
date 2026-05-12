# Order-execution-costs tool (browser bundle)

Browser port of the [`calculator/`](../calculator/) cost model + a matrix
view of [`order-execution/quality/results/`](../order-execution/quality/results/).
Renders the **order-execution-cost matrix** and **parametric trading-cost
calculator** that ship on <https://pfolio.io/tools/order-execution-costs>.

Reads matrix CSVs + static cost tables from this repo via jsDelivr at
runtime, so the data updates automatically when the harness pushes new
results; falls back to an inline snapshot if the network is unavailable.

## What's in the box

- **Matrix** of median bps slippage versus mid_t0 across 6 asset-class buckets ×
  4 order strategies (LMT_MID, MIDPRICE_NATIVE, MKT_ADAPTIVE, MKT_RAW). Each
  cell shows median bps + sample size; row-relative color saturation; ✓
  marker on the bucket's policy pick.
- **Calculator** — category × notional × side → bps + native-currency
  breakdown (slippage, commission, regulatory fees). Round-trip applies the
  policy pick to both legs. Inputs persist in `localStorage`.
- **Data sourcing** — both components fetch from
  `cdn.jsdelivr.net/gh/pfolio-io/pfolio-execution-quality@main/...` on
  mount. Inline fallback snapshot (2026-05-11) is used until the fetch
  succeeds, so the components always render.

## Bundle

`dist/pfolio-oec.js` — single hand-concatenated bundle, ~34 KiB.

To rebuild after editing source files:

```bash
./build.sh
```

No Node toolchain required. Build is shell concatenation in dependency order.

## Source layout

```
src/
  00-config.js          — URLs, labels, BUCKET_ORDER, POLICY_PICK_BY_BUCKET, etc.
  01-fallback.js        — inline FALLBACK_MATRIX_{PAPER,LIVE}, FALLBACK_BROKER,
                          FALLBACK_REG_FEES, FALLBACK_FX_RATES snapshots
  02-data.js            — parseCsv, indexByBucket, loadFromRepo, bestGuess,
                          cellState (Rule B + outlier guard)
  03-cost-model.js      — commissionForLeg, regFeesForLeg, regFeeLabel; port
                          of calculator/cost_model.py
  04-matrix.js          — buildMatrixSkeleton, colorForBps, renderMatrix
  05-calculator.js      — buildCalcSkeleton, render/recompute, localStorage
                          persistence, FX-aware notional conversion
  06-styles.js          — OEC_STYLES + injectStyles(); scoped to .oec-tool
  99-mount.js           — window.pfolioOEC public API + autoMount()

dist/
  pfolio-oec.js         — built bundle, ready to serve from a CDN

test/
  index.html            — local smoke harness — mount both components
                          against the inline fallback + live jsDelivr fetch
```

## Public API

After the bundle loads, `window.pfolioOEC` exposes:

```js
pfolioOEC.autoMount()              // mount #oec-matrix and/or #oec-calc
pfolioOEC.mountMatrix(rootEl)      // mount matrix into a custom element
pfolioOEC.mountCalculator(rootEl)  // mount calculator into a custom element
pfolioOEC.bestGuess(bucket, strat) // raw {bps, n, source} cell read
pfolioOEC.commissionForLeg(b, ntl) // USD commission per leg
pfolioOEC.regFeesForLeg(b, ntl, s) // [{key, amount}] reg-fee lines per leg
```

`autoMount()` is what production pages call. It looks for `#oec-matrix` and
`#oec-calc` on the page and wires them up. Either can be absent — partial
mounts work (e.g. matrix-only on a comparison page).

## Running locally

```bash
cd tool
python3 -m http.server 8080
# open http://127.0.0.1:8080/test/
```

## Production deployment

Upload `dist/pfolio-oec.js` to your CDN of choice, then add this to the
Webflow page (per-page Custom Code, before `</body>`):

```html
<script src="<your-cdn-url>/pfolio-oec.js" defer></script>
<script>
  document.addEventListener('DOMContentLoaded', () => pfolioOEC.autoMount());
</script>
```

Required placeholders on the page:

- `<div id="oec-matrix"></div>` — bundle builds the table inside it
- `<div id="oec-calc"></div>` — bundle builds the form inside it

### A note on jsdelivr

The script tag must **pin to a commit SHA, not `@main`**. The `@main` tag is
cached aggressively at jsdelivr's edge nodes and purge propagation is
unreliable, so you can end up with users running an old bundle for hours after
publishing. Pinning gives every release a unique URL and bypasses the cache:

```
https://cdn.jsdelivr.net/gh/pfolio-io/pfolio-execution-quality@<sha>/tool/dist/pfolio-oec.js
```

Update the SHA in the Webflow script tag on each tool release. The bundle's
*data* fetches (matrix CSVs + cost-table JSONs) intentionally still target
`pfolio-execution-quality@main` so harness refreshes show up automatically —
script tag and data tag rev independently. The 12h jsdelivr cache window is
acceptable for the data side.

## Data contract

The matrix and calculator both read from
`pfolio-io/pfolio-execution-quality@main`:

- `order-execution/quality/results/matrix_paper.csv` —
  `bucket,LMT_MID_median_bps,LMT_MID_n,MIDPRICE_NATIVE_median_bps,…`
- `order-execution/quality/results/matrix_live.csv` — same schema
- `order-execution/quality/cost_tables/broker_ibkr.json` — per-bucket
  IBKR Pro commission rules
- `order-execution/quality/cost_tables/reg_fees.json` — SEC, FINRA TAF,
  NFA, clearing, PTM levy (with `_inherits` chains)
- `order-execution/quality/cost_tables/fx_rates.json` — USD-anchored FX

Inline fallback snapshots in `src/01-fallback.js` get refreshed manually
when the public-repo data moves materially.

## Best-guess rule

For each `(bucket × strategy)` cell, the matrix renders the value from
whichever dataset is more trustworthy:

```
n_live ≥ 5                                          → live
n_live ≥ 2 and |live − paper| < 50 bps (or no paper) → live
n_paper ≥ 10                                        → paper
otherwise                                           → "low n" or "—"
```

The 50 bps outlier guard protects the matrix from one-off live samples
that haven't been replicated yet. Once `n_live ≥ 5`, the guard releases.

## Privacy

Fully client-side. Calculator inputs persist in `localStorage` only.
No server, no analytics, no telemetry. The only network calls are the
jsdelivr fetches for matrix CSVs and cost tables on mount.

## Licence

MIT — see [LICENSE](./LICENSE).
