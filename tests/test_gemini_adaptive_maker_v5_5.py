import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_executor_v5_5 as v55
from src import gemini_adaptive_maker_v5_5_runtime as runtime55
from src import gemini_q10_production_executor_v5_2 as v52


APPROVAL = "v54cont-20260830-1834-et"


def _old_ids(campaign_no: int) -> dict[str, str]:
    # Exact V5.4 construction that caused the live collision.
    trade_id = f"gadv53-{APPROVAL}-{campaign_no}"[:24]
    return {
        "trade_id": trade_id,
        "yes": f"{trade_id}-yes"[:36],
        "no": f"{trade_id}-no"[:36],
        "grace": f"{trade_id}-grace"[:36],
        "fok1": f"{trade_id}-v5fok-1"[:36],
    }


def test_regression_old_long_approval_ids_collide_but_v55_ids_do_not():
    old1 = _old_ids(1)
    old2 = _old_ids(2)
    assert old1 == old2

    new1 = v55.campaign_client_ids(APPROVAL, 1)
    new2 = v55.campaign_client_ids(APPROVAL, 2)
    new20 = v55.campaign_client_ids(APPROVAL, 20)

    for key in ("trade_id", "yes", "no", "grace", "fok1", "fok2"):
        assert len({new1[key], new2[key], new20[key]}) == 3
    assert len(new1["trade_id"]) <= 24
    assert len(new20["trade_id"]) <= 24
    assert all(len(value) <= 36 for key, value in new20.items() if key != "trade_id")


def test_compact_approval_id_is_deterministic_and_not_plain_truncation():
    first = v55.compact_approval_id(APPROVAL)
    assert first == v55.compact_approval_id(APPROVAL)
    assert first != APPROVAL[: len(first)]
    assert first.startswith("v55-")
    assert len(first) == 14


def test_sanitized_ack_error_allow_lists_only_safe_fields():
    raw = {
        "status": 400,
        "code": "DUPLICATE_CLIENT_ORDER_ID",
        "message": "client order id already exists",
        "reason": "duplicate",
        "secret": "do-not-log",
        "params": {"apiKey": "do-not-log"},
        "error": {
            "code": "DUPLICATE_CLIENT_ORDER_ID",
            "message": "duplicate id",
            "stack": "do-not-log",
        },
    }
    safe = v55.sanitized_ack_error(raw)
    assert safe["status"] == 400
    assert safe["code"] == "DUPLICATE_CLIENT_ORDER_ID"
    assert safe["error"]["message"] == "duplicate id"
    assert "secret" not in safe
    assert "params" not in safe
    assert "stack" not in safe["error"]


def test_definite_fok_400_hard_halts_once_and_clears_pending(monkeypatch):
    heartbeats = []
    emitted = []
    requests = []

    class FakeRoute:
        safe = True
        side = "sell"
        limit_price = D("0.10")
        outcome = "YES"
        action = "UNWIND"

        def as_dict(self):
            return {
                "safe": True,
                "side": self.side,
                "limit_price": str(self.limit_price),
                "outcome": self.outcome,
                "action": self.action,
            }

    class FakeCache:
        async def wait_fresh(self, **kwargs):
            return SimpleNamespace(
                bid=D("0.58"), bid_size=D("100"),
                ask=D("0.60"), ask_size=D("100"), age_ms=5.0,
            )

    class FakeMaker:
        async def request(self, method, params, timeout):
            requests.append((method, params, timeout))
            return {
                "status": 400,
                "error": {
                    "code": "DUPLICATE_CLIENT_ORDER_ID",
                    "message": "client order id already exists",
                },
                "unsafe": "must-not-survive",
            }

    def fake_heartbeat(state, *, stage=None, **fields):
        if stage is not None:
            state["status"] = stage
        state.update(fields)
        heartbeats.append((stage, dict(fields)))

    monkeypatch.setattr(v52.rescue, "best_route_after_fill", lambda **kwargs: FakeRoute())
    monkeypatch.setattr(v52.v5, "_heartbeat", fake_heartbeat)
    monkeypatch.setattr(v52.v5.v1, "emit", lambda stage, **fields: emitted.append((stage, fields)))

    state = {}
    result = asyncio.run(v55.bounded_fok_v55(
        FakeMaker(),
        cache=FakeCache(),
        symbol="GEMI-BTC05M2608302335-UP",
        filled_outcome="YES",
        filled_price=D("0.38"),
        quantity=D("1"),
        increment=D("0.01"),
        trade_id=v55.campaign_trade_id(APPROVAL, 3),
        state=state,
    ))

    assert len(requests) == 1
    assert result["status"] == "RESCUE_FOK_REJECTED"
    assert result["hard_halt"] is True
    assert result["flattened"] is False
    assert result["ack_error"]["error"]["code"] == "DUPLICATE_CLIENT_ORDER_ID"
    assert "unsafe" not in result["ack_error"]
    assert state["pending_rescue"] is None
    assert state["rescue_last_fill"] == "0"
    assert state["rescue_reject"]["error"]["message"] == "client order id already exists"
    assert any(stage == "V5_RESCUE_FOK_ACK" for stage, _ in emitted)


def test_v55_runtime_install_reasserts_exact_hooks_after_legacy_install():
    runtime55.install_new_campaign_runtime()
    assert v53.v5._run_campaign is v55.run_campaign_v55
    assert v52.bounded_fok_v52 is v55.bounded_fok_v55

    # Legacy recovery wiring can mutate shared V5 hooks; V5.5 must restore both.
    v52.install()
    assert v53.v5._run_campaign is not v55.run_campaign_v55

    runtime55.install_new_campaign_runtime()
    assert v53.v5._run_campaign is v55.run_campaign_v55
    assert v52.bounded_fok_v52 is v55.bounded_fok_v55
