"""Spring evidence-pursuit WS retention regressions (issue #167 comments
5531925556/5531961242): a one-strict-match candidate earns a bounded,
freshness-gated WS seat so a second/third 300s completion can be
observed; loose Q1 MATCHED never qualifies; the cap and the COLD
starvation guarantee are untouched; stale evidence expires fail closed;
no admission/execution behavior changes."""
from __future__ import annotations

import time

from src import pmus_priority_queue as pq
from src import pmus_spring_l5_session as spring
from src import pmus_ws_evidence_recorder_enriched as enriched


def _busy_state(n, now, trades=40):
    st = {}
    for i in range(n):
        st[f"busy-{i:02d}"] = {
            "trades": [{"t": now - 1, "px": "0.5", "dir": "buy"}] * trades,
            "book_updates": trades * 10, "both_sides_served": True,
            "bid": "0.50", "ask": "0.51", "bid_qty": "50", "ask_qty": "50"}
    return st


def _strict_candidate(now, age_s=60.0, match_age_s=400.0):
    """State carrying exactly one canonical strict SPRING_MATCHED
    (join 0.50, complement path to <=0.96) with configurable freshness."""
    m = now - match_age_s
    return {
        "bid": "0.50", "ask": "0.52", "bid_qty": "10", "ask_qty": "10",
        "both_sides_served": True, "book_updates": 5,
        "bbo_history": [{"t": m - 5, "bid": "0.50", "ask": "0.52",
                         "bid_qty": "10", "ask_book_missing": False}],
        "trades": [
            {"t": m, "px": "0.50", "dir": "sell"},
            {"t": m + 30, "px": "0.54", "dir": "buy"},
            {"t": now - age_s, "px": "0.51", "dir": "buy"},
        ],
    }


def _loose_candidate(now):
    """Out-of-band joins: produces loose Q1-style flow but ZERO strict
    spring matches (slots never start 45-55c)."""
    return {
        "bid": "0.76", "ask": "0.78", "bid_qty": "10", "ask_qty": "10",
        "both_sides_served": True, "book_updates": 5,
        "bbo_history": [{"t": now - 500, "bid": "0.76", "ask": "0.78",
                         "bid_qty": "10", "ask_book_missing": False}],
        "trades": [
            {"t": now - 490, "px": "0.76", "dir": "sell"},
            {"t": now - 460, "px": "0.80", "dir": "buy"},
            {"t": now - 430, "px": "0.76", "dir": "sell"},
            {"t": now - 400, "px": "0.80", "dir": "buy"},
        ],
    }


def _targets(state, extra=()):
    return [{"slug": s} for s in list(state) + list(extra)]


def test_one_strict_match_candidate_earns_bounded_seat():
    now = time.time()
    state = _busy_state(30, now)
    state["pursue-me"] = _strict_candidate(now)
    targets = _targets(state)
    p = enriched.plan(targets, state, 12, 0, {}, now=now)
    assert len(p["subscribe"]) == 12                    # cap unchanged
    assert "pursue-me" in p["subscribe"]
    rows = p["diagnostics"]["spring_ws_reserved"]
    assert [r["slug"] for r in rows] == ["pursue-me"]
    assert rows[0]["reason"] == "strict_match_pursuit"
    assert rows[0]["strict_matches"] == 1
    assert rows[0]["last_update_age_s"] > 0
    # only a WARM seat was traded, never a hot or cold-sampled seat
    evicted = rows[0]["evicted_warm_seat"]
    assert p["per_market"][evicted]["tier"] == "warm"


def test_loose_q1_matched_never_earns_strict_retention():
    now = time.time()
    state = _busy_state(30, now)
    state["loose-only"] = _loose_candidate(now)
    p = enriched.plan(_targets(state), state, 12, 0, {}, now=now)
    assert "loose-only" not in {r["slug"] for r in
                                p["diagnostics"]["spring_ws_reserved"]}


