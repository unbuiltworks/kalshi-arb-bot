import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_l5_bounded_rescue_q1 as m


class DummyMaker:
    def __init__(self):
        self.requests = []
        self.events = asyncio.Queue()

    async def request(self, method, payload, timeout=4.0):
        self.requests.append((method, dict(payload), timeout))
        return {"status": 200}


def _event(tte=180.0):
    return SimpleNamespace(symbol="GEMI-ETH05M-TEST-UP", seconds_to_expiry=tte)


def _specs_no_first():
    return [
        {"side": "YES", "price": D("0.47"), "id": "yes0", "row": {"orderId": "1"}, "liquidity": "MAKER"},
        {"side": "NO", "price": D("0.49"), "id": "no0", "row": {"orderId": "2"}, "liquidity": "MAKER"},
    ]


def test_exact_failed_case_is_cheap_to_flatten_at_50c():
    plan = m.emergency_ioc_plan(
        filled_outcome="NO",
        filled_price=D("0.49"),
        yes_bid=D("0.48"),
        yes_bid_size=D("10"),
        yes_ask=D("0.50"),
        yes_ask_size=D("10"),
    )
    assert plan["action"] == "IOC"
    assert plan["price"] == D("0.50")
    assert plan["final_edge"] == D("-0.02")


def test_53c_is_hard_cap_and_54c_is_rejected_for_49c_first_fill():
    at_cap = m.emergency_ioc_plan(
        filled_outcome="NO",
        filled_price=D("0.49"),
        yes_bid=D("0.52"),
        yes_bid_size=D("10"),
        yes_ask=D("0.53"),
        yes_ask_size=D("10"),
    )
    over = m.emergency_ioc_plan(
        filled_outcome="NO",
        filled_price=D("0.49"),
        yes_bid=D("0.53"),
        yes_bid_size=D("10"),
        yes_ask=D("0.54"),
        yes_ask_size=D("10"),
    )
    assert at_cap["action"] == "IOC"
    assert at_cap["final_edge"] == D("-0.05")
    assert over["action"] == "BLOCK"
    assert over["reason"] == "LOSS_CAP_EXCEEDED"
    assert over["final_edge"] == D("-0.06")


def test_entry_gate_requires_q1_displayed_depth_for_both_emergency_directions():
    assert m.entry_emergency_flatten_ok(
        yes_price=D("0.47"),
        no_price=D("0.49"),
        yes_bid=D("0.48"),
        yes_bid_size=D("1"),
        yes_ask=D("0.50"),
        yes_ask_size=D("1"),
    )
    assert not m.entry_emergency_flatten_ok(
        yes_price=D("0.47"),
        no_price=D("0.49"),
        yes_bid=D("0.48"),
        yes_bid_size=D("1"),
        yes_ask=D("0.50"),
        yes_ask_size=D("0.50"),
    )


def test_choose_pair_rejects_pair_if_emergency_depth_is_not_q1():
    assert m.choose_pair(
        D("0.48"),
        D("0.50"),
        min_edge=D("0.01"),
        yes_queue=D("1"),
        no_queue=D("0.50"),
        tie_seed="x",
    ) is None


def test_summary_charges_taker_fee_for_ioc_leg():
    specs = [
        {"side": "NO", "price": D("0.49"), "id": "n", "liquidity": "MAKER", "tif": "MOC"},
        {"side": "YES", "price": D("0.50"), "id": "y", "liquidity": "TAKER", "tif": "IOC"},
    ]
    out = m.summarize(specs, {"n": D("1"), "y": D("1")})
    assert out["matched_qty"] == "1"
    assert out["maker_fees"] == "0.03"
    assert out["locked_edge_after_fees"] == "-0.02"
    assert {row["liquidity"] for row in out["fills"]} == {"MAKER", "TAKER"}


def test_emergency_order_is_ioc_and_never_resting_moc():
    maker = DummyMaker()
    spec = asyncio.run(m._post_emergency_ioc(
        maker,
        event=_event(),
        sid="session",
        missing="YES",
        price=D("0.50"),
        sequence=1,
    ))
    assert spec is not None
    assert spec["liquidity"] == "TAKER"
    assert spec["tif"] == "IOC"
    _, payload, _ = maker.requests[0]
    assert payload["timeInForce"] == "IOC"
    assert payload["eventOutcome"] == "YES"
    assert payload["quantity"] == "1"


