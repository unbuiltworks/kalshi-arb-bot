"""An expired envelope should disarm the lane, not blind the watchdog.

WHAT HAPPENED. `l5-btc5m-live` exited 2026-11-17T23:05Z on
ENVELOPE_EXPIRED with code 0 -- a designed dead-man, which Railway
reports as CRASHED -- and stayed down. `serve_fills` runs on a DAEMON
thread, so the registry died with the process. Every watchdog sweep that
day then read

    l5-btc5m-live.railway.internal:8090:REGISTRY_UNREACHABLE:URLError

and because the ownership oracle requires EVERY registry to be readable
before it will claim any id, the sports halt at 16:02Z could cancel
NOTHING account-wide. A lane that was merely switched off degraded the
protection the other lane depends on.

THE DEAD-MAN IS PRESERVED. These tests are mostly about proving that
staying resident is not permission:

  - the executor is built `Btc5mExecutor(None, ledger, ...)`, transport
    None, so no object in the process can POST;
  - the loop `continue`s and never reaches the trading body;
  - the same gate refuses again every 15 s;
  - and a LATCH still ends the process, because "NOTHING REARMS".
"""
from __future__ import annotations

import inspect

import pytest

from src import l5_btc5m_runner as RUN


# ----------------------------------------------------- off by default

def test_off_by_default():
    assert RUN.resident_when_disarmed() is False


@pytest.mark.parametrize("raw,want", [
    ("YES", True), (" YES ", True),
    ("yes", False), ("Yes", False), ("1", False), ("TRUE", False),
    ("ON", False), ("", False), ("NO", False),
])
def test_only_the_exact_string_YES_enables_it(monkeypatch, raw, want):
    monkeypatch.setenv(RUN.RESIDENT_WHEN_DISARMED_ENV, raw)
    assert RUN.resident_when_disarmed() is want


def test_while_off_every_exit_still_exits(monkeypatch):
    monkeypatch.delenv(RUN.RESIDENT_WHEN_DISARMED_ENV, raising=False)
    for reason in ("ENVELOPE_EXPIRED", "STATE_HALTED", "anything"):
        assert RUN.stay_resident({"exit": True, "reason": reason}) is False


# --------------------------------- ON: envelope reasons, and only those

@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(RUN.RESIDENT_WHEN_DISARMED_ENV, "YES")


@pytest.mark.parametrize("reason", sorted(RUN.UNAUTHORISED_REASONS))
def test_a_benignly_unauthorised_lane_stays_resident(on, reason):
    """No envelope, an expired one, one for another deployment, one that
    does not name this strategy: expected states, so sit disarmed and
    stay readable."""
    assert RUN.stay_resident({"exit": True, "reason": reason}) is True


@pytest.mark.parametrize("reason", [
    "ENVELOPE_UNPARSEABLE", "ENVELOPE_NOT_AN_OBJECT",
    "ENVELOPE_EXPIRY_UNREADABLE", "ENVELOPE_LIMITS_UNREADABLE",
    "ENVELOPE_NONCE_TOO_SHORT", "DEPLOYMENT_UNKNOWN", "NOW_UNREADABLE",
    "REGISTRY_UNAVAILABLE",
])
def test_a_BROKEN_configuration_still_ends_the_process(on, reason):
    """THE OTHER HALF OF THE RULE. A lane that cannot read its own
    envelope, its clock or its strategy registry is not benignly
    unauthorised -- it is broken, and its cleanest failure mode is to
    stop. Residency is for the first case only."""
    assert RUN.stay_resident({"exit": True, "reason": reason}) is False


@pytest.mark.parametrize("reason", [
    "STATE_HALTED", "STATE_STOPPED", "STATE_PATH_UNSET",
    "STATE_UNREADABLE", "WATCHDOG:HALT", "", None, "SOMETHING_NEW",
])
def test_a_latch_or_an_unknown_reason_still_ends_the_process(on, reason):
    """THE PROPERTY THAT MATTERS MOST. A latch must not be downgraded to
    a pause, and a reason this module does not recognise must not be
    treated as benign."""
    assert RUN.stay_resident({"exit": True, "reason": reason}) is False


def test_a_gate_that_is_not_exiting_is_left_alone(on):
    assert RUN.stay_resident({"exit": False,
                              "reason": "STATE_NOT_ARMED"}) is False
    assert RUN.stay_resident({"ok": True, "exit": False,
                              "reason": None}) is False


