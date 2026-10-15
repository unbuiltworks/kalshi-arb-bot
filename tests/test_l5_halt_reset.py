"""The one-time owner-gated HALT reset, and the credentialed cancel backstop.

Every test here asserts on REAL behaviour of scripts/l5_halt_reset.py and
src/l5_watchdog.py. Nothing asserts on source strings except the two image /
closure tests, which are about what is absent and can only be read that way.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import l5_halt_reset as R            # noqa: E402
from src import l5_halt_gate as HG                # noqa: E402
from src import l5_watchdog as W                  # noqa: E402

PHRASE = R.CONFIRM_PHRASE
AUTOMATIC = W.AUTOMATIC


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class FakeStore:
    """An ObjectStore with controllable failure, like the real volume."""

    def __init__(self):
        self.obj = {}
        self.fail_get = set()
        self.fail_put = set()
        self.puts = []

    def get(self, key):
        if key in self.fail_get:
            raise OSError("induced read failure")
        return self.obj.get(key)

    def put(self, key, body):
        if key in self.fail_put:
            raise OSError("induced write failure")
        self.obj[key] = body
        self.puts.append(key)
        return {"key": key}


class FakeTransport:
    """Venue reads only. Mirrors CancelOnlyTransport's two GET verbs."""

    def __init__(self, open_rows=None, positions=None, raise_open=False,
                 raise_pos=False):
        self._open = open_rows if open_rows is not None else []
        self._pos = positions if positions is not None else {}
        self.raise_open = raise_open
        self.raise_pos = raise_pos

    def open_orders(self):
        if self.raise_open:
            raise OSError("venue unreachable")
        return list(self._open)

    def request(self, method, path, body=None, base=None):
        if path == "/v1/portfolio/positions":
            if self.raise_pos:
                raise OSError("venue unreachable")
            return {"positions": self._pos}
        raise AssertionError("unexpected path %s" % path)


def latch(store, reason="UNMANAGED_RESTING_ORDER"):
    rec = {"halt": True, "t": R._utc().isoformat(), "who": "l5-watchdog",
           "reason": reason, "evidence": {"orders": ["X1"]}}
    raw = json.dumps(rec, sort_keys=True).encode()
    store.obj[HG.HALT_KEY] = raw
    return hashlib.sha256(raw).hexdigest()


def fresh_registry(rows=(), age_s=1.0):
    """An opener for RegistryOrderSource returning a fresh readable report."""
    t = R._utc() - dt.timedelta(seconds=age_s)
    doc = {"readable": True, "as_of_utc": t.isoformat(),
           "orders": [dict(r) for r in rows]}
    body = json.dumps(doc).encode()

    def opener(url, timeout_s):
        return body
    return opener


def env(**kw):
    base = {R.CONFIRM_ENV: PHRASE, R.WHO_ENV: "owner",
            R.ID_ENV: "reset-1", R.ATTEST_ENV: "sweeps 20s, no alarm"}
    base.update(kw)
    return base


def run(store, transport, digest, *, registry_rows=(), registry_age=1.0,
        **overrides):
    e = env(**{R.EXPECT_ENV: digest})
    e.update(overrides)
    saved = {k: os.environ.get(k) for k in e}
    os.environ.update({k: v for k, v in e.items() if v is not None})
    for k, v in e.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        return R.perform_reset(
            store=store, transport=transport,
            registry_url="http://registry.local",
            registry_opener=fresh_registry(registry_rows, registry_age))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------
# P0.1 -- the watchdog cannot submit, and cannot cancel a manual order
# --------------------------------------------------------------------------
def test_the_cancel_transport_has_no_submit_verb_at_all():
    seen = []
    t = W.CancelOnlyTransport(opener=lambda m, p, b, base: seen.append((m, p)))
    for method, path in [("POST", "/v1/order"), ("POST", "/v1/orders"),
                         ("PUT", "/v1/order/A1"), ("DELETE", "/v1/order/A1"),
                         ("PATCH", "/v1/order/A1"),
                         ("POST", "/v1/order/A1/replace"),
                         ("POST", "/v1/order/A1/cancel/../submit")]:
        with pytest.raises(W.MutationRefused):
            t.request(method, path)
    assert seen == [], "a refused request must never reach the wire"
    assert not hasattr(t, "submit")
    assert not hasattr(t, "place")


