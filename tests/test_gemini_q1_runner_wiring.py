"""The q=1 envelope reaches the driver, and the position gate refuses first.

Two new things on the live path, both required by the authorization:

  * `run_continuous --envelope live_q1` is the ONLY way the reviewed q=1
    bounds reach `ws_continuous.Driver`. The default is still the 0.01 shadow
    envelope, so a bigger size is a decision rather than an upgrade.
  * an AUTHORITATIVE POSITION READ runs BEFORE THE LATCH and refuses unless
    the venue's own position agrees with our fill-derived ledger, exactly.
"""
import asyncio
import json
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import position_ledger as PL
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609181730-UP"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro)


def _arm(monkeypatch, tmp_path, *, fills, rows, ok=True):
    """Everything before the latch answered, so the POSITION GATE is what
    decides. The latch itself is spied on and never consumed."""
    from src.gemini_execution import latch as L
    reached = []

    def spy(auth_id, intent, state_dir, **k):
        reached.append(auth_id)
        raise RuntimeError("stop at the latch; the gate let us through")

    monkeypatch.setattr(RC, "eligible_symbol",
                        lambda *a, **k: {"eligible": True,
                                         "chosen": {"symbol": SYMBOL,
                                                    "tte_s": 250.0}})

    async def _terms():
        return {"accepted": True}
    monkeypatch.setattr(RC.MD, "terms_status", _terms)
    monkeypatch.setattr(RC.PF, "preflight_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(RC.PF, "check_frozen", lambda pf: (True, []))
    monkeypatch.setattr(RC.WC, "authorization_state",
                        lambda: {"authorized": True, "why_not": []})
    monkeypatch.setattr(RC.BUNDLE, "state",
                        lambda: {"authorized": True, "why_not": []})

    orders = [{"orderId": 1, "clientOrderId": "seed", "symbol": SYMBOL,
               "side": "buy", "outcome": f["leg"].lower(), "status": "filled",
               "price": f["price"], "quantity": f["qty"],
               "filledQuantity": f["qty"]} for f in fills]

    async def _auth_state(sym):
        return {"symbol": sym, "fills": list(fills), "open_orders": [],
                "orders_seen": orders, "trades": [], "terminal_seen": [],
                "order_history_raw": {"readable": True},
                "active_raw": {"ok": True}}
    monkeypatch.setattr(RC, "authoritative_state", _auth_state)

    async def _positions(**k):
        return {"path": "/v1/prediction-markets/positions", "rows": rows,
                "status": 200 if ok else 500, "ok": ok,
                "why": None if ok else "positions read returned 500"}
    monkeypatch.setattr(RC.MD, "positions", _positions)
    monkeypatch.setattr(L, "consume", spy)
    return reached


def _go(tmp_path, envelope=LE.DEFAULT):
    return _run(RC.main_async("AUTH-TEST", state_dir=str(tmp_path),
                              ledger_path=str(tmp_path / "led.jsonl"),
                              out_path=str(tmp_path / "out.json"),
                              envelope=envelope))


# ------------------------------------------------- the envelope reaches through

def test_the_default_is_still_the_shadow_envelope(tmp_path, monkeypatch):
    reached = _arm(monkeypatch, tmp_path, fills=[], rows=[])
    rep = _go(tmp_path)
    assert reached == ["AUTH-TEST"], rep.get("why")
    assert rep["envelope_name"] == LE.SHADOW_Q001.name
    assert rep["envelope"]["action_size"] == "0.01"


def test_live_q1_is_carried_into_the_report_and_the_driver(tmp_path,
                                                           monkeypatch):
    reached = _arm(monkeypatch, tmp_path, fills=[], rows=[])
    rep = _go(tmp_path, envelope=LE.LIVE_Q1)
    assert reached == ["AUTH-TEST"], rep.get("why")
    assert rep["envelope_name"] == LE.LIVE_Q1.name
    assert rep["envelope_sha"] == LE.LIVE_Q1.sha()
    assert rep["envelope"]["action_size"] == "1"
    assert rep["envelope"]["max_committed_usd"] == "2.50"
    assert rep["envelope"]["max_residual_usd"] == "2.00"


def test_the_cli_refuses_an_envelope_it_does_not_know(capsys):
    with pytest.raises(SystemExit):
        RC.main(["--auth-id", "x", "--envelope", "q100"])


def test_the_cli_maps_the_flag_to_the_reviewed_envelopes():
    import inspect
    src = inspect.getsource(RC.main)
    assert '"shadow_q001": LE.SHADOW_Q001' in src
    assert '"live_q1": LE.LIVE_Q1' in src
    assert 'default="shadow_q001"' in src


# ------------------------------------------------------- the position gate

def test_an_unreadable_position_read_refuses_before_the_latch(tmp_path,
                                                              monkeypatch):
    reached = _arm(monkeypatch, tmp_path, fills=[], rows=None, ok=False)
    rep = _go(tmp_path, envelope=LE.LIVE_Q1)
    assert reached == [], "the latch must not be reached"
    assert rep["state"] == WC.DO_NOT_TRADE
    assert rep["position_gate"]["reconciliation"]["verdict"] == PL.UNREADABLE


def test_a_disagreeing_venue_position_refuses_before_the_latch(tmp_path,
                                                               monkeypatch):
    """Our fills say we hold NO 0.01; the venue says flat. Nothing is sent."""
    reached = _arm(monkeypatch, tmp_path,
                   fills=[{"leg": "NO", "qty": "0.01", "price": "0.14"}],
                   rows=[])
    rep = _go(tmp_path, envelope=LE.LIVE_Q1)
    assert reached == []
    assert rep["state"] == WC.DO_NOT_TRADE
    assert rep["position_gate"]["reconciliation"]["verdict"] == PL.DISAGREE


def test_an_agreeing_venue_position_lets_the_run_reach_the_latch(tmp_path,
                                                                 monkeypatch):
    reached = _arm(monkeypatch, tmp_path,
                   fills=[{"leg": "NO", "qty": "0.01", "price": "0.14"}],
                   rows=[{"symbol": SYMBOL, "outcome": "no",
                          "totalQuantity": "0.01"}])
    rep = _go(tmp_path, envelope=LE.LIVE_Q1)
    assert reached == ["AUTH-TEST"], rep.get("why")
    g = rep["position_gate"]["reconciliation"]
    assert g["verdict"] == PL.AGREE and g["tradeable"] is True


def test_the_gate_records_which_representation_the_venue_used(tmp_path,
                                                              monkeypatch):
    """The open A/B/C question is answered as a by-product of the run."""
    _arm(monkeypatch, tmp_path, fills=[], rows=[])
    rep = _go(tmp_path, envelope=LE.LIVE_Q1)
    g = rep["position_gate"]["reconciliation"]
    assert g["venue_representation"] == PL.FLAT_NO_ROWS
    assert g["implies_model"]
    assert g["matched_is_verified"] is False


def test_the_report_is_written_even_when_the_gate_refuses(tmp_path,
                                                          monkeypatch):
    _arm(monkeypatch, tmp_path, fills=[], rows=None, ok=False)
    out = tmp_path / "out.json"
    _run(RC.main_async("AUTH-TEST", state_dir=str(tmp_path),
                       ledger_path=str(tmp_path / "led.jsonl"),
                       out_path=str(out), envelope=LE.LIVE_Q1))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["state"] == WC.DO_NOT_TRADE
    assert saved["position_gate"]["read"]["ok"] is False


def test_the_positions_reader_is_fail_closed_on_every_shape():
    from src.gemini_execution import metadata as MD
    assert MD.POSITIONS_PATH == "/v1/prediction-markets/positions"
    # an unreadable read must never present as an empty account
    r = PL.venue_signed(None, SYMBOL)
    assert r["representation"] == PL.UNREADABLE and r["signed"] is None


# ------------------------------------------------- the bundle covers the new code

def test_the_bundle_covers_the_q1_safety_modules():
    from src.gemini_execution import bundle as BUN
    for m in ("live_envelope.py", "outstanding_ledger.py",
              "position_ledger.py"):
        assert m in BUN.MEMBERS, m
        assert m in BUN.EXPECTED_AUTH_LINES, m
    assert len(BUN.bundle_sha()) == 64
