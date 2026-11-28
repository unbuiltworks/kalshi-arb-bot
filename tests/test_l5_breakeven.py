"""The break-even module must not be able to flatter itself.

The failure modes it is written against, and which these tests pin:
  * pooling two populations that were never randomly assigned;
  * counting a non-reconciling market's loss against zero units;
  * letting ACCRUED incentives stand in for credited ones;
  * quietly turning an approximate credit mapping into a point estimate.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal as D

import pytest

from src import l5_breakeven as be

_SRC = open(be.__file__).read()


def _market(slug, pairs, resid, mc, rc, *, reconciles=True, fills=4,
            basis="venue_confirmed_realized", value=None):
    return {
        "slug": slug,
        "matched_pairs": str(pairs),
        "directional_residual_units": str(resid),
        "matched_contribution_usd": str(mc),
        "residual_contribution_usd": str(rc),
        "fills": fills,
        "value_basis": basis,
        "value_usd": str(value if value is not None else D(str(mc)) + D(str(rc))),
        "realized_usd_venue": str(value if value is not None
                                  else D(str(mc)) + D(str(rc))),
        "qty_reconciliation": {"reconciles": reconciles, "qty_delta": "0.0000"},
    }


# ------------------------------------------------------- the solved equation
def test_breakeven_solves_a_hand_checkable_case():
    # +$1 per pair, -$1 per residual unit, no credit => 50%
    assert be.breakeven_p(D("1"), D("-1")) == pytest.approx(0.5)
    # +$1 per pair, -$3 per residual unit => 75%
    assert be.breakeven_p(D("1"), D("-3")) == pytest.approx(0.75)


def test_credits_lower_the_required_completion_rate():
    bare = be.breakeven_p(D("1"), D("-3"))
    fed = be.breakeven_p(D("1"), D("-3"), D("0.20"))
    assert fed < bare
    # and by exactly credit / (pair - residual)
    assert bare - fed == pytest.approx(0.2 / 4.0)


def test_a_degenerate_spread_returns_none_rather_than_dividing_by_zero():
    assert be.breakeven_p(D("0.5"), D("0.5")) is None


# ------------------------------------------------- the non-reconciling market
def test_a_non_reconciling_market_leaves_both_sides_of_every_rate():
    good = _market("good", 10, 5, "1.00", "-2.00")
    bad = _market("bad", 4, 0, "0.30", "-1.90", reconciles=False)
    econ = be.leg_economics([good, bad])
    assert econ["markets_used"] == 1
    assert [x["slug"] for x in econ["markets_excluded"]] == ["bad"]
    # its 1.90 of loss is absent from the numerator...
    assert econ["residual_contribution_usd"] == D("-2.00")
    # ...and its units are absent from the denominator
    assert econ["matched_pairs"] == D("10")
    assert econ["residual_units"] == D("5")


def test_the_real_cohorts_one_non_reconciling_market_is_the_excluded_one():
    rep = be.report()
    excluded = [x["slug"] for x in rep["markets_excluded"]]
    assert excluded == ["aec-setkameua-vervas-kohboh-2026-09-08"]
    assert rep["markets_used"] == 15


# -------------------------------------------------------------- the two bases
def test_the_per_leg_and_per_market_bases_are_never_pooled():
    rep = be.report()
    primary = rep["breakeven"]["observed"]
    cross = rep["per_market_crosscheck"]["observed_pairing_rate_market_level"]
    assert primary != cross                      # different denominators
    assert "per opened leg" in rep["basis"]
    # the weaker cut is kept, and it carries its warning with it
    assert "CONSTRUCTION" in rep["per_market_crosscheck"]["warning"]


def test_the_per_market_crosscheck_never_feeds_the_headline():
    rep = be.report()
    for k in ("excluding_credits", "including_confirmed_credits", "observed"):
        assert rep["breakeven"][k] != rep["per_market_crosscheck"].get(
            "breakeven_" + k.replace("including_confirmed", "including"))


# ------------------------------------------------------------------- credits
def test_only_credited_incentives_enter_and_rewards_are_zero():
    assert be.CONFIRMED_LIQUIDITY_REWARDS_USD == D("0")
    rep = be.report()
    c = rep["confirmed_credits"]
    assert D(c["liquidity_rewards_usd"]) == D("0")
    assert "SKIPPED or PENDING" in c["rewards_are_zero_because"]
    assert "never revenue" in c["rewards_are_zero_because"]


def test_the_credit_mapping_is_declared_approximate_and_produces_a_range():
    rep = be.report()
    # the key itself is the declaration; the prose says why
    note = rep["confirmed_credits"]["mapping_is_approximate"]
    assert "RANGE, not a point" in note and "NOTIONAL" in note
    b = rep["breakeven"]
    assert b["excluding_credits"] != b["including_confirmed_credits"]
    assert "BRACKET" in b["range_reading"]


def test_fees_are_confirmed_zero_not_assumed_away():
    assert be.CONFIRMED_FEES_USD == D("0.0000")
    assert "CONFIRMED, not assumed" in be.__doc__


# ------------------------------------------------------------- the decomposition
def test_the_decomposition_adds_up_to_the_ledger_quantity():
    """2*pairs + residual must equal the ledger quantity on every row used."""
    cohort = json.load(open("analysis/l5/l5_r5f_cohort.json"))
    for m in cohort["markets"]:
        if not be.reconciles(m):
            continue
        p = D(str(m["matched_pairs"]))
        r = D(str(m["directional_residual_units"]))
        led = D(str(m["qty_reconciliation"]["ledger_qty_total"]))
        assert 2 * p + r == led, m["slug"]


def test_the_headline_matches_the_cohort_arithmetic():
    rep = be.report()
    pp = D(rep["pair_profit_per_matched_pair_usd"])
    rl = D(rep["residual_loss_per_residual_unit_usd"])
    assert pp > 0 and rl < 0
    # a residual unit costs multiples of what a pair earns -- the whole reason
    # completion rate is the dominant lever
    assert (-rl / pp) > 4
    assert rep["breakeven"]["verdict"].startswith("BELOW BREAK-EVEN")
    obs = rep["breakeven"]["observed"]
    assert obs < rep["breakeven"]["including_confirmed_credits"]
    assert obs < rep["breakeven"]["excluding_credits"]


def test_marginal_value_of_a_completion_point_is_the_spread_over_one_hundred():
    rep = be.report()
    pp = D(rep["pair_profit_per_matched_pair_usd"])
    rl = D(rep["residual_loss_per_residual_unit_usd"])
    got = D(rep["marginal_value"]["usd_per_opened_leg_per_completion_point"])
    assert got == ((pp - rl) / D(100)).quantize(D("0.000001"))


# ----------------------------------------------------------------- read-only
def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "post(", "place_order", "submit",
                      "subprocess", "os.system"):
        assert forbidden not in _SRC, forbidden
    # the only writes are the report artifacts under --out
    # the only writes are the two report artifacts under --out
    assert _SRC.count(', "w")') == 2
    assert _SRC.count('"rt"') == 1


def test_it_does_not_touch_the_frozen_completion_experiment():
    for frozen in ("pmus_spring_l5_session", "l5_completion_extension_gate",
                   "l5_completion_extension_eval"):
        assert frozen not in _SRC, frozen


def test_render_survives_an_empty_decomposition():
    md = be.render_markdown({
        "basis": "b", "markets_used": 0, "markets_excluded": [],
        "excluded_because": "x", "matched_pairs": "0", "residual_units": "0",
        "opened_legs": "0", "pair_profit_per_matched_pair_usd": None,
        "residual_loss_per_residual_unit_usd": None,
        "confirmed_credits": {"rebate_per_opened_leg_usd": "0",
                              "liquidity_rewards_usd": "0", "fees_usd": "0"},
        "breakeven": {"available": False, "why": "empty"},
    })
    assert "Unavailable" in md
