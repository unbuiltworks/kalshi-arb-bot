"""Regressions for the SHADOW-ONLY frozen_key x side_streak arm.

Every test here exists because getting the thing wrong would have produced
a plausible-looking number. The comparison is only worth anything if the
shadow arm differs from production in exactly ONE way.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import l5_streak_shadow as ss
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------ the streak definition
def test_an_unknown_run_length_is_not_zero():
    """0 means "flat, nothing has filled". None means "we cannot tell".
    Calling an unknown run zero conditions the model on a streak state L5
    never occupies -- in the canonical sequence every transition follows a
    fill, so s0 does not exist there at all."""
    assert ss.streak_bucket(None) == "unknown"
    assert ss.streak_bucket(0) == "s0"
    assert ss.streak_bucket(1) == "s1"
    assert ss.streak_bucket(4) == "s4+"
    assert ss.streak_bucket(97) == "s4+"


def test_an_unfilled_order_does_not_extend_a_run():
    """THE REGRESSION. A decision is not a fill. Counting decisions would
    have made every quiet market look like a long same-side streak."""
    d = [{"inventory_before": {"up": "0", "dn": "0"}},          # flat
         {"inventory_before": {"up": "2", "dn": "0"}},          # up filled
         {"inventory_before": {"up": "2", "dn": "0"}},          # no fill
         {"inventory_before": {"up": "4", "dn": "0"}},          # up again
         {"inventory_before": {"up": "4", "dn": "3"}}]          # side change
    # a fill shows up in the NEXT decision's inventory_before, so the run
    # is 1 from d1 onwards; index 2 stays 1 because nothing filled there
    assert ss.live_streaks(d) == [0, 1, 1, 2, 1]


def test_a_campaign_that_starts_holding_inventory_has_an_unknown_run():
    """We know what it holds, not the order it bought it in."""
    d = [{"inventory_before": {"up": "5", "dn": "1"}},
         {"inventory_before": {"up": "7", "dn": "1"}}]
    assert ss.live_streaks(d) == [None, 1]   # unknown, then one up fill


def test_both_sides_moving_between_observations_resets_to_unknown():
    """Two fills on opposite sides inside one gap: the ORDER is not
    recoverable, so guessing either would invent evidence."""
    d = [{"inventory_before": {"up": "0", "dn": "0"}},
         {"inventory_before": {"up": "2", "dn": "0"}},
         {"inventory_before": {"up": "4", "dn": "2"}},
         {"inventory_before": {"up": "6", "dn": "2"}}]
    assert ss.live_streaks(d) == [0, 1, None, 1]


# ------------------------------------------------------- campaign grouping
def test_campaigns_are_not_chopped_by_the_engines_log_chunking():
    """THE REGRESSION. The engine writes a fresh session_id every five
    decisions while decision_idx keeps climbing. Grouping on session_id
    reset the streak at each boundary and reported 85% of live decisions as
    'no fill yet' when most of them followed one."""
    ds = [{"slug": "m", "session_id": f"chunk{i // 5}", "decision_idx": i,
           "t_utc": f"2026-09-13T00:{i:02d}:00Z"} for i in range(12)]
    g = ss.group_campaigns(ds)
    assert len(g) == 1
    assert [d["decision_idx"] for d in next(iter(g.values()))] == list(range(12))


def test_a_restarted_decision_index_does_start_a_new_campaign():
    ds = [{"slug": "m", "decision_idx": i, "t_utc": f"2026-09-13T00:0{i}:00Z"}
          for i in (0, 1, 2)]
    ds += [{"slug": "m", "decision_idx": i, "t_utc": f"2026-09-13T01:0{i}:00Z"}
           for i in (0, 1)]
    assert len(ss.group_campaigns(ds)) == 2


# ------------------------------------------------------------- the draw
@pytest.mark.parametrize("masses", [
    {"complete": 0.5, "extend": 0.4, "open": 0.1},
    {"complete": 0.0, "extend": 1.0, "open": 0.0},
    {"complete": 0.33, "extend": 0.33, "open": 0.34},
])
def test_our_draw_is_the_engines_draw(masses):
    """The whole comparison rests on both arms sharing one coin. If this
    ever diverges, differences between arms stop being differences between
    probabilities."""
    ranked = ss.ranked_from_masses(masses)
    for idx in range(40):
        seed = fid.seed_material("slug-x", idx, "modelsha")
        prod = fid.sample_relation(ranked, seed)
        u = prod["u"]
        assert ss.inverse_cdf(ranked, u) == prod["sampled"]


def test_the_draw_respects_alphabetical_ordering_not_mass_ordering():
    """Production walks sorted(rows) -- alphabetical by relation. Sorting
    by mass instead would move the outcome for the same u, silently."""
    ranked = ss.ranked_from_masses(
        {"complete": 0.2, "extend": 0.3, "open": 0.5})
    # alphabetical: complete [0,0.2), extend [0.2,0.5), open [0.5,1.0)
    assert ss.inverse_cdf(ranked, 0.1) == "complete"
    assert ss.inverse_cdf(ranked, 0.4) == "extend"
    assert ss.inverse_cdf(ranked, 0.9) == "open"


# ------------------------------------------------ the one-difference rule
def _toy_model():
    return {"marginal": {"complete|q<5": 60, "extend|q<5": 40},
            "base": {"B": {"complete|q<5": 30, "extend|q<5": 30}},
            "full": {"B|open|q<5": {"complete|q<5": 20, "extend|q<5": 20}},
            "streak": {"B|open|q<5|s2": {"extend|q<5": 200}}}


def test_a_streak_cell_with_no_evidence_returns_production_exactly():
    """The backoff must END at production. If an unseen streak bucket
    changed the distribution at all, every comparison would be measuring
    the backoff and not the variable."""
    m = _toy_model()
    frozen = ss.frozen_dist(m, "B", "open", "q<5")
    shadow = ss.streak_dist(m, "B", "open", "q<5", "s9-never-seen")
    assert shadow["dist"] == frozen["dist"]
    assert shadow["backed_off"] is True


def test_a_streak_cell_with_evidence_moves_the_distribution():
    """...and the test is not rigged to always say 'no change'."""
    m = _toy_model()
    frozen = ss.relation_masses(ss.frozen_dist(m, "B", "open", "q<5")["dist"])
    shadow = ss.relation_masses(
        ss.streak_dist(m, "B", "open", "q<5", "s2")["dist"])
    assert shadow["extend"] > frozen["extend"] + 0.2
    assert abs(sum(shadow.values()) - 1.0) < 1e-9


def test_relation_masses_reports_an_absent_relation_as_zero():
    """A missing key and a zero mass are not the same thing to a scorer;
    one of them crashes or silently renormalises."""
    out = ss.relation_masses({"extend|q<5": 1.0})
    assert out == {"complete": 0.0, "extend": 1.0, "open": 0.0}
    assert set(out) == set(ss.RELATIONS)


# ---------------------------------------- the shadow must not drift away
@pytest.mark.skipif(not ss.ARTIFACT.exists(),
                    reason="shadow artifact not built in this checkout")
def test_the_shadow_artifact_reproduces_production_counts_exactly():
    """The streak artifact is built by a LOCAL walk, deliberately not by
    editing the module the live path imports. That freedom is only safe
    while the shared part of the key still lands on production's own
    counts, byte for byte."""
    shadow = ss.load()
    prod = json.loads(pv2.ARTIFACT.read_text())
    assert shadow["full"] == prod["full"]
    assert shadow["base"] == prod["base"]
    assert shadow["marginal"] == prod["marginal"]


def test_the_shadow_is_not_imported_by_any_armed_path():
    """SHADOW ONLY is a property of the import graph, not of a comment."""
    armed = ["src/pmus_spring_l5_session.py", "src/pmus_l5_continuous_session.py",
             "src/pmus_sports_l5_decision.py", "src/pmus_l5_persistence_v2.py",
             "src/pmus_l5_fidelity_live.py"]
    for rel in armed:
        p = ROOT / rel
        if p.exists():
            assert "l5_streak_shadow" not in p.read_text(), rel
