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

def test_a_document_with_NO_opening_bound_OPENS_NOTHING(monkeypatch, tmp_path):
    """THE DEFECT ITSELF, and where the compatibility line goes.

    A legacy document with no opening bound still PARSES -- every identity,
    namespace, clamping and budget property that does not open a window
    keeps working -- because parsing cannot trade. It is INERT rather than
    invalid: it may settle and reconcile, and it may not open any window,
    including one comfortably inside its expiry.
    """
    d = doc()
    d.pop("last_open_utc")
    st = gate(monkeypatch, d).state()
    assert st.get("approved") is True, st          # parses
    sc = st["scope"]
    assert sc["last_open_utc"] is None
    assert sc["window_starts"] is None
    for hhmm in ("0600", "0700", "0745", "0800"):
        bad = CA.opening_refusal(sc, slug(hhmm))
        assert bad is not None, hhmm
        assert bad["reason"] == "CAMPAIGN_OPENING_BOUND_ABSENT", (hhmm, bad)
    # and end to end: the claim path refuses and reserves nothing
    res = CA.authorize_window(gate(monkeypatch, d), slug("0745"),
                              ledger=CA.WindowLedger(str(tmp_path)),
                              now=FROZEN, reserve=True)
    assert res["ok"] is False
    assert res["reason"] == "CAMPAIGN_OPENING_BOUND_ABSENT", res


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


# ===== the SUBMISSION path shares the one definition (requirement 2)

def live_doc(**over):
    """A document anchored to the REAL clock.

    The submission path reads `approval_gate()`, which uses the real clock
    for staleness and expiry, so the fixed-date document used above would be
    stale here -- and a stale document is now a refusal in its own right,
    which would mask the WINDOW check under test. Windows are placed on the
    current 15-minute grid: the last authorized open is 45 minutes out and
    the settlement grace runs an hour.
    """
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    base0 = now - dt.timedelta(minutes=now.minute % 15)
    d = {"approved": True, "nonce": "b" * 24, "series": SERIES,
         "issued_utc": (now - dt.timedelta(minutes=1)).isoformat(),
         "starts_utc": base0.isoformat(),
         "last_open_utc": (base0 + dt.timedelta(minutes=45)).isoformat(),
         "expires_utc": (base0 + dt.timedelta(minutes=60)).isoformat(),
         "max_windows": 4, "max_total_exposure_usd": "20",
         "max_daily_loss_usd": "5"}
    d.update(over)
    return d, base0


def live_slug(at):
    return "%s%s-%02d%02dz" % (SERIES, at.strftime("%Y-%m-%d"),
                               at.hour, at.minute)


def _claim(monkeypatch, tmp_path, market, d=None):
    from src import l5_live_authorization as LX
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", json.dumps(d))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "opening-bound-test")
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path))
    return LX.claim_submission(market, {"probe": True},
                               path=str(tmp_path / "ledger.json"))


def test_a_submission_AFTER_the_last_open_IS_REFUSED_before_the_wire(
        monkeypatch, tmp_path):
    """Belt and braces on the incident: even with a claim somehow held, the
    submission refuses before any budget or ticket is consumed and before
    anything reaches the venue."""
    from src import l5_live_authorization as LX
    d, base0 = live_doc()
    beyond = live_slug(base0 + dt.timedelta(minutes=60))   # past last_open
    with pytest.raises(LX.Refused) as e:
        _claim(monkeypatch, tmp_path, beyond, d=d)
    assert e.value.reason == "SUBMISSION_WINDOW_NOT_AUTHORIZED"
    assert e.value.detail["why"] == "WINDOW_AFTER_LAST_AUTHORIZED_OPEN", \
        e.value.detail


def test_a_submission_into_an_authorized_window_passes_the_WINDOW_check(
        monkeypatch, tmp_path):
    """It may still be refused by a budget or ticket downstream; what
    matters is that the WINDOW check does not reject it."""
    from src import l5_live_authorization as LX
    d, base0 = live_doc()
    inside = live_slug(base0 + dt.timedelta(minutes=45))   # the last open
    try:
        _claim(monkeypatch, tmp_path, inside, d=d)
    except LX.Refused as exc:
        assert "SUBMISSION_WINDOW_NOT_AUTHORIZED" not in str(exc), exc


