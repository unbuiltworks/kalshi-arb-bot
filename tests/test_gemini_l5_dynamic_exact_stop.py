import json
from pathlib import Path

from src import gemini_l5_dynamic_exact_stop as m


def test_newest_cycle_selects_highest_durable_cycle_without_session_filter(tmp_path: Path):
    (tmp_path / "cycle-001.json").write_text(json.dumps({"orders": [{"id": "old"}]}))
    (tmp_path / "cycle-010.json").write_text(json.dumps({"orders": [{"id": "new"}]}))
    path, payload = m.newest_cycle(tmp_path)
    assert path.name == "cycle-010.json"
    assert payload["orders"][0]["id"] == "new"


def test_newest_cycle_ignores_higher_number_from_old_session(tmp_path: Path):
    (tmp_path / "session.json").write_text(json.dumps({"sid": "current-session"}))
    (tmp_path / "cycle-006.json").write_text(json.dumps({
        "sid": "old-session-c006",
        "orders": [{"id": "old-high"}],
    }))
    (tmp_path / "cycle-003.json").write_text(json.dumps({
        "sid": "current-session-c003",
        "orders": [{"id": "current-low"}],
    }))
    sid = m.current_session_id(tmp_path)
    assert sid == "current-session"
    path, payload = m.newest_cycle(tmp_path, session_id=sid)
    assert path.name == "cycle-003.json"
    assert payload["orders"][0]["id"] == "current-low"


def test_current_session_id_requires_sid_when_file_exists(tmp_path: Path):
    (tmp_path / "session.json").write_text(json.dumps({"terminal": False}))
    try:
        m.current_session_id(tmp_path)
    except RuntimeError as exc:
        assert "missing sid" in str(exc)
    else:
        raise AssertionError("session file without sid was accepted")


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
