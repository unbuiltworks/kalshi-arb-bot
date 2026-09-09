"""The cohort reader must never pool two market-selection regimes.

The service log holds windows from both: the flow-blind slate (ten county
cricket matches, 4 opportunities in 2,136 observations) ran until 14:54Z on
2026-09-09, and the flow-ranked slate after it. A pooled number would be
larger and cleaner than either and would answer the persistence question
partly on markets chosen by list order.
"""

import json

from src.pmus_persistence_report import (FLOW_RANKED, UNKNOWN, UNRANKED,
                                         provenance, read, regime_of, report)
from src.pmus_persistence_window import (BUCKETS,
                                         MIN_OPPORTUNITIES_FOR_COMPARISON)


def win(opps=(0, 0, 0), obs=(6, 6, 12), selection=FLOW_RANKED, score=0.5,
        pin=1):
    names = [n for _, _, n in BUCKETS]
    w = {"slug": "s", "complement_side": "NO", "complement_px_at_entry": "0.4",
         "unresolvable_buckets": [], "closed": True,
         "buckets": {n: {"observations": o, "opportunities": p, "prints": p,
                         "shares": "0", "best_complement_px": "0.40",
                         "resolvable": True}
                     for n, p, o in zip(names, opps, obs)}}
    if selection is not None:
        w["selection"] = selection
        if selection == FLOW_RANKED:
            w["roster_pin"] = pin
            w["pre_window_flow_score"] = score
    return "PMUS_PERSISTENCE_WINDOW " + json.dumps(w)


def pin_row(decided=3):
    return "PMUS_FLOW_ROSTER " + json.dumps(
        {"kind": "roster_pinned", "pin": 1, "ranking": [],
         "two_sidedness_decided_on": decided})


def test_the_two_regimes_are_never_pooled():
    lines = [win(opps=(9, 9, 9), selection=UNRANKED),
             win(opps=(1, 0, 0), selection=FLOW_RANKED)]
    r = report(lines, FLOW_RANKED)
    assert r["cohort"]["by_bucket"]["a_0_30s"]["opportunities"] == 1, \
        "the unranked window's 9 opportunities must not be counted here"
    assert r["provenance"]["windows_unranked"] == 1
    assert r["provenance"]["windows_flow_ranked"] == 1


def test_a_row_with_no_regime_is_unknown_not_assumed():
    lines = [win(opps=(9, 9, 9), selection=None)]
    assert regime_of(json.loads(lines[0].split(" ", 1)[1])) == UNKNOWN
    r = report(lines, FLOW_RANKED)
    assert r["verdict"] == "NO_DATA"
    assert r["cohort"] is None
    assert r["provenance"]["windows_unknown_regime"] == 1


def test_an_unrecognised_regime_string_is_unknown():
    lines = [win(selection="something_else")]
    assert read(lines)["by_regime"][UNKNOWN] == \
        [json.loads(lines[0].split(" ", 1)[1])]


def test_an_underpowered_cohort_reports_the_exact_deficit():
    lines = [win(opps=(0, 1, 1))]
    r = report(lines, FLOW_RANKED)
    assert r["verdict"] == "UNDERPOWERED"
    assert r["cohort"]["power"]["opportunity_deficit"] == \
        MIN_OPPORTUNITIES_FOR_COMPARISON - 2
    assert "need %d more" % (MIN_OPPORTUNITIES_FOR_COMPARISON - 2) in r["why"]


def test_a_powered_cohort_says_so():
    lines = [win(opps=(20, 10, 5), obs=(600, 600, 900))]
    r = report(lines, FLOW_RANKED)
    assert r["verdict"] == "POWERED"
    assert r["cohort"]["comparison"] == "powered"


def test_provenance_reports_the_pre_window_score_range():
    lines = [win(score=0.2), win(score=0.9), pin_row(decided=4)]
    p = provenance(read(lines))
    assert p["min_pre_window_score"] == 0.2
    assert p["max_pre_window_score"] == 0.9
    assert p["windows_carrying_a_pre_window_score"] == 2
    assert p["pins"] == 1


def test_provenance_surfaces_a_ranking_that_never_used_two_sidedness():
    """If no pin ever decided it, the owner's third ranking input was absent."""
    p = provenance(read([win(), pin_row(decided=0), pin_row(decided=0)]))
    assert p["two_sidedness_decided_on_total"] == 0


def test_malformed_and_unrelated_lines_are_ignored_not_guessed():
    lines = ["hello", "PMUS_PERSISTENCE_WINDOW {not json",
             "PMUS_COMPLETION_SHADOW {\"kind\": \"heartbeat\"}", win()]
    assert len(read(lines)["by_regime"][FLOW_RANKED]) == 1


def test_a_heartbeat_is_not_mistaken_for_a_window():
    lines = ["PMUS_PERSISTENCE_WINDOW " + json.dumps({"kind": "heartbeat"})]
    assert read(lines)["by_regime"][FLOW_RANKED] == []
    assert read(lines)["by_regime"][UNKNOWN] == []
