"""The reporting-only book evidence module.

The constants are the live 1500z record: a first leg of 3.00 UP at own
0.4400 while the authenticated book read bid 0.1200 / ask 0.1300, the
complement quoted 0.8500 for a pair of 1.2900 against the 0.96 ceiling.
"""

from __future__ import annotations

import ast
import inspect
import os
from decimal import Decimal as D

import pytest

import src.l5_book_evidence as BE


SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "l5_book_evidence.py")

# the live 1500z book, wire (YES/UP) terms
ROW = {
    "t": 1000.0, "observed_t": 1000.0, "updates": 7,
    "bids": [("0.1200", "5830.0000"), ("0.1100", "900.0000"),
             ("0.1000", "1500.0000")],
    "asks": [("0.1300", "362.0000"), ("0.1400", "800.0000"),
             ("0.1500", "2000.0000")],
    "bids_served": True, "asks_served": True, "n_bids": 3, "n_asks": 3,
}


# ------------------------------------------------------- reporting only

def test_imports_nothing_from_the_execution_stack():
    """The boundary is structural, not promised: stdlib only, and nothing
    from the execution stack, so this module cannot reach a venue."""
    tree = ast.parse(open(SRC).read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= {"__future__", "decimal", "typing",
                    "datetime"}, mods


def test_module_exposes_no_submit_or_cancel_surface():
    for name in dir(BE):
        if name.startswith("_"):
            continue
        low = name.lower()
        assert "submit" not in low and "cancel" not in low, name


# ------------------------------------------------------------- the ring

def test_ring_keeps_history_so_an_event_time_book_is_reconstructable():
    r = BE.BookRing(max_rows=10)
    r.append("m", dict(ROW, t=100.0), 100.0)
    r.append("m", dict(ROW, t=200.0, bids=[("0.2000", "5.0000")]), 200.0)
    got = r.as_of("m", 150.0)
    assert got["basis"] == BE.SYNC_AT_EVENT
    assert got["row"]["t"] == 100.0
    assert got["frame_age_at_event_s"] == 50.0
    assert got["superseded_at_t"] == 200.0
    assert got["frame_held_for_s"] == 100.0


def test_as_of_refuses_rather_than_guessing_before_the_ring_starts():
    r = BE.BookRing()
    r.append("m", dict(ROW, t=500.0), 500.0)
    got = r.as_of("m", 100.0)
    assert got["basis"] == BE.SYNC_BEFORE_RING
    assert got["row"] is None
    assert got["ring_starts_after_event_by_s"] == 400.0


def test_as_of_says_so_when_there_is_no_event_time():
    r = BE.BookRing()
    r.append("m", ROW, 1.0)
    assert r.as_of("m", None)["basis"] == BE.SYNC_NO_EVENT_TIME


def test_ring_is_bounded_in_rows():
    r = BE.BookRing(max_rows=3)
    for i in range(20):
        r.append("m", dict(ROW, t=float(i),
                           bids=[("0.%02d" % (i + 10), "1")]), float(i))
    assert len(r.rows("m")) == 3
    assert r.latest("m")["t"] == 19.0


def test_ring_is_bounded_in_slugs():
    r = BE.BookRing(max_slugs=2)
    for i in range(5):
        r.append("m%d" % i, ROW, 1.0)
    assert len(r.report()["slugs"]) == 2
    assert r.dropped_slugs == 3


def test_identical_frames_are_deduped_not_stored_twice():
    r = BE.BookRing()
    assert r.append("m", dict(ROW, t=1.0), 1.0) is True
    assert r.append("m", dict(ROW, t=2.0), 2.0) is False
    assert r.deduped == 1


def test_a_side_never_served_is_unknown_not_empty():
    r = BE.BookRing()
    r.append("m", {"bids": [("0.12", "5")], "asks": None, "t": 1.0}, 1.0)
    row = r.latest("m")
    assert row["asks_served"] is False and row["n_asks"] is None
    assert row["bids_served"] is True


def test_ring_never_raises_into_the_feed():
    r = BE.BookRing()

    class Bad:
        @property
        def books(self):
            raise RuntimeError("feed")

    assert r.observe_ladder(Bad(), 1.0) == 0
    assert any("observe_ladder" in k for k in r.errors)
    assert r.append("m", object(), 1.0) is False


def test_observe_ladder_reads_the_same_rungs_the_money_path_models():
    class Ladder:
        books = {"m": {"bids": [(D("0.12"), D("5830"))],
                       "asks": [(D("0.13"), D("362"))],
                       "t": 42.0, "updates": 3}}

    r = BE.BookRing()
    assert r.observe_ladder(Ladder(), 50.0) == 1
    row = r.latest("m")
    assert row["t"] == 42.0 and row["observed_t"] == 50.0
    assert row["bids"] == [("0.12", "5830")]


# ----------------------------------------------- reachability arithmetic

def test_down_complement_own_cost_is_one_minus_the_wire_ask():
    """Holding UP, the complement is DOWN; joining the ask at 0.1300 costs
    0.8700 of our own money, and the live engine quoted 0.8500."""
    got = BE.complement_reachability(held_side="UP",
                                     first_leg_own_px="0.44",
                                     qty_needed="3", row=ROW)
    assert got["complement_side"] == "DOWN"
    tob = got["opposite_top_of_book"]
    assert tob["maker_join_wire_px"] == "0.1300"
    assert tob["maker_join_own_px"] == "0.8700"
    assert tob["wire_side_joined"] == "ASK"


def test_up_complement_own_cost_is_the_wire_bid():
    got = BE.complement_reachability(held_side="DOWN", row=ROW)
    tob = got["opposite_top_of_book"]
    assert got["complement_side"] == "UP"
    assert tob["maker_join_own_px"] == "0.1200"
    assert tob["wire_side_joined"] == "BID"


def test_the_live_1500z_pair_is_reported_above_the_ceiling():
    got = BE.complement_reachability(held_side="UP",
                                     first_leg_own_px="0.44",
                                     qty_needed="3", row=ROW)
    pm = got["pair_if_maker"]
    assert pm["pair_own_cost"] == "1.3100"          # 0.44 + 0.87
    assert pm["clears_pair_ceiling"] is False
    assert D(pm["margin_to_ceiling"]) < 0


def test_a_reachable_complement_is_reported_as_clearing_the_ceiling():
    row = dict(ROW, asks=[("0.5000", "100")])
    got = BE.complement_reachability(held_side="UP",
                                     first_leg_own_px="0.44",
                                     qty_needed="3", row=row)
    assert got["opposite_top_of_book"]["maker_join_own_px"] == "0.5000"
    assert got["pair_if_maker"]["pair_own_cost"] == "0.9400"
    assert got["pair_if_maker"]["clears_pair_ceiling"] is True


def test_queue_ahead_is_a_bound_and_counts_better_rungs_plus_our_own():
    got = BE.complement_reachability(held_side="DOWN", qty_needed="3",
                                     row=ROW)
    # joining the bid at 0.1200: nothing is better-priced, our rung is 5830
    assert got["maker_quote"]["queue_ahead_bound"] == "5830.0000"
    assert got["maker_quote"]["queue_ahead_multiple_of_need"] == "1943.33"
    assert "not an executable opportunity" in \
        got["maker_quote"]["basis"].lower()


def test_maker_quote_never_claims_a_fill():
    got = BE.complement_reachability(held_side="UP", row=ROW)
    blob = (got["maker_quote"]["basis"] + got["labels"]).lower()
    assert "does not imply a maker fill" in blob
    assert "not guarantee" in blob


def test_taker_walk_crosses_displayed_depth_and_prices_the_vwap():
    """Holding UP, 3 DOWN as a taker hits the bid at 0.1200: own 0.8800,
    and 5830 displayed is far more than the 3 needed, so one level."""
    got = BE.complement_reachability(held_side="UP", first_leg_own_px="0.44",
                                     qty_needed="3", row=ROW)
    w = got["taker_walk"]
    assert w["displayed_qty_crossed"] == "3"
    assert w["vwap_own_px"] == "0.880000"
    assert w["levels_used"] == 1
    assert w["insufficient_displayed_depth"] is False
    assert got["pair_if_taker_walk"]["pair_own_cost"] == "1.320000"


def test_taker_walk_spans_levels_and_flags_thin_depth():
    row = dict(ROW, bids=[("0.1200", "1.0000"), ("0.1100", "1.0000")])
    got = BE.complement_reachability(held_side="UP", qty_needed="3", row=row)
    w = got["taker_walk"]
    assert w["levels_used"] == 2
    assert D(w["displayed_qty_crossed"]) == D(2)
    assert w["insufficient_displayed_depth"] is True


def test_depth_is_never_called_guaranteed():
    got = BE.complement_reachability(held_side="UP", qty_needed="3", row=ROW)
    assert "not guaranteed" in got["taker_walk"]["basis"]


def test_unreadable_inputs_refuse_rather_than_defaulting():
    assert BE.complement_reachability(held_side="sideways")["error"] == \
        "UNREADABLE_HELD_SIDE"
    assert BE.complement_reachability(held_side="UP", row=None)["error"] == \
        "NO_BOOK_ROW"
    got = BE.complement_reachability(held_side="UP", row=ROW)
    assert got["pair_if_maker"]["pair_own_cost"] is None
    assert got["pair_if_maker"]["why"] == "no_first_leg_price"


# ------------------------------------------------------- the fill record

def test_fill_record_carries_both_time_bases_separately():
    r = BE.BookRing()
    r.append("m", dict(ROW, t=100.0), 100.0)
    r.append("m", dict(ROW, t=120.0, asks=[("0.5000", "100")]), 120.0)
    rec = BE.fill_evidence(slug="m", coid="l5f-1", side="UP", fill_qty="3",
                           fill_own_px="0.44", venue_event_t=110.0,
                           processing_t=125.0, residual_after="3", ring=r)
    assert rec["processing_lag_s"] == 15.0
    ev, pr = rec["book_at_event"], rec["book_at_processing"]
    assert ev["sync"]["basis"] == BE.SYNC_AT_EVENT
    assert ev["row"]["t"] == 100.0
    assert pr["sync"]["basis"] == BE.SYNC_PROCESSING
    assert pr["row"]["t"] == 120.0
    # and the two bases DISAGREE, which is the whole reason to carry both
    assert ev["reachability"]["pair_if_maker"]["clears_pair_ceiling"] is False
    assert pr["reachability"]["pair_if_maker"]["clears_pair_ceiling"] is True


def test_fill_record_states_when_the_event_book_is_unavailable():
    rec = BE.fill_evidence(slug="m", coid="c", side="UP", fill_qty="3",
                           venue_event_t=None, processing_t=5.0)
    assert rec["book_at_event"]["sync"]["basis"] == BE.SYNC_NO_EVENT_TIME
    assert rec["processing_lag_s"] is None


def test_fill_record_records_the_opposite_side_at_the_fill():
    rec = BE.fill_evidence(slug="m", coid="c", side="UP", fill_qty="3",
                           fill_own_px="0.44", processing_t=1.0,
                           book_at_processing=ROW)
    tob = rec["book_at_processing"]["reachability"]["opposite_top_of_book"]
    assert tob["maker_join_own_px"] == "0.8700"
    assert tob["taker_cross_own_px"] == "0.8800"
    assert tob["maker_join_displayed_qty"] == "362.0000"
    assert tob["taker_cross_displayed_qty"] == "5830.0000"


def test_sync_labels_distinguish_the_two_books_in_words():
    rec = BE.fill_evidence(slug="m", coid="c", side="UP", fill_qty="1")
    assert "book_at_event" in rec["sync_labels"]
    assert "processing lag" in rec["sync_labels"]


# ---------------------------------------------------- residual sampling

def test_sampler_fires_with_no_order_resting():
    s = BE.ResidualSampler(every_s=5.0)
    rec = s.sample(slug="m", now=10.0, residual="3", held_side="UP",
                   first_leg_own_px="0.44", order_resting=False, book=ROW)
    assert rec is not None
    assert rec["order_resting"] is False
    assert rec["sampler"]["driven_by"] == \
        "UNMATCHED_INVENTORY_NOT_ORDER_STATE"
    assert rec["reachability"]["pair_if_maker"]["pair_own_cost"] == "1.3100"


def test_sampler_is_silent_when_inventory_is_matched():
    s = BE.ResidualSampler()
    assert s.sample(slug="m", now=1.0, residual="0", book=ROW) is None
    assert s.sample(slug="m", now=2.0, residual=None, book=ROW) is None


def test_sampler_respects_its_interval_then_fires_again():
    s = BE.ResidualSampler(every_s=5.0)
    assert s.sample(slug="m", now=10.0, residual="3", book=ROW) is not None
    assert s.sample(slug="m", now=12.0, residual="3", book=ROW) is None
    assert s.sample(slug="m", now=15.0, residual="3", book=ROW) is not None
    assert s.emitted == 2 and s.skipped == 1


def test_sampler_interval_resets_once_inventory_is_matched():
    s = BE.ResidualSampler(every_s=5.0)
    s.sample(slug="m", now=10.0, residual="3", book=ROW)
    s.sample(slug="m", now=11.0, residual="0", book=ROW)
    assert s.sample(slug="m", now=11.5, residual="3", book=ROW) is not None


def test_sampler_infers_the_held_side_from_the_sign_of_the_residual():
    s = BE.ResidualSampler()
    up = s.sample(slug="m", now=1.0, residual="3", book=ROW)
    s.reset("m")
    dn = s.sample(slug="m", now=2.0, residual="-3", book=ROW)
    assert up["held_side"] == "UP" and up["reachability"][
        "complement_side"] == "DOWN"
    assert dn["held_side"] == "DOWN" and dn["reachability"][
        "complement_side"] == "UP"


def test_sampler_reports_book_staleness():
    s = BE.ResidualSampler()
    rec = s.sample(slug="m", now=1050.0, residual="3",
                   book=dict(ROW, t=1000.0))
    assert rec["book"]["sync"]["row_age_s"] == 50.0
    assert rec["book"]["sync"]["basis"] == BE.SYNC_PROCESSING


def test_sampler_falls_back_to_the_ring_when_no_book_is_handed_over():
    r = BE.BookRing()
    r.append("m", dict(ROW, t=9.0), 9.0)
    s = BE.ResidualSampler()
    rec = s.sample(slug="m", now=10.0, residual="3", ring=r)
    assert rec["book"]["row"]["t"] == 9.0


def test_force_bypasses_the_interval_but_never_the_matched_check():
    s = BE.ResidualSampler(every_s=99.0)
    s.sample(slug="m", now=1.0, residual="3", book=ROW)
    assert s.sample(slug="m", now=2.0, residual="3", book=ROW,
                    force=True) is not None
    assert s.sample(slug="m", now=3.0, residual="0", book=ROW,
                    force=True) is None


@pytest.mark.parametrize("bad", [float("nan"), "abc", None, object()])
def test_unreadable_numbers_never_raise(bad):
    s = BE.ResidualSampler()
    s.sample(slug="m", now=bad, residual="3", book=ROW)
    BE.fill_evidence(slug="m", coid="c", side="UP", fill_qty=bad,
                     fill_own_px=bad, venue_event_t=bad, processing_t=bad)
    BE.complement_reachability(held_side="UP", first_leg_own_px=bad,
                               qty_needed=bad, row=ROW)


def test_records_are_labelled_reporting_only():
    s = BE.ResidualSampler()
    rec = s.sample(slug="m", now=1.0, residual="3", book=ROW)
    assert "REPORTING ONLY" in rec["labels"]
    assert "REPORTING ONLY" in BE.BookRing().report()["labels"]
    assert "REPORTING ONLY" in BE.complement_reachability(
        held_side="UP", row=ROW)["labels"]


def test_signatures_take_no_executor_or_client():
    for fn in (BE.fill_evidence, BE.complement_reachability,
               BE.ResidualSampler.sample):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"executor", "client", "runner", "session",
                             "order", "safety"}, (fn, params)


