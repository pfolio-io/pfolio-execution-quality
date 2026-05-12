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
