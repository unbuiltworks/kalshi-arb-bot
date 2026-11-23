"""Runtime campaign authorization in the engine.

Owner 2026-11-23: "Expired, invalid, missing or revoked authorization must
prevent new exposure. Cancellation and safe reconciliation must remain
available." "Halt marker: Takes absolute precedence over any campaign
authorization. A new campaign must never automatically clear an emergency
halt." "If authorization cannot be refreshed successfully, block new
exposure and cancel opening orders." "The same valid campaign should be
resumable after a normal process restart, preserving its original expiry,
cumulative spend, inventory and losses. It must never restart with a fresh
budget or resume after an emergency halt."
"""
import datetime as dt
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_campaign_auth as CA
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

SECRET = "campaign-secret"
SERVICE = "pmus-spring-l5-probe"
BUILD = "e6e8b4e60a2c5c9c4732d001035aaa718949f226"
ATP = "aec-atp-a-b-2026-11-25"
NBA = "aec-nba-a-b-2026-11-25"
NHL = "aec-nhl-a-b-2026-11-25"


class Reader:
    def __init__(self):
        self.snap = {"raw": None, "age_s": 1.0, "err": None, "missing": True}

    def snapshot(self):
        return dict(self.snap)

    def publish(self, raw):
        self.snap = {"raw": raw, "age_s": 1.0, "err": None,
                     "missing": raw is None}

    def fail(self):
        self.snap = {"raw": None, "age_s": 1.0, "err": "URLError",
                     "missing": False}


def make(**kw):
    now = dt.datetime.now(dt.timezone.utc)
    d = {"campaign_id": "c-1", "service": SERVICE, "build": BUILD[:9],
         "not_before": (now - dt.timedelta(minutes=5)).isoformat(),
         "expires": (now + dt.timedelta(hours=6)).isoformat(),
         "competitions": ["atp"], "max_markets": 3, "order_qty": 1,
         "campaign_spend_usd": "16", "campaign_unpaired_usd": "6",
         "per_market_spend_usd": "8", "per_market_unpaired_usd": "3",
         "slate": "live", "issued_utc": (now - dt.timedelta(
             minutes=6)).isoformat()}
    secret = kw.pop("_secret", SECRET)
    d.update(kw)
    d["hmac"] = CA.sign(d, secret)
    return json.dumps(d).encode()


@pytest.fixture
def rt(tmp_path, monkeypatch):
    """A bot deployed with the `runtime` lock value and these ceilings:
    4 markets, 2 contracts, $16/$6, $8/$3, ATP+NBA+NHL permitted."""
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("50"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "runtime")
    monkeypatch.setenv(M.CAMPAIGN_AUTH_SECRET_ENV, SECRET)
    monkeypatch.setenv(M.CAMPAIGN_AUTH_SERVICE_ENV, SERVICE)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", BUILD)
    # deployed values = ceilings; every one is restored after the test
    for mod, name, val in (
            (M, "MAX_MARKETS", 4), (M, "STRICT_SPORTS", True),
            (M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"atp", "nba", "nhl"})),
            (M, "SLATE", "live"), (M, "RN1_PREGAME_QTY", None),
            (M, "RN1_LIVE_QTY", None), (M, "WINDOW_MAX_S", 43200.0),
            (R, "QTY", D("2")), (R, "TOTAL_SPEND_USD", D("16")),
            (R, "TOTAL_UNMATCHED_USD", D("6")), (R, "PER_MARKET_USD", D("8")),
            (R, "RESIDUAL_CAP_USD", D("3"))):
        monkeypatch.setattr(mod, name, val)
    monkeypatch.setattr(M, "_CEIL", {})
    monkeypatch.setattr(M, "_RUNTIME", {"at": None, "decision": None})
    halt = {"v": None}
    monkeypatch.setattr(M.Engine, "shared_halt", lambda self: halt["v"])
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._camp_reader = rd = Reader()
    return {"e": e, "v": v, "rd": rd, "halt": halt, "tmp": tmp_path}


def open_orders(e):
    return sorted((s, o["side"], str(o["qty"]), o.get("stage"))
                  for s, mk in e.b.d["markets"].items()
                  for o in mk["orders"].values() if o.get("open"))


def step(rt, slug=ATP):
    e = rt["e"]
    e.refresh_campaign_auth(force=True)
    e.enforce_lock()
    e.b.m(slug)
    e.step_market(slug)
    return open_orders(e)


# ------------------------------------------------------------ fail closed
def test_no_file_means_locked(rt):
    assert step(rt) == []
    st = M.lock_state()
    assert st["new"] and st["all"]
    assert st["why"] == "runtime:AUTH_FILE_ABSENT"


def test_never_evaluated_means_locked(rt):
    assert M.lock_state()["why"] == "runtime:AUTH_NOT_EVALUATED"
    assert M.submission_locked() and M.fully_locked()


