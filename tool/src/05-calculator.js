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
        `promise of negative cost — the matrix above shows the raw measurement.`;
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
