"""The arm must change ONE branch and be pinned to production's real pricing."""
from decimal import Decimal as D

import pytest

from src import l5_ceiling_pinned_persist as cp
from src import pmus_spring_l5_session as S

TICK = D("0.01")


# ---------- pinned to production, not reimplemented

def test_the_mirrored_pricing_agrees_with_production_on_a_grid(monkeypatch):
    """A drift in spring_complement_price must turn this red rather than
    quietly invalidate every counterfactual built on the mirror."""
    monkeypatch.setattr(S, "complement_pricing", lambda: "l5_touch")
    monkeypatch.setattr(S, "completion_pair_ceiling", lambda: D("1.00"))
    checked = 0
    for avg in ("0.10", "0.3612", "0.46", "0.62", "0.88"):
        for touch in ("0.05", "0.25", "0.50", "0.66", "0.88", "0.95"):
            for opp in ("0.30", "0.55", "0.90", "0.99"):
                a, t, o = D(avg), D(touch), D(opp)
                prod_px, prod_info = S.spring_complement_price(a, o, TICK, t)
                mine, info = cp.ceiling_pinned_price(a, o, TICK, t)
                assert mine == prod_px, (avg, touch, opp, mine, prod_px)
                if prod_px is not None:
                    assert (info["capped_by_pair_ceiling"]
                            == prod_info["capped_by_pair_ceiling"])
                checked += 1
    assert checked >= 100


def test_the_mirrored_ceiling_matches_production(monkeypatch):
    monkeypatch.setattr(S, "completion_pair_ceiling", lambda: D("1.00"))
    assert cp.COMPLETION_PAIR_CEILING == S.completion_pair_ceiling()


def test_the_control_ladder_is_mirrored_in_production_order():
    """Reading order is load-bearing: would_cross must precede touch_moved,
    or holding could breach post-only."""
    src = S.spring_complement_price.__module__
    assert src
    body = open("src/pmus_spring_l5_session.py").read()
    i_cross = body.index('return f"would_cross:{opp_ask}"')
    i_touch = body.index('return f"touch_moved:{touch}"')
    assert i_cross < i_touch
    assert cp.LADDER.index("would_cross") < cp.LADDER.index("touch_moved")


# ---------- exactly one branch changes

def base(**kw):
    d = {"relation": "complete", "resting_px": D("0.54"), "touch": D("0.88"),
         "opp_ask": D("0.90"), "avg_cost": D("0.46"), "tick": TICK}
    d.update(kw)
    return d


def test_control_cancels_the_ceiling_pinned_order_and_the_arm_holds_it():
    assert cp.cancel_decision(cp.CONTROL, **base()) == "touch_moved:0.88"
    assert cp.cancel_decision(cp.ARM, **base()) is None


@pytest.mark.parametrize("arm", cp.ARMS)
def test_post_only_still_cancels_under_both_arms(arm):
    """would_cross runs first and is never suspended."""
    assert cp.cancel_decision(
        arm, **base(resting_px=D("0.90"), opp_ask=D("0.90"))
    ) == "would_cross:0.90"


@pytest.mark.parametrize("arm", cp.ARMS)
@pytest.mark.parametrize("kw,expected", [
    ({"book_readable": False}, "bbo_unreadable"),
    ({"book_two_sided": False}, "book_one_sided"),
])
def test_every_earlier_termination_still_fires_under_both_arms(arm, kw,
                                                               expected):
    assert cp.cancel_decision(arm, **base(**kw)) == expected


@pytest.mark.parametrize("arm", cp.ARMS)
def test_l5_action_changed_still_fires_when_the_touch_matches(arm):
    """With touch == px the touch_moved branch is silent in BOTH arms, so
    this proves the later branch is reachable and unmodified."""
    assert cp.cancel_decision(
        arm, **base(touch=D("0.54"), l5_relation_now="extend")
    ) == "l5_action_changed:extend"


def test_the_arm_holding_through_touch_moved_does_not_swallow_the_next_branch():
    """The whole point of "preserve until another existing termination
    condition fires": suppressing branch 6 must let branch 7 through."""
    kw = base(l5_relation_now="extend")
    assert cp.cancel_decision(cp.CONTROL, **kw) == "touch_moved:0.88"
    assert cp.cancel_decision(cp.ARM, **kw) == "l5_action_changed:extend"


