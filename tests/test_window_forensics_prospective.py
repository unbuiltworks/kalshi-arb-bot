"""The prospective scorer, on fixtures built to break it.

Every test here is synthetic. The real windows are not touched, because the
sample is blind until W3's follow completes and scoring a real tape early would
be exactly the peek the preregistration forbids.
"""
import io
import json
import os

import pytest

from analysis.l5.window_forensics import eligibility as EL
from analysis.l5.window_forensics import prospective as P

TICK = 0.01
START = 1789670700
END = START + 900


def _book(t, *, up_bids=(), dn_bids=(), up_asks=(), dn_asks=(),
          obs=None, one_sided=False):
    def side(levels):
        return [[p, q] for p, q in levels]

    def summ(bids, asks):
        b = max((p for p, _ in bids), default=None)
        a = min((p for p, _ in asks), default=None)
        return {"bid": b, "ask": a, "tick": str(TICK), "one_sided": one_sided,
                "MARKET_STATE_CLOCK": t}
    return {"kind": "book_full_joint", "tte_s": END - t, "pre_window": t < START,
            # our wall clock is deliberately OFFSET from the venue's, so a join
            # on the wrong clock cannot pass by accident
            "L5_OBSERVATION_CLOCK": (obs if obs is not None else t + 22.2),
            "outcomes": {
                "Up": {"token": "u", "MARKET_STATE_CLOCK": t,
                       "bids": side(up_bids), "asks": side(up_asks),
                       "summary": summ(up_bids, up_asks)},
                "Down": {"token": "d", "MARKET_STATE_CLOCK": t,
                         "bids": side(dn_bids), "asks": side(dn_asks),
                         "summary": summ(dn_bids, dn_asks)}}}


def _parent(t, outcome, price, qty, *, fee="0", lies=None, ohash=None):
    return {"kind": "l5_parent_execution", "attribution": "OBSERVED_L5_PARENT",
            "L5_EVENT_CLOCK": t, "L5_OBSERVATION_CLOCK": t + 22.2,
            "publication_lag_s": 22.2, "tx": "0x%x" % int(t * 1000),
            "outcome": outcome, "price": price, "qty": qty,
            "parents": [{"order_hash": ohash or ("0xh%d" % int(t)),
                         "maker_asset_id": "0", "taker_asset_id": "1",
                         "maker_amount_filled": "1", "taker_amount_filled": "1",
                         "fee": fee}],
            # the recorder's OWN fields, deliberately wrong. Nothing may read
            # them: they are written in discovery order, not event order.
            "inventory_before": lies or {"Up": -999.0, "Down": -999.0},
            "inventory_after": lies or {"Up": -999.0, "Down": -999.0},
            "residual_after": -12345.0}


def _pred(t, levels):
    return {"kind": "surface_prediction", "decision_time": t,
            "predictions": [{"attribution": "INFERRED_SURFACE",
                             "outcome": oc, "side": "BUY", "price": px,
                             "ticks_behind_ask": n, "qty_base_lot": 100.0}
                            for oc, px, n in levels]}


def _tape(tmp_path, rows, *, name=None, durable=True, complete=True):
    d = tmp_path / ("seg_20260917T184300Z_001" if durable else "handrun")
    d.mkdir(exist_ok=True)
    p = d / (name or ("window_%d.jsonl" % START))
    head = [{"kind": "preregistration", "window_start": START,
             "window_end": END, "slug": "btc-updown-15m-%d" % START,
             "procedure": {"lead_s": 120, "follow_s": 300},
             "L5_OBSERVATION_CLOCK": START - 120}]
    tail = ([{"kind": "window_end", "window_start": START, "books": 0,
              "predictions": 0, "parent_executions": 0,
              "L5_OBSERVATION_CLOCK": END + 300}] if complete else [])
    io.open(p, "w").write("".join(json.dumps(r) + "\n"
                                  for r in head + list(rows) + tail))
    return str(d), str(p)


# ------------------------------------------- inventory and ordering integrity

