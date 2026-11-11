"""The watchdog must be able to actually STOP the executor, and a configured
watchdog that is missing, stale or unreachable must fail CLOSED.

An unconfigured watchdog is reported as absent, never as active."""
from __future__ import annotations

import json
import pathlib
import tempfile
import time
from decimal import Decimal as D

import pytest

from src import l5_halt_gate as HG
from src import l5_live_execution as EX
from src import l5_pmus_live_runner as RUN
from src import l5_probe_manifest as PM
import tests.test_l5_live_execution as L

MARKET = "cpc-btc-updown-15m-2026-10-12-1600z"


def limits():
    return EX.SafetyLimits(max_usd_per_market=D("5"),
                           max_abs_residual=D("10"),
                           max_total_exposure_usd=D("5"),
                           max_concurrent_markets=1, max_live_orders=8,
                           daily_realized_loss_halt_usd=D("5"))


def executor(tmp_path, halt_gate=None):
    store = EX.InventoryStore(str(pathlib.Path(tmp_path) / "s.json"))
    ex = EX.FaithfulExecutor(store, limits(), transport=L.FakeTransport(),
                             lam=PM.LAMBDA, now_fn=L.NOW,
                             maker_treatment=True, maker_only=True)
    ex.halt_gate = halt_gate
    return ex


def decide(ex):
    return ex.handle(L.row(), {}, market=MARKET, window="1600z")


class Gate:
    def __init__(self, permitted, reason=None, raises=None):
        self._p, self._r, self._raises = permitted, reason, raises

    def submissions_permitted(self):
        if self._raises:
            raise self._raises
        return self._p

    def refuse_reason(self):
        return self._r


# ------------------------------- the watchdog can actually stop the executor

def test_an_asserted_halt_refuses_the_decision(tmp_path):
    ex = executor(tmp_path, Gate(False, "HALT_ASSERTED_BY_GUARD"))
    rec = decide(ex)
    assert rec.get("refused_reason")
    assert "HALT" in json.dumps(rec, default=str)
    assert "HALT_ASSERTED_BY_GUARD" in json.dumps(rec, default=str)


def test_a_permitting_gate_does_not_block_the_decision(tmp_path):
    ex = executor(tmp_path, Gate(True, None))
    rec = decide(ex)
    assert "HALT:" not in json.dumps(rec.get("refused_reason") or "", default=str)


def test_the_halt_is_evaluated_before_the_book_is_read(tmp_path):
    """An unreadable row must still be halted, not refused for being
    unreadable -- otherwise a halt depends on a good book."""
    ex = executor(tmp_path, Gate(False, "STOP"))
    rec = ex.handle({"action_key": "x"}, {}, market=MARKET, window="1600z")
    blob = json.dumps(rec, default=str)
    assert "HALT" in blob and "UNREADABLE_ROW" not in blob


# --------------------------------------------- fail closed, every failure mode

def test_an_unreachable_gate_refuses(tmp_path):
    ex = executor(tmp_path, Gate(True, raises=OSError("connection refused")))
    rec = decide(ex)
    assert "HALT_GATE_UNREADABLE" in json.dumps(rec, default=str)


def test_a_gate_raising_anything_at_all_refuses(tmp_path):
    ex = executor(tmp_path, Gate(True, raises=ValueError("garbage json")))
    rec = decide(ex)
    assert "HALT_GATE_UNREADABLE" in json.dumps(rec, default=str)


def test_a_missing_halt_record_refuses(tmp_path):
    """HaltGate over an empty store: absent is not permission."""
    root = str(pathlib.Path(tmp_path) / "halt")
    gate = HG.HaltGate(HG.FileStore(root))
    ex = executor(tmp_path, gate)
    rec = decide(ex)
    assert "HALT" in json.dumps(rec, default=str), "an absent record must refuse"


