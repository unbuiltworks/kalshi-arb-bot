"""The exact owner input a one-window probe needs, pinned.

PRIORITY 1 asked me to identify the owner-specific inputs precisely. The
three preflight failures resolve differently:

  shadow_lock         an env the OWNER flips; nothing else clears it
  absolute_deadline   an env (`L5_LIVE_AUTH_NOT_AFTER`). In single-window
                      mode with a valid campaign it is OPTIONAL -- the
                      module says clearing a stale one "is a legitimate
                      fix rather than a widening"
  campaign_approval   an owner-written DOCUMENT in `L5_CAMPAIGN_APPROVAL`

The document needs **nine** fields, not the eight in `REQUIRED_FIELDS`.
The ninth is an OPENING BOUND -- `window_starts` or `last_open_utc` -- and
omitting it parses as approved but refuses every window with
`CAMPAIGN_OPENING_BOUND_ABSENT`. I hit that on the first attempt, which is
exactly why it is a test and not a note.

`window_starts` is the right one for a one-window probe: it is an
exact-match allowlist, so the one-window cap is enforced by the
authorization layer rather than by convention.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

import pytest

from src import l5_campaign_approval as CA

SERIES = "cpc-btc-updown-15m-"


def iso(d):
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def window(now, minutes_ahead=15):
    start = now.replace(minute=(now.minute // 15) * 15, second=0,
                        microsecond=0)
    while (start - now).total_seconds() < minutes_ahead * 60:
        start += dt.timedelta(minutes=15)
    slug = "cpc-btc-updown-15m-%s-%02d%02dz" % (
        start.strftime("%Y-%m-%d"), start.hour, start.minute)
    return start, slug


def doc_for(start, now, **over):
    d = {"approved": True,
         "nonce": "probe-%s" % start.strftime("%Y%m%d-%H%Mz"),
         "series": SERIES,
         "issued_utc": iso(now),
         "expires_utc": iso(start + dt.timedelta(minutes=30)),
         "max_windows": 1,
         "max_total_exposure_usd": "1.00",
         "max_daily_loss_usd": "5.00",
         "window_starts": [iso(start)]}
    d.update(over)
    return d


def decide(doc, slug, now, monkeypatch):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    monkeypatch.setenv(CA.ROOT_ENV, tempfile.mkdtemp())
    return CA.campaign_decision(slug, now=now, reserve=False)


@pytest.fixture
def clock():
    return dt.datetime.now(dt.timezone.utc)


# ================================================== the working document
def test_the_nine_field_document_approves(clock, monkeypatch):
    start, slug = window(clock)
    out = decide(doc_for(start, clock), slug, clock, monkeypatch)
    assert out["ok"] is True, out
    sc = out["scope"]
    assert sc["max_windows"] == 1
    assert sc["max_total_exposure_usd"] == "1.00"
    assert sc["max_daily_loss_usd"] == "5.00"
    assert sc["series"] == SERIES
    # nothing was clamped, so the document asked for no more than allowed
    assert all(v is False for v in sc["clamped"].values()), sc["clamped"]
    assert sc["document_sha256"]


def test_omitting_the_opening_bound_refuses_every_window(clock, monkeypatch):
    """THE MISTAKE I MADE. The eight REQUIRED_FIELDS parse, and then every
    window is refused -- which looks like an approval problem rather than a
    missing field."""
    start, slug = window(clock)
    d = doc_for(start, clock)
    d.pop("window_starts")
    out = decide(d, slug, clock, monkeypatch)
    assert out["present"] is True
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_OPENING_BOUND_ABSENT", out


def test_last_open_utc_is_the_other_accepted_opening_bound(clock,
                                                          monkeypatch):
    start, slug = window(clock)
    d = doc_for(start, clock)
    d.pop("window_starts")
    d["last_open_utc"] = iso(start + dt.timedelta(minutes=1))
    out = decide(d, slug, clock, monkeypatch)
    assert out["ok"] is True, out


# ======================================== one window, enforced not promised
def test_window_starts_is_an_exact_match_allowlist(clock, monkeypatch):
    """THE ONE-WINDOW CAP. Any window but the authorized one is refused by
    the authorization layer itself."""
    start, slug = window(clock)
    d = doc_for(start, clock)
    for delta in (15, 30, -15):
        other = start + dt.timedelta(minutes=delta)
        other_slug = "cpc-btc-updown-15m-%s-%02d%02dz" % (
            other.strftime("%Y-%m-%d"), other.hour, other.minute)
        out = decide(d, other_slug, clock, monkeypatch)
        assert out["ok"] is False, (other_slug, out)
        assert out["reason"] == "WINDOW_NOT_IN_AUTHORIZED_SET", out


def test_a_window_outside_the_series_is_refused(clock, monkeypatch):
    start, _ = window(clock)
    out = decide(doc_for(start, clock),
                 "cpceth-eth-updown-15m-2026-11-09-0145z", clock,
                 monkeypatch)
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_SERIES_MISMATCH", out


# ============================= the document cannot widen anything, at all
def test_a_greedy_document_is_clamped_to_the_code_constants(clock,
                                                            monkeypatch):
    """A document asking $50, 40 windows and $500 daily yields $20, 8 and
    $5. There is deliberately no path by which a number in the document
    raises a limit."""
    start, slug = window(clock)
    # NOTE the nonce: "greedy" is six characters and the module refuses a
    # nonce under eight, so a short one would refuse before any clamping
    # happened and this test would pass for the wrong reason.
    d = doc_for(start, clock, nonce="greedy-probe-01", max_windows=40,
                max_total_exposure_usd="50.00",
                max_daily_loss_usd="500")
    out = decide(d, slug, clock, monkeypatch)
    sc = out.get("scope")
    assert sc, out
    assert sc["max_windows"] == CA.MAX_WINDOWS == 8
    assert sc["max_total_exposure_usd"] == str(CA.MAX_TOTAL_EXPOSURE_USD)
    assert sc["max_daily_loss_usd"] == str(CA.MAX_DAILY_LOSS_USD)
    assert sc["clamped"]["max_windows"] is True
    assert sc["clamped"]["max_total_exposure_usd"] is True
    assert sc["clamped"]["max_daily_loss_usd"] is True


def test_the_allowed_series_is_a_code_constant_not_a_document_field(clock,
                                                                   monkeypatch):
    start, slug = window(clock)
    out = decide(doc_for(start, clock, series="anything-i-like-"), slug,
                 clock, monkeypatch)
    assert out["ok"] is False, out
    # still a code constant. The 5-minute series joined it by owner decision
    # on 2026-11-11; a document still cannot name a series the code lacks.
    assert CA.ALLOWED_SERIES == (
        SERIES, "cpc-btc-updown-5m-", "cpceth-eth-updown-5m-",
        "cpcsol-sol-updown-5m-", "cpcxrp-xrp-updown-5m-")


# ======================================================= fail closed, always
@pytest.mark.parametrize("field", [
    "approved", "nonce", "series", "issued_utc", "expires_utc",
    "max_windows", "max_total_exposure_usd", "max_daily_loss_usd"])
def test_every_required_field_is_required(field, clock, monkeypatch):
    start, slug = window(clock)
    d = doc_for(start, clock)
    d.pop(field)
    out = decide(d, slug, clock, monkeypatch)
    assert out["ok"] is False, (field, out)


def test_approved_false_is_a_refusal_not_a_formality(clock, monkeypatch):
    start, slug = window(clock)
    out = decide(doc_for(start, clock, approved=False), slug, clock,
                 monkeypatch)
    assert out["ok"] is False, out


def test_an_expired_campaign_is_refused(clock, monkeypatch):
    start, slug = window(clock)
    d = doc_for(start, clock,
                expires_utc=iso(clock - dt.timedelta(minutes=1)))
    out = decide(d, slug, clock, monkeypatch)
    assert out["ok"] is False
    assert out["reason"] in ("CAMPAIGN_EXPIRED", "APPROVAL_NOT_VALID"), out


def test_an_absent_variable_reads_as_silence_not_as_approval(clock,
                                                             monkeypatch):
    _, slug = window(clock)
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    monkeypatch.setenv(CA.ROOT_ENV, tempfile.mkdtemp())
    out = CA.campaign_decision(slug, now=clock, reserve=False)
    assert out.get("ok") is not True
    assert out.get("present") is not True


def test_a_malformed_variable_is_refused_rather_than_ignored(clock,
                                                             monkeypatch):
    _, slug = window(clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, "{not json")
    monkeypatch.setenv(CA.ROOT_ENV, tempfile.mkdtemp())
    out = CA.campaign_decision(slug, now=clock, reserve=False)
    assert out.get("ok") is not True


def test_a_nonce_under_eight_characters_is_refused(clock, monkeypatch):
    start, slug = window(clock)
    out = decide(doc_for(start, clock, nonce="short"), slug, clock,
                 monkeypatch)
    assert out["ok"] is False, out


def test_claude_cannot_approve_a_campaign(clock):
    """The security property, asserted on the module rather than trusted.
    The approval is READ from an owner-controlled object; this module has
    no writer for it."""
    import inspect
    import pytest as _pytest
    src = inspect.getsource(CA)
    assert "def get(" in src          # a reader
    # THERE IS a `put`, and it is STRONGER than its absence: it exists and
    # REFUSES, so a caller that tries to write its own approval gets an
    # explicit failure rather than an AttributeError someone might catch.
    assert "DELIBERATELY NOT IMPLEMENTED" in src
    store = CA.EnvApprovalStore()
    with _pytest.raises(CA.ApprovalReadUnavailable):
        store.put(CA.APPROVAL_KEY, b'{"approved": true}')


# ===================================== the generator emits a valid document
def test_the_window_generator_emits_a_document_that_actually_approves(
        monkeypatch):
    """The CLI's `window` mode exists because hand-computing an ISO
    timestamp for an exact-match allowlist under time pressure is how the
    wrong window gets authorized. So what it emits must approve."""
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cli = os.path.join(root, "analysis", "l5", "issue208",
                       "run_probe_preflight.py")
    r = subprocess.run([sys.executable, cli, "window", "--json"],
                       capture_output=True, text=True, timeout=120,
                       cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    doc, slug = out["approval_document"], out["slug"]
    assert out["lead_s"] >= 600, out
    now = dt.datetime.now(dt.timezone.utc)
    d = decide(doc, slug, now, monkeypatch)
    assert d["ok"] is True, (d, doc, slug)
    assert d["scope"]["max_windows"] == 1
    assert all(v is False for v in d["scope"]["clamped"].values())