@pytest.mark.parametrize("relation", ["open", "extend"])
def test_open_and_extend_are_untouched_by_the_arm(relation):
    kw = base(relation=relation)
    assert (cp.cancel_decision(cp.CONTROL, **kw)
            == cp.cancel_decision(cp.ARM, **kw) == "touch_moved:0.88")


def test_a_free_price_is_not_held_even_under_the_arm():
    """Only a CEILING-PINNED price qualifies. A price the engine chose
    freely is re-quoted exactly as today."""
    kw = base(resting_px=D("0.50"), touch=D("0.88"), avg_cost=D("0.10"))
    assert cp.arm_may_hold(kw["resting_px"], kw["avg_cost"], kw["opp_ask"],
                           TICK, kw["touch"], "complete")["hold"] is False
    assert cp.cancel_decision(cp.ARM, **kw) == "touch_moved:0.88"


def test_a_recompute_that_moves_the_price_falls_back_to_control():
    """Same order, but the post-only trim now binds below the pinned price."""
    kw = base(opp_ask=D("0.52"))
    v = cp.arm_may_hold(kw["resting_px"], kw["avg_cost"], kw["opp_ask"], TICK,
                        kw["touch"], "complete")
    assert v["hold"] is False and v["why"].startswith("recompute_moves_price")
    assert cp.cancel_decision(cp.ARM, **kw) == "would_cross:0.52" or \
        cp.cancel_decision(cp.ARM, **kw).startswith("touch_moved")


def test_a_filled_extend_moves_avg_cost_and_the_arm_defers_to_control():
    """avg_cost is an input to the pinned price, so inventory changing under
    a resting COMPLETE returns control to CONTROL with no special case."""
    assert cp.cancel_decision(cp.ARM, **base()) is None
    assert cp.cancel_decision(cp.ARM, **base(avg_cost=D("0.40"))) \
        == "touch_moved:0.88"


def test_the_arm_cannot_hold_without_an_average_cost():
    assert cp.cancel_decision(cp.ARM, **base(avg_cost=None)) \
        == "touch_moved:0.88"


def test_holding_can_never_raise_the_pair_above_the_ceiling():
    for avg in ("0.10", "0.46", "0.80", "0.99"):
        a = D(avg)
        px, info = cp.ceiling_pinned_price(a, D("0.99"), TICK, D("0.95"))
        if px is not None:
            assert a + px <= cp.COMPLETION_PAIR_CEILING


# ---------- no fill is ever invented

def order(px="0.54", touch="0.88", filled="0", rest="2.0",
          end="repriced:touch_moved:0.88", polls=1):
    return {"px": px, "l5_touch_px": touch, "filled_qty": filled,
            "rest_s": rest, "end_reason": end,
            "rest_book": {"polls": polls, "min_opp_ask": "0.90",
                          "max_touch": touch}}


def test_a_chain_with_no_observed_fill_is_never_classified_as_a_fill():
    c = cp.classify_chain([order(), order(), order()])
    assert c["class"] != cp.OBSERVED_FILL
    assert c["observed_fill_qty"] == "0"
    assert c["reposts_eliminated"] == 2


def test_a_merged_order_outliving_its_twins_is_unknown_not_no_fill():
    """The gaps between cancel and repost carry no observation."""
    assert cp.classify_chain([order(), order()])["class"] \
        == cp.COUNTERFACTUAL_FILL_UNKNOWN


def test_a_single_uncancelled_attempt_is_an_honest_observed_no_fill():
    assert cp.classify_chain([order(end="deadline")])["class"] \
        == cp.OBSERVED_NO_FILL


def test_an_observed_fill_in_the_chain_is_reported_as_observed():
    c = cp.classify_chain([order(), order(filled="1.2", end="filled")])
    assert c["class"] == cp.OBSERVED_FILL
    assert c["observed_fill_qty"] == "1.2"


def test_every_class_is_one_of_the_three_required_labels():
    for ch in ([order()], [order(), order()],
               [order(filled="1", end="filled")]):
        assert cp.classify_chain(ch)["class"] in cp.FILL_CLASSES


# ---------- chaining only merges what the arm would merge