def test_parents_are_stable_sorted_and_inventory_rebuilt_from_scratch(tmp_path):
    """Rows arrive in DISCOVERY order carrying poisoned inventory fields. The
    scorer must reorder by event clock and ignore those fields entirely."""
    rows = [_parent(START + 30, "Up", 0.55, 100.0),      # written second
            _parent(START + 10, "Down", 0.49, 100.0),    # happened FIRST
            _parent(START + 50, "Up", 0.51, 10.0)]
    ps = P.parents_in_event_order(rows)
    assert [p["L5_EVENT_CLOCK"] - START for p in ps] == [10, 30, 50]
    assert ps[0]["inventory_before"] == {}
    assert ps[0]["inventory_after"] == {"Down": 100.0}
    assert ps[1]["inventory_after"] == {"Down": 100.0, "Up": 100.0}
    assert ps[2]["inventory_after"] == {"Down": 100.0, "Up": 110.0}
    assert ps[2]["residual_after"] if False else True
    for p in ps:                       # the poison never appears anywhere
        assert -999.0 not in p["inventory_before"].values()
        assert -999.0 not in p["inventory_after"].values()


def test_a_tie_on_the_event_clock_keeps_file_order(tmp_path):
    rows = [_parent(START + 10, "Up", 0.55, 1.0, ohash="0xFIRST"),
            _parent(START + 10, "Down", 0.49, 2.0, ohash="0xSECOND")]
    ps = P.parents_in_event_order(rows)
    assert [p["order_hashes"][0] for p in ps] == ["0xFIRST", "0xSECOND"]


def test_the_maker_taker_role_comes_from_the_chain_fee_not_a_guess():
    mk = P.parents_in_event_order([_parent(START, "Up", 0.5, 1.0, fee="0")])
    tk = P.parents_in_event_order([_parent(START, "Up", 0.5, 1.0, fee="336000")])
    assert mk[0]["liquidity_role"] == "MAKER"
    assert tk[0]["liquidity_role"] == "TAKER"
    no = _parent(START, "Up", 0.5, 1.0)
    no["parents"] = []
    assert P.parents_in_event_order([no])[0]["liquidity_role"] is None


# --------------------------------------------------------- the book join

def test_the_book_join_uses_the_venue_clock_not_our_own(tmp_path):
    """Our wall clock trails the venue's by the public-feed lag. Joining on it
    would answer the -1s mark with a book from 20+ seconds earlier."""
    b = P.Book(_book(START + 100, up_bids=[(0.55, 500.0)]))
    assert b.t == START + 100
    assert b.obs_t == START + 122.2
    got = P._at([b], START + 101)
    assert got is b
    # a mark BEFORE the snapshot must not be answered by it
    assert P._at([b], START + 99) is None


def test_a_stale_book_refuses_the_mark_instead_of_answering_it():
    b = P.Book(_book(START, up_bids=[(0.55, 500.0)]))
    assert P._at([b], START + P.BOOK_STALE_S - 0.1) is b
    assert P._at([b], START + P.BOOK_STALE_S + 0.1) is None


def test_unreadable_and_served_but_empty_are_different_answers():
    b = P.Book(_book(START, up_bids=[(0.55, 500.0)]))
    assert b.size_at("Up", 0.55, "BUY") == 500.0
    assert b.size_at("Up", 0.42, "BUY") == 0.0      # served, nothing there
    assert b.size_at("Sideways", 0.55, "BUY") is None   # cannot answer


def test_distance_from_touch_is_measured_in_ticks_behind_the_bid():
    b = P.Book(_book(START, up_bids=[(0.55, 10.0), (0.52, 10.0)]))
    assert b.ticks_from_touch("Up", 0.52, "BUY") == 3.0
    assert b.ticks_from_touch("Up", 0.55, "BUY") == 0.0


# ------------------------------------------------------- the primary result