def test_the_only_post_is_the_single_cancel_route_and_it_signs_correctly():
    """THE REGRESSION FOR THE ARITY DEFECT.

    signed_headers is (method, path, now_ms=None). The body used to be passed
    as now_ms, so the cancel sent X-PM-Timestamp: "{}" and signed over
    "{}"+POST+path -- a guaranteed 401 that would have left the order resting
    while reporting a cancel error.
    """
    from src import pmus_auth as A
    seen = {}

    def opener(method, path, body, base):
        seen.update(method=method, path=path, body=body, base=base)
        return {}

    t = W.CancelOnlyTransport(opener=opener)
    t.cancel("A1")
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/order/A1/cancel"

    # and the headers the real signer would produce for that call
    os.environ[A.KEY_ENV] = "k"
    import base64
    os.environ[A.SECRET_ENV] = base64.b64encode(b"\x01" * 32).decode()
    try:
        h = A.signed_headers("POST", "/v1/order/A1/cancel")
        assert h["X-PM-Timestamp"].isdigit(), h["X-PM-Timestamp"]
        assert int(h["X-PM-Timestamp"]) > 10 ** 12
    finally:
        os.environ.pop(A.KEY_ENV, None)
        os.environ.pop(A.SECRET_ENV, None)


def test_cancel_requires_both_the_registry_and_the_venue_to_agree():
    """Neither witness alone is sufficient; a MANUAL order is never cancelled."""
    overdue_id = "D23N1EHTTZ7D"
    old = (R._utc() - dt.timedelta(seconds=9999)).isoformat()
    rows = [{"id": overdue_id, "marketSlug": "m1", "createTime": old,
             "state": "OPEN", "leavesQuantity": "1"}]

    class Src:
        name = "T"

        def __init__(self, venue_row):
            self.venue_row = venue_row
            self.cancelled = []

        def open_orders(self):
            return list(rows)

        def order(self, oid):
            return dict(self.venue_row)

        def cancel(self, oid):
            self.cancelled.append(oid)
            return {}

    class Oracle:
        def __init__(self, claimed):
            self._c = claimed

        def claimed(self):
            return dict(self._c)

    class Asserter:
        def __init__(self):
            self.halts = []

        def assert_halt(self, *, reason, evidence):
            self.halts.append(reason)
            return {"halt": True, "reason": reason}

        def heartbeat_no_halt(self):
            raise AssertionError("not reached while latching")

    # (a) claimed by us, but the VENUE says MANUAL -> never cancelled
    t = Src({"manualOrderIndicator": "MANUAL", "marketSlug": "m1",
             "state": "OPEN", "leavesQuantity": "1"})
    a = Asserter()
    wd = W.Watchdog(t, a, max_order_age_s=1.0, cancel_enabled=True,
                    oracle=Oracle({overdue_id: {"market": "m1"}}))
    out = wd.sweep()
    assert t.cancelled == []
    assert out["not_ours"][0]["why"] == "VENUE_SAYS_MANUAL"
    assert a.halts == ["UNMANAGED_RESTING_ORDER"]

    # (b) venue says AUTOMATIC, but we never claimed it -> never cancelled
    t = Src({"manualOrderIndicator": AUTOMATIC, "marketSlug": "m1",
             "state": "OPEN", "leavesQuantity": "1"})
    wd = W.Watchdog(t, Asserter(), max_order_age_s=1.0, cancel_enabled=True,
                    oracle=Oracle({}))
    out = wd.sweep()
    assert t.cancelled == []

    # (c) both agree -> cancelled by EXACT id, and the re-read decides
    t = Src({"manualOrderIndicator": AUTOMATIC, "marketSlug": "m1",
             "state": "CANCELED", "leavesQuantity": "0", "cumQuantity": "0"})
    wd = W.Watchdog(t, Asserter(), max_order_age_s=1.0, cancel_enabled=True,
                    oracle=Oracle({overdue_id: {"market": "m1"}}))
    out = wd.sweep()
    assert t.cancelled == [overdue_id]
    assert out["cancelled"][0]["venue_order_id"] == overdue_id


