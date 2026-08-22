from decimal import Decimal

import pytest

from src import sports_kp_live_combo_supervisor_v29 as v29
from src import sports_mve_rfq_feedback_store as store

D = Decimal


def _row(obs=1, quoted=1, best="-0.05", last="-0.05"):
    return {
        "observations": obs,
        "quoted": quoted,
        "best_lock": D(best),
        "last_lock": D(last),
    }


def test_feedback_snapshot_normalizes_without_aliasing():
    source = {"NFLSPREAD:yes+NFLTOTAL:no": _row()}
    snap = store.snapshot(source)
    source["NFLSPREAD:yes+NFLTOTAL:no"]["observations"] = 99
    assert snap["NFLSPREAD:yes+NFLTOTAL:no"]["observations"] == 1
    assert snap["NFLSPREAD:yes+NFLTOTAL:no"]["best_lock"] == D("-0.05")


@pytest.mark.asyncio
async def test_generate_hydrates_side_aware_feedback_and_persists_only_cycle_delta(monkeypatch):
    signature = "NFLSPREAD:yes+NFLTOTAL:no"
    v29.v23._FEEDBACK.clear()
    calls = {}

    async def fake_load():
        return {signature: _row(obs=4, quoted=2, best="-0.049", last="-0.08")}

    async def fake_generate():
        fb = v29.v23._FEEDBACK[signature]
        assert fb["observations"] == 4
        assert fb["best_lock"] == D("-0.049")
        fb["observations"] += 1
        fb["quoted"] += 1
        fb["last_lock"] = D("-0.03")
        fb["best_lock"] = D("-0.03")
        return {"ranked": [], "status": "ok"}

    async def fake_merge(before, after):
        calls["before"] = before
        calls["after"] = after
        return 1

    monkeypatch.setattr(v29.feedback_store, "load_feedback", fake_load)
    monkeypatch.setattr(v29.feedback_store, "merge_feedback_delta", fake_merge)
    monkeypatch.setattr(v29, "_ORIGINAL_GENERATE", fake_generate)

    result = await v29._durable_feedback_generate()
    assert result["status"] == "ok"
    assert calls["before"][signature]["observations"] == 4
    assert calls["after"][signature]["observations"] == 5
    assert calls["after"][signature]["quoted"] == 3
    assert calls["after"][signature]["best_lock"] == D("-0.03")


@pytest.mark.asyncio
async def test_load_failure_keeps_existing_process_local_feedback(monkeypatch):
    signature = "NFLGAME:no+NFLTOTAL:yes"
    v29.v23._FEEDBACK.clear()
    v29.v23._FEEDBACK[signature].update(_row(obs=3, quoted=1, best="-0.20", last="-0.25"))

    async def broken_load():
        raise RuntimeError("db unavailable")

    async def fake_generate():
        assert v29.v23._FEEDBACK[signature]["observations"] == 3
        return {"ranked": []}

    async def fake_merge(before, after):
        return 0

    monkeypatch.setattr(v29.feedback_store, "load_feedback", broken_load)
    monkeypatch.setattr(v29.feedback_store, "merge_feedback_delta", fake_merge)
    monkeypatch.setattr(v29, "_ORIGINAL_GENERATE", fake_generate)

    await v29._durable_feedback_generate()
    assert v29.v23._FEEDBACK[signature]["best_lock"] == D("-0.20")


def test_selected_side_orientation_remains_distinct_feedback_key():
    yes_no = [
        {"event_ticker": "NFLSPREAD-GAME1", "side": "yes"},
        {"event_ticker": "NFLTOTAL-GAME1", "side": "no"},
    ]
    no_yes = [
        {"event_ticker": "NFLSPREAD-GAME1", "side": "no"},
        {"event_ticker": "NFLTOTAL-GAME1", "side": "yes"},
    ]
    assert v29.v23._family_signature_from_legs(yes_no) != v29.v23._family_signature_from_legs(no_yes)