def test_a_bound_less_document_refuses_every_submission(monkeypatch, tmp_path):
    """An inert legacy document settles and reconciles; it submits nothing."""
    from src import l5_live_authorization as LX
    d, base0 = live_doc()
    d.pop("last_open_utc")
    with pytest.raises(LX.Refused) as e:
        _claim(monkeypatch, tmp_path, live_slug(base0), d=d)
    assert e.value.reason == "SUBMISSION_WINDOW_NOT_AUTHORIZED"
    assert e.value.detail["why"] == "CAMPAIGN_OPENING_BOUND_ABSENT", \
        e.value.detail


def test_a_STALE_document_refuses_rather_than_reading_as_absent(
        monkeypatch, tmp_path):
    """Present and not valid is a refusal, never silence: otherwise an
    exhausted campaign quietly becomes a fresh one-window authorization at
    the one boundary before the wire."""
    from src import l5_live_authorization as LX
    stale, base0 = live_doc(issued_utc=(
        dt.datetime.now(dt.timezone.utc)
        - dt.timedelta(hours=9)).isoformat())
    with pytest.raises(LX.Refused) as e:
        _claim(monkeypatch, tmp_path, live_slug(base0), d=stale)
    assert e.value.reason == "SUBMISSION_WINDOW_NOT_AUTHORIZED"
    assert e.value.detail["why"] == "approval_stale", e.value.detail


def test_NO_approval_document_leaves_the_submission_path_alone(monkeypatch,
                                                               tmp_path):
    """Absent says nothing, exactly as authorize_window treats it: the
    pre-campaign single-window handshake is not replaced by this layer."""
    from src import l5_live_authorization as LX
    monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    monkeypatch.setenv("L5_EXPERIMENT_ID", "opening-bound-test")
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path))
    try:
        LX.claim_submission(slug("0800"), {"probe": True},
                            path=str(tmp_path / "ledger.json"))
    except LX.Refused as exc:
        assert "SUBMISSION_WINDOW_NOT_AUTHORIZED" not in str(exc), exc


# ===== EVERY market-keyed pre-wire claim is guarded, not just one

def test_claim_accumulation_leg_refuses_an_unauthorized_window(monkeypatch,
                                                               tmp_path):
    """THE BYPASS THIS CLOSES. The first fix guarded `claim_submission`
    alone; the accumulation branch -- which the execution path actually
    takes and the runner calls three times over -- went straight past it.
    A bound enforced on one of three doors is not a bound."""
    from src import l5_live_authorization as LX
    d, base0 = live_doc()
    beyond = live_slug(base0 + dt.timedelta(minutes=60))
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", json.dumps(d))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "opening-bound-test")
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path))
    with pytest.raises(LX.Refused) as e:
        LX.claim_accumulation_leg(beyond, 0, "UP", {"probe": True},
                                  path=str(tmp_path / "ledger.json"))
    assert e.value.reason == "SUBMISSION_WINDOW_NOT_AUTHORIZED"
    assert e.value.detail["why"] == "WINDOW_AFTER_LAST_AUTHORIZED_OPEN", \
        e.value.detail


def test_claim_accumulation_leg_allows_an_authorized_window(monkeypatch,
                                                            tmp_path):
    from src import l5_live_authorization as LX
    d, base0 = live_doc()
    inside = live_slug(base0 + dt.timedelta(minutes=45))
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", json.dumps(d))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "opening-bound-test")
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path))
    try:
        LX.claim_accumulation_leg(inside, 0, "UP", {"probe": True},
                                  path=str(tmp_path / "ledger.json"))
    except LX.Refused as exc:
        assert exc.reason != "SUBMISSION_WINDOW_NOT_AUTHORIZED", exc.detail


def test_EVERY_market_keyed_preWire_claim_calls_the_shared_guard():
    """A STRUCTURAL PIN, so a fourth door cannot be added unguarded. Every
    claim function taking a `market` must call the one shared guard; the
    check itself lives once, in l5_campaign_approval.opening_refusal."""
    import inspect
    from src import l5_live_authorization as LX
    guarded, unguarded = [], []
    for name in dir(LX):
        if not name.startswith("claim_"):
            continue
        fn = getattr(LX, name)
        if not callable(fn) or not hasattr(fn, "__code__"):
            continue
        if "market" not in inspect.signature(fn).parameters:
            continue
        src = inspect.getsource(fn)
        (guarded if "refuse_unauthorized_window(" in src
         else unguarded).append(name)
    assert unguarded == [], (
        "market-keyed pre-wire claims with no window guard: %s" % unguarded)
    # the third is the BTC 5-minute claim (owner decision, 2026-11-11):
    # the same guard, by name
    assert sorted(guarded) == ["claim_accumulation_leg",
                               "claim_btc5m_submission",
                               "claim_submission"], guarded
