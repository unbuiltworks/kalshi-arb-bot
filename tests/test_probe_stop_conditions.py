"""OWNER stop conditions for the bounded COMPLETE-reprice live probe.

The owner explicitly declined a 21.7% bar -- "a historical diagnostic, not a
magical safety threshold" -- and set these instead. They only ever ADD a
reason to stop; the existing hard loss/exposure gates stay authoritative.
"""
import pytest

from src import pmus_spring_l5_session as ses


def test_the_three_bars_are_the_owners_numbers():
    assert ses.COMPLETE_REJECT_STREAK_MAX == 3
    assert ses.COMPLETE_REJECT_RATE_MIN_N == 10
    assert ses.COMPLETE_REJECT_RATE_MAX == 0.35
    assert ses.INFRA_REJECT_MAX == 2


def test_the_historical_21_7_percent_is_NOT_a_bar():
    """It was a diagnostic. Encoding it would halt a tiny sample on noise."""
    assert ses.COMPLETE_REJECT_RATE_MAX > 0.217


@pytest.mark.parametrize("blob", [
    "rejected:429 too_many_requests", "rejected:RATE_LIMIT exceeded",
    "rejected:401 unauthorized", "rejected:invalid signature",
    "rejected:400 bad_request", "rejected:schema mismatch",
    "rejected:503 service unavailable", "rejected:internal error",
])
def test_infrastructure_classes_are_recognised(blob):
    assert ses.is_infra_rejection(blob) is True


@pytest.mark.parametrize("blob,cls", [
    ("rejected:ORDER_REJECTED_ZERO_FILL", None),
    ("rejected:x", "venue/execution adaptation:post_only_cross_after_book_move"),
    ("rejected:y", "implementation bug candidate:rejected_at_non_crossing_price"),
])
def test_pricing_rejections_are_NOT_infrastructure(blob, cls):
    """A pricing rejection is information about the market, not a fault.
    Treating it as infra would halt the probe on exactly the signal we are
    running the probe to collect."""
    assert ses.is_infra_rejection(blob, cls) is False


def test_venue_halt_and_unreadable_book_count_as_infrastructure():
    assert ses.is_infra_rejection(
        "rejected:z", "venue_market_status=MARKET_STATUS_HALTED") is True
    assert ses.is_infra_rejection(
        "rejected:z", "unknown:bbo_unreadable:TimeoutError") is True


def test_all_three_stop_conditions_are_wired_into_the_session_loop():
    """Constants that nothing reads are decoration. Pin the live wiring."""
    src = open("src/pmus_spring_l5_session.py").read()
    assert 'report["stop_reason"] = (\n                    f"complete_infra_rejections:' in src
    assert 'f"complete_reject_rate:{complete_rejections}"' in src
    assert "complete_submissions >= COMPLETE_REJECT_RATE_MIN_N" in src
    assert "COMPLETE_REJECT_RATE_MAX * complete_submissions" in src
    # and the counters must actually advance on a rejection
    assert "complete_rejections += 1" in src
    assert "infra_rejections += 1" in src
    assert "complete_submissions += 1" in src


def test_rate_bar_needs_the_minimum_sample_first():
    """9 of 9 rejected must NOT halt on the rate rule; a tiny sample is not
    evidence of a rate. The consecutive-3 rule is what catches that case."""
    n, rej = 9, 9
    assert not (n >= ses.COMPLETE_REJECT_RATE_MIN_N
                and rej > ses.COMPLETE_REJECT_RATE_MAX * n)
    n, rej = 10, 4                       # 40% of 10
    assert (n >= ses.COMPLETE_REJECT_RATE_MIN_N
            and rej > ses.COMPLETE_REJECT_RATE_MAX * n)
    n, rej = 10, 3                       # 30% of 10 -> keep going
    assert not (n >= ses.COMPLETE_REJECT_RATE_MIN_N
                and rej > ses.COMPLETE_REJECT_RATE_MAX * n)


# --- the process must say what code it is running -----------------------
def test_every_campaign_row_carries_the_running_commit():
    """Four deployment surprises in one evening, including the probe found
    running a commit written BEFORE two of the three approved stop conditions
    existed -- while GitHub and Railway both reported success. For a
    money-moving service, "deployed" is not evidence; the process has to say.

    Stamped in _log() so EVERY row carries it, not just boot: a rejection row
    can then never be attributed to the wrong build."""
    import json as _json
    import os as _os
    from src import pmus_l5_continuous_session as cs
    src = open("src/pmus_l5_continuous_session.py").read()
    assert '"commit": _COMMIT' in src, "the stamp must be inside _log()"
    assert "RAILWAY_GIT_COMMIT_SHA" in src

    _os.environ["RAILWAY_GIT_COMMIT_SHA"] = "deadbeefcafe1234"
    import importlib
    importlib.reload(cs)
    try:
        assert cs._COMMIT == "deadbeefcafe"          # truncated, not full
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cs._log("probe_boot", open_orders=0)
        row = _json.loads(buf.getvalue().split(" ", 1)[1])
        assert row["commit"] == "deadbeefcafe"
        assert row["kind"] == "probe_boot"
    finally:
        _os.environ.pop("RAILWAY_GIT_COMMIT_SHA", None)
        importlib.reload(cs)


def test_an_unstamped_environment_reports_none_not_a_guess():
    """Absent is UNKNOWN. A fabricated or defaulted SHA would be worse than
    no SHA, because it would look like proof."""
    import os as _os
    import importlib
    from src import pmus_l5_continuous_session as cs
    _os.environ.pop("RAILWAY_GIT_COMMIT_SHA", None)
    _os.environ.pop("GIT_COMMIT_SHA", None)
    importlib.reload(cs)
    assert cs._COMMIT is None
