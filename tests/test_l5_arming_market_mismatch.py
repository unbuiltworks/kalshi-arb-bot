"""The executor's arming boundary refuses a market the approval does not name.

THE OWNER'S CONCERN, STATED EXACTLY: "A preflight can pass for one BTC15m
window while the executor heartbeat still refers to the previous window."
The review CLI and the executor are two different readers of the same
approval, and only one of them can put an order on a venue. #212 made the
CLI refuse the mismatch; this file proves the ENFORCEMENT boundary refuses
it too, because a review tool that disagrees with the executor is a
reporting problem, while an executor that arms on the wrong window is a
money problem.

FOUR ASSERTIONS, all of them, for every mismatch below:

  1. arming is REFUSED;
  2. no authorization claim is consumed -- the single-use lock is not
     written, so the authorization is still available to the right window;
  3. no campaign window is spent -- the owner's one window survives;
  4. no order is submitted, and the transport is never even armed.

WHY ALL FOUR AND NOT JUST THE FIRST. Each has its own failure mode and
they have all happened. 2045z spent a claim on a window that then refused
usability and was unreachable for the rest of its life. 1600z and 1630z
spent campaign windows before the arming boundary had ruled on anything.
0145z accepted a campaign alone, spent a claim, and `PMUSTransport.arm()`
refused the same process one call later.

THE ORDERING THAT MAKES 2 AND 3 POSSIBLE is already in the code and this
pins it: `campaign_decision` is called with `reserve=False` so the verdict
is read-only, and `reserve_window` is a separate call made only once
`authorize_arming` has RETURNED a claim.

No network, no venue, no order. The transport here is a double that
records what was asked of it and raises if anything tries to submit.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH
from src import pmus_auth as Q1

SERIES = "cpc-btc-updown-15m-"
SID = "sess-mismatch-1"


class Limits:
    """The running caps as an OBJECT, because `_limit_of` reads them with
    `getattr` and a dict therefore reads as UNREADABLE -- which refuses
    `CAMPAIGN_RUNNING_LIMITS_UNREADABLE` before the market is ever
    considered. The deployed values, so a test cannot pass by being
    narrower than production."""

    max_usd_per_market = "20"
    max_total_exposure_usd = "1.00"
    daily_realized_loss_halt_usd = "5"
    max_abs_residual = "10"
    max_concurrent_markets = 1
    max_live_orders = 2


LIMITS = Limits()


def iso(d):
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug_of(start: dt.datetime) -> str:
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        start.strftime("%Y-%m-%d"), start.hour, start.minute)


def two_windows(now):
    """The approved window, and the one the executor is still sitting on.

    Deliberately ADJACENT: the realistic mismatch is one rollover of lag,
    not a wild slug. `approved` is far enough ahead that the campaign's own
    clock checks do not refuse it for a different reason.
    """
    start = now.replace(minute=(now.minute // 15) * 15, second=0,
                        microsecond=0)
    while (start - now).total_seconds() < 20 * 60:
        start += dt.timedelta(minutes=15)
    previous = start - dt.timedelta(minutes=15)
    return start, previous


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


class Store:
    def orders_uncertain(self):
        return ()


class Transport:
    """Records what was asked of it, and REFUSES to pretend it submitted."""

    def __init__(self, market_slug):
        self.market_slug = market_slug
        self.armed = False
        self.arm_calls = 0
        self.submitted = []

    def arm(self, *a, **kw):
        self.arm_calls += 1
        self.armed = True
        return True

    def submit(self, intent):
        raise AssertionError(
            "an order reached the venue double: %r" % (intent,))


def owner_authorized(monkeypatch):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, SID)
    monkeypatch.setenv(Q1.ARM_ENV, SID)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-mismatch-test")


def live_window(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(seconds=300)
    return {"source": AUTH.WINDOW_SOURCE_VENUE, "start": start.isoformat(),
            "end": (start + dt.timedelta(seconds=900)).isoformat()}


@pytest.fixture
def clock():
    return dt.datetime.now(dt.timezone.utc)


@pytest.fixture
def camp_root(monkeypatch):
    root = tempfile.mkdtemp()
    monkeypatch.setenv(CA.ROOT_ENV, root)
    return root


def windows_spent(doc, root):
    """How many of the owner's windows this approval has actually spent,
    read from the durable ledger rather than from a return value."""
    sid = CA.nonce_sid(str(doc["nonce"]))
    return len(CA.WindowLedger(root).read(sid).get("windows") or ())


def claims_written(ledger_path):
    """Authorization claim locks on disk. Zero is the assertion that
    matters: a claim is single-use and is never returned."""
    if not os.path.exists(ledger_path):
        return 0
    with open(ledger_path, "r", encoding="utf-8") as fh:
        return sum(1 for ln in fh if ln.strip())


# =====================================================================
# THE CAMPAIGN LAYER SEES THE MISMATCH
# =====================================================================

def test_the_previous_window_is_refused_by_name(clock, camp_root,
                                                monkeypatch):
    approved, previous = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))

    good = CA.campaign_decision(slug_of(approved), now=clock, reserve=False)
    assert good["ok"] is True, good

    bad = CA.campaign_decision(slug_of(previous), now=clock, reserve=False)
    assert bad["present"] is True
    assert bad["ok"] is False
    assert bad["reason"] == "WINDOW_NOT_IN_AUTHORIZED_SET", bad
    # and reading the verdict spent nothing, for EITHER window
    assert windows_spent(doc, camp_root) == 0


def test_the_NEXT_window_is_refused_too_not_only_the_previous(clock,
                                                             camp_root,
                                                             monkeypatch):
    """Lag is not the only mismatch; running ahead is the same defect."""
    approved, _ = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    later = approved + dt.timedelta(minutes=15)
    out = CA.campaign_decision(slug_of(later), now=clock, reserve=False)
    assert out["ok"] is False
    assert out["reason"] == "WINDOW_NOT_IN_AUTHORIZED_SET", out
    assert windows_spent(doc, camp_root) == 0


# =====================================================================
# THE ARMING BOUNDARY REFUSES IT, AND SPENDS NOTHING
# =====================================================================

def test_arming_is_refused_and_no_claim_and_no_window_are_spent(
        clock, camp_root, monkeypatch, tmp_path):
    """THE FOUR ASSERTIONS, on the real `authorize_arming`."""
    approved, previous = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    owner_authorized(monkeypatch)
    ledger = str(tmp_path / "auth.jsonl")

    t = Transport(slug_of(previous))          # the executor's ACTIVE market
    campaign = CA.campaign_decision(t.market_slug, now=clock, reserve=False)
    assert campaign["ok"] is False            # the mismatch, seen

    # 1. ARMING IS REFUSED.
    with pytest.raises(AUTH.Refused) as ei:
        AUTH.authorize_arming(
            campaign=campaign, market=t.market_slug,
            preflight={"preflight": "PASSED"}, store=Store(),
            halted=False, halt_gate_present=True, reconciled=True,
            unresolved=(), limits=LIMITS, shadow_locked=False,
            require_durable=False, window_bounds=live_window(),
            nominal_window_s=900.0, transport_armable=True, path=ledger)
    assert "WINDOW_NOT_IN_AUTHORIZED_SET" in str(ei.value), str(ei.value)

    # 2. NO AUTHORIZATION CLAIM IS CONSUMED.
    assert claims_written(ledger) == 0

    # 3. NO CAMPAIGN WINDOW IS SPENT.
    assert windows_spent(doc, camp_root) == 0

    # 4. NO ORDER IS SUBMITTED, and the transport was never armed.
    assert t.arm_calls == 0
    assert t.armed is False
    assert t.submitted == []


def test_the_authorization_stays_available_to_the_RIGHT_window(
        clock, camp_root, monkeypatch, tmp_path):
    """THE POINT OF ASSERTION 2. A refusal that burned the claim would make
    the approved window unreachable -- which is exactly how 2045z was lost.

    ASSERTED AS THE PROPERTY, not by completing an arm. Driving a full
    successful arm here would mean satisfying every unrelated precondition
    (`CAPITAL_SCOPE_UNKNOWN`, the gates, the durable root), and those have
    their own file in `test_l5_arming_boundary.py`. What belongs here is
    that the mismatch consumed NOTHING, so the approved window is still
    exactly as arm-able as it was before the wrong one was tried.
    """
    approved, previous = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    owner_authorized(monkeypatch)
    ledger = str(tmp_path / "auth.jsonl")
    sid = CA.nonce_sid(str(doc["nonce"]))

    before = CA.WindowLedger(camp_root).would_reserve(
        sid, slug_of(approved), max_windows=1)
    assert before["ok"] is True, before
    assert before["windows_remaining"] == 1, before

    wrong = CA.campaign_decision(slug_of(previous), now=clock, reserve=False)
    with pytest.raises(AUTH.Refused) as ei:
        AUTH.authorize_arming(
            campaign=wrong, market=slug_of(previous),
            preflight={"preflight": "PASSED"}, store=Store(), halted=False,
            halt_gate_present=True, reconciled=True, unresolved=(),
            limits=LIMITS, shadow_locked=False, require_durable=False,
            window_bounds=live_window(), nominal_window_s=900.0,
            transport_armable=True, path=ledger)
    assert "WINDOW_NOT_IN_AUTHORIZED_SET" in str(ei.value), str(ei.value)

    # NOTHING WAS CONSUMED by the refusal
    assert claims_written(ledger) == 0
    assert windows_spent(doc, camp_root) == 0

    # and the approved window is still exactly as available as before
    after = CA.WindowLedger(camp_root).would_reserve(
        sid, slug_of(approved), max_windows=1)
    assert after["ok"] is True, after
    assert after["windows_remaining"] == before["windows_remaining"], (
        after, before)
    assert after.get("dry_run") is True
    assert CA.campaign_decision(slug_of(approved), now=clock,
                                reserve=False)["ok"] is True


def test_the_window_is_spent_ONLY_after_the_claim_returns(clock, camp_root,
                                                          monkeypatch,
                                                          tmp_path):
    """THE ORDERING, pinned. `campaign_decision(reserve=False)` spends
    nothing however many times it is asked; `reserve_window` is the only
    thing that spends, and the runner calls it after the claim returns."""
    approved, _ = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    for _ in range(5):
        assert CA.campaign_decision(slug_of(approved), now=clock,
                                    reserve=False)["ok"] is True
    assert windows_spent(doc, camp_root) == 0

    spend = CA.reserve_window(slug_of(approved), now=clock)
    assert spend["ok"] is True, spend
    assert windows_spent(doc, camp_root) == 1
    # idempotent per slug: a restart inside the same window spends no more
    again = CA.reserve_window(slug_of(approved), now=clock)
    assert again.get("reserved_now") is False, again
    assert windows_spent(doc, camp_root) == 1


def test_a_mismatched_window_cannot_spend_the_window_even_by_reserving(
        clock, camp_root, monkeypatch):
    """`reserve_window` is called with the ACTIVE slug, so a mismatch
    refuses there too rather than spending the approved window's budget on
    the wrong market."""
    approved, previous = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    out = CA.reserve_window(slug_of(previous), now=clock)
    assert out.get("ok") is not True, out
    assert windows_spent(doc, camp_root) == 0


# =====================================================================
# THE SHADOW LOCK OUTRANKS A CORRECT WINDOW
# =====================================================================

def test_even_the_RIGHT_window_cannot_arm_while_the_shadow_lock_holds(
        clock, camp_root, monkeypatch, tmp_path):
    """The freeze is not a market question. With `shadow_locked` true the
    boundary refuses the approved window too, and still spends nothing."""
    approved, _ = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    owner_authorized(monkeypatch)
    ledger = str(tmp_path / "auth.jsonl")
    right = CA.campaign_decision(slug_of(approved), now=clock, reserve=False)
    assert right["ok"] is True
    with pytest.raises(AUTH.Refused):
        AUTH.authorize_arming(
            campaign=right, market=slug_of(approved),
            preflight={"preflight": "PASSED"}, store=Store(), halted=False,
            halt_gate_present=True, reconciled=True, unresolved=(),
            limits=LIMITS, shadow_locked=True, require_durable=False,
            window_bounds=live_window(), nominal_window_s=900.0,
            transport_armable=True, path=ledger)
    assert claims_written(ledger) == 0
    assert windows_spent(doc, camp_root) == 0


# =====================================================================
# THE CLI AND THE EXECUTOR AGREE
# =====================================================================

def test_the_review_gate_and_the_executor_refuse_the_SAME_mismatch(
        clock, camp_root, monkeypatch):
    """#212's `exact_window_consistency` is the review-time reader and
    `authorize_arming` is the enforcement one. A mismatch must not be
    visible to only one of them -- that divergence is the whole defect."""
    from src import l5_probe_preflight as PF
    approved, previous = two_windows(clock)
    doc = doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))

    # the heartbeat still reports the PREVIOUS window
    hb = {"market": slug_of(previous),
          "authorization_preflight": {"ok": True,
                                      "evaluated_market": slug_of(previous)},
          "campaign": {"permits_this_window": False}}
    review = PF.exact_window_consistency(
        heartbeat=hb, plan={"market": slug_of(approved)},
        market=slug_of(approved))
    assert review["ok"] is False, review
    assert "executor_active_market_matches" in review["failed"], review
    assert "campaign_permits_exact_window" in review["failed"], review

    # and the campaign layer the executor consults says the same
    assert CA.campaign_decision(slug_of(previous), now=clock,
                                reserve=False)["ok"] is False


def test_the_review_gate_passes_only_when_every_reader_names_one_market(
        clock, camp_root, monkeypatch):
    from src import l5_probe_preflight as PF
    approved, _ = two_windows(clock)
    s = slug_of(approved)
    hb = {"market": s,
          "authorization_preflight": {"ok": True, "evaluated_market": s},
          "campaign": {"permits_this_window": True}}
    out = PF.exact_window_consistency(heartbeat=hb, plan={"market": s},
                                      market=s)
    assert out["ok"] is True, out
    assert out["failed"] == [], out