# ------------------------------- the engine rests behind the touch

def test_depth_ticks_reproduces_the_live_frozen_complement_price():
    """THE CALIBRATION. At the 1500z fill the ask was 0.4700 and the engine
    logged `frozen_price_superseded: "0.5100"` for its DOWN complement.
    Two ticks behind the touch: 1 - (0.47 + 0.02) = 0.51.
    """
    row = dict(ROW, bids=[("0.4600", "118.97")], asks=[("0.4700", "500")])
    got = BE.complement_reachability(held_side="UP", first_leg_own_px="0.44",
                                     qty_needed="3", row=row, depth_ticks=2)
    assert got["opposite_top_of_book"]["maker_join_own_px"] == "0.5300"
    bt = got["maker_quote_behind_touch"]
    assert bt["wire_px"] == "0.4900"
    assert bt["own_px"] == "0.5100"
    assert got["pair_if_maker"]["pair_own_cost"] == "0.9700"
    assert got["pair_if_maker_behind_touch"]["pair_own_cost"] == "0.9500"


@pytest.mark.parametrize("side", ["UP", "DOWN"])
def test_resting_behind_the_touch_is_cheaper_on_both_sides(side):
    """A less aggressive bid costs LESS if it fills, whichever side we need.
    Getting this backwards would invert every pair figure, so it is pinned
    on both sides and by exactly the tick count."""
    row = dict(ROW, bids=[("0.4600", "1")], asks=[("0.4700", "1")])
    got = BE.complement_reachability(held_side=side,
                                     first_leg_own_px="0.44", row=row,
                                     depth_ticks=3)
    at = D(got["pair_if_maker"]["pair_own_cost"])
    bt = D(got["pair_if_maker_behind_touch"]["pair_own_cost"])
    assert bt == at - D("0.03"), (side, at, bt)