def test_full_rescue_escalates_to_bounded_ioc_and_matches(monkeypatch):
    specs = _specs_no_first()
    state = {"orders": []}
    stages = []

    async def fake_fills(current_specs, symbol):
        out = {s["id"]: D("0") for s in current_specs}
        out["no0"] = D("1")
        if any(s.get("liquidity") == "TAKER" for s in current_specs):
            taker = [s for s in current_specs if s.get("liquidity") == "TAKER"][-1]
            out[taker["id"]] = D("1")
        return out

    async def fake_stage(*args, **kwargs):
        stages.append(D(str(kwargs["final_edge_floor"])))
        return "CONTINUE", kwargs["current"], kwargs["sequence"]

    async def fake_cancel_prove(*args, **kwargs):
        return "INACTIVE_ZERO_OR_PARTIAL", {"yes0": D("0"), "no0": D("1")}

    async def fake_top(symbol, attempts=2, delay_s=0.10):
        return D("0.48"), D("10"), D("0.50"), D("10")

    async def fake_ioc(maker, *, event, sid, missing, price, sequence):
        assert missing == "YES"
        assert price == D("0.50")
        return {
            "side": "YES",
            "price": price,
            "id": "yes-ioc",
            "row": None,
            "liquidity": "TAKER",
            "tif": "IOC",
        }

    monkeypatch.setattr(m.pair.ladder, "fills", fake_fills)
    monkeypatch.setattr(m, "_maker_stage", fake_stage)
    monkeypatch.setattr(m, "_cancel_prove_inactive_and_reconcile", fake_cancel_prove)
    monkeypatch.setattr(m.pair.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m, "_post_emergency_ioc", fake_ioc)
    monkeypatch.setattr(m.pair, "_write_state", lambda payload: None)
    monkeypatch.setattr(m, "IOC_RETRY_SECONDS", 0.0)

    status = asyncio.run(m._dynamic_complement_rescue(
        DummyMaker(),
        event=_event(),
        sid="s",
        specs=specs,
        state=state,
        filled_side="NO",
        min_edge=D("0.01"),
        complement_wait=60.0,
    ))
    assert status == "MATCHED_BOUNDED_IOC"
    assert stages == [D("0.01"), D("0")]
    assert specs[-1]["tif"] == "IOC"
    assert state["emergency_ioc_attempts"] == 1
    assert state["emergency_loss_cap"] == "0.05"


def test_loss_cap_blocks_ioc_instead_of_unbounded_cross(monkeypatch):
    specs = _specs_no_first()
    posted = False

    async def fake_fills(current_specs, symbol):
        return {"yes0": D("0"), "no0": D("1")}

    async def fake_stage(*args, **kwargs):
        return "CONTINUE", kwargs["current"], kwargs["sequence"]

    async def fake_cancel_prove(*args, **kwargs):
        return "INACTIVE_ZERO_OR_PARTIAL", {"yes0": D("0"), "no0": D("1")}

    async def fake_top(symbol, attempts=2, delay_s=0.10):
        return D("0.53"), D("10"), D("0.54"), D("10")

    async def fake_ioc(*args, **kwargs):
        nonlocal posted
        posted = True
        raise AssertionError("IOC posted beyond configured hard loss cap")

    monkeypatch.setattr(m.pair.ladder, "fills", fake_fills)
    monkeypatch.setattr(m, "_maker_stage", fake_stage)
    monkeypatch.setattr(m, "_cancel_prove_inactive_and_reconcile", fake_cancel_prove)
    monkeypatch.setattr(m.pair.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m, "_post_emergency_ioc", fake_ioc)

    status = asyncio.run(m._dynamic_complement_rescue(
        DummyMaker(),
        event=_event(),
        sid="s",
        specs=specs,
        state={"orders": []},
        filled_side="NO",
        min_edge=D("0.01"),
        complement_wait=60.0,
    ))
    assert status == "EMERGENCY_LOSS_CAP_EXCEEDED"
    assert posted is False
