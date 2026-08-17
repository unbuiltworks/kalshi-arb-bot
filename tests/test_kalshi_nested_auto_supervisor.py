from pathlib import Path

from src.kalshi_nested_auto_supervisor import pair_key, append_journal


class _Candidate:
    event_ticker = "EVT"
    maker_ticker = "M2"
    maker_side = "no"
    hedge_ticker = "M1"
    hedge_side = "yes"


def test_pair_key_is_canonical_across_leg_order():
    c = _Candidate()
    assert pair_key(c) == "EVT|M1:yes|M2:no"


def test_journal_writes_jsonl(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    append_journal(path, {"status": "PAPER_TRIGGER", "actual_order_placed": False})
    text = path.read_text(encoding="utf-8")
    assert '"status":"PAPER_TRIGGER"' in text
    assert '"actual_order_placed":false' in text
    assert text.endswith("\n")


def test_supervisor_has_no_order_entry_surface():
    source = Path("src/kalshi_nested_auto_supervisor.py").read_text(encoding="utf-8")
    assert "execute_seed_first" not in source
    assert "KalshiV2RestTransport" not in source
    assert 'add_argument("--live"' not in source
    assert '"order_entry": False' in source
