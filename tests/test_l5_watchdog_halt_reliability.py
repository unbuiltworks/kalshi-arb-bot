"""Watchdog halt-record reliability: the five properties the owner required.

API NOTE, learned the hard way: the gate verdict is `HaltGate.state()`
(there is no `permitted()`), and `HaltAsserter.assert_halt` is keyword-only
and REQUIRES evidence -- a halt with no evidence cannot be asserted at all.

ROOT CAUSE THIS FILE GUARDS. On 2026-11-05 every watchdog sweep reported
`read_error: "HTTPError"` with `halted: null`. The sweep's venue read
(`self.t.open_orders()`) was failing, so by design it did NOT heartbeat, the
halt record aged past HaltGate's 900s bound, the executor refused at
HALT_GATE with `halt_record_stale`, and the campaign could not be activated.

That behaviour is CORRECT and is not changed here. What was wrong is that
`read_error` recorded only the exception TYPE, so nobody could tell a 401
from a 429 from a 400 -- the exact gap `http_error_detail` was written for on
the cancel path, and the one call site that never used it.

The five demonstrations:
  1. fresh halt records are published and readable on a clean sweep
  2. a missing, stale or unreadable record still blocks submissions
  3. an asserted remote HALT prevents trading
  4. recovery does not silently clear an asserted HALT
  5. executor and watchdog agree on the authoritative halt state
"""
import datetime as dt
import json
import urllib.error

import pytest

from src import l5_halt_gate as HG
from src import l5_watchdog as W


def _utc():
    return dt.datetime.now(dt.timezone.utc)


class Blind:
    """A venue read that fails the way production is failing."""

    def __init__(self, exc=None):
        self.exc = exc or urllib.error.HTTPError(
            "https://venue/orders", 400, "Bad Request", {},
            None)

    def open_orders(self):
        raise self.exc


class Clean:
    def open_orders(self):
        return []


@pytest.fixture()
def store(tmp_path):
    return HG.FileStore(str(tmp_path))


def wd(store, transport, *, cancel=False):
    return W.Watchdog(transport, W.asserter_for(store, who="test"),
                      max_order_age_s=300.0, cancel_enabled=cancel,
                      oracle=None)


# ============================ 1. fresh records are published and readable

def test_a_clean_sweep_publishes_a_fresh_readable_no_halt(store):
    out = wd(store, Clean()).sweep()
    assert out["read_error"] is None
    assert out.get("heartbeat") is not None, out
    gate = HG.HaltGate(store)
    v = gate.state()
    assert v["permitted"] is True, v
    assert v["why"] is None
    assert v["age_s"] < 5.0


def test_repeated_clean_sweeps_keep_it_fresh(store):
    g = HG.HaltGate(store)
    for _ in range(3):
        wd(store, Clean()).sweep()
        assert g.state()["permitted"] is True


def test_the_published_record_is_parseable_json_saying_halt_false(store):
    wd(store, Clean()).sweep()
    raw = store.get(HG.HALT_KEY)
    rec = json.loads(raw)
    assert rec["halt"] is False
    assert dt.datetime.fromisoformat(str(rec["t"])).tzinfo is not None


# ================ 2. missing / stale / unreadable still blocks submissions

def test_a_missing_record_blocks(store):
    v = HG.HaltGate(store).state()
    assert v["permitted"] is False
    assert v["why"] in ("halt_state_unavailable", "halt_asserted")


def test_a_stale_record_blocks(store):
    wd(store, Clean()).sweep()
    gate = HG.HaltGate(store, max_age_s=0.0)      # everything is stale
    v = gate.state()
    assert v["permitted"] is False
    assert v["why"] == "halt_record_stale", v


def test_an_unparseable_time_blocks(store):
    store.put(HG.HALT_KEY, json.dumps({"halt": False, "t": "not-a-time"}
                                      ).encode())
    v = HG.HaltGate(store).state()
    assert v["permitted"] is False
    assert v["why"] in ("halt_record_unparseable_time",
                        "halt_record_naive_time")


