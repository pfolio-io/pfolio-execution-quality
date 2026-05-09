"""
Cost-model core: turn an instrument + size + side into a bps breakdown.

This module is deliberately UI-agnostic — it knows nothing about HTML or
any rendering layer. It loads static lookup tables from
`order-execution/quality/cost_tables/` and (eventually) empirical spread
data from the harness's parquet store, and returns a structured
`CostBreakdown`.

V0 scope (this iteration):
- Commission via `broker_ibkr.json`
- Regulatory fees via `reg_fees.json`
- Transaction taxes via `tax_rules.json`
- FX conversion via `fx_rates.json`
- Spread cost: static stub keyed by asset_class (will switch to harness
  median once we expose a programmatic accessor)

Out of scope until live data lands:
- Slippage by strategy
- Impact model `impact_bps(size)`
- Carry / financing for shorts and CFDs
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from calculator import harness_data


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CostInput:
    """A single trade or round-trip request.

    `qty` and `price` are unsigned magnitudes; `side` carries direction.
    `side="BOTH"` means a round-trip — both legs are computed and summed.
    `asset_class` keys the broker/reg/tax tables (e.g. "US_STK",
    "FUT_CME", "FX_IDEALPRO"). `jurisdiction` is the ISO-2 code that
    drives transaction-tax lookup; auto-derived from `asset_class` when
    omitted.
    """
    symbol: str
    asset_class: str
    side: str  # BUY, SELL, BOTH
    qty: float
    price: float
    multiplier: float = 1.0
    strategy: str = "LMT_MID"
    base_currency: str = "USD"
    jurisdiction: Optional[str] = None
    holding_days: int = 0
    contract_currency: Optional[str] = None  # defaults to base_currency


@dataclass
class CostLine:
    """One row of the cost breakdown."""
    label: str
    value_base_ccy: float
    bps_of_notional: float
    source: str
    side: str = ""  # which leg this charge applies to (entry, exit, both)
    note: str = ""


@dataclass
class CostBreakdown:
    input: CostInput
    notional_base_ccy: float
    lines: list[CostLine] = field(default_factory=list)

    @property
    def total_value_base_ccy(self) -> float:
        return sum(l.value_base_ccy for l in self.lines)

    @property
    def total_bps(self) -> float:
        if self.notional_base_ccy <= 0:
            return float("nan")
        return self.total_value_base_ccy / self.notional_base_ccy * 1e4

    def render(self) -> str:
        """Human-readable breakdown table."""
        if not self.lines:
            return "(no cost lines)"
        max_label = max(len(l.label) for l in self.lines)
        rows = [
            f"{'component'.ljust(max_label)}  {'value':>12}  {'bps':>8}  source"
        ]
        rows.append("-" * (max_label + 2 + 12 + 2 + 8 + 2 + 6 + 30))
        for l in self.lines:
            rows.append(
                f"{l.label.ljust(max_label)}  "
                f"{l.value_base_ccy:>12.4f}  {l.bps_of_notional:>8.2f}  {l.source}"
            )
        rows.append("-" * (max_label + 2 + 12 + 2 + 8 + 2 + 6 + 30))
        rows.append(
            f"{'TOTAL'.ljust(max_label)}  "
            f"{self.total_value_base_ccy:>12.4f}  {self.total_bps:>8.2f}  "
            f"({self.input.base_currency} on {self.notional_base_ccy:,.2f} notional)"
        )
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Table loading
# ---------------------------------------------------------------------------
_DEFAULT_TABLES_DIR = (
        Path(__file__).resolve().parent.parent
        / "order-execution" / "quality" / "cost_tables"
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


@dataclass
class CostTables:
    broker: dict
    reg_fees: dict
    tax_rules: dict
    fx_rates: dict[str, float]

    @classmethod
    def load(cls, tables_dir: Optional[Path] = None) -> "CostTables":
        d = tables_dir or _DEFAULT_TABLES_DIR
        broker = _read_json(d / "broker_ibkr.json")
        reg = _read_json(d / "reg_fees.json")
        tax = _read_json(d / "tax_rules.json")
        fx_raw = _read_json(d / "fx_rates.json")
        fx = {
            k: float(v) for k, v in fx_raw.items()
            if not k.startswith("_") and isinstance(v, (int, float))
        }
        return cls(broker=broker, reg_fees=reg, tax_rules=tax, fx_rates=fx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_base(amount: float, ccy: str, base: str, fx: dict[str, float]) -> float:
    """Convert amount in `ccy` to `base` via the USD-anchor FX table."""
    if ccy == base:
        return amount
    if ccy not in fx or base not in fx:
        raise ValueError(f"missing FX rate for {ccy} or {base}: have {sorted(fx)}")
    # USD-anchor: rate[ccy] = USD price of 1 unit of ccy.
    return amount * fx[ccy] / fx[base]


def _resolve_inherited(node: dict, table: dict) -> dict:
    """Resolve `_inherits` recursively. Returns a flat merged dict."""
    if "_inherits" not in node:
        return dict(node)
    parent = _resolve_inherited(table[node["_inherits"]], table)
    parent.update({k: v for k, v in node.items() if k != "_inherits"})
    return parent


_JURISDICTION_BY_ASSET_CLASS = {
    "US_STK": "US",
    "US_SMALL_CAP_STK": "US",
    "US_ETF": "US",
    "EU_STK_LSE": "GB",
    "EU_STK_XETRA": "DE",
    "EU_STK_SIX": "CH",
    "FUT_CME": "US",
    "FUT_CFE": "US",
    "FUT_NYBOT": "US",
    "FUT_EUREX": "DE",
    "FX_IDEALPRO": None,
    "CFD_FX": None,
    "CFD_INDEX": None,
}


# ---------------------------------------------------------------------------
# Component computers
# ---------------------------------------------------------------------------
def _commission(inp: CostInput, tables: CostTables) -> Optional[CostLine]:
    rule = tables.broker.get(inp.asset_class)
    if not rule or not isinstance(rule, dict):
        return None
    ccy = rule.get("currency", "USD")
    notional_native = inp.qty * inp.price * inp.multiplier
    raw = 0.0
    if "per_share" in rule:
        raw = inp.qty * float(rule["per_share"])
    elif "per_value_bps" in rule:
        raw = notional_native * float(rule["per_value_bps"]) / 1e4
    elif "per_contract" in rule:
        raw = inp.qty * float(rule["per_contract"])
        raw += inp.qty * float(rule.get("exchange_fee_per_contract", 0))
    else:
        return None
    if "min_per_order" in rule:
        raw = max(raw, float(rule["min_per_order"]))
    if "max_pct_of_notional" in rule:
        raw = min(raw, notional_native * float(rule["max_pct_of_notional"]))
    if "max_per_order" in rule:
        raw = min(raw, float(rule["max_per_order"]))
    base = _to_base(raw, ccy, inp.base_currency, tables.fx_rates)
    notional_base = _to_base(notional_native, inp.contract_currency or inp.base_currency,
                             inp.base_currency, tables.fx_rates)
    bps = base / notional_base * 1e4 if notional_base > 0 else float("nan")
    return CostLine(
        label="commission",
        value_base_ccy=base,
        bps_of_notional=bps,
        source=f"broker_ibkr.{inp.asset_class}",
    )


def _reg_fees(inp: CostInput, tables: CostTables) -> list[CostLine]:
    raw_node = tables.reg_fees.get(inp.asset_class)
    if not raw_node:
        return []
    rule = _resolve_inherited(raw_node, tables.reg_fees)
    ccy = rule.get("currency", "USD")
    notional_native = inp.qty * inp.price * inp.multiplier
    notional_base = _to_base(notional_native, inp.contract_currency or inp.base_currency,
                             inp.base_currency, tables.fx_rates)

    sides = ["BUY", "SELL"] if inp.side == "BOTH" else [inp.side]
    lines: list[CostLine] = []
    for s in sides:
        bucket_key = "buys_only" if s == "BUY" else "sells_only"
        for bucket in (rule.get(bucket_key, {}), rule.get("both_sides", {})):
            for k, v in bucket.items():
                if k.endswith("_doc"):
                    continue
                amount_native = _apply_fee_rule(k, v, inp.qty, notional_native)
                if amount_native <= 0:
                    continue
                base = _to_base(amount_native, ccy, inp.base_currency, tables.fx_rates)
                bps = base / notional_base * 1e4 if notional_base > 0 else float("nan")
                lines.append(CostLine(
                    label=f"reg:{k}",
                    value_base_ccy=base,
                    bps_of_notional=bps,
                    source=f"reg_fees.{inp.asset_class}",
                    side=s,
                ))
    return lines


def _apply_fee_rule(name: str, value, qty: float, notional_native: float) -> float:
    """Map one (name, numeric_value) entry to a native-currency amount.
    Recognised conventions:
      *_per_million    → numeric × notional_native / 1e6
      *_per_share      → numeric × qty
      *_per_trade      → numeric (flat)
      *_per_contract   → numeric × qty
      *_max_*          → ignored here (caps applied elsewhere)
    """
    if not isinstance(value, (int, float)):
        return 0.0
    if name.endswith("_max_per_trade") or name.startswith("_") or name.endswith("_doc"):
        return 0.0
    if name.endswith("_per_million"):
        return float(value) * notional_native / 1e6
    if name.endswith("_per_share"):
        return float(value) * qty
    if name.endswith("_per_trade"):
        return float(value)
    if name.endswith("_per_contract"):
        return float(value) * qty
    return 0.0


def _transaction_tax(inp: CostInput, tables: CostTables) -> list[CostLine]:
    juris = inp.jurisdiction or _JURISDICTION_BY_ASSET_CLASS.get(inp.asset_class)
    if juris is None:
        return []
    rule_set = tables.tax_rules.get(juris)
    if not rule_set or not rule_set.get("stk_tax"):
        return []
    rule = rule_set["stk_tax"]
    ccy = rule.get("currency", inp.base_currency)
    notional_native = inp.qty * inp.price * inp.multiplier
    notional_base = _to_base(notional_native, inp.contract_currency or inp.base_currency,
                             inp.base_currency, tables.fx_rates)

    if "rate_pct" in rule:
        # Single-rate jurisdictions (UK, FR, IT, BE, ...).
        rate = float(rule["rate_pct"]) / 100.0
        rule_side = rule.get("side", "BUY")
        sides_to_charge = (
            ["BUY", "SELL"] if rule_side == "BOTH"
            else [rule_side] if rule_side in ("BUY", "SELL")
            else []
        )
    elif "rate_pct_domestic" in rule:
        # Switzerland-style: 0.075% domestic, 0.15% foreign. Default to
        # foreign rate (more conservative); user overrides via input field
        # in V2 if needed.
        rate = float(rule.get("rate_pct_foreign", rule["rate_pct_domestic"])) / 100.0
        sides_to_charge = ["BUY", "SELL"]
    else:
        return []

    requested_sides = ["BUY", "SELL"] if inp.side == "BOTH" else [inp.side]
    lines: list[CostLine] = []
    for s in requested_sides:
        if s not in sides_to_charge:
            continue
        if "min_notional" in rule and notional_native < float(rule["min_notional"]):
            continue
        amount_native = notional_native * rate
        base = _to_base(amount_native, ccy, inp.base_currency, tables.fx_rates)
        bps = base / notional_base * 1e4 if notional_base > 0 else float("nan")
        lines.append(CostLine(
            label=f"tax:{juris}",
            value_base_ccy=base,
            bps_of_notional=bps,
            source=f"tax_rules.{juris}",
            side=s,
        ))
    return lines


# Static spread fallback (bps, half-spread) used when the harness has no
# data for an asset class (e.g. CFE/NYBOT without market-data permission)
# or when the harness store is missing entirely. The harness accessor
# returns more accurate numbers whenever spread_t0_bps rows exist.
_DEFAULT_HALF_SPREAD_BPS = {
    "US_STK": 1.0, "US_SMALL_CAP_STK": 20.0, "US_ETF": 0.2,
    "EU_STK_XETRA": 1.5, "EU_STK_LSE": 1.5, "EU_STK_SIX": 3.0,
    "FUT_CME": 0.2, "FUT_CFE": 12.0, "FUT_NYBOT": 0.5, "FUT_EUREX": 1.0,
    "FX_IDEALPRO": 0.1, "CFD_FX": 0.1, "CFD_INDEX": 1.0,
}


def _slippage_cost(
        inp: CostInput, tables: CostTables, *, harness_mode: str = "paper",
) -> list[CostLine]:
    """Slippage on top of the half-spread, per leg, by entry strategy.

    Returns one line for the entry leg's slippage (using `inp.strategy`)
    and — when `side=BOTH` — a second line for the exit leg's MKT_RAW
    slippage (auto-flatten always uses MKT_RAW). Lines come from the
    harness median.

    On `mode=paper`, IB's sim fills LMT/MIDPRICE at the mid and MKT at
    the touch deterministically — the paper median understates real LMT
    slippage and overstates MKT_RAW cleanliness. The line is still
    emitted so the breakdown is structurally complete and so the source
    string clearly flags the paper-mode caveat; switch to `mode=live`
    once Phase 6.5 data lands.
    """
    notional_native = inp.qty * inp.price * inp.multiplier
    notional_base = _to_base(
        notional_native, inp.contract_currency or inp.base_currency,
        inp.base_currency, tables.fx_rates,
    )

    def _line(strategy: str, leg_label: str) -> Optional[CostLine]:
        slip_bps = harness_data.median_slip_bps_by_strategy(
            inp.asset_class, strategy, mode=harness_mode,
        )
        if slip_bps is None:
            return CostLine(
                label=f"slippage [{leg_label}, {strategy}]",
                value_base_ccy=0.0,
                bps_of_notional=0.0,
                source=f"no harness({harness_mode}) slip data for "
                       f"{inp.asset_class}×{strategy}",
                side=leg_label,
                note="placeholder — needs live or more paper data",
            )
        cov = harness_data.coverage_by_strategy(
            inp.asset_class, strategy, mode=harness_mode,
        )
        return CostLine(
            label=f"slippage [{leg_label}, {strategy}]",
            value_base_ccy=notional_base * slip_bps / 1e4,
            bps_of_notional=slip_bps,
            source=f"harness({harness_mode}).median slip_vs_mid_t0_bps "
                   f"[n={cov['with_slip']}]",
            side=leg_label,
            note=("paper sim is not actionable for LMT/MIDPRICE — "
                  "switch to mode=live when available")
            if harness_mode == "paper" else "",
        )

    lines: list[CostLine] = []
    entry_line = _line(inp.strategy, "entry" if inp.side == "BOTH" else inp.side)
    if entry_line is not None:
        lines.append(entry_line)
    if inp.side == "BOTH":
        exit_line = _line("MKT_RAW", "exit")
        if exit_line is not None:
            lines.append(exit_line)
    return lines


def _spread_cost(
        inp: CostInput, tables: CostTables, *, harness_mode: str = "paper",
) -> Optional[CostLine]:
    """Spread cost line: prefer harness median (empirical), fall back to
    the static table. `harness_mode` selects paper vs live store."""
    half = harness_data.median_half_spread_bps(inp.asset_class, mode=harness_mode)
    if half is not None:
        cov = harness_data.coverage(inp.asset_class, mode=harness_mode)
        source = (
            f"harness({harness_mode}).median spread_t0_bps/2 "
            f"[n={cov['with_spread']} rows]"
        )
        note = ""
    else:
        half = _DEFAULT_HALF_SPREAD_BPS.get(inp.asset_class)
        if half is None:
            return None
        source = f"static:_DEFAULT_HALF_SPREAD_BPS[{inp.asset_class}]"
        note = "no harness data for this asset class — static fallback"

    notional_native = inp.qty * inp.price * inp.multiplier
    notional_base = _to_base(
        notional_native, inp.contract_currency or inp.base_currency,
        inp.base_currency, tables.fx_rates,
    )
    legs = 2 if inp.side == "BOTH" else 1
    bps = half * legs
    return CostLine(
        label="spread (half × legs)",
        value_base_ccy=notional_base * bps / 1e4,
        bps_of_notional=bps,
        source=source,
        note=note,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def compute_cost(
        inp: CostInput,
        tables: Optional[CostTables] = None,
        *,
        harness_mode: str = "paper",
) -> CostBreakdown:
    """Compute a cost breakdown for one trade or round-trip.

    Loads tables on first call if not passed. Returns a `CostBreakdown`
    with one `CostLine` per cost component plus convenience totals.
    `harness_mode` selects which trials store the spread accessor reads
    (`paper` or `live`).
    """
    if tables is None:
        tables = CostTables.load()
    if inp.contract_currency is None:
        inp.contract_currency = tables.broker.get(
            inp.asset_class, {}
        ).get("currency", inp.base_currency)
    if inp.side not in ("BUY", "SELL", "BOTH"):
        raise ValueError(f"side must be BUY/SELL/BOTH, got {inp.side!r}")

    notional_native = inp.qty * inp.price * inp.multiplier
    notional_base = _to_base(
        notional_native, inp.contract_currency,
        inp.base_currency, tables.fx_rates,
    )
    breakdown = CostBreakdown(input=inp, notional_base_ccy=notional_base)

    spread = _spread_cost(inp, tables, harness_mode=harness_mode)
    if spread:
        breakdown.lines.append(spread)
    breakdown.lines.extend(_slippage_cost(inp, tables, harness_mode=harness_mode))
    comm = _commission(inp, tables)
    if comm:
        # If side=BOTH (round-trip), commission is charged twice.
        if inp.side == "BOTH":
            comm.value_base_ccy *= 2
            comm.bps_of_notional *= 2
            comm.label = "commission (×2 round-trip)"
        breakdown.lines.append(comm)
    breakdown.lines.extend(_reg_fees(inp, tables))
    breakdown.lines.extend(_transaction_tax(inp, tables))

    return breakdown
