from src.mlb_decided_episode_clock_audit import audit_row, audit_rows


def _open_row(*, observed_ms=1_100, poly_ms=1_000, kalshi_ms=1_050, opened_ms=1_100):
    return {
        "poly_book_received_ms": poly_ms,
        "kalshi_book_received_ms": kalshi_ms,
        "pair_episode": {
            "event": "OPEN",
            "opened_ms": opened_ms,
            "observed_ms": observed_ms,
        },
    }


def test_open_event_requires_observation_after_both_book_receives():
    ok, reason = audit_row(_open_row())
    assert ok is True
    assert reason == "VALID_BOOK_EVIDENCE_CLOCK"


def test_cycle_start_timestamp_before_book_receives_fails_closed():
    ok, reason = audit_row(_open_row(observed_ms=900, opened_ms=900))
    assert ok is False
    assert reason == "EPISODE_CLOCK_PRECEDES_BOOK_EVIDENCE"


def test_open_timestamp_cannot_follow_observed_timestamp():
    ok, reason = audit_row(_open_row(opened_ms=1_200))
    assert ok is False
    assert reason == "INVALID_EPISODE_OPEN_TIMESTAMP"


def test_close_clock_is_interval_ordered():
    row = {
        "pair_episode": {
            "event": "CLOSE",
            "opened_ms": 1_000,
            "last_seen_ms": 1_200,
            "closed_observed_ms": 1_400,
        }
    }
    assert audit_row(row) == (True, "VALID_INTERVAL_CENSORED_CLOSE_CLOCK")
    row["pair_episode"]["closed_observed_ms"] = 1_100
    assert audit_row(row) == (False, "INVALID_CLOSE_CLOCK_ORDER")


def test_summary_quarantines_clock_inversion_from_capacity():
    good = _open_row()
    bad = _open_row(observed_ms=900, opened_ms=900)
    report = audit_rows([good, bad])
    assert report["rows"] == 2
    assert report["valid_rows"] == 1
    assert report["quarantined_rows"] == 1
    assert report["capacity_eligible_rows"] == 1
    assert report["execution_eligible"] is False
    assert report["order_entry"] is False