def test_a_naive_timestamp_blocks(store):
    store.put(HG.HALT_KEY, json.dumps(
        {"halt": False, "t": "2026-11-05T12:00:00"}).encode())
    v = HG.HaltGate(store).state()
    assert v["permitted"] is False
    assert v["why"] == "halt_record_naive_time"


def test_a_future_timestamp_blocks(store):
    t = (_utc() + dt.timedelta(hours=1)).isoformat()
    store.put(HG.HALT_KEY, json.dumps({"halt": False, "t": t}).encode())
    v = HG.HaltGate(store).state()
    assert v["permitted"] is False
    assert v["why"] == "halt_record_in_the_future"


@pytest.mark.parametrize("halt", [True, "false", "no", 0, 1, None, {}])
def test_anything_but_explicit_false_is_a_halt(store, halt):
    store.put(HG.HALT_KEY, json.dumps(
        {"halt": halt, "t": _utc().isoformat()}).encode())
    v = HG.HaltGate(store).state()
    assert v["permitted"] is False, halt


# ============ THE PRODUCTION FAILURE: a blind sweep must not attest

def test_a_blind_sweep_does_not_heartbeat(store):
    """This is what kept the record stale, and it must stay true."""
    out = wd(store, Blind()).sweep()
    assert out["read_error"] == "HTTPError"
    assert out.get("heartbeat") is None
    assert HG.HaltGate(store).state()["permitted"] is False


def test_a_blind_sweep_does_not_latch_a_halt_either(store):
    """A transient network error must not halt the system permanently."""
    out = wd(store, Blind()).sweep()
    assert out["halted"] is None


def test_going_blind_stops_the_executor_by_omission(store):
    wd(store, Clean()).sweep()                       # fresh
    assert HG.HaltGate(store).state()["permitted"] is True
    wd(store, Blind()).sweep()                       # blind: no new stamp
    stale = HG.HaltGate(store, max_age_s=0.0).state()
    assert stale["permitted"] is False
    assert stale["why"] == "halt_record_stale"


# ---- the repair itself: the error is now diagnosable

def test_the_read_error_now_carries_the_http_status(store):
    """`read_error: "HTTPError"` alone could not distinguish 401 from 429."""
    out = wd(store, Blind()).sweep()
    d = out["read_error_detail"]
    assert d["error"] == "HTTPError"
    assert d["status"] == 400


@pytest.mark.parametrize("code", [400, 401, 403, 429, 500, 503])
def test_every_status_is_reported(store, code):
    exc = urllib.error.HTTPError("https://venue/o", code, "x", {}, None)
    out = wd(store, Blind(exc)).sweep()
    assert out["read_error_detail"]["status"] == code


def test_read_error_keeps_its_old_shape(store):
    """ADDITIVE. Anything reading `read_error` must not change."""
    out = wd(store, Blind()).sweep()
    assert out["read_error"] == "HTTPError"
    assert isinstance(out["read_error"], str)


def test_a_non_http_failure_still_reports_and_still_blocks(store):
    class Boom:
        def open_orders(self):
            raise RuntimeError("socket died")
    out = wd(store, Boom()).sweep()
    assert out["read_error"] == "RuntimeError"
    assert out.get("heartbeat") is None
    assert HG.HaltGate(store).state()["permitted"] is False


def test_the_detail_never_replaces_the_refusal(store):
    """A diagnostic must never become a permission."""
    out = wd(store, Blind()).sweep()
    assert "read_error_detail" in out
    assert out.get("heartbeat") is None
    assert out["halted"] is None


def test_a_detail_that_raises_does_not_break_the_sweep(store, monkeypatch):
    monkeypatch.setattr(W, "http_error_detail",
                        lambda e: (_ for _ in ()).throw(ValueError("x")))
    out = wd(store, Blind()).sweep()
    assert out["read_error"] == "HTTPError"
    assert out["read_error_detail"] == {"error": "DETAIL_UNAVAILABLE"}
    assert out.get("heartbeat") is None


def test_secrets_are_redacted_from_the_detail():
    class E(urllib.error.HTTPError):
        def __init__(self):
            pass
        code = 400
        def read(self):
            return b'{"m":"bad","authorization: Bearer abc123SECRET":1}'
    d = W.http_error_detail(E())
    assert "abc123SECRET" not in json.dumps(d), d


