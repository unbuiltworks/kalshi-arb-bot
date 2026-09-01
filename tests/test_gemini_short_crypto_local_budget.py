import json
from pathlib import Path

from src import gemini_short_crypto_local_budget as budget


def test_short_symbol_classifier():
    assert budget._short_symbol("GEMI-BTC05M2608271710-UP")
    assert budget._short_symbol("GEMI-XRP15M2608271715-UP")
    assert not budget._short_symbol("GEMI-YANKEES-MONEYLINE")


def test_local_open_state_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(budget, "STATE_DIR", tmp_path)
    monkeypatch.setattr(budget, "JOURNAL", tmp_path / "events.jsonl")
    monkeypatch.setattr(budget, "OPEN_STATE", tmp_path / "open.json")

    budget._write_open({"trade_id": "t1", "status": "CAPITAL_RESERVED"})
    assert budget.local_open_state() == {"trade_id": "t1", "status": "CAPITAL_RESERVED"}

    budget._append({"event_id": "e1", "trade_id": "t1"})
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert rows == [{"event_id": "e1", "trade_id": "t1"}]

    budget._clear_open()
    assert budget.local_open_state() is None
