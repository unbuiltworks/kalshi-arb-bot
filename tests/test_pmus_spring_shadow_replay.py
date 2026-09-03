from src import pmus_spring_shadow_replay as spring


def _snap(t, bid="0.50", *, missing=False):
    return {"t": t, "bid": bid, "ask": "0.51", "bid_qty": "10",
            "ask_book_missing": missing}


def _trade(t, px, direction):
    return {"t": t, "px": px, "dir": direction}


def test_strict_spring_matches_two_true_96c_paths():
    # Each 300s slot starts at 50c. A sell at/below 50 fills YES maker;
    # later YES-buy flow reaches 54c, consistent with a 46c NO maker fill.
    s = {
        "bbo_history": [_snap(100.0), _snap(400.0)],
        "trades": [
            _trade(100.001, "0.50", "sell"),
            _trade(120.0, "0.54", "buy"),
            _trade(400.001, "0.50", "sell"),
            _trade(430.0, "0.54", "buy"),
        ],
    }
    out = spring.summarize_state(s, now=450.0)
    assert out["spring_matched_slots"] == 2
    assert out["spring_repeated_completion"] is True
    assert all(m["pair_cost"] == "0.96"
               for m in out["spring_matches_theoretical"])


def test_ordinary_99c_q1_path_does_not_count_as_spring_match():
    # A 50c first leg followed by YES buying at only 51c is enough for the
    # ordinary 99c complement path, but not the strict spring 54c threshold.
    row = spring.replay_slot(
        _snap(100.0),
        [{"t": 100.001, "px": spring.D("0.50"), "dir_inferred": "sell"},
         {"t": 120.0, "px": spring.D("0.51"), "dir_inferred": "buy"}],
        100.0)
    assert row["status"] == "SPRING_ONE_SIDED_HOLD"
    assert row["yes_threshold_for_comp"] == "0.54"


def test_out_of_band_historical_match_cannot_contaminate_current_spring_state():
    # Earlier 76c activity cannot become a spring match merely because the
    # latest/current book later moves to 50c. Slot-entry state is authoritative.
    s = {
        "bbo_history": [_snap(100.0, "0.76"), _snap(400.0, "0.50")],
        "trades": [
            _trade(100.001, "0.76", "sell"),
            _trade(120.0, "0.80", "buy"),
            _trade(400.001, "0.50", "sell"),
            _trade(420.0, "0.51", "buy"),
        ],
    }
    out = spring.summarize_state(s, now=450.0)
    assert out["spring_matched_slots"] == 0
    assert out["spring_repeated_completion"] is False
    assert out["spring_slot_counts_theoretical"]["SPRING_OUTSIDE_BAND"] >= 1


def test_prevailing_snapshot_not_current_bbo_controls_slot():
    row = spring.replay_slot(
        _snap(100.0, "0.50"),
        [{"t": 100.001, "px": spring.D("0.50"), "dir_inferred": "sell"},
         {"t": 130.0, "px": spring.D("0.54"), "dir_inferred": "buy"}],
        100.0)
    assert row["status"] == "SPRING_MATCHED"
    assert row["join"] == "0.50"
    assert row["pair_cost"] == "0.96"
