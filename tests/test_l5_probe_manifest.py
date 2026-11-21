"""The envelope must be exactly what was authorised, and provably so.

The defaults are not the envelope: SafetyLimits ships max_total_exposure_usd
$10, so a probe built on them would risk twice what every document said. That
is the specific mistake these tests exist to prevent.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_probe_manifest as M
from src import pmus_fees as fees
from src.l5_live_execution import SafetyLimits


def test_the_worst_case_is_exactly_the_authorised_number():
    wc = M.worst_case()
    assert wc["principal_usd"] == "5"
    assert wc["worst_case_total_usd"] == "5.2375"


def test_the_defaults_are_NOT_the_envelope():
    # the whole reason this module exists
    assert SafetyLimits().worst_case_loss_usd() == D("10")
    assert M.probe_limits().worst_case_loss_usd() == D("5")


def test_every_bound_is_stated_not_defaulted():
    d = M.probe_limits().as_dict()
    assert d["max_usd_per_market"] == "5"
    assert d["max_total_exposure_usd"] == "5"
    assert d["daily_realized_loss_halt_usd"] == "5"
    assert d["max_concurrent_markets"] == 1
    assert d["band"] is None          # no behavioural band is imposed


def test_the_fee_comes_from_the_venue_implementation():
    wc = M.worst_case()
    assert D(wc["max_taker_fee_usd"]) == fees.taker_fee(D("0.5"), D("10"))


def test_the_manifest_is_disarmed_and_names_one_market():
    m = M.manifest("cpc-btc-updown-15m-2026-09-23-2330z")
    assert m["armed"] is False
    assert m["limits"]["max_concurrent_markets"] == 1
    assert m["series"] == "btc-updown-15m"


def test_lambda_is_the_frozen_normalization():
    # 1/median(l5_size); the median 22.13-share action becomes ~1.00 share
    assert abs(M.LAMBDA * D("22.13") - D("1")) < D("0.001")
