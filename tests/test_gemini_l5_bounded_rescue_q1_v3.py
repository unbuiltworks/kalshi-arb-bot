import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_l5_pair_q1 as pair
from src import gemini_l5_bounded_rescue_q1 as v1
from src import gemini_l5_bounded_rescue_q1_v2 as v2
from src import gemini_l5_bounded_rescue_q1_v3 as v3


class DummyMaker:
    def __init__(self):
        self.events = asyncio.Queue()


def _event():
    return SimpleNamespace(symbol="GEMI-BTC05M-TEST-UP", seconds_to_expiry=120.0)


def _specs():
    return [
        {"side": "YES", "price": D("0.60"), "id": "yes0", "row": {"orderId": "1"}, "liquidity": "MAKER", "tif": "MOC"},
        {"side": "NO", "price": D("0.25"), "id": "no0", "row": {"orderId": "2"}, "liquidity": "MAKER", "tif": "MOC"},
    ]


def test_v3_install_removes_maker_wait_and_breakeven_repost():
    old_profit = v1.PROFIT_STAGE_SECONDS
    old_be = v1.BREAKEVEN_STAGE_SECONDS
    old_post = v2._post_breakeven_maker
    old_rescue = pair._dynamic_complement_rescue
    try:
        v3.install()
        assert v1.PROFIT_STAGE_SECONDS == 0.0
        assert v1.BREAKEVEN_STAGE_SECONDS == 0.0
        assert v2._post_breakeven_maker is v3._skip_breakeven_maker
        assert pair._dynamic_complement_rescue is v2._dynamic_complement_rescue
    finally:
        v1.PROFIT_STAGE_SECONDS = old_profit
        v1.BREAKEVEN_STAGE_SECONDS = old_be
        v2._post_breakeven_maker = old_post
        pair._dynamic_complement_rescue = old_rescue


def test_v3_first_fill_goes_cancel_then_bounded_fok_without_repost(monkeypatch):
    specs = _specs()
    sequence = []

    async def fake_fills(current_specs, symbol):
        out = {s["id"]: D("0") for s in current_specs}
        out["yes0"] = D("1")
        if any(s.get("tif") == "FOK" for s in current_specs):
            fok = [s for s in current_specs if s.get("tif") == "FOK"][-1]
            out[fok["id"]] = D("1")
        return out

    async def fake_cancel(*args, **kwargs):
        sequence.append("cancel")
        return "INACTIVE_ZERO_OR_PARTIAL"

    async def fake_top(symbol, attempts=2, delay_s=0.10):
        sequence.append("fresh_top")
        # YES filled at 60c; NO taker at 37c gives a small after-fee bounded loss.
        return D("0.63"), D("10"), D("0.65"), D("10")

    async def fake_fok(maker, *, event, sid, missing, price, sequence: int):
        assert missing == "NO"
        assert price == D("0.37")
        return {"side": "NO", "price": price, "id": "no-fok", "row": None, "liquidity": "TAKER", "tif": "FOK"}

    monkeypatch.setattr(v1, "PROFIT_STAGE_SECONDS", 0.0)
    monkeypatch.setattr(v1, "BREAKEVEN_STAGE_SECONDS", 0.0)
    monkeypatch.setattr(v1, "IOC_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(pair.ladder, "fills", fake_fills)
    monkeypatch.setattr(v2, "_cancel_current", fake_cancel)
    # Exercise the hook V3 installs: no maker replacement exists between exact
    # sibling cancellation and the bounded FOK decision.
    monkeypatch.setattr(v2, "_post_breakeven_maker", v3._skip_breakeven_maker)
    monkeypatch.setattr(pair.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(v2, "_post_emergency_fok", fake_fok)
    monkeypatch.setattr(pair, "_write_state", lambda payload: None)

    status = asyncio.run(v2._dynamic_complement_rescue(
        DummyMaker(),
        event=_event(),
        sid="v3",
        specs=specs,
        state={"orders": []},
        filled_side="YES",
        min_edge=D("0.01"),
        complement_wait=60.0,
    ))
    assert status == "MATCHED_BOUNDED_FOK"
    assert sequence == ["cancel", "fresh_top"]
    assert specs[-1]["tif"] == "FOK"


def test_live_canary_regression_60c_yes_blocks_70c_no():
    plan = v1.emergency_ioc_plan(
        filled_outcome="YES",
        filled_price=D("0.60"),
        yes_bid=D("0.30"),
        yes_bid_size=D("500"),
        yes_ask=D("0.32"),
        yes_ask_size=D("500"),
    )
    assert plan["price"] == D("0.70")
    assert plan["action"] == "BLOCK"
    assert plan["reason"] == "LOSS_CAP_EXCEEDED"
    assert plan["final_edge"] == D("-0.33")
