from decimal import Decimal

import pytest

from src.kalshi_nested_probe_transport import (
    KalshiV2RestTransport,
    parse_create_order_response,
    parse_order_state,
    private_event_progress,
)

D = Decimal


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self):
        self.calls = []
        self.create_body = {
            "order_id": "oid-1",
            "client_order_id": "cid-1",
            "fill_count": "0.00",
            "remaining_count": "1.00",
            "ts_ms": 123,
        }
        self.order_row = {
            "order_id": "oid-1",
            "client_order_id": "cid-1",
            "ticker": "MKT",
            "fill_count_fp": "1.00",
            "remaining_count_fp": "0.00",
        }

    async def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return FakeResponse(201, self.create_body)

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if path == "/portfolio/orders":
            return FakeResponse(200, {"orders": [self.order_row]})
        return FakeResponse(200, {"order": self.order_row})

    async def delete(self, path, **kwargs):
        self.calls.append(("DELETE", path, kwargs))
        return FakeResponse(200, {
            "order_id": "oid-1",
            "client_order_id": "cid-1",
            "reduced_by": "1.00",
            "ts_ms": 456,
        })



def test_create_response_parser_uses_v2_fixed_point_fields():
    ack = parse_create_order_response({
        "order_id": "o",
        "client_order_id": "c",
        "fill_count": "0.25",
        "remaining_count": "0.75",
        "ts_ms": 10,
    })
    assert ack.order_id == "o"
    assert ack.fill_count == D("0.25")
    assert ack.remaining_count == D("0.75")


def test_order_state_parser_uses_fp_fields():
    state = parse_order_state({
        "order_id": "o",
        "client_order_id": "c",
        "ticker": "M",
        "fill_count_fp": "1.00",
        "remaining_count_fp": "0.00",
    })
    assert state.fully_filled


def test_private_fill_matches_order_id_and_reports_fill_size():
    progress = private_event_progress(
        {"type": "fill", "msg": {"order_id": "o", "count_fp": "1.00"}},
        order_id="o",
        client_order_id="c",
    )
    assert progress == ("FILL", D("1.00"))
    assert private_event_progress(
        {"type": "fill", "msg": {"order_id": "other", "count_fp": "1.00"}},
        order_id="o",
        client_order_id="c",
    ) is None


def test_private_user_order_matches_client_id_and_terminal_states():
    assert private_event_progress(
        {"type": "user_order", "msg": {
            "order_id": "o", "client_order_id": "c", "status": "executed",
            "fill_count_fp": "1.00", "remaining_count_fp": "0.00",
        }},
        order_id="o", client_order_id="c",
    ) == ("FILLED", D("1.00"))
    assert private_event_progress(
        {"type": "user_order", "msg": {
            "order_id": "o", "client_order_id": "c", "status": "canceled",
            "fill_count_fp": "0.00", "remaining_count_fp": "0.00",
        }},
        order_id="o", client_order_id="c",
    ) == ("CANCELED", D("0.00"))


@pytest.mark.asyncio
async def test_transport_uses_current_v2_create_get_cancel_paths():
    client = FakeClient()
    transport = KalshiV2RestTransport(client=client)
    ack = await transport.create({"ticker": "MKT", "client_order_id": "cid-1"})
    assert ack.order_id == "oid-1"
    state = await transport.get("oid-1")
    assert state.fully_filled
    canceled = await transport.cancel("oid-1")
    assert canceled["reduced_by"] == "1.00"
    assert client.calls[0][0:2] == ("POST", "/portfolio/events/orders")
    assert client.calls[1][0:2] == ("GET", "/portfolio/orders/oid-1")
    assert client.calls[2][0:2] == ("DELETE", "/portfolio/events/orders/oid-1")


@pytest.mark.asyncio
async def test_reconcile_scans_ticker_orders_for_exact_client_id():
    client = FakeClient()
    transport = KalshiV2RestTransport(client=client)
    state = await transport.reconcile_by_client_id(ticker="MKT", client_order_id="cid-1")
    assert state is not None and state.order_id == "oid-1"
    method, path, kwargs = client.calls[-1]
    assert method == "GET"
    assert path == "/portfolio/orders"
    assert kwargs["params"]["ticker"] == "MKT"
