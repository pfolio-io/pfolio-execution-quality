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
