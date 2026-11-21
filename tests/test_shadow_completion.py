"""The shadow's zero completions are a missing component, not a market fact."""
from __future__ import annotations

from src import pmus_shadow_completion as SC
from src import pmus_sports_l5_decision as DEC

BOOK = {"bid": DEC.D("0.35"), "ask": DEC.D("0.355")}


def _acts(n_yes, px="0.35"):
    return [{"decision": "quote", "fill_class": "possible", "side": "YES",
             "target_price": px, "relation": "open" if i == 0 else "extend"}
            for i in range(n_yes)]


def test_the_frozen_model_never_proposes_complete_at_the_residual_cap():
    """The absorbing state, pinned. If this ever starts failing the policy
    changed and the two-arm split may no longer be needed."""
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    inv = DEC.Inventory()
    for _ in range(DEC.MAX_RESIDUAL_UNITS):
        inv.record(True, DEC.D("0.35"))
    for _ in range(5):
        prop = DEC._proposal(model, 0.35, inv, "extend")
        assert prop.get("recommended_relation") != "complete"
        plan = DEC.plan_action(prop["recommended_relation"], inv, BOOK)
        assert plan["decision"] == "no_action"
        assert str(plan["reason"]).startswith("residual_cap")


def test_arm_b_completes_exactly_where_arm_a_is_stuck():
    d = {"actions_replayed": _acts(3),
         "inventory_final": {"yes": 3, "no": 0, "matched": 0, "residual": 3}}
    out = SC.two_arm(d, BOOK)
    assert out[SC.ARM_A]["completions"] == 0
    assert out[SC.ARM_A]["stalled_at_residual_cap"] is False   # no cap action here
    b = out[SC.ARM_B]
    assert b["completion_would_post"] is True
    assert b["plan"]["relation"] == "complete"
    assert b["plan"]["side"] == "NO"


def test_arm_b_respects_the_pair_ceiling_it_did_not_set():
    """Arm B must not be a loophole: it calls the frozen policy's own
    COMPLETE branch, so the ceiling still binds."""
    d = {"actions_replayed": _acts(3),
         "inventory_final": {"yes": 3, "no": 0, "matched": 0, "residual": 3}}
    plan = SC.two_arm(d, BOOK)[SC.ARM_B]["plan"]
    assert DEC.D(plan["max_pair_cost"]) <= DEC.D("1.00")
    pair = DEC.D("0.35") + DEC.D(plan["target_price"])
    assert pair <= DEC.D("1.00")


def test_a_flat_replay_has_nothing_to_complete():
    d = {"actions_replayed": [],
         "inventory_final": {"yes": 0, "no": 0, "matched": 0, "residual": 0}}
    out = SC.two_arm(d, BOOK)
    assert out[SC.ARM_B]["completion_would_post"] is False


def test_reconstruction_that_disagrees_with_the_replay_refuses_to_complete():
    """An inventory we cannot confirm is never completed against."""
    d = {"actions_replayed": _acts(3),
         "inventory_final": {"yes": 9, "no": 0, "matched": 0, "residual": 9}}
    out = SC.two_arm(d, BOOK)
    assert out[SC.ARM_B]["completion_would_post"] is False
    assert "reconstruction_disagrees" in out[SC.ARM_B]["plan"]["reason"]


def test_an_action_without_a_price_refuses_rather_than_guessing():
    acts = [{"decision": "quote", "fill_class": "possible", "side": "YES"}]
    inv, why = SC._inventory_from_actions(acts, None)
    assert inv is None and why == "action_missing_side_or_price"


def test_unfilled_quotes_do_not_enter_the_inventory():
    acts = _acts(3) + [{"decision": "quote", "fill_class": "no_fill",
                        "side": "YES", "target_price": "0.35"}]
    inv, why = SC._inventory_from_actions(acts, {"residual": 3})
    assert why is None and inv.residual == 3


def test_the_two_arms_are_reported_separately_and_never_summed():
    d = {"actions_replayed": _acts(3),
         "inventory_final": {"yes": 3, "no": 0, "matched": 0, "residual": 3}}
    out = SC.two_arm(d, BOOK)
    assert SC.ARM_A in out and SC.ARM_B in out
    assert "never summed" in out["labels"]
    # no combined completion total anywhere in the payload
    assert not any(k.startswith("total") or k == "completions"
                   for k in out)


def test_no_order_entry_path():
    src = open("src/pmus_shadow_completion.py").read()
    for banned in ("submit", "place_order", "executor", "credential"):
        assert banned not in src.lower()
