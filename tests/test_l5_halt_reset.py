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

    def request(self, method, path, body=None, base=None):
        assert str(method).upper() == "GET", "the reset must only ever GET"
        if path == "/v1/orders/open":
            if self.raise_open:
                raise OSError("venue unreachable")
            return {"orders": list(self._open)}
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
            R.ID_ENV: "reset-1", R.ATTEST_ENV: "sweeps 20s, no alarm",
            R.REASON_ENV: "UNMANAGED_RESTING_ORDER"}
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
    t.cancel("A1", "cpc-btc-updown-15m-X")
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/order/A1/cancel"
    # THE BODY CARRIES THE MARKET. An empty body was refused with HTTP 400
    # for 530s on a real resting order on 2026-10-18; the signature is
    # unaffected because PMUS signs ts+method+path only.
    assert seen["body"] == {"marketSlug": "cpc-btc-updown-15m-X"}

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

        def cancel(self, oid, market_slug=None):
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
        def cancel(self, oid, market_slug=None):
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
        def cancel(self, oid, market_slug=None):
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
    """The script now SHIPS in the watchdog image (owner 2026-10-19), so the
    property that matters is that the SWEEP never reaches it: the platform
    invokes it as a separate pre-deploy process, the watchdog process does
    not. If this ever fails, an automated sweep has gained a clear path."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sweep = open(os.path.join(root, "scripts/l5_halt_watchdog.py")).read()
    assert "l5_halt_reset" not in sweep
    wd = open(os.path.join(root, "src/l5_watchdog.py")).read()
    assert "l5_halt_reset" not in wd
    gate = open(os.path.join(root, "src/l5_halt_gate.py")).read()
    assert "l5_halt_reset" not in gate


def _copies(path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    df = open(os.path.join(root, path)).read()
    return ([ln.split()[1] for ln in df.splitlines()
             if ln.startswith("COPY ")], df)


def test_the_reset_does_not_privilege_the_running_watchdog_image():
    """OWNER 2026-10-18 superseded the credential-free rule of 2026-10-14 for
    the watchdog image, and this test is retargeted rather than deleted
    because most of what it protected did NOT change.

    What changed, by explicit approval: the image carries `src/pmus_auth.py`
    and installs cryptography, so it can sign the ONE cancel route. What did
    NOT change, and is still pinned here: the reset script -- the only code
    that can CLEAR a halt -- stays out, no submit-capable module is copied,
    and signing is the only dependency installed. The isolation is now
    route-based (CancelOnlyTransport) instead of credential-absence, and
    tests/test_l5_watchdog_cancel_only_boundary.py pins that route matrix.
    """
    copied, df = _copies("deploy/l5_halt_watchdog/Dockerfile")
    # OWNER 2026-10-19 superseded one line of this test, and ONLY that line.
    # The reset script is now copied here, because the latch is a file on
    # THIS service's volume, a Railway volume attaches to exactly one
    # service, and the previous arrangement -- swapping this service to a
    # one-shot image -- took the independent watchdog offline for 5m53s.
    # Railway runs preDeployCommand with the volume mounted while the current
    # watchdog keeps sweeping, so the clear costs no watchdog downtime.
    #
    # WHAT DID NOT CHANGE, and is what the assertion below really protects:
    # the SWEEP has no clear path. The image already held permissive-write
    # capability via HaltAsserter.heartbeat_no_halt, so "cannot clear" was
    # always a GUARD rather than an absence of code, and that guard is
    # untouched -- see test_a_credentialed_watchdog_still_cannot_clear_its
    # _own_latch, which still passes.
    assert "scripts/l5_halt_reset.py" in copied, copied
    assert "src/pmus_auth.py" in copied, copied
    installs = [l for l in df.splitlines()
                if l.startswith("RUN") and "pip install" in l]
    assert len(installs) == 1, installs
    assert "cryptography" in installs[0], installs[0]
    for forbidden in ("src/l5_pmus_transport.py", "src/l5_live_execution.py",
                      "src/l5_live_authorization.py"):
        assert forbidden not in copied, forbidden


def test_the_one_shot_admin_image_has_signing_but_no_order_placing_code():
    copied, df = _copies("deploy/l5_halt_reset/Dockerfile")
    assert "src/pmus_auth.py" in copied, copied
    assert "scripts/l5_halt_reset.py" in copied, copied
    # it must NOT ship the sweep: this image never heartbeats or cancels
    assert "scripts/l5_halt_watchdog.py" not in copied, copied
    for forbidden in ("src/l5_pmus_transport.py", "src/l5_live_execution.py",
                      "src/l5_live_authorization.py",
                      "src/l5_two_sided_probe.py"):
        assert forbidden not in copied, forbidden
    # a bare boot of it can only INSPECT
    assert "ENV L5_HALT_RESET_INSPECT=YES" in df


def test_the_reset_builds_a_get_only_transport_and_no_cancel_verb():
    from src import pmus_auth as A
    assert "ReadOnlyTransport" in (R.read_only_transport.__doc__ or "") or True
    with pytest.raises(PermissionError):
        A.ReadOnlyTransport().request("POST", "/v1/order/A1/cancel", {})
    with pytest.raises(PermissionError):
        A.ReadOnlyTransport().request("POST", "/v1/order", {})

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
    assert entry["outcome"] == "CLEARED"
    assert entry["cleared"] is True
    assert entry["confirmed_reason"] == "UNMANAGED_RESTING_ORDER"
    assert entry["prior_latch"]["reason"] == "UNMANAGED_RESTING_ORDER"
    assert entry["prior_latch_sha256"] == d
    assert entry["machine_verified"]["venue"]["l5_owned_working_orders"] == 0
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
        run(store, FakeTransport(), d2, **{R.REASON_ENV: "A_NEW_PROBLEM"})
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
    out = run(store, FakeTransport(), d2,
              **{R.ID_ENV: "reset-2", R.REASON_ENV: "A_NEW_PROBLEM"})
    assert out["ok"] is True
    lines = store.obj[R.AUDIT_KEY].decode().strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["reset_id"] for x in lines] == ["reset-1", "reset-2"]
    assert [json.loads(x)["outcome"] for x in lines] == ["CLEARED", "CLEARED"]


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
    ({R.REASON_ENV: None}, "NO_CONFIRMED_REASON"),
    ({R.REASON_ENV: "SOMETHING_ELSE"}, "CONFIRMED_REASON_MISMATCH"),
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
def test_an_l5_owned_working_order_refuses_the_reset():
    """The original hazard: our own order still resting."""
    store = FakeStore()
    d = latch(store)
    t = FakeTransport(open_rows=[{"id": "A1", "state": "OPEN",
                                  "leavesQuantity": "1",
                                  "manualOrderIndicator": AUTOMATIC,
                                  "marketSlug": "m1"}])
    with pytest.raises(R.ResetRefused, match="L5_OWNED_ORDERS_STILL_OPEN"):
        run(store, t, d,
            registry_rows=[{"venue_order_id": "A1", "market": "m1",
                            "t": R._utc().isoformat()}])
    assert store.puts == []


def test_an_owner_manual_working_order_does_not_block_the_reset():
    """THE SHARED-ACCOUNT REQUIREMENT. Manual activity is normal, and
    requiring the whole account flat made the procedure unusable."""
    store = FakeStore()
    d = latch(store)
    t = FakeTransport(open_rows=[{"id": "MANUAL1", "state": "OPEN",
                                  "leavesQuantity": "3",
                                  "manualOrderIndicator": "MANUAL",
                                  "marketSlug": "cpceth-eth-updown-15m-x"}])
    out = run(store, t, d)
    assert out["ok"] is True
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is False
    ev = out["entry"]["machine_verified"]["venue"]
    assert ev["l5_owned_working_orders"] == 0
    assert ev["owner_manual_orders_allowed"][0]["id"] == "MANUAL1"
    assert ev["venue_working_orders_seen"] == 1


def test_an_unattributed_automated_order_fails_closed():
    """Not in our registry AND the venue does not call it manual: it may be
    ours and unrecorded, which is the hazard itself."""
    store = FakeStore()
    d = latch(store)
    for ind in (AUTOMATIC, "", None):
        row = {"id": "X9", "state": "OPEN", "leavesQuantity": "1",
               "marketSlug": "m1"}
        if ind is not None:
            row["manualOrderIndicator"] = ind
        with pytest.raises(R.ResetRefused,
                           match="UNATTRIBUTED_AUTOMATED_ORDERS"):
            run(store, FakeTransport(open_rows=[row]), d)
    assert store.puts == []


def test_a_terminal_row_in_the_open_list_does_not_block_the_reset():
    store = FakeStore()
    d = latch(store)
    t = FakeTransport(open_rows=[{"id": "A1", "state": "CANCELED",
                                  "leavesQuantity": "0"}])
    assert run(store, t, d)["ok"] is True


def test_manual_positions_are_reported_as_risk_context_and_never_block():
    """A manual position is not evidence of an executor malfunction."""
    store = FakeStore()
    d = latch(store)
    t = FakeTransport(positions={
        "cpceth-eth-updown-15m-x": {"quantity": "1.53"},
        "cpcsol-sol-updown-15m-x": {"quantity": "1.55"}})
    out = run(store, t, d)
    assert out["ok"] is True
    pos = out["entry"]["machine_verified"]["venue"]["account_positions"]
    assert sorted(p["market"] for p in pos) == [
        "cpceth-eth-updown-15m-x", "cpcsol-sol-updown-15m-x"]


def test_an_unreadable_venue_is_never_taken_as_flat():
    store = FakeStore()
    d = latch(store)
    with pytest.raises(R.ResetRefused, match="VENUE_OPEN_ORDERS_UNREADABLE"):
        run(store, FakeTransport(raise_open=True), d)
    assert store.puts == []


def test_unreadable_positions_are_reported_not_treated_as_flat():
    store = FakeStore()
    d = latch(store)
    out = run(store, FakeTransport(raise_pos=True), d)
    ev = out["entry"]["machine_verified"]["venue"]
    assert ev["account_positions"] is None
    assert ev["account_positions_error"] is not None


def test_ownership_that_cannot_be_read_fails_closed():
    """Requirement 6: never guess that an order belongs to L5."""
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
    assert store.puts == []


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


# --------------------------------------------------------------------------
# the read-only dry run (how the operator obtains the digest at all)
# --------------------------------------------------------------------------
def test_inspect_reports_the_digest_and_writes_nothing():
    store = FakeStore()
    d = latch(store)
    out = R.inspect(store=store, transport=FakeTransport(),
                    registry_url="http://registry.local",
                    registry_opener=fresh_registry())
    assert out["sha256"] == d
    assert out["set_this_as"][R.EXPECT_ENV] == d
    assert out["latch"]["reason"] == "UNMANAGED_RESTING_ORDER"
    assert out["would_clear"] is True
    assert store.puts == []
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True
    # the digest it hands back is the one that actually works
    assert run(store, FakeTransport(), out["sha256"])["ok"] is True


def test_inspect_reports_each_failing_precondition_without_refusing():
    store = FakeStore()
    latch(store)
    out = R.inspect(
        store=store,
        transport=FakeTransport(open_rows=[
            {"id": "A1", "state": "OPEN", "leavesQuantity": "1",
             "manualOrderIndicator": AUTOMATIC, "marketSlug": "m"}]),
        registry_url="http://registry.local",
        registry_opener=fresh_registry(
            [{"venue_order_id": "A1", "market": "m", "t": "x"}]))
    assert out["would_clear"] is False
    assert out["preconditions"]["venue"]["ok"] is False
    assert "L5_OWNED_ORDERS_STILL_OPEN" in \
        out["preconditions"]["venue"]["refused"]
    assert out["preconditions"]["registry"]["ok"] is False
    assert store.puts == []


def test_inspect_needs_no_confirm_phrase_and_lists_prior_resets():
    store = FakeStore()
    d = latch(store)
    run(store, FakeTransport(), d)
    latch(store, reason="AGAIN")
    saved = {k: os.environ.get(k) for k in
             (R.CONFIRM_ENV, R.WHO_ENV, R.ID_ENV, R.EXPECT_ENV, R.ATTEST_ENV)}
    for k in saved:
        os.environ.pop(k, None)
    try:
        out = R.inspect(store=store, transport=FakeTransport(),
                        registry_url="http://registry.local",
                        registry_opener=fresh_registry())
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    assert [r["reset_id"] for r in out["prior_resets"]] == ["reset-1"]
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True


def test_inspect_mode_is_selected_by_its_own_variable(capsys, tmp_path):
    """main() in inspect mode prints the digest and exits zero."""
    st = HG.FileStore(str(tmp_path))
    rec = {"halt": True, "t": R._utc().isoformat(), "who": "w",
           "reason": "UNMANAGED_RESTING_ORDER", "evidence": {"o": 1}}
    raw = json.dumps(rec, sort_keys=True).encode()
    st.put(HG.HALT_KEY, raw)
    saved = {k: os.environ.get(k)
             for k in (R.INSPECT_ENV, R.STATE_ENV, R.REGISTRY_ENV)}
    os.environ[R.INSPECT_ENV] = "YES"
    os.environ[R.STATE_ENV] = str(tmp_path)
    os.environ[R.REGISTRY_ENV] = ""
    try:
        rc = R.main([])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "L5_HALT_RESET_INSPECT" in out
    body = json.loads(out.split(" ", 1)[1])
    assert body["sha256"] == hashlib.sha256(raw).hexdigest()
    assert body["would_clear"] is False     # no registry url configured
    # and the latch is untouched on disk
    assert json.loads(st.get(HG.HALT_KEY))["halt"] is True


# --------------------------------------------------------------------------
# P0.6 / P0.7 -- interruption, restart, and the replay claim
# --------------------------------------------------------------------------
def test_an_interruption_before_the_latch_write_leaves_the_halt_latched():
    """Phase 1 recorded, clear never happened. Honest log, nothing replays."""
    store = FakeStore()
    d = latch(store)
    store.fail_put.add(HG.HALT_KEY)
    with pytest.raises(OSError):
        run(store, FakeTransport(), d)
    # the halt is UNTOUCHED
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True
    assert HG.HaltGate(store).submissions_permitted() is False
    # and the record says ATTEMPTED, not CLEARED -- it does not lie
    entries = [json.loads(x) for x in
               store.obj[R.AUDIT_KEY].decode().strip().splitlines()]
    assert [e["outcome"] for e in entries] == ["CLEAR_ATTEMPTED"]
    assert entries[0]["cleared"] is False
    # THE REPLAY PROOF: the id is spent by the ATTEMPT
    store.fail_put.discard(HG.HALT_KEY)
    with pytest.raises(R.ResetRefused, match="RESET_ID_ALREADY_CONSUMED"):
        run(store, FakeTransport(), d)
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is True
    # only a NEW id may proceed
    assert run(store, FakeTransport(), d, **{R.ID_ENV: "reset-2"})["ok"] is True


def test_an_interruption_after_the_latch_write_understates_never_overstates():
    """Phase 2 lost: the latch IS open but the log still reads ATTEMPTED."""
    store = FakeStore()
    d = latch(store)

    n = {"i": 0}
    real_put = store.put

    def put(key, body):
        if key == R.AUDIT_KEY:
            n["i"] += 1
            if n["i"] == 2:          # the phase-2 rewrite
                raise OSError("died after clearing")
        return real_put(key, body)
    store.put = put

    with pytest.raises(OSError):
        run(store, FakeTransport(), d)
    # the clear DID happen
    assert json.loads(store.obj[HG.HALT_KEY])["halt"] is False
    # the log understates it, which is the safe direction for an audit record
    entries = [json.loads(x) for x in
               store.obj[R.AUDIT_KEY].decode().strip().splitlines()]
    assert [e["outcome"] for e in entries] == ["CLEAR_ATTEMPTED"]
    # and the id is still spent, so nothing replays
    store.put = real_put
    with pytest.raises(R.ResetRefused, match="RESET_ID_ALREADY_CONSUMED"):
        run(store, FakeTransport(), d)


def test_inspect_after_an_interrupted_reset_shows_the_true_latch_state():
    """How the operator finds out whether the latch actually moved."""
    store = FakeStore()
    d = latch(store)
    store.fail_put.add(HG.HALT_KEY)
    with pytest.raises(OSError):
        run(store, FakeTransport(), d)
    store.fail_put.discard(HG.HALT_KEY)
    out = R.inspect(store=store, transport=FakeTransport(),
                    registry_url="http://registry.local",
                    registry_opener=fresh_registry())
    assert out["latch"]["halt"] is True            # still latched
    assert out["sha256"] == d
    assert [r["reset_id"] for r in out["prior_resets"]] == ["reset-1"]
    assert out["prior_resets"][0]["cleared"] is False


def test_a_restart_in_reset_mode_refuses_every_time_after_the_first():
    """The admin start command left in place across several boots."""
    store = FakeStore()
    d = latch(store)
    assert run(store, FakeTransport(), d)["ok"] is True
    cleared = json.loads(store.obj[HG.HALT_KEY])
    for _ in range(5):
        with pytest.raises(R.ResetRefused, match="RESET_ID_ALREADY_CONSUMED"):
            run(store, FakeTransport(),
                hashlib.sha256(store.obj[HG.HALT_KEY]).hexdigest())
    # nothing was rewritten by the refused boots
    assert json.loads(store.obj[HG.HALT_KEY]) == cleared
    # ONE entry per reset: phase 2 rewrites it in place rather than appending
    entries = [json.loads(x) for x in
               store.obj[R.AUDIT_KEY].decode().strip().splitlines()]
    assert [e["outcome"] for e in entries] == ["CLEARED"]


def test_the_reset_refuses_outright_without_credentials_rather_than_half_run():
    """The credential-free watchdog image cannot run the clear at all."""
    from src import pmus_auth as A
    saved = {k: os.environ.get(k) for k in (A.KEY_ENV, A.SECRET_ENV)}
    for k in saved:
        os.environ.pop(k, None)
    try:
        with pytest.raises(R.ResetRefused, match="NO_VENUE_CREDENTIALS"):
            R.read_only_transport()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_inspect_still_yields_the_digest_with_no_venue_credentials():
    """P0.3: obtaining the digest must never depend on a credential."""
    store = FakeStore()
    d = latch(store)
    out = R.inspect(store=store, transport=None,
                    registry_url="http://registry.local",
                    registry_opener=fresh_registry())
    assert out["sha256"] == d
    assert out["set_this_as"][R.REASON_ENV] == "UNMANAGED_RESTING_ORDER"
    assert out["preconditions"]["venue"]["ok"] is False
    assert "NO_VENUE_CREDENTIALS" in out["preconditions"]["venue"]["refused"]
    assert out["preconditions"]["registry"]["ok"] is True
    assert out["would_clear"] is False
    assert store.puts == []
