"""JOINT: owner's signed marker -> watchdog latch -> the sports bot's own
resting orders cancelled by exact id and re-read -> the sports bot refuses
to place. Sports engine and watchdog from the two trees as deployed, one
fake venue between them, the real halt record on disk between them.

What is real here: the marker signature check, the nonce record, the halt
record and gate, the watchdog's sweep / target selection / cancel / re-read,
the sports engine's placement path and its shared-halt read.
What is not: the venue (a fake), the HTTP hop that carries the halt record
to the sports bot (the gate reads the same directory), and the ownership
registry (an oracle that claims the sports bot's open orders; the registry
itself is tested in the watchdog's own suite).
"""
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_emergency_halt_marker as EHM
from src import l5_halt_gate as HG
from src import l5_watchdog as W
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

SLUG = "aec-x"
SECRET = "joint-test-secret"
SERVICE = "pmus-spring-l5-probe"


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


class VenueForWatchdog:
    """The watchdog's view of the SAME fake venue the sports bot trades on."""

    def __init__(self, fake, created):
        self.fake, self.created = fake, created
        self.cancelled = []

    def _row(self, oid, o):
        left = D(str(o["qty"])) - D(str(o["filled"]))
        return {"id": oid, "marketSlug": o["slug"],
                "state": "ORDER_STATE_OPEN" if o["open"]
                else "ORDER_STATE_CANCELED",
                "leavesQuantity": str(left if o["open"] else 0),
                "cumQuantity": str(o["filled"]),
                "createTime": stamp(self.created),
                "insertTime": stamp(self.created),
                "manualOrderIndicator": W.AUTOMATIC}

    def open_orders(self):
        return [self._row(i, o) for i, o in self.fake.orders.items()
                if o["open"]]

    def cancel(self, oid, market_slug=None):
        self.cancelled.append(oid)
        self.fake.orders[oid]["open"] = False
        return {"ok": True}

    def order(self, oid):
        return self._row(oid, self.fake.orders[oid])


class SportsClaims:
    """Ownership: exactly the orders open in the sports bot's own book."""

    def __init__(self, eng):
        self.eng = eng

    def claimed(self):
        out = {}
        for slug, mk in self.eng.b.d["markets"].items():
            for oid, o in mk["orders"].items():
                if o.get("open"):
                    out[oid] = {"venue_order_id": oid, "market": slug}
        return out


class Source:
    name = "EMERGENCY_HALT_MARKER"

    def __init__(self):
        self.body, self.down = None, False

    def fetch(self):
        if self.down or self.body is None:
            raise OSError("marker endpoint unavailable")
        return self.body


def marker(now, *, secret=SECRET, nonce="n-1", service=SERVICE,
           command="HALT"):
    doc = {"command": command, "issued_utc": now.isoformat(),
           "nonce": nonce, "service": service, "reason": "joint test"}
    doc["hmac"] = EHM.sign(doc, secret)
    return json.dumps(doc).encode()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    monkeypatch.setattr(M, "SHARED_HALT_TTL_S", 0.0)       # read every time
    latch_dir = tmp_path / "latch"
    latch_dir.mkdir()
    store = HG.FileStore(str(latch_dir))
    # the sports bot's gate reads the record the watchdog writes
    monkeypatch.setenv(M.SHARED_HALT_URL_ENV, "http://watchdog.test")
    monkeypatch.setattr(HG, "HttpHaltStore", lambda url, **k: store)
    fake = FakeVenue()
    fake.states = {}
    fake.bid, fake.ask = D("0.48"), D("0.50")
    eng = M.Engine(fake, M.Book(tmp_path / "s.json"))
    now = dt.datetime.now(dt.timezone.utc)
    wv = VenueForWatchdog(fake, created=now - dt.timedelta(seconds=5))
    wd = W.Watchdog(wv, HG.HaltAsserter(store, who="watchdog"),
                    oracle=SportsClaims(eng), max_order_age_s=300,
                    cancel_enabled=True)
    return {"eng": eng, "fake": fake, "wd": wd, "wv": wv, "store": store,
            "now": now, "src": Source()}


def open_ids(fake):
    return sorted(i for i, o in fake.orders.items() if o["open"])


def quote(rig):
    rig["eng"].b.m(SLUG)
    rig["eng"].step_market(SLUG)
    return open_ids(rig["fake"])


def check_marker(rig):
    return EHM.check(rig["src"], rig["wd"].asserter, rig["store"],
                     service=SERVICE, secret=SECRET, now=rig["now"])


def test_normal_operation_is_untouched_until_a_marker_appears(rig):
    rig["wd"].sweep(now=rig["now"])                  # attests "no halt"
    ids = quote(rig)
    assert len(ids) == 2                             # both sides resting
    out = rig["wd"].sweep(now=rig["now"])
    assert out["emergency_cancel"] is False and rig["wv"].cancelled == []
    assert rig["eng"].shared_halt() is None
    assert check_marker(rig)["reason"] == "MARKER_UNREACHABLE"
    assert open_ids(rig["fake"]) == ids


