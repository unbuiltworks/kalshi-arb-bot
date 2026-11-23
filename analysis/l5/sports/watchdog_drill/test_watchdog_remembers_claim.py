"""Unit tests for the watchdog's bounded memory of a dead executor's claim."""
import datetime as dt, json, sys, time
sys.path.insert(0, ".")
import pytest
from src import l5_watchdog as W

def doc(ids, age=0.0, readable=True):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age)
    return json.dumps({"readable": readable, "as_of_utc": t.isoformat(),
                       "orders": [{"venue_order_id": i, "market": "aec-x"} for i in ids]}).encode()

class Box:
    def __init__(self): self.body, self.dead = doc(["o1", "o2"]), False
    def __call__(self, url, timeout):
        if self.dead: raise ConnectionRefusedError("gone")
        return self.body

@pytest.fixture
def orc(monkeypatch):
    monkeypatch.delenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", raising=False)
    b = Box(); return W.OwnershipOracle("http://bot:8090", opener=b), b

def test_off_by_default_an_unreachable_registry_owns_nothing(orc):
    o, b = orc
    assert set(o.claimed()) == {"o1", "o2"}
    b.dead = True
    assert o.claimed() == {} and o.last_refusal.startswith("REGISTRY_UNREACHABLE")

def test_with_the_bound_set_a_dead_executors_last_claim_stands(orc, monkeypatch):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    assert set(o.claimed()) == {"o1", "o2"}
    b.dead = True
    got = o.claimed()
    assert set(got) == {"o1", "o2"} and got["o1"]["market"] == "aec-x"
    assert "USING_LAST_GOOD_CLAIM:2 ids" in o.last_refusal

def test_the_memory_expires(orc, monkeypatch):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    o.claimed(); b.dead = True
    o._last_good = (time.monotonic() - 901, o._last_good[1])
    assert o.claimed() == {}

def test_never_read_means_nothing_to_remember(orc, monkeypatch):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    b.dead = True
    assert o.claimed() == {}

@pytest.mark.parametrize("body", [doc(["o1"], age=3600), doc(["o1"], readable=False), b"{}"])
def test_a_registry_that_answers_is_never_overridden_by_memory(orc, monkeypatch, body):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    assert set(o.claimed()) == {"o1", "o2"}
    b.body = body                          # alive, but stale / unreadable
    assert o.claimed() == {}
    assert "USING_LAST_GOOD_CLAIM" not in (o.last_refusal or "")

def test_a_live_registry_that_now_claims_nothing_replaces_the_memory(orc, monkeypatch):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    o.claimed(); b.body = doc([])
    assert o.claimed() == {}
    b.dead = True
    assert o.claimed() == {}               # it had cancelled everything itself