def test_every_unauthorised_reason_is_one_the_parser_can_actually_return():
    """THIS TEST CAUGHT MY OWN BAD LIST. My first version enumerated the
    reasons from truncated grep output and got 11 of the 22
    `parse_envelope` returns, which would have left the other 11 treated
    as latches. Asserting the SUBSET relation means a typo or an invented
    reason fails here, and a NEW parser reason defaults to exiting --
    the safe side -- until somebody adds it deliberately."""
    import re
    src = inspect.getsource(
        __import__("src.l5_persistent_mode", fromlist=["x"]))
    i = src.index("def parse_envelope")
    j = src.index("\ndef ", i + 10)
    known = {m for m in re.findall(r'"([A-Z_]{6,})"', src[i:j])
             if m != "PERSISTENT"}
    invented = sorted(set(RUN.UNAUTHORISED_REASONS) - known)
    assert not invented, invented
    #: and the parser has reasons we deliberately do NOT treat as benign
    assert known - set(RUN.UNAUTHORISED_REASONS)


# ------------------------------- staying resident is not permission

def test_the_executor_is_constructed_without_a_transport():
    """STRUCTURAL, not gated: with transport None there is no object in
    the process that can POST, so residency cannot become submission."""
    src = inspect.getsource(RUN)
    assert "X.Btc5mExecutor(None, ledger," in src


def test_the_resident_branch_does_not_reach_the_trading_body():
    """It emits and falls through to the sleep/continue, exactly as
    STATE_NOT_ARMED does."""
    src = inspect.getsource(RUN)
    i = src.index('emit("L5_BTC5M_RESIDENT_DISARMED"')
    tail = src[i:i + 1200]
    assert "time.sleep(15.0)" in tail
    assert "continue" in tail
    #: and nothing in the branch places or arms
    #: CALLS, not substrings. "submit" matched my own `"can_submit":
    #: False` field -- the fourth time this session a string check hit
    #: prose or a key name rather than code.
    code = "\n".join(ln for ln in tail.splitlines()
                      if not ln.strip().startswith("#"))
    for forbidden in ("ex.step(", ".arm(", ".submit("):
        assert forbidden not in code, forbidden


def test_the_emitted_line_states_it_cannot_submit():
    src = inspect.getsource(RUN)
    i = src.index('emit("L5_BTC5M_RESIDENT_DISARMED"')
    block = src[i:i + 900]
    assert '"can_submit": False' in block
    assert "serving_registry" in block
    assert "not permission to trade" in block


def test_the_exit_check_runs_before_the_resident_check():
    """Source order: the `return 0` path is evaluated first, so a reason
    that does not qualify can never fall into residency."""
    src = inspect.getsource(RUN)
    i_ret = src.index("not stay_resident(gate)")
    i_res = src.index('emit("L5_BTC5M_RESIDENT_DISARMED"')
    assert i_ret < i_res


# ================= the explicit DISARMED transition, and no resumption