def test_the_cheaper_price_is_not_called_more_reachable():
    """Cheaper and likelier-to-fill are opposite here, and the record must
    say so, or a reader takes the cheaper pair as better evidence."""
    got = BE.complement_reachability(held_side="UP", row=ROW, depth_ticks=2)
    assert "less" in got["maker_quote_behind_touch"]["basis"].lower()
    assert "likely to fill" in got["maker_quote_behind_touch"]["basis"]
    assert "less likely to fill" in got["labels"]


def test_depth_ticks_reproduces_the_live_refusal_too():
    """The 15:04:37 refusal: ask 0.1300, complement 0.8500, pair 1.2900."""
    row = dict(ROW, bids=[("0.1200", "5830")], asks=[("0.1300", "362")])
    got = BE.complement_reachability(held_side="UP", first_leg_own_px="0.44",
                                     qty_needed="3", row=row, depth_ticks=2)
    assert got["maker_quote_behind_touch"]["own_px"] == "0.8500"
    assert got["pair_if_maker_behind_touch"]["pair_own_cost"] == "1.2900"
    assert got["pair_if_maker_behind_touch"]["clears_pair_ceiling"] is False


def test_behind_touch_is_absent_rather_than_assumed_when_not_supplied():
    got = BE.complement_reachability(held_side="UP", row=ROW)
    bt = got["maker_quote_behind_touch"]
    assert bt["own_px"] is None and bt["depth_ticks"] is None
    assert got["pair_if_maker_behind_touch"]["pair_own_cost"] is None
    assert "AT-TOUCH pair" in got["labels"]


