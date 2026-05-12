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
