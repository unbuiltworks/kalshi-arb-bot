import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

# Capture the legacy pair implementation before importing the overlay. V1 installs
# its hooks at import time; restore them immediately so these tests cannot pollute
# the existing legacy rescue test module during pytest collection.
from src import gemini_l5_pair_q1 as base_pair

_ORIG_CHOOSE = base_pair.choose_pair
_ORIG_SUMMARIZE = base_pair.summarize
_ORIG_RESCUE = base_pair._dynamic_complement_rescue
_ORIG_EMIT = base_pair.emit

from src import gemini_l5_bounded_rescue_q1 as economics
from src import gemini_l5_bounded_rescue_q1_v2 as m

base_pair.choose_pair = _ORIG_CHOOSE
base_pair.summarize = _ORIG_SUMMARIZE
base_pair._dynamic_complement_rescue = _ORIG_RESCUE
base_pair.emit = _ORIG_EMIT


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
        {"side": "YES", "price": D("0.47"), "id": "yes0", "row": {"orderId": "1"}, "liquidity": "MAKER", "tif": "MOC"},
        {"side": "NO", "price": D("0.49"), "id": "no0", "row": {"orderId": "2"}, "liquidity": "MAKER", "tif": "MOC"},
    ]


def test_exact_failed_case_is_cheap_to_flatten_at_50c():
    plan = economics.emergency_ioc_plan(
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
    at_cap = economics.emergency_ioc_plan(
        filled_outcome="NO",
        filled_price=D("0.49"),
        yes_bid=D("0.52"),
        yes_bid_size=D("10"),
        yes_ask=D("0.53"),
        yes_ask_size=D("10"),
    )
    over = economics.emergency_ioc_plan(
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
    assert economics.entry_emergency_flatten_ok(
        yes_price=D("0.47"),
        no_price=D("0.49"),
        yes_bid=D("0.48"),
        yes_bid_size=D("1"),
        yes_ask=D("0.50"),
        yes_ask_size=D("1"),
    )
    assert not economics.entry_emergency_flatten_ok(
        yes_price=D("0.47"),
        no_price=D("0.49"),
        yes_bid=D("0.48"),
        yes_bid_size=D("1"),
        yes_ask=D("0.50"),
        yes_ask_size=D("0.50"),
    )


def test_choose_pair_rejects_pair_if_emergency_depth_is_not_q1():
    assert economics.choose_pair(
        D("0.48"),
        D("0.50"),
        min_edge=D("0.01"),
        yes_queue=D("1"),
        no_queue=D("0.50"),
        tie_seed="x",
    ) is None


def test_summary_charges_taker_fee_for_fok_leg():
    specs = [
        {"side": "NO", "price": D("0.49"), "id": "n", "liquidity": "MAKER", "tif": "MOC"},
        {"side": "YES", "price": D("0.50"), "id": "y", "liquidity": "TAKER", "tif": "FOK"},
    ]
    out = economics.summarize(specs, {"n": D("1"), "y": D("1")})
    assert out["matched_qty"] == "1"
    assert out["maker_fees"] == "0.03"
    assert out["locked_edge_after_fees"] == "-0.02"
    assert {row["liquidity"] for row in out["fills"]} == {"MAKER", "TAKER"}


def test_emergency_order_is_fok_and_never_resting_moc():
    maker = DummyMaker()
    spec = asyncio.run(m._post_emergency_fok(
        maker,
        event=_event(),
        sid="session",
        missing="YES",
        price=D("0.50"),
        sequence=1,
    ))
    assert spec is not None
    assert spec["liquidity"] == "TAKER"
    assert spec["tif"] == "FOK"
    _, payload, _ = maker.requests[0]
    assert payload["timeInForce"] == "FOK"
    assert payload["eventOutcome"] == "YES"
    assert payload["quantity"] == "1"


def test_full_rescue_escalates_to_bounded_fok_and_matches(monkeypatch):
    specs = _specs_no_first()
    state = {"orders": []}
    cancel_calls = []

    async def fake_fills(current_specs, symbol):
        out = {s["id"]: D("0") for s in current_specs}
        out["no0"] = D("1")
        if any(s.get("tif") == "FOK" for s in current_specs):
            fok = [s for s in current_specs if s.get("tif") == "FOK"][-1]
            out[fok["id"]] = D("1")
        return out

    async def fake_wait(*args, **kwargs):
        return False

    async def fake_cancel(*args, **kwargs):
        cancel_calls.append(kwargs["current"]["id"])
        return "INACTIVE_ZERO_OR_PARTIAL"

    async def no_breakeven(*args, **kwargs):
        return None

    async def fake_top(symbol, attempts=2, delay_s=0.10):
        return D("0.48"), D("10"), D("0.50"), D("10")

    async def fake_fok(maker, *, event, sid, missing, price, sequence):
        assert missing == "YES"
        assert price == D("0.50")
        return {
            "side": "YES",
            "price": price,
            "id": "yes-fok",
            "row": None,
            "liquidity": "TAKER",
            "tif": "FOK",
        }

    monkeypatch.setattr(m.pair.ladder, "fills", fake_fills)
    monkeypatch.setattr(m.v1, "_wait_missing_ws", fake_wait)
    monkeypatch.setattr(m, "_cancel_current", fake_cancel)
    monkeypatch.setattr(m, "_post_breakeven_maker", no_breakeven)
    monkeypatch.setattr(m.pair.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m, "_post_emergency_fok", fake_fok)
    monkeypatch.setattr(m.pair, "_write_state", lambda payload: None)
    monkeypatch.setattr(m.v1, "IOC_RETRY_SECONDS", 0.0)

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
    assert status == "MATCHED_BOUNDED_FOK"
    assert cancel_calls == ["yes0"]
    assert specs[-1]["tif"] == "FOK"
    assert state["emergency_fok_attempts"] == 1
    assert state["emergency_loss_cap"] == "0.05"


def test_loss_cap_blocks_fok_instead_of_unbounded_cross(monkeypatch):
    specs = _specs_no_first()
    posted = False

    async def fake_fills(current_specs, symbol):
        return {"yes0": D("0"), "no0": D("1")}

    async def fake_wait(*args, **kwargs):
        return False

    async def fake_cancel(*args, **kwargs):
        return "INACTIVE_ZERO_OR_PARTIAL"

    async def no_breakeven(*args, **kwargs):
        return None

    async def fake_top(symbol, attempts=2, delay_s=0.10):
        return D("0.53"), D("10"), D("0.54"), D("10")

    async def fake_fok(*args, **kwargs):
        nonlocal posted
        posted = True
        raise AssertionError("FOK posted beyond configured hard loss cap")

    monkeypatch.setattr(m.pair.ladder, "fills", fake_fills)
    monkeypatch.setattr(m.v1, "_wait_missing_ws", fake_wait)
    monkeypatch.setattr(m, "_cancel_current", fake_cancel)
    monkeypatch.setattr(m, "_post_breakeven_maker", no_breakeven)
    monkeypatch.setattr(m.pair.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m, "_post_emergency_fok", fake_fok)

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


def test_fok_partial_fill_is_invariant_violation_and_is_not_retried(monkeypatch):
    specs = _specs_no_first()
    fok_posts = 0

    async def fake_fills(current_specs, symbol):
        return {"yes0": D("0"), "no0": D("1")}

    async def fake_wait(*args, **kwargs):
        return False

    async def fake_cancel(*args, **kwargs):
        return "INACTIVE_ZERO_OR_PARTIAL"

    async def no_breakeven(*args, **kwargs):
        return None

    async def fake_top(symbol, attempts=2, delay_s=0.10):
        return D("0.48"), D("10"), D("0.50"), D("10")

    async def fake_fok(maker, *, event, sid, missing, price, sequence):
        nonlocal fok_posts
        fok_posts += 1
        return {"side": "YES", "price": price, "id": "yes-fok", "row": None, "liquidity": "TAKER", "tif": "FOK"}

    async def fake_authoritative(specs, symbol):
        return False, {"yes_qty": "0.5", "no_qty": "1", "fills": []}

    monkeypatch.setattr(m.pair.ladder, "fills", fake_fills)
    monkeypatch.setattr(m.v1, "_wait_missing_ws", fake_wait)
    monkeypatch.setattr(m, "_cancel_current", fake_cancel)
    monkeypatch.setattr(m, "_post_breakeven_maker", no_breakeven)
    monkeypatch.setattr(m.pair.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m, "_post_emergency_fok", fake_fok)
    monkeypatch.setattr(m, "_authoritative_matched", fake_authoritative)
    monkeypatch.setattr(m.pair, "_write_state", lambda payload: None)
    monkeypatch.setattr(m.v1, "IOC_RETRY_SECONDS", 0.0)

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
    assert status == "FOK_PARTIAL_FILL_INVARIANT_VIOLATION"
    assert fok_posts == 1


def test_install_is_explicit_and_reversible(monkeypatch):
    # Production calls V2.install() from main; ordinary imports must be restorable
    # and legacy tests must not depend on the overlay being globally installed.
    orig_choose = m.pair.choose_pair
    orig_summary = m.pair.summarize
    orig_rescue = m.pair._dynamic_complement_rescue
    orig_emit = m.pair.emit
    try:
        m.install()
        assert m.pair._dynamic_complement_rescue is m._dynamic_complement_rescue
        assert m.pair.choose_pair is economics.choose_pair
        assert m.pair.summarize is economics.summarize
    finally:
        m.pair.choose_pair = orig_choose
        m.pair.summarize = orig_summary
        m.pair._dynamic_complement_rescue = orig_rescue
        m.pair.emit = orig_emit