class TestTheDisarmedTransition:
    """The state write is BOTH requirements at once.

    It makes the lane SAY disarmed durably, and -- because
    `persistent_gate` requires state == ARMED -- it is the mechanism that
    stops a refreshed envelope resuming trading. Without it the lane
    would have resumed the moment a new envelope appeared, since the
    envelope was the only thing refusing.
    """

    def _branch(self):
        src = inspect.getsource(RUN)
        i = src.index('if gate.get("exit"):\n                    #: THE '
                      'EXPLICIT TRANSITION')
        return src[i:src.index('emit("L5_BTC5M_RESIDENT_DISARMED"')]

    def test_it_writes_MONITOR_through_the_existing_single_writer(self):
        b = self._branch()
        assert "PMODE.write_state(" in b
        assert "PMODE.MONITOR" in b
        assert 'why="ENVELOPE:%s"' in b

    def test_a_latch_is_never_downgraded_to_MONITOR(self):
        """HALTED and STOPPED exit above, and the branch RE-CHECKS rather
        than trusting that."""
        b = self._branch()
        assert "not in PMODE.LATCHED" in b

    def test_an_already_MONITOR_state_is_not_rewritten(self):
        b = self._branch()
        assert "!= PMODE.MONITOR" in b

    def test_a_failed_state_write_EXITS_instead_of_staying_armed(self):
        """THE ONE COMBINATION THAT COULD RESUME is resident + still
        ARMED. If the write fails we exit rather than risk it."""
        b = self._branch()
        assert "L5_BTC5M_DISARM_WRITE_FAILED" in b
        assert "return 0" in b

    def test_the_gate_requires_ARMED_so_MONITOR_cannot_trade(self):
        """The mechanism, stated against the gate itself: with the state
        no longer ARMED, a VALID envelope is not sufficient."""
        src = inspect.getsource(RUN)
        i = src.index("def persistent_gate")
        gate = src[i:src.index("\ndef ", i + 10)]
        assert 'st["state"] != PMODE.ARMED' in gate
        assert '"exit": False' in gate
        assert "STATE_NOT_ARMED" in gate

    def test_rearming_is_a_startup_ceremony_not_a_loop_action(self):
        """Nothing inside the persistent loop writes ARMED."""
        src = inspect.getsource(RUN)
        i = src.index('emit("L5_BTC5M_PERSISTENT_START"')
        loop = src[i:]
        assert "write_state(PMODE.ARMED" not in loop
        assert "PMODE.write_state(\n                                PMODE.ARMED" \
            not in loop


# ============================ the endpoints, honest about being disarmed

class TestTheServedEndpoints:

    def _handler(self):
        src = inspect.getsource(RUN)
        i = src.index("def serve_fills")
        return src[i:src.index("\ndef ", i + 10)]

    def test_the_ownership_registry_is_still_served(self):
        assert "REG.REGISTRY_KEY" in self._handler()
        assert "REG.registry(ex)" in self._handler()

    def test_the_fills_document_is_still_served(self):
        assert '"btc5m_fills"' in self._handler()
        assert "fills_document(ex)" in self._handler()

    def test_health_and_status_are_served(self):
        h = self._handler()
        assert '("health", "healthz")' in h
        assert '("status", "l5/status.json")' in h

    def test_status_reports_the_DURABLE_state_not_an_assertion(self):
        h = self._handler()
        assert "PMODE.read_state()" in h
        assert '"state_readable"' in h
        assert '"state_reason"' in h

    def test_it_does_not_claim_a_single_can_submit_verdict(self):
        """THE OWNER'S CHECK 4. A transport is a CAPABILITY, not a
        PERMISSION. `can_submit: True` invited the inference that
        authorization was fine, and `can_submit` as a field name claimed
        to answer a question this endpoint cannot. It is gone."""
        h = self._handler()
        assert '"can_submit"' not in h

    def test_absence_of_a_transport_is_reported_as_conclusive(self):
        """The one claim the endpoint CAN establish: with no
        POST-capable object, nothing can be sent whatever any gate
        says."""
        h = self._handler()
        assert '"submission_structurally_impossible"' in h
        assert 'getattr(ex, "transport", None) is None' in h

    def test_presence_of_a_transport_is_NOT_reported_as_authorised(self):
        """And the gates it does not evaluate are named, so a reader
        cannot mistake capability for permission."""
        h = self._handler()
        assert '"transport_present"' in h
        assert '"authorization_evaluated_here": False' in h
        for gate in ("campaign approval", "per-order claim",
                     "market lock", "shared halt", "account-wide"):
            assert gate in h, gate

    def test_the_armed_field_names_its_source(self):
        """`armed` alone would read as "is trading possible"; it is only
        what the durable state records."""
        h = self._handler()
        assert '"armed_per_durable_state"' in h
        assert '"armed":' not in h

    def test_health_claims_only_that_it_is_answering(self):
        """It must not be mistakable for "trading" or "well"."""
        h = self._handler()
        i = h.index('("health", "healthz")')
        block = h[i:i + 400]
        assert b'{"ok":true,"serving":true}'.decode() in block
        for leak in ("armed", "can_submit", "ARMED"):
            assert leak not in block, leak

    def test_an_unreadable_state_is_reported_not_fabricated(self):
        """REQ 4: no fabricated healthy state. An unreadable state says
        so rather than defaulting to something reassuring."""
        h = self._handler()
        assert "STATE_READ_FAILED" in h
        i = h.index("STATE_READ_FAILED")
        assert '"state_readable": bool(_st.get("ok"))' in h

    def test_an_unknown_path_is_still_404(self):
        assert 'b\'{"error":"no such key"}\', 404' in self._handler()