# ============ 3 & 4. an asserted HALT prevents trading and is not cleared

def test_an_asserted_halt_blocks(store):
    W.asserter_for(store, who="test").assert_halt(
        reason="owner stop", evidence={"src": "test"})
    v = HG.HaltGate(store).state()
    assert v["permitted"] is False
    assert v["why"] == "halt_asserted"


def test_a_clean_sweep_cannot_clear_a_latched_halt(store):
    """RECOVERY MUST NOT SILENTLY CLEAR.

    A latched halt diverts the sweep down the EMERGENCY CANCEL path, which
    returns before the heartbeat branch is reached -- so neither `heartbeat`
    nor `heartbeat_refused` appears, and asserting on either would be testing
    a mechanism that does not run here. The invariant that matters is the
    one asserted below: after a full clean sweep the latch is still asserted
    and submissions are still refused.
    """
    W.asserter_for(store, who="test").assert_halt(
        reason="owner stop", evidence={"src": "test"})
    out = wd(store, Clean()).sweep()
    assert out["read_error"] is None
    assert out.get("heartbeat") is None
    # the watchdog RECOGNISES the latch rather than ignoring it
    assert out.get("emergency_cancel") is True
    assert str(out.get("emergency_reason", "")).startswith("HALT_LATCHED")
    v = HG.HaltGate(store).state()
    assert v["permitted"] is False
    assert v["why"] == "halt_asserted"
    assert HG.HaltGate(store).submissions_permitted() is False


def test_a_latched_halt_makes_every_resting_order_a_cancel_target(store):
    """Independent cancellation is preserved, not weakened."""
    class OneOrder:
        def open_orders(self):
            return [{"order_id": "v-1", "client_order_id": "l5f-1",
                     "created_time": "2026-11-05T12:00:00+00:00"}]
    W.asserter_for(store, who="test").assert_halt(
        reason="owner stop", evidence={"src": "test"})
    out = wd(store, OneOrder()).sweep()
    assert out.get("emergency_cancel") is True
    assert str(out.get("emergency_reason", "")).startswith("HALT_LATCHED")
    assert HG.HaltGate(store).state()["why"] == "halt_asserted"


def test_many_clean_sweeps_still_cannot_clear_it(store):
    W.asserter_for(store, who="test").assert_halt(
        reason="owner stop", evidence={"src": "test"})
    for _ in range(5):
        wd(store, Clean()).sweep()
    assert HG.HaltGate(store).state()["why"] == "halt_asserted"


def test_the_asserter_has_no_clear_method(store):
    a = W.asserter_for(store, who="test")
    for name in ("clear", "clear_halt", "unhalt", "reset", "release"):
        assert not hasattr(a, name), name


def test_heartbeat_no_halt_raises_over_a_latch(store):
    a = W.asserter_for(store, who="test")
    a.assert_halt(reason="owner stop", evidence={"src": "test"})
    with pytest.raises(PermissionError):
        a.heartbeat_no_halt()


# ============ 5. executor and watchdog agree on the authoritative state

def test_both_read_the_same_key_from_the_same_store(store):
    wd(store, Clean()).sweep()
    assert store.get(HG.HALT_KEY) is not None
    assert HG.HaltGate(store).state()["permitted"] is True


def test_the_executors_gate_sees_a_watchdog_halt(store):
    """The executor must not need to be told; it reads the latch."""
    W.asserter_for(store, who="test").assert_halt(
        reason="watchdog saw an orphan", evidence={"src": "test"})
    g = HG.HaltGate(store)
    assert g.submissions_permitted() is False


def test_the_executors_gate_sees_a_watchdog_heartbeat(store):
    wd(store, Clean()).sweep()
    assert HG.HaltGate(store).submissions_permitted() is True


def test_the_digest_identifies_the_record_both_sides_read(store):
    wd(store, Clean()).sweep()
    g = HG.HaltGate(store)
    g.state()
    assert g.last_sha256
    assert len(g.last_sha256) == 64
