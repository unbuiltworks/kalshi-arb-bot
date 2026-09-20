"""The economic completion veto, tested against the pair that forced it."""
from decimal import Decimal as D

import pytest

from src.gemini_execution.econ import veto as V


def test_the_deployed_113_pair_is_refused():
    """The 2026-09-20 11:44:33Z completion, replayed as an admission test.

    The arm reported basis=1.13 with fee+=0. Whatever split of that basis is
    assumed between the two legs, the veto must refuse the completion: a pair
    costing 1.13 cannot pay back a $1.00 settlement.
    """
    for held, comp in (("0.63", "0.50"), ("0.78", "0.35"), ("0.50", "0.63")):
        r = V.admits(held_avg_cost=held, proposed_price=comp, min_edge="0.01")
        assert r["admitted"] is False, (held, comp)
        assert "CEILING" in r["reason"]
        # and the pair it would have formed is provably negative
        econ = V.pair_economics(held_qty="0.01", held_avg_cost=held,
                                complement_qty="0.01", complement_price=comp)
        assert D(econ["pair_basis_before_fees"]) > D("1")
        assert econ["fee_positive"] is False
        assert econ["basis_at_or_below_one"] is False


def test_the_ceiling_is_exactly_the_break_even_point_plus_edge():
    """Quoting AT the ceiling clears the edge; one tick above it does not."""
    res = V.max_complement_price(held_avg_cost="0.63", min_edge="0.01")
    assert res["ok"] is True
    cap = D(res["ceiling"])

    at = V.pair_economics(held_qty="1", held_avg_cost="0.63",
                          complement_qty="1", complement_price=str(cap))
    assert D(at["net_pair_economics"]) >= D("0.01")

    above = V.pair_economics(held_qty="1", held_avg_cost="0.63",
                             complement_qty="1",
                             complement_price=str(cap + D("0.01")))
    assert D(above["net_pair_economics"]) < D("0.01")


def test_an_impossible_completion_is_named_impossible_not_priced_near_zero():
    """A held leg above the settlement value admits NO complement at all.

    The failure mode this guards is returning a tiny positive ceiling, which
    reads as "complete cheaply" when the truth is "this pair can never be
    whole".
    """
    r = V.max_complement_price(held_avg_cost="0.99", min_edge="0.05")
    assert r["ok"] is False
    assert r["ceiling"] is None
    assert "NO complement price" in r["why"] or "below one tick" in r["why"]


def test_a_rebate_cannot_buy_admission():
    """THE DISGUISE THIS MODULE EXISTS TO PREVENT.

    A pair that is negative on trading economics stays REFUSED however large
    an accrual is offered alongside it. The parameter is accepted so the
    record shows what was ignored.
    """
    for claimed in ("0.01", "1.00", "1000000"):
        r = V.admits(held_avg_cost="0.63", proposed_price="0.50",
                     min_edge="0.01", accrued_rebate=claimed)
        assert r["admitted"] is False
        assert r["rebate_seen_and_ignored"] == str(D(claimed))
    assert V.max_complement_price(
        held_avg_cost="0.63", min_edge="0.01")["rebate_excluded"] is True


def test_rebate_is_reported_apart_from_trading_pnl():
    econ = V.pair_economics(held_qty="1", held_avg_cost="0.40",
                            complement_qty="1", complement_price="0.55")
    # the accrual exists as its own field and is not inside the net
    assert "rebate_accrued_complement" in econ
    assert "ACCRUED FROM PUBLISHED RULES" in econ["rebate_note"]
    gross = D(econ["trading_pnl_before_fees"])
    fees = D(econ["fees_total"])
    assert D(econ["net_pair_economics"]) == gross - fees
    assert econ["rebate_credited"] is None      # nothing credited is claimed


def test_residual_is_kept_apart_from_the_matched_pair():
    econ = V.pair_economics(held_qty="0.05", held_avg_cost="0.40",
                            complement_qty="0.02", complement_price="0.55")
    assert econ["matched_qty"] == "0.02"
    assert econ["residual_qty"] == "0.03"
    assert econ["residual_side"] == "HELD"
    # economics are computed on the MATCHED quantity only
    assert D(econ["trading_pnl_before_fees"]) == D("0.02") * (
        D("1") - D("0.40") - D("0.55"))


def test_the_ceiling_never_rounds_up_past_a_tick():
    r = V.max_complement_price(held_avg_cost="0.63", min_edge="0.01",
                               tick="0.01")
    assert D(r["ceiling"]) <= D(r["raw_ceiling"])
    assert D(r["ceiling"]) % D("0.01") == 0


def test_a_taker_complement_is_priced_at_the_taker_rate():
    """Bounded aggressive completion is admissible but MORE expensive, so its
    ceiling must be strictly lower than the maker ceiling."""
    mk = V.max_complement_price(held_avg_cost="0.50", min_edge="0.005",
                                complement_liquidity="MAKER", tick="0.001")
    tk = V.max_complement_price(held_avg_cost="0.50", min_edge="0.005",
                                complement_liquidity="TAKER", tick="0.001")
    assert mk["ok"] and tk["ok"]
    assert D(tk["ceiling"]) < D(mk["ceiling"])


def test_unreadable_input_refuses_rather_than_defaulting():
    for bad in (None, "", "not-a-price"):
        r = V.admits(held_avg_cost=bad, proposed_price="0.30",
                     min_edge="0.01")
        assert r["admitted"] is False
    r = V.admits(held_avg_cost="0.30", proposed_price=None, min_edge="0.01")
    assert r["admitted"] is False
    assert "UNREADABLE" in r["reason"]


def test_the_fee_regime_travels_with_every_decision():
    """A decision that cannot say which rates produced it cannot be audited
    after a fee change."""
    r = V.admits(held_avg_cost="0.40", proposed_price="0.30", min_edge="0.01")
    reg = r["ceiling"]["regime"]
    assert reg["maker_rate"] == "0.0175"
    assert reg["taker_rate"] == "0.07"
    assert "rebate_program" in reg["source"]


def test_higher_required_edge_lowers_the_ceiling_monotonically():
    caps = []
    for edge in ("0", "0.01", "0.02", "0.05"):
        r = V.max_complement_price(held_avg_cost="0.50", min_edge=edge,
                                   tick="0.001")
        caps.append(D(r["ceiling"]) if r["ok"] else D("-1"))
    assert caps == sorted(caps, reverse=True)


def test_slippage_is_charged_before_admission_not_after():
    tight = V.max_complement_price(held_avg_cost="0.50", min_edge="0.01",
                                   slippage="0", tick="0.001")
    slipped = V.max_complement_price(held_avg_cost="0.50", min_edge="0.01",
                                     slippage="0.02", tick="0.001")
    assert D(slipped["ceiling"]) < D(tight["ceiling"])


def test_module_holds_no_transport():
    import src.gemini_execution.econ.veto as mod
    src = open(mod.__file__).read()
    for forbidden in ("requests", "httpx", "websocket", "order.place",
                      "api_key", "private_key"):
        assert forbidden not in src
