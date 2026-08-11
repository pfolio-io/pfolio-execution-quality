"""What keeps a European trial honest once the venue is observed, not chosen.

    python3 -m pytest tests -q          (from the repo root)

`tests/test_buckets.py` asserts that the bucket *map* can tell the three venues
apart. This file asserts the layer above it: that a SMART-routed row buckets by
**where it executed**, that a venue nobody has a commission rule for is reported
rather than absorbed, and that the matrix says "never measured" out loud instead
of by omission.

⚑ **Rewritten 2026-08-11 for E-14.** It used to test a pre-trade `venue_guard`
that refused to trade when the resolved contract would not land in the intended
bucket. Under SMART there is no intended bucket before the fill — the router
chooses at execution — so prevention became detection: `exec_exchange` decides
the bucket, and `venue_coverage` names the venues canon cannot price. The failure
mode being defended against is unchanged: a fill that costs money and produces
either no measurement or one attributed to the wrong venue.

No IB connection, no network.
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


# --------------------------------------------------------------------------- #
# Bucketing from the FILL, not the request (E-14)
# --------------------------------------------------------------------------- #

def test_every_line_expects_a_bucket_the_map_declares():
    """`expect_bucket` is a prior for the coverage report, not a filter — but a
    name that does not exist would make the report meaningless."""
    declared = buckets.load_bucket_map()
    for symbol, line in runner.EU_LINES.items():
        assert line["expect_bucket"] in declared, (
            f"{symbol} expects {line['expect_bucket']!r}, undeclared"
        )


def test_a_smart_row_buckets_by_where_it_executed():
    """The whole of E-14 in one assertion. Under SMART `exchange` is the string
    'SMART' and says nothing; European commission varies by venue, so the bucket
    has to name the venue that actually charged."""
    df = pd.DataFrame([{
        "symbol": "SXR8", "secType": "STK", "exchange": "SMART",
        "exec_exchange": "IBIS2", "currency": "EUR", "expiry": None,
    }])
    assert list(buckets.bucket_series(df)) == ["EU_STK_XETRA"]


def test_the_same_row_without_the_fill_venue_buckets_nowhere():
    """The state before `exec_exchange` existed: SMART matches no EU selector,
    so the row is unmapped rather than silently assigned. That is the failure
    E-14 had to solve, kept as a test so it cannot come back unnoticed."""
    df = pd.DataFrame([{
        "symbol": "SXR8", "secType": "STK", "exchange": "SMART",
        "exec_exchange": None, "currency": "EUR", "expiry": None,
    }])
    assert list(buckets.bucket_series(df)) == [None]


def test_rows_written_before_the_column_existed_are_unaffected():
    """No `exec_exchange` column at all — every historical row, and the FUT/FX
    cells where the request IS the venue."""
    df = pd.DataFrame([
        {"symbol": "ES", "secType": "FUT", "exchange": "CME", "currency": "USD",
         "expiry": "20260618"},
        {"symbol": "SPY", "secType": "STK", "exchange": "SMART", "currency": "USD",
         "expiry": None},
    ])
    assert list(buckets.bucket_series(df)) == ["FUT_CME", "US_ETF"]


def test_venue_series_prefers_the_fill_and_falls_back_to_the_request():
    df = pd.DataFrame([
        {"exchange": "SMART", "exec_exchange": "GETTEX2"},
        {"exchange": "SMART", "exec_exchange": None},
        {"exchange": "CME", "exec_exchange": ""},
        {"exchange": "SMART", "exec_exchange": "LSEETF,CHIXCH"},
    ])
    assert list(buckets.venue_series(df)) == ["GETTEX2", "SMART", "CME", "LSEETF"]


def test_us_equity_buckets_ignore_the_venue_entirely():
    """Why SMART was always fine for the US cells: those selectors key on symbol
    and never look at an exchange, and US commission does not vary by venue."""
    df = pd.DataFrame([
        {"symbol": "SPY", "secType": "STK", "exchange": "SMART",
         "exec_exchange": v, "currency": "USD", "expiry": None}
        for v in ("ARCA", "BATS", "IEX", "ISLAND")
    ])
    assert set(buckets.bucket_series(df)) == {"US_ETF"}


# --------------------------------------------------------------------------- #
# The venue-coverage report — the discovery mechanism that replaced the guard
# --------------------------------------------------------------------------- #

def test_coverage_flags_a_venue_with_no_commission_rule():
    """AEB is Amsterdam. The EUR line's `validExchanges` includes it, so SMART
    *could* route there — and the bucket map deliberately does not accept it,
    because folding another country into the German bucket would be the same
    error as calling a Gettex fill XETRA. If SMART ever uses it, this is what
    says the fill is priced by nothing instead of leaving a hole in a total.

    ⚑ `GETTEX2` was this test's example until 2026-08-11, when the selectors
    widened to cover the venues SMART actually reaches for each line. That it
    stopped failing here is the edit working."""
    rows = [{"status": "FILLED", "symbol": "SXR8", "secType": "STK",
             "currency": "EUR", "exec_exchange": "AEB"}]
    cov = runner.venue_coverage(rows)
    assert cov["AEB"]["fills"] == 1
    assert cov["AEB"]["bucket"] is None
    assert cov["AEB"]["priced"] is False


def test_the_venues_smart_actually_used_are_all_priced_now():
    """Every venue observed in 71 trials on 2026-08-11. Before the selector
    widening, 17 of 28 fills were discarded."""
    for venue, ccy in (("IBIS2", "EUR"), ("GETTEX2", "EUR"),
                       ("LSEETF", "GBP"), ("EBS", "CHF"), ("BATECH", "CHF")):
        cov = runner.venue_coverage([{
            "status": "FILLED", "symbol": "X", "secType": "STK",
            "currency": ccy, "exec_exchange": venue}])
        assert cov[venue]["priced"], f"{venue}/{ccy} still unpriced"


def test_coverage_confirms_a_venue_canon_can_price():
    rows = [{"status": "FILLED", "symbol": "SXR8", "secType": "STK",
             "currency": "EUR", "exec_exchange": "IBIS2"}]
    cov = runner.venue_coverage(rows)
    assert cov["IBIS2"]["bucket"] == "EU_STK_XETRA"
    assert cov["IBIS2"]["priced"] is True


def test_coverage_ignores_rows_that_never_filled():
    rows = [{"status": "CANCELLED", "exec_exchange": "IBIS2"},
            {"status": "FILLED", "exec_exchange": None}]
    assert runner.venue_coverage(rows) == {}


# --------------------------------------------------------------------------- #
# Flags that survive E-14 unchanged
# --------------------------------------------------------------------------- #

def test_outside_rth_is_refused_for_european_cells():
    refusal = runner.check_outside_rth(["EU_ETF_EUR", "SPY"], outside_rth=True)
    assert refusal and "EU_ETF_EUR" in refusal


def test_outside_rth_is_still_allowed_for_us_only_runs():
    assert runner.check_outside_rth(["AAPL", "SPY"], outside_rth=True) == ""
    assert runner.check_outside_rth(["EU_ETF_EUR"], outside_rth=False) == ""


def test_the_eu_tier_is_reachable_by_name():
    """A run without European market data must be able to skip these by name,
    and a run that wants only them must be able to ask for only them."""
    assert set(runner._expand_instruments("eu")) == set(runner.TIER_EU)
    assert set(runner.TIER_EU) <= set(runner._expand_instruments("all"))


def test_the_usd_line_is_addressable_but_not_in_the_tier():
    """IBKR publishes no SMART listing for it, so a user routing SMART cannot
    buy it and a cost-per-user figure for it would price something unreachable.
    Still nameable, so asking for it explicitly says so rather than 404s."""
    assert "EU_ETF_USD" in runner.EU_LINES
    assert "EU_ETF_USD" not in runner.TIER_EU
    assert runner._expand_instruments("EU_ETF_USD") == ["EU_ETF_USD"]


def test_each_cell_is_a_distinct_listing_currency():
    """The currency is the only thing separating the cells, which is why
    resolution requires it rather than preferring it."""
    ccys = [line["currency"] for line in runner.EU_LINES.values()]
    assert sorted(ccys) == sorted(set(ccys)), "two cells share a currency"
    assert set(ccys) == {"EUR", "GBP", "USD", "CHF"}


def test_the_chf_cell_uses_a_different_fund_and_says_so():
    """Forced, not chosen: the global candidates have zero CHF listings, so the
    only natively SIX-listed CHF ETFs are Swiss-domiciled trackers. The confound
    is real — the CHF cell measures a different asset class — and the separate
    candidate chain is where that is visible in the code."""
    chf = runner.EU_LINES["EU_ETF_CHF"]
    assert chf["isins"] is runner.EU_CHF_ISIN_CANDIDATES
    assert not set(i for i, _ in runner.EU_CHF_ISIN_CANDIDATES) & set(
        i for i, _ in runner.EU_ISIN_CANDIDATES)
    for line in ("EU_ETF_EUR", "EU_ETF_GBP", "EU_ETF_USD"):
        assert "isins" not in runner.EU_LINES[line], (
            f"{line} should use the shared global chain")


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
    a cross-venue comparison silently becomes a cross-fund comparison.

    The primary is `IE00B5BMR087` as of 2026-08-11: its EUR line is the only one
    of the three that is **IBKR-primary on IBIS2**. The others are Amsterdam- or
    London-primary and merely routable to Xetra, which makes a Xetra order in
    them a foreign-primary instrument sent to Xetra — and bills the quote to a
    market-data feed no German subscription covers."""
    assert runner.EU_ISIN_CANDIDATES[0][0] == "IE00B5BMR087"
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


