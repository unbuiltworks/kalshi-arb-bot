"""The shadow completion evaluator: modelled inventory, real book.

This exists because Stage B's structural shadow lock makes the live
completion path unobservable -- it reads real fills and there are none -- so
the policy is measured against the inventory the maker-only arm modelled.
These tests pin the join between the two, the price convention, and the
settlement comparison.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_maker_fill_model as FM
from src import l5_shadow_completion as SC
from src import l5_two_sided_probe as TSP


class _Cfg:
    """A shadow Config's surface, and nothing else."""

    def __init__(self, up, dn, up_basis, dn_basis, *,
                 policy=SC.LIVE_SEQUENTIAL, thresh="0.96", envelope="3",
                 depth_ticks=2, spent="0"):
        self.qty = {FM.UP: D(str(up)), FM.DOWN: D(str(dn))}
        self._basis = {FM.UP: D(str(up_basis)), FM.DOWN: D(str(dn_basis))}
        self.policy = policy
        self.thresh = D(str(thresh))
        self.envelope = D(str(envelope))
        self.depth_ticks = depth_ticks
        self._spent = D(str(spent))

    def basis(self, side):
        return self._basis.get(side)

    def spent(self):
        return self._spent


BOOK = {"bid": "0.54", "ask": "0.55", "bid_shares": "830",
        "ask_shares": "120", "stream_age_s": 0.1}


# ------------------------------------------------------------- the arm join

def test_arm_id_and_cell_id_agree_for_the_same_arm():
    """The evaluate-time id and the score-time id must join.

    The live Config spells it `thresh`; the scored cell spells it
    `threshold`. One translation, in one place, or the window's cross cannot
    be matched to the window's result.
    """
    cfg = _Cfg(0, 2, 0, "0.45")
    cell = {"policy": SC.LIVE_SEQUENTIAL, "threshold": "0.96",
            "envelope": "3", "depth_ticks": 2}
    assert SC.arm_id(cfg) == SC.arm_id_from_cell(cell)


def test_only_the_arm_that_mirrors_the_deployed_engine_is_graded():
    """Grading historical at-touch cells would measure a config we don't run."""
    assert SC.is_live_arm(_Cfg(0, 1, 0, "0.4")) is True
    assert SC.is_live_arm(_Cfg(0, 1, 0, "0.4", depth_ticks=0)) is False
    assert SC.is_live_arm(_Cfg(0, 1, 0, "0.4", thresh="0.99")) is False
    assert SC.is_live_arm(_Cfg(0, 1, 0, "0.4", policy="SKEWED")) is False


def test_live_arm_cells_picks_the_same_arm_out_of_a_scored_report():
    rep = {"cells": [
        {"policy": SC.LIVE_SEQUENTIAL, "threshold": "0.96", "depth_ticks": 2},
        {"policy": SC.LIVE_SEQUENTIAL, "threshold": "0.96", "depth_ticks": 0},
        {"policy": "SKEWED", "threshold": "0.99", "depth_ticks": 2},
    ]}
    got = SC.live_arm_cells(rep)
    assert len(got) == 1
    assert got[0]["depth_ticks"] == 2


# --------------------------------------------------------- the own-price rule

def test_a_DOWN_completion_is_priced_at_one_minus_the_UP_BID():
    """Passing the UP ask for a DOWN completion is the error this prevents."""
    cfg = _Cfg(2, 0, "0.46", 0)            # long UP -> needs DOWN
    res = SC.evaluate(cfg=cfg, snapshot=BOOK, maker_rested_s=400.0)
    assert res["side"] == FM.DOWN
    assert D(res["cross_own_px"]) == D(1) - D("0.54")
    assert D(res["displayed_depth"]) == D("830")      # the BID side


def test_an_UP_completion_is_priced_at_the_UP_ASK():
    cfg = _Cfg(0, 2, 0, "0.45")            # long DOWN -> needs UP
    res = SC.evaluate(cfg=cfg, snapshot=BOOK, maker_rested_s=400.0)
    assert res["side"] == FM.UP
    assert D(res["cross_own_px"]) == D("0.55")
    assert D(res["displayed_depth"]) == D("120")      # the ASK side


# ------------------------------------------------------------- the refusals

def test_a_balanced_arm_has_nothing_to_complete():
    res = SC.evaluate(cfg=_Cfg(2, 2, "0.5", "0.45"), snapshot=BOOK)
    assert res["ok"] is False
    assert res["reason"] == "NO_MODELLED_DEFICIT"