# ============================ the registry itself is not fabricated

def test_the_registry_reports_unreadable_rather_than_empty():
    """REQ 4, at the source: `l5_order_registry_server.registry` returns
    readable=False when the ledger cannot be read, because an empty list
    would read as "we own nothing" -- a different and dangerous claim."""
    import inspect as _i
    from src import l5_order_registry_server as REG
    src = _i.getsource(REG.registry)
    assert '"readable": False' in src
    assert "an empty list here would read as" in src


# ============ checks 2 and 3, functionally rather than by inspection

class TestNoResumptionAndAReadableRegistry:
    """The owner's checks 2 and 3, driven through the real modules."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        import json
        from src import l5_persistent_mode as P
        dep = "sha-abc123"
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
        monkeypatch.setenv(P.DEPLOYMENT_ENV, dep)
        monkeypatch.setenv(P.ENVELOPE_ENV, json.dumps({
            "approved": True, "mode": "PERSISTENT", "deployment": dep,
            "strategies": ["STRADDLE", "LATE_FAVORITE"],
            "series": ["cpc-btc-updown-5m-"], "taker_allowed": False,
            "max_aggregate_usd": "10.00", "max_per_market_usd": "5.00",
            "max_daily_loss_usd": "5.00", "nonce": "persist-0001-abcdef"}))
        return P

    def test_a_FRESH_envelope_plus_MONITOR_still_refuses(self, env):
        """CHECK 2. The envelope is valid and unexpired; the state is
        MONITOR. Trading must not resume."""
        import datetime as dt
        now = dt.datetime(2026, 11, 16, 13, 0, tzinfo=dt.timezone.utc)
        assert env.envelope(now=now)["ok"] is True
        env.write_state(env.MONITOR, why="ENVELOPE:ENVELOPE_EXPIRED",
                        now=now)
        g = RUN.persistent_gate(now=now)
        assert g["ok"] is False
        assert g["reason"] == "STATE_NOT_ARMED"
        assert g["exit"] is False          # waits, so it can be armed

    def test_the_same_envelope_with_ARMED_does_permit(self, env):
        """The control: the refusal above is the STATE, not the gate
        being broken."""
        import datetime as dt
        now = dt.datetime(2026, 11, 16, 13, 0, tzinfo=dt.timezone.utc)
        env.write_state(env.ARMED, why="t", now=now)
        assert RUN.persistent_gate(now=now)["ok"] is True

    def test_MONITOR_persists_across_a_reread(self, env):
        """`read_state` reads the file on every call, which IS the
        restart path."""
        import datetime as dt
        import json
        import os
        now = dt.datetime(2026, 11, 16, 13, 0, tzinfo=dt.timezone.utc)
        env.write_state(env.ARMED, why="t", now=now)
        env.write_state(env.MONITOR, why="ENVELOPE:X", now=now)
        assert env.read_state()["state"] == env.MONITOR
        on_disk = json.load(open(os.environ[env.STATE_PATH_ENV]))
        assert on_disk["state"] == env.MONITOR
        assert env.read_state()["state"] == env.MONITOR

    def test_the_registry_publishes_UNCERTAIN_orders(self):
        """CHECK 3. An uncertain order is exactly what the watchdog must
        be able to claim, so it has to appear."""
        from src import l5_order_registry_server as REG

        class Store:
            def live_orders(self):
                return {
                    "c1": {"venue_order_id": "v1", "market": "m",
                           "side": "UP", "session_id": "s", "t": "t",
                           "state": "NEW"},
                    "c2": {"venue_order_id": "v2", "market": "m",
                           "side": "DOWN", "session_id": "s", "t": "t",
                           "state": "UNCERTAIN"},
                }
        doc = REG.registry(Store())
        assert doc["readable"] is True
        assert [r["venue_order_id"] for r in doc["orders"]] == ["v1", "v2"]
        assert "submission_disabled" in doc

    def test_an_unreadable_ledger_is_not_an_empty_one(self):
        from src import l5_order_registry_server as REG

        class Broken:
            def live_orders(self):
                raise OSError("ledger unreadable")
        doc = REG.registry(Broken())
        assert doc["readable"] is False
        assert doc["orders"] == []