# --------------------------------------------------------------------------- #
# The pence bug — measured, not theorised
# --------------------------------------------------------------------------- #

def test_a_pence_quoted_line_does_not_understate_commission_by_100x():
    """The defect as found on 2026-08-11's first live GBP fill.

    IBKR reports `currency = GBP` for `CSP1` and then quotes it in GBX: the fill
    came back at 61917, meaning GBP 619.17, and a GBP 1.00 commission on it is
    ~16 bps. Dividing by the quoted number gave 0.16.
    """
    import pandas as pd
    from quality import analyze

    row = pd.Series({
        "commission": 1.00, "commission_currency": "GBP", "currency": "GBP",
        "filled_qty": 1.0, "avg_fill_px": 61917.0, "multiplier": None,
        "price_magnifier": 100,
    })
    bps = analyze._commission_bps_row(row, {"GBP": 1.27, "USD": 1.0})
    assert bps == pytest.approx(16.15, abs=0.1), bps

    naive = row.copy()
    naive["price_magnifier"] = 1
    assert analyze._commission_bps_row(naive, {"GBP": 1.27, "USD": 1.0}) \
        == pytest.approx(bps / 100, rel=1e-6)


def test_rows_without_a_magnifier_are_unchanged():
    """Every row written before 2026-08-11, and every non-pence instrument."""
    import pandas as pd
    from quality import analyze

    row = pd.Series({
        "commission": 1.00, "commission_currency": "USD", "currency": "USD",
        "filled_qty": 100.0, "avg_fill_px": 200.0, "multiplier": None,
    })
    assert analyze._commission_bps_row(row, {"USD": 1.0}) == pytest.approx(0.5)


