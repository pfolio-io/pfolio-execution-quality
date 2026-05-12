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
