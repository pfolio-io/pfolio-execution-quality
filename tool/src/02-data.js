/* Data layer: CSV parser, mutable stores fed from fallbacks or jsDelivr fetch. */

let MATRIX_PAPER = parseCsv(FALLBACK_MATRIX_PAPER);
let MATRIX_LIVE  = parseCsv(FALLBACK_MATRIX_LIVE);
let BROKER       = FALLBACK_BROKER;
let REG_FEES     = FALLBACK_REG_FEES;
let FX_RATES     = FALLBACK_FX_RATES;
let DATA_SOURCE  = "fallback"; /* flips to "repo" once jsDelivr fetch succeeds */

function parseCsv(text) {
  const lines = text.trim().split("\n").map(l => l.trim()).filter(Boolean);
  const header = lines[0].split(",");
  return lines.slice(1).map(line => {
    const cells = line.split(",");
    const row = {};
    header.forEach((h, i) => {
      const v = cells[i];
      row[h] = (v === "" || v === undefined) ? null : (isNaN(+v) ? v : +v);
    });
    return row;
  });
}

function indexByBucket(rows) {
  const out = {};
  rows.forEach(r => out[r.bucket] = r);
  return out;
}

let PAPER_BY_BUCKET = indexByBucket(MATRIX_PAPER);
let LIVE_BY_BUCKET  = indexByBucket(MATRIX_LIVE);

function rebuildIndices() {
  PAPER_BY_BUCKET = indexByBucket(MATRIX_PAPER);
  LIVE_BY_BUCKET  = indexByBucket(MATRIX_LIVE);
}

async function loadFromRepo(rerender) {
  try {
    const [paperRes, liveRes, brokerRes, regRes, fxRes] = await Promise.all([
      fetch(MATRIX_URLS.paper,    { mode: "cors" }),
      fetch(MATRIX_URLS.live,     { mode: "cors" }),
      fetch(TABLE_URLS.broker,    { mode: "cors" }),
      fetch(TABLE_URLS.reg_fees,  { mode: "cors" }),
      fetch(TABLE_URLS.fx_rates,  { mode: "cors" }),
    ]);
    if (!paperRes.ok || !liveRes.ok || !brokerRes.ok || !regRes.ok || !fxRes.ok) throw new Error("non-200");
    const [paperText, liveText, brokerJson, regJson, fxJson] = await Promise.all([
      paperRes.text(), liveRes.text(), brokerRes.json(), regRes.json(), fxRes.json(),
    ]);
    MATRIX_PAPER = parseCsv(paperText);
    MATRIX_LIVE  = parseCsv(liveText);
    BROKER       = brokerJson;
    REG_FEES     = regJson;
    /* fx_rates.json carries meta keys (_doc, _as_of) — strip to numbers only */
    FX_RATES = Object.fromEntries(
      Object.entries(fxJson).filter(([k, v]) => !k.startsWith("_") && typeof v === "number")
    );
    DATA_SOURCE = "repo";
    rebuildIndices();
    if (typeof rerender === "function") rerender();
  } catch (err) {
    if (typeof console !== "undefined") console.warn("[pfolio-oec] using inline fallback data —", err && err.message);
  }
}

/* Best-guess value (Rule B + outlier guard):
   - Live overrules paper whenever n_live ≥ 2 and the value isn't a clear
     outlier vs paper (|live - paper| < OUTLIER_BPS when n_live < 5).
   - n_live ≥ 5 always wins (no outlier check needed).
   - n_live = 1 is too thin — falls through.
   - Falls back to paper at n ≥ 10. Otherwise null (renders as low-n or
     "not supported"). */
function bestGuess(bucket, strategy) {
  const live = LIVE_BY_BUCKET[bucket];
  const paper = PAPER_BY_BUCKET[bucket];
  const liveBps = live ? live[strategy + "_median_bps"] : null;
  const liveN = live ? live[strategy + "_n"] : 0;
  const paperBps = paper ? paper[strategy + "_median_bps"] : null;
  const paperN = paper ? paper[strategy + "_n"] : 0;

  let liveTrusted = false;
  if (liveBps !== null) {
    if (liveN >= 5) {
      liveTrusted = true;
    } else if (liveN >= 2) {
      const paperKnown = paperBps !== null && paperN >= 10;
      liveTrusted = !paperKnown || Math.abs(liveBps - paperBps) < OUTLIER_BPS;
    }
  }
  if (liveTrusted) {
    return { bps: liveBps, n: liveN, source: "live" };
  }
  if (paperBps !== null && paperN >= 10) {
    return { bps: paperBps, n: paperN, source: "paper" };
  }
  return null;
}

/* Three-state classification per (bucket × strategy). */
function cellState(bucket, strategy) {
  const cell = bestGuess(bucket, strategy);
  if (cell) return { state: "ok", cell };
  const live = LIVE_BY_BUCKET[bucket];
  const paper = PAPER_BY_BUCKET[bucket];
  const liveN = (live && live[strategy + "_n"]) || 0;
  const paperN = (paper && paper[strategy + "_n"]) || 0;
  if (liveN === 0 && paperN === 0) return { state: "ineligible" };
  return { state: "thin", liveN, paperN };
}
