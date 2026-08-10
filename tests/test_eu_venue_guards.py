"""The guards that stop a European cell spending money without measuring.

    python3 -m pytest tests -q          (from the repo root)

`tests/test_buckets.py` asserts that the bucket *map* can tell the three venues
apart. This file asserts that the *runner* refuses to trade when the contract it
resolved would not land where the run claims — and that the matrix export says
"never measured" out loud instead of by omission.

Every European failure mode these cover has the same shape: the order still
fills, the commission is still charged, and the measurement is still absent. That
is why the checks are pre-trade and why they fail in the safe direction — they
decline to trade. No IB connection, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "order-execution"))

from quality import analyze, buckets, runner  # noqa: E402


class _FakeContract:
    """Just the four fields the guard reads. Avoids constructing an ib_insync
    Contract, so the test says which fields the decision actually turns on."""

    def __init__(self, symbol, secType, exchange, currency):
        self.symbol, self.secType = symbol, secType
        self.exchange, self.currency = exchange, currency


# --------------------------------------------------------------------------- #
# The venue guard
# --------------------------------------------------------------------------- #

def test_every_eu_venue_names_a_bucket_that_the_map_declares():
    """The guard reads the map, so the two cannot drift — but only if the name
    it looks up exists at all."""
    declared = buckets.load_bucket_map()
    for symbol, venue in runner.EU_VENUES.items():
        assert venue["bucket"] in declared, (
            f"{symbol} points at bucket {venue['bucket']!r}, which "
            f"asset_class_buckets.json does not declare"
        )


def test_the_expected_venue_and_currency_resolve_to_the_expected_bucket():
    for symbol, venue in runner.EU_VENUES.items():
        got = runner.bucket_of(
            "ANYTICKER", "STK", venue["exchange"], venue["expect_currency"],
        )
        assert got == venue["bucket"], (
            f"{symbol}: {venue['exchange']}/{venue['expect_currency']} → {got}"
        )


def test_a_contract_that_resolved_onto_smart_is_refused():
    """The concrete hole `_qualify_contract`'s all-venues retry used to open: a
    SMART contract trades, records `exchange=SMART`, matches no EU selector, and
    is dropped from the matrix — after the commission is paid."""
    refusal = runner.venue_guard(
        "EU_XETRA", _FakeContract("EUNL", "STK", "SMART", "EUR"),
    )
    assert refusal
    assert "SMART" in refusal or "None" in refusal


def test_a_contract_that_resolved_onto_the_wrong_european_venue_is_refused():
    """Same fund, wrong listing: it would trade, and it would land in another
    venue's bucket — moving a published median rather than raising anything."""
    refusal = runner.venue_guard(
        "EU_XETRA", _FakeContract("SWDA", "STK", "LSE", "USD"),
    )
    assert refusal
    assert "EU_STK_LSE" in refusal and "EU_STK_XETRA" in refusal


def test_the_right_contract_is_not_refused():
    for symbol, venue in runner.EU_VENUES.items():
        contract = _FakeContract(
            "LOCAL", "STK", venue["exchange"], venue["expect_currency"],
        )
        assert runner.venue_guard(symbol, contract) == "", symbol


def test_us_symbols_are_not_guarded():
    """The guard exists because the EU bucket is decided by the contract. US
    cells are decided by the ticker, and a guard there would be noise."""
    assert runner.venue_guard("AAPL", _FakeContract("AAPL", "STK", "SMART", "USD")) == ""


# --------------------------------------------------------------------------- #
# The RTH guard
# --------------------------------------------------------------------------- #

def test_outside_rth_is_refused_for_european_cells():
    refusal = runner.check_outside_rth(["EU_XETRA", "SPY"], outside_rth=True)
    assert refusal and "EU_XETRA" in refusal


def test_outside_rth_is_still_allowed_for_us_only_runs():
    assert runner.check_outside_rth(["AAPL", "SPY"], outside_rth=True) == ""
    assert runner.check_outside_rth(["EU_XETRA"], outside_rth=False) == ""


def test_the_eu_tier_is_reachable_by_name():
    """A run without European market data must be able to skip these by name,
    and a run that wants only them must be able to ask for only them."""
    assert set(runner._expand_instruments("eu")) == set(runner.EU_VENUES)
    assert set(runner.EU_VENUES) <= set(runner._expand_instruments("all"))


# --------------------------------------------------------------------------- #
# The account/mode gate, both directions
# --------------------------------------------------------------------------- #

def test_live_mode_against_a_paper_account_is_refused():
    """Untouched, and load-bearing: paper fills in the live store would corrupt
    the calibration dataset silently."""
    assert runner.account_mode_refusal("live", "DU1234567")
    assert runner.account_mode_refusal("live", "U1234567") == ""


def test_paper_mode_against_a_live_account_is_refused():
    """It used to print 'Orders WILL fire on a real account' and then fire them,
    writing the fills to the store the repo documents as synthetic. Wrong money,
    wrong store, and the store is where it would not show."""
    refusal = runner.account_mode_refusal("paper", "U1234567")
    assert refusal and "LIVE" in refusal
    assert runner.account_mode_refusal("paper", "DU1234567") == ""


