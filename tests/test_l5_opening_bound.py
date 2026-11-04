"""Trading authorization and settlement grace are different periods.

THE INCIDENT, 2026-11-04T08:00Z. The owner authorized EIGHT windows ending
07:45Z and set expiry 08:15Z so the 07:45 window could finish and settle.
The engine read the expiry as "windows may OPEN until then", claimed an
un-enumerated 08:00Z window, submitted 5 orders, filled 3, completed a pair
at 0.99 and settled -$0.2900. No gate failed: `starts_utc` bounded the
grant from below and nothing bounded it from above.

These tests pin the structural distinction: an opening bound is REQUIRED,
is enforced on the window's own start, and expiry can never authorize an
opening.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_campaign_approval as CA

SERIES = "cpc-btc-updown-15m-"


def slug(hhmm: str) -> str:
    return "%s2026-11-04-%sz" % (SERIES, hhmm)


def doc(**over):
    d = {"approved": True, "nonce": "a" * 24, "series": SERIES,
         "issued_utc": "2026-11-04T05:00:00+00:00",
         "starts_utc": "2026-11-04T06:00:00+00:00",
         "last_open_utc": "2026-11-04T07:45:00+00:00",
         "expires_utc": "2026-11-04T08:15:00+00:00",
         "max_windows": 8, "max_total_exposure_usd": "20",
         "max_daily_loss_usd": "5"}
    d.update(over)
    return d


#: `state()` reads the real clock for staleness and expiry, so the whole
#: file pins it to a moment inside the campaign. The opening bound itself is
#: deliberately time-of-day independent -- it reads the WINDOW's start -- and
#: the restart tests below vary this to prove exactly that.
FROZEN = dt.datetime(2026, 11, 4, 7, 50, tzinfo=dt.timezone.utc)


def gate(monkeypatch, d, now=None):
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", json.dumps(d))
    at = now or FROZEN
    return CA.ApprovalGate(CA.EnvApprovalStore(),
                           max_age_s=CA.MAX_CAMPAIGN_S,
                           now_fn=lambda: at)


def scope_of(monkeypatch, d):
    st = gate(monkeypatch, d).state()
    assert st.get("approved") is True, st
    return st["scope"]


# ================================ the incident, both directions

def test_0745_IS_PERMITTED(monkeypatch):
    """The last authorized window opens exactly on the bound."""
    sc = scope_of(monkeypatch, doc())
    assert CA.opening_refusal(sc, slug("0745")) is None


def test_0800_IS_REFUSED_even_though_expiry_is_0815(monkeypatch):
    """THE INCIDENT. 08:00 is inside the settlement grace and outside the
    trading grant, and the grace must not authorize it."""
    sc = scope_of(monkeypatch, doc())
    bad = CA.opening_refusal(sc, slug("0800"))
    assert bad is not None
    assert bad["reason"] == "WINDOW_AFTER_LAST_AUTHORIZED_OPEN", bad
    assert bad["detail"]["late_by_s"] == 900.0


@pytest.mark.parametrize("hhmm", ["0600", "0615", "0700", "0730", "0745"])
def test_every_enumerated_window_is_permitted(monkeypatch, hhmm):
    sc = scope_of(monkeypatch, doc())
    assert CA.opening_refusal(sc, slug(hhmm)) is None


@pytest.mark.parametrize("hhmm", ["0545", "0800", "0815", "0900"])
def test_windows_outside_the_grant_are_refused(monkeypatch, hhmm):
    sc = scope_of(monkeypatch, doc())
    assert CA.opening_refusal(sc, slug(hhmm)) is not None


# ============================ an EXPLICIT window set is also honoured

def test_an_explicit_window_set_refuses_a_gap_inside_the_range(monkeypatch):
    """`window_starts` is a SET, not a range: a window between two approved
    ones is still not approved."""
    sc = scope_of(monkeypatch, doc(
        window_starts=["2026-11-04T06:00:00+00:00",
                       "2026-11-04T07:45:00+00:00"],
        last_open_utc="2026-11-04T07:45:00+00:00"))
    assert CA.opening_refusal(sc, slug("0600")) is None
    assert CA.opening_refusal(sc, slug("0745")) is None
    bad = CA.opening_refusal(sc, slug("0700"))
    assert bad is not None and bad["reason"] == "WINDOW_NOT_IN_AUTHORIZED_SET"


# ================================================ FAIL CLOSED

def test_a_document_with_NO_opening_bound_is_NOT_APPROVED(monkeypatch):
    """The defect itself: expiry alone must never approve a document. This
    is the one backward incompatibility, and it cannot permit trading."""
    d = doc()
    d.pop("last_open_utc")
    st = gate(monkeypatch, d).state()
    assert st.get("approved") is not True
    assert st.get("why") == "approval_opening_bound_absent", st


@pytest.mark.parametrize("over,why", [
    ({"last_open_utc": "not-a-time"}, "approval_last_open_unusable"),
    ({"last_open_utc": "2026-11-04T09:00:00+00:00"},
     "approval_last_open_after_expiry"),
    ({"window_starts": []}, "approval_window_starts_unusable"),
    ({"window_starts": ["nope"]}, "approval_window_starts_unusable"),
    ({"window_starts": ["2026-11-04T05:00:00+00:00"]},
     "approval_window_starts_before_start"),
    ({"window_starts": ["2026-11-04T08:00:00+00:00"]},
     "approval_window_starts_after_last_open"),
])
def test_inconsistent_schedule_fields_are_refused(monkeypatch, over, why):
    st = gate(monkeypatch, doc(**over)).state()
    assert st.get("approved") is not True
    assert st.get("why") == why, st


def test_an_unlocatable_window_fails_closed(monkeypatch):
    sc = scope_of(monkeypatch, doc())
    bad = CA.opening_refusal(sc, SERIES + "not-a-window")
    assert bad is not None and bad["reason"] == "WINDOW_START_UNREADABLE"


def test_a_scope_with_no_bound_refuses_even_if_state_let_it_through(monkeypatch):
    """opening_refusal does not trust that state() already checked: a scope
    assembled by any other path cannot quietly become permissive."""
    bad = CA.opening_refusal({"series": SERIES}, slug("0745"))
    assert bad is not None
    assert bad["reason"] == "CAMPAIGN_OPENING_BOUND_ABSENT"


# ============== time-of-day independence: restart, staleness, expiry

@pytest.mark.parametrize("now", ["2026-11-04T07:59:00+00:00",
                                 "2026-11-04T08:01:00+00:00",
                                 "2026-11-04T08:14:59+00:00"])
def test_a_RESTART_near_the_boundary_cannot_open_0800(monkeypatch, now):
    """RESTART AT 07:59 and after. The bound is on the window's start, so a
    fresh process arriving at any moment inside the settlement grace still
    refuses 08:00 -- no stale in-memory state is involved, because none is
    consulted."""
    at = dt.datetime.fromisoformat(now)
    st = gate(monkeypatch, doc(), now=at).state()
    assert st.get("approved") is True, st
    sc = st["scope"]
    assert CA.opening_refusal(sc, slug("0800")) is not None
    # and the last authorized window stays authorized at the same instants
    assert CA.opening_refusal(sc, slug("0745")) is None


def test_the_window_claim_path_refuses_0800(monkeypatch, tmp_path):
    """End to end through authorize_window, with no reservation written."""
    g = gate(monkeypatch, doc())
    res = CA.authorize_window(g, slug("0800"),
                              ledger=CA.WindowLedger(str(tmp_path)),
                              now=dt.datetime(2026, 11, 4, 8, 1,
                                              tzinfo=dt.timezone.utc),
                              reserve=False)
    assert res["present"] is True and res["ok"] is False
    assert res["reason"] == "WINDOW_AFTER_LAST_AUTHORIZED_OPEN", res


def test_the_window_claim_path_permits_0745(monkeypatch, tmp_path):
    g = gate(monkeypatch, doc())
    res = CA.authorize_window(g, slug("0745"),
                              ledger=CA.WindowLedger(str(tmp_path)),
                              now=dt.datetime(2026, 11, 4, 7, 46,
                                              tzinfo=dt.timezone.utc),
                              reserve=False)
    assert res["ok"] is True, res


def test_AFTER_EXPIRY_nothing_opens_including_an_authorized_window(
        monkeypatch, tmp_path):
    """The settlement grace ENDS too, and then even 07:45 is refused for
    opening. Expiry remains a ceiling; it is simply never a licence."""
    g = gate(monkeypatch, doc())
    res = CA.authorize_window(g, slug("0745"),
                              ledger=CA.WindowLedger(str(tmp_path)),
                              now=dt.datetime(2026, 11, 4, 8, 16,
                                              tzinfo=dt.timezone.utc),
                              reserve=False)
    assert res["ok"] is False
    assert res["reason"] == "CAMPAIGN_EXPIRED", res