def test_reserve_is_bounded_and_cap_unchanged(monkeypatch):
    monkeypatch.setenv(enriched.PURSUIT_SEATS_ENV, "2")
    now = time.time()
    state = _busy_state(30, now)
    for i in range(5):
        state[f"pursue-{i}"] = _strict_candidate(now)
    p = enriched.plan(_targets(state), state, 12, 0, {}, now=now)
    assert len(p["subscribe"]) == 12
    assert len(p["diagnostics"]["spring_ws_reserved"]) == 2
    # env is clamped to at most 4 seats even if set absurdly high
    monkeypatch.setenv(enriched.PURSUIT_SEATS_ENV, "99")
    p2 = enriched.plan(_targets(state), state, 12, 0, {}, now=now)
    assert len(p2["diagnostics"]["spring_ws_reserved"]) <= 4


def test_cold_sampling_guarantee_survives_pursuit():
    now = time.time()
    state = _busy_state(20, now)
    state["pursue-me"] = _strict_candidate(now)
    blanks = [{"slug": f"blank-{i:02d}"} for i in range(60)]
    targets = _targets(state) + blanks
    prev: dict = {}
    seen: set = set()
    for cycle in range(60):
        p = enriched.plan(targets, state, 12, cycle, prev, now=now)
        prev = p["state"]
        seen.update(p["subscribe"])
    assert {b["slug"] for b in blanks} <= seen          # no starvation
    assert p["diagnostics"]["cold_sampled"] >= 1        # sampler intact


def test_stale_source_and_aged_out_match_expire_fail_closed(monkeypatch):
    now = time.time()
    state = _busy_state(30, now)
    # source stale: EVERY authenticated update older than the TTL, while
    # the strict match itself is still inside the rolling replay window
    state["stale-source"] = _strict_candidate(now, age_s=1500.0,
                                              match_age_s=1500.0)
    # match aged out of the rolling replay window: updates fresh, but the
    # strict completion happened >1h ago so the rolling summary shows 0
    state["aged-match"] = _strict_candidate(now, age_s=60.0,
                                            match_age_s=4000.0)
    p = enriched.plan(_targets(state), state, 12, 0, {}, now=now)
    reserved = {r["slug"] for r in p["diagnostics"]["spring_ws_reserved"]}
    assert "stale-source" not in reserved
    assert "aged-match" not in reserved


def test_no_admission_or_execution_behavior_changes():
    # pursuit lives entirely in the planner wrapper: the spring gate is
    # byte-for-byte the canonical one and still fails closed
    row = {"slug": "x", "bid": "0.50", "skip_reason": None,
           "both_sides_served": True, "trades_1h": 20,
           "two_sided_trades": True}
    assert spring.spring_candidate_gate(row) == \
        "spring_category_bar_not_met"
    # zero seats disables pursuit entirely and the plan passes through
    now = time.time()
    state = _busy_state(30, now)
    state["pursue-me"] = _strict_candidate(now)
    import os
    os.environ[enriched.PURSUIT_SEATS_ENV] = "0"
    try:
        p = enriched.plan(_targets(state), state, 12, 0, {}, now=now)
        base_p = pq.plan(_targets(state), state, 12, 0, {}, now=now) \
            if pq.plan is not enriched.plan else None
        assert p["diagnostics"]["spring_ws_reserved"] == []
        if base_p is not None:
            assert p["subscribe"] == base_p["subscribe"]
    finally:
        os.environ.pop(enriched.PURSUIT_SEATS_ENV, None)


def test_status_rows_expose_authenticated_freshness():
    now = time.time()
    s = _strict_candidate(now, age_s=120.0)
    out = enriched.ws_gate(s, now)
    assert 115.0 <= out["last_authenticated_update_age_s"] <= 125.0
    assert out["spring_matched_slots"] == 1
    # a market with no timestamped updates reports None, not a fake zero
    assert enriched.ws_gate({"trades": [], "bbo_history": []},
                            now)["last_authenticated_update_age_s"] is None
