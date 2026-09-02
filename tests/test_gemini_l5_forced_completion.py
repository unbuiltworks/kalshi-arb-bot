"""Tests for the forced-completion risk overlay (issue #167 next config).

Covers the requested set: 180s horizon, immediate complement despite modal
EXTEND, fee-safe >=1c pair bound, no same-side extension, residual
fail-closed, disarmed default — plus a full DISARMED end-to-end simulation
of the campaign with all venue I/O faked, which doubles as the example
trace (captured emits printed on -s).
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D

import pytest

from src import gemini_l5_dynamic_q1 as owner
from src import gemini_l5_pair_q1 as pair
from src import gemini_l5_v1_campaign as camp
from src.gemini_l5_ladder_q1 import edge


def test_disarmed_default_everywhere(monkeypatch):
    for env in (camp.POSTURE_ENV, "GEMINI_L5_V1_POLICY", "GEMINI_L5_V1_DRIVER",
                owner.LIVE_ENV, owner.SID_ENV, owner.ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    assert camp.forced_completion_enabled() is False
    assert owner.armed() is False
    # overlay off -> post-fill action is pure v1
    assert camp.post_fill_action({"allow": True, "modal_relation": "extend"}) \
        == "ABSTAIN_EXTEND_CAPPED"


def test_first_wait_180s_under_overlay(monkeypatch):
    monkeypatch.delenv(camp.POSTURE_ENV, raising=False)
    assert camp.effective_first_wait(45.0) == 45.0
    monkeypatch.setenv(camp.POSTURE_ENV, "1")
    assert camp.effective_first_wait(45.0) == 180.0
    monkeypatch.setenv(camp.FC_FIRST_WAIT_ENV, "120")
    assert camp.effective_first_wait(45.0) == 120.0


def test_overlay_completes_despite_modal_extend(monkeypatch):
    monkeypatch.setenv(camp.POSTURE_ENV, "1")
    extend_verdict = {"allow": True, "modal_relation": "extend"}
    assert camp.decide_after_fill(extend_verdict) == "ABSTAIN_EXTEND_CAPPED"
    assert camp.post_fill_action(extend_verdict) == "COMPLETE"
    # even off-distribution does not suppress the economic complement
    assert camp.post_fill_action({"allow": False}) == "COMPLETE"


def test_fee_safe_pair_bound_holds_under_overlay():
    for exec_px in ("0.35", "0.50", "0.62"):
        px = camp.complement_price("YES", D(exec_px), D("0.01"))
        assert px is not None
        assert D("0.20") <= px <= D("0.80")
        assert edge(D(exec_px), px) >= D("0.01")


def test_no_same_side_extension_path_exists():
    import inspect
    src = inspect.getsource(camp)
    # the only order placements are the opener and the opposite-side
    # complement; the complement side is always the opposite outcome
    assert src.count("await place_order_durable(") == 2
    assert 'comp_side = "NO" if spec["side"] == "YES" else "YES"' in src
    # no code constructs a second same-side order spec
    assert src.count('ladder.cid(sid, chosen["side"]') == 1


def test_residual_fail_closed_unchanged():
    assert owner.classify_cycle_status(camp.STATUS_RESIDUAL_HOLD, D("1")) \
        == "RESIDUAL_HALT"
    assert owner.classify_cycle_status(camp.STATUS_MATCHED, D("0")) == "MATCHED"


# ---------------- end-to-end DISARMED simulation (example trace) ----------------

class _FakeMaker:
    """Venue double: accepts orders; the FIRST placed order (the rung-0
    opener) gets a fill event."""

    def __init__(self):
        self.placed = []
        self.events = asyncio.Queue()

    async def connect(self):
        return None

    async def close(self):
        return None

    async def request(self, method, payload, timeout=None):
        self.placed.append(payload)
        if len(self.placed) == 1:
            await self.events.put({"c": payload["clientOrderId"],
                                   "Z": "1", "X": "FILLED"})
        return {"status": 200}


def test_end_to_end_disarmed_simulation_matched(monkeypatch, tmp_path, capsys):
    """Full campaign run with venue I/O faked: open YES -> fill @ exec 0.48
    -> v1 says EXTEND -> overlay posts NO complement at fee-safe bound ->
    complement fills -> MATCHED_V1_COMPLETION with strict accounting."""
    monkeypatch.setenv(camp.POSTURE_ENV, "1")
    monkeypatch.setenv("GEMINI_L5_V1_POLICY", "1")
    monkeypatch.setenv(pair.LIVE_ENV, "YES")
    monkeypatch.setenv(pair.SID_ENV, "simtrace")
    monkeypatch.setenv(pair.ARM_ENV, "simtrace")           # simulated arm only
    monkeypatch.setenv(pair.STATE_ENV, str(tmp_path / "cycle.json"))
    monkeypatch.setattr(camp, "REEVAL_INTERVAL_SECONDS", 0.01)

    fills_state = {"n": 0}
    fake = _FakeMaker()

    class Event:
        symbol = "GEMI-BTC05MSIM-UP"
        ticker = "BTC05MSIM"
        seconds_to_expiry = 240.0

    async def fake_terms():
        return True

    async def fake_usd():
        return D("39")

    async def fake_discover():
        return Event()

    async def fake_top(symbol, *a, **k):
        return D("0.48"), D("250"), D("0.50"), D("250")

    async def fake_find_row(cid, symbol):
        return {"clientOrderId": cid, "filledQuantity": "0"}

    async def fake_cancel(maker, spec, symbol):
        return {}

    async def fake_fills(specs, symbol):
        fills_state["n"] += 1
        out = {}
        for s in specs:
            if s["id"].endswith("0"):            # rung-0 opener: filled @0.48
                s["row"] = {"clientOrderId": s["id"], "filledQuantity": "1",
                            "avgExecutionPrice": "0.48"}
                out[s["id"]] = D("1")
            elif fills_state["n"] >= 3:          # complement fills later
                s["row"] = {"clientOrderId": s["id"], "filledQuantity": "1",
                            "avgExecutionPrice": str(s["price"])}
                out[s["id"]] = D("1")
            else:
                out[s["id"]] = D("0")
        return out

    async def fake_exact_active(cid, symbol):
        return False

    def fake_evaluate(price, tte, residual):
        # frozen-gate stand-in that insists on EXTEND post-fill
        if abs(residual) < 1:
            return {"allow": True, "state": "sim|flat", "modal_relation": "open",
                    "model_sha256_16": "simsha", "rel_probs": {"open": 1.0}}
        return {"allow": True, "state": "sim|imb1-10",
                "modal_relation": "extend", "model_sha256_16": "simsha",
                "rel_probs": {"extend": 0.53, "complete": 0.47}}

    monkeypatch.setattr(camp.pair, "_gemini_terms_ok", fake_terms)
    monkeypatch.setattr(camp.pair, "_gemini_available_usd", fake_usd)
    monkeypatch.setattr(camp.pair, "discover_eth5m", fake_discover)
    monkeypatch.setattr(camp.pair, "_exact_active", fake_exact_active)
    monkeypatch.setattr(camp.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(camp.ladder, "cancel", fake_cancel)
    monkeypatch.setattr(camp.ladder, "fills", fake_fills)
    monkeypatch.setattr(camp.ladder.base, "_find_row", fake_find_row)
    monkeypatch.setattr(camp.ladder.base, "GeminiMakerSession", lambda: fake)
    monkeypatch.setattr(camp.v1_policy, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        camp.v1_policy, "gate_candidate",
        lambda b, a, t: fake_evaluate(float((D(str(b)) + D(str(a))) / 2), t, 0))

    asyncio.run(camp.run(min_edge=D("0.01"), first_wait=45.0,
                         complement_wait=60.0, min_tte=105.0))

    state = json.loads((tmp_path / "cycle.json").read_text())
    assert state["status"] == "MATCHED_V1_COMPLETION"
    assert state["posture"] == camp.POSTURE_LABEL
    assert state["first_wait_s"] == 180.0                  # overlay horizon
    assert state["first_fill_exec_price"] == "0.48"
    # opener was NO @0.50 (queue tiebreak); complement is the second order,
    # on the opposite side, priced off the EXEC price at the fee-safe bound
    assert state["orders"][0]["side"] == "NO"
    comp = state["orders"][1]
    assert comp["side"] == "YES"
    assert edge(D(comp["price"]), D("0.48")) >= D("0.01")
    # v1's extend verdict was logged but did not suppress the complement
    post = [d for d in state["policy_decisions"] if d["stage"] == "POST_FILL"]
    assert post and post[0]["decision"] == "COMPLETE"
    assert post[0]["v1_pure_decision"] == "ABSTAIN_EXTEND_CAPPED"
    assert state["matched_qty"] == "1" and state["residual_qty"] == "0"
    assert state["realized_pnl"] is None
    assert state["settlement_required"] is True
    trace = capsys.readouterr().out
    assert "V1_COMPLEMENT_POSTED" in trace and "FORCED_COMPLETION" in trace
    import os
    if os.getenv("SIM_TRACE_OUT"):
        with open(os.environ["SIM_TRACE_OUT"], "w") as fh:
            fh.write(trace)
