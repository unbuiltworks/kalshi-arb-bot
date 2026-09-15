"""The state-driven candidate, and the ways a model candidate becomes a claim.

The hypothesis: L5's public activity shows when he FILLED, not when he PLACED.
If he keeps eligible quotes on both outcomes and waits, a clone that quotes the
complement only after his next fill is published is not running his policy — it
chases each leg after the flow that would have filled it has gone. That would
produce naked residuals from a perfectly reasonable behavioural model, which is
what we observe.

Arm S makes that refutable. These tests pin the two things that would make it
worthless instead: buying pairs with the edge, and quietly becoming a claim
about L5 rather than a candidate.
"""
from decimal import Decimal as D

import pytest

import analysis.l5.pair_formation.arms as A
from src.l5_intent.action import DesiredInventoryAction
from src.l5_shadow import terms as T
from src.l5_shadow import two_sided as TS


def _intent(qty="10", ceiling="0.50", outcome="Up"):
    return DesiredInventoryAction(
        cohort="BTC15m", market_key="m", source_state_id="s", t_utc=0.0,
        tte_s=100.0, action="OPEN", desired_outcome=outcome,
        desired_inventory=qty, incremental_qty=qty,
        max_acceptable_price=ceiling, current_matched="0", residual="0",
        model_version="test")


def _plan(own, opp, *, terms=None, qty="10", outcome="Up"):
    return TS.two_sided_plan(_intent(qty, outcome=outcome),
                             terms or T.gemini_terms(),
                             own_rest=own, opp_rest=opp, venue="GEMINI",
                             now=1.0)


# ------------------------------------------- it refuses rather than pays

def test_a_pair_inside_the_fee_safe_target_is_admissible():
    r = _plan([(D("0.45"), D("100"))], [(D("0.49"), D("80"))])
    assert r["admissible"] is True
    assert r["pair_cost"] == "0.950000"
    assert len(r["legs"]) == 2


def test_a_pair_above_the_target_is_refused_not_repriced():
    """The one thing that would make this arm worthless: buying pairs with the
    edge we are trying to keep."""
    r = _plan([(D("0.50"), D("100"))], [(D("0.49"), D("80"))])
    assert r["admissible"] is False
    assert "forbidden" in r["reason"]
    assert "0.96" in r["reason"]


def test_the_target_is_the_spring_discipline_number():
    assert TS.PAIR_TARGET == D("0.96")


def test_neither_leg_ever_crosses_the_spread():
    """Each leg joins the bid. Nothing here lifts an offer."""
    r = _plan([(D("0.45"), D("100"))], [(D("0.49"), D("80"))])
    assert r["legs"][0]["price"] == "0.45"
    assert r["legs"][1]["price"] == "0.49"


def test_fees_are_charged_into_the_pair_cost_not_ignored():
    """A pair that is only cheap before fees is not a fee-safe pair."""
    r = _plan([(D("0.48"), D("100"))], [(D("0.48"), D("80"))])
    assert D(r["pair_cost"]) > D("0.96")
    assert r["admissible"] is False


def test_polymarket_pays_no_maker_fee_so_its_pair_cost_is_the_two_touches():
    w = {"fee_rate": 0.07, "fee_taker_only": True, "order_min_size": 5,
         "tick": "0.01", "terms_source": "t"}
    r = _plan([(D("0.45"), D("100"))], [(D("0.50"), D("80"))],
              terms=T.pm_terms(w))
    assert r["pair_cost"] == "0.950000"


# ----------------------------------- an absent side is a venue fact, not ours

def test_no_complement_book_is_reported_as_the_venue_not_hosting_it():
    r = _plan([(D("0.45"), D("100"))], [])
    assert r["admissible"] is False
    assert "cannot host a two-sided pair" in r["reason"]


def test_an_unreadable_complement_book_is_unknown_never_empty():
    r = _plan([(D("0.45"), D("100"))], None)
    assert r["admissible"] is False
    assert "UNKNOWN" in r["reason"]
    assert "not an absent side" in r["reason"]


