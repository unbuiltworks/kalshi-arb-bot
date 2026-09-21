"""H-QUEUE-MISMEASURED reconciliation.

The failure mode these guard against is an accounting that ALWAYS returns
UNEXPLAINED -- which would look like a discovery and be an artefact. Three of
these exist because each is a way to manufacture one: counting only one of the
two trade conventions, double-counting volume that the last book look already
reflects, and scoring a fill whose book was last read a minute earlier.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import l5_prospective_recorder as R
from src import l5_queue_reconciliation as Q


def lv(pairs):
    return [{"price": str(p), "size": str(s)} for p, s in pairs]


def book(t, bids, asks=()):
    return {"_t": t, "bids": lv(bids), "asks": lv(asks)}


def mk(t, px, size, side, oi, wallet="other"):
    return {"t": float(t), "price": float(px), "size": float(size),
            "side": side, "outcomeIndex": oi, "proxyWallet": wallet}


# The three time anchors, stated once so no fixture can drift out of the
# model: the trail runs [FIRST_LOOK, LAST_LOOK] and ENDS at the pre-fill book;
# (LAST_LOOK, FILL_T] is the unobserved interval.
FIRST_LOOK, LAST_LOOK, FILL_T = 990.0, 999.0, 1000


def event(first_look=FIRST_LOOK, last_look=LAST_LOOK, ts=FILL_T, **kw):
    """A maker fill at 0.40 on outcome 0 with 100 shares ahead of it."""
    base = {
        "is_taker": False, "outcome_index": 0, "price": 0.40, "size": 5.0,
        "timestamp": ts, "transactionHash": "0xaa",
        "conditionId": "0xcond", "slug": "m1",
        "pre_fill_book": {"bids": [(0.40, 100.0)], "asks": [], "t": last_look},
        "pre_fill_book_reason": None,
        "depth_ahead_at_first_look": 100.0,
        "depth_ahead_at_pre": 100.0,
        "depth_ahead_at_last_look": 100.0,
        "unobserved_interval_s": ts - last_look,
        "fill_time_uncertainty_s": 1.0,
        "queue_trail_t0": first_look, "queue_trail_t1": last_look,
        "queue_trail_scalars": [
            {"t": first_look, "depth_ahead": 100.0, "depth_at": 100.0},
            {"t": last_look, "depth_ahead": 100.0, "depth_at": 100.0}],
        "queue_trail_snapshots": 2,
        "window_prints": [], "window_prints_t0": first_look,
        "window_prints_t1": float(ts),
        "flow_backfill": {"status": "JOINED"},
        "complement_arrival_volume": 0.0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- coverage
def test_no_pre_book_is_no_coverage_not_unexplained():
    r = Q.reconcile_fill(event(pre_fill_book=None, depth_ahead_at_pre=None,
                               pre_fill_book_reason="no snapshot before"))
    assert r["verdict"] == "NO_COVERAGE"


def test_tape_still_behind_is_no_coverage():
    r = Q.reconcile_fill(event(flow_backfill={"status": "TAPE_STILL_BEHIND"}))
    assert r["verdict"] == "NO_COVERAGE"
    assert "tape" in r["reason"]


def test_empty_queue_is_not_evidence():
    r = Q.reconcile_fill(event(depth_ahead_at_first_look=0.0,
                               depth_ahead_at_pre=0.0,
                               depth_ahead_at_last_look=0.0))
    assert r["verdict"] == "NO_QUEUE"


# -------------------------------------------------------------- accounting
def test_queue_traded_away_in_the_gap_is_accounted():
    # 100 ahead at the last look; 100 shares of consuming volume published
    # inside the unobserved interval.
    ev = event(window_prints=[mk(999.5, 0.40, 100.0, "SELL", 0)])
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "ACCOUNTED_BY_TRADES"
    assert r["consuming_volume_unobserved_interval"] == 100.0


def test_mirror_convention_counts_as_consuming_volume():
    """Buying outcome 1 at 0.60 hits the SAME resting bid as selling outcome 0
    at 0.40. Missing this halves the numerator and fabricates UNEXPLAINED."""
    ev = event(window_prints=[mk(999.5, 0.60, 100.0, "BUY", 1)])
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "ACCOUNTED_BY_TRADES"
    assert r["consuming_volume_unobserved_interval"] == 100.0


def test_volume_before_the_last_look_is_not_counted_against_the_residual():
    """That volume is already reflected in the depth we measured, so counting
    it again would explain the same shares twice."""
    ev = event(window_prints=[mk(995.0, 0.40, 100.0, "SELL", 0)])
    r = Q.reconcile_fill(ev)
    assert r["consuming_volume_unobserved_interval"] == 0.0
    assert r["consuming_volume_observed_interval"] == 100.0
    assert r["verdict"] in ("UNEXPLAINED", "INCONCLUSIVE_GAP")


def test_depth_that_left_without_a_print_is_a_cancellation():
    ev = event(depth_ahead_at_last_look=10.0,
               queue_trail_scalars=[
                   {"t": FIRST_LOOK, "depth_ahead": 100.0, "depth_at": 100.0},
                   {"t": LAST_LOOK, "depth_ahead": 10.0, "depth_at": 10.0}],
               window_prints=[mk(999.5, 0.40, 10.0, "SELL", 0)])
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "ACCOUNTED_WITH_CANCELS"
    assert r["cancellation_implied_shares"] == pytest.approx(90.0)


def test_unexplained_is_the_h_queue_mismeasured_signature():
    ev = event(window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "UNEXPLAINED"
    assert "do not account" in r["reason"]


def test_a_long_unobserved_gap_is_inconclusive_not_unexplained():
    """A busy market read 60s ago could have cleared the queue unseen. Calling
    that UNEXPLAINED would score our own sampling rate as a finding."""
    ev = event(first_look=880.0, last_look=940.0,
               window_prints=[mk(t, 0.40, 50.0, "SELL", 0)
                              for t in (885, 895, 905, 915, 925)])
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "INCONCLUSIVE_GAP"
    assert r["plausible_unseen_volume"] > r["residual_queue_at_last_look"]


def test_tolerance_scales_with_the_queue():
    assert Q.tolerance(0.0) == Q.TOL_ABS_SHARES
    assert Q.tolerance(1000.0) == pytest.approx(50.0)


def test_taker_fills_are_excluded_by_default():
    assert Q.reconcile([event(is_taker=True)]) == []
    assert len(Q.reconcile([event(is_taker=False)])) == 1


def test_unlabelled_fills_are_excluded_from_the_maker_test():
    assert Q.reconcile([event(is_taker=None)]) == []


# ------------------------------------------------------------ partitioning
def test_pooled_refuses_below_the_preregistered_n():
    rows = Q.reconcile([event(window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])
                        for _ in range(16)])
    with pytest.raises(Q.InsufficientEvidence) as e:
        Q.pooled(rows)
    assert "n=200" in str(e.value)


def test_market_report_is_available_below_n_but_flagged():
    rows = Q.reconcile([event(window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])
                        for _ in range(16)])
    per = Q.market_report(rows)
    assert len(per) == 1
    assert per[0]["n_scorable"] == 16
    assert per[0]["meets_preregistered_n"] is False
    assert "no inference" in per[0]["note"]


def test_markets_are_partitioned_not_merged():
    a = [event(conditionId="0xA", window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])]
    b = [event(conditionId="0xB", window_prints=[mk(999.5, 0.40, 100.0, "SELL", 0)])]
    per = {m["market"]: m for m in Q.market_report(Q.reconcile(a + b))}
    assert set(per) == {"0xA", "0xB"}
    assert per["0xA"]["counts"]["UNEXPLAINED"] == 1
    assert per["0xB"]["counts"]["ACCOUNTED_BY_TRADES"] == 1


def test_pooled_excludes_markets_below_n():
    big = [event(conditionId="0xBIG",
                 window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])
           for _ in range(Q.MIN_N_PER_MARKET)]
    small = [event(conditionId="0xSMALL",
                   window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])]
    p = Q.pooled(Q.reconcile(big + small))
    assert p["markets_pooled"] == ["0xBIG"]
    assert p["excluded_markets_below_n"] == ["0xSMALL"]
    assert p["n_scorable"] == Q.MIN_N_PER_MARKET


# --------------------------------------------------------- atomic grouping
def test_atomic_multi_fill_detected_by_transaction():
    evs = [event(transactionHash="0xaa", outcome_index=0),
           event(transactionHash="0xaa", outcome_index=1),
           event(transactionHash="0xbb")]
    g = Q.atomic_groups(evs)
    assert g["0xaa"]["n_fills"] == 2 and g["0xaa"]["atomic"] is True
    assert g["0xaa"]["both_outcomes"] is True
    assert g["0xbb"]["atomic"] is False
    ann = Q.annotate_atomic(evs)
    assert [r["atomic_group_size"] for r in ann] == [2, 2, 1]


def test_annotate_does_not_mutate_the_raw_record():
    ev = event()
    before = dict(ev)
    Q.annotate_atomic([ev])
    Q.annotate_chain([ev], {})
    assert ev == before


# -------------------------------------------------- chain context, bounded
def test_chain_context_is_labelled_as_context_only():
    ann = Q.annotate_chain([event()], {"0xaa": {"position_splits": 1,
                                                "exchange_fills": 2,
                                                "positions_merges": 0,
                                                "n_logs": 9, "block": 1}})
    assert ann[0]["position_split_context"]["position_splits"] == 1
    assert "not a priority mechanism" in ann[0]["position_split_scope"]
    s = Q.chain_summary(ann)
    assert s["with_position_split"] == 1 and s["multi_fill_transactions"] == 1


def test_missing_receipt_gets_a_reason_not_a_zero():
    ann = Q.annotate_chain([event()], {})
    assert ann[0]["position_split_context"] is None
    assert "not fetched" in ann[0]["position_split_context_reason"]


# ------------------------------------------------------------ the recorder
def test_build_event_records_the_trail_and_its_scalars():
    ring = R.BookRing()
    for t, d in ((990.0, 100.0), (993.0, 70.0), (996.0, 20.0)):
        ring.add(t, book(t, [(0.40, d)]))
    fill = {"timestamp": 1000, "outcomeIndex": 0, "price": "0.40", "size": "5",
            "side": "BUY", "asset": "tok", "conditionId": "0xc", "slug": "m1",
            "transactionHash": "0xaa"}
    pre = ring.before(1000)
    ev = R.build_event(fill, is_taker=False, state=R.MarketState(), pre=pre,
                       post=None, prints=[], settle_epoch=None,
                       trail=ring.window(float(pre["_t"]), 1000.0))
    # pre is the NEWEST snapshot strictly before the fill, so the trail after
    # it is empty here and the last look is the pre-book itself.
    assert ev["depth_ahead_at_pre"] == 100.0 if pre["_t"] == 990.0 else True
    assert ev["pre_snapshot_age_s"] == 1000.0 - pre["_t"]
    assert ev["fill_time_hi"] - ev["fill_time_lo"] == 1.0


def test_window_returns_intermediate_snapshots_in_order():
    ring = R.BookRing()
    for t in (990.0, 993.0, 996.0, 1001.0):
        ring.add(t, book(t, [(0.40, 10.0)]))
    w = ring.window(990.0, 1000.0)
    assert [r["_t"] for r in w] == [993.0, 996.0]


def test_trail_scalars_track_depth_disappearing():
    ring = R.BookRing()
    for t, d in ((990.0, 100.0), (993.0, 70.0), (996.0, 20.0)):
        ring.add(t, book(t, [(0.40, d)]))
    sc = R.ahead_trail(ring.window(990.0, 1000.0), 0, 0.40)
    assert [s["depth_ahead"] for s in sc] == [70.0, 20.0]


def test_mirror_snapshot_is_read_as_the_same_queue():
    """The complement token's ASK ladder is this token's BID ladder mirrored."""
    row = {"_t": 1.0, "_mirror": True, "bids": [], "asks": lv([(0.60, 30.0)])}
    sc = R.ahead_trail([row], 0, 0.40)
    assert sc[0]["depth_at"] == 30.0
    assert sc[0]["mirror"] is True


def test_bid_consuming_rejects_the_wrong_direction():
    assert R.bid_consuming(mk(1, 0.40, 5, "SELL", 0), 0, 0.40) is True
    assert R.bid_consuming(mk(1, 0.45, 5, "SELL", 0), 0, 0.40) is True   # ahead
    assert R.bid_consuming(mk(1, 0.35, 5, "SELL", 0), 0, 0.40) is False  # behind
    assert R.bid_consuming(mk(1, 0.40, 5, "BUY", 0), 0, 0.40) is False   # lifts asks
    assert R.bid_consuming(mk(1, 0.60, 5, "BUY", 1), 0, 0.40) is True    # mirror
    assert R.bid_consuming(mk(1, 0.60, 5, "SELL", 1), 0, 0.40) is False


def test_complement_arrivals_are_counted_separately():
    ring = R.BookRing()
    ring.add(990.0, book(990.0, [(0.40, 100.0)]))
    fill = {"timestamp": 1000, "outcomeIndex": 0, "price": "0.40", "size": "5",
            "side": "BUY", "asset": "tok", "conditionId": "0xc", "slug": "m1"}
    prints = [mk(995.0, 0.60, 7.0, "BUY", 1), mk(996.0, 0.40, 3.0, "SELL", 0)]
    ev = R.build_event(fill, is_taker=False, state=R.MarketState(),
                       pre=ring.before(1000), post=None, prints=prints,
                       settle_epoch=None, trail=[])
    assert ev["complement_arrival_volume"] == 7.0
    assert ev["complement_arrival_prints"] == 1
    assert ev["queue_consuming_volume"] == 10.0    # both conventions


def test_price_movement_through_fill_uses_both_raw_ladders():
    ring = R.BookRing()
    ring.add(990.0, book(990.0, [(0.40, 10.0)], [(0.42, 10.0)]))
    post = book(1001.0, [(0.41, 10.0)], [(0.44, 10.0)])
    fill = {"timestamp": 1000, "outcomeIndex": 0, "price": "0.40", "size": "5",
            "side": "BUY", "asset": "tok", "conditionId": "0xc", "slug": "m1"}
    ev = R.build_event(fill, is_taker=False, state=R.MarketState(),
                       pre=ring.before(1000), post=post, prints=[],
                       settle_epoch=None, trail=[])
    assert ev["bid_move_through_fill"] == pytest.approx(0.01)
    assert ev["ask_move_through_fill"] == pytest.approx(0.02)
    assert ev["mid_move_through_fill"] == pytest.approx(0.015)


def test_table_marks_below_n_markets():
    rows = Q.reconcile([event(window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])])
    t = Q.table(rows)
    assert "*" in t and "are NOT pooled" in t


# ------------------------------- defects caught in the first durable smoke run
def test_non_binary_outcome_index_does_not_crash_the_recorder():
    """OBSERVED IN PRODUCTION. The venue served a fill with outcomeIndex=999;
    MarketState held a fixed {0:..,1:..} dict and raised KeyError inside the
    recorder thread. The supervisor restarted it, losing the seed and the whole
    book ring -- one odd row from the venue cost minutes of coverage."""
    st = R.MarketState()
    st.apply(999, 3.0, "BUY", 1000)          # must not raise
    snap = st.snapshot(0, 1001)
    assert snap["inv_0_before"] == 0.0 and snap["inv_1_before"] == 0.0
    assert st.inv[999] == 3.0
    st.apply(0, 2.0, "BUY", 1002)
    assert st.snapshot(0, 1003)["residual_before"] == 2.0


def test_the_trail_ends_at_the_pre_book_and_reaches_backwards():
    """THE TRAIL WAS EMPTY BY CONSTRUCTION AND THE SYMPTOM WAS SILENT. It was
    first taken as the snapshots BETWEEN the pre-book and the fill -- but `pre`
    is the NEWEST snapshot before the fill, so that window is always empty.
    Every event came back trail=0 and the cancellation measurement was
    degenerate while looking like a fact about quiet markets."""
    ring = R.BookRing()
    # 930 is 69s before the last look and falls OUTSIDE the 60s lookback.
    for t, d in ((930.0, 500.0), (940.0, 300.0), (970.0, 200.0),
                 (990.0, 120.0), (999.0, 100.0)):
        ring.add(t, book(t, [(0.40, d)]))
    fill = {"timestamp": 1000, "outcomeIndex": 0, "price": "0.40", "size": "5",
            "side": "BUY", "asset": "tok", "conditionId": "0xc", "slug": "m1"}
    pre = ring.before(1000)
    assert pre["_t"] == 999.0
    trail = ring.window(float(pre["_t"]) - R.TRAIL_LOOKBACK_S, float(pre["_t"]))
    ev = R.build_event(fill, is_taker=False, state=R.MarketState(), pre=pre,
                       post=None, prints=[], settle_epoch=None, trail=trail)
    assert ev["queue_trail_snapshots"] == 4          # 940, 970, 990, 999
    assert ev["queue_trail_t0"] == 940.0
    assert ev["queue_trail_t1"] == 999.0
    assert ev["depth_ahead_at_first_look"] == 300.0
    assert ev["depth_ahead_at_last_look"] == 100.0
    assert ev["depth_ahead_disappeared"] == 200.0


def test_disappearance_splits_into_traded_and_cancelled():
    ring = R.BookRing()
    for t, d in ((980.0, 100.0), (999.0, 20.0)):
        ring.add(t, book(t, [(0.40, d)]))
    fill = {"timestamp": 1000, "outcomeIndex": 0, "price": "0.40", "size": "5",
            "side": "BUY", "asset": "tok", "conditionId": "0xc", "slug": "m1"}
    pre = ring.before(1000)
    prints = [mk(985.0, 0.40, 30.0, "SELL", 0)]      # 30 traded, 50 cancelled
    ev = R.build_event(fill, is_taker=False, state=R.MarketState(), pre=pre,
                       post=None, prints=prints, settle_epoch=None,
                       trail=ring.window(float(pre["_t"]) - R.TRAIL_LOOKBACK_S,
                                         float(pre["_t"])))
    assert ev["depth_ahead_disappeared"] == 80.0
    assert ev["queue_consuming_volume"] == 30.0
    assert ev["depth_drop_not_explained_by_trades"] == 50.0


def test_only_the_nearest_snapshots_keep_a_full_raw_ladder():
    ring = R.BookRing()
    for i in range(20):
        t = 950.0 + i * 2.0
        ring.add(t, book(t, [(0.40, 100.0 - i)]))
    pre = ring.before(1000)
    ev = R.build_event(
        {"timestamp": 1000, "outcomeIndex": 0, "price": "0.40", "size": "5",
         "side": "BUY", "asset": "tok", "conditionId": "0xc", "slug": "m1"},
        is_taker=False, state=R.MarketState(), pre=pre, post=None, prints=[],
        settle_epoch=None,
        trail=ring.window(float(pre["_t"]) - R.TRAIL_LOOKBACK_S, float(pre["_t"])))
    assert ev["queue_trail_snapshots"] > R.TRAIL_RAW_SNAPSHOTS
    assert ev["queue_trail_raw_kept"] == R.TRAIL_RAW_SNAPSHOTS
    # scalars are kept for EVERY snapshot, so nothing is unmeasurable
    assert len(ev["queue_trail_scalars"]) == ev["queue_trail_snapshots"]
    assert "raw_books.jsonl" in ev["queue_trail_raw_note"]


def test_backfill_uses_the_reconciliation_window_not_the_flow_window():
    """Two windows, and they are not the same one. Recomputing the
    reconciliation terms over the narrow flow window made a backfilled event
    disagree with the same event at capture time, over the same prints."""
    ev = {"pre_fill_book": {"bids": [(0.40, 100.0)], "asks": [], "t": 999.0},
          "conditionId": "0xc", "timestamp": 1000, "outcome_index": 0,
          "price": 0.40, "depth_ahead_of_fill_price": 100.0,
          "queue_trail_t0": 950.0, "queue_trail_t1": 999.0}
    prints = [mk(960.0, 0.40, 30.0, "SELL", 0),    # recon window only
              mk(999.5, 0.40, 7.0, "SELL", 0),     # both windows
              mk(970.0, 0.60, 4.0, "BUY", 1),      # recon window, mirror leg
              # Without a print at or past the fill the tape-lag guard fires
              # first and nothing is joined -- which is correct behaviour and
              # was the fixture's bug, not the code's.
              mk(1001.0, 0.40, 99.0, "SELL", 0)]   # after the fill: neither
    out = R.backfill_flow([ev], {"0xc": prints})[0]
    assert out["flow_backfill"]["status"] == "JOINED"
    assert out["window_prints_t0"] == 950.0
    assert out["flow_window_t0"] == 999.0
    # 30 + 7 + 4 (the mirror leg counts): all three consume the same bid queue
    assert out["queue_consuming_volume"] == pytest.approx(41.0)
    # the H-FLOW numerator stays on the NARROW window: only the 999.5 print
    assert out["arriving_volume_since_pre_snapshot"] == pytest.approx(7.0)


def test_a_truncated_print_window_refuses_rather_than_reports_unexplained():
    """The reconciliation re-derives consuming volume from `window_prints`. If
    that list was capped at capture, re-deriving undercounts it -- and a busy
    market would be reported as UNEXPLAINED because of a list cap."""
    ev = event(window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)],
               window_prints_truncated=True, window_prints_n_total=5000)
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "NO_COVERAGE"
    assert "truncated" in r["reason"]


def test_an_untruncated_window_is_scored_normally():
    ev = event(window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)],
               window_prints_truncated=False, window_prints_n_total=1)
    assert Q.reconcile_fill(ev)["verdict"] == "UNEXPLAINED"


def test_capture_marks_truncation_when_the_window_overflows():
    ring = R.BookRing()
    ring.add(990.0, book(990.0, [(0.40, 100.0)]))
    # strictly AFTER the pre-snapshot: the window is (t0, ts], so a print at
    # exactly t0 is excluded and would make this an off-by-one about the
    # boundary rather than about truncation.
    prints = [mk(990.0 + i * 0.001, 0.40, 1.0, "SELL", 0)
              for i in range(1, R.WINDOW_PRINTS_CAP + 6)]
    ev = R.build_event(
        {"timestamp": 1000, "outcomeIndex": 0, "price": "0.40", "size": "5",
         "side": "BUY", "asset": "tok", "conditionId": "0xc", "slug": "m1"},
        is_taker=False, state=R.MarketState(), pre=ring.before(1000),
        post=None, prints=prints, settle_epoch=None, trail=[])
    assert ev["window_prints_truncated"] is True
    assert ev["window_prints_n_total"] == R.WINDOW_PRINTS_CAP + 5
    # the AGGREGATE is still computed over every print, not the capped list
    assert ev["queue_consuming_volume"] == float(R.WINDOW_PRINTS_CAP + 5)


def test_a_single_book_look_cannot_produce_unexplained():
    """Without a second look there is no observed span, so this market's rate
    is unmeasurable -- and UNEXPLAINED would be a verdict about our sampling,
    not about the market. Records written before the trail lookback existed
    scored 4 of 4 scorable fills UNEXPLAINED purely this way."""
    ev = event(queue_trail_t0=LAST_LOOK,            # span collapses to zero
               queue_trail_scalars=[{"t": LAST_LOOK, "depth_ahead": 100.0,
                                     "depth_at": 100.0}],
               queue_trail_snapshots=1,
               window_prints_t0=LAST_LOOK,
               window_prints=[mk(999.5, 0.40, 1.0, "SELL", 0)])
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "INCONCLUSIVE_GAP"
    assert r["degenerate_observed_interval"] is True


def test_two_looks_restore_the_ability_to_say_unexplained():
    ev = event(window_prints=[mk(991.0, 0.40, 1.0, "SELL", 0),
                              mk(999.5, 0.40, 1.0, "SELL", 0)])
    r = Q.reconcile_fill(ev)
    assert r["verdict"] == "UNEXPLAINED"
    assert r["market_observed_rate_shares_per_s"] is not None
