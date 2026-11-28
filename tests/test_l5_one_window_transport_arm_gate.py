"""A one-window campaign must not spend its window on an arm that cannot happen.

THE DEFECT, found while verifying the arming gates for a refreshed
one-window approval (`analysis/l5/issue206/verify_arming_gates.py`).

`authorize_arming` refuses `TRANSPORT_CANNOT_ARM` when the caller states
that the transport's own arm check would fail -- the Q1 handshake is
absent, so `PMUSTransport.arm()` will raise `NotArmed` one call later. The
refusal exists so the owner's window is not consumed by a process that
could never have placed an order. It is the repair for 0145z: the claim was
written at 01:45:01.572 and `t.arm()` raised at 01:45:18, costing one of
three authorized windows with no order and no possibility of one.

THAT REFUSAL WAS NESTED INSIDE `if mw > 1:`.

So it was evaluated for a MULTI-window mandate and skipped entirely for a
ONE-window campaign -- which is the shape of every bounded single-window
probe this programme runs, and the shape of the campaign that lost 0145z.
Measured before the fix, Q1 handshake absent and `transport_armable` False:

    max_windows=1  ->  CLAIM GRANTED, 1 lock line written
    max_windows=2  ->  REFUSED TRANSPORT_CANNOT_ARM
    max_windows=4  ->  REFUSED TRANSPORT_CANNOT_ARM

The repair protected every case except the only one in use. The check now
sits before BOTH claim paths -- the mandate's two-level claim and the
single-window claim at step 11 -- because the property is about the
transport, not about how many windows were approved.

WHAT IS NOT WIDENED. `None` still means "not stated" and behaves exactly as
before, so every existing caller and test is unchanged; only an explicit
`False` refuses. And nothing becomes armable that was not: a campaign-only
process could never arm, because the transport refused it a call later.
The only thing that changes is that the window survives.

No network, no venue, no order.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX

SERIES = "cpc-btc-updown-15m-"


def iso(d):
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug_of(start):
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        start.strftime("%Y-%m-%d"), start.hour, start.minute)


def next_window(now, lead_min=40):
    start = now.replace(minute=(now.minute // 15) * 15, second=0,
                        microsecond=0)
    while (start - now).total_seconds() < lead_min * 60:
        start += dt.timedelta(minutes=15)
    return start


def doc_for(start, now, *, max_windows=1, nonce=None, **over):
    d = {"approved": True,
         "nonce": nonce or ("probe-%s" % start.strftime("%Y%m%d-%H%Mz")),
         "series": SERIES, "issued_utc": iso(now),
         "expires_utc": iso(start + dt.timedelta(minutes=30)),
         "max_windows": int(max_windows),
         "max_total_exposure_usd": "1.00",
         "max_daily_loss_usd": "5.00",
         "window_starts": [iso(start)]}
    d.update(over)
    return d


def limits():
    """The REAL SafetyLimits. Two gates read it two different ways --
    `_limit_of` with getattr, `scoped_limits` with as_dict() -- so a
    double with only attributes refuses CAPITAL_SCOPE_UNKNOWN and the test
    would never reach the gate it is about."""
    from decimal import Decimal as D
    return LX.SafetyLimits(
        max_usd_per_market=D("20"), max_abs_residual=D("10"),
        max_total_exposure_usd=D("1.00"), max_concurrent_markets=1,
        max_live_orders=2, daily_realized_loss_halt_usd=D("5"))


def bounds(start):
    return {"source": AUTH.WINDOW_SOURCE_VENUE, "start": start.isoformat(),
            "end": (start + dt.timedelta(seconds=900)).isoformat()}


class Store:
    def orders_uncertain(self):
        return ()


@pytest.fixture
def clock():
    return dt.datetime.now(dt.timezone.utc)


def attempt(tmp_path, monkeypatch, clock, *, max_windows, armable,
            handshake=False, market=None):
    """Drive the REAL boundary. Returns (claim_or_None, reason, locks)."""
    start = next_window(clock)
    mkt = market or slug_of(start)
    at = start + dt.timedelta(seconds=300)          # inside the window
    root = tempfile.mkdtemp()
    doc = doc_for(start, clock, max_windows=max_windows,
                  nonce="n-%d-%s" % (max_windows, os.path.basename(root)))
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    monkeypatch.setenv(CA.ROOT_ENV, root)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-test")
    from src import pmus_auth as Q1
    for k in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(k, raising=False)
    if handshake:
        monkeypatch.setenv(Q1.LIVE_ENV, "YES")
        monkeypatch.setenv(Q1.SID_ENV, "sess-t")
        monkeypatch.setenv(Q1.ARM_ENV, "sess-t")
    ledger = str(tmp_path / ("auth-%s.jsonl" % os.path.basename(root)))
    camp = CA.campaign_decision(mkt, now=at, reserve=False)
    assert camp.get("ok") is True or market is not None, camp
    try:
        claim = AUTH.authorize_arming(
            campaign=camp, market=mkt, preflight={"preflight": "PASSED"},
            store=Store(), halted=False, halt_gate_present=True,
            reconciled=True, unresolved=(), limits=limits(),
            shadow_locked=False, require_durable=False,
            window_bounds=bounds(start), nominal_window_s=900.0,
            transport_armable=armable, path=ledger, now=at)
        locks = (sum(1 for ln in open(ledger) if ln.strip())
                 if os.path.exists(ledger) else 0)
        return claim, None, locks
    except AUTH.Refused as exc:
        locks = (sum(1 for ln in open(ledger) if ln.strip())
                 if os.path.exists(ledger) else 0)
        return None, str(getattr(exc, "reason", None) or exc), locks


# =====================================================================
# THE DEFECT
# =====================================================================

@pytest.mark.parametrize("max_windows", [1, 2, 4, 8])
def test_an_unarmable_transport_is_refused_at_EVERY_window_budget(
        tmp_path, monkeypatch, clock, max_windows):
    """THE REGRESSION. Before the fix, max_windows=1 granted the claim and
    wrote the lock while 2, 4 and 8 refused."""
    claim, reason, locks = attempt(tmp_path, monkeypatch, clock,
                                   max_windows=max_windows, armable=False)
    assert claim is None, (max_windows, claim)
    assert reason == "TRANSPORT_CANNOT_ARM", (max_windows, reason)


@pytest.mark.parametrize("max_windows", [1, 2, 4])
def test_the_refusal_writes_NO_claim_lock_at_any_budget(
        tmp_path, monkeypatch, clock, max_windows):
    """THE POINT OF THE GATE. A window not spent is a window the owner can
    still use. A refusal that wrote the lock would be the 0145z loss with a
    tidier error message."""
    claim, reason, locks = attempt(tmp_path, monkeypatch, clock,
                                   max_windows=max_windows, armable=False)
    assert claim is None
    assert locks == 0, (max_windows, locks)


def test_the_ONE_window_case_specifically_spends_nothing(tmp_path,
                                                         monkeypatch,
                                                         clock):
    """Stated on its own because it is the only shape in use: every
    bounded single-window probe this programme runs is max_windows=1."""
    claim, reason, locks = attempt(tmp_path, monkeypatch, clock,
                                   max_windows=1, armable=False)
    assert (claim, reason, locks) == (None, "TRANSPORT_CANNOT_ARM", 0)


def test_the_refusal_names_what_the_operator_must_set(tmp_path, monkeypatch,
                                                      clock):
    """A refusal an operator cannot act on is a refusal that gets
    overridden. It names the three handshake variables and says the window
    survived."""
    from src import pmus_auth as Q1
    start = next_window(clock)
    at = start + dt.timedelta(seconds=300)
    root = tempfile.mkdtemp()
    monkeypatch.setenv(CA.APPROVAL_ENV,
                       json.dumps(doc_for(start, clock, nonce="n-detail")))
    monkeypatch.setenv(CA.ROOT_ENV, root)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-test")
    for k in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(k, raising=False)
    camp = CA.campaign_decision(slug_of(start), now=at, reserve=False)
    with pytest.raises(AUTH.Refused) as ei:
        AUTH.authorize_arming(
            campaign=camp, market=slug_of(start),
            preflight={"preflight": "PASSED"}, store=Store(), halted=False,
            halt_gate_present=True, reconciled=True, unresolved=(),
            limits=limits(), shadow_locked=False, require_durable=False,
            window_bounds=bounds(start), nominal_window_s=900.0,
            transport_armable=False,
            path=str(tmp_path / "auth.jsonl"), now=at)
    detail = getattr(ei.value, "detail", None) or {}
    assert detail.get("window_preserved") is True, detail
    assert set(detail.get("needs") or ()) == {Q1.LIVE_ENV, Q1.SID_ENV,
                                              Q1.ARM_ENV}, detail


# =====================================================================
# NOTHING ELSE MOVED
# =====================================================================

@pytest.mark.parametrize("max_windows", [1, 2])
def test_an_ARMABLE_transport_still_claims(tmp_path, monkeypatch, clock,
                                           max_windows):
    claim, reason, locks = attempt(tmp_path, monkeypatch, clock,
                                   max_windows=max_windows, armable=True,
                                   handshake=True)
    assert claim is not None, reason
    assert locks >= 1


@pytest.mark.parametrize("max_windows", [1, 2])
def test_NOT_STATED_behaves_exactly_as_before(tmp_path, monkeypatch, clock,
                                              max_windows):
    """`None` is "the caller could not establish it", which is the
    pre-existing behaviour every other caller and test relies on. Only an
    explicit False refuses."""
    claim, reason, locks = attempt(tmp_path, monkeypatch, clock,
                                   max_windows=max_windows, armable=None)
    assert claim is not None, reason
    assert locks >= 1


def test_the_gate_is_no_longer_inside_the_multi_window_branch():
    """THE STRUCTURAL ASSERTION, so the fix cannot regress by someone
    re-indenting it. The refusal must appear at function-body indent, not
    nested inside `if mw > 1:`."""
    import inspect
    src = inspect.getsource(AUTH.authorize_arming)
    lines = src.split("\n")
    hits = [i for i, l in enumerate(lines)
            if "if transport_armable is False:" in l]
    assert len(hits) == 1, hits
    line = lines[hits[0]]
    indent = len(line) - len(line.lstrip())
    assert indent == 4, (indent, line)
    # and it precedes the multi-window branch
    mw = next(i for i, l in enumerate(lines) if l.strip() == "if mw > 1:")
    assert hits[0] < mw, (hits[0], mw)


def test_the_transport_arm_check_runs_before_the_window_claim(tmp_path,
                                                              monkeypatch,
                                                              clock):
    """ORDERING, asserted through behaviour rather than through source: the
    gate must fire before the claim write, which is what makes
    `window_preserved` true."""
    claim, reason, locks = attempt(tmp_path, monkeypatch, clock,
                                   max_windows=1, armable=False)
    assert reason == "TRANSPORT_CANNOT_ARM"
    assert locks == 0
    # and the window is still grantable afterwards
    claim2, reason2, locks2 = attempt(tmp_path, monkeypatch, clock,
                                      max_windows=1, armable=True,
                                      handshake=True)
    assert claim2 is not None, reason2
