"""The calculator's `fx_conv_bps` term — DS-1.

    python3 -m pytest tests -q          (from the repo root)

Record: `batches/2026-09-14-DS1-fx-conversion-record.md`. The conversion is
charged only when the caller names the currency the client pays in, and it is
priced as an `FX_IDEALPRO` trade through the calculator's own commission and
slippage paths. The broker rule is read from the committed `broker_ibkr.json`
(its 0.20 bps and $2 minimum are the point); the harness is stubbed for
`FX_IDEALPRO` so the arithmetic is pinned, except in the one test whose point
is the committed store.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "order-execution"))

from calculator import cost_model, harness_data  # noqa: E402

FX = cost_model.FX_CONV_ASSET_CLASS
STUB_SLIP_BPS = 0.1


@pytest.fixture
def fx_harness(monkeypatch):
    """`FX_IDEALPRO` measured at 0.1 bps, n = 4 of 4; every other class real."""
    real = {name: getattr(harness_data, name) for name in (
        "median_slip_bps_by_strategy", "coverage_by_strategy", "fill_attempts_by_strategy")}

    def stub(name, value):
        def f(asset_class, strategy, mode="paper"):
            if asset_class == FX:
                return value
            return real[name](asset_class, strategy, mode=mode)
        monkeypatch.setattr(harness_data, name, f)

    stub("median_slip_bps_by_strategy", STUB_SLIP_BPS)
    stub("coverage_by_strategy", {"total": 4, "filled": 4, "with_slip": 4})
    stub("fill_attempts_by_strategy", {"attempts": 4, "filled": 4})


def _cost(side="BUY", qty=2000, price=100.0, **kw):
    return cost_model.compute_cost(cost_model.CostInput(
        symbol="ACWI", asset_class="US_ETF", side=side, qty=qty, price=price,
        strategy="MKT_RAW", **kw), harness_mode="live")


def _fx(bd):
    return [line for line in bd.lines if line.label.startswith("fx_conv")]


def test_no_funding_currency_charges_nothing_and_moves_nothing(fx_harness):
    """FX-1: every existing caller names none, so its output is unchanged."""
    before = _cost()
    assert not _fx(before)
    same = _cost(funding_currency="USD")  # funding = contract: no conversion either
    assert not _fx(same)
    assert [(l.label, l.bps_of_notional) for l in same.lines] == \
           [(l.label, l.bps_of_notional) for l in before.lines]


def test_the_core_race_call_shape_is_not_charged_a_conversion(fx_harness):
    """F4: a CHF listing priced with base_currency USD as a pivot — the shape
    pfolio-app's allocation-study/costs.py uses — is not a conversion."""
    bd = cost_model.compute_cost(cost_model.CostInput(
        symbol="ETF", asset_class="EU_STK_SIX", side="BOTH", qty=100, price=100.0,
        base_currency="USD", strategy="MKT_RAW"), harness_mode="live")
    assert not _fx(bd)


def test_a_chf_client_buying_a_usd_fund_pays_the_conversion(fx_harness):
    """Marcel's case (DS-1): ACWI in USD bought with converted francs. $200,000
    clears the $2 minimum, so the rate binds: 0.20 bps commission + 0.1 bps."""
    plain = _cost()
    bd = _cost(funding_currency="CHF", fx_conv_strategy="MKT_RAW")
    comm, slip = _fx(bd)
    assert comm.label == "fx_conv commission [CHF→USD]"
    assert comm.value_base_ccy == pytest.approx(4.0)
    assert comm.bps_of_notional == pytest.approx(0.20)
    assert comm.source == "broker_ibkr.FX_IDEALPRO"
    assert slip.label == "fx_conv slippage [BUY, CHF→USD, MKT_RAW]"
    assert slip.bps_of_notional == pytest.approx(STUB_SLIP_BPS)
    assert slip.value_base_ccy == pytest.approx(2.0)
    assert "EUR/CASH" in slip.note and "CHF.USD" in slip.note
    assert bd.total_bps == pytest.approx(plain.total_bps + 0.30)