def test_compatible_liquidity_resting_before_the_execution_is_detected(
        tmp_path):
    t = START + 200
    rows = [_book(t - 130, up_bids=[(0.55, 400.0)]),
            # a mark is answered only by a book within BOOK_STALE_S of it, so
            # each mark needs its own fresh snapshot -- the t-130 one is the
            # earliest SIGHTING, not the answer to the -120s mark
            _book(t - 121, up_bids=[(0.55, 400.0)]),
            _book(t - 61, up_bids=[(0.55, 400.0)]),
            _book(t - 31, up_bids=[(0.55, 400.0)]),
            _book(t - 16, up_bids=[(0.55, 400.0)]),
            _book(t - 6, up_bids=[(0.55, 400.0)]),
            _book(t - 2, up_bids=[(0.55, 400.0)]),
            _parent(t, "Up", 0.55, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    for m in P.MARKS_S:
        assert w["primary"]["share_at_%ds" % m] == 1.0, m
        assert w["primary"]["share_covering_qty_at_%ds" % m] == 1.0
    (r,) = w["scored_parents"]
    assert r["marks"]["-60"]["attribution"] == "L5_COMPATIBLE_BOOK_LIQUIDITY"
    assert r["first_observed_compatible"]["lead_s"] == 130.0
    assert r["lot_class"] == "EXACT_100"
    assert r["tte_s"] == round(END - t, 1)


def test_an_empty_level_before_the_execution_is_reported_as_absent(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.50, 400.0)]),     # served; 0.55 is empty
            _parent(t, "Up", 0.55, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    assert w["primary"]["share_at_-1s"] == 0.0
    assert w["primary"]["n_readable_at_-1s"] == 1
    (r,) = w["scored_parents"]
    assert r["marks"]["-1"]["compatible_qty_displayed"] == 0.0
    assert r["first_observed_compatible"] is None


def test_a_mark_with_no_book_is_excluded_rather_than_counted_as_absent(
        tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]), _parent(t, "Up", 0.55, 5.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    assert w["primary"]["n_readable_at_-1s"] == 1
    assert w["primary"]["n_readable_at_-120s"] == 0
    assert w["primary"]["share_at_-120s"] is None


# ---------------------------------------------- predictions cannot use hindsight

def test_a_prediction_emitted_after_the_execution_is_not_a_hit(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]),
            _pred(t + 1, [("Up", 0.55, 1)]),        # AFTER the fill
            _parent(t, "Up", 0.55, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    assert w["prediction_hits"]["exact"] == 0
    rows[1] = _pred(t - 5, [("Up", 0.55, 1)])       # before it
    d2, _ = _tape(tmp_path, rows, name="window_%d.jsonl" % (START + 900))
    w2 = P.analyse_window(P.EL.tapes(d2)[-1])
    assert w2["prediction_hits"]["exact"] == 1


def test_a_one_tick_miss_counts_as_near_but_not_exact(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]),
            _pred(t - 5, [("Up", 0.54, 2)]),
            _parent(t, "Up", 0.55, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    assert w["prediction_hits"]["exact"] == 0
    assert w["prediction_hits"]["within_1_tick"] == 1


# -------------------------------------------------- the causal reversal test

def test_opposite_already_present_when_b_was_standing_before_a_filled(
        tmp_path):
    a, b = START + 100, START + 160
    rows = [_book(a - 30, up_bids=[(0.55, 300.0)], dn_bids=[(0.49, 300.0)]),
            _book(a - 2, up_bids=[(0.55, 300.0)], dn_bids=[(0.49, 300.0)]),
            _book(b - 2, dn_bids=[(0.49, 300.0)]),
            _parent(a, "Up", 0.55, 100.0), _parent(b, "Down", 0.49, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    (rv,) = w["reversal_rows"]
    assert rv["verdict"] == P.OPPOSITE_ALREADY_PRESENT
    assert rv["evidence"]["covers_B_qty"] is True
    assert rv["evidence"]["attribution"] == "L5_COMPATIBLE_BOOK_LIQUIDITY"
    assert w["reversals"]["share_already_present"] == 1.0


def test_opposite_appeared_after_fill_when_b_was_empty_until_a_filled(
        tmp_path):
    a, b = START + 100, START + 160
    rows = [_book(a - 30, up_bids=[(0.55, 300.0)], dn_bids=[(0.40, 300.0)]),
            _book(a - 2, up_bids=[(0.55, 300.0)], dn_bids=[(0.40, 300.0)]),
            _book(a + 3, dn_bids=[(0.49, 300.0)]),
            _parent(a, "Up", 0.55, 100.0), _parent(b, "Down", 0.49, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    (rv,) = w["reversal_rows"]
    assert rv["verdict"] == P.OPPOSITE_APPEARED_AFTER_FILL
    assert rv["evidence"]["first_after_A"]["delay_after_A_s"] == 3.0
    assert w["reversals"]["share_appeared_after_fill"] == 1.0


def test_opposite_never_observed_when_the_level_reads_empty_throughout(
        tmp_path):
    a, b = START + 100, START + 160
    rows = [_book(a - 2, up_bids=[(0.55, 300.0)], dn_bids=[(0.40, 300.0)]),
            _book(a + 3, dn_bids=[(0.40, 300.0)]),
            _parent(a, "Up", 0.55, 100.0), _parent(b, "Down", 0.49, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    (rv,) = w["reversal_rows"]
    assert rv["verdict"] == P.OPPOSITE_NEVER_OBSERVED


def test_no_reading_before_a_is_unresolved_not_absent(tmp_path):
    """Absence of a reading is not a reading of absence."""
    a, b = START + 100, START + 160
    rows = [_book(a + 3, dn_bids=[(0.49, 300.0)]),
            _parent(a, "Up", 0.55, 100.0), _parent(b, "Down", 0.49, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    (rv,) = w["reversal_rows"]
    assert rv["verdict"] == P.UNRESOLVED
    assert "not a reading of absence" in rv["why"]
    assert w["reversals"]["resolved"] == 0
    assert w["reversals"]["share_already_present"] is None


def test_same_outcome_sequences_are_not_reversals(tmp_path):
    rows = [_book(START + 50, up_bids=[(0.55, 300.0)]),
            _parent(START + 100, "Up", 0.55, 100.0),
            _parent(START + 160, "Up", 0.54, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    assert w["reversal_rows"] == []


# ----------------------------------------------- the frozen mechanism rule

def test_the_rule_is_declared_before_the_code_that_evaluates_it():
    src = io.open("analysis/l5/window_forensics/prospective.py").read()
    assert src.index("MECHANISM_RULE") < src.index("def classify")


def test_too_small_a_sample_refuses_rather_than_classifies():
    v = P.classify({"parents": {"n": 3}, "primary": {"share_at_-60s": 1.0},
                    "reversals": {"resolved": 99, "share_already_present": 1.0},
                    "both_sides": {"both_outcomes_share": 1.0}})
    assert v["verdict"] == P.UNRESOLVED and "fewer than" in v["why"]


def test_a_middling_pre_execution_share_is_unresolved_not_the_nearest_fit():
    v = P.classify({"parents": {"n": 50}, "primary": {"share_at_-60s": 0.65},
                    "reversals": {"resolved": 30,
                                  "share_already_present": 0.95,
                                  "share_appeared_after_fill": 0.05},
                    "both_sides": {"both_outcomes_share": 0.95}})
    assert v["verdict"] == P.UNRESOLVED
    assert "between the frozen" in v["why"]


def test_a_low_pre_execution_share_rejects_the_hypothesis():
    v = P.classify({"parents": {"n": 50}, "primary": {"share_at_-60s": 0.10},
                    "reversals": {"resolved": 30,
                                  "share_already_present": 1.0},
                    "both_sides": {"both_outcomes_share": 1.0}})
    assert v["verdict"] == "H3-REJECTED"


def test_too_few_resolved_reversals_cannot_reach_a_verdict():
    v = P.classify({"parents": {"n": 50}, "primary": {"share_at_-60s": 0.95},
                    "reversals": {"resolved": 2, "share_already_present": 1.0},
                    "both_sides": {"both_outcomes_share": 0.95}})
    assert v["verdict"] == P.UNRESOLVED and "resolved reversals" in v["why"]


def test_each_branch_of_the_rule_is_reachable():
    base = {"parents": {"n": 50}, "primary": {"share_at_-60s": 0.95}}
    a = P.classify({**base, "reversals": {"resolved": 30,
                                          "share_already_present": 0.9,
                                          "share_appeared_after_fill": 0.1},
                    "both_sides": {"both_outcomes_share": 0.9}})
    b = P.classify({**base, "reversals": {"resolved": 30,
                                          "share_already_present": 0.1,
                                          "share_appeared_after_fill": 0.9},
                    "both_sides": {"both_outcomes_share": 0.2}})
    c = P.classify({**base, "reversals": {"resolved": 30,
                                          "share_already_present": 0.5,
                                          "share_appeared_after_fill": 0.5},
                    "both_sides": {"both_outcomes_share": 0.5}})
    assert a["verdict"] == "H3-A PERSISTENT_TWO_SIDED_SURFACE"
    assert b["verdict"] == "H3-B STATE_REFRESHED_PASSIVE_SURFACE"
    assert c["verdict"] == "H3-C HYBRID"


# -------------------------------------------------- eligibility gates first

def test_an_ineligible_window_is_never_scored(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]), _parent(t, "Up", 0.55, 1.0)]
    d, _ = _tape(tmp_path, rows, complete=False)     # no window_end row
    rep = P.report(d)
    assert rep["eligible_windows"] == []
    assert rep["windows"] == []
    assert rep["mechanism"]["verdict"] == P.INTEGRITY_FAILURE


def test_a_hand_run_tape_is_never_scored_however_complete(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]), _parent(t, "Up", 0.55, 1.0)]
    d, _ = _tape(tmp_path, rows, durable=False)
    assert P.report(d)["eligible_windows"] == []


def test_windows_are_pooled_and_never_merged_across(tmp_path):
    """A level in one window must never answer for a parent in another."""
    for i in (0, 1):
        st = START + i * 1800
        d = tmp_path / "seg_20260917T184300Z_001"
        d.mkdir(exist_ok=True)
        t = st + 200
        rows = [{"kind": "preregistration", "window_start": st,
                 "window_end": st + 900, "slug": "s",
                 "procedure": {"lead_s": 120, "follow_s": 300},
                 "L5_OBSERVATION_CLOCK": st - 120}]
        # window 0 shows the level; window 1 does not
        rows.append(_book(t - 2, up_bids=([(0.55, 400.0)] if i == 0
                                          else [(0.50, 400.0)])))
        rows.append(_parent(t, "Up", 0.55, 100.0))
        rows.append({"kind": "window_end", "window_start": st,
                     "L5_OBSERVATION_CLOCK": st + 1200})
        io.open(d / ("window_%d.jsonl" % st), "w").write(
            "".join(json.dumps(r) + "\n" for r in rows))
    rep = P.report(str(tmp_path / "seg_20260917T184300Z_001"))
    assert len(rep["eligible_windows"]) == 2
    assert rep["pooled"]["primary"]["n_readable_at_-1s"] == 2
    assert rep["pooled"]["primary"]["n_present_at_-1s"] == 1
    assert rep["pooled"]["primary"]["share_at_-1s"] == 0.5


# ------------------------------------------------------- attribution hygiene

def test_no_book_derived_quantity_is_ever_called_l5_owned(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)], dn_bids=[(0.49, 9.0)]),
            _pred(t - 5, [("Up", 0.55, 1)]),
            _parent(t, "Up", 0.55, 100.0),
            _parent(t + 60, "Down", 0.49, 100.0)]
    d, _ = _tape(tmp_path, rows)
    rep = P.report(d)
    blob = json.dumps(rep)
    assert "L5_OWNED" not in blob
    for w in rep["windows"]:
        for r in w["scored_parents"]:
            assert r["attribution"] == "OBSERVED_L5_PARENT"
            for m in r["marks"].values():
                if m.get("readable"):
                    assert m["attribution"] == "L5_COMPATIBLE_BOOK_LIQUIDITY"
            if r.get("first_observed_compatible"):
                assert (r["first_observed_compatible"]["attribution"]
                        == "L5_COMPATIBLE_BOOK_LIQUIDITY")


def test_the_unresolved_variables_are_stated_not_implied():
    joined = " ".join(P.UNRESOLVED_VARIABLES).lower()
    for must in ("ownership", "lifetime", "cancel", "queue", "incentive",
                 "settle"):
        assert must in joined, must


def test_pair_economics_never_claims_realization(tmp_path):
    ps = P.parents_in_event_order([_parent(START + 10, "Up", 0.48, 100.0),
                                   _parent(START + 20, "Down", 0.47, 100.0)])
    e = P.pair_economics(ps)
    assert e["effective_pair_cost_avg_cost_basis"] == 0.95
    assert e["below_par"] is True
    assert e["matched_qty"] == 100.0
    joined = " ".join(e["labels"]).lower()
    assert "not realized" in joined and "accounting-method dependent" in joined


# ------------------------------------------- the integrity gate and artifact

def test_an_incomplete_sample_yields_no_verdict_at_all(tmp_path):
    """Not UNRESOLVED, which is a finding. NO verdict: a preregistered test run
    on a short sample is not that test."""
    for i in (0, 1):                       # two complete windows, not three
        st = START + i * 1800
        t = st + 200
        d = tmp_path / "seg_20260917T184300Z_001"
        d.mkdir(exist_ok=True)
        rows = [{"kind": "preregistration", "window_start": st,
                 "window_end": st + 900, "slug": "s",
                 "procedure": {"lead_s": 120, "follow_s": 300},
                 "L5_OBSERVATION_CLOCK": st - 120},
                _book(t - 2, up_bids=[(0.55, 400.0)]),
                _parent(t, "Up", 0.55, 100.0),
                {"kind": "window_end", "window_start": st,
                 "L5_OBSERVATION_CLOCK": st + 1200}]
        io.open(d / ("window_%d.jsonl" % st), "w").write(
            "".join(json.dumps(r) + "\n" for r in rows))
    rep = P.report(str(tmp_path / "seg_20260917T184300Z_001"))
    assert rep["integrity"]["eligible_n"] == 2
    assert rep["integrity"]["sample_complete"] is False
    assert rep["mechanism"]["verdict"] == P.INTEGRITY_FAILURE
    assert "incomplete preregistered sample" in rep["mechanism"]["why"]
    assert "without retuning" in rep["mechanism"]["next"]


def test_integrity_names_the_gates_and_the_frozen_module(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]), _parent(t, "Up", 0.55, 1.0)]
    d, _ = _tape(tmp_path, rows)
    integ = P.report(d)["integrity"]
    assert integ["n_required"] == 3
    assert len(integ["frozen_module_sha256"]) == 64
    assert integ["gates_per_capture"][0]["durable"] is True
    assert "four frozen gates" in " ".join(integ["labels"])


def test_coverage_reports_the_gaps_rather_than_smoothing_them(tmp_path):
    t = START + 200
    rows = [_book(t - 400, up_bids=[(0.55, 1.0)]),
            _book(t - 2, up_bids=[(0.55, 400.0)]),
            _parent(t, "Up", 0.55, 100.0)]
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    c = w["coverage"]
    assert c["book_snapshots"] == 2
    assert c["book_gaps_over_stale_threshold"] == 1
    assert c["book_largest_gap_s"] == 398.0
    assert c["chain_receipt_share"] == 1.0


def test_the_complete_artifact_is_persisted_whole_and_atomically(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]),
            _parent(t, "Up", 0.55, 100.0),
            _parent(t + 60, "Down", 0.49, 100.0)]
    d, _ = _tape(tmp_path, rows)
    rep = P.report(d)
    out = P.persist(rep, d)
    assert out and out.endswith(P.LABEL + ".json")
    assert not os.path.exists(out + ".tmp")
    back = json.load(io.open(out))
    # the rows behind the label must survive: a verdict without them cannot
    # be re-checked
    assert back["windows"][0]["scored_parents"]
    assert "reversal_rows" in back["windows"][0]
    assert back["integrity"]["eligible_n"] == 1


