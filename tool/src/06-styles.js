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
  background: var(--oec-bg);
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