@pytest.mark.parametrize("up,dn,book", [
    # long DOWN -> needs UP -> priced off the ASK, so the ASK is what matters
    (0, 2, {"bid": "0.54", "ask": None, "bid_shares": "1",
            "ask_shares": "1"}),
    (0, 2, {"bid": "0.54", "ask": "0.55", "bid_shares": "1"}),
    # long UP -> needs DOWN -> priced off the BID
    (2, 0, {"bid": None, "ask": "0.55", "bid_shares": "1",
            "ask_shares": "1"}),
    (2, 0, {"bid": "0.54", "ask": "0.55", "ask_shares": "1"}),
])
def test_an_incomplete_book_refuses_rather_than_assuming_a_price(up, dn, book):
    res = SC.evaluate(cfg=_Cfg(up, dn, "0.46" if up else 0,
                               "0.45" if dn else 0), snapshot=book)
    assert res["ok"] is False
    assert res["reason"] == "BOOK_INCOMPLETE"


def test_the_side_we_do_not_buy_may_be_missing_without_refusing():
    """A DOWN-held arm needs UP, so a missing BID is correctly irrelevant.

    Refusing on it would discard real opportunities for a field the price
    does not depend on.
    """
    res = SC.evaluate(cfg=_Cfg(0, 2, 0, "0.45"),
                      snapshot={"bid": None, "ask": "0.10",
                                "ask_shares": "900", "bid_shares": None,
                                "stream_age_s": 0.1},
                      maker_rested_s=400.0)
    assert res["reason"] != "BOOK_INCOMPLETE"
    assert D(res["cross_own_px"]) == D("0.10")


def test_an_unreadable_arm_refuses():
    class _Bad:
        policy = SC.LIVE_SEQUENTIAL
        thresh = D("0.96")
        envelope = D(3)
        depth_ticks = 2

        @property
        def qty(self):
            raise RuntimeError("boom")

    res = SC.evaluate(cfg=_Bad(), snapshot=BOOK)
    assert res["ok"] is False
    assert res["reason"].startswith("ARM_UNREADABLE:")


def test_the_cash_bound_is_the_ARM_envelope_not_the_account():
    """An arm sized by a $3 envelope must not get a completion it can't fund.

    Crediting it with the account's headroom would report an opportunity
    that this modelled arm never had.
    """
    cheap = {"bid": "0.90", "ask": "0.95", "bid_shares": "900",
             "ask_shares": "900", "stream_age_s": 0.1}
    broke = _Cfg(0, 2, 0, "0.05", envelope="3", spent="3")
    res = SC.evaluate(cfg=broke, snapshot=cheap, maker_rested_s=400.0)
    assert D(res["arm_cash_headroom"]) == D(0)
    assert res["ok"] is False


# ------------------------------------------------- the policy is still in force

def test_the_margin_floor_still_applies_to_modelled_inventory():
    """The shadow measures the real policy, not a relaxed one."""
    cfg = _Cfg(0, 2, 0, "0.45")
    thin = dict(BOOK, ask="0.54", bid="0.53")
    res = SC.evaluate(cfg=cfg, snapshot=thin, maker_rested_s=400.0)
    assert res["ok"] is False
    assert res["reason"] in ("MARGIN_BELOW_FLOOR",
                            "WORST_OUTCOME_NOT_POSITIVE",
                            "CONSERVATIVE_WORST_NOT_POSITIVE",
                            "IMPROVEMENT_BELOW_ITS_OWN_FEE",
                            "DECISION_NOT_OK")


def test_maker_first_still_applies_to_modelled_inventory():
    cfg = _Cfg(0, 2, 0, "0.45")
    cheap = dict(BOOK, ask="0.10", bid="0.09", ask_shares="900")
    ok = SC.evaluate(cfg=cfg, snapshot=cheap, maker_rested_s=400.0)
    assert ok["ok"] is True
    early = SC.evaluate(cfg=cfg, snapshot=cheap, maker_rested_s=5.0)
    assert early["ok"] is False
    assert early["reason"] == "MAKER_PATIENCE_NOT_SPENT"


