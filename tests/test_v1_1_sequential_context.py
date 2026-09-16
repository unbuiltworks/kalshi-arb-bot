"""The context defect, and the proof that V1.1 no longer has it.

V1 called the v2 transition model with `last_rel`/`last_size` left at their
defaults on every row, so every lookup was `(open, q<5)`: 40 of 988 full keys
and 202 of 69,646 transitions -- 0.29% of L5's evidence. These fixtures assert
that a SEQUENCE of actions now produces a SEQUENCE of different lookup keys,
which is the whole content of the fix.
"""
from __future__ import annotations

import pytest

from src import l5_v1_1_shadow as V11
from src import l5_v1_shadow as V1
from src import pmus_l5_persistence_v2 as pv2

pytestmark = pytest.mark.real_mandate_env

BOOK = {"bid": 0.35, "ask": 0.36}


# --------------------------------------------------------------------------
# the context itself

def test_a_fresh_context_starts_at_the_old_frozen_defaults():
    """Not a regression -- it is the honest starting point. Before we have
    acted once there IS no previous action, and (open, q<5) is what the model
    means by that."""
    ctx = V11.SequentialContext()
    assert ctx.get("mkt") == ("open", "q<5")
    assert ctx.since("mkt", 100.0) is None


def test_a_sequence_of_actions_produces_a_sequence_of_different_lookup_keys():
    """THE FIX, stated as a property. OPEN -> EXTEND -> COMPLETE must give
    three different (last_rel, last_size) pairs and therefore three different
    full keys."""
    ctx = V11.SequentialContext()
    seen = []
    for rel, qty, t in (("open", 3.0, 100.0), ("extend", 12.0, 110.0),
                        ("complete", 30.0, 140.0)):
        seen.append(ctx.get("mkt"))
        ctx.record("mkt", rel, qty, t)
    seen.append(ctx.get("mkt"))
    assert seen == [("open", "q<5"), ("open", "q<5"),
                    ("extend", "q5-15"), ("complete", "q15-40")]
    assert len(set(seen)) == 3          # the first two coincide only at boot


def test_elapsed_since_the_last_action_is_tracked_per_market():
    ctx = V11.SequentialContext()
    ctx.record("a", "open", 5.0, 1000.0)
    ctx.record("b", "extend", 5.0, 1010.0)
    assert ctx.since("a", 1030.0) == 30.0
    assert ctx.since("b", 1030.0) == 20.0
    assert ctx.since("c", 1030.0) is None      # never acted here


def test_size_is_bucketed_in_l5_share_units_not_our_scaled_contracts():
    """A 0.06-contract action is ~9.7 L5 shares at k=0.00619. Bucketing the
    0.06 would put every action we can afford in `q<5` forever, which is the
    same class of error as the state-key lift."""
    ctx = V11.SequentialContext()
    ctx.record("m", "extend", 0.06 / V1.RISK_SCALE_K, 1.0)
    assert ctx.get("m") == ("extend", "q5-15")
    ctx.record("m", "extend", 0.06, 2.0)        # NOT lifted -> wrong bucket
    assert ctx.get("m") == ("extend", "q<5")


# --------------------------------------------------------------------------
# it actually reaches more of the model

def test_real_context_reaches_full_keys_the_frozen_default_cannot():
    """Counted against the deployed artifact, not asserted in prose."""
    m = pv2.load_v2()
    full = m["full"]
    default = {k for k in full if k.split("|")[3:5] == ["open", "q<5"]}
    assert len(default) == 40, len(default)
    assert len(full) == 988
    reachable_ctx = {tuple(k.split("|")[3:5]) for k in full}
    assert len(reachable_ctx) == 12         # 3 relations x 4 size buckets
    # the frozen default is one of twelve contexts, and the smallest few
    assert ("open", "q<5") in reachable_ctx


def test_v1_observe_still_defaults_to_the_old_behaviour_for_the_control():
    """The running V1 shadow is the control for the V1.1 comparison. Threading
    the parameters must not change what V1 does when nobody passes them."""
    import inspect
    sig = inspect.signature(V1.observe)
    assert sig.parameters["last_rel"].default == "open"
    assert sig.parameters["last_size"].default == "q<5"


# --------------------------------------------------------------------------
# stage 1 gates stage 2, and the two reasons never merge

def test_stage_one_can_veto_an_action_stage_two_wanted():
    art = {"act": {"10": {"threshold_from_train_only": 0.9,
                          "marginal": [900, 100], "full": {}, "base": {}}},
           "timing": {"marginal": {"gap>30": 10}, "full": {}, "base": {}}}
    r = V11.act_probability(art, state="s", last_rel="open", last_size="q<5",
                            since_last_s=40.0, horizon_s=10)
    assert r["tier"] == "marginal" and r["p_act"] == pytest.approx(0.1)
    assert r["p_act"] < r["threshold"]          # -> WAIT


def test_a_missing_act_artifact_waits_loudly_rather_than_acting():
    """Fail closed: no Stage 1 means no ACT, never a silent fallthrough to the
    transition model's always-act answer."""
    r = V11.act_probability(None, state="s", last_rel="open", last_size="q<5",
                            since_last_s=1.0)
    assert r["available"] is False and r["p_act"] is None
    assert r["reason"] == "act_artifact_missing"


def test_the_three_stages_are_reported_separately_and_never_summed():
    src = open(V11.__file__, encoding="utf-8").read()
    for field in ("p_act", "conditional_action_probs", "predicted_gap_dist"):
        assert f'"{field}"' in src
    # no combined score anywhere
    for banned in ("combined_probability", "overall_score", "p_total"):
        assert banned not in src


def test_the_module_cannot_trade():
    src = open(V11.__file__, encoding="utf-8").read()
    for bad in ("place_limit_buy", "place_order", "submit_order", "Transport",
                "requests", "urllib", "PMUS_API_KEY_ID", "has_credentials",
                "/v1/orders"):
        assert bad not in src, bad


def test_the_frozen_v2_artifact_is_loaded_read_only_and_not_rewritten():
    """Scans CODE, not prose. The module's docstring names the frozen artifact
    precisely to say it is not touched, and a substring check over the whole
    file reads that explanation as a violation."""
    import re
    src = open(V11.__file__, encoding="utf-8").read()
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)
    assert "l5_action_model_v2" not in code       # never opened here
    assert 'open(path, encoding="utf-8")' in code  # the act artifact, read mode
    assert '"w"' not in code and "json.dump(" not in code


# --------------------------------------------------------------------------
# timing stage

def test_timing_maps_gap_buckets_to_the_four_way_decision():
    assert V11.GAP_TO_DECISION["gap<2s"] == V11.ACT_NOW
    assert V11.GAP_TO_DECISION["gap2-10"] == V11.WAIT_SHORT
    assert V11.GAP_TO_DECISION["gap10-30"] == V11.WAIT_MEDIUM
    assert V11.GAP_TO_DECISION["gap>30"] == V11.WAIT_LONG
    assert len(set(V11.GAP_TO_DECISION.values())) == 4


def test_timing_returns_a_distribution_not_just_a_label():
    art = V11.load_act_artifact()
    if not art:
        pytest.skip("act artifact not built")
    t = V11.timing_prediction(art, state="p25-45c|tte>10m|flat",
                              last_rel="extend", last_size="q5-15",
                              since_last_s=5.0)
    assert t["available"] and t["modal_gap"] in dict(V11.GAP_BUCKETS)
    assert abs(sum(t["dist"].values()) - 1.0) < 0.02
    assert t["tier"] in ("full", "base", "marginal")