def test_depth_ticks_threads_through_both_record_builders():
    row = dict(ROW, bids=[("0.4600", "1")], asks=[("0.4700", "1")])
    rec = BE.fill_evidence(slug="m", coid="c", side="UP", fill_qty="3",
                           fill_own_px="0.44", processing_t=1.0,
                           book_at_processing=row, depth_ticks=2)
    assert rec["book_at_processing"]["reachability"][
        "maker_quote_behind_touch"]["own_px"] == "0.5100"
    s = BE.ResidualSampler()
    samp = s.sample(slug="m", now=1.0, residual="3", held_side="UP",
                    first_leg_own_px="0.44", book=row, depth_ticks=2)
    assert samp["reachability"]["pair_if_maker_behind_touch"][
        "pair_own_cost"] == "0.9500"


# ------------------------------------- the venue's own event time

def test_venue_event_t_never_substitutes_our_receipt():
    """`_ack_epoch` on the money path falls back to now(); this must not,
    or an unknown fill time becomes a confident one."""
    assert BE.venue_event_t({}) == (None, None)
    assert BE.venue_event_t(None) == (None, None)
    assert BE.venue_event_t({"transactTime": None}) == (None, None)
    assert BE.venue_event_t({"transactTime": "not-a-time"}) == (None, None)


