import json
from pathlib import Path

from src import gemini_l5_dynamic_exact_stop as m


def test_newest_cycle_selects_highest_durable_cycle(tmp_path: Path):
    (tmp_path / "cycle-001.json").write_text(json.dumps({"orders": [{"id": "old"}]}))
    (tmp_path / "cycle-010.json").write_text(json.dumps({"orders": [{"id": "new"}]}))
    path, payload = m.newest_cycle(tmp_path)
    assert path.name == "cycle-010.json"
    assert payload["orders"][0]["id"] == "new"


def test_cycle_client_ids_are_exact_and_ignore_missing_ids():
    payload = {
        "orders": [
            {"id": "l5lad-y", "side": "YES"},
            {"id": "l5lad-n", "side": "NO"},
            {"side": "YES"},
        ]
    }
    assert m.cycle_client_ids(payload) == {"l5lad-y", "l5lad-n"}


def test_filled_prefers_explicit_filled_quantity():
    assert m._filled({"filledQuantity": "1"}) == m.D("1")
    assert m._filled({"filled_quantity": "0.5"}) == m.D("0.5")
    assert m._filled(None) == m.ZERO


def test_order_and_client_identity_are_narrow():
    row = {"clientOrderId": "exact-client", "orderId": 123}
    assert m._client(row) == "exact-client"
    assert m._order_id(row) == "123"