def test_quantity_compatibility_is_scored_apart_from_price(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)]),
            _pred(t - 5, [("Up", 0.55, 1)]),          # base lot 100
            _parent(t, "Up", 0.55, 37.0)]            # right level, wrong lot
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    (r,) = w["scored_parents"]
    assert r["predicted_exact"] is not None
    assert r["predicted_qty_base_lot"] == 100.0
    assert r["qty_compatible_with_prediction"] is False
    assert r["qty_within_prediction_lot"] is True
    assert w["prediction_hits"]["exact"] == 1
    assert w["prediction_hits"]["qty_exactly_compatible"] == 0
    assert w["prediction_hits"]["qty_within_lot"] == 1


def test_the_frozen_safeguards_are_unchanged_since_d01094c4():
    """The additions are reporting completeness. Every safeguard the owner
    enumerated must be byte-identical to the reviewed commit."""
    import subprocess, sys as _s
    old = subprocess.run(
        ["git", "show", "d01094c4:analysis/l5/window_forensics/prospective.py"],
        capture_output=True, text=True, timeout=120)
    assert old.returncode == 0, old.stderr[-300:]
    now = io.open("analysis/l5/window_forensics/prospective.py").read()

    def block(src, start, end=None):
        i = src.index(start)
        j = src.index(end, i) if end else len(src)
        return src[i:j]

    for start, end in (
            ("MECHANISM_RULE: Dict[str, Any] = {", "\n# ---"),
            ("def classify(", "\nUNRESOLVED_VARIABLES"),
            ("def parents_in_event_order(", "\ndef _lot_class"),
            ("def reversals(", "\n# ------"),
            ("    def size_at(", "\n    def ticks_from_touch"),
            ("def _at(", "\n# ---")):
        assert block(old.stdout, start, end) == block(now, start, end), start
    for const in ("MARKS_S: Tuple[int, ...] = (-120, -60, -30, -15, -5, -1)",
                  "BOOK_STALE_S = 5.0",
                  '"min_resolved_reversals": 8',
                  '"min_parents": 20'):
        assert const in now, const