def test_a_market_mismatch_between_our_claim_and_the_venue_cancels_nothing():
    overdue_id = "A1"
    old = (R._utc() - dt.timedelta(seconds=9999)).isoformat()

    class Src:
        name = "T"
        def __init__(self):
            self.cancelled = []
        def open_orders(self):
            return [{"id": overdue_id, "marketSlug": "m1", "createTime": old,
                     "state": "OPEN", "leavesQuantity": "1"}]
        def order(self, oid):
            return {"manualOrderIndicator": AUTOMATIC, "marketSlug": "OTHER",
                    "state": "OPEN", "leavesQuantity": "1"}
        def cancel(self, oid):
            self.cancelled.append(oid)

    class Asserter:
        def assert_halt(self, *, reason, evidence):
            return {}
        def heartbeat_no_halt(self):
            raise AssertionError

    class Oracle:
        def claimed(self):
            return {overdue_id: {"market": "m1"}}

    t = Src()
    out = W.Watchdog(t, Asserter(), max_order_age_s=1.0, cancel_enabled=True,
                     oracle=Oracle()).sweep()
    assert t.cancelled == []
    assert out["not_ours"][0]["why"] == "MARKET_MISMATCH"


def test_cancel_stays_unavailable_without_an_oracle_however_configured():
    class Src:
        name = "T"
        def open_orders(self):
            return []

    class Asserter:
        def heartbeat_no_halt(self):
            return {"halt": False}

    wd = W.Watchdog(Src(), Asserter(), cancel_enabled=True, oracle=None)
    assert wd.cancel_enabled is False
    assert wd.cancel_requested is True


# --------------------------------------------------------------------------
# P0.2 -- HALT still asserted independently, and still not clearable
# --------------------------------------------------------------------------
def test_the_halt_is_latched_before_any_cancel_is_attempted():
    old = (R._utc() - dt.timedelta(seconds=9999)).isoformat()
    order = []

    class Src:
        name = "T"
        def open_orders(self):
            return [{"id": "A1", "marketSlug": "m1", "createTime": old,
                     "state": "OPEN", "leavesQuantity": "1"}]
        def order(self, oid):
            return {"manualOrderIndicator": AUTOMATIC, "marketSlug": "m1",
                    "state": "CANCELED", "leavesQuantity": "0"}
        def cancel(self, oid):
            order.append("cancel")

    class Asserter:
        def assert_halt(self, *, reason, evidence):
            order.append("halt")
            return {}
        def heartbeat_no_halt(self):
            raise AssertionError

    class Oracle:
        def claimed(self):
            return {"A1": {"market": "m1"}}

    W.Watchdog(Src(), Asserter(), max_order_age_s=1.0, cancel_enabled=True,
               oracle=Oracle()).sweep()
    assert order[0] == "halt", order


def test_a_credentialed_watchdog_still_cannot_clear_its_own_latch():
    store = FakeStore()
    a = HG.HaltAsserter(store, who="l5-watchdog")
    a.assert_halt(reason="UNMANAGED_RESTING_ORDER", evidence={"o": 1})
    with pytest.raises(PermissionError):
        a.heartbeat_no_halt()
    assert not hasattr(a, "clear")
    assert not hasattr(a, "clear_halt")
    assert HG.HaltGate(store).submissions_permitted() is False


def test_the_reset_module_is_not_reachable_from_the_sweep_entrypoint():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sweep = open(os.path.join(root, "scripts/l5_halt_watchdog.py")).read()
    assert "l5_halt_reset" not in sweep
    wd = open(os.path.join(root, "src/l5_watchdog.py")).read()
    assert "l5_halt_reset" not in wd
    gate = open(os.path.join(root, "src/l5_halt_gate.py")).read()
    assert "l5_halt_reset" not in gate