def test_no_reader_means_locked(rt):
    rt["e"]._camp_reader = None
    assert step(rt) == []
    assert M.lock_state()["why"] == "runtime:AUTH_UNREACHABLE"


@pytest.mark.parametrize("kw,reason", [
    ({"_secret": "wrong"}, "AUTH_SIGNATURE_INVALID"),
    ({"service": "other"}, "AUTH_WRONG_SERVICE"),
    ({"build": "0ebb4a14a"}, "AUTH_WRONG_BUILD"),
    ({"max_markets": 5}, "AUTH_ABOVE_CEILING"),
    ({"order_qty": 3}, "AUTH_ABOVE_CEILING"),
    ({"campaign_unpaired_usd": "6.50"}, "AUTH_ABOVE_CEILING"),
    ({"competitions": ["atp", "cricket"]}, "AUTH_ABOVE_CEILING"),
    ({"account_limit_usd": "10"}, "AUTH_UNKNOWN_FIELD"),
])
def test_an_invalid_file_places_nothing(rt, kw, reason):
    rt["rd"].publish(make(**kw))
    assert step(rt) == []
    assert M.lock_state()["why"] == "runtime:" + reason
    # and the deployed values are untouched
    assert M.MAX_MARKETS == 4 and R.QTY == D("2")


def test_a_file_not_yet_started_places_nothing(rt):
    now = dt.datetime.now(dt.timezone.utc)
    rt["rd"].publish(make(not_before=(now + dt.timedelta(
        minutes=10)).isoformat()))
    assert step(rt) == []
    assert M.lock_state()["why"] == "runtime:AUTH_NOT_STARTED"


# -------------------------------------------------------------- permitted
def test_a_valid_file_opens_the_campaign_with_its_own_values(rt):
    rt["rd"].publish(make())
    got = step(rt)
    assert [(s, q) for s, _sd, q, _st in got] == [(ATP, "1"), (ATP, "1")]
    assert M.lock_state()["why"] is None
    assert M.MAX_MARKETS == 3 and R.QTY == D("1")
    assert M.OPEN_GATE_ALLOW_SPORTS == frozenset({"atp"})
    assert M.slate() == "live"
    st = rt["e"].b.d["campaign_auth"]
    assert st["active"]["id"] == "c-1"
    assert rt["e"].since == st["active"]["start"]


def test_the_campaigns_scope_is_strict(rt):
    """NBA is inside the deployed ceiling but not in this campaign."""
    rt["rd"].publish(make())
    assert step(rt, NBA) == []
    n = len(rt["v"].orders)
    rt["e"].place(NBA, "YES", D("0.47"), D("1"), "rn1_open")
    assert len(rt["v"].orders) == n


def test_values_at_the_ceiling_are_used_as_given(rt):
    rt["rd"].publish(make(order_qty=2, max_markets=4,
                          competitions=["atp", "nba", "nhl"]))
    got = step(rt)
    assert {q for _s, _sd, q, _st in got} == {"2"}
    assert M.MAX_MARKETS == 4


def test_the_plain_lock_overrides_any_file(rt, monkeypatch):
    rt["rd"].publish(make())
    assert len(step(rt)) == 2
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []
    rt["e"].step_market(ATP)
    assert open_orders(rt["e"]) == []


# ------------------------------------------------------- no grace, expiry
def test_a_failed_refresh_cancels_what_rests_at_once(rt):
    f = make()
    rt["rd"].publish(f)
    assert len(step(rt)) == 2
    rt["rd"].fail()
    rt["e"].refresh_campaign_auth(force=True)
    assert M.lock_state()["why"] == "runtime:AUTH_UNREACHABLE"
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []
    # the campaign is not ended by an outage: it comes back when the file
    # can be read again (the SAME file), with its original window
    rt["rd"].publish(f)
    assert len(step(rt)) == 2


def test_a_stale_read_blocks(rt):
    rt["rd"].publish(make())
    assert len(step(rt)) == 2
    rt["rd"].snap["age_s"] = M.CAMPAIGN_AUTH_MAX_AGE_S + 1
    rt["e"].refresh_campaign_auth(force=True)
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []
    assert M.lock_state()["why"] == "runtime:AUTH_SNAPSHOT_STALE"


def test_a_loop_that_stops_evaluating_stops_permitting(rt):
    rt["rd"].publish(make())
    assert len(step(rt)) == 2
    M._RUNTIME["at"] = time.monotonic() - M.CAMPAIGN_DECISION_MAX_AGE_S - 1
    assert M.lock_state()["why"] == "runtime:AUTH_DECISION_STALE"
    assert M.submission_locked("two_sided_complete")


