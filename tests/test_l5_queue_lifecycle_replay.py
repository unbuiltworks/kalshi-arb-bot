"""Regression tests for lifecycle-bounded book-only queue evidence."""
from src.l5_queue_depletion import lifecycle_aware_verdict


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