def test_the_watchdog_image_still_carries_no_order_placing_code():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    df = open(os.path.join(root, "deploy/l5_halt_watchdog/Dockerfile")).read()
    copied = [ln.split()[1] for ln in df.splitlines()
              if ln.startswith("COPY ")]
    assert "src/pmus_auth.py" in copied
    assert "scripts/l5_halt_reset.py" in copied
    for forbidden in ("src/l5_pmus_transport.py", "src/l5_live_execution.py",
                      "src/l5_two_sided_probe.py",
                      "src/l5_live_authorization.py"):
        assert forbidden not in copied, forbidden


# --------------------------------------------------------------------------
# P0.3 -- owner-controlled, auditable, ONE-TIME
# --------------------------------------------------------------------------
def test_the_happy_path_clears_the_latch_and_writes_one_audit_entry():
    store = FakeStore()
    d = latch(store)
    out = run(store, FakeTransport(), d)
    assert out["ok"] is True
    rec = json.loads(store.obj[HG.HALT_KEY])
    assert rec["halt"] is False
    assert rec["reset_id"] == "reset-1"
    assert rec["cleared_reason"] == "UNMANAGED_RESTING_ORDER"
    # the gate is now permissive
    assert HG.HaltGate(store).submissions_permitted() is True
    # exactly one audit line, carrying the prior latch verbatim
    lines = store.obj[R.AUDIT_KEY].decode().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["who"] == "owner"
    assert entry["prior_latch"]["reason"] == "UNMANAGED_RESTING_ORDER"
    assert entry["prior_latch_sha256"] == d
    assert entry["machine_verified"]["venue"]["venue_open_orders"] == 0
    assert entry["operator_attested"]["text"] == "sweeps 20s, no alarm"


def test_the_audit_record_is_written_before_the_latch_is_cleared():
    store = FakeStore()
    d = latch(store)
    store.fail_put.add(R.AUDIT_KEY)
    with pytest.raises(OSError):
        run(store, FakeTransport(), d)
    # the halt is UNTOUCHED: no audit, no clear
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True
    assert HG.HaltGate(store).submissions_permitted() is False


def test_the_same_reset_id_cannot_clear_a_second_latch():
    store = FakeStore()
    d1 = latch(store)
    run(store, FakeTransport(), d1)
    # the watchdog latches again, for anything
    d2 = latch(store, reason="A_NEW_PROBLEM")
    with pytest.raises(R.ResetRefused, match="RESET_ID_ALREADY_CONSUMED"):
        run(store, FakeTransport(), d2)
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True


def test_a_second_boot_with_the_admin_command_left_in_place_clears_nothing():
    """The scenario the one-time guard exists for."""
    store = FakeStore()
    d = latch(store)
    assert run(store, FakeTransport(), d)["ok"] is True
    latch(store, reason="UNMANAGED_RESTING_ORDER")
    for _ in range(3):
        with pytest.raises(R.ResetRefused, match="ALREADY_CONSUMED"):
            run(store, FakeTransport(), hashlib.sha256(
                store.obj[HG.HALT_KEY]).hexdigest())
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True


def test_a_new_reset_id_is_required_and_is_itself_recorded():
    store = FakeStore()
    d1 = latch(store)
    run(store, FakeTransport(), d1)
    d2 = latch(store, reason="A_NEW_PROBLEM")
    out = run(store, FakeTransport(), d2, **{R.ID_ENV: "reset-2"})
    assert out["ok"] is True
    lines = store.obj[R.AUDIT_KEY].decode().strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["reset_id"] for x in lines] == ["reset-1", "reset-2"]


def test_a_corrupt_or_unreadable_audit_log_refuses_rather_than_assume_empty():
    store = FakeStore()
    d = latch(store)
    store.obj[R.AUDIT_KEY] = b"{not json}\n"
    with pytest.raises(R.ResetRefused, match="AUDIT_LOG_CORRUPT"):
        run(store, FakeTransport(), d)
    store.obj.pop(R.AUDIT_KEY)
    store.fail_get.add(R.AUDIT_KEY)
    with pytest.raises(R.ResetRefused, match="AUDIT_LOG_UNREADABLE"):
        run(store, FakeTransport(), d)
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True