def test_expiry_stops_every_placement_and_cancels_orders(rt):
    now = dt.datetime.now(dt.timezone.utc)
    f = make(expires=(now + dt.timedelta(seconds=2)).isoformat(),
             not_before=(now - dt.timedelta(minutes=5)).isoformat())
    rt["rd"].publish(f)
    mk = rt["e"].b.m(ATP)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.44")
    got = step(rt)
    assert ("two_sided_complete" in {st for *_x, st in got})
    time.sleep(2.2)
    rt["e"].refresh_campaign_auth(force=True)
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []            # completion included
    assert M.submission_locked("two_sided_complete")
    st = rt["e"].b.d["campaign_auth"]
    assert st["active"] is None and st["ended"]["c-1"]["why"] == "expired"
    assert mk["pos"]["YES"] == D("1")            # inventory left to settle


def test_an_expired_campaign_cannot_be_reopened_by_a_new_file_same_id(rt):
    now = dt.datetime.now(dt.timezone.utc)
    rt["rd"].publish(make(expires=(now + dt.timedelta(
        seconds=1)).isoformat()))
    step(rt)
    time.sleep(1.2)
    rt["e"].refresh_campaign_auth(force=True)
    rt["rd"].publish(make())                     # same id, six more hours
    assert step(rt) == []
    assert M.lock_state()["why"] == "runtime:AUTH_CAMPAIGN_ENDED"


# ------------------------------------------------------------- revocation
def test_removing_the_file_revokes_the_campaign_for_good(rt):
    f = make()
    rt["rd"].publish(f)
    assert len(step(rt)) == 2
    rt["rd"].publish(None)
    rt["e"].refresh_campaign_auth(force=True)
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []
    assert rt["e"].b.d["campaign_auth"]["ended"]["c-1"]["why"] == \
        "revoked:file_removed"
    rt["rd"].publish(f)                          # put back: stays ended
    assert step(rt) == []


def test_a_new_campaign_must_revoke_the_one_in_force(rt):
    rt["rd"].publish(make())
    assert len(step(rt)) == 2
    rt["rd"].publish(make(campaign_id="c-2"))
    rt["e"].refresh_campaign_auth(force=True)
    assert M.lock_state()["why"] == "runtime:AUTH_ANOTHER_CAMPAIGN_ACTIVE"
    rt["rd"].publish(make(campaign_id="c-2", revokes="c-1", max_markets=2))
    rt["e"].refresh_campaign_auth(force=True)
    st = rt["e"].b.d["campaign_auth"]
    assert st["active"]["id"] == "c-2"
    assert st["ended"]["c-1"]["why"] == "revoked_by:c-2"
    assert M.MAX_MARKETS == 2


def test_changing_a_running_campaigns_file_under_the_same_id_stops_it(rt):
    rt["rd"].publish(make())
    assert len(step(rt)) == 2
    rt["rd"].publish(make(order_qty=2))
    rt["e"].refresh_campaign_auth(force=True)
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []
    assert M.lock_state()["why"] == "runtime:AUTH_CAMPAIGN_CONTENT_CHANGED"
    assert R.QTY == D("2")                       # back to the deployed value


# --------------------------------------------------------- halt precedence
def test_a_latched_halt_beats_a_valid_campaign_and_ends_it(rt):
    f = make()
    rt["rd"].publish(f)
    assert len(step(rt)) == 2
    rt["halt"]["v"] = "HALT"
    rt["e"].refresh_campaign_auth(force=True)
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []
    assert M.lock_state()["why"] == "runtime:EMERGENCY_HALT"
    assert rt["e"].b.d["campaign_auth"]["ended"]["c-1"]["why"] == \
        "emergency_halt"
    # the owner resets the halt: the old campaign does NOT resume
    rt["halt"]["v"] = None
    assert step(rt) == []
    assert M.lock_state()["why"] == "runtime:AUTH_CAMPAIGN_ENDED"


def test_a_campaign_published_during_a_halt_never_starts(rt):
    rt["halt"]["v"] = "HALT"
    rt["rd"].publish(make())
    assert step(rt) == []
    rt["halt"]["v"] = None
    assert step(rt) == []
    assert M.lock_state()["why"] == "runtime:AUTH_CAMPAIGN_ENDED"
    # a NEW campaign after the reset is a new decision by the operator
    rt["rd"].publish(make(campaign_id="c-2"))
    assert len(step(rt)) == 2


def test_an_unreadable_halt_blocks_without_ending_the_campaign(rt):
    rt["rd"].publish(make())
    assert len(step(rt)) == 2
    rt["halt"]["v"] = "UNAVAILABLE"
    rt["e"].refresh_campaign_auth(force=True)
    rt["e"].enforce_lock()
    assert open_orders(rt["e"]) == []
    assert M.lock_state()["why"] == "runtime:HALT_STATE_UNAVAILABLE"
    rt["halt"]["v"] = None
    assert len(step(rt)) == 2


