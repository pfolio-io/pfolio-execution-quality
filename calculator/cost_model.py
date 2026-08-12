"""
Cost-model core: turn an instrument + size + side into a bps breakdown.

This module is deliberately UI-agnostic—it knows nothing about HTML or
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
    `side="BOTH"` means a round-trip—both legs are computed and summed.
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
    # ⚑ REQUIRED, and deliberately has no default. It defaulted to "LMT_MID"
    # until 2026-08-12, which silently priced every caller against the order
    # type that mostly does not execute: LMT_MID fills 12% of attempts on
    # EU_STK_SIX and 62% on EU_STK_LSE, and a mid-limit only fills WHEN IT GETS
    # THE MID, so its measured slippage is ~0 by construction. All three
    # European buckets therefore returned 0.00 bps of slippage from a real
    # measurement — reproducing, from good data, exactly the reading that the
    # European harness was built to eliminate.
    #
    # The shipped policy reaches MKT_RAW, not LMT_MID. Making the caller name
    # the strategy is the smallest change that stops the wrong one being chosen
    # by nobody. A default here is not a convenience; it is an unattributed
    # assumption about execution, and S1-33 does not allow those.
    strategy: str
    multiplier: float = 1.0
    base_currency: str = "USD"
    jurisdiction: Optional[str] = None
    holding_days: int = 0
    contract_currency: Optional[str] = None  # defaults to base_currency
    #: Is the executing broker a Swiss securities dealer? Swiss stamp duty
    #: (Umsatzabgabe) is levied only when at least one party to the trade is
    #: one — `tax_rules.json` has recorded that as `broker_swiss_only: true`
    #: since it was written, and `_transaction_tax` never read it, so the duty
    #: was charged unconditionally.
    #:
    #: Defaults to False because that is the answer for this account and the
    #: common one for our users: Marcel confirmed 2026-08-12 that the account
    #: trades through IB UK, which is not a Swiss securities dealer (decision
    #: record E-9, discharged). A caller who IS with a Swiss broker sets it.
    broker_is_swiss: bool = False


@dataclass
class CostLine:
    """One row of the cost breakdown."""
    label: str
    value_base_ccy: float
    bps_of_notional: float
    source: str
    side: str = ""  # which leg this charge applies to (entry, exit, both)
    note: str = ""
    #: True when this line is a PLACEHOLDER: the component is real, we have no
    #: measurement of it, and the 0.00 is an absence rather than a value.
    #:
    #: ⚑ Before this flag existed the two were indistinguishable in the output.
    #: `US_STK` with `LMT_MID` prints `0.00` because the measured median is
    #: ~0; `EU_STK_LSE` printed `0.00` because nothing has ever traded on the
    #: LSE through this harness. Same number, same column, same TOTAL — and only
    #: the `source` string, which no total reads, told them apart.
    unmeasured: bool = False


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

    @property
    def unmeasured_components(self) -> list[str]:
        """Labels of the lines that are placeholders, not measurements."""
        return [l.label for l in self.lines if l.unmeasured]

    @property
    def is_complete(self) -> bool:
        """Is every component in this total actually measured?

        ⚑ **The total's arithmetic is deliberately unchanged** — a placeholder
        contributes 0 exactly as it did before. Changing the number would be a
        silent semantic change of its own, and the defect was never the
        arithmetic: it was that an incomplete total presented as a complete one.
        This is the flag that says so; the caller decides what to do about it.
        """
        return not any(l.unmeasured for l in self.lines)

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
        label = "TOTAL" if self.is_complete else "PARTIAL TOTAL"
        rows.append(
            f"{label.ljust(max_label)}  "
            f"{self.total_value_base_ccy:>12.4f}  {self.total_bps:>8.2f}  "
            f"({self.input.base_currency} on {self.notional_base_ccy:,.2f} notional)"
        )
        # `CostLine.note` was rendered by nothing — not this table, not
        # tool/src, not the wireframe — so every caveat written into one since
        # the field existed has been invisible, including the price-improvement
        # cap note. A caveat nobody can read is the same as no caveat, and these
        # are exactly the lines where the number alone misleads.
        noted = [l for l in self.lines if l.note]
        if noted:
            rows.append("")
            rows.append("notes:")
            for l in noted:
                rows.append(f"    · {l.label}: {l.note}")
        if not self.is_complete:
            rows.append("")
            rows.append(
                "⚑ INCOMPLETE — the following components are UNMEASURED and enter "
                "the total as zero:"
            )
            for label in self.unmeasured_components:
                rows.append(f"    · {label}")
            rows.append(
                "  This total is a floor, not a cost. Run the harness on this asset "
                "class before quoting it."
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

    # ⚑ The condition the rule has always carried and nothing ever read.
    # `tax_rules.json` records `broker_swiss_only: true` on CH, because Swiss
    # stamp duty is levied only when a party to the trade is a Swiss securities
    # dealer. `_transaction_tax` never looked at the flag, so the duty was
    # charged to everyone — 15 bps per leg, BOTH legs, which was 30 of the
    # 42 bps this model returned for a Swiss round-trip. Roughly 71% of the
    # quoted cost was a tax that does not apply to us.
    #
    # While the answer was unknown the over-charge was at least conservative.
    # It stopped being conservative on 2026-08-12, when Marcel confirmed the
    # account trades through IB UK (E-9): from that point the model was simply
    # wrong, in the expensive direction, on the venue whose measurement had
    # just cost the most to obtain.
    if rule_set.get("broker_swiss_only") and not inp.broker_is_swiss:
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



def _slippage_cost(
        inp: CostInput, tables: CostTables, *, harness_mode: str = "paper",
) -> list[CostLine]:
    """Realized execution cost per leg, measured as `slip_vs_mid_t0_bps`.

    Returns one line per leg. The same policy strategy applies to entry
    and (for `side=BOTH`) exit — no auto-flatten asymmetry; the harness's
    auto-flatten exit is a test-fixture choice, not what a user would do.

    `slip_vs_mid_t0_bps` already captures the full execution cost vs the
    mid at submit: a plain MKT fill at the touch shows up as a positive
    bps cost equal to the half-spread crossed; a LMT_MID fill at the mid
    shows up as ≈0. A separate "spread cost" line would double-count.

    Negative measured slippage (price improvement) is capped at zero in
    the breakdown so the total isn't a promise of guaranteed price
    improvement at low sample sizes. The `note` field records the raw
    measurement for transparency; the raw matrix view shows the unclamped
    median for those who want to see it.

    On `mode=paper`, IB's sim fills LMT/MIDPRICE at the mid deterministically
    and MKT at the touch — paper median understates real LMT slippage and
    overstates MKT cleanliness. The source string flags paper-mode
    explicitly; prefer `mode=live` whenever live data is available.
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
            state = harness_data.measurement_state(inp.asset_class, mode=harness_mode)
            reason = {
                "undeclared": "asset class is not in asset_class_buckets.json",
                "unmeasured": "declared, but the harness has never traded it",
            }.get(state, "no rows for this class × strategy")
            return CostLine(
                label=f"slippage [{leg_label}, {strategy}]",
                value_base_ccy=0.0,
                bps_of_notional=0.0,
                source=f"UNMEASURED — {reason} "
                       f"({inp.asset_class}×{strategy}, harness {harness_mode})",
                side=leg_label,
                note="placeholder—needs live or more paper data",
                # The flag, not the note, is what the total reads. A note is a
                # string nobody sums.
                unmeasured=True,
            )
        cov = harness_data.coverage_by_strategy(
            inp.asset_class, strategy, mode=harness_mode,
        )
        capped_bps = max(0.0, slip_bps)
        cap_note = (
            f"measured {slip_bps:.2f} bps (price improvement); "
            f"capped at 0 in total"
        ) if slip_bps < 0 else ""
        paper_note = (
            "paper sim is not actionable for LMT/MIDPRICE—switch to mode=live"
        ) if harness_mode == "paper" else ""
        # ⚑ A slippage median is CONDITIONAL ON HAVING FILLED, and for the
        # limit-style strategies that condition is often false. `LMT_MID` is the
        # default strategy and fills 12% of attempts on EU_STK_SIX, 62% on
        # EU_STK_LSE and 100% on EU_STK_XETRA — yet all three price at 0.00 bps,
        # because a mid-limit only fills WHEN IT GETS THE MID, so its measured
        # slippage is ~0 by construction and the cap floors the rest.
        #
        # That is the failure this whole line item exists to prevent, in a new
        # form: before the European harness ran, these buckets published 0.00 bps
        # because nothing had ever traded them. They now publish 0.00 bps from a
        # real measurement of the one strategy that mostly does not execute. The
        # model has no notion of fill probability and cannot price the unfilled
        # attempts, so the honest move is to make the denominator visible next to
        # the number rather than to invent a haircut.
        fa = harness_data.fill_attempts_by_strategy(
            inp.asset_class, strategy, mode=harness_mode,
        )
        attempts, filled = fa["attempts"], fa["filled"]
        rate_note = (
            f"filled {filled} of {attempts} attempts "
            f"({filled / attempts:.0%}) — this figure is conditional on "
            f"filling; the unfilled attempts are not priced here"
        ) if attempts and filled < attempts else ""
        note = "; ".join(n for n in (cap_note, rate_note, paper_note) if n)
        n_txt = (f"n={cov['with_slip']} of {attempts} attempts"
                 if attempts else f"n={cov['with_slip']}")
        return CostLine(
            label=f"slippage [{leg_label}, {strategy}]",
            value_base_ccy=notional_base * capped_bps / 1e4,
            bps_of_notional=capped_bps,
            source=f"harness({harness_mode}).median slip_vs_mid_t0_bps "
                   f"[{n_txt}]",
            side=leg_label,
            note=note,
        )

    lines: list[CostLine] = []
    # Same policy strategy on both legs of a round-trip — a real user
    # executes both legs the same way; auto-flatten MKT exit is a harness
    # test-fixture choice, not a user pattern.
    entry_line = _line(inp.strategy, "entry" if inp.side == "BOTH" else inp.side)
    if entry_line is not None:
        lines.append(entry_line)
    if inp.side == "BOTH":
        exit_line = _line(inp.strategy, "exit")
        if exit_line is not None:
            lines.append(exit_line)
    return lines


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