def test_venue_event_t_reads_seconds_and_milliseconds():
    t, k = BE.venue_event_t({"transactTime": 1792854089.7078202})
    assert k == "transactTime" and abs(t - 1792854089.7078202) < 1e-6
    t2, _ = BE.venue_event_t({"transactTime": 1792854089707})
    assert abs(t2 - 1792854089.707) < 1e-3


def test_venue_event_t_reads_iso_with_z():
    t, k = BE.venue_event_t({"created_at": "2026-10-24T15:01:29.700000Z"})
    assert k == "created_at" and abs(t - 1792854089.7) < 1.0


def test_venue_event_t_prefers_the_money_paths_key_order():
    t, k = BE.venue_event_t({"createdAt": 1792854000.0,
                             "transact_time": 1792854089.0})
    assert k == "transact_time" and t == 1792854089.0


def test_venue_event_t_rejects_implausible_epochs():
    for bad in (0, -1, 12345, 1e20, float("nan")):
        assert BE.venue_event_t({"transactTime": bad}) == (None, None), bad


# --------------------------------- the recorders report on themselves

def test_ring_report_distinguishes_attached_from_silent():
    """Zero fill records on a flat window must not look like a missing
    module, which is the whole reason the window summary carries this."""
    r = BE.BookRing()
    rep = r.report()
    assert rep["appended"] == 0 and rep["slugs"] == {}
    r.append("m", ROW, 1.0)
    rep = r.report()
    assert rep["appended"] == 1 and rep["slugs"] == {"m": 1}
    assert rep["errors"] == {}