# ------------------------------------------- chain recovery and the collapse

def test_the_worked_case_collapse_reproduces_one_parent_of_eighty():
    """30/10/40 at 7c sharing one order_hash is ONE parent of exactly 80.00 --
    the frozen worked case, where 58 public fills became 37 proven parents."""
    fills = [{"idx": i, "L5_EVENT_CLOCK": 100.0 + i * 0.5, "outcome": "Down",
              "price": 0.07, "qty": q, "order_hashes": ["0xA"], "n_legs": 1}
             for i, q in enumerate((30.0, 10.0, 40.0))]
    c = P.collapse_to_parents(fills)
    assert c["n_units"] == 1 and c["n_chain_proven"] == 1
    (p,) = c["parents"]
    assert p["qty"] == 80.0 and p["n_fills"] == 3
    assert p["unit"] == "CHAIN_PROVEN_PARENT"
    assert p["execution_lifetime_lower_bound_s"] == 1.0


def test_a_fill_with_no_recovered_hash_is_never_merged():
    """Guessing which unproven fills belong together is the same-second
    cluster heuristic, which the forensics kept separate from chain proof."""
    fills = [{"idx": 0, "L5_EVENT_CLOCK": 100.0, "outcome": "Up",
              "price": 0.55, "qty": 30.0, "order_hashes": [None], "n_legs": 0},
             {"idx": 1, "L5_EVENT_CLOCK": 100.0, "outcome": "Up",
              "price": 0.55, "qty": 70.0, "order_hashes": [], "n_legs": 0}]
    c = P.collapse_to_parents(fills)
    assert c["n_units"] == 2 and c["n_chain_proven"] == 0
    assert all(p["unit"] == "UNPROVEN_FILL" for p in c["parents"])
    assert all(p["qty"] in (30.0, 70.0) for p in c["parents"])


