"""A lane advances on what it can EVIDENCE, not on how promising it looks."""
import pytest

from src.l5_surface import evidence as EV, lane_gates as G, lanes as LN


def test_every_lane_declares_every_stage():
    """A stage nobody declared is a stage that would be silently assumed."""
    for lane in LN.LANES:
        assert set(G.SOURCES[lane]) == set(EV.STAGES), lane


def test_every_declared_source_has_a_known_status_and_a_reason():
    for lane in LN.LANES:
        for stage, src in G.SOURCES[lane].items():
            assert src["status"] in G.STATUSES, (lane, stage)
            assert src["why"], "%s/%s claims a status and no reason" % (lane,
                                                                        stage)


def test_an_unknown_lane_is_refused():
    with pytest.raises(ValueError):
        G.lane_report("L5_ADAPTED_SOMEWHERE_ELSE")


def test_a_bad_status_cannot_be_declared():
    with pytest.raises(ValueError):
        G._src("somewhere", "PROBABLY_FINE", "because")


# ------------------------------------------------- what each lane is missing

def test_pmus_is_blocked_at_trade_flow_and_everything_downstream():
    """The public gateway serves no print tape, and sharesTraded is a
    market-wide cumulative counter -- it cannot say a trade happened at OUR
    price, which is exactly what VOLUME_AHEAD_CLEARED requires."""
    r = G.lane_report(LN.PMUS_SPORTS)
    assert r["can_propose_canary"] is False
    assert r["canary_blocked_by"] == [EV.TRADE_FLOW, EV.EVIDENCED_FILL,
                                      EV.OPPOSITE_RECYCLING]
    # but the book IS readable, which is why the lane is worth running at all
    for stage in (EV.RESTABLE, EV.BOOK_REACHED):
        assert r["stages"][stage]["status"] == G.AVAILABLE


def test_gemini_can_evidence_every_stage_a_proposal_needs():
    r = G.lane_report(LN.GEMINI_CRYPTO)
    assert r["can_propose_canary"] is True
    assert r["canary_blocked_by"] == []
    assert r["stages"][EV.TRADE_FLOW]["status"] == G.AVAILABLE


def test_gemini_cannot_score_what_it_could_place():
    """The dangerous state: real money on a result nobody can read."""
    r = G.lane_report(LN.GEMINI_CRYPTO)
    assert r["can_score_canary"] is False
    assert r["scoring_blocked_by"] == [EV.SETTLED, EV.REALIZED]
    assert r["canary_ready"] is False


def test_proposing_without_scoring_is_not_ready():
    """canary_ready is the conjunction. A lane that can place and cannot
    settle is not one stage from done, it is spending money blind."""
    for lane in LN.LANES:
        r = G.lane_report(lane)
        assert r["canary_ready"] == (r["can_propose_canary"]
                                     and r["can_score_canary"])


def test_neither_lane_is_canary_ready_today():
    assert G.race()["any_ready"] is False


# --------------------------------------------------------------- the race

def test_the_race_is_scored_in_blocked_stages_not_in_promise():
    r = G.race()
    assert r["closest"] == LN.GEMINI_CRYPTO
    assert r["closest_blocked_by"] == [EV.SETTLED, EV.REALIZED]
    assert r["lanes"][LN.PMUS_SPORTS]["n_blocked"] > \
        r["lanes"][LN.GEMINI_CRYPTO]["n_blocked"]


def test_the_race_counts_scoring_blockers_too():
    """Ranking on proposal blockers alone would call Gemini ready."""
    r = G.race()
    assert r["lanes"][LN.GEMINI_CRYPTO]["n_blocked"] == 2


def test_nothing_here_authorizes_a_canary():
    joined = " ".join(G.lane_report(LN.GEMINI_CRYPTO)["labels"])
    assert "never that a canary is approved" in joined
    assert "separate human decision" in joined


def test_a_stage_may_not_be_passed_by_substituting_a_weaker_one():
    joined = " ".join(G.race()["labels"])
    assert "weakening the evidence rule" in joined
    assert "neither lane blocks the other" in joined


def test_the_blocker_reasons_name_the_measurement_not_a_guess():
    pm = G.SOURCES[LN.PMUS_SPORTS][EV.TRADE_FLOW]["why"]
    assert "404" in pm and "cumulative" in pm
    gm = G.SOURCES[LN.GEMINI_CRYPTO][EV.SETTLED]["why"]
    assert "publishes NO settlement value" in gm