def test_sampler_report_shows_it_was_asked_even_when_silent():
    s = BE.ResidualSampler(every_s=5.0)
    s.sample(slug="m", now=1.0, residual="0", book=ROW)      # matched: silent
    rep = s.report()
    assert rep["emitted"] == 0
    s.sample(slug="m", now=2.0, residual="3", book=ROW)
    assert s.report()["emitted"] == 1


def test_reports_never_raise_on_a_damaged_ring():
    r = BE.BookRing()
    r._bump("append:TypeError")
    rep = r.report()
    assert rep["errors"] == {"append:TypeError": 1}


# ------------------------------- the label must not overclaim the clock

def test_the_located_frame_is_not_called_a_venue_instant_snapshot():
    """The owner's constraint, pinned: do not claim the observed book is a
    synchronized snapshot of the exact venue fill instant. Frame boundaries
    are OUR receipt clock, so the name must not say otherwise."""
    assert "VENUE_EVENT_TIME_SYNCHRONIZED" != BE.SYNC_AT_EVENT
    assert "LOCAL_RECEIPT" in BE.SYNC_AT_EVENT
    assert "synchronised to the exact venue instant" in BE.SYNC_LABELS


def test_clock_domains_are_disclosed_on_every_as_of_answer():
    r = BE.BookRing()
    r.append("m", dict(ROW, t=100.0), 100.0)
    for ev in (150.0, 1.0, None):
        got = r.as_of("m", ev)
        cd = got["clock_domains"]
        assert cd["offset_measured"] is False
        assert "LOCAL_RECEIPT" in cd["frame_boundaries"]
        assert "VENUE" in cd["event_time"]
        assert "never as the book at the exact venue instant" in \
            cd["consequence"]


def test_clock_domain_disclosure_reaches_the_fill_record():
    r = BE.BookRing()
    r.append("m", dict(ROW, t=100.0), 100.0)
    rec = BE.fill_evidence(slug="m", coid="c", side="UP", fill_qty="3",
                           venue_event_t=150.0, processing_t=160.0, ring=r)
    assert rec["book_at_event"]["sync"]["clock_domains"][
        "offset_measured"] is False
    assert "not a snapshot synchronised" in rec["sync_labels"] or \
        "synchronised to the exact venue instant" in rec["sync_labels"]


# ------------------------- a MODELLED fill must not borrow a venue clock

def test_a_modelled_fill_gets_no_event_time_book():
    """The shadow path passes venue_event_t=None deliberately: a modelled
    fill has no venue timestamp, and substituting our own would manufacture
    an event-time book out of nothing."""
    r = BE.BookRing()
    r.append("m", dict(ROW, t=100.0), 100.0)
    rec = BE.fill_evidence(slug="m", coid="shadow|BASELINE", side="YES",
                           fill_qty=None, fill_own_px="0.44",
                           venue_event_t=None, processing_t=120.0,
                           residual_after="3", ring=r, depth_ticks=2)
    assert rec["book_at_event"]["sync"]["basis"] == BE.SYNC_NO_EVENT_TIME
    assert rec["book_at_event"]["row"] is None
    # but the processing-time book IS available and fully priced
    pr = rec["book_at_processing"]["reachability"]
    assert pr["pair_if_maker_behind_touch"]["pair_own_cost"] is not None
    assert rec["processing_lag_s"] is None


def test_yes_and_no_are_read_as_up_and_down():
    """The shadow controller reports sides as YES/NO, the money path as
    UP/DOWN, and one record builder serves both."""
    for word, want in (("YES", "UP"), ("NO", "DOWN")):
        got = BE.complement_reachability(held_side=word, row=ROW)
        assert got["held_side"] == want


def test_missing_fill_quantity_is_absent_not_zero():
    """A modelled fill carries no quantity; the record must say so rather
    than imply a zero-size fill."""
    rec = BE.fill_evidence(slug="m", coid="c", side="YES", fill_qty=None,
                           fill_own_px="0.44", processing_t=1.0,
                           book_at_processing=ROW)
    assert rec["fill_qty"] is None
