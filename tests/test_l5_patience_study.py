"""OWNER 5569709706 #2: patience/repricing read off public prints, deterministic."""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_patience_study as ps


def _row(t, outcome, price, qty="5", side="BUY", seq=1):
    return {"timestamp": str(t), "trade_seq": str(seq), "side": side, "outcome": outcome, "price": price,
            "quantity": qty, "dollars": str(D(price) * D(qty)), "seconds_to_settlement": "100",
            "seconds_from_window_start": "50", "window_id": "w", "asset": "BTC", "duration_minutes": "15",
            "settlement_outcome": "Up"}


def test_clusters_gaps_and_reprices_from_one_window():
    rows = [_row(100, "Up", "0.47", seq=1), _row(103, "Up", "0.4700000001", seq=2),   # same level, dust
            _row(140, "Up", "0.47", seq=3),                                            # follow-on after 37 s
            _row(150, "Up", "0.49", seq=4),                                            # reprice +2 c after 10 s
            _row(151, "Down", "0.52", seq=5), _row(200, "Up", "0.10", seq=6, side="SELL")]  # NO side; SELL ignored
    w = ps.study_window(ps.window_prints(rows))
    yes = [c for c in w["clusters"] if c["side"] == "YES"]
    assert [c["prints"] for c in yes] == [3, 1] and yes[0]["duration_s"] == 40
    assert [g["gap_s"] for g in w["gaps_same_level"]] == [3, 37]
    assert w["reprices"] == [{"step_c": 2, "dt_s": 10, "side": "YES"}]
    assert [c["side"] for c in w["clusters"]].count("NO") == 1


def test_summary_counts_follow_ons_after_the_deadline_and_reprices_inside_it():
    rows = [_row(100, "Up", "0.47", seq=1), _row(103, "Up", "0.47", seq=2), _row(140, "Up", "0.47", seq=3),
            _row(150, "Up", "0.49", seq=4)]
    out = ps.l5_patience(rows)
    o = out["overall"]
    assert o["clusters"] == 2 and o["single_print_clusters_share"] == 0.5
    late = o["same_level_follow_on_after_our_deadline"]
    assert late["prints"] == 1 and late["share_of_follow_on_prints"] == 0.5
    assert o["reprices"] == 1 and o["reprices_within_our_deadline_share"] == 1.0
    assert o["reprice_direction"] == {"paid_more": 1, "paid_less": 0}


def test_canonical_history_reads_l5_as_a_fast_repricer_not_a_long_rester():
    out = ps.study()
    o = out["l5"]["overall"]
    assert out["l5"]["windows"] == 2216
    assert o["same_level_follow_on_after_our_deadline"]["share_of_follow_on_prints"] < 0.05
    assert o["time_to_reprice_s"]["p50"] < ps.OUR_DEADLINE_S
    assert o["reprices_within_our_deadline_share"] > 0.8
    assert "NOT rest length" in out["comparison"]["reading"]
    ours = out["ours"]
    # R4 complete (3 markets settled 2026-09-07 18:49Z): 64 orders, 10 fills,
    # 41 deadline cancels -- market 3 added 19 orders / 1 fill / 18 deadlines
    assert ours["orders"] == 64 and ours["filled"] == 10 and ours["deadline_cancels"] == 41
