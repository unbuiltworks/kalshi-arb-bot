"""P0-A: the evidence-age reference instant, and the gate it feeds.

LIVE DEFECT, 2026-09-13T19:16:39Z. Three MLB moneylines --
aec-mlb-cws-stl, aec-mlb-cle-min, aec-mlb-pit-chc -- were refused with
`strict_evidence_from_future:-7.617`. The evidence age was NEGATIVE.

That was NOT host clock skew and NOT a market problem. `select_candidate` took
`now = time.time()` BEFORE calling `load_status_document()`, the recorder stamps
`status["t"]` when it BUILDS the document, and fetching a ~180 KB status takes
real time -- so the comparison was `time_before_request - time_during_request`,
negative by roughly the request latency.

The gate is NOT loosened here. No abs(), no clamping of negative age, no
widened window. Only the reference instant is corrected: freshness is measured
from the moment the evidence was actually in hand.
"""
from __future__ import annotations

import time

import pytest

from src import pmus_spring_l5_probe_entry as entry


def _ev(ts, slug="aec-mlb-cws-stl-2026-09-13", **kw):
    base = {
        "slug": slug, "probe_evidence": {}, "both_sides_served": True,
        "bid_touch_qty": "5", "bid": "0.50", "trades_1h": 50,
        "two_sided_trades": True, "off_distribution": False,
        "spring_pair_target": "0.98", "evidence_provenance": "authenticated",
        "probe_entry_action": {"decision": "quote", "relation": "open"},
        "market_state": {"active": True, "archived": False, "closed": False,
                         "status": "MARKET_STATUS_OPEN",
                         "endDate": "2026-09-30T00:00:00Z"},
        "evidence_t": ts,
    }
    base.update(kw)
    return base


# --- the gate itself: all four required behaviours -------------------------

def test_genuinely_stale_evidence_refuses():
    now = 1_000_000.0
    r = entry.strict_evidence_reason("aec-mlb-cws-stl-2026-09-13",
                                     _ev(now - 3600.0), now=now, max_age_s=60.0)
    assert r and r.startswith("strict_evidence_stale"), r


def test_genuinely_future_evidence_still_refuses():
    """The fix must not make the from-future branch unreachable. A timestamp
    that is really ahead of a correctly-taken `now` is still a refusal."""
    now = 1_000_000.0
    r = entry.strict_evidence_reason("aec-mlb-cws-stl-2026-09-13",
                                     _ev(now + 600.0), now=now, max_age_s=60.0)
    assert r == "strict_evidence_from_future:-600.0", r


def test_synchronized_fresh_evidence_passes():
    now = 1_000_000.0
    assert entry.strict_evidence_reason("aec-mlb-cws-stl-2026-09-13",
                                        _ev(now - 1.0), now=now,
                                        max_age_s=60.0) is None


def test_malformed_timestamp_refuses():
    now = 1_000_000.0
    for bad in ("not-a-time", "", None, {}, []):
        r = entry.strict_evidence_reason("aec-mlb-cws-stl-2026-09-13",
                                         _ev(bad), now=now, max_age_s=60.0)
        assert r is not None, f"malformed timestamp {bad!r} must refuse"


def test_the_live_failure_reproduces_under_the_old_ordering():
    """Pins the exact observed number: a `now` taken 7.617 s before the
    document's own stamp yields precisely the refusal we saw."""
    stamp = 1_000_000.0
    stale_now = stamp - 7.617
    r = entry.strict_evidence_reason("aec-mlb-cws-stl-2026-09-13",
                                     _ev(stamp), now=stale_now, max_age_s=60.0)
    assert r == "strict_evidence_from_future:-7.617", r
    # and with `now` taken AFTER the document arrives, the same evidence passes
    assert entry.strict_evidence_reason("aec-mlb-cws-stl-2026-09-13",
                                        _ev(stamp), now=stamp + 0.4,
                                        max_age_s=60.0) is None


def test_utc_and_z_suffix_round_trip_identically():
    """Timezone handling was on the suspect list; it is not the defect."""
    t = 1_789_000_000.0
    from datetime import datetime, timezone
    iso_z = datetime.fromtimestamp(t, timezone.utc).isoformat().replace(
        "+00:00", "Z")
    iso_off = datetime.fromtimestamp(t, timezone.utc).isoformat()
    naive = datetime.fromtimestamp(t, timezone.utc).replace(
        tzinfo=None).isoformat()
    for v in (t, str(t), iso_z, iso_off, naive):
        assert abs(entry._epoch(v) - t) < 1e-3, v


# --- the caller: the reference instant is taken after the fetch ------------

@pytest.mark.xfail(strict=True, reason=(
    "DEFERRED FROM THE OBSERVATION-ONLY CUTOVER, not abandoned. The re-stamp "
    "landed at 392b5c0e, but production (f7a3e224) never received it: the "
    "probe's Railway watch set is a sentinel, so 21 research commits sat on "
    "the branch unserved. The 2026-09-13 instrumentation cutover was "
    "authorised as OBSERVATION ONLY, and this fix changes CANDIDATE "
    "ADMISSION -- a market refused today as strict_evidence_from_future "
    "would be admitted -- so it was excluded from that deploy and the "
    "module was reverted to the serving content. The test BODY is unchanged "
    "and is the acceptance criterion for re-landing.\n\n"
    "TO RE-LAND (its own deploy decision, at its own boundary):\n"
    "    git show 392b5c0e -- src/pmus_l5_continuous_session.py | git apply\n"
    "then DELETE this marker. strict=True means the suite goes RED the "
    "moment the fix returns, so nobody can re-land it and quietly leave a "
    "stale xfail behind."))
def test_select_candidate_restamps_now_after_the_status_fetch(monkeypatch):
    """The regression that would have caught the live defect: a slow fetch must
    not produce a negative evidence age."""
    from src import pmus_l5_continuous_session as sess

    fetch_latency = 8.0
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(sess.time, "time", lambda: clock["t"])

    def slow_load(path=None):
        clock["t"] += fetch_latency          # the fetch takes real time
        return {"t": clock["t"], "spring_watch": []}, "test"

    monkeypatch.setattr(sess.entry, "load_status_document", slow_load)
    monkeypatch.setattr(sess, "pmus_shadow_yield", lambda now: {})

    seen = {}
    monkeypatch.setattr(sess, "_log",
                        lambda kind, **kw: seen.update(kw) if kind == "selection"
                        else None)
    sess.select_candidate()
    assert seen.get("evidence_age_s") is not None
    assert seen["evidence_age_s"] >= 0.0, (
        f"evidence age {seen['evidence_age_s']} is negative -- `now` was taken "
        "before the fetch that produced the timestamp")


def test_a_caller_supplied_now_is_honoured_verbatim(monkeypatch):
    """Tests and replays must stay deterministic: an explicit `now` is never
    silently replaced by wall-clock."""
    from src import pmus_l5_continuous_session as sess
    monkeypatch.setattr(sess.entry, "load_status_document",
                        lambda path=None: ({"t": 500.0, "spring_watch": []}, "t"))
    monkeypatch.setattr(sess, "pmus_shadow_yield", lambda now: {})
    seen = {}
    monkeypatch.setattr(sess, "_log",
                        lambda kind, **kw: seen.update(kw) if kind == "selection"
                        else None)
    sess.select_candidate(now=560.0)
    assert seen["evidence_age_s"] == 60.0, seen
