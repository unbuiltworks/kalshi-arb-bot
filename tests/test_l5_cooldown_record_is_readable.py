"""The cooldown record must answer the question it was built to answer.

THE DEFECT. `cooldown_observation` exists because, in its own words, "every
cooldown record carried `pair: None` and `executable_evidence: false` ... it
made the one question worth asking -- did the cadence decline a completion
that was actually available -- unanswerable from the logs in either
direction." It prices the book, the complement and the conditional pair
correctly. It then hands them to `l5_missed_pair_blocker.record`, which read
DIFFERENT KEYS:

    writer  decide() -> {"book": {"stream_age_s", "source"},
                         "pair_if": {"at_touch", ...}}
    reader  record() -> dec["stream_age_s"], dec["pair_own_cost"],
                        book_source=<kwarg the DECISION caller never passed>

So every DECISION-stage record came out `pair.known false`,
`missed_profitable_pair UNKNOWN`, `basis EVIDENCE_BASIS_UNKNOWN`, "book
source not stated" -- while the book was authenticated and current.

PRODUCTION CONFIRMATION, 2026-11-05 window 2230z: all six DECIDE_COOLDOWN
records reported UNKNOWN while holding UP 3. That is why the cooldown's cost
could not be established, and why no cadence change is justified by it yet.
The instrument is fixed first; a behaviour change needs the evidence this
now produces.

REPORTING ONLY. Nothing gates on this record, and the fallbacks can only
make it say more than "unknown".
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_book_completion as BC
from src import l5_missed_pair_blocker as MPB


MARKET = "cpc-btc-updown-15m-2026-11-05-2230z"
#: an authenticated, current, two-sided book -- the one the cadence declined
#: against in production.
BOOK = {"bid": "0.4300", "ask": "0.4400", "stream_age_s": "0.076",
        "source": "authenticated_ws", "bid_shares": "500",
        "ask_shares": "500"}


def _cooldown_decision():
    """`decide` returning DECIDE_COOLDOWN, exactly as production produced it."""
    rec = BC.decide(
        market=MARKET, held_side="UP", held_qty="3", held_cost_usd="1.29",
        deficient_qty="2", deficient_cost_usd="1.07",
        book=BOOK, now_t=1000.0, window_start=600.0,
        last_decide_t=999.0,          # inside DECIDE_EVERY_S -> cooldown
        residual_budget_usd="1.50", submissions_left=400)
    assert rec["reason"] == "DECIDE_COOLDOWN", rec["reason"]
    return rec


# ------------------------------------------------- the writer's output
def test_the_writer_prices_the_book_and_states_its_source():
    rec = _cooldown_decision()
    assert rec["book"]["usable"] is True
    assert rec["book"]["stream_age_s"] == "0.076"
    assert rec["book"]["source"] == "authenticated_ws", (
        "the consumer grades evidence on the source; it must be stated")


def test_the_writer_prices_a_conditional_pair():
    rec = _cooldown_decision()
    assert "pair_if" in rec, "a basis was given, so the pair is priceable"
    assert D(rec["pair_if"]["at_touch"]) > 0


# ------------------------------------------- the reader now reads them
def test_the_record_no_longer_reports_an_unknown_evidence_basis():
    out = MPB.record(market=MARKET, reason="DECIDE_COOLDOWN",
                     decision=_cooldown_decision(),
                     entry_ceiling="0.96", held_side="UP", held_qty="3",
                     deficient_qty="2", stage="DECISION")
    ev = out["evidence"]
    assert ev["basis"] == MPB.EV_AUTHENTICATED, ev
    assert ev["executable_evidence"] is True
    assert ev["why"] == "authenticated stream, current"


def test_the_pair_is_now_known_and_its_provenance_is_recorded():
    out = MPB.record(market=MARKET, reason="DECIDE_COOLDOWN",
                     decision=_cooldown_decision(),
                     entry_ceiling="0.96", held_side="UP", held_qty="3",
                     deficient_qty="2", stage="DECISION")
    assert out["pair"]["known"] is True
    assert out["missed_profitable_pair"] != "UNKNOWN"
    assert out["pair_source"] == "decision.pair_if.at_touch (CONDITIONAL)", (
        "a conditional pair must never be readable as an executed one")


def test_a_flat_pair_own_cost_still_wins_and_is_labelled_as_such():
    """The OPENING caller's shape must keep working unchanged."""
    out = MPB.record(market=MARKET, reason="PAIR_COST_ABOVE_CEILING",
                     decision={"pair_own_cost": "0.9500",
                               "stream_age_s": "0.076"},
                     entry_ceiling="0.96", stage="OPENING",
                     book_source="authenticated_ws")
    assert out["pair"]["pair_own_cost"] == "0.9500"
    assert out["pair_source"] == "decision.pair_own_cost"
    assert out["evidence"]["basis"] == MPB.EV_AUTHENTICATED


def test_an_explicit_book_source_kwarg_still_overrides_the_nested_one():
    out = MPB.record(market=MARKET, reason="DECIDE_COOLDOWN",
                     decision=_cooldown_decision(), entry_ceiling="0.96",
                     held_side="UP", held_qty="3", stage="DECISION",
                     book_source="rest_poll")
    assert out["evidence"]["basis"] == MPB.EV_REST_SPARSE


def test_a_stale_frame_is_not_promoted_to_authenticated():
    """The freshness rule is unchanged: a lagged authenticated frame is
    reported as lagged, not as executable."""
    rec = BC.decide(
        market=MARKET, held_side="UP", held_qty="3", held_cost_usd="1.29",
        deficient_qty="2", book=dict(BOOK, stream_age_s="99.0"),
        now_t=1000.0, window_start=600.0, last_decide_t=999.0)
    out = MPB.record(market=MARKET, reason="DECIDE_COOLDOWN", decision=rec,
                     entry_ceiling="0.96", stage="DECISION")
    assert out["evidence"]["basis"] == MPB.EV_REST_SPARSE
    assert out["evidence"]["executable_evidence"] is False


def test_a_decision_with_neither_shape_still_reports_unknown_not_a_crash():
    out = MPB.record(market=MARKET, reason="DECIDE_COOLDOWN",
                     decision={}, entry_ceiling="0.96", stage="DECISION")
    assert out["pair"]["known"] is False
    assert out["missed_profitable_pair"] == "UNKNOWN"
    assert out["pair_source"] is None
    assert out["evidence"]["basis"] == MPB.EV_UNKNOWN


def test_a_hostile_decision_shape_cannot_raise():
    for bad in ({"book": "not-a-mapping"}, {"pair_if": 7},
                {"book": None, "pair_if": None}):
        out = MPB.record(market=MARKET, reason="DECIDE_COOLDOWN",
                         decision=bad, entry_ceiling="0.96",
                         stage="DECISION")
        assert "evidence" in out
