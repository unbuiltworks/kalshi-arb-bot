"""A blocked preflight must say WHY, and must not report a broken read as 93
settlement facts.

LIVE 2026-09-11T22:45:07Z. The boot gate passed, the advance gate fired, and
continuation_preflight blocked on `venue_settled` with all 93 markets at
status AUDIT_UNAVAILABLE and every other field null -- settled_by,
realized_usd, venue_qty_total, ledger_qty_total. settle_held() puts the
exception in `error`; the preflight's per-market dict did not copy it, so the
row said "not settled" 93 times and could not say that it had never managed to
ask. That cost a deployment to discover.
"""
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont


@pytest.fixture
def _stub(monkeypatch):
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: {
        "seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    monkeypatch.setattr(cont, "_preflight_sleep", lambda s: None)


def _unavailable(t, slug, walk=None):
    return {"status": "AUDIT_UNAVAILABLE",
            "error": "HTTPError:403 Forbidden"}


def _settled(t, slug, walk=None):
    return {"status": "REALIZED", "settled": True,
            "settled_by": "POSITION_RESOLUTION", "realized_confirmed": True,
            "realized_usd": "0.10", "venue_qty_total": "1",
            "ledger_qty_total": "1"}


def test_the_error_reaches_the_preflight_row(_stub, monkeypatch):
    """Without this the blocker is undiagnosable from the log."""
    monkeypatch.setattr(cont, "settle_held", _unavailable)
    pre = cont.continuation_preflight(None, {"a", "b"}, {"a", "b"})
    vs = pre["checks"]["venue_settled"]
    assert vs["ok"] is False
    for slug in ("a", "b"):
        assert vs["markets"][slug]["error"] == "HTTPError:403 Forbidden"


def test_a_uniform_failure_is_named_as_one_fact_not_many(_stub, monkeypatch):
    monkeypatch.setattr(cont, "settle_held", _unavailable)
    pre = cont.continuation_preflight(None, {"a", "b", "c"}, set())
    vs = pre["checks"]["venue_settled"]
    assert vs["uniform_failure"] is True
    assert vs["audit_unavailable"] == 3
    assert vs["audit_error_kinds"] == ["HTTPError:403 Forbidden"]
    assert "venue/transport fault" in vs["diagnosis"]
    assert "not a settlement fact" in vs["diagnosis"]


def test_a_genuine_unsettled_market_is_not_called_a_transport_fault(
        _stub, monkeypatch):
    """The diagnosis must not fire when the venue answered and the answer was
    simply 'not settled' -- that IS a settlement fact and must still block."""
    def mixed(t, slug, walk=None):
        return _settled(t, slug) if slug == "a" else {
            "status": "PENDING", "settled": False}
    monkeypatch.setattr(cont, "settle_held", mixed)
    pre = cont.continuation_preflight(None, {"a", "b"}, set())
    vs = pre["checks"]["venue_settled"]
    assert vs["ok"] is False, "still blocks -- b is not settled"
    assert vs["uniform_failure"] is False
    assert vs["diagnosis"] is None
    assert vs["audit_unavailable"] == 0


def test_a_clean_audit_still_passes_and_reports_no_errors(_stub, monkeypatch):
    monkeypatch.setattr(cont, "settle_held", _settled)
    pre = cont.continuation_preflight(None, {"a"}, {"a"})
    vs = pre["checks"]["venue_settled"]
    assert vs["ok"] is True
    assert vs["audit_unavailable"] == 0 and vs["audit_error_kinds"] == []
    assert vs["uniform_failure"] is False
    assert vs["markets"]["a"]["error"] is None


def test_the_check_still_blocks_on_an_unavailable_audit(_stub, monkeypatch):
    """Diagnosis is not permission. "We could not ask" must never pass."""
    monkeypatch.setattr(cont, "settle_held", _unavailable)
    pre = cont.continuation_preflight(None, {"a"}, {"a"})
    assert pre["ok"] is False
    assert "venue_settled" in pre["failed"]


# ---------------------------------------------- the 429 that actually blocked
def test_the_audits_are_paced_like_the_read_only_sweep(_stub, monkeypatch):
    """LIVE 23:11:26Z: 93 unpaced authenticated walks -> HTTP 429 on every
    one. The sweep has paced at 1.5s all along; this path had no pacing."""
    from src import pmus_settlement_sweep as sweep
    assert cont.PREFLIGHT_AUDIT_PACE_S == sweep.PACE_S
    naps: list = []
    monkeypatch.setattr(cont, "_preflight_sleep", naps.append)
    monkeypatch.setattr(cont, "settle_held", _settled)
    cont.continuation_preflight(None, {"a", "b", "c"}, set())
    assert naps == [cont.PREFLIGHT_AUDIT_PACE_S] * 2, "n-1 pauses for n markets"


def test_every_pass_re_audits_every_market(_stub, monkeypatch):
    """No caching. A memo was tried and removed: it answered a later pass
    with an earlier pass's ledger quantity, which would hide a ledger/venue
    divergence that appeared after the first confirmation. The retry loop
    can afford to re-read; the reconcile check cannot afford a stale one."""
    calls: list = []
    monkeypatch.setattr(cont, "settle_held",
                        lambda t, s, walk=None: (calls.append(s), _settled(t, s))[1])
    cont.continuation_preflight(None, {"a", "b"}, set())
    cont.continuation_preflight(None, {"a", "b"}, set())
    assert sorted(calls) == ["a", "a", "b", "b"]


def test_a_changed_ledger_quantity_is_seen_on_the_next_pass(_stub, monkeypatch):
    """The exact case the memo broke."""
    qty = {"v": "0.04"}
    monkeypatch.setattr(cont, "settle_held", lambda t, s, walk=None: {
        "status": "REALIZED", "settled": True, "settled_by": "POSITION_RESOLUTION",
        "realized_confirmed": True, "realized_usd": "0.01",
        "venue_qty_total": "0.04", "ledger_qty_total": qty["v"]})
    first = cont.continuation_preflight(None, {"m"}, {"m"})
    assert first["checks"]["venue_settled"]["ledger_vs_venue_qty_mismatch"] == []
    qty["v"] = "0.06"
    second = cont.continuation_preflight(None, {"m"}, {"m"})
    assert second["checks"]["venue_settled"]["ledger_vs_venue_qty_mismatch"] == ["m"]


# -------------------------------------------- one account feed, not 93 of them
def test_the_preflight_fetches_one_activity_walk_and_shares_it(
        _stub, monkeypatch):
    """/v1/portfolio/activities is ACCOUNT-scoped. Auditing 93 markets was
    re-reading one feed 93 times -- up to ~2,300 requests -- which the venue
    answered with 429. Backoff could not win that: at 2026-09-11T23:55:31Z the
    preflight ground for 17+ minutes without finishing one pass."""
    from src import pmus_settlement_auditor as auditor
    walks: list = []
    monkeypatch.setattr(auditor, "first_fill_time",
                        lambda s: {"a": "2026-09-08T00:00:00Z",
                                   "b": "2026-09-06T00:00:00Z"}.get(s))
    monkeypatch.setattr(auditor, "activity_pages",
                        lambda t, since=None, **kw: (walks.append(since),
                                                     {"rows": [], "pages": 1})[1])
    seen: list = []
    monkeypatch.setattr(cont, "settle_held",
                        lambda t, s, walk=None: (seen.append(walk), _settled(t, s))[1])
    pre = cont.continuation_preflight(object(), {"a", "b"}, set())
    assert len(walks) == 1, "one walk for the whole set, not one per market"
    # bounded by the EARLIEST first fill, so it covers every market's own since
    assert walks[0] == "2026-09-06T00:00:00Z"
    assert all(w == {"rows": [], "pages": 1} for w in seen)
    assert pre["checks"]["venue_settled"]["activity_walk"]["shared_walk"] is True


def test_a_failed_shared_walk_falls_back_instead_of_blocking(
        _stub, monkeypatch):
    """If the one walk fails, each market fetches its own exactly as before --
    slower, but never a new way to fail closed on a transport problem."""
    from src import pmus_settlement_auditor as auditor
    monkeypatch.setattr(auditor, "first_fill_time", lambda s: None)
    def _boom(t, since=None, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(auditor, "activity_pages", _boom)
    seen: list = []
    monkeypatch.setattr(cont, "settle_held",
                        lambda t, s, walk=None: (seen.append(walk), _settled(t, s))[1])
    pre = cont.continuation_preflight(object(), {"a"}, {"a"})
    assert seen == [None], "None means 'fetch your own', the old behaviour"
    note = pre["checks"]["venue_settled"]["activity_walk"]
    assert note["shared_walk"] is False and "boom" in note["error"]
    assert pre["ok"] is True, "the fallback must not itself block"


# --------------------------------------------- the walk must be deep enough
def test_the_shared_walk_is_asked_for_the_deep_page_cap(_stub, monkeypatch):
    """LIVE 00:39:48Z: the shared walk inherited ACTIVITY_MAX_PAGES = 25 and
    stopped at 500 rows / 2026-09-08T03:11Z, short of the 2026-09-07T19:25Z
    it was asked for. 25 was a cap on 93 SEPARATE walks; there is one now."""
    from src import pmus_settlement_auditor as auditor
    assert auditor.SHARED_ACTIVITY_MAX_PAGES > auditor.ACTIVITY_MAX_PAGES
    caps: list = []
    monkeypatch.setattr(auditor, "first_fill_time", lambda s: "2026-09-06T00:00:00Z")
    monkeypatch.setattr(auditor, "activity_pages",
                        lambda t, since=None, max_pages=None, **kw:
                        (caps.append(max_pages), {"rows": [], "pages": 1})[1])
    monkeypatch.setattr(cont, "settle_held", _settled)
    cont.continuation_preflight(object(), {"a"}, set())
    assert caps == [auditor.SHARED_ACTIVITY_MAX_PAGES]


def test_a_market_the_walk_never_reached_is_named_not_counted_as_agreeing(
        _stub, monkeypatch):
    """venue_qty None used to drop the market silently out of the mismatch
    filter. Unknown is the honest answer, and it has to be visible."""
    monkeypatch.setattr(cont, "settle_held", lambda t, s, walk=None: {
        "status": "REALIZED", "settled": True, "settled_by": "POSITION_RESOLUTION",
        "realized_confirmed": True, "realized_usd": "-2.12",
        "venue_qty_total": None,
        "venue_qty_unknown_reason": "activity walk ended 'max_pages' at ...",
        "ledger_qty_total": "16.20"})
    pre = cont.continuation_preflight(None, {"m"}, {"m"})
    vs = pre["checks"]["venue_settled"]
    assert vs["venue_qty_unknown"] == ["m"]
    assert vs["ledger_vs_venue_qty_mismatch"] == [], (
        "a quantity we never read is not a divergence")