# --------------------------------------------------------------------------
# intent gating
# --------------------------------------------------------------------------
@pytest.mark.parametrize("override,match", [
    ({R.CONFIRM_ENV: "yes"}, "CONFIRM_PHRASE_MISMATCH"),
    ({R.CONFIRM_ENV: "reset the latched halt"}, "CONFIRM_PHRASE_MISMATCH"),
    ({R.CONFIRM_ENV: None}, "CONFIRM_PHRASE_MISMATCH"),
    ({R.WHO_ENV: None}, "NO_OPERATOR"),
    ({R.ID_ENV: None}, "NO_RESET_ID"),
    ({R.ATTEST_ENV: None}, "NO_ATTESTATION"),
])
def test_intent_gates_refuse_and_write_nothing(override, match):
    store = FakeStore()
    d = latch(store)
    with pytest.raises(R.ResetRefused, match=match):
        run(store, FakeTransport(), d, **override)
    assert store.puts == []
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True


@pytest.mark.parametrize("bad", ["", "deadbeef", "x" * 64, "A" * 63])
def test_a_missing_or_malformed_expected_digest_refuses(bad):
    store = FakeStore()
    latch(store)
    with pytest.raises(R.ResetRefused, match="NO_EXPECTED_DIGEST"):
        run(store, FakeTransport(), bad)
    assert store.puts == []


def test_you_cannot_clear_a_latch_you_did_not_review():
    """Gate 2: the watchdog re-latched for a different reason."""
    store = FakeStore()
    reviewed = latch(store, reason="UNMANAGED_RESTING_ORDER")
    latch(store, reason="SOMETHING_NEW_AND_WORSE")
    with pytest.raises(R.ResetRefused, match="LATCH_CHANGED_SINCE_REVIEW"):
        run(store, FakeTransport(), reviewed)
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True
    assert store.puts == []


# --------------------------------------------------------------------------
# P0.4 -- the triggering condition must actually be gone
# --------------------------------------------------------------------------
def test_a_working_order_at_the_venue_refuses_the_reset():
    store = FakeStore()
    d = latch(store)
    t = FakeTransport(open_rows=[{"id": "A1", "state": "OPEN",
                                  "leavesQuantity": "1"}])
    with pytest.raises(R.ResetRefused, match="VENUE_STILL_HAS_OPEN_ORDERS"):
        run(store, t, d)
    assert store.puts == []


def test_a_terminal_row_in_the_open_list_does_not_block_the_reset():
    store = FakeStore()
    d = latch(store)
    t = FakeTransport(open_rows=[{"id": "A1", "state": "CANCELED",
                                  "leavesQuantity": "0"}])
    assert run(store, t, d)["ok"] is True


def test_an_unreadable_venue_is_never_taken_as_flat():
    store = FakeStore()
    d = latch(store)
    with pytest.raises(R.ResetRefused, match="VENUE_OPEN_ORDERS_UNREADABLE"):
        run(store, FakeTransport(raise_open=True), d)
    with pytest.raises(R.ResetRefused, match="VENUE_POSITIONS_UNREADABLE"):
        run(store, FakeTransport(raise_pos=True), d)
    assert store.puts == []


def test_a_non_zero_position_refuses_in_both_venue_shapes():
    store = FakeStore()
    d = latch(store)
    # the OBJECT-keyed-by-slug shape
    t = FakeTransport(positions={"m1": {"quantity": "2"}})
    with pytest.raises(R.ResetRefused, match="VENUE_NOT_FLAT"):
        run(store, t, d)
    # and an unreadable quantity is not a zero
    t = FakeTransport(positions={"m1": {"quantity": "weird"}})
    with pytest.raises(R.ResetRefused, match="VENUE_NOT_FLAT"):
        run(store, t, d)
    assert store.puts == []


def test_a_zero_position_row_does_not_block_the_reset():
    store = FakeStore()
    d = latch(store)
    t = FakeTransport(positions={"m1": {"quantity": "0"}})
    assert run(store, t, d)["ok"] is True