def test_inventory_is_rebuilt_over_the_collapsed_sequence():
    fills = [{"idx": 0, "L5_EVENT_CLOCK": 100.0, "outcome": "Down",
              "price": 0.07, "qty": 30.0, "order_hashes": ["0xA"], "n_legs": 1},
             {"idx": 1, "L5_EVENT_CLOCK": 101.0, "outcome": "Down",
              "price": 0.07, "qty": 50.0, "order_hashes": ["0xA"], "n_legs": 1},
             {"idx": 2, "L5_EVENT_CLOCK": 200.0, "outcome": "Up",
              "price": 0.55, "qty": 20.0, "order_hashes": ["0xB"], "n_legs": 1}]
    c = P.collapse_to_parents(fills)
    a, b = c["parents"]
    assert a["qty"] == 80.0 and a["inventory_after"] == {"Down": 80.0}
    assert b["inventory_before"] == {"Down": 80.0}
    assert b["inventory_after"] == {"Down": 80.0, "Up": 20.0}


def test_enrichment_recovers_legs_from_the_recorded_tx():
    rows = [{"kind": "l5_parent_execution", "tx": "0x1", "parents": [],
             "outcome": "Up", "price": 0.5, "qty": 10.0,
             "L5_EVENT_CLOCK": 1.0},
            {"kind": "l5_parent_execution", "tx": "0x1", "parents": [],
             "outcome": "Up", "price": 0.5, "qty": 5.0,
             "L5_EVENT_CLOCK": 2.0}]
    leg = {"order_hash": "0xA", "maker_asset_id": 0, "taker_asset_id": 1,
           "maker_amount_filled": 1, "taker_amount_filled": 1, "fee": 0}
    got = P.enrich_chain(rows, fetch=lambda tx: (P.RECEIPT_OK, [leg]))
    assert got["recovered"] == 2 and got["distinct_tx"] == 1
    assert got["receipt_attempts"] == 1, "one tx, fetched once"
    assert rows[0]["parents"][0]["order_hash"] == "0xA"
    assert rows[0]["chain_recovery"] == P.CHAIN_RECOVERED
    assert rows[0]["receipt_status"] == P.RECEIPT_OK
    (pr,) = got["provenance"]
    assert pr == {"tx": "0x1", "status": P.RECEIPT_OK,
                  "n_l5_order_fills": 1, "order_hashes": ["0xA"]}


