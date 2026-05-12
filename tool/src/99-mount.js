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
