"""Tests for the durable L5 transfer supervisor (shadow default)."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src import gemini_l5_transfer_supervisor as sup


@pytest.fixture()
def sroot(tmp_path, monkeypatch):
    monkeypatch.setenv(sup.ROOT_ENV, str(tmp_path / "sv"))
    monkeypatch.delenv(sup.MODE_ENV, raising=False)
    monkeypatch.delenv(sup.MAX_PER_DAY_ENV, raising=False)
    monkeypatch.delenv(sup.LOSS_STOP_ENV, raising=False)
    return tmp_path / "sv"


def test_shadow_is_default_mode_and_stamped(sroot, capsys):
    assert sup.mode() == "SHADOW"
    sup.emit("TEST")
    out = capsys.readouterr().out
    assert '"mode":"SHADOW"' in out.replace(" ", "")


def test_bounds_daily_cap_and_loss_stop(sroot, monkeypatch):
    st = sup.load_state()
    assert sup.bounds_allow(st) is None
    st["campaigns_today"] = sup.DEFAULT_MAX_PER_DAY
    assert sup.bounds_allow(st) == "DAILY_CAMPAIGN_CAP"
    st["campaigns_today"] = 0
    st["realized_today_usd"] = "-1.00"
    assert sup.bounds_allow(st) == "DAILY_LOSS_STOP"
    # shadow/theoretical results must never move realized: only the live
    # reconciliation path writes realized_today_usd (asserted structurally)
    import inspect
    src = inspect.getsource(sup.run_loop)
    live_branch = src.split('if mode() == "LIVE":')[1].split("else:")[0]
    assert "realized_today_usd" in live_branch
    shadow_branch = src.split('if mode() == "LIVE":')[1].split("else:")[1]
    assert "realized_today_usd" not in shadow_branch


def test_halt_is_persistent_across_restart(sroot):
    st = sup.load_state()
    sup.halt(st, "settlement UNRESOLVED for TESTEVENT")
    st2 = sup.load_state()          # fresh read = simulated restart
    assert st2["halt"] is True
    assert sup.bounds_allow(st2).startswith("HALTED")


def test_shadow_fillable_semantics():
    # maker YES bid at 0.48 fills on taker SELL at <= 0.48
    trades = [{"type": "buy", "price": "0.50"}, {"type": "sell", "price": "0.48"}]
    assert sup.shadow_fillable(trades, "YES", D("0.48")) is not None
    assert sup.shadow_fillable([{"type": "sell", "price": "0.49"}],
                               "YES", D("0.48")) is None
    # maker NO at 0.50 rests as ask 0.50: fills on taker BUY at >= 0.50
    assert sup.shadow_fillable([{"type": "buy", "price": "0.52"}],
                               "NO", D("0.50")) is not None
    assert sup.shadow_fillable([{"type": "buy", "price": "0.49"}],
                               "NO", D("0.50")) is None


def test_live_mode_without_arming_is_a_noop(sroot, monkeypatch):
    monkeypatch.setenv(sup.MODE_ENV, "live")
    from src import gemini_l5_dynamic_q1 as owner
    for env in (owner.LIVE_ENV, owner.SID_ENV, owner.ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    row = asyncio.run(sup.live_campaign(1))
    assert row["status"] == "DISARMED" and row["theoretical"] is False


def test_shadow_loop_iteration_is_mutation_free(sroot, monkeypatch, capsys):
    """One full shadow iteration with the selector faked: no private call,
    no order path, ledger row labeled theoretical."""
    async def fake_candidate(**_kw):
        return SimpleNamespace(
            bid=D("0.48"), bid_size=D("250"), ask=D("0.50"),
            ask_size=D("250"),
            event=SimpleNamespace(ticker="BTC05MTEST",
                                  symbol="GEMI-BTC05MTEST-UP",
                                  seconds_to_expiry=200.0))
    monkeypatch.setattr(sup.selector, "select_best_candidate", fake_candidate)
    monkeypatch.setenv("GEMINI_L5_V1_POLICY", "1")
    monkeypatch.setenv("GEMINI_L5_FORCED_COMPLETION", "1")

    async def fake_trades(http, symbol, since_ms):
        return [{"type": "sell", "price": "0.48"},   # fills YES join
                {"type": "buy", "price": "0.60"}]
    monkeypatch.setattr(sup, "_public_trades", fake_trades)
    monkeypatch.setattr(sup, "IDLE_SLEEP_S", 0.01)
    monkeypatch.setattr(sup.camp, "STOP_BEFORE_EXPIRY_SECONDS", 199.0)

    asyncio.run(sup.run_loop(max_iterations=1))
    ledger = (sroot / "campaigns.jsonl").read_text().strip().splitlines()
    row = json.loads(ledger[-1])
    assert row["mode"] == "SHADOW" and row["theoretical"] is True
    assert row["status"].startswith("SHADOW")
    assert row.get("initializer") == "TRANSFER_INITIALIZER"
    hb = json.loads((sroot / "heartbeat.json").read_text())
    assert hb["mode"] == "SHADOW"
    st = sup.load_state()
    assert st["campaigns_today"] == 1
    assert st["realized_today_usd"] == "0"        # theoretical never realizes


def test_status_report_shape(sroot):
    sup.save_state(sup.load_state())
    sup.heartbeat("idle")
    rep = sup.status_report()
    for key in ("mode", "halt", "campaigns_today",
                "realized_today_usd_authoritative_only",
                "heartbeat_age_s", "unresolved_settlements"):
        assert key in rep
    assert rep["mode"] == "SHADOW"


def test_scan_error_never_kills_the_loop_or_burns_a_campaign(sroot, monkeypatch):
    async def boom(**_kw):
        raise TimeoutError("ws top-of-book timeout")
    monkeypatch.setattr(sup.selector, "select_best_candidate", boom)
    monkeypatch.setattr(sup, "IDLE_SLEEP_S", 0.01)
    asyncio.run(sup.run_loop(max_iterations=2))       # must not raise
    st = sup.load_state()
    assert st["campaigns_today"] == 0                 # scan errors don't count
    assert st["halt"] is False                        # and never halt
    ledger = (sroot / "campaigns.jsonl").read_text().strip().splitlines()
    rows = [json.loads(l) for l in ledger]
    assert all(r["status"] == "SCAN_ERROR" for r in rows)
    assert all(r["mode"] == "SHADOW" for r in rows)


def test_supervisor_halt_taxonomy_for_netting(sroot):
    assert "MATCHED_PAIR_NO_SETTLED_ROWS" in sup.SAFE_CONTINUE_STATUSES
    assert "MATCHED_NETTED_CONFIRMED" in sup.SAFE_CONTINUE_STATUSES
    assert "MATCHED_NETTING_UNCONFIRMED" in sup.SAFE_CONTINUE_STATUSES
    assert "UNRESOLVED" in sup.HALT_STATUSES
    assert "AMBIGUOUS_SETTLEMENT_IDENTITY" in sup.HALT_STATUSES
    assert "NETTING_CASHFLOW_INCONSISTENT" in sup.HALT_STATUSES
    # structural: the live loop halts exactly on HALT_STATUSES
    import inspect
    src = inspect.getsource(sup.run_loop)
    assert "in HALT_STATUSES" in src


def test_ws_evidence_module_is_subscribe_only():
    import inspect
    from src import gemini_l5_ws_evidence as wse
    src = inspect.getsource(wse)
    assert "order.place" not in src
    assert "order.cancel" not in src
    assert "request(" not in src          # never calls the session request API