def test_undeclared_venue_terms_refuse_rather_than_assume():
    w = {"fee_rate": None, "fee_taker_only": None, "tick": None,
         "terms_source": "t"}
    r = _plan([(D("0.45"), D("100"))], [(D("0.49"), D("80"))],
              terms=T.pm_terms(w))
    assert r["admissible"] is False
    assert "has not defined" in r["reason"]


def test_the_queue_ahead_of_each_leg_is_recorded():
    r = _plan([(D("0.45"), D("100")), (D("0.45"), D("20"))],
              [(D("0.49"), D("80"))])
    assert r["legs"][0]["queue_ahead"] == "120"
    assert r["legs"][1]["queue_ahead"] == "80"


# ------------------------------------ it stays a candidate, not a claim

def test_every_plan_is_labelled_a_model_candidate():
    r = _plan([(D("0.45"), D("100"))], [(D("0.49"), D("80"))])
    assert r["label"] == "MODEL_CANDIDATE/state_driven_two_sided"
    assert r["arm"] == "S"


def test_the_comparison_names_the_refutation_condition():
    r = A.report("/nonexistent")
    assert "MODEL_CANDIDATE" in r["arm_S_label"]
    assert "never a claim about L5" in r["labels"]


def test_a_result_that_refutes_the_hypothesis_is_reported_as_plainly():
    v = A._verdict({"pair_rate": 0.4}, {"pair_rate": 0.1})
    assert v["decided"] is True
    assert v["supports_continuous_inventory_hypothesis"] is False
    assert v["delta"] == -0.3
    assert "REFUTES" in v["why"]


def test_a_missing_pair_rate_is_not_a_zero():
    assert A._verdict({"pair_rate": None}, {"pair_rate": 0.5})["decided"] \
        is False


# --------------------------------------------- the arms are scored apart

def test_the_two_arms_read_different_stores():
    """Arm S's quotes are a different policy's. Letting them into
    maker_outcomes.jsonl would rewrite the reactive clone's measured economics
    with results it never produced."""
    assert A.R_OUT == "maker_outcomes.jsonl"
    assert A.S_OUT == "two_sided_outcomes.jsonl"
    assert A.R_OUT != A.S_OUT


def test_the_service_writes_arm_s_verdicts_to_the_separate_store(tmp_path,
                                                                 monkeypatch):
    import json

    import src.l5_shadow.service as S
    import src.l5_shadow.venues as V
    monkeypatch.setattr(V, "pm_trades", lambda *a, **k: [])
    for arm, store in (("R", S.MAKER_OUT), ("S", S.TWO_SIDED_OUT)):
        S.resolve_maker(str(tmp_path),
                        {"venue": "PMUS", "intent_id": f"i{arm}", "arm": arm,
                         "price": "0.45", "qty": "10", "queue_ahead": "0",
                         "placed_at": 1.0, "condition_id": "c"},
                        now=400.0)
        rows = [json.loads(l) for l in
                (tmp_path / store).read_text().splitlines() if l.strip()]
        assert [r["intent_id"] for r in rows] == [f"i{arm}"]
        assert rows[0]["arm"] == arm


def test_a_pending_quote_with_no_arm_defaults_to_the_reactive_store(
        tmp_path, monkeypatch):
    """Every quote written before arm S existed is arm R's."""
    import json

    import src.l5_shadow.service as S
    import src.l5_shadow.venues as V
    monkeypatch.setattr(V, "pm_trades", lambda *a, **k: [])
    S.resolve_maker(str(tmp_path),
                    {"venue": "PMUS", "intent_id": "i", "price": "0.45",
                     "qty": "10", "queue_ahead": "0", "placed_at": 1.0,
                     "condition_id": "c"}, now=400.0)
    rows = [json.loads(l) for l in
            (tmp_path / S.MAKER_OUT).read_text().splitlines() if l.strip()]
    assert rows[0]["arm"] == "R"
    assert not (tmp_path / S.TWO_SIDED_OUT).exists()


# ------------------------------------------------------- the pair arithmetic

def test_a_market_with_both_sides_filled_counts_as_paired():
    r = A.arm_pairs([
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Up",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.45"},
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Down",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.50"}])
    assert r["paired"] == 1 and r["one_sided"] == 0
    assert r["pair_rate"] == 1.0
    assert r["median_effective_pair_cost"] == "0.95"


