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


# ---- the owner ruling of 2026-09-18, and what it requires of this module ----

def test_the_veto_is_classified_a_risk_overlay_not_a_fidelity_claim():
    """L5_OPERATING_MODEL.md 2d: the Gemini live path takes NO pair-cost
    ceiling, and any future ceiling must be labelled a risk overlay.

    42.75% of L5's 33,765 historical COMPLETEs were above $1.00. This module
    plainly does NOT reproduce that behaviour and must never be read as
    evidence that L5 would have refused these pairs.
    """
    assert V.CLASSIFICATION == "RISK_OVERLAY"
    assert any("RISK OVERLAY" in x for x in V.LABELS)
    assert any("not strategy fidelity" in x for x in V.LABELS)


def test_a_forbidden_pair_records_the_cost_the_ruling_demands():
    """'Carry its stated cost' means a number measured on OUR tape, not the
    ruling's historical shares restated back at it."""
    f = V.forbidden_pair(held_qty="0.01", held_avg_cost="0.63",
                         complement_qty="0.01", complement_price="0.50")
    assert f["forbidden_by_overlay"] is True
    assert f["classification"] == "RISK_OVERLAY"
    assert D(f["pair_basis_before_fees"]) == D("1.13")
    assert D(f["net_pair_economics"]) < 0


def test_the_cost_record_states_what_it_cannot_see():
    """The ruling's strongest counter is DOWNSTREAM -- 97.21% of expensive
    completes were followed by further fills. An instantaneous record cannot
    see that, and must say so rather than imply the cost is fully captured."""
    f = V.forbidden_pair(held_qty="0.01", held_avg_cost="0.63",
                         complement_qty="0.01", complement_price="0.50")
    assert "97.21%" in f["cost_note"]
    assert "INSTANTANEOUS" in f["cost_note"]


def test_no_096_ceiling_is_imposed():
    """The ruling names 0.96 specifically, and the scored evidence is that
    `<= 0.96` was NEGATIVE (-28 / -119 bp) while 1.00-1.02 was BEST
    (+98 / +61 bp). The overlay must not quietly become a 0.96 gate."""
    # at the preregistered 0.005 edge a 0.63 held leg admits a 0.35 complement,
    # i.e. a basis of 0.98 -- looser than 0.96, deliberately
    r = V.max_complement_price(held_avg_cost="0.63", min_edge="0.005")
    basis = D("0.63") + D(r["ceiling"])
    assert basis > D("0.96")
    # 0.96 appears in the module only as PROSE quoting the ruling, never as a
    # value. Assert on the parsed module, not on the text.
    import ast
    tree = ast.parse(open(V.__file__).read())
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and not n.value.strip().startswith(("RISK", "A matched"))
                and len(n.value) < 12}
    assert "0.96" not in literals
    assert not any(isinstance(n, ast.Constant) and n.value == 0.96
                   for n in ast.walk(tree))