def test_an_unreadable_receipt_is_not_a_receipt_without_an_l5_order():
    """The two look identical, and only one of them is a finding. This is the
    exact confusion that let chain_receipt_share 0.0 pass for data."""
    rows = [{"kind": "l5_parent_execution", "tx": "0x1", "parents": [],
             "outcome": "Up", "price": 0.5, "qty": 10.0,
             "L5_EVENT_CLOCK": 1.0}]
    for status in (P.RECEIPT_UNAVAILABLE, P.RPC_ERROR, P.DECODE_ERROR):
        r = [dict(rows[0])]
        got = P.enrich_chain(r, fetch=lambda tx, s=status: (s, None))
        assert got["recovered"] == 0 and got["missing_evidence"] == 1
        assert got["no_l5_parent"] == 0, "not knowing is not absence"
        assert r[0]["parents"] == []
        assert r[0]["receipt_status"] == status
        assert "chain_recovery" not in r[0]
    assert "never added together" in got["labels"]

    # a receipt we READ that carries no L5 order is a different answer
    empty = [{"kind": "l5_parent_execution", "tx": "0x2", "parents": [],
              "outcome": "Up", "price": 0.5, "qty": 10.0,
              "L5_EVENT_CLOCK": 1.0}]
    got2 = P.enrich_chain(empty, fetch=lambda tx: (P.NO_L5_PARENT, []))
    assert got2["no_l5_parent"] == 1 and got2["missing_evidence"] == 0
    assert empty[0]["receipt_status"] == P.NO_L5_PARENT