# ----------------------------------------------------------- restart-safe
def test_a_restart_resumes_the_same_campaign_with_its_spend(rt):
    f = make()
    rt["rd"].publish(f)
    e = rt["e"]
    assert len(step(rt)) == 2
    # one bid fills: 1 YES for 0.47-ish; that money is this campaign's
    for o in rt["v"].orders.values():
        if o["side"] == "YES":
            o["filled"], o["open"] = o["qty"], False
    e.step_market(ATP)
    spent = e.rn1_campaign_exposure()["spend"]
    start = e.b.d["campaign_auth"]["active"]["start"]
    end = e.b.d["campaign_auth"]["active"]["end"]
    assert spent > 0
    e.b.save()
    # new process, same durable book, same file
    M._RUNTIME.update(at=None, decision=None)
    e2 = M.Engine(rt["v"], M.Book(rt["tmp"] / "s.json"))
    e2._camp_reader = rt["rd"]
    assert M.lock_state()["why"] == "runtime:AUTH_NOT_EVALUATED"
    d = e2.refresh_campaign_auth(force=True)
    assert d["permit"] is True
    act = e2.b.d["campaign_auth"]["active"]
    assert act["id"] == "c-1" and act["start"] == start and act["end"] == end
    assert e2.since == start
    assert e2.rn1_campaign_exposure()["spend"] >= spent     # no fresh budget
    assert D(e2.b.d["markets"][ATP]["pos"]["YES"]) == D("1")


def test_a_restart_does_not_revive_an_ended_campaign(rt):
    f = make()
    rt["rd"].publish(f)
    step(rt)
    rt["halt"]["v"] = "HALT"
    rt["e"].refresh_campaign_auth(force=True)
    rt["e"].b.save()
    rt["halt"]["v"] = None
    M._RUNTIME.update(at=None, decision=None)
    e2 = M.Engine(rt["v"], M.Book(rt["tmp"] / "s.json"))
    e2._camp_reader = rt["rd"]
    assert e2.refresh_campaign_auth(force=True)["permit"] is False
    assert M.lock_state()["why"] == "runtime:AUTH_CAMPAIGN_ENDED"


def test_a_restart_after_expiry_ends_it_even_if_the_file_is_unreachable(rt):
    now = dt.datetime.now(dt.timezone.utc)
    rt["rd"].publish(make(expires=(now + dt.timedelta(
        seconds=1)).isoformat()))
    step(rt)
    rt["e"].b.save()
    time.sleep(1.2)
    rt["rd"].fail()
    M._RUNTIME.update(at=None, decision=None)
    e2 = M.Engine(rt["v"], M.Book(rt["tmp"] / "s.json"))
    e2._camp_reader = rt["rd"]
    e2.refresh_campaign_auth(force=True)
    assert e2.b.d["campaign_auth"]["ended"]["c-1"]["why"] == "expired"


# ------------------------------------------------------ unchanged behaviour
def test_without_the_runtime_value_nothing_here_acts(rt, monkeypatch):
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    rt["rd"].publish(make())
    assert rt["e"].refresh_campaign_auth(force=True) is None
    assert M.lock_state()["why"] == "locked"
    assert M.MAX_MARKETS == 4 and R.QTY == D("2")


def test_hard_limits_are_not_reachable_from_a_file():
    for name in ("account", "loss", "halt", "daily", "pair_cap", "taker",
                 "depth", "audit"):
        assert not [f for f in CA.SIGNED_FIELDS if name in f], name


def test_status_block_names_the_reason(rt):
    assert step(rt) == []
    s = rt["e"].campaign_status()
    assert s["mode"] == "runtime" and s["permit"] is False
    assert s["reason"] == "AUTH_FILE_ABSENT"
    rt["rd"].publish(make())
    step(rt)
    s = rt["e"].campaign_status()
    assert s["permit"] is True and s["campaign_id"] == "c-1"
    assert s["in_force"]["max_markets"] == 3


def test_report_only_shows_the_verdict_and_permits_nothing(rt, monkeypatch):
    """A locked deployment can show what the file would decide."""
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    rt["rd"].publish(make())
    e = rt["e"]
    assert e.refresh_campaign_auth(force=True) is None
    s = e.campaign_status()
    assert s["mode"] == "report_only" and s["permit"] is True
    assert s["campaign_id"] == "c-1" and s["active_id"] is None
    assert M.lock_state()["why"] == "locked"
    assert M.MAX_MARKETS == 4 and R.QTY == D("2")        # nothing applied
    assert e.b.d["campaign_auth"]["active"] is None      # nothing recorded
    e.enforce_lock()
    e.b.m(ATP)
    e.step_market(ATP)
    assert open_orders(e) == []


def test_report_only_is_off_unless_asked_for():
    assert M.CAMPAIGN_AUTH_REPORT is False
