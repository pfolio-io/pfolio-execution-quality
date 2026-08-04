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
    assert harness_data.is_declared("EU_STK_LSE")
    assert harness_data.measurement_state("EU_STK_LSE", mode="live") == "unmeasured"
    assert harness_data.measurement_state("NOT_A_CLASS") == "undeclared"


def test_a_measured_class_reports_measured():
    if not (REPO / "order-execution" / "quality" / "results" / "trials_live.parquet").exists():
        pytest.skip("no live trial store")
    assert harness_data.measurement_state("US_STK", mode="live") == "measured"


def test_an_unmeasured_european_total_is_flagged_incomplete():
    """The defect, stated as a test.

    Before this change set a European round-trip returned 61.43 bps with
    commission, PTM levy and stamp duty present and **execution counted as
    zero**, and nothing in the object said so. Under S1-33 an assumed spread is
    inadmissible, and a zero arriving by omission is an assumed spread.
    """
    out = cost_model.compute_cost(cost_model.CostInput(
        symbol="X", asset_class="EU_STK_LSE", side="BOTH", qty=1000, price=7,
    ))
    assert not out.is_complete
    assert out.unmeasured_components, "no component was flagged"
    assert all(l.bps_of_notional == 0.0 for l in out.lines if l.unmeasured)
    assert "INCOMPLETE" in out.render()
    assert "PARTIAL TOTAL" in out.render()


def test_a_measured_total_is_not_flagged_and_its_arithmetic_is_untouched():
    """The flag must not change any number. A measured zero and an unmeasured
    zero contribute the same 0; only one of them is a value."""
    out = cost_model.compute_cost(cost_model.CostInput(
        symbol="AAPL", asset_class="US_STK", side="BOTH", qty=100, price=200,
    ))
    assert out.is_complete
    assert "TOTAL" in out.render() and "PARTIAL TOTAL" not in out.render()
    assert out.total_bps == pytest.approx(0.6363, abs=1e-3)


def test_a_measured_zero_and_an_unmeasured_zero_are_distinguishable():
    """They were not, before. Same number, same column, same total — the only
    difference was a `source` string, and no total reads a string."""
    eu = cost_model.compute_cost(cost_model.CostInput(
        symbol="X", asset_class="EU_STK_LSE", side="BUY", qty=1000, price=7))
    us = cost_model.compute_cost(cost_model.CostInput(
        symbol="AAPL", asset_class="US_STK", side="BUY", qty=100, price=200))
    eu_slip = [l for l in eu.lines if l.label.startswith("slippage")][0]
    us_slip = [l for l in us.lines if l.label.startswith("slippage")][0]
    assert eu_slip.bps_of_notional == us_slip.bps_of_notional == 0.0
    assert eu_slip.unmeasured and not us_slip.unmeasured