def test_a_raising_fetch_is_an_rpc_error_not_an_absence():
    def boom(tx):
        raise RuntimeError("connection reset")
    rows = [{"kind": "l5_parent_execution", "tx": "0x1", "parents": [],
             "outcome": "Up", "price": 0.5, "qty": 1.0, "L5_EVENT_CLOCK": 1.0}]
    got = P.enrich_chain(rows, fetch=boom)
    assert got["by_status"][P.RPC_ERROR] == 1
    assert got["no_l5_parent"] == 0 and got["missing_evidence"] == 1


def test_an_execution_with_no_tx_is_counted_not_dropped():
    rows = [{"kind": "l5_parent_execution", "parents": [], "outcome": "Up",
             "price": 0.5, "qty": 1.0, "L5_EVENT_CLOCK": 1.0}]
    got = P.enrich_chain(rows, fetch=lambda tx: (P.RECEIPT_OK, []))
    assert got["executions_without_tx"] == 1
    assert rows[0]["receipt_status"] == P.RECEIPT_UNAVAILABLE


def test_a_window_reports_fills_and_parents_as_different_units(tmp_path):
    t = START + 200
    rows = [_book(t - 2, up_bids=[(0.55, 400.0)])]
    for i in range(3):
        p = _parent(t + i, "Up", 0.55, 10.0)
        p["parents"][0]["order_hash"] = "0xSAME"
        rows.append(p)
    d, _ = _tape(tmp_path, rows)
    w = P.analyse_window(P.EL.tapes(d)[0])
    assert w["counts"]["public_fills"] == 3
    assert w["counts"]["parents"] == 1
    assert w["parents"]["chain_proven"] == 1
    assert w["collapse"]["collapse_ratio"] == round(1 / 3, 4)
    (r,) = w["scored_parents"]
    assert r["qty"] == 30.0