def test_slippage_is_immune_to_the_magnifier():
    """The measurement the exercise exists for is a ratio of two prices in the
    same units, so the bug never touched it. Asserted so nobody 'fixes' it."""
    from quote_snapshot import slip_vs_mid_bps

    assert slip_vs_mid_bps(1, 61923.0, 61917.5) == pytest.approx(
        slip_vs_mid_bps(1, 619.23, 619.175), rel=1e-9)


# --------------------------------------------------------------------------- #
# The append path must survive a store written by something else
# --------------------------------------------------------------------------- #

def test_append_survives_a_store_rewritten_by_pandas(tmp_path):
    """⚑ Regression for a defect that cost a live fill on 2026-08-11.

    A maintenance script rewrote the trial stores through `pandas.to_parquet`,
    which encodes strings as `large_string` and an all-integer column as
    `double`. `Table.from_pylist` produces `string` and `int64`, and
    `concat_tables(promote_options="default")` refuses both pairings — so
    `append_row` raised AFTER the order had filled. The trial was lost and, worse,
    the exception unwound past auto-flatten and left a position open on a live
    account.

    The store must therefore tolerate being written by something other than the
    harness, because sooner or later it will be.
    """
    import pandas as pd
    from quality import results

    store = tmp_path / "trials_paper.parquet"
    first = {c: None for c in results.COLUMNS}
    first.update({"run_id": "r1", "symbol": "SXR8", "conId": 1,
                  "price_magnifier": 1, "status": "FILLED"})
    results._append_parquet(store, first)

    # the round trip that broke it
    pd.read_parquet(store).to_parquet(store, index=False)

    second = dict(first, run_id="r2")
    results._append_parquet(store, second)          # must not raise

    out = pd.read_parquet(store)
    assert list(out["run_id"]) == ["r1", "r2"]
    assert len(out) == 2


def test_the_tradeweb_mtfs_are_treated_consistently_across_the_three_lines():
    """The invariant that broke on 2026-08-11.

    `TRWBCH` was admitted to the Swiss selector and `TRWBUKETF` to the London one
    straight from `validExchanges`, while `TRWBEN` — the same venue family on the
    EUR line — was excluded because I read it as a Netherlands *exchange*. One
    family, three lines, two different answers, and only the missing one cost
    fills."""
    m = buckets.load_bucket_map()
    tradeweb = {
        "EU_STK_XETRA": "TRWBEN",
        "EU_STK_LSE": "TRWBUKETF",
        "EU_STK_SIX": "TRWBCH",
    }
    for bucket, venue in tradeweb.items():
        assert venue in m[bucket].exchange_any, (
            f"{bucket} does not accept {venue}, but its siblings accept theirs"
        )


def test_no_european_fill_in_the_committed_live_store_is_unmapped():
    """The regression guard the venue discoveries earned.

    GETTEX2, BATECH, EUDARK and TRWBEN each turned up only by trading, and each
    silently dropped fills until canon caught up. This fails the moment SMART
    uses a venue nothing can bucket — which is the signal to run the same
    evidence check (does its commission match the line's home schedule?) rather
    than to widen the selector reflexively."""
    import pandas as pd

    store = REPO / "order-execution" / "quality" / "results" / "trials_live.parquet"
    if not store.exists():
        pytest.skip("no live store")
    df = pd.read_parquet(store)
    if "sec_id" not in df:
        pytest.skip("store predates the sec_id column")
    fills = df[(df["sec_id"].notna()) & (df["status"] == "FILLED")].copy()
    if fills.empty:
        pytest.skip("no European fills yet")
    fills["bucket"] = buckets.bucket_series(fills)
    orphans = fills[fills["bucket"].isna()]
    assert orphans.empty, (
        "European fills that bucket nowhere: "
        f"{sorted(orphans['exec_exchange'].dropna().unique())}"
    )