def test_one_side_filled_twice_is_not_a_pair():
    r = A.arm_pairs([
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Up",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.45"},
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Up",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.46"}])
    assert r["paired"] == 0 and r["one_sided"] == 1


def test_an_unfilled_quote_contributes_nothing():
    r = A.arm_pairs([
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Up",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.45"},
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Down",
         "evidenced_fill": False, "quote_qty": "10", "quote_price": "0.50"}])
    assert r["paired"] == 0 and r["one_sided"] == 1


def test_availability_separates_a_refused_pair_from_an_absent_book():
    """'Too expensive' and 'the venue has no other side' call for different
    work, and only one of them is a transfer failure."""
    a = A.availability([
        {"venue": "GEMINI", "admissible": False,
         "reason": "the two touches sum to 1.02, above the fee-safe target 0.96"},
        {"venue": "GEMINI", "admissible": False,
         "reason": "no bid to join on the opp side: the venue cannot host a "
                   "two-sided pair here at any price"},
        {"venue": "GEMINI", "admissible": True, "pair_cost": "0.94"}])
    g = a["GEMINI"]
    assert g["planned"] == 3 and g["admissible"] == 1
    assert g["refused_pair_too_expensive"] == 1
    assert g["no_complement_book"] == 1
    assert g["admissible_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert g["median_admissible_pair_cost"] == "0.94"


def test_the_distribution_of_refused_pair_costs_is_reported_not_just_the_verdict():
    """Whether a pair cleared says yes or no. How far from the target the
    venue sits says whether the target is attainable here at all — and a first
    live sample priced Polymarket at 0.9930 against a 0.96 target."""
    a = A.availability([
        {"venue": "PMUS", "admissible": False, "pair_cost": "0.993",
         "reason": "above the fee-safe target"},
        {"venue": "PMUS", "admissible": False, "pair_cost": "0.985",
         "reason": "above the fee-safe target"},
        {"venue": "PMUS", "admissible": True, "pair_cost": "0.950"}])
    d = a["PMUS"]["pair_cost_all_priced"]
    assert d["median"] == "0.985" and d["max"] == "0.993"
    assert d["under_1_00"] == 3
    assert a["PMUS"]["priced_pairs"] == 3
    assert "binding constraint is the venue" in d["reads"]


def test_a_plan_that_could_not_be_priced_contributes_no_cost():
    a = A.availability([{"venue": "GEMINI", "admissible": False,
                         "reason": "the venue cannot host a two-sided pair"}])
    assert a["GEMINI"]["pair_cost_all_priced"] is None
    assert a["GEMINI"]["priced_pairs"] == 0


def test_forming_a_pair_is_not_the_same_as_forming_a_good_one():
    """Arm R's first two live pairs came in at a median 1.43 — a guaranteed
    43c-per-share loss on the matched leg. It has no pair-cost constraint at
    all: each leg is quoted against its own ceiling, never against the sum."""
    r = A.arm_pairs([
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Up",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.50"},
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Down",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.93"}])
    assert r["paired"] == 1
    assert r["median_effective_pair_cost"] == "1.43"
    assert r["pairs_above_par"] == 1 and r["pairs_within_0_96"] == 0
    assert "guaranteed loss" in r["pair_quality_note"]


def test_a_rate_bought_with_above_par_pairs_is_not_called_an_improvement():
    v = A._verdict({"pair_rate": 0.1, "pairs_above_par": 0},
                   {"pair_rate": 0.5, "pairs_above_par": 9})
    assert v["supports_continuous_inventory_hypothesis"] is True
    assert v["arm_S_pairs_above_par"] == 9
    assert "not an improvement" in v["note"]


def test_a_good_pair_is_counted_as_within_the_target():
    r = A.arm_pairs([
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Up",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.45"},
        {"venue": "PMUS", "market_key": "m", "desired_outcome": "Down",
         "evidenced_fill": True, "quote_qty": "10", "quote_price": "0.50"}])
    assert r["pairs_above_par"] == 0 and r["pairs_within_0_96"] == 1
