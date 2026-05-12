/* pfolio-oec.js — built bundle. Do not edit; regenerate via build.sh.       */
/* See pfolio-io/pfolio-execution-quality (tool/src/) for source.            */
(function () {
  'use strict';

  /* ───── 00-config.js ───── */
/* Configuration constants — URLs, labels, ordering, policy picks. */

const REPO_BASE_RESULTS = "https://cdn.jsdelivr.net/gh/pfolio-io/pfolio-execution-quality@main/order-execution/quality/results";
const REPO_BASE_TABLES  = "https://cdn.jsdelivr.net/gh/pfolio-io/pfolio-execution-quality@main/order-execution/quality/cost_tables";
const MATRIX_URLS = {
  paper: `${REPO_BASE_RESULTS}/matrix_paper.csv`,
  live:  `${REPO_BASE_RESULTS}/matrix_live.csv`,
};
const TABLE_URLS = {
  broker:   `${REPO_BASE_TABLES}/broker_ibkr.json`,
  reg_fees: `${REPO_BASE_TABLES}/reg_fees.json`,
  fx_rates: `${REPO_BASE_TABLES}/fx_rates.json`,
};

const STRATEGIES = ["LMT_MID", "MIDPRICE_NATIVE", "MKT_ADAPTIVE", "MKT_RAW"];
const STRATEGY_LABEL = {
  LMT_MID: "Limit at mid",
  MIDPRICE_NATIVE: "IB midprice algo",
  MKT_ADAPTIVE: "IB adaptive algo",
  MKT_RAW: "Plain market",
};
const BUCKET_LABEL = {
  US_STK: "US large-cap stock",
  US_ETF: "US ETF",
  US_SMALL_CAP_STK: "US small-cap stock",
  FUT_CME: "US futures (high liquidity)",
  FUT_CFE: "US futures (low liquidity)",
  FX_IDEALPRO: "FX",
};
const BUCKET_ORDER = ["US_STK", "US_ETF", "US_SMALL_CAP_STK", "FUT_CME", "FUT_CFE", "FX_IDEALPRO"];

/* Policy pick per bucket. Mirrors section "Our recommended execution policy":
   derived from each bucket's typical spread band and per-step eligibility.
   Used by both the matrix ✓ marker and the calculator; round-trip applies the
   same pick to BOTH legs (no auto-flatten asymmetry). */
const POLICY_PICK_BY_BUCKET = {
  US_STK:           "MIDPRICE_NATIVE",
  US_ETF:           "MIDPRICE_NATIVE",
  US_SMALL_CAP_STK: "LMT_MID",
  FUT_CME:          "MKT_ADAPTIVE",
  FUT_CFE:          "LMT_MID",
  FX_IDEALPRO:      "MKT_RAW",
};

const FX_CURRENCY_ORDER = ["USD", "AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD"];

/* Reference price + contract multiplier per V1 bucket. The form takes notional
   only; we synthesize qty = notional / (price × multiplier) so per-share /
   per-contract rules and the $0.35 minimum behave correctly. */
const BUCKET_PRICE_MULT = {
  US_STK:           { price: 200,   multiplier: 1 },
  US_ETF:           { price: 400,   multiplier: 1 },
  US_SMALL_CAP_STK: { price: 30,    multiplier: 1 },
  FUT_CME:          { price: 5000,  multiplier: 50 },
  FUT_CFE:          { price: 20,    multiplier: 1000 },
  FX_IDEALPRO:      { price: 1,     multiplier: 1 },
};

const OUTLIER_BPS = 50;
const CALC_STATE_STORAGE_KEY = "oec_calc_v1";

const REG_FEE_EXPLANATION = {
  sec:       "Securities and Exchange Commission fee on US equity sales (~USD 27.80 per USD 1M of principal sold).",
  finra_taf: "FINRA Trading Activity Fee on US equity sales (~USD 0.000166 per share, capped at USD 8.30 per trade).",
  nfa:       "National Futures Association fee on US futures, both sides (USD 0.02 per contract).",
  clearing:  "Exchange clearing fee, charged per trade.",
  ptm_levy:  "UK Panel on Takeovers and Mergers levy (GBP 1.00 per trade on UK equities at GBP 10,000 notional or above).",
};

  /* ───── 01-fallback.js ───── */
/* Inline fallback data — snapshot from 2026-05-11 (post the targeted VIX+PRIM
   live sweep). Used if the jsDelivr fetch fails. parseCsv() trims per-line
   whitespace so the source-file indentation below doesn't break bucket lookup. */

const FALLBACK_MATRIX_PAPER = `bucket,LMT_MID_median_bps,LMT_MID_n,MIDPRICE_NATIVE_median_bps,MIDPRICE_NATIVE_n,MKT_ADAPTIVE_median_bps,MKT_ADAPTIVE_n,MKT_RAW_median_bps,MKT_RAW_n
FUT_CFE,12.8041,17,,0,-0.0328,4,12.8041,63
FUT_CME,-0.1721,29,,0,-0.1715,16,0.1718,87
FX_IDEALPRO,-0.2136,27,,0,,0,0.0427,63
US_ETF,0.0000,71,-0.4623,61,0.3456,46,0.4607,265
US_SMALL_CAP_STK,-2.3468,10,-2.4459,3,0.1229,6,19.8574,59
US_STK,-0.1785,26,-0.5312,26,-0.1783,16,0.8804,95`;

const FALLBACK_MATRIX_LIVE = `bucket,LMT_MID_median_bps,LMT_MID_n,MIDPRICE_NATIVE_median_bps,MIDPRICE_NATIVE_n,MKT_ADAPTIVE_median_bps,MKT_ADAPTIVE_n,MKT_RAW_median_bps,MKT_RAW_n
FUT_CFE,12.9366,3,,0,12.8700,1,12.9366,8
FUT_CME,-0.1708,4,,0,-0.0005,4,0.1714,16
FX_IDEALPRO,0.0216,4,,0,,0,0.0641,12
US_ETF,-0.0770,4,-0.1376,4,-0.1035,4,0.2070,20
US_SMALL_CAP_STK,-2.1799,3,-2.8019,4,-0.9211,3,6.8274,14
US_STK,-0.1762,4,-2.0278,4,-0.7928,4,-0.2642,20`;

const FALLBACK_BROKER = {
  US_STK:           { currency: "USD", per_share: 0.0035, min_per_order: 0.35, max_pct_of_notional: 0.01 },
  US_ETF:           { currency: "USD", per_share: 0.0035, min_per_order: 0.35, max_pct_of_notional: 0.01 },
  US_SMALL_CAP_STK: { currency: "USD", per_share: 0.0035, min_per_order: 0.35, max_pct_of_notional: 0.01 },
  EU_STK_XETRA:     { currency: "EUR", per_value_bps: 5.0, min_per_order: 1.25, max_per_order: 99.0 },
  EU_STK_LSE:       { currency: "GBP", per_value_bps: 5.0, min_per_order: 1.0,  max_pct_of_notional: 0.0149 },
  EU_STK_SIX:       { currency: "CHF", per_value_bps: 6.0, min_per_order: 1.5,  max_per_order: 99.0 },
  FX_IDEALPRO:      { currency: "USD", per_value_bps: 0.20, min_per_order: 2.0 },
  CFD_FX:           { currency: "USD", per_value_bps: 0.20, min_per_order: 2.0 },
  CFD_INDEX:        { currency: "USD", per_value_bps: 0.50, min_per_order: 1.0 },
  FUT_CME:          { currency: "USD", per_contract: 0.85, exchange_fee_per_contract: 1.40 },
  FUT_CME_MICRO:    { currency: "USD", per_contract: 0.25, exchange_fee_per_contract: 0.35 },
  FUT_CFE:          { currency: "USD", per_contract: 1.50, exchange_fee_per_contract: 0.85 },
  FUT_NYBOT:        { currency: "USD", per_contract: 1.50, exchange_fee_per_contract: 0.72 },
  FUT_EUREX:        { currency: "EUR", per_contract: 1.0,  exchange_fee_per_contract: 0.50 },
};

const FALLBACK_REG_FEES = {
  US_STK: {
    currency: "USD",
    sells_only: {
      sec_fee_per_million: 27.80,
      finra_taf_per_share: 0.000166,
      finra_taf_max_per_trade: 8.30,
    },
    buys_only: {},
    both_sides: {},
  },
  US_ETF:           { currency: "USD", _inherits: "US_STK" },
  US_SMALL_CAP_STK: { currency: "USD", _inherits: "US_STK" },
  FUT_CME:          { currency: "USD", both_sides: { nfa_fee_per_contract: 0.02 } },
  FUT_CFE:          { currency: "USD", _inherits: "FUT_CME" },
  FUT_NYBOT:        { currency: "USD", _inherits: "FUT_CME" },
  FX_IDEALPRO:      { currency: "USD", buys_only: {}, sells_only: {}, both_sides: {} },
  CFD_FX:           { currency: "USD", _inherits: "FX_IDEALPRO" },
  EU_STK_XETRA:     { currency: "EUR", both_sides: { clearing_fee_per_trade: 0.10 } },
  EU_STK_LSE:       { currency: "GBP", buys_only: { ptm_levy_per_trade: 1.00 }, both_sides: {} },
  EU_STK_SIX:       { currency: "CHF", both_sides: {} },
};

/* USD-anchored FX rates: each value is the USD price of 1 unit of that currency. */
const FALLBACK_FX_RATES = {
  USD: 1.0,
  AUD: 0.66,
  CAD: 0.74,
  CHF: 1.20,
  EUR: 1.10,
  GBP: 1.27,
  JPY: 0.0066,
  NZD: 0.61,
};

  /* ───── 02-data.js ───── */
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

  /* ───── 03-cost-model.js ───── */
/* Cost engine — browser port of calculator/cost_model.py.
   All amounts returned in the broker rule's native currency. V1 buckets are
   USD-only so the FX layer is a no-op until EU_* buckets land. */

function resolveInherited(node, table) {
  if (!node || !node._inherits) return Object.assign({}, node || {});
  const parent = resolveInherited(table[node._inherits], table);
  for (const [k, v] of Object.entries(node)) {
    if (k !== "_inherits") parent[k] = v;
  }
  return parent;
}

function applyFeeRule(name, value, qty, notionalNative) {
  if (typeof value !== "number") return 0;
  if (name.startsWith("_") || name.endsWith("_doc") || name.endsWith("_max_per_trade")) return 0;
  if (name.endsWith("_per_million")) return value * notionalNative / 1e6;
  if (name.endsWith("_per_share"))   return value * qty;
  if (name.endsWith("_per_trade"))   return value;
  if (name.endsWith("_per_contract"))return value * qty;
  return 0;
}

/* Commission for ONE leg, in native currency. Mirrors cost_model.py::_commission. */
function commissionForLeg(bucket, notional) {
  const rule = BROKER[bucket];
  if (!rule || typeof rule !== "object") return 0;
  const px = BUCKET_PRICE_MULT[bucket] || { price: 1, multiplier: 1 };
  const qty = notional / (px.price * px.multiplier);
  let raw = 0;
  if (rule.per_share != null) {
    raw = qty * rule.per_share;
  } else if (rule.per_value_bps != null) {
    raw = notional * rule.per_value_bps / 1e4;
  } else if (rule.per_contract != null) {
    raw = qty * rule.per_contract + qty * (rule.exchange_fee_per_contract || 0);
  } else {
    return 0;
  }
  if (rule.min_per_order != null)       raw = Math.max(raw, rule.min_per_order);
  if (rule.max_pct_of_notional != null) raw = Math.min(raw, notional * rule.max_pct_of_notional);
  if (rule.max_per_order != null)       raw = Math.min(raw, rule.max_per_order);
  return raw;
}

/* Reg-fee lines for ONE leg on the given side. Returns array of
   {key, amount} so callers can convert to bps and label them. */
function regFeesForLeg(bucket, notional, side) {
  const node = REG_FEES[bucket];
  if (!node) return [];
  const rule = resolveInherited(node, REG_FEES);
  const px = BUCKET_PRICE_MULT[bucket] || { price: 1, multiplier: 1 };
  const qty = notional / (px.price * px.multiplier);
  const sideKey = side === "BUY" ? "buys_only" : "sells_only";
  const buckets = [rule[sideKey] || {}, rule.both_sides || {}];
  const lines = [];
  for (const b of buckets) {
    for (const [k, v] of Object.entries(b)) {
      const amt = applyFeeRule(k, v, qty, notional);
      if (amt > 0) lines.push({ key: k, amount: amt });
    }
  }
  return lines;
}

function regFeeRoot(key) {
  return key
    .replace(/_per_million$/, "")
    .replace(/_per_share$/, "")
    .replace(/_per_trade$/, "")
    .replace(/_per_contract$/, "")
    .replace(/_fee$/, "");
}

function regFeeLabel(key) {
  const root = regFeeRoot(key);
  const map = {
    sec: "SEC fee",
    finra_taf: "FINRA TAF",
    nfa: "NFA fee",
    clearing: "Clearing fee",
    ptm_levy: "PTM levy",
  };
  return map[root] || root.replace(/_/g, " ");
}

  /* ───── 04-matrix.js ───── */
/* Matrix render: builds the table skeleton inside the placeholder, then
   fills the tbody row-by-row from the data layer. Each cell shows median
   bps + sample size, with a row-relative color saturation. The ✓ marker
   reflects the production policy pick from POLICY_PICK_BY_BUCKET. */

let MATRIX_ROOT = null;

function buildMatrixSkeleton(root) {
  root.classList.add("oec-tool");
  root.innerHTML = `
    <div class="oec-matrix-wrap">
      <table class="oec-matrix">
        <thead>
          <tr>
            <th>Category</th>
            <th title="Limit order priced at the midpoint at submit (T0), with retries">Limit at mid</th>
            <th title="IB's hosted MIDPRICE algorithm — internally waits and fills at the prevailing midpoint">IB midprice algo</th>
            <th title="IB's Adaptive algorithm at Normal priority">IB adaptive algo</th>
            <th title="Plain market order — fills immediately at the touch">Plain market</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  `;
}

function colorForBps(bps, cap) {
  if (bps === null || bps === undefined) return "transparent";
  /* Row-wise saturation: caller passes the row's max-abs bps (with a 1 bps
     floor so noise rows don't all saturate to full color). */
  const c = Math.max(cap || 0, 1);
  const t = Math.min(Math.abs(bps) / c, 1);
  if (bps < 0) {
    /* improvement → brand teal: #d6f4f1 → #00bfb2 */
    const r = Math.round(214 + (0   - 214) * t);
    const g = Math.round(244 + (191 - 244) * t);
    const b = Math.round(241 + (178 - 241) * t);
    return `rgba(${r},${g},${b},${0.35 + 0.5 * t})`;
  } else {
    /* cost → brand coral: #fbe1df → #ef6f6c */
    const r = Math.round(251 + (239 - 251) * t);
    const g = Math.round(225 + (111 - 225) * t);
    const b = Math.round(223 + (108 - 223) * t);
    return `rgba(${r},${g},${b},${0.35 + 0.5 * t})`;
  }
}

function renderMatrix() {
  if (!MATRIX_ROOT) return;
  const tbody = MATRIX_ROOT.querySelector("table.oec-matrix tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  BUCKET_ORDER.forEach(bucket => {
    const tr = document.createElement("tr");
    const tdBucket = document.createElement("td");
    tdBucket.textContent = BUCKET_LABEL[bucket];
    tr.appendChild(tdBucket);
    const rec = POLICY_PICK_BY_BUCKET[bucket];

    /* Row-wise color cap = max absolute bps of cells that meet threshold.
       Falls back to 1 bps minimum so near-noise rows don't all saturate. */
    const states = STRATEGIES.map(s => cellState(bucket, s));
    const rowCap = Math.max(
      ...states.filter(s => s.state === "ok").map(s => Math.abs(s.cell.bps)),
      0,
    );

    STRATEGIES.forEach((strategy, i) => {
      const td = document.createElement("td");
      td.className = "oec-cell";
      const s = states[i];
      if (s.state === "ok") {
        td.style.backgroundColor = colorForBps(s.cell.bps, rowCap);
        const sign = s.cell.bps > 0 ? "+" : "";
        td.innerHTML = `<div class="oec-bps">${sign}${s.cell.bps.toFixed(2)} bps</div><div class="oec-n" title="Source: ${s.cell.source} fills">n=${s.cell.n}</div>`;
        if (strategy === rec) td.classList.add("oec-recommended");
      } else if (s.state === "thin") {
        td.classList.add("oec-empty", "oec-thin");
        const total = (s.paperN || 0) + (s.liveN || 0);
        td.innerHTML = `<div class="oec-bps">low n</div><div class="oec-n" title="Strategy is supported but neither dataset clears its threshold (live ≥ 2 with outlier guard, or paper ≥ 10). Combined n=${total}.">below threshold</div>`;
      } else {
        td.classList.add("oec-empty");
        td.innerHTML = `<div class="oec-bps">—</div><div class="oec-n" title="Order type not available for this category (eligibility or no live quote)">not supported</div>`;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

  /* ───── 05-calculator.js ───── */
/* Calculator render: builds the form skeleton inside the placeholder, wires
   inputs to a state object persisted in localStorage, recomputes the
   bps/native-currency breakdown on every change. */

let CALC_ROOT = null;
let calcState = { bucket: "US_STK", notional: 10000, side: "BOTH", currency: "USD" };

function buildCalcSkeleton(root) {
  root.classList.add("oec-tool");
  root.innerHTML = `
    <div class="oec-calc">
      <div class="oec-calc-grid">
        <div>
          <label for="oec-ac">Category</label>
          <select id="oec-ac"></select>
        </div>
        <div>
          <label for="oec-notional">Notional</label>
          <div class="oec-notional-row">
            <input id="oec-notional" min="100" step="1000" type="number" value="10000">
            <select aria-label="Display currency" id="oec-ccy"></select>
          </div>
        </div>
        <div>
          <label>Side</label>
          <div class="oec-seg" id="oec-side">
            <button data-side="BUY">Buy</button>
            <button data-side="SELL">Sell</button>
            <button class="oec-active" data-side="BOTH">Round-trip</button>
          </div>
        </div>
      </div>
      <p class="oec-calc-summary" id="oec-calc-summary"></p>
      <table class="oec-breakdown" id="oec-breakdown"><tbody></tbody></table>
      <div class="oec-reg-fee-note" id="oec-reg-fee-note"></div>
    </div>
  `;
}

function loadCalcStateFromStorage() {
  try {
    const raw = localStorage.getItem(CALC_STATE_STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved && typeof saved === "object") {
      if (BUCKET_LABEL[saved.bucket]) calcState.bucket = saved.bucket;
      if (Number.isFinite(saved.notional) && saved.notional > 0) calcState.notional = saved.notional;
      if (["BUY", "SELL", "BOTH"].includes(saved.side)) calcState.side = saved.side;
      if (FX_RATES[saved.currency]) calcState.currency = saved.currency;
    }
  } catch { /* ignore corrupt state */ }
}

function saveCalcStateToStorage() {
  try { localStorage.setItem(CALC_STATE_STORAGE_KEY, JSON.stringify(calcState)); } catch { /* ignore */ }
}

function populateAssetClassDropdown() {
  const sel = CALC_ROOT.querySelector("#oec-ac");
  sel.innerHTML = "";
  BUCKET_ORDER.forEach(b => {
    const opt = document.createElement("option");
    opt.value = b;
    opt.textContent = BUCKET_LABEL[b];
    sel.appendChild(opt);
  });
  sel.value = calcState.bucket;
}

function populateCurrencyDropdown() {
  const sel = CALC_ROOT.querySelector("#oec-ccy");
  sel.innerHTML = "";
  FX_CURRENCY_ORDER.forEach(c => {
    if (!FX_RATES[c]) return;
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    sel.appendChild(opt);
  });
  sel.value = calcState.currency;
}

function bindCalcInputs() {
  CALC_ROOT.querySelector("#oec-ac").addEventListener("change", e => {
    calcState.bucket = e.target.value; saveCalcStateToStorage(); renderCalc();
  });
  CALC_ROOT.querySelector("#oec-notional").addEventListener("input", e => {
    calcState.notional = +e.target.value || 0; saveCalcStateToStorage(); renderCalc();
  });
  CALC_ROOT.querySelector("#oec-ccy").addEventListener("change", e => {
    const newCcy = e.target.value;
    const oldCcy = calcState.currency;
    if (newCcy !== oldCcy) {
      /* Preserve USD-equivalent trade size — otherwise switching from USD
         10,000 to JPY would leave the user at JPY 10,000 (≈ USD 66), where
         the commission floor dominates and the bps blows up. */
      const oldRate = FX_RATES[oldCcy] || 1;
      const newRate = FX_RATES[newCcy] || 1;
      const usdEquiv = calcState.notional * oldRate;
      calcState.notional = roundNotionalToClean(usdEquiv / newRate);
      CALC_ROOT.querySelector("#oec-notional").value = calcState.notional;
    }
    calcState.currency = newCcy;
    saveCalcStateToStorage();
    renderCalc();
  });
  CALC_ROOT.querySelectorAll("#oec-side button").forEach(btn => {
    btn.addEventListener("click", () => {
      CALC_ROOT.querySelectorAll("#oec-side button").forEach(b => b.classList.remove("oec-active"));
      btn.classList.add("oec-active");
      calcState.side = btn.dataset.side;
      saveCalcStateToStorage();
      renderCalc();
    });
  });
  /* Restore side selection from state */
  CALC_ROOT.querySelectorAll("#oec-side button").forEach(b => {
    b.classList.toggle("oec-active", b.dataset.side === calcState.side);
  });
  CALC_ROOT.querySelector("#oec-notional").value = calcState.notional;
}

function fmtBps(b) { return (b >= 0 ? "+" : "") + b.toFixed(2) + " bps"; }

/* Round a notional to a tidy magnitude — 100k step for ≥1M, 10k for ≥100k,
   1k for ≥10k, 100 for ≥1k, etc. */
function roundNotionalToClean(amount) {
  const abs = Math.abs(amount);
  if (abs <= 0) return 0;
  let step;
  if (abs >= 1e6)      step = 1e5;
  else if (abs >= 1e5) step = 1e4;
  else if (abs >= 1e4) step = 1e3;
  else if (abs >= 1e3) step = 100;
  else if (abs >= 100) step = 10;
  else                 step = 1;
  return Math.max(step, Math.round(amount / step) * step);
}

/* Currency format: ISO 4217 code only (USD/EUR/CHF/…), never symbol.
   Format: "USD 10,000.00" or "-USD 0.36". */
function formatCcy(amount) {
  const sign = amount < 0 ? "-" : "";
  const abs = Math.abs(amount).toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  return `${sign}${calcState.currency} ${abs}`;
}

function renderCalc() {
  if (!CALC_ROOT) return;
  const { bucket, notional, side, currency } = calcState;
  const bucketLabel = BUCKET_LABEL[bucket];
  const rec = POLICY_PICK_BY_BUCKET[bucket] || "MKT_RAW";
  const recCell = bestGuess(bucket, rec);

  const sides = side === "BOTH" ? ["BUY", "SELL"] : [side];
  const legs = sides.length;
  const safeNotional = notional > 0 ? notional : 0;

  /* Internal math anchored in USD (every V1 broker rule is USD-denominated).
     Notional input is in user's display currency; convert to USD for
     commission / reg-fee math, then derive bps. Bps is currency-invariant. */
  const usdPerDisplayUnit = FX_RATES[currency] || 1;
  const safeNotionalUsd = safeNotional * usdPerDisplayUnit;

  const rows = [];

  /* 1. Slippage = realized execution cost vs mid_t0, summed across legs.
        Capped at zero so the calculator doesn't imply guaranteed price
        improvement from low-n negative measurements (the matrix shows the
        raw value). */
  let slippageNote = null;
  if (recCell) {
    const rawBps = recCell.bps * legs;
    const cappedBps = Math.max(0, rawBps);
    const legSuffix = legs > 1 ? `, ×${legs} legs` : "";
    rows.push({
      label: `Slippage (${STRATEGY_LABEL[rec]}${legSuffix})`,
      bps: cappedBps,
    });
    if (rawBps < 0) {
      slippageNote = `Measured slippage on ${bucketLabel.toLowerCase()} with ` +
        `${STRATEGY_LABEL[rec]} is ${rawBps.toFixed(2)} bps across our ${legs}-leg sample ` +
        `(price improvement). The calculator caps slippage at zero so the total isn't a ` +
        `promise of negative cost—the matrix above shows the raw measurement.`;
    }
  }

  /* 2. Commission (broker rule × legs). */
  if (safeNotionalUsd > 0) {
    const commPerLegUsd = commissionForLeg(bucket, safeNotionalUsd);
    const commTotalUsd = commPerLegUsd * legs;
    if (commTotalUsd > 0) {
      rows.push({
        label: `Commission${legs > 1 ? ` (×${legs} legs)` : ""}`,
        bps: (commTotalUsd / safeNotionalUsd) * 1e4,
      });
    }
  }

  /* 3. Regulatory fees (one combined line). Side-aware. */
  const appearingFeeRoots = [];
  if (safeNotionalUsd > 0) {
    const feeAmounts = {};
    for (const s of sides) {
      for (const { key, amount } of regFeesForLeg(bucket, safeNotionalUsd, s)) {
        feeAmounts[key] = (feeAmounts[key] || 0) + amount;
      }
    }
    const feeKeys = Object.keys(feeAmounts);
    if (feeKeys.length > 0) {
      const totalNativeUsd = feeKeys.reduce((sum, k) => sum + feeAmounts[k], 0);
      const tags = [...new Set(feeKeys.map(regFeeLabel))];
      rows.push({
        label: `Regulatory fees (${tags.join(", ")})`,
        bps: (totalNativeUsd / safeNotionalUsd) * 1e4,
      });
      for (const root of new Set(feeKeys.map(regFeeRoot))) {
        appearingFeeRoots.push(root);
      }
    }
  }

  const totalBps = rows.reduce((sum, r) => sum + r.bps, 0);
  const totalDisplay = (totalBps / 1e4) * notional;

  const sideLabel = side === "BOTH" ? "Round-trip" : (side === "BUY" ? "Buy" : "Sell");
  CALC_ROOT.querySelector("#oec-calc-summary").innerHTML =
    `${sideLabel} cost on ${formatCcy(notional)} of ${bucketLabel}: ` +
    `<strong>${totalBps >= 0 ? "+" : ""}${totalBps.toFixed(2)} bps</strong> ` +
    `(≈ ${formatCcy(totalDisplay)})`;

  const tbody = CALC_ROOT.querySelector("#oec-breakdown tbody");
  tbody.innerHTML = "";
  rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${r.label}</td><td>${fmtBps(r.bps)}</td><td>${formatCcy((r.bps / 1e4) * notional)}</td>`;
    tbody.appendChild(tr);
  });
  const trTotal = document.createElement("tr");
  trTotal.className = "oec-total";
  trTotal.innerHTML = `<td>Total</td><td>${fmtBps(totalBps)}</td><td>${formatCcy(totalDisplay)}</td>`;
  tbody.appendChild(trTotal);

  const noteEl = CALC_ROOT.querySelector("#oec-reg-fee-note");
  noteEl.innerHTML = "";
  if (slippageNote) {
    const item = document.createElement("span");
    item.className = "oec-item";
    item.innerHTML = `<span class="oec-name">Note on slippage.</span> ${slippageNote}`;
    noteEl.appendChild(item);
  }
  for (const root of appearingFeeRoots) {
    const explanation = REG_FEE_EXPLANATION[root];
    if (!explanation) continue;
    const name = regFeeLabel(root);
    const item = document.createElement("span");
    item.className = "oec-item";
    item.innerHTML = `<span class="oec-name">${name}.</span> ${explanation}`;
    noteEl.appendChild(item);
  }
}

  /* ───── 06-styles.js ───── */
/* Scoped CSS for the matrix + calculator components only. All selectors
   are namespaced under .oec-tool to avoid leaking into the host page's
   styles. Brand colors mirror pfolio.io webflow.shared CSS. */

const OEC_STYLES = `
.oec-tool {
  --oec-ink: #1f2f36;
  --oec-muted: #1f2f36;
  --oec-rule: #dee2e6;
  --oec-bg: #f8f9fa;
  --oec-accent: #264653;
  --oec-primary-2: #00bfb2;
  font-family: Poppins, Arial, sans-serif;
  font-size: 16px;
  line-height: 1.5;
  color: var(--oec-ink);
}

/* ─── Matrix ─── */
.oec-matrix-wrap { overflow-x: auto; }
.oec-tool table.oec-matrix {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  margin-top: 8px;
}
.oec-tool table.oec-matrix th,
.oec-tool table.oec-matrix td {
  padding: 16px 12px;
  border-bottom: 1px solid var(--oec-rule);
  text-align: right;
  vertical-align: top;
}
.oec-tool table.oec-matrix th {
  font-family: Poppins, Arial, sans-serif;
  font-weight: 600;
  font-size: 13px;
  color: var(--oec-muted);
  text-align: right;
}
.oec-tool table.oec-matrix th:first-child,
.oec-tool table.oec-matrix td:first-child {
  text-align: left;
  font-weight: 500;
  color: var(--oec-ink);
}
.oec-tool table.oec-matrix td.oec-cell {
  position: relative;
  padding-right: 28px;
}
.oec-tool .oec-bps {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 15px;
  color: var(--oec-ink);
}
.oec-tool .oec-n {
  font-size: 11px;
  color: var(--oec-muted);
  margin-top: 2px;
}
.oec-tool td.oec-cell.oec-empty { color: var(--oec-muted); }
.oec-tool td.oec-cell.oec-empty .oec-bps { font-weight: 500; font-size: 14px; }
.oec-tool td.oec-cell.oec-thin { font-style: italic; }
.oec-tool td.oec-cell.oec-recommended .oec-bps::after {
  content: " ✓";
  color: var(--oec-primary-2);
  font-weight: 700;
}

/* ─── Calculator ─── */
.oec-tool .oec-calc {
  background: white;
  border: 1px solid var(--oec-rule);
  border-radius: 12px;
  padding: 28px;
  margin-top: 16px;
}
.oec-tool .oec-calc-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 16px;
  margin-bottom: 28px;
}
.oec-tool .oec-calc-grid label {
  display: block;
  font-size: 13px;
  color: var(--oec-muted);
  margin-bottom: 6px;
  font-weight: 600;
}
.oec-tool .oec-calc-grid select,
.oec-tool .oec-calc-grid input {
  width: 100%;
  height: 48px;
  padding: 0 14px;
  font-size: 16px;
  line-height: 1.5;
  border: 1px solid var(--oec-rule);
  border-radius: 8px;
  background: white;
  font-family: Poppins, Arial, sans-serif;
  color: var(--oec-ink);
}
.oec-tool .oec-notional-row { display: flex; gap: 8px; }
.oec-tool .oec-notional-row input { flex: 1; }
.oec-tool .oec-notional-row select { width: 96px; padding: 0 10px; }
.oec-tool .oec-calc-grid select:focus,
.oec-tool .oec-calc-grid input:focus {
  outline: none;
  border-color: var(--oec-accent);
}
.oec-tool .oec-seg {
  display: flex;
  height: 48px;
  border: 1px solid var(--oec-rule);
  border-radius: 8px;
  overflow: hidden;
  background: white;
}
.oec-tool .oec-seg button {
  flex: 1;
  height: 100%;
  padding: 0 8px;
  border: 0;
  background: white;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  font-family: Poppins, Arial, sans-serif;
  font-weight: 500;
  color: var(--oec-ink);
  transition: background-color .15s;
}
.oec-tool .oec-seg button:hover { background: var(--oec-bg); }
.oec-tool .oec-seg button.oec-active { background: var(--oec-accent); color: white; }
.oec-tool .oec-calc-summary {
  font-size: 18px;
  line-height: 1.5;
  margin: 8px 0 18px;
  color: var(--oec-ink);
}
.oec-tool .oec-calc-summary strong { font-weight: 700; }
.oec-tool table.oec-breakdown {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
.oec-tool table.oec-breakdown td {
  padding: 12px 0;
  border-bottom: 1px solid var(--oec-rule);
}
.oec-tool table.oec-breakdown td:nth-child(2),
.oec-tool table.oec-breakdown td:nth-child(3) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.oec-tool table.oec-breakdown tr.oec-total td {
  font-weight: 700;
  border-bottom: none;
  border-top: 2px solid var(--oec-ink);
  padding-top: 14px;
}
.oec-tool .oec-reg-fee-note {
  margin-top: 18px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--oec-ink);
}
.oec-tool .oec-reg-fee-note .oec-item { display: block; margin-top: 4px; }
.oec-tool .oec-reg-fee-note .oec-item:first-child { margin-top: 0; }
.oec-tool .oec-reg-fee-note .oec-name { font-weight: 600; }

@media (max-width: 767px) {
  .oec-tool .oec-calc-grid { grid-template-columns: 1fr; }
}
`;

let STYLES_INJECTED = false;
function injectStyles() {
  if (STYLES_INJECTED) return;
  const style = document.createElement("style");
  style.setAttribute("data-pfolio-oec", "1");
  style.textContent = OEC_STYLES;
  document.head.appendChild(style);
  STYLES_INJECTED = true;
}

  /* ───── 99-mount.js ───── */
/* Public API on window.pfolioOEC.
   autoMount() looks for #oec-matrix and #oec-calc on the page and wires
   them up. Either can be absent — partial mounts work. */

function mountMatrix(rootEl) {
  if (!rootEl) return;
  injectStyles();
  MATRIX_ROOT = rootEl;
  buildMatrixSkeleton(rootEl);
  renderMatrix();
}

function mountCalculator(rootEl) {
  if (!rootEl) return;
  injectStyles();
  CALC_ROOT = rootEl;
  buildCalcSkeleton(rootEl);
  loadCalcStateFromStorage();
  populateAssetClassDropdown();
  populateCurrencyDropdown();
  bindCalcInputs();
  renderCalc();
}

function autoMount() {
  const matrixEl = document.getElementById("oec-matrix");
  const calcEl   = document.getElementById("oec-calc");
  if (matrixEl) mountMatrix(matrixEl);
  if (calcEl)   mountCalculator(calcEl);
  /* Fire-and-forget repo fetch; rerenders both components on success. */
  loadFromRepo(() => {
    /* Re-populate currency dropdown in case fx_rates.json widened set. */
    if (CALC_ROOT) populateCurrencyDropdown();
    renderMatrix();
    renderCalc();
  });
}

window.pfolioOEC = {
  autoMount,
  mountMatrix,
  mountCalculator,
  /* expose engine helpers for advanced callers */
  bestGuess,
  commissionForLeg,
  regFeesForLeg,
};

})();
