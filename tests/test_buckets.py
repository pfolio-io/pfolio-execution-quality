"""Tests for the asset-class bucket layer and the unmeasured-cost guard.

    python3 -m pytest tests -q          (from the repo root)

**Why this file is the first Python test in the repo.** The bucket map decides
which measured trials back a published number, and it had two properties that
were true by coincidence rather than by check: no two selectors overlapped, and
every declared class had data. Adding the three venue-partitioned EU classes
falsified the second and made the first fragile. Both are now asserted.

The tests use synthetic frames rather than the committed parquet stores, except
where the point IS the committed store — a test that depends on live trial data
fails for reasons that have nothing to do with the code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "order-execution"))

from calculator import cost_model, harness_data  # noqa: E402
from quality import buckets  # noqa: E402


def _rows(*specs) -> pd.DataFrame:
    """specs: (symbol, secType, exchange, currency[, expiry])"""
    return pd.DataFrame([
        {"symbol": s[0], "secType": s[1], "exchange": s[2], "currency": s[3],
         "expiry": (s[4] if len(s) > 4 else None)}
        for s in specs
    ])


# --------------------------------------------------------------------------- #
# The map itself
# --------------------------------------------------------------------------- #

def test_every_broker_rule_has_a_bucket_or_is_named_as_lacking_one():
    """A commission rule with no bucket is a class the calculator will price
    with an UNMEASURED execution term — which is the defect this change set
    exists to remove. The three EU venues had a rule since 2026-05-04 and no
    bucket until 2026-08-04."""
    import json
    broker = json.loads(
        (REPO / "order-execution" / "quality" / "cost_tables" / "broker_ibkr.json").read_text()
    )
    priced = {k for k, v in broker.items() if not k.startswith("_") and isinstance(v, dict)}
    declared = set(buckets.load_bucket_map())

    # Known and accepted: these have a commission rule and no harness instrument.
    # Each is here because it is a class we do not trade, not because it was
    # forgotten — CFD_INDEX and the micro/EUREX futures are not in the universe.
    accepted = {"CFD_INDEX", "FUT_CME_MICRO", "FUT_EUREX"}

    missing = priced - declared - accepted
    assert not missing, (
        f"priced but unbucketed: {sorted(missing)}. Either add a selector to "
        f"asset_class_buckets.json or add the key to `accepted` here with the "
        f"reason, so the omission is a decision rather than an oversight."
    )


def test_the_three_eu_venues_are_declared_and_venue_partitioned():
    m = buckets.load_bucket_map()
    for name in ("EU_STK_XETRA", "EU_STK_LSE", "EU_STK_SIX"):
        assert name in m, f"{name} missing from the bucket map"
        assert m[name].is_venue_partitioned, (
            f"{name} is one of three classes on three venues; a symbol-only "
            f"selector cannot tell them apart"
        )


def test_smart_is_not_an_accepted_venue_for_the_eu_classes():
    """A SMART-routed row records `exchange = SMART` and cannot say which venue
    it measured. Accepting it would make the bucket a claim about the router."""
    m = buckets.load_bucket_map()
    for name in ("EU_STK_XETRA", "EU_STK_LSE", "EU_STK_SIX"):
        assert "SMART" not in m[name].exchange_any


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def test_the_venues_do_not_capture_each_other():
    """The property a symbol-only selector could not have had."""
    df = _rows(
        ("EUNL", "STK", "IBIS", "EUR"),
        ("SWDA", "STK", "LSE", "USD"),
        ("IWRD", "STK", "EBS", "CHF"),
    )
    got = list(buckets.bucket_series(df))
    assert got == ["EU_STK_XETRA", "EU_STK_LSE", "EU_STK_SIX"], got


def test_the_same_ticker_on_two_venues_lands_in_two_buckets():
    """The concrete case a `SWDA/STK` pattern would have got wrong: one fund,
    two listings, two different commission schedules and two different taxes."""
    df = _rows(("SWDA", "STK", "LSE", "USD"), ("SWDA", "STK", "EBS", "CHF"))
    assert list(buckets.bucket_series(df)) == ["EU_STK_LSE", "EU_STK_SIX"]


def test_us_rows_are_unaffected_by_the_new_selectors():
    df = _rows(
        ("AAPL", "STK", "SMART", "USD"),
        ("SPY", "STK", "SMART", "USD"),
        ("PRIM", "STK", "SMART", "USD"),
        ("ES", "FUT", "CME", "USD", "20260618"),
        ("EUR", "CASH", "IDEALPRO", "USD"),
    )
    assert list(buckets.bucket_series(df)) == [
        "US_STK", "US_ETF", "US_SMALL_CAP_STK", "FUT_CME", "FX_IDEALPRO",
    ]


def test_no_selector_pair_collides_on_the_committed_trial_stores():
    """`bucket_series` resolves a collision by file order and says nothing, so
    the absence of collisions is checked rather than assumed."""
    results = REPO / "order-execution" / "quality" / "results"
    stores = [p for p in (results / "trials_paper.parquet",
                          results / "trials_live.parquet") if p.exists()]
    if not stores:
        pytest.skip("no committed trial store")
    for path in stores:
        df = pd.read_parquet(path)
        collisions = buckets.check_ambiguity(df)
        assert not collisions, f"{path.name}: {collisions}"


def test_the_shared_instrument_key_is_the_one_both_consumers_use():
    """`analyze.py` and `harness_data.py` each carried their own copy. A drift
    between them would not raise — it would silently empty a bucket."""
    sys.path.insert(0, str(REPO / "order-execution"))
    from quality import analyze

    assert analyze._instrument_key is buckets.instrument_key
    assert harness_data._instrument_key is buckets.instrument_key


# --------------------------------------------------------------------------- #
# The unmeasured-cost guard — the reason a missing bucket was dangerous
# --------------------------------------------------------------------------- #

def test_a_declared_but_untraded_class_reports_unmeasured_not_missing():
    """⚑ `EU_STK_LSE` was the standing example twice over — until it started
    measuring in paper on 2026-08-11 morning, and in **live** the same afternoon.
    Both times this test failed, and both times the failure was the point. It now
    picks a class that is genuinely unmeasured rather than naming one, so it keeps
    asserting the behaviour as the European buckets fill up."""
    unmeasured = _an_unmeasured_class(mode="live")
    assert harness_data.is_declared(unmeasured)
    assert harness_data.measurement_state(unmeasured, mode="live") == "unmeasured"
    assert harness_data.measurement_state("NOT_A_CLASS") == "undeclared"


def test_a_measured_class_reports_measured():
    if not (REPO / "order-execution" / "quality" / "results" / "trials_live.parquet").exists():
        pytest.skip("no live trial store")
    assert harness_data.measurement_state("US_STK", mode="live") == "measured"


def _an_unmeasured_class(mode: str = "paper") -> str:
    """A declared class the store has no usable measurement for.

    ⚑ Was hardcoded to `EU_STK_LSE` until 2026-08-11, when that bucket started
    carrying fills — which is the outcome this whole exercise was for, and it
    broke the test. Selecting one instead of naming one keeps the assertion
    about the *behaviour* (an unmeasured class must be flagged, not silently
    zeroed) rather than about which class happens to be empty this week."""
    import pandas as pd
    store = (REPO / "order-execution" / "quality" / "results"
             / f"trials_{mode}.parquet")
    if not store.exists():
        pytest.skip(f"no {mode} trial store")
    df = pd.read_parquet(store)
    fills = df[(df["status"] == "FILLED") & df["slip_vs_mid_t0_bps"].notna()]
    absent = buckets.unmeasured_classes(fills)
    if not absent:
        pytest.skip("every declared class is measured — nothing left to assert on")
    return sorted(absent)[0]


def test_an_unmeasured_european_total_is_flagged_incomplete():
    """The defect, stated as a test.

    Before this change set a European round-trip returned 61.43 bps with
    commission, PTM levy and stamp duty present and **execution counted as
    zero**, and nothing in the object said so. Under S1-33 an assumed spread is
    inadmissible, and a zero arriving by omission is an assumed spread.
    """
    out = cost_model.compute_cost(cost_model.CostInput(
        symbol="X", asset_class=_an_unmeasured_class(), side="BOTH",
        qty=1000, price=7, strategy="LMT_MID",
    ))
    assert not out.is_complete
    assert out.unmeasured_components, "no component was flagged"
    assert all(line.bps_of_notional == 0.0 for line in out.lines if line.unmeasured)
    assert "INCOMPLETE" in out.render()
    assert "PARTIAL TOTAL" in out.render()


def test_a_measured_total_is_not_flagged_and_its_arithmetic_is_untouched():
    """The flag must not change any number. A measured zero and an unmeasured
    zero contribute the same 0; only one of them is a value."""
    out = cost_model.compute_cost(cost_model.CostInput(
        symbol="AAPL", asset_class="US_STK", side="BOTH", qty=100, price=200,
        strategy="LMT_MID",
    ))
    assert out.is_complete
    assert "TOTAL" in out.render() and "PARTIAL TOTAL" not in out.render()
    assert out.total_bps == pytest.approx(0.6363, abs=1e-3)


def test_a_measured_zero_and_an_unmeasured_zero_are_distinguishable():
    """They were not, before. Same number, same column, same total — the only
    difference was a `source` string, and no total reads a string."""
    eu = cost_model.compute_cost(cost_model.CostInput(
        symbol="X", asset_class=_an_unmeasured_class(), side="BUY",
        qty=1000, price=7, strategy="LMT_MID"))
    us = cost_model.compute_cost(cost_model.CostInput(
        symbol="AAPL", asset_class="US_STK", side="BUY", qty=100, price=200,
        strategy="LMT_MID"))
    eu_slip = [line for line in eu.lines if line.label.startswith("slippage")][0]
    us_slip = [line for line in us.lines if line.label.startswith("slippage")][0]
    assert eu_slip.bps_of_notional == us_slip.bps_of_notional == 0.0
    assert eu_slip.unmeasured and not us_slip.unmeasured


def test_strategy_has_no_default_so_nobody_prices_against_the_wrong_order_type():
    """`CostInput.strategy` defaulted to LMT_MID until 2026-08-12, and that
    default priced every caller against the order type that mostly does not
    execute — 12% of attempts on EU_STK_SIX, 62% on EU_STK_LSE. A mid-limit
    only fills when it gets the mid, so its slippage is ~0 by construction, and
    all three European buckets returned 0.00 bps from a *real* measurement:
    the exact reading the European harness was built to eliminate, reproduced
    from good data.

    The regression this guards is a default quietly reappearing because it is
    convenient at a call site. Under S1-33 an unattributed execution assumption
    is inadmissible, and a default strategy is one.
    """
    import dataclasses
    field = {f.name: f for f in dataclasses.fields(cost_model.CostInput)}["strategy"]
    assert field.default is dataclasses.MISSING, (
        "CostInput.strategy has a default again; callers will be priced "
        "against an order type nobody chose"
    )
    assert field.default_factory is dataclasses.MISSING

    with pytest.raises(TypeError):
        cost_model.CostInput(
            symbol="AAPL", asset_class="US_STK", side="BOTH", qty=1, price=1,
        )


def test_swiss_stamp_duty_is_charged_only_to_a_swiss_broker():
    """`tax_rules.json` has carried `broker_swiss_only: true` on CH since it was
    written, because the Umsatzabgabe is levied only when a party to the trade is
    a Swiss securities dealer. `_transaction_tax` never read the flag, so the
    duty was charged to everyone: 15 bps per leg, both legs, 30 of the 42 bps the
    model returned for a Swiss round-trip — about 71% of the quote.

    While the answer was unknown that was at least conservative. It stopped being
    conservative on 2026-08-12, when the account was confirmed to trade through
    IB UK (E-9): from then the model was simply wrong, in the expensive
    direction, on the venue whose measurement had cost the most to obtain.

    Guards both directions, because a flag that is read but always false is the
    same bug wearing a condition.
    """
    def total(swiss: bool) -> float:
        return cost_model.compute_cost(cost_model.CostInput(
            symbol="X", asset_class="EU_STK_SIX", side="BOTH", qty=30,
            price=173.0, strategy="MKT_RAW", broker_is_swiss=swiss,
        ), harness_mode="live").total_bps

    non_swiss, swiss = total(False), total(True)
    assert swiss > non_swiss, "broker_swiss_only is being ignored again"
    assert swiss - non_swiss == pytest.approx(30.0, abs=0.5), (
        "CH duty should be 15 bps on each of two legs"
    )
    assert cost_model.CostInput(
        symbol="X", asset_class="EU_STK_SIX", side="BOTH", qty=1, price=1,
        strategy="MKT_RAW",
    ).broker_is_swiss is False, "the safe default must be non-Swiss"


def test_eu_commission_minimums_are_the_measured_routing_weighted_charge():
    """`min_per_order` for the three EU buckets is no longer the published
    schedule minimum but the routing-weighted charge measured over live SMART
    fills. The largest correction is SIX: EBS, the primary, charges CHF 3.58
    against a schedule 1.50 on 8 of 8 fills with zero variance.

    Pinned because these are the only hand-written numbers in `broker_ibkr.json`
    that came from measurement rather than a published schedule, and a future
    refresh from the IBKR pricing page would quietly restore the schedule values
    and silently under-state the Swiss line. `_min_per_order_schedule` keeps the
    published figure beside each so the two are never confused.

    ⚑ Re-pinned 2026-09-08 (H-4 session 3, `batches/2026-09-08-W8-record.md`):
    137 fills over two sessions became 159 over three. XETRA reproduced within
    EUR 0.0008; SIX moved most and on the thinnest session (n = 6, where one EBS
    draw shifts it CHF 0.347). **If this test goes red, do not edit it to match
    the table — find the measured run that moved the table, or the refresh that
    should never have.**
    """
    import json
    rules = json.loads((
        Path(__file__).resolve().parents[1] / "order-execution" / "quality"
        / "cost_tables" / "broker_ibkr.json"
    ).read_text())

    expected = {"EU_STK_XETRA": (1.2635, 1.25, 73),
                "EU_STK_LSE": (1.0348, 1.0, 46),
                "EU_STK_SIX": (1.9168, 1.5, 40)}
    for bucket, (measured, schedule, n) in expected.items():
        rule = rules[bucket]
        assert rule["min_per_order"] == pytest.approx(measured), bucket
        assert rule["_min_per_order_schedule"] == pytest.approx(schedule), bucket
        assert rule["min_per_order"] >= rule["_min_per_order_schedule"], (
            f"{bucket}: measured charge below schedule minimum is not possible"
        )
        assert rule["_min_per_order_measured_n"] == n, (
            f"{bucket}: fill count moved without the value being re-pinned"
        )

    # per_value_bps on SIX stopped being a schedule read on 2026-09-08: it is
    # measured at 5.0 over 12 fills at 23 shares (sigma 7.1e-08), on Marcel's
    # explicit call. It is now exposed to exactly the failure above — a refresh
    # from the pricing page restoring 6.0 would over-state every Swiss order
    # above ~CHF 3,858 by 20%, where the rate binds over min_per_order.
    assert rules["EU_STK_SIX"]["per_value_bps"] == pytest.approx(5.0), (
        "EU_STK_SIX.per_value_bps is MEASURED, not the published 6.0"
    )
