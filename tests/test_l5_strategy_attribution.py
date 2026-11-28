"""Per-strategy attribution, derived from the durable order rows.

Each share was bought by ONE order belonging to ONE strategy, and a
winning share redeems $1 whoever bought it. So attribution is exact
arithmetic, and these tests pin that it survives partial fills, a fill
that races a cancel, and a restart.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_execution as X

M = "cpc-btc-updown-5m-2026-11-16-1300z"
#: `_validated` refuses any row whose id is not ours, so the ids here must
#: carry the real prefix -- which is the ledger check working, not a
#: nuisance: a foreign row in our ledger is exactly what it must reject.
def oid(c):
    return X.COID_PREFIX + (c * 24)[:24]


def row(coid, side, price, qty, filled_qty, filled_cost, strategy,
        state=X.FILLED, market=M):
    return coid, {"client_order_id": coid, "market": market, "side": side,
                  "price": price, "qty": qty, "venue_order_id": "v" + coid,
                  "state": state, "filled_qty": filled_qty,
                  "filled_cost": filled_cost, "created_at": None,
                  "reason": None, "slot": 0, "klass": "OPEN",
                  "claim_sid": None, "strategy": strategy}


@pytest.fixture
def ex(tmp_path):
    e = X.Btc5mExecutor(transport=None,
                        ledger_path=str(tmp_path / "ledger.json"))
    return e


def load(ex, *rows):
    ex._ledger["orders"] = dict(rows)


class TestTheStrategyIsDerivedFromTheOrder:

    def test_an_explicit_tag_wins(self):
        assert X._strategy_of({"strategy": "late_favorite"}) == "LATE_FAVORITE"

    @pytest.mark.parametrize("reason,want", [
        ("STRADDLE_LEG", "STRADDLE"),
        ("STRADDLE_COMPLETION", "STRADDLE"),
        ("LATE_FAVORITE", "LATE_FAVORITE"),
    ])
    def test_the_straddles_own_reasons_are_recognised(self, reason, want):
        assert X._strategy_of({"reason": reason}) == want

    def test_an_unplaceable_order_is_UNKNOWN_not_guessed(self):
        """A wrong attribution is worse than an absent one."""
        assert X._strategy_of({"reason": "SOMETHING_ELSE"}) == X.UNATTRIBUTED
        assert X._strategy_of({}) == X.UNATTRIBUTED
        assert X._strategy_of(None) == X.UNATTRIBUTED


class TestAttributionIsExactThroughSettlement:

    def test_each_strategy_gets_exactly_what_it_bought(self, ex):
        load(ex,
             row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE"),
             row(oid("b"), X.C.UP, "0.97", "2", "2", "1.94", "LATE_FAVORITE"))
        got = ex.attribution(M, up_won=True)
        assert got["STRADDLE"]["cost_total"] == D("0.16")
        assert got["LATE_FAVORITE"]["cost_total"] == D("1.94")
        #: a winning share redeems $1 whoever bought it
        assert got["STRADDLE"]["redemption_total"] == D("2")
        assert got["LATE_FAVORITE"]["redemption_total"] == D("2")
        assert got["STRADDLE"]["settled_net_usd"] == D("1.84")
        assert got["LATE_FAVORITE"]["settled_net_usd"] == D("0.06")

    def test_the_losing_side_redeems_nothing_for_either(self, ex):
        load(ex,
             row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE"),
             row(oid("b"), X.C.UP, "0.97", "2", "2", "1.94", "LATE_FAVORITE"))
        got = ex.attribution(M, up_won=False)
        assert got["STRADDLE"]["settled_net_usd"] == D("-0.16")
        assert got["LATE_FAVORITE"]["settled_net_usd"] == D("-1.94")

    def test_the_parts_sum_to_the_market_result(self, ex):
        """Attribution must not create or destroy money."""
        load(ex,
             row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE"),
             row(oid("b"), X.C.DOWN, "0.08", "2", "2", "0.16", "STRADDLE"),
             row(oid("c"), X.C.UP, "0.97", "1", "1", "0.97", "LATE_FAVORITE"))
        got = ex.attribution(M, up_won=True)
        total = sum(v["settled_net_usd"] for v in got.values())
        #: the market's own result from the SAME rows: every UP share
        #: redeems $1, every share cost what it cost.
        up_qty = sum(v[X.C.UP]["qty"] for v in got.values())
        cost = sum(v["cost_total"] for v in got.values())
        assert total == up_qty - cost
        assert total == D("1.71")   # 1.68 straddle + 0.03 late favourite

    def test_an_unknown_outcome_leaves_the_result_unstated(self, ex):
        load(ex, row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE"))
        got = ex.attribution(M, up_won=None)
        assert got["STRADDLE"]["settled_net_usd"] is None
        assert got["STRADDLE"]["redemption_total"] is None
        assert got["STRADDLE"]["cost_total"] == D("0.16")


class TestItSurvivesTheAwkwardCases:

    def test_a_partial_fill_attributes_only_what_filled(self, ex):
        """2 of 5 shares filled: the strategy owns 2, not 5."""
        load(ex, row(oid("a"), X.C.UP, "0.97", "5", "2", "1.94", "LATE_FAVORITE",
                     state=X.CANCELLED))
        got = ex.attribution(M, up_won=True)
        assert got["LATE_FAVORITE"]["filled_qty"] == D("2")
        assert got["LATE_FAVORITE"]["settled_net_usd"] == D("0.06")

    def test_a_fill_that_raced_a_cancel_still_attributes(self, ex):
        """The executor records the fill on the order row, so a cancel
        that lost the race does not lose the attribution with it."""
        load(ex, row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE",
                     state=X.CANCELLED))
        assert ex.attribution(M, up_won=True)["STRADDLE"]["filled_qty"] == D("2")

    def test_an_unfilled_order_contributes_nothing(self, ex):
        load(ex, row(oid("a"), X.C.UP, "0.08", "2", "0", "0", "STRADDLE",
                     state=X.CANCELLED))
        assert ex.attribution(M, up_won=True) == {}

    def test_another_market_is_not_counted(self, ex):
        load(ex,
             row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE"),
             row(oid("b"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE",
                 market="cpceth-eth-updown-5m-2026-11-16-1300z"))
        assert ex.attribution(M, up_won=True)["STRADDLE"]["filled_qty"] == D("2")

    def test_it_survives_a_restart(self, ex, tmp_path):
        """Attribution lives on the durable row, so a new process reads
        the same answer."""
        load(ex,
             row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE"),
             row(oid("b"), X.C.UP, "0.97", "2", "2", "1.94", "LATE_FAVORITE"))
        #: the inventory aggregate must agree with the booked fills or
        #: `_validated` refuses the whole ledger on reload.
        ex._ledger["inventory"] = {M: {X.C.UP: {"qty": "4", "cost": "2.10"},
                                       X.C.DOWN: {"qty": "0", "cost": "0"}}}
        assert ex._save_or_halt() is True
        fresh = X.Btc5mExecutor(transport=None,
                                ledger_path=str(tmp_path / "ledger.json"))
        got = fresh.attribution(M, up_won=True)
        assert set(got) == {"STRADDLE", "LATE_FAVORITE"}
        assert got["LATE_FAVORITE"]["settled_net_usd"] == D("0.06")

    def test_an_order_with_no_strategy_is_reported_as_unknown(self, ex):
        """Rows written before this field existed must not be silently
        folded into a strategy that did not place them."""
        coid, r = row(oid("a"), X.C.UP, "0.08", "2", "2", "0.16", "STRADDLE")
        del r["strategy"]
        load(ex, (coid, r))
        assert X.UNATTRIBUTED in ex.attribution(M, up_won=True)