def test_the_escape_hatch_exists_and_is_explicit():
    assert runner.account_mode_refusal(
        "paper", "U1234567", allow_live_account=True,
    ) == ""
    # …and does not weaken the other direction, which has no escape hatch.
    assert runner.account_mode_refusal(
        "live", "DU1234567", allow_live_account=True,
    )


# --------------------------------------------------------------------------- #
# The provenance column
# --------------------------------------------------------------------------- #

def test_the_trial_schema_carries_the_isin():
    """A UCITS ETF's local ticker differs per venue, so `symbol` cannot say
    which fund a published European figure measured."""
    from quality import results

    assert "sec_id" in results.COLUMNS


def test_the_primary_isin_is_tried_first_on_every_venue():
    """The candidate list is an ordered chain, and the order is what keeps the
    three venues measuring the same fund. If the primary ever stops being first,
    a cross-venue comparison silently becomes a cross-fund comparison."""
    assert runner.EU_ISIN_CANDIDATES[0][0] == "IE00B4L5Y983"
    assert len({isin for isin, _ in runner.EU_ISIN_CANDIDATES}) == len(
        runner.EU_ISIN_CANDIDATES
    )


# --------------------------------------------------------------------------- #
# Unmeasured is a row, not an omission
# --------------------------------------------------------------------------- #

def _fills(*specs) -> pd.DataFrame:
    """specs: (symbol, secType, exchange, currency, strategy, slip_bps)"""
    return pd.DataFrame([
        {"symbol": s[0], "secType": s[1], "exchange": s[2], "currency": s[3],
         "expiry": None, "strategy_label": s[4], "slip_vs_mid_t0_bps": s[5],
         "status": "FILLED"}
        for s in specs
    ])


def test_a_declared_but_unmeasured_bucket_gets_an_explicit_zero_n_row():
    """`cost_model` floors slippage at zero, so a US bucket can publish 0.00 bps
    from a *measured* negative while an unmeasured bucket publishes 0.00 from
    nothing. An absent row reads as 'not applicable'; `n = 0` beside a blank
    median cannot be read as zero cost."""
    matrix = analyze.bucket_strategy_matrix(_fills(
        ("AAPL", "STK", "SMART", "USD", "MKT_RAW", 1.5),
    ))
    row = matrix[matrix["bucket"] == "EU_STK_XETRA"]
    assert len(row) == 1, "EU_STK_XETRA is declared and untraded; it must appear"
    assert row["MKT_RAW_n"].iloc[0] == 0
    assert pd.isna(row["MKT_RAW_median_bps"].iloc[0]), (
        "an unmeasured median must be blank, never 0.0 — that is the whole point"
    )


def test_every_declared_bucket_appears_exactly_once():
    matrix = analyze.bucket_strategy_matrix(_fills(
        ("AAPL", "STK", "SMART", "USD", "MKT_RAW", 1.5),
        ("EUNL", "STK", "IBIS", "EUR", "MKT_RAW", 4.0),
    ))
    declared = set(buckets.load_bucket_map())
    assert set(matrix["bucket"]) == declared
    assert matrix["bucket"].is_unique


def test_a_measured_bucket_keeps_its_numbers_and_its_counts_stay_integers():
    """The unmeasured rows must not perturb the measured ones — concatenating
    NA into an int column silently promotes it to float, and `4` would publish
    as `4.0`."""
    matrix = analyze.bucket_strategy_matrix(_fills(
        ("EUNL", "STK", "IBIS", "EUR", "MKT_RAW", 4.0),
        ("EUNL", "STK", "IBIS", "EUR", "MKT_RAW", 6.0),
    ))
    xetra = matrix[matrix["bucket"] == "EU_STK_XETRA"].iloc[0]
    assert xetra["MKT_RAW_median_bps"] == pytest.approx(5.0)
    assert xetra["MKT_RAW_n"] == 2
    for col in (c for c in matrix.columns if c.endswith("_n")):
        assert pd.api.types.is_integer_dtype(matrix[col]), col


def test_the_committed_live_matrix_names_the_three_european_venues():
    """The regression this file exists for: before 2026-08-10 the question
    'what about Europe?' was answered by whoever remembered, because the file
    said nothing at all."""
    path = REPO / "order-execution" / "quality" / "results" / "matrix_live.csv"
    if not path.exists():
        pytest.skip("no committed live matrix")
    matrix = pd.read_csv(path)
    for bucket in ("EU_STK_XETRA", "EU_STK_LSE", "EU_STK_SIX"):
        row = matrix[matrix["bucket"] == bucket]
        assert len(row) == 1, f"{bucket} missing from matrix_live.csv"
        n_cols = [c for c in matrix.columns if c.endswith("_n")]
        med_cols = [c for c in matrix.columns if c.endswith("_median_bps")]
        # Until the venues are traded this must stay all-zero-n. When it stops
        # being true this test is what tells you the figures are now real.
        if row[n_cols].sum(axis=1).iloc[0] == 0:
            assert row[med_cols].isna().all(axis=1).iloc[0], (
                f"{bucket} publishes a median with n = 0"
            )
