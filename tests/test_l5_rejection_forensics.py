"""#93 forensics: the module must convict only on the venue's own numbers,
and must never turn "we could not read the book" into "we crossed"."""
from __future__ import annotations

import json

from src import l5_rejection_forensics as rf


def _order(px, opp, age=1.0, side="YES", stage="spring_open_1",
           oid="X1", reason="rejected:ORDER_REJECTED_ZERO_FILL",
           bid="0.30", ask="0.40", ours=None):
    book = {"source": "s", "status_utc": "t", "yes_bid": bid, "yes_ask": ask,
            "authenticated_age_s": age, "both_sides_served": True,
            "opp_ask_authenticated": opp}
    o = {"order_id": oid, "stage": stage, "side": side, "px": px,
         "qty": "1", "filled_qty": "0", "end_reason": reason,
         "recorder_book": book}
    if ours is not None:
        o["fresh_opp_ask"] = ours
    return o


def test_a_price_at_or_through_the_authenticated_ask_is_a_crossing():
    """These are the rejections the venue was right about, and the only
    ones we may call explained."""
    assert rf.classify(_order("0.40", "0.40"))["klass"] == \
        "CROSSED_AUTHENTICATED"
    assert rf.classify(_order("0.41", "0.40"))["klass"] == \
        "CROSSED_AUTHENTICATED"


def test_a_price_inside_the_authenticated_ask_survives_as_unexplained():
    r = rf.classify(_order("0.38", "0.39"))
    assert r["klass"] == "NON_CROSSING_AUTHENTICATED"
    assert r["true_margin_ticks"] == 1.0


def test_an_unreadable_book_is_unverifiable_and_never_a_crossing():
    """The whole point of the diagnostic. A book we could not read is not
    evidence that we crossed, and calling it one would manufacture the
    conclusion the investigation exists to test."""
    o = _order("0.38", None)
    o["recorder_book"] = {"unreadable": "slug_not_in_status_document"}
    r = rf.classify(o)
    assert r["klass"] == "UNVERIFIABLE_BOOK_UNREADABLE"
    assert "CROSS" not in r["klass"]


def test_a_stale_book_convicts_nobody():
    """A 116 s book was in the live data. It cannot speak to a submission
    made now, in either direction."""
    assert rf.classify(_order("0.38", "0.39", age=116.3))["klass"] == \
        "UNVERIFIABLE_BOOK_STALE"
    assert rf.classify(_order("0.40", "0.39", age=116.3))["klass"] == \
        "UNVERIFIABLE_BOOK_STALE"


def test_retries_against_one_cached_book_are_one_observation(tmp_path):
    """LIVE: four orders on aec-setkameua-zhyole-strole were rejected
    against a single status document with authenticated_age_s 2.5. Counting
    orders reports 20 unexplained rejections where there are 13 distinct
    book readings -- larger, plausible, and wrong."""
    orders = [_order("0.33", "0.48", age=2.5, oid=f"B{i}",
                     stage=f"spring_extend_{i}") for i in range(4)]
    msg = "PMUS_L5_CAMPAIGN " + json.dumps(
        {"kind": "session_end", "slug": "s1", "orders": orders})
    p = tmp_path / "logs.json"
    p.write_text(json.dumps({"deploymentId": "d", "deploy": [
        {"message": msg, "timestamp": "2026-09-08T02:00:00Z"}]}))
    rep = rf.report([str(p)])
    assert rep["survive_as_non_crossing"] == 4
    assert rep["distinct_observations_surviving"] == 1


def test_the_side_convention_is_carried_not_recomputed():
    """The NO-side opposite ask is DERIVED as 1 - yes_bid upstream. If this
    module recomputed it the even YES/NO split in the live result would be
    a property of my arithmetic instead of the venue's behaviour, so it
    reads the field the session wrote and reports the side alongside."""
    r = rf.classify(_order("0.62", "0.63", side="NO", bid="0.37", ask="0.40"))
    assert r["side"] == "NO"
    assert r["authenticated_opp_ask"] == "0.63"
    assert r["klass"] == "NON_CROSSING_AUTHENTICATED"


def test_it_reads_and_never_acts():
    """Forensics on the order path must not be able to touch the order
    path -- and #93's standing instruction is that live pricing does not
    move while these rejections are unexplained."""
    import inspect
    src = inspect.getsource(rf)
    body = src.split('"""', 2)[-1]          # drop the module docstring
    for forbidden in ("place_limit_buy", "session_runner", "run_campaign(",
                      "os.environ[", "set_variables", "requests.post"):
        assert forbidden not in body, forbidden
