"""Regression tests for lifecycle-bounded book-only queue evidence."""
from src.l5_queue_depletion import lifecycle_aware_verdict, touch_pair


def frame(bid, ask):
    return {"bids": [[str(bid), "10"]], "asks": [[str(ask), "10"]]}


FRAMES = [frame(".45", ".55"), frame(".45", ".54"), frame(".45", ".44")]


def test_crossing_requires_attested_resting_interval():
    assert lifecycle_aware_verdict("UP", ".45", FRAMES, start=0)["reason"] == "LIFECYCLE_UNATTESTED"
    r = lifecycle_aware_verdict("UP", ".45", FRAMES, start=0,
                                lifecycle=[{"frame": 0, "state": "ACK"},
                                           {"frame": 0, "state": "RESTING"}])
    assert r["verdict"] == "PROVEN"
    assert r["venue_fill_confirmed"] is False
    assert r["book_only"] is True


def test_cancel_before_crossing_never_proves_fill():
    r = lifecycle_aware_verdict("UP", ".45", FRAMES, start=0,
                                lifecycle=[{"frame": 0, "state": "RESTING"},
                                           {"frame": 2, "state": "CANCELED"}])
    assert r["verdict"] != "PROVEN"
    assert r["eligible_end_exclusive"] == 2


def test_ack_without_resting_is_not_eligibility():
    r = lifecycle_aware_verdict("UP", ".45", FRAMES, start=0,
                                lifecycle=[{"frame": 0, "state": "ACK"}])
    assert r["verdict"] is None
    assert r["reason"] == "RESTING_NOT_AT_JOIN"


def test_partial_fill_does_not_invent_full_fill():
    r = lifecycle_aware_verdict("UP", ".45", FRAMES, start=0,
                                lifecycle=[{"frame": 0, "state": "RESTING"},
                                           {"frame": 1, "state": "PARTIAL_FILL"},
                                           {"frame": 2, "state": "CANCELED"}])
    assert r["venue_fill_confirmed"] is False
    assert r["verdict"] != "PROVEN"


def test_restart_unordered_and_contradictory_fail_closed():
    for lifecycle, reason in [
        ([{"frame": 1, "state": "RESTING"}], "RESTING_NOT_AT_JOIN"),
        ([{"frame": 0, "state": "RESTING"}, {"frame": 1, "state": "ACK"},
          {"frame": 0, "state": "CANCELED"}], "LIFECYCLE_UNORDERED"),
        ([{"frame": 0, "state": "RESTING"}, {"frame": 1, "state": "CANCELED"},
          {"frame": 2, "state": "RESTING"}], "LIFECYCLE_CONTRADICTORY"),
    ]:
        assert lifecycle_aware_verdict("UP", ".45", FRAMES, start=0,
                                       lifecycle=lifecycle)["reason"] == reason


def test_terminal_event_cannot_be_followed_by_ack_or_partial_fill():
    for terminal in ("CANCELED", "EXPIRED", "REJECTED", "FILLED"):
        for late in ("ACK", "PARTIAL_FILL"):
            result = lifecycle_aware_verdict(
                "UP", ".45", FRAMES, start=0,
                lifecycle=[{"frame": 0, "state": "RESTING"},
                           {"frame": 1, "state": terminal},
                           {"frame": 2, "state": late}],
            )
            assert result["verdict"] is None
            assert result["reason"] == "LIFECYCLE_CONTRADICTORY"


def test_zero_size_opposite_touch_does_not_prove_fill():
    # A deleted/zero-size level must not be treated as an executable cross.
    for side, empty_cross in (
        ("UP", {"bids": [[".45", "10"]], "asks": [[".44", "0"], [".55", "10"]]}),
        ("DOWN", {"bids": [[".56", "0"], [".45", "10"]], "asks": [[".55", "10"]]}),
    ):
        frames = [frame(".45", ".55"), empty_cross]
        result = lifecycle_aware_verdict(
            side, ".45", frames, start=0,
            lifecycle=[{"frame": 0, "state": "RESTING"}],
        )
        assert result["verdict"] != "PROVEN"
        assert result["venue_fill_confirmed"] is False


def test_touch_pair_ignores_zero_size_tombstones():
    from decimal import Decimal
    # A deleted bid at .54 and deleted ask at .46 must not fabricate
    # a crossed or high-edge executable touch.
    assert touch_pair([[".54", "0"], [".45", "10"]],
                      [[".46", "0"], [".55", "10"]]) == Decimal(".90")
    assert touch_pair([[".45", "0"]], [[".55", "10"]]) is None
    assert touch_pair([[".45", "10"]], [[".55", "0"]]) is None


def test_tombstone_only_own_side_cannot_attest_zero_queue():
    from src.l5_queue_depletion import queue_ahead
    # A deleted book side is missing evidence, not an empty FIFO queue.
    assert queue_ahead("UP", ".45", bids=[[".45", "0"]]) is None
    assert queue_ahead("DOWN", ".45", asks=[[".55", "0"]]) is None
    for side, empty in (
        ("UP", {"bids": [[".45", "0"]], "asks": [[".55", "10"]]}),
        ("DOWN", {"bids": [[".45", "10"]], "asks": [[".55", "0"]]}),
    ):
        result = lifecycle_aware_verdict(
            side, ".45", [empty, frame(".45", ".55")],
            lifecycle=[{"frame": 0, "state": "RESTING"}],
        )
        assert result["verdict"] is None
        assert result["reason"] == "QUEUE_UNREADABLE"
    # Real displayed liquidity only at worse prices legitimately means
    # no queue ahead; it must not be mistaken for a missing book.
    assert queue_ahead("UP", ".45", bids=[[".44", "10"], [".45", "0"]]) == 0
    assert queue_ahead("DOWN", ".45", asks=[[".56", "10"], [".55", "0"]]) == 0
