"""The corpus census, and the one correction it makes to the method.

`L5_COMPLETION_IS_A_QUEUE_PROBLEM.md` quoted "501 samples" from a single
window of 539 s at a 5 s sampler. Six shadow controllers sample the same
book, so that is ~108 distinct book instants observed ~5 times each. The
percentages barely move; the effective n moves by almost 5x, and n is what
a confidence claim rests on. These tests pin that the census never counts
one book instant six times, and never double-counts a line that appears in
two overlapping log fetches.
"""
from __future__ import annotations

import json
import os
import tempfile

from src import l5_residual_reachability_census as CEN

W = "cpc-btc-updown-15m-2026-10-26-0400z"


def _sample(arm, row_t, *, queue_mult="500", cross_px="0.4800",
            insufficient=False, sample_t=1.0, n_bids=54):
    doc = {
        "label": CEN.LABEL, "slug": "%s|%s" % (W, arm), "t": sample_t,
        "held_side": "DOWN", "residual": "-3",
        "book": {"row": {"t": row_t, "n_bids": n_bids, "n_asks": 12}},
        "reachability": {
            "opposite_top_of_book": {"taker_cross_wire_px": cross_px},
            "maker_quote": {"queue_ahead_multiple_of_need": queue_mult,
                            "qty_needed": "3",
                            "queue_ahead_bound": "1554.51"},
            "taker_walk": {"insufficient_displayed_depth": insufficient},
        },
    }
    return {"message": "%s %s" % (CEN.LABEL, json.dumps(doc))}


def _dump(samples):
    p = os.path.join(tempfile.mkdtemp(), "logs.json")
    with open(p, "w") as fh:
        json.dump({"deploy": samples}, fh)
    return p


def test_six_arms_on_one_book_instant_count_as_one_instant():
    arms = ["ARGMAX", "ARGMAX@ONE_TICK_BEHIND", "ARGMAX@TWO_SIDED",
            "ARGMAX@TWO_SIDED_LADDER", "PAIR_AVAILABLE", "RESIDUAL_URGENCY"]
    p = _dump([_sample(a, 1000.0, sample_t=float(i)) for i, a in enumerate(arms)])
    out = CEN.census(CEN.iter_samples([p]))
    assert out["n_arm_samples"] == 6
    assert out["n_book_instants"] == 1
    assert out["n_windows"] == 1
    assert out["arms_seen"] == sorted(arms)


def test_a_rate_is_reported_both_ways_and_they_differ():
    """One instant with no bid to cross, observed by 6 arms, against 3
    instants that are fine and observed once each. Per arm sample that is
    6/9 = 66.7%; per book instant it is 1/4 = 25.0%. Reporting only the
    first would overstate the finding almost threefold."""
    rows = [_sample("ARM%d" % i, 1000.0, cross_px=None, insufficient=True,
                    sample_t=float(i)) for i in range(6)]
    rows += [_sample("ARGMAX", 1000.0 + t, sample_t=100.0 + t)
             for t in (1.0, 2.0, 3.0)]
    out = CEN.census(CEN.iter_samples([_dump(rows)]))
    assert out["n_arm_samples"] == 9
    assert out["n_book_instants"] == 4
    assert out["nothing_to_cross_pct"]["per_arm_sample"] == 66.7
    assert out["nothing_to_cross_pct"]["per_book_instant"] == 25.0
    assert out["taker_impossible_pct"]["per_book_instant"] == 25.0


def test_overlapping_fetches_do_not_inflate_the_corpus():
    """Two time-range pulls that overlap serve the same line twice. The
    census must not count it twice, or the corpus grows by refetching."""
    rows = [_sample("ARGMAX", 1000.0 + t, sample_t=float(t))
            for t in (1.0, 2.0, 3.0)]
    a, b = _dump(rows), _dump(rows)
    out = CEN.census(CEN.iter_samples([a, b]))
    assert out["n_arm_samples"] == 3
    assert out["n_book_instants"] == 3


def test_queue_distribution_and_thresholds():
    rows = [_sample("ARGMAX", 1000.0 + i, queue_mult=str(v), sample_t=float(i))
            for i, v in enumerate([0.5, 2, 50, 200, 1500, 5000])]
    out = CEN.census(CEN.iter_samples([_dump(rows)]))
    q = out["queue_ahead_multiple_of_need"]
    assert q["n"] == 6
    assert q["min"] == 0.5 and q["max"] == 5000.0
    assert q["ge_1x_pct"] == 83.3          # 5 of 6
    assert q["ge_100x_pct"] == 50.0        # 200, 1500, 5000
    assert q["ge_1000x_pct"] == 33.3       # 1500, 5000


def test_it_is_structurally_read_only():
    """It parses log dumps. No socket, no credential, no order body."""
    import ast
    src = ast.parse(open("src/l5_residual_reachability_census.py").read())
    calls = {getattr(n.func, "attr", getattr(n.func, "id", ""))
             for n in ast.walk(src) if isinstance(n, ast.Call)}
    for verb in ("urlopen", "Request", "post", "submit", "cancel",
                 "rmtree", "unlink", "remove"):
        assert verb not in calls, verb
    names = {n.names[0].name for n in ast.walk(src)
             if isinstance(n, ast.Import)}
    assert "urllib" not in names and "httpx" not in names


def test_a_malformed_or_missing_dump_is_skipped_not_fatal():
    bad = os.path.join(tempfile.mkdtemp(), "bad.json")
    open(bad, "w").write("{ not json")
    good = _dump([_sample("ARGMAX", 1000.0)])
    out = CEN.census(CEN.iter_samples([bad, "/nope/missing.json", good]))
    assert out["n_arm_samples"] == 1