def test_the_2145z_shaped_state_authorizes_on_modelled_inventory():
    """The campaign's real case, as a modelled arm would have held it."""
    cfg = _Cfg(0, 2, 0, "0.45")
    cheap = dict(BOOK, ask="0.10", bid="0.09", ask_shares="126")
    res = SC.evaluate(cfg=cfg, snapshot=cheap, maker_rested_s=400.0)
    assert res["ok"] is True
    pol = res["taker_policy"]
    assert D(pol["conservative_worst_after"]) > D("0.25")


# ------------------------------------------------------- settlement comparison

ARM = {"policy": SC.LIVE_SEQUENTIAL, "threshold": "0.96", "depth_ticks": 2,
       "up": "0", "dn": "2", "matched_shares": "0", "matched_cost": "0",
       "residual_cost": "0.90", "net_pnl_modelled": "-0.90"}


def test_an_unsettled_window_is_never_scored():
    res = SC.score_window(arm_report=ARM, cross=None, settle_up=None)
    assert res["ok"] is False
    assert res["reason"] == "NOT_SETTLED"


def test_a_window_where_the_policy_did_nothing_reports_an_identical_outcome():
    """A window the policy correctly sat out is evidence, not an omission."""
    res = SC.score_window(arm_report=ARM, cross=None, settle_up=False)
    assert res["ok"] is True
    assert res["reason"] == "NO_CROSS_AUTHORIZED"
    assert res["crossed"] is False
    assert D(res["delta_usd"]) == D(0)
    assert D(res["completed_net_usd"]) == D(res["maker_only_net_usd"])


def test_the_completed_outcome_is_scored_on_the_venue_resolution():
    """2145z's shape: hold 2 DOWN at 0.45, cross 2 UP at 0.10.

    DOWN wins: maker-only keeps +1.10 and the completed pair is +0.88, so
    completing COSTS 0.22 of directional luck -- which is the trade, and the
    comparison must show it rather than hide it.
    """
    arm = dict(ARM, net_pnl_modelled="1.10")
    cross = {"side": FM.UP, "qty": "2", "price": "0.10"}
    dn_wins = SC.score_window(arm_report=arm, cross=cross, settle_up=False)
    assert dn_wins["ok"] is True
    assert D(dn_wins["completed_net_usd"]) == D("0.88")
    assert D(dn_wins["delta_usd"]) == D("-0.22")

    # UP wins: maker-only loses the whole leg, the completion saves it
    arm_up = dict(ARM, net_pnl_modelled="-0.90")
    up_wins = SC.score_window(arm_report=arm_up, cross=cross, settle_up=True)
    assert D(up_wins["completed_net_usd"]) == D("0.88")
    assert D(up_wins["delta_usd"]) == D("1.78")


def test_the_completed_outcome_charges_a_taker_fee():
    cross = {"side": FM.UP, "qty": "2", "price": "0.10"}
    res = SC.score_window(arm_report=ARM, cross=cross, settle_up=True)
    fee = TSP.worst_fee(D(2), D("0.10"))
    assert fee > 0
    assert D(res["cross_outlay_usd"]) == D(2) * D("0.10") + fee
    # payout 2, cost 0.90 + 0.20 + fee
    assert D(res["total_cost_usd"]) == D("0.90") + D("0.20") + fee


def test_an_unreadable_cross_refuses_rather_than_scoring_a_guess():
    for bad in ({"side": "SIDEWAYS", "qty": "2", "price": "0.1"},
                {"side": FM.UP, "qty": None, "price": "0.1"},
                {"side": FM.UP, "qty": "2", "price": None}):
        res = SC.score_window(arm_report=ARM, cross=bad, settle_up=True)
        assert res["ok"] is False
        assert res["reason"] == "CROSS_UNREADABLE"


def test_an_unreadable_maker_only_net_refuses():
    res = SC.score_window(arm_report=dict(ARM, net_pnl_modelled=None),
                          cross=None, settle_up=True)
    assert res["ok"] is False
    assert res["reason"] == "MAKER_ONLY_NET_UNREADABLE"


def test_every_record_declares_the_modelled_basis():
    """A modelled figure that does not say so is how phantom P&L happens."""
    res = SC.evaluate(cfg=_Cfg(0, 2, 0, "0.45"), snapshot=BOOK)
    assert "MODELLED" in res["basis"]
    sc = SC.score_window(arm_report=ARM, cross=None, settle_up=True)
    assert "MODELLED" in sc["basis"]
    assert "realized" in sc["basis"]