def test_an_executor_still_claiming_a_live_order_refuses():
    store = FakeStore()
    d = latch(store)
    with pytest.raises(R.ResetRefused,
                       match="EXECUTOR_STILL_CLAIMS_LIVE_ORDERS"):
        run(store, FakeTransport(), d,
            registry_rows=[{"venue_order_id": "A1", "market": "m1",
                            "t": R._utc().isoformat()}])
    assert store.puts == []


def test_a_stale_registry_report_is_the_wedged_signal_not_an_empty_book():
    store = FakeStore()
    d = latch(store)
    with pytest.raises(R.ResetRefused, match="REGISTRY_STALE"):
        run(store, FakeTransport(), d,
            registry_age=R.MAX_REGISTRY_AGE_S + 5)
    assert store.puts == []


def test_an_unreadable_registry_refuses():
    store = FakeStore()
    d = latch(store)
    e = env(**{R.EXPECT_ENV: d})
    saved = {k: os.environ.get(k) for k in e}
    os.environ.update(e)
    try:
        def boom(url, timeout_s):
            raise OSError("registry down")
        with pytest.raises(R.ResetRefused, match="REGISTRY_UNREADABLE"):
            R.perform_reset(store=store, transport=FakeTransport(),
                            registry_url="http://registry.local",
                            registry_opener=boom)
        # and a report that says readable: false
        def unreadable(url, timeout_s):
            return json.dumps({"readable": False, "orders": []}).encode()
        with pytest.raises(R.ResetRefused, match="REGISTRY_UNREADABLE"):
            R.perform_reset(store=store, transport=FakeTransport(),
                            registry_url="http://registry.local",
                            registry_opener=unreadable)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert store.puts == []


def test_no_registry_url_refuses():
    store = FakeStore()
    d = latch(store)
    e = env(**{R.EXPECT_ENV: d})
    saved = {k: os.environ.get(k) for k in e}
    os.environ.update(e)
    try:
        with pytest.raises(R.ResetRefused, match="NO_REGISTRY_URL"):
            R.perform_reset(store=store, transport=FakeTransport(),
                            registry_url="")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------
# latch-state edge cases
# --------------------------------------------------------------------------
def test_an_absent_latch_is_not_something_to_reset():
    store = FakeStore()
    with pytest.raises(R.ResetRefused, match="NO_LATCH_PRESENT"):
        run(store, FakeTransport(), "a" * 64)


def test_an_unreadable_or_torn_latch_is_never_cleared():
    store = FakeStore()
    store.obj[HG.HALT_KEY] = b"{tornjson"
    with pytest.raises(R.ResetRefused, match="LATCH_UNPARSEABLE"):
        run(store, FakeTransport(), "a" * 64)
    store.fail_get.add(HG.HALT_KEY)
    with pytest.raises(R.ResetRefused, match="LATCH_UNREADABLE"):
        run(store, FakeTransport(), "a" * 64)
    assert store.puts == []


def test_an_already_permissive_record_is_refused_as_not_latched():
    store = FakeStore()
    rec = {"halt": False, "t": R._utc().isoformat(), "who": "l5-watchdog"}
    raw = json.dumps(rec, sort_keys=True).encode()
    store.obj[HG.HALT_KEY] = raw
    with pytest.raises(R.ResetRefused, match="NOT_LATCHED"):
        run(store, FakeTransport(), hashlib.sha256(raw).hexdigest())


def test_main_returns_nonzero_and_prints_a_refusal_without_env(capsys):
    saved = {k: os.environ.get(k) for k in
             (R.CONFIRM_ENV, R.WHO_ENV, R.ID_ENV, R.EXPECT_ENV, R.ATTEST_ENV)}
    for k in saved:
        os.environ.pop(k, None)
    try:
        rc = R.main([])
        out = capsys.readouterr().out
        assert rc != 0
        assert "L5_HALT_RESET_REFUSED" in out
        assert json.loads(out.split(" ", 1)[1])["ok"] is False
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
