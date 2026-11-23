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


# ---- how soon: a gone executor's young orders do not wait for the age rule
class Venue:
    name = "FAKE_VENUE"
    def __init__(s, ids, age_s):
        t = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_s)).isoformat()
        s.rows = {i: {"id": i, "marketSlug": "aec-x", "createTime": t,
                      "manualOrderIndicator": W.AUTOMATIC, "state": "ORDER_STATE_OPEN",
                      "leavesQuantity": "2", "cumQuantity": "0"} for i in ids}
        s.rows["other-desk"] = dict(s.rows[ids[0]], id="other-desk", marketSlug="aec-y")
        s.rows["mine-manual"] = dict(s.rows[ids[0]], id="mine-manual", manualOrderIndicator=W.MANUAL)
        s.cancels = []
    def open_orders(s): return [dict(r) for r in s.rows.values() if r["leavesQuantity"] != "0"]
    def order(s, i): return dict(s.rows[i])
    def cancel(s, i, slug):
        s.cancels.append(i); s.rows[i].update(state="ORDER_STATE_CANCELED", leavesQuantity="0")

def dog(o, v, tmp_path):
    from src import l5_halt_gate as HG
    return W.Watchdog(v, W.asserter_for(HG.FileStore(str(tmp_path))), max_order_age_s=300,
                      cancel_enabled=True, oracle=o)

def test_young_orders_of_a_gone_executor_are_cancelled_after_the_grace(orc, monkeypatch, tmp_path):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    monkeypatch.setenv("L5_WATCHDOG_DEAD_AFTER_S", "30")
    v = Venue(["o1", "o2"], age_s=20)                 # far younger than 300 s
    wd = dog(o, v, tmp_path)
    wd.sweep(); assert v.cancels == []                # executor alive
    b.dead = True
    wd.sweep(); assert v.cancels == []                # unreachable, inside the grace
    assert o.dead_ids() == set()
    o._unreachable_since = time.monotonic() - 31      # the grace has passed
    out = wd.sweep()
    assert sorted(v.cancels) == ["o1", "o2"]          # only its own two
    assert out["executor_unreachable_ids"] == 2
    assert {x["venue_order_id"] for x in out["overdue"]} == {"o1", "o2"}
    assert all(x.get("selected_by") == "EXECUTOR_UNREACHABLE" for x in out["overdue"])

def test_an_executor_that_answers_again_is_not_gone(orc, monkeypatch, tmp_path):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    v = Venue(["o1", "o2"], age_s=20)
    wd = dog(o, v, tmp_path)
    wd.sweep(); b.dead = True; wd.sweep()
    o._unreachable_since = time.monotonic() - 31
    b.dead = False                                    # it is back before the sweep
    wd.sweep()
    assert v.cancels == [] and o.dead_ids() == set()

def test_without_memory_nothing_is_selected_early(orc, tmp_path):
    o, b = orc
    v = Venue(["o1", "o2"], age_s=20)
    wd = dog(o, v, tmp_path)
    wd.sweep(); b.dead = True; wd.sweep()
    o._unreachable_since = time.monotonic() - 3600
    wd.sweep()
    assert v.cancels == [] and o.dead_ids() == set()


# ---- LIVE 2026-11-23 16:31Z: an order placed after the last sweep was unknown
class FakeClock:
    def __init__(s): s.t = 0.0
    def now(s): return s.t
    def sleep(s, d): s.t += d

def test_claims_are_refreshed_between_sweeps(orc, monkeypatch, tmp_path):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_CLAIM_POLL_S", "2")
    v = Venue(["o1", "o2"], age_s=20)
    wd = dog(o, v, tmp_path)
    wd.sweep()                                        # remembers o1, o2
    b.body = doc(["o9"])                              # the bot replaces its orders
    c = FakeClock()
    n = W.wait_refreshing_claims(wd, 20.0, sleep=c.sleep, monotonic=c.now)
    assert n == 9 and c.t == 20.0                     # every 2 s, not at the end
    assert set(o._last_good[1]) == {"o9"}             # the new order is known

def test_an_order_placed_just_before_death_is_cancelled(orc, monkeypatch, tmp_path):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    monkeypatch.setenv("L5_WATCHDOG_DEAD_AFTER_S", "30")
    monkeypatch.setenv("L5_WATCHDOG_CLAIM_POLL_S", "2")
    v = Venue(["o9"], age_s=10)
    wd = dog(o, v, tmp_path)
    b.body = doc(["o1"])
    wd.sweep()                                        # last sweep: only the OLD order
    b.body = doc(["o9"])                              # new order placed after the sweep
    c = FakeClock()
    W.wait_refreshing_claims(wd, 6.0, sleep=c.sleep, monotonic=c.now)
    b.dead = True                                     # then the bot dies
    W.wait_refreshing_claims(wd, 14.0, sleep=c.sleep, monotonic=c.now)
    wd.sweep(); assert v.cancels == []                # inside the grace
    o._unreachable_since = time.monotonic() - 31
    wd.sweep()
    assert v.cancels == ["o9"]                        # the order really resting

def test_without_the_refresh_that_order_is_never_known(orc, monkeypatch, tmp_path):
    o, b = orc
    monkeypatch.setenv("L5_WATCHDOG_REMEMBER_CLAIMS_S", "900")
    monkeypatch.setenv("L5_WATCHDOG_CLAIM_POLL_S", "0")
    v = Venue(["o9"], age_s=10)
    wd = dog(o, v, tmp_path)
    b.body = doc(["o1"]); wd.sweep()
    b.body = doc(["o9"])
    c = FakeClock()
    assert W.wait_refreshing_claims(wd, 20.0, sleep=c.sleep, monotonic=c.now) == 0
    b.dead = True; wd.sweep()
    o._unreachable_since = time.monotonic() - 31
    wd.sweep()
    assert v.cancels == []                            # what happened in production

def test_the_refresh_never_raises_and_touches_no_venue(orc, monkeypatch, tmp_path):
    o, b = orc
    v = Venue(["o1"], age_s=10)
    calls = []
    v.open_orders = lambda: calls.append("open") or []
    wd = dog(o, v, tmp_path)
    b.dead = True
    c = FakeClock()
    assert W.wait_refreshing_claims(wd, 10.0, sleep=c.sleep, monotonic=c.now) == 4
    assert calls == [] and v.cancels == []
