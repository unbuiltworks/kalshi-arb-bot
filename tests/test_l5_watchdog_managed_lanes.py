"""OWNER 2026-11-14 ("Both"): sports two-sided and BTC5m share the account.
A FRESH managed lane's long-resting maker orders are exempt from the age
latch; a stale/unreachable/non-managed lane exempts nothing."""
import json

from src import l5_halt_gate as HG
from src import l5_watchdog as W
from tests.test_l5_watchdog import NOW, Venue, ClaimsOpenOrders, open_row, gate

OLD = "2026-10-12T21:40:00.000000000Z"         # 20 min before NOW


def lane(doc):
    return lambda url, t: json.dumps(doc).encode()


def mk(tmp_path, venue, opener, lanes=("http://sports:8090",)):
    store = HG.FileStore(str(tmp_path))
    return W.Watchdog(venue, HG.HaltAsserter(store, who="t"),
                      max_order_age_s=300, oracle=ClaimsOpenOrders(venue),
                      managed_lanes=list(lanes), lane_opener=opener)


def doc(ids, as_of=NOW.isoformat(), managed=True):
    return {"readable": True, "manages_own_resting": managed, "as_of_utc": as_of,
            "orders": [{"venue_order_id": i} for i in ids]}


def test_fresh_lane_exempts_its_own_old_orders(tmp_path):
    v = Venue([open_row("SPORT1", created=OLD)])
    out = mk(tmp_path, v, lane(doc(["SPORT1"]))).sweep(now=NOW)
    assert out["overdue"] == [] and out["managed_lane_exempt"] == ["SPORT1"]
    assert gate(tmp_path).submissions_permitted() is True


def test_an_order_the_lane_does_not_name_still_latches(tmp_path):
    v = Venue([open_row("SPORT1", created=OLD), open_row("ORPHAN", created=OLD)])
    out = mk(tmp_path, v, lane(doc(["SPORT1"]))).sweep(now=NOW)
    assert [o["venue_order_id"] for o in out["overdue"]] == ["ORPHAN"]
    assert out["halted"]


def test_stale_lane_exempts_nothing(tmp_path):
    v = Venue([open_row("SPORT1", created=OLD)])
    out = mk(tmp_path, v, lane(doc(["SPORT1"], as_of="2026-10-12T21:50:00+00:00"))).sweep(now=NOW)
    assert out["overdue"] and out["halted"]


def test_unreachable_or_unmanaged_lane_exempts_nothing(tmp_path):
    def boom(url, t):
        raise OSError("down")
    v = Venue([open_row("SPORT1", created=OLD)])
    assert mk(tmp_path, v, boom).sweep(now=NOW)["overdue"]
    v2 = Venue([open_row("SPORT1", created=OLD)])
    assert mk(tmp_path / "b", v2, lane(doc(["SPORT1"], managed=False))).sweep(now=NOW)["overdue"]


def test_no_lanes_configured_is_unchanged(tmp_path):
    v = Venue([open_row("SPORT1", created=OLD)])
    store = HG.FileStore(str(tmp_path))
    w = W.Watchdog(v, HG.HaltAsserter(store, who="t"), max_order_age_s=300,
                   oracle=ClaimsOpenOrders(v))
    out = w.sweep(now=NOW)
    assert out["overdue"] and "managed_lane_exempt" not in out


def test_from_env_reads_lane_urls(monkeypatch, tmp_path):
    monkeypatch.setenv("L5_WATCHDOG_MANAGED_LANE_URLS", "http://a:1, http://b:2/")
    assert W._managed_lanes_from_env() == ["http://a:1", "http://b:2/"]