def test_signed_marker_cancels_the_sports_bots_orders_and_stops_it(rig):
    rig["wd"].sweep(now=rig["now"])
    ids = quote(rig)
    assert len(ids) == 2
    rig["src"].body = marker(rig["now"])
    m = check_marker(rig)
    assert m["acted"] and m["reason"] == "HALT_LATCHED_FROM_OWNER_MARKER"
    # the very next sweep cancels every resting order, 5 seconds old
    out = rig["wd"].sweep(now=rig["now"])
    assert out["emergency_cancel"] is True
    assert sorted(rig["wv"].cancelled) == ids
    assert sorted(r["venue_order_id"] for r in out["cancelled"]) == ids
    assert all(r["selected_by"] == "EMERGENCY_HALT_LATCHED"
               for r in out["cancelled"])
    assert out["still_open"] == [] and out["unverified"] == []
    assert open_ids(rig["fake"]) == []
    # the sports bot sees the latch and places nothing more
    assert rig["eng"].shared_halt() == "HALT"
    n = len(rig["fake"].orders)
    for _ in range(3):
        rig["eng"].step_market(SLUG)
    assert len(rig["fake"].orders) == n and open_ids(rig["fake"]) == []
    # a direct placement attempt is refused at the last gate too
    rig["eng"].place(SLUG, "YES", D("0.47"), D("2"), "rn1_open")
    assert len(rig["fake"].orders) == n


def test_the_halt_holds_if_the_sports_bot_is_dead(rig):
    """Nothing in the cancel needs the sports bot: latch and cancel with
    the engine never run again."""
    rig["wd"].sweep(now=rig["now"])
    ids = quote(rig)
    rig["src"].body = marker(rig["now"])
    check_marker(rig)
    out = rig["wd"].sweep(now=rig["now"])
    assert sorted(rig["wv"].cancelled) == ids
    assert out["emergency_cancel"] is True
    assert HG.HaltGate(rig["store"]).submissions_permitted() is False


def test_the_latch_survives_a_restart_of_both_and_cannot_be_unlatched(rig,
                                                                     tmp_path):
    rig["wd"].sweep(now=rig["now"])
    quote(rig)
    rig["src"].body = marker(rig["now"])
    check_marker(rig)
    rig["wd"].sweep(now=rig["now"])
    wd2 = W.Watchdog(rig["wv"], HG.HaltAsserter(rig["store"], who="wd2"),
                     oracle=SportsClaims(rig["eng"]), max_order_age_s=300,
                     cancel_enabled=True)
    wd2.sweep(now=rig["now"])
    assert HG.HaltGate(rig["store"]).submissions_permitted() is False
    eng2 = M.Engine(rig["fake"], M.Book(tmp_path / "s.json"))
    assert eng2.shared_halt() == "HALT"


def test_a_wrongly_signed_marker_does_nothing(rig):
    rig["wd"].sweep(now=rig["now"])
    ids = quote(rig)
    rig["src"].body = marker(rig["now"], secret="not-the-secret")
    m = check_marker(rig)
    assert not m["acted"] and m["reason"] == "MARKER_SIGNATURE_INVALID"
    out = rig["wd"].sweep(now=rig["now"])
    assert out["emergency_cancel"] is False and open_ids(rig["fake"]) == ids
    assert rig["eng"].shared_halt() is None


def test_a_marker_for_another_service_or_command_does_nothing(rig):
    rig["wd"].sweep(now=rig["now"])
    quote(rig)
    rig["src"].body = marker(rig["now"], service="l5-pmus-live")
    assert check_marker(rig)["reason"] == "MARKER_WRONG_SERVICE"
    rig["src"].body = marker(rig["now"], command="RESUME")
    assert check_marker(rig)["reason"] == "MARKER_COMMAND_NOT_HALT"
    assert rig["eng"].shared_halt() is None


def test_a_spent_marker_cannot_latch_again(rig):
    rig["wd"].sweep(now=rig["now"])
    rig["src"].body = marker(rig["now"])
    assert check_marker(rig)["acted"]
    again = check_marker(rig)
    assert not again["acted"] and again["reason"] == "MARKER_ALREADY_ACTED"


def test_an_old_marker_is_refused(rig):
    rig["wd"].sweep(now=rig["now"])
    rig["src"].body = marker(rig["now"] - dt.timedelta(hours=25))
    assert check_marker(rig)["reason"] == "MARKER_STALE"
    assert rig["eng"].shared_halt() is None


def test_with_the_marker_endpoint_down_the_watchdogs_own_trigger_still_acts(
        rig):
    """Owner 2026-11-23: the halt must not depend on the marker endpoint.
    An order the sports bot has stopped managing (older than the limit) is
    latched and cancelled by the watchdog's own rule, endpoint unreachable."""
    rig["wd"].sweep(now=rig["now"])
    ids = quote(rig)
    rig["src"].down = True
    assert check_marker(rig)["reason"] == "MARKER_UNREACHABLE"
    rig["wv"].created = rig["now"] - dt.timedelta(seconds=1200)
    out = rig["wd"].sweep(now=rig["now"])
    assert sorted(rig["wv"].cancelled) == ids
    assert rig["eng"].shared_halt() == "HALT"
    assert out["halted"] is not None