def test_only_ceiling_pinned_touch_moved_attempts_are_merged():
    free = order(px="0.88", touch="0.88")          # at the touch, not pinned
    chains = cp.chain_orders([order(), order(), free, order()], TICK)
    assert [len(c) for c in chains] == [2, 1]


def test_a_price_change_breaks_the_chain():
    chains = cp.chain_orders([order(), order(px="0.53"), order(px="0.53")],
                             TICK)
    assert [len(c) for c in chains] == [1, 2]


def test_an_attempt_that_ended_another_way_closes_the_chain():
    chains = cp.chain_orders([order(), order(end="deadline"), order()], TICK)
    assert [len(c) for c in chains] == [2, 1]


def test_is_ceiling_pinned_is_none_not_false_without_a_touch():
    o = order()
    o.pop("l5_touch_px")
    assert cp.is_ceiling_pinned(o, TICK)[0] is None


def test_the_exact_route_uses_avg_cost_and_ignores_the_touch_entirely():
    """floor((ceiling - avg_cost)/tick)*tick involves no touch, so the
    verdict cannot be contaminated by which touch was recorded."""
    o = order(px="0.54")
    v, prov = cp.is_ceiling_pinned(o, TICK, avg_cost=D("0.46"))
    assert v is True and prov == "recomputed_from_avg_cost"
    v2, _ = cp.is_ceiling_pinned(order(px="0.54", touch="0.54"), TICK,
                                 avg_cost=D("0.46"))
    assert v2 is True          # same verdict with a completely different touch


def test_a_free_price_is_not_pinned_on_the_exact_route():
    assert cp.is_ceiling_pinned(order(px="0.30"), TICK,
                                avg_cost=D("0.46"))[0] is False


def test_the_weak_route_is_labelled_as_derived_from_the_POST_TIME_touch():
    _v, prov = cp.is_ceiling_pinned(order(), TICK)
    assert prov == "derived_from_post_time_touch"


# ---------- horizons come from observation, not assumption

def test_an_unobserved_boundary_is_reported_unknown():
    h = cp.horizon_at_natural_boundaries([order(), order()], deadline_s=30.0)
    assert h["boundary"] == "UNKNOWN"


def test_a_boundary_the_tape_shows_is_named():
    h = cp.horizon_at_natural_boundaries([order(end="deadline")],
                                         deadline_s=30.0)
    assert h["boundary"] == "deadline"


def test_would_cross_is_detected_from_the_rest_book():
    o = order()
    o["rest_book"]["min_opp_ask"] = "0.54"
    assert cp.horizon_at_natural_boundaries([o], 30.0)["boundary"] \
        == "would_cross"


# ---------- the arm may not claim reposts the deadline still forces

def test_the_deadline_still_splits_a_long_chain_into_several_orders():
    """The arm suppresses touch_moved; it does NOT extend the rest bound, so
    a merged order still dies at the model's next re-evaluation."""
    chain = [order(rest="2.0") for _ in range(23)]      # 46s of observed rest
    seg = cp.segment_chain_by_deadline(chain, deadline_s=6.0)
    assert seg["orders_under_arm"] > 1
    assert seg["reposts_eliminated"] < seg["reposts_eliminated_naive"]
    assert seg["deadline_expiries_inside_the_chain"] >= 7


def test_a_chain_inside_one_deadline_window_really_is_one_order():
    seg = cp.segment_chain_by_deadline([order(rest="1.0"), order(rest="1.0")],
                                       deadline_s=30.0)
    assert seg["orders_under_arm"] == 1
    assert seg["reposts_eliminated"] == 1


def test_attempts_without_a_rest_time_are_counted_not_silently_dropped():
    c = [order(rest=None), order(rest="1.0")]
    c[0].pop("rest_s")
    seg = cp.segment_chain_by_deadline(c, deadline_s=30.0)
    assert seg["attempts_with_no_rest_time"] == 1


# ---------- it reads and never acts

def test_it_reads_and_never_acts():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(cp))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    code = ast.unparse(tree)
    for tok in ("place_limit", "submit", "cancel_order", "post_only=False",
                "requests.", "httpx", "os.environ", "open("):
        assert tok not in code, tok


def test_production_does_not_import_the_arm():
    body = open("src/pmus_spring_l5_session.py").read()
    assert "l5_ceiling_pinned_persist" not in body
