"""Transport-free replay tests for the R2 execution-validation runner.

NO NETWORK, NO CREDENTIALS, NO VENUE. Every venue call is stubbed, so these
tests can prove the runner's behaviour without an artificial order ever
reaching Gemini -- which is the owner's explicit condition for building it.

Scenarios required before the first live submission, each below:
  process restart              disappearing offer        risen offer
  ambiguous send               filled order              unfilled / cancel
  settlement reconciliation    gate arrival while idle
  kill between slot fsync and venue response (no duplicate)
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal as D

import pytest

from src.gemini_execution import (arming, cohort, metadata, payload,
                                  r2_exec_budget as B,
                                  r2_exec_probe as P,
                                  r2_exec_runner as R, transport)

CID = "COHORT-TEST-RUNNER"
TICKER = "BTC05M2610031245"
SYMBOL = "GEMI-%s-UP" % TICKER

GOOD_TERMS = {"readable": True, "missing_fields": [],
              "quantityMinimum": "0.01", "quantityIncrement": "0.01",
              "priceMinimum": "0.01", "priceIncrement": "0.01",
              "contract_status": "active", "market_state": "open",
              "strike": "84875.6", "expiry": "2026-10-03T12:45:00.000Z"}


def _ticks(now, n=400, px=85000.0):
    """One tick per second, so sigma has >= 20 consecutive-second returns."""
    out = []
    for i in range(n):
        out.append((now - n + i, px + (0.5 if i % 2 else -0.5)))
    return out


def _depth(ask=None, bid=None, qty="750"):
    a = [{"price": ask, "qty": qty}] if ask is not None else []
    b = [{"price": bid, "qty": qty}] if bid is not None else []
    return {"asks": {"levels": a}, "bids": {"levels": b}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    pred = tmp_path / "hist.jsonl"
    pred.write_text('{"kind":"PLACE"}\n')
    monkeypatch.setattr(cohort, "HISTORICAL_LEDGER", str(pred))
    monkeypatch.setattr(cohort, "COHORT_ROOT", root)
    monkeypatch.setattr(B.cohort, "COHORT_ROOT", root)
    B.create(CID, root=root)

    # bind every budget entry point to this temp root
    for fn in ("may_probe", "consume_slot", "finish_market", "state",
               "expired", "finished_markets", "realized_principal_loss",
               "consumed", "remaining", "record_settlement"):
        orig = getattr(B, fn)

        def mk(o=orig):
            def w(*a, **k):
                k.setdefault("root", root)
                return o(*a, **k)
            return w
        monkeypatch.setattr(B, fn, mk())

    monkeypatch.setattr(P, "PROBE_WINDOWS", tmp_path / "probe_windows.json")
    monkeypatch.setattr(P, "HALT_FILE", tmp_path / "HALT", raising=False)
    import src.gemini_execution.semantics_probe as SP
    monkeypatch.setattr(SP, "HALT_FILE", tmp_path / "HALT")

    # a strike that published 60 s ago, expiring in 120 s
    now = 1791029000.0
    monkeypatch.setattr(metadata, "terms_for", lambda s: dict(GOOD_TERMS))
    monkeypatch.setattr(metadata, "resolution_for",
                        lambda s, **k: {"expiryDate":
                                        "2026-10-03T12:45:00.000Z"})
    monkeypatch.setattr(metadata, "event_ticker", lambda s: TICKER)
    monkeypatch.setattr(metadata, "contract_depth",
                        lambda s, **k: _depth(ask="0.50"))

    async def _active(symbol=None):
        return {"ok": True, "orders": []}
    monkeypatch.setattr(transport, "active_orders", _active)
    monkeypatch.setattr(transport, "_orders_of",
                        lambda r: (r or {}).get("orders") or [])

    async def _cancel(*a, **k):
        return {"state": "CONFIRMED"}
    monkeypatch.setattr(transport, "cancel_and_confirm", _cancel)

    import time as _t
    monkeypatch.setenv(arming.LIVE_ENV, "YES")
    monkeypatch.setenv(arming.AUTH_ENV, arming.EXPECTED_AUTH_ID)
    monkeypatch.setenv(arming.EXPIRY_ENV,
                       _t.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   _t.gmtime(_t.time() + 3600)))
    return {"root": root, "tmp": tmp_path, "now": now}


def _expiry_now(monkeypatch, tte=120.0):
    """Pin 'now' so the contract has `tte` seconds left."""
    from datetime import datetime
    E = datetime.fromisoformat("2026-10-03T12:45:00+00:00").timestamp()
    t = E - tte
    monkeypatch.setattr(P.time, "time", lambda: t)
    return t


def _place(result):
    async def _p(**k):
        _p.calls.append(k)
        return dict(result, request={"side": "buy", "outcome": k["outcome"],
                                     "price": k["price"],
                                     "quantity": k["quantity"]})
    _p.calls = []
    return _p


ACK_FILLED = {"ok": True, "classification": "ACCEPTED", "status": 201,
              "body": {"orderId": 1, "status": "filled", "filledQuantity": "1",
                       "remainingQuantity": "0", "avgExecutionPrice": "0.5",
                       "price": "0.5", "quantity": "1", "side": "buy",
                       "outcome": "yes"}}
ACK_UNFILLED = {"ok": True, "classification": "ACCEPTED", "status": 201,
                "body": {"orderId": 2, "status": "live",
                         "filledQuantity": "0", "remainingQuantity": "1",
                         "price": "0.5", "quantity": "1", "side": "buy",
                         "outcome": "yes"}}
ACK_AMBIGUOUS = {"ok": False, "classification": transport.AMBIGUOUS,
                 "status": None, "body": None}


# ---------------------------------------------------------------- qualification

def test_qualifies_and_freezes_price_and_depth(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    q = P.qualify(SYMBOL, TICKER, _ticks(t), t)
    assert q["state"] == "QUALIFIED", q
    assert q["frozen_price"] == 0.5 and q["frozen_depth"] == 750.0
    assert q["favored_side"] in ("UP", "DOWN")
    assert q["z"] >= P.RULE["Z_TRIGGER"]


def test_pre_strike_is_refused(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(metadata, "terms_for",
                        lambda s: dict(GOOD_TERMS, strike=None))
    q = P.qualify(SYMBOL, TICKER, _ticks(t), t)
    assert q["state"] == "NOT_QUALIFIED" and "PRE_STRIKE" in q["why"]


def test_terms_failure_is_refused(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(metadata, "terms_for",
                        lambda s: dict(GOOD_TERMS, market_state="closed"))
    q = P.qualify(SYMBOL, TICKER, _ticks(t), t)
    assert q["state"] == "NOT_QUALIFIED" and "TERMS_READ_FAILED" in q["why"]


# ------------------------------------------------------------- filled order

def test_filled_order_records_the_full_comparison(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "FILLED", rec.get("why")
    assert len(pl.calls) == 1 and pl.calls[0]["quantity"] == "1"
    assert pl.calls[0]["allow_taker"] is True
    assert rec["comparison"]["modeled_price"] == 0.5
    assert rec["comparison"]["actual_fill_price"] == "0.5"
    assert rec["comparison"]["price_slippage"] == 0.0
    assert rec["comparison"]["obtained_quantity"] == "1"
    assert rec["reconciliation"]["anomaly"] is False
    assert rec["realized"].startswith("NOT_REALIZED")
    assert B.consumed(CID) == 1
    assert B.finished_markets(CID)[TICKER] == "SUBMITTED"


def test_fee_absence_is_reported_not_filled_in(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(transport, "place", _place(ACK_FILLED))
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["fee_observed"] is None
    assert "NOT confirmed" in rec["fee_note"]


# ------------------------------------------------------- unfilled / cancel

def test_unfilled_order_is_not_a_trade(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(transport, "place", _place(ACK_UNFILLED))
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "ACCEPTED_UNFILLED"
    assert rec["filled_quantity"] == "0"
    assert rec["reconciliation"]["anomaly"] is False
    assert B.consumed(CID) == 1, "an accepted order spends its slot"


# ----------------------------------------------------- disappearing / risen

def test_disappearing_offer_records_missed_and_spends_no_slot(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    seq = [_depth(ask="0.50"), _depth(ask=None)]       # qualify, then gone
    monkeypatch.setattr(metadata, "contract_depth",
                        lambda s, **k: seq.pop(0) if seq else _depth(ask=None))
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "MISSED_NOT_EXECUTABLE"
    assert "DISAPPEARED" in rec["why"]
    assert pl.calls == [], "nothing may reach the venue"
    assert B.consumed(CID) == 0, "MISSED spends no slot"
    assert B.finished_markets(CID)[TICKER] == "MISSED_NOT_EXECUTABLE"


def test_risen_offer_is_not_chased(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    seq = [_depth(ask="0.50"), _depth(ask="0.60")]
    monkeypatch.setattr(metadata, "contract_depth",
                        lambda s, **k: seq.pop(0) if seq else _depth(ask="0.60"))
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "MISSED_NOT_EXECUTABLE"
    assert "ROSE_ABOVE" in rec["why"] and pl.calls == []
    assert B.consumed(CID) == 0


def test_a_missed_market_is_never_revisited(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: _depth(ask=None))
    r1 = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID, cb_ticks=_ticks(t)))
    assert r1["state"] == "NOT_EXECUTABLE"
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: _depth(ask="0.50"))
    r2 = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID, cb_ticks=_ticks(t)))
    assert r2["state"] == "BUDGET_REFUSED"
    assert "MARKET_ALREADY_FINISHED" in r2["why"]


# ---------------------------------------------------------- ambiguous send

def test_ambiguous_send_spends_the_slot_and_halts(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(transport, "place", _place(ACK_AMBIGUOUS))
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "SUBMISSION_UNKNOWN"
    assert B.consumed(CID) == 1, "ambiguity consumes the slot"
    assert "AMBIGUOUS" in rec["halt"]
    from src.gemini_execution.semantics_probe import halted as h
    assert h() is not None, "a HALT must block every later probe"


def test_halt_blocks_the_next_probe(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(transport, "place", _place(ACK_AMBIGUOUS))
    asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID, cb_ticks=_ticks(t)))
    monkeypatch.setattr(metadata, "event_ticker", lambda s: "BTC05M2610031250")
    rec = asyncio.run(P.probe(symbol="GEMI-BTC05M2610031250-UP",
                              cohort_id=CID, cb_ticks=_ticks(t)))
    assert rec["state"] == "HALTED"


# ------------------------------------------------- semantic contradictions

def test_fill_above_the_frozen_limit_halts(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    bad = {"ok": True, "classification": "ACCEPTED", "status": 201,
           "body": dict(ACK_FILLED["body"], avgExecutionPrice="0.62")}
    monkeypatch.setattr(transport, "place", _place(bad))
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["reconciliation"]["kind"] == "FILL_ABOVE_LIMIT"
    assert rec["halt"]


def test_mapping_contradiction_halts(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    bad = {"ok": True, "classification": "ACCEPTED", "status": 201,
           "body": dict(ACK_FILLED["body"], outcome="no")}
    monkeypatch.setattr(transport, "place", _place(bad))
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["reconciliation"]["kind"] == "MAPPING_CONTRADICTION"
    assert rec["halt"]


def test_price_normalisation_is_not_a_contradiction(env, monkeypatch):
    """The venue echoes 0.50 as "0.5". That must NOT halt the cohort."""
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(transport, "place", _place(ACK_FILLED))
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["reconciliation"]["anomaly"] is False


# ------------------------------------------- restart / duplicate protection

def test_restart_recovers_rather_than_initialises(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    monkeypatch.setattr(transport, "place", _place(ACK_FILLED))
    asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID, cb_ticks=_ticks(t)))
    rec = asyncio.run(R.recover(CID))
    assert rec["consumed_recovered"] == 1
    assert rec["remaining"] == 9
    assert rec["initialized_rather_than_recovered"] is False
    assert TICKER in rec["markets_finished"]


def test_kill_between_slot_fsync_and_venue_response_cannot_duplicate(
        env, monkeypatch):
    """The decisive safety property. The slot is fsynced and the market marked
    SUBMITTED before the venue is touched, so a process killed while the
    venue response is in flight restarts UNABLE to send a second order."""
    t = _expiry_now(monkeypatch)

    class Killed(BaseException):
        pass

    async def die(**k):
        die.calls.append(k)
        raise Killed("process killed while the venue response was in flight")
    die.calls = []
    monkeypatch.setattr(transport, "place", die)

    with pytest.raises(Killed):
        asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID, cb_ticks=_ticks(t)))
    assert len(die.calls) == 1
    # the slot is spent and the market finished, on disk, before the send
    assert B.consumed(CID) == 1
    assert B.finished_markets(CID)[TICKER] == "SUBMITTED"

    # "restart": a fresh probe for the SAME market is refused outright
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    again = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                                cb_ticks=_ticks(t)))
    assert again["state"] == "BUDGET_REFUSED"
    assert "MARKET_ALREADY_FINISHED" in again["why"]
    assert pl.calls == [], "no duplicate order may ever be sent"


def test_account_not_flat_refuses_without_spending_a_slot(env, monkeypatch):
    t = _expiry_now(monkeypatch)

    async def busy(symbol=None):
        return {"ok": True, "orders": [{"orderId": 9}]}
    monkeypatch.setattr(transport, "active_orders", busy)
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "REFUSED" and "ACCOUNT_NOT_FLAT" in rec["why"]
    assert B.consumed(CID) == 0 and pl.calls == []


def test_unreadable_account_is_not_an_empty_one(env, monkeypatch):
    t = _expiry_now(monkeypatch)

    async def broken(symbol=None):
        return {"ok": False}
    monkeypatch.setattr(transport, "active_orders", broken)
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "REFUSED"
    assert "UNREADABLE" in rec["why"]
    assert B.consumed(CID) == 0


# ------------------------------------------------- settlement reconciliation

def test_settlement_reconciliation_only_counts_confirmed_cashflow(env):
    B.record_settlement(CID, TICKER, slot=0, realized_principal_net="-0.50",
                        cashflow_confirmed=False, evidence="resolution only")
    assert B.realized_principal_loss(CID) == D("0")
    B.record_settlement(CID, TICKER, slot=0, realized_principal_net="-0.50",
                        cashflow_confirmed=True,
                        evidence="positions FLAT_NO_ROWS")
    assert B.realized_principal_loss(CID) == D("0.50")
    assert B.expired(CID) is None


def test_eight_dollars_of_confirmed_loss_stops_new_probes(env, monkeypatch):
    for i in range(8):
        B.record_settlement(CID, "M%d" % i, slot=i,
                            realized_principal_net="-1.00",
                            cashflow_confirmed=True, evidence="x")
    t = _expiry_now(monkeypatch)
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t)))
    assert rec["state"] == "BUDGET_REFUSED"
    assert "REALIZED_LOSS_STOP" in rec["why"] and pl.calls == []


# -------------------------------------------------- gate arrival while idle

def test_gate_arrival_while_idle_stops_the_runner(env, monkeypatch):
    async def go():
        return await R.run(cohort_id=CID, outdir=str(env["tmp"] / "o"),
                           max_runtime_s=5.0, dry_run=True,
                           stat_gate_fn=lambda: 100)
    # no Coinbase task needed: the gate is checked before the tick freshness
    monkeypatch.setitem(R.IEC, "coinbase_task",
                        lambda sink, stop: asyncio.sleep(0.01))
    s = asyncio.run(go())
    assert "STATISTICAL_GATE_REACHED" in s["stopped"]
    assert s["probes"] == []


def test_gate_below_100_does_not_stop_the_runner(env, monkeypatch):
    monkeypatch.setitem(R.IEC, "coinbase_task",
                        lambda sink, stop: asyncio.sleep(0.01))
    s = asyncio.run(R.run(cohort_id=CID, outdir=str(env["tmp"] / "o2"),
                          max_runtime_s=0.2, dry_run=True,
                          stat_gate_fn=lambda: 99))
    assert s["stopped"] == "MAX_RUNTIME"


# ------------------------------------------------------ runner discipline

def test_runner_defaults_to_dry_run_and_sends_nothing(env, monkeypatch):
    monkeypatch.setitem(R.IEC, "coinbase_task",
                        lambda sink, stop: asyncio.sleep(0.01))
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    s = asyncio.run(R.run(cohort_id=CID, outdir=str(env["tmp"] / "o3"),
                          max_runtime_s=0.2, dry_run=True))
    assert s["dry_run"] is True and pl.calls == []
    assert B.consumed(CID) == 0


def test_runner_owns_no_signal_logic_and_one_order_path():
    import inspect
    src = inspect.getsource(R)
    assert src.count("PROBE.probe(") == 1
    assert "transport.place" not in src
    for bad in ("def favored_side", "def z_of", "def qualifies",
                "Z_TRIGGER =", "math.log", "def extend", "def flip",
                "def complement", "def reprice", "def chase"):
        assert bad not in src, bad


def test_runner_only_discovers_post_strike_windows_with_published_strike():
    import inspect
    src = inspect.getsource(R.discover)
    assert "0 < tte <= 300" in src
    assert 'st.get("value")' in src
    assert 'availableAt' in src and "now < A" in src


# ------------------------------------------- durability beyond the host

def test_a_failing_durability_hook_stops_the_probe_with_the_slot_spent(
        env, monkeypatch):
    """On an ephemeral host an fsync is not enough. If the slot cannot be made
    durable beyond the disk, NOTHING may be sent -- and the slot stays spent,
    so a retry on a fresh disk cannot resurrect it."""
    t = _expiry_now(monkeypatch)
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t),
                              slot_hook=lambda c, tk, s: {"ok": False,
                                                          "why": "push failed"}))
    assert rec["state"] == "REFUSED_SLOT_NOT_DURABLE"
    assert pl.calls == [], "nothing may reach the venue"
    assert B.consumed(CID) == 1, "the slot stays SPENT"


def test_a_raising_durability_hook_is_also_fail_closed(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    pl = _place(ACK_FILLED)
    monkeypatch.setattr(transport, "place", pl)

    def boom(c, tk, s):
        raise RuntimeError("git unreachable")
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t), slot_hook=boom))
    assert rec["state"] == "REFUSED_SLOT_NOT_DURABLE"
    assert "RuntimeError" in rec["slot_durable"]["error"]
    assert pl.calls == []


def test_the_hook_runs_before_the_venue_is_touched(env, monkeypatch):
    t = _expiry_now(monkeypatch)
    order = []

    async def pl(**k):
        order.append("place")
        return dict(ACK_FILLED, request={"side": "buy", "outcome": k["outcome"],
                                         "price": k["price"],
                                         "quantity": k["quantity"]})
    monkeypatch.setattr(transport, "place", pl)

    def hook(c, tk, s):
        order.append("durable")
        return {"ok": True}
    rec = asyncio.run(P.probe(symbol=SYMBOL, cohort_id=CID,
                              cb_ticks=_ticks(t), slot_hook=hook))
    assert rec["state"] == "FILLED"
    assert order == ["durable", "place"], order


def test_live_without_a_durable_branch_is_refused():
    import pytest as _p
    with _p.raises(SystemExit) as e:
        R.main(["--cohort", "X", "--live"])
    assert "durable-branch" in str(e.value)