def test_the_two_dollar_minimum_binds_on_a_small_conversion(fx_harness):
    bd = _cost(qty=100, funding_currency="CHF", fx_conv_strategy="MKT_RAW")  # $10,000
    comm = _fx(bd)[0]
    assert comm.value_base_ccy == pytest.approx(2.0)
    assert comm.bps_of_notional == pytest.approx(2.0)


def test_the_commission_is_dollars_even_when_the_contract_is_not(fx_harness):
    """A USD client buying on SIX converts USD→CHF; the rule's $2 minimum is
    compared with the notional's USD value, not its franc count."""
    bd = cost_model.compute_cost(cost_model.CostInput(
        symbol="ETF", asset_class="EU_STK_SIX", side="BUY", qty=1000, price=100.0,
        strategy="MKT_RAW", funding_currency="USD", fx_conv_strategy="MKT_RAW"),
        harness_mode="live")
    comm = _fx(bd)[0]
    usd_value = 100_000 * cost_model.CostTables.load().fx_rates["CHF"]
    assert comm.label == "fx_conv commission [USD→CHF]"
    assert comm.value_base_ccy == pytest.approx(max(usd_value * 0.20 / 1e4, 2.0))


def test_a_round_trip_converts_in_and_back(fx_harness):
    """FX-4: commission ×2, and one slippage line per leg with its direction."""
    bd = _cost(side="BOTH", funding_currency="CHF", fx_conv_strategy="LMT_MID")
    comm, entry, exit_ = _fx(bd)
    assert comm.label == "fx_conv commission (×2 round-trip, CHF⇄USD)"
    assert comm.bps_of_notional == pytest.approx(0.40)
    assert entry.label == "fx_conv slippage [entry, CHF→USD, LMT_MID]"
    assert exit_.label == "fx_conv slippage [exit, USD→CHF, LMT_MID]"


def test_a_sell_converts_the_proceeds_back(fx_harness):
    bd = _cost(side="SELL", funding_currency="CHF", fx_conv_strategy="MKT_RAW")
    assert [l.label for l in _fx(bd)] == [
        "fx_conv commission [USD→CHF]", "fx_conv slippage [SELL, USD→CHF, MKT_RAW]"]


def test_the_conversion_strategy_has_no_default(fx_harness):
    """FX-3: reusing the trade's strategy silently would be the default
    `CostInput.strategy` was stripped of."""
    with pytest.raises(ValueError, match="fx_conv_strategy"):
        _cost(funding_currency="CHF")


def test_an_unmeasured_conversion_is_flagged_not_zeroed(monkeypatch):
    monkeypatch.setattr(harness_data, "median_slip_bps_by_strategy",
                        lambda ac, s, mode="paper": None if ac == FX else 0.5)
    real_state = harness_data.measurement_state
    monkeypatch.setattr(harness_data, "measurement_state",
                        lambda ac, mode="paper": "unmeasured" if ac == FX else real_state(ac, mode=mode))
    bd = _cost(funding_currency="CHF", fx_conv_strategy="MKT_ADAPTIVE")
    slip = _fx(bd)[1]
    assert slip.unmeasured and slip.bps_of_notional == 0.0
    assert slip.label in bd.unmeasured_components
    assert not bd.is_complete


def test_on_the_committed_store_a_chf_to_usd_conversion_is_measured():
    """The point here IS the store: `FX_IDEALPRO` × `MKT_RAW` has live fills, so
    the conversion leg DS-1 names prices from a measurement, not a placeholder."""
    if not (REPO / "order-execution" / "quality" / "results" / "trials_live.parquet").exists():
        pytest.skip("no live trial store")
    bd = _cost(funding_currency="CHF", fx_conv_strategy="MKT_RAW")
    slip = [l for l in _fx(bd) if "slippage" in l.label][0]
    assert not slip.unmeasured
    assert 0.0 <= slip.bps_of_notional < 1.0
