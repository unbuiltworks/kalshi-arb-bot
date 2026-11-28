"""Why the full preflight said PASS and the heartbeat said FAIL.

THE ANSWER IS THAT THEY WERE ANSWERING DIFFERENT QUESTIONS, and the
authorization logic is correct in both. `authorization_preflight`
deliberately branches on whether a market was supplied
(`src/l5_live_authorization.py`, the `if market:` at the campaign row):

  WITH a market    -> the full `campaign_decision(market)`, which includes
                      the `window_starts` allowlist. A campaign authorizing
                      only the NEXT window correctly FAILS for this one,
                      with WINDOW_NOT_IN_AUTHORIZED_SET.

  WITHOUT one      -> the campaign DOCUMENT alone, through the canonical
                      gate, reporting coverage as NOT_EVALUATED. That
                      branch exists because `campaign_decision("")` used
                      to reach the series test and emit a false
                      CAMPAIGN_SERIES_MISMATCH against a perfectly valid
                      approval -- a real production misdiagnosis on
                      2026-10-23T19:37.

The full `L5_AUTHORIZATION_PREFLIGHT` line is emitted ONCE AT STARTUP,
when the transport has no slug: document-only, PASS. The compact verdict
in the heartbeat is emitted CONTINUOUSLY with the current window's slug:
market-aware, FAIL while the authorized window has not arrived.

So there was no defect in authorization. The defect was that
`fail: ["campaign_approval"]` carried no market and no reason, making a
valid-campaign-wrong-window indistinguishable from a broken approval.
These tests pin the distinction.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
from collections import Counter

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH
from src import l5_pmus_live_runner as RUN

SERIES = "cpc-btc-updown-15m-"


def iso(d):
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def two_windows(now):
    """The authorized NEXT window, and the CURRENT one before it."""
    start = now.replace(minute=(now.minute // 15) * 15, second=0,
                        microsecond=0)
    while (start - now).total_seconds() < 600:
        start += dt.timedelta(minutes=15)
    prev = start - dt.timedelta(minutes=15)

    def slug(t):
        return "cpc-btc-updown-15m-%s-%02d%02dz" % (
            t.strftime("%Y-%m-%d"), t.hour, t.minute)
    return start, slug(start), prev, slug(prev)


def approve(monkeypatch, start, now):
    doc = {"approved": True,
           "nonce": "probe-%s" % start.strftime("%Y%m%d-%H%Mz"),
           "series": SERIES, "issued_utc": iso(now),
           "expires_utc": iso(start + dt.timedelta(minutes=30)),
           "max_windows": 1, "max_total_exposure_usd": "1.00",
           "max_daily_loss_usd": "5.00",
           "window_starts": [iso(start)]}
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    root = tempfile.mkdtemp()
    monkeypatch.setenv(CA.ROOT_ENV, root)
    return doc, root


class Stub:
    """Only what the verdict method touches."""
    authorization_preflight_verdict = \
        RUN.Runner.authorization_preflight_verdict

    def __init__(self, slug, root):
        self.transport = type("T", (), {"market_slug": slug})()
        self.root = root
        self.stats = Counter()


@pytest.fixture
def clock():
    return dt.datetime.now(dt.timezone.utc)


# ============================ the two answers, and why they differ
def test_the_document_alone_passes_with_no_market(clock, monkeypatch):
    """The STARTUP line. No slug, so coverage is NOT_EVALUATED rather than
    a failure -- which is why it reported PASS."""
    start, _, _, _ = two_windows(clock)
    _, root = approve(monkeypatch, start, clock)
    pf = AUTH.authorization_preflight("", root=root)
    camp = next(r for r in pf["rows"]
                if r["check"] == "campaign_approval")
    assert camp["verdict"] == "PASS", camp
    assert "campaign_approval" not in (pf.get("fail") or [])


def test_the_previous_window_fails_the_same_valid_campaign(clock,
                                                           monkeypatch):
    """The HEARTBEAT verdict. Same document, market-aware, and the current
    window is not the authorized one."""
    start, _, _, prev_slug = two_windows(clock)
    _, root = approve(monkeypatch, start, clock)
    pf = AUTH.authorization_preflight(prev_slug, root=root)
    camp = next(r for r in pf["rows"]
                if r["check"] == "campaign_approval")
    assert camp["verdict"] == "FAIL", camp
    assert camp["reason"] == "WINDOW_NOT_IN_AUTHORIZED_SET", camp
    assert "campaign_approval" in (pf.get("fail") or [])


def test_the_authorized_window_passes(clock, monkeypatch):
    """And the window the owner actually approved does pass the campaign
    row -- so nothing is wrong with the approval."""
    start, slug, _, _ = two_windows(clock)
    _, root = approve(monkeypatch, start, clock)
    pf = AUTH.authorization_preflight(slug, root=root)
    camp = next(r for r in pf["rows"]
                if r["check"] == "campaign_approval")
    assert camp["verdict"] == "PASS", camp


# ======================= the verdict now carries its own context
def test_the_verdict_names_the_market_it_evaluated(clock, monkeypatch):
    start, _, _, prev_slug = two_windows(clock)
    _, root = approve(monkeypatch, start, clock)
    v = Stub(prev_slug, root).authorization_preflight_verdict()
    assert v["evaluated_market"] == prev_slug


def test_a_valid_campaign_in_the_wrong_window_is_named_as_such(clock,
                                                               monkeypatch):
    """THE FIX. This is the case that read as a broken approval."""
    start, _, _, prev_slug = two_windows(clock)
    _, root = approve(monkeypatch, start, clock)
    v = Stub(prev_slug, root).authorization_preflight_verdict()
    assert v["ok"] is False
    assert "campaign_approval" in v["fail"]
    assert v["awaiting_authorized_window"] is True
    assert v["fail_reasons"]["campaign_approval"] == \
        "WINDOW_NOT_IN_AUTHORIZED_SET"


def test_the_authorized_window_is_not_flagged_as_awaiting(clock,
                                                          monkeypatch):
    start, slug, _, _ = two_windows(clock)
    _, root = approve(monkeypatch, start, clock)
    v = Stub(slug, root).authorization_preflight_verdict()
    assert v["awaiting_authorized_window"] is False
    assert "campaign_approval" not in (v["fail"] or [])


def test_a_genuinely_absent_campaign_is_NOT_flagged_as_awaiting(clock,
                                                                monkeypatch):
    """The distinction has to cut both ways, or it is just a nicer label
    for the same ambiguity."""
    start, slug, _, _ = two_windows(clock)
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    root = tempfile.mkdtemp()
    monkeypatch.setenv(CA.ROOT_ENV, root)
    v = Stub(slug, root).authorization_preflight_verdict()
    assert v["awaiting_authorized_window"] is False
    assert v["ok"] is not True


def test_an_expired_campaign_is_NOT_flagged_as_awaiting(clock, monkeypatch):
    start, slug, _, _ = two_windows(clock)
    doc = {"approved": True, "nonce": "probe-expired-01", "series": SERIES,
           "issued_utc": iso(clock - dt.timedelta(hours=2)),
           "expires_utc": iso(clock - dt.timedelta(minutes=1)),
           "max_windows": 1, "max_total_exposure_usd": "1.00",
           "max_daily_loss_usd": "5.00", "window_starts": [iso(start)]}
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    root = tempfile.mkdtemp()
    monkeypatch.setenv(CA.ROOT_ENV, root)
    v = Stub(slug, root).authorization_preflight_verdict()
    assert v["awaiting_authorized_window"] is False
    assert v["ok"] is not True


def test_an_unreadable_verdict_is_never_ok(clock):
    """Unchanged, and re-pinned because the fix touches this method."""
    class Broken(Stub):
        def __init__(self):
            self.transport = None
            self.root = "/nonexistent"
            self.stats = Counter()
    v = Broken().authorization_preflight_verdict()
    assert v["ok"] is not True


# ============================ the fix changes no control whatsoever
def test_the_verdict_is_reporting_only():
    import ast
    import inspect
    import textwrap
    src = inspect.getsource(RUN.Runner.authorization_preflight_verdict)
    # CHECK FOR CALLS, NOT WORDS. This method's own prose says
    # "`authorize_arming` still decides" -- saying it does NOT arm -- and a
    # bare substring ban reads that as evidence that it does. The fifth
    # time a crude word-match has caught me on correct code, so: parse the
    # body and look at what it actually CALLS.
    tree = ast.parse(textwrap.dedent(src)).body[0]
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(getattr(f, "attr", None) or getattr(f, "id", None))
    assert "authorize_arming" not in called, called
    assert "record_submit" not in called, called
    assert "arm" not in called, called
    # it calls the preflight and nothing that moves money
    assert "authorization_preflight" in called, called
    assert "reporting only" in src.lower()


def test_exact_market_binding_is_untouched(clock, monkeypatch):
    """The one-window allowlist still refuses every other window, which is
    the control this diagnosis must not have weakened."""
    start, slug, _, prev_slug = two_windows(clock)
    _, root = approve(monkeypatch, start, clock)
    for other in (prev_slug, "cpc-btc-updown-15m-2026-01-01-0000z"):
        d = CA.campaign_decision(other, root=root, now=clock, reserve=False)
        assert d.get("ok") is False, (other, d)
    d_ok = CA.campaign_decision(slug, root=root, now=clock, reserve=False)
    assert d_ok["ok"] is True
    assert d_ok["scope"]["max_windows"] == 1