def test_a_stale_halt_record_refuses(tmp_path):
    root = str(pathlib.Path(tmp_path) / "halt")
    store = HG.FileStore(root)
    HG.HaltAsserter(store, who="guard").heartbeat_no_halt()
    gate = HG.HaltGate(store, max_age_s=0.0)      # everything is stale
    time.sleep(0.01)
    ex = executor(tmp_path, gate)
    assert "HALT" in json.dumps(decide(ex), default=str), "a stale record must refuse"


def test_a_fresh_no_halt_heartbeat_permits(tmp_path):
    root = str(pathlib.Path(tmp_path) / "halt")
    store = HG.FileStore(root)
    HG.HaltAsserter(store, who="guard").heartbeat_no_halt()
    gate = HG.HaltGate(store)
    assert gate.submissions_permitted() is True


def test_an_asserted_halt_through_the_real_store_stops_the_executor(tmp_path):
    """The full path: guard writes a halt, executor refuses."""
    root = str(pathlib.Path(tmp_path) / "halt")
    store = HG.FileStore(root)
    a = HG.HaltAsserter(store, who="l5-e1-safety-monitor")
    a.heartbeat_no_halt()
    gate = HG.HaltGate(store)
    ex = executor(tmp_path, gate)
    assert "HALT:" not in json.dumps(decide(ex).get("refused_reason") or "", default=str)

    a.assert_halt(reason="FOREIGN_ORDER_DETECTED",
                  evidence={"order_id": "X1", "market": MARKET})
    rec = decide(ex)
    assert "HALT" in json.dumps(rec, default=str)
    assert "FOREIGN_ORDER_DETECTED" in json.dumps(rec, default=str)


def test_the_executor_cannot_clear_a_halt_it_is_governed_by(tmp_path):
    """The guard asserts; the executor may only read."""
    root = str(pathlib.Path(tmp_path) / "halt")
    http = HG.HttpHaltStore("http://guard.internal:8080")
    with pytest.raises(PermissionError):
        http.put("halt", b"{}")


# ------------------------- an unconfigured watchdog is absent, not active

def test_an_unconfigured_watchdog_is_not_represented_as_active(monkeypatch):
    monkeypatch.delenv(RUN.HALT_URL_ENV, raising=False)
    assert RUN.halt_gate() is None


def test_an_absent_gate_does_not_block_shadow_decisions(tmp_path):
    """Inert means inert: with no gate the decision path is unchanged."""
    ex = executor(tmp_path, None)
    rec = decide(ex)
    assert "HALT" not in json.dumps(rec.get("refused_reason") or "", default=str)


def test_the_gates_report_distinguishes_configured_from_active(monkeypatch):
    monkeypatch.delenv(RUN.HALT_URL_ENV, raising=False)
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "src" / "l5_pmus_live_runner.py").read_text()
    assert '"halt_gate_configured"' in src, \
        "the heartbeat must say whether a watchdog exists at all"


def test_the_guards_own_reason_reaches_the_record(tmp_path):
    """Not just 'halt_asserted' -- an operator needs to know what it saw."""
    root = str(pathlib.Path(tmp_path) / "halt")
    store = HG.FileStore(root)
    a = HG.HaltAsserter(store, who="l5-e1-safety-monitor")
    a.assert_halt(reason="FOREIGN_ORDER_DETECTED",
                  evidence={"order_id": "X1"})
    ex = executor(tmp_path, HG.HaltGate(store))
    rec = decide(ex)
    assert "FOREIGN_ORDER_DETECTED" in str(rec.get("refused_reason"))
    assert rec["HALT_GATE"]["guard_reason"] == "FOREIGN_ORDER_DETECTED"


def test_a_coarse_failure_still_reports_its_classification(tmp_path):
    """A stale record has no guard reason; the classification must survive."""
    root = str(pathlib.Path(tmp_path) / "halt")
    store = HG.FileStore(root)
    HG.HaltAsserter(store, who="guard").heartbeat_no_halt()
    ex = executor(tmp_path, HG.HaltGate(store, max_age_s=0.0))
    time.sleep(0.01)
    assert "halt_record_stale" in str(decide(ex).get("refused_reason"))
