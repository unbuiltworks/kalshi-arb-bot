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


# ---------------------------------------------------------------------------
# LIVE 2026-11-17 21:57:04Z. The lane probe was given the sweep's `now`,
# captured before a venue read and a registry probe that together took
# ~6.1 s. The lane stamps `as_of_utc` as its document is built, so a
# correct, fresh, self-managing lane reported STALE:-6.1 on every sweep,
# its exemption never applied, and a 315 s sports order latched
# UNMANAGED_RESTING_ORDER for the whole account.
#
# These pin the REFERENCE TIME, not the tolerance. The +/-5 s band is
# deliberately left as it was.
# ---------------------------------------------------------------------------
class TestTheLaneProbeMeasuresAgainstTheClockAtTheRead:
    """The sweep's `now` is stale by its own elapsed time; the lane is not.

    Modelled exactly as production behaves: `W._utc` (what the sweep uses
    for `now`) returns a point `elapsed_s` in the PAST, while the lane
    stamps `as_of_utc` with the real clock as its document is built. With
    the sweep's `now` forwarded to the lane probe, age reads -elapsed_s
    and the lane is refused. Measuring at the read, age reads ~0.
    """

    def _slow_sweep(self, tmp_path, monkeypatch, elapsed_s,
                    lane_offset_s=0.0):
        import datetime as dt
        real = dt.datetime.now(dt.timezone.utc)

        #: A CLOCK THAT ADVANCES, because that is the whole point. The
        #: FIRST call is the sweep's `now`, taken before the venue read;
        #: by the time anything else asks, `elapsed_s` has passed. Pinning
        #: `_utc` to one stale value instead would model a broken clock,
        #: not a slow sweep, and would not distinguish the two code paths.
        calls = []

        def advancing_utc():
            calls.append(1)
            return real if len(calls) > 1 else (
                real - dt.timedelta(seconds=elapsed_s))
        monkeypatch.setattr(W, "_utc", advancing_utc)

        def stamping_lane(url, t):
            at = (dt.datetime.now(dt.timezone.utc)
                  + dt.timedelta(seconds=lane_offset_s))
            return json.dumps(doc(["SPORT1"], as_of=at.isoformat())).encode()

        v = Venue([open_row("SPORT1")])          # created 2026-10-12: overdue
        return mk(tmp_path, v, stamping_lane).sweep()

    def test_a_six_second_sweep_still_sees_a_fresh_lane(self, tmp_path,
                                                        monkeypatch):
        """The exact live shape: 6.1 s of sweep, lane stamped at the read."""
        out = self._slow_sweep(tmp_path, monkeypatch, 6.1)
        assert out["managed_lanes"] == ["http://sports:8090:OK:1"], \
            out["managed_lanes"]
        assert out["managed_lane_exempt"] == ["SPORT1"]
        assert out["overdue"] == []

    def test_and_therefore_does_not_latch_the_account(self, tmp_path,
                                                      monkeypatch):
        out = self._slow_sweep(tmp_path, monkeypatch, 6.1)
        assert out.get("halted") is None
        assert out["heartbeat"]["halt"] is False

    def test_the_outlier_sweep_of_seven_and_a_half_seconds_is_also_fine(
            self, tmp_path, monkeypatch):
        """The observed outlier was -7.451331."""
        out = self._slow_sweep(tmp_path, monkeypatch, 7.46)
        assert out["managed_lane_exempt"] == ["SPORT1"]

    def test_a_genuinely_old_lane_document_still_exempts_nothing(
            self, tmp_path, monkeypatch):
        """The 60 s staleness bound is untouched."""
        out = self._slow_sweep(tmp_path, monkeypatch, 6.1,
                               lane_offset_s=-120.0)
        assert out["managed_lane_exempt"] == []
        assert [o["venue_order_id"] for o in out["overdue"]] == ["SPORT1"]
        assert out["halted"]["reason"] == "UNMANAGED_RESTING_ORDER"

    def test_a_wildly_future_lane_document_still_exempts_nothing(
            self, tmp_path, monkeypatch):
        """A real clock fault is still refused; only the reference moved."""
        out = self._slow_sweep(tmp_path, monkeypatch, 0.0,
                               lane_offset_s=+600.0)
        assert out["managed_lane_exempt"] == []
        assert out["halted"]["reason"] == "UNMANAGED_RESTING_ORDER"

    def test_the_skew_band_is_not_widened_by_this_fix(self):
        from src import l5_managed_lanes as ML
        assert ML.MAX_SKEW_S == 5.0
        assert ML.MAX_AGE_S == 60.0

    def test_an_injected_now_is_still_honoured_for_determinism(self, tmp_path):
        v = Venue([open_row("SPORT1")])
        out = mk(tmp_path, v, lane(doc(["SPORT1"]))).sweep(now=NOW)
        assert out["managed_lane_exempt"] == ["SPORT1"]

    def test_an_injected_now_still_refuses_a_stale_document(self, tmp_path):
        v = Venue([open_row("SPORT1")])
        out = mk(tmp_path, v, lane(doc(["SPORT1"], as_of=OLD))).sweep(now=NOW)
        assert out["managed_lane_exempt"] == []
