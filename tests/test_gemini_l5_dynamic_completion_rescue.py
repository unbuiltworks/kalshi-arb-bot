import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_l5_pair_q1 as m


class DummyMaker:
    pass


def _event():
    return SimpleNamespace(symbol="GEMI-ETH05M-TEST-UP", seconds_to_expiry=180.0)


def _specs():
    return [
        {"side": "YES", "price": D("0.47"), "id": "yes0", "row": {"orderId": "1"}},
        {"side": "NO", "price": D("0.49"), "id": "no0", "row": {"orderId": "2"}},
    ]


def test_rescue_cancel_verifies_inactive_then_reposts_and_matches(monkeypatch):
    specs = _specs()
    state = {"orders": []}
    sequence = []

    async def fake_fills(current_specs, symbol):
        out = {s["id"]: D("0") for s in current_specs}
        out["yes0"] = D("1")
        if len(current_specs) > 2:
            out[current_specs[-1]["id"]] = D("1")
        return out

    async def fake_top(symbol, attempts=5, delay_s=0.6):
        return D("0.48"), D("100"), D("0.51"), D("100")

    async def fake_cancel(maker, spec, symbol):
        sequence.append(("cancel", spec["id"]))
        return {"ok": True}

    async def fake_active(client_id, symbol):
        sequence.append(("active_check", client_id))
        return False

    async def fake_post(maker, *, event, sid, missing, price, sequence: int):
        assert sequence == 1
        sequence_log = globals().get("_unused")
        del sequence_log
        spec = {"side": missing, "price": D(str(price)), "id": "no1", "row": {"orderId": "3"}}
        # Use outer list without shadowing the function parameter.
        order_log.append(("post", spec["id"], str(price)))
        return spec

    order_log = sequence
    monkeypatch.setattr(m.ladder, "fills", fake_fills)
    monkeypatch.setattr(m.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m.ladder, "cancel", fake_cancel)
    monkeypatch.setattr(m, "_exact_active", fake_active)
    monkeypatch.setattr(m, "_post_replacement", fake_post)

    status = asyncio.run(m._dynamic_complement_rescue(
        DummyMaker(),
        event=_event(),
        sid="s",
        specs=specs,
        state=state,
        filled_side="YES",
        min_edge=D("0.01"),
        complement_wait=5.0,
    ))
    assert status == "MATCHED"
    assert sequence[0] == ("cancel", "no0")
    assert sequence[1] == ("active_check", "no0")
    assert sequence[2][0] == "post"
    assert sequence[2][2] == "0.50"
    assert len(specs) == 3
    assert specs[-1]["id"] == "no1"


def test_rescue_never_reposts_when_cancel_identity_remains_active(monkeypatch):
    specs = _specs()
    state = {"orders": []}
    posted = False

    async def fake_fills(current_specs, symbol):
        return {"yes0": D("1"), "no0": D("0")}

    async def fake_top(symbol, attempts=5, delay_s=0.6):
        return D("0.48"), D("100"), D("0.51"), D("100")

    async def fake_cancel(maker, spec, symbol):
        return {"ok": True}

    async def fake_active(client_id, symbol):
        return True

    async def fake_post(*args, **kwargs):
        nonlocal posted
        posted = True
        raise AssertionError("replacement posted while old exact ID remains active")

    monkeypatch.setattr(m.ladder, "fills", fake_fills)
    monkeypatch.setattr(m.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m.ladder, "cancel", fake_cancel)
    monkeypatch.setattr(m, "_exact_active", fake_active)
    monkeypatch.setattr(m, "_post_replacement", fake_post)

    status = asyncio.run(m._dynamic_complement_rescue(
        DummyMaker(),
        event=_event(),
        sid="s",
        specs=specs,
        state=state,
        filled_side="YES",
        min_edge=D("0.01"),
        complement_wait=5.0,
    ))
    assert status == "COMPLEMENT_CANCEL_AMBIGUOUS"
    assert posted is False
