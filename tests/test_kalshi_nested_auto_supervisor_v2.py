from pathlib import Path

from src.kalshi_nested_auto_supervisor_v2 import _lockout_row
from src.kalshi_nested_auto_governor import AutoRiskGovernor


def test_v2_has_no_order_entry_or_live_cli_surface():
    source = Path("src/kalshi_nested_auto_supervisor_v2.py").read_text(encoding="utf-8")
    assert "from src.kalshi_nested_seed_first_probe import" not in source
    assert "await execute_seed_first(" not in source
    assert "KalshiV2RestTransport" not in source
    assert 'add_argument("--live"' not in source
    assert '"order_entry": False' in source
    assert '"actual_order_placed": False' in source


def test_v2_defaults_to_durable_mount_paths():
    source = Path("src/kalshi_nested_auto_supervisor_v2.py").read_text(encoding="utf-8")
    assert '/data/kalshi-nested-auto-paper.jsonl' in source
    assert '/data/kalshi-nested-auto-state.json' in source


def test_lockout_row_is_explicitly_read_only():
    governor = AutoRiskGovernor()
    governor.lockout("TEST")
    row = _lockout_row(governor=governor, stats={}, reason="TEST")
    assert row["status"] == "PAPER_SUPERVISOR_LOCKOUT"
    assert row["actual_order_placed"] is False
    assert row["read_only"] is True
    assert row["order_entry"] is False


def test_v2_persists_reservation_before_paper_trigger_log():
    source = Path("src/kalshi_nested_auto_supervisor_v2.py").read_text(encoding="utf-8")
    record_pos = source.index("governor.record_trigger(")
    save_pos = source.index("safe_save_state(state_path, governor)", record_pos)
    trigger_pos = source.index("NESTED_AUTO_SUPERVISOR_V2_PAPER_TRIGGER", save_pos)
    assert record_pos < save_pos < trigger_pos


def test_empty_generation_sleeps_until_generation_deadline():
    source = Path("src/kalshi_nested_auto_supervisor_v2.py").read_text(encoding="utf-8")
    empty_pos = source.index("if not candidates:")
    sleep_pos = source.index("await sleep_until(generation_deadline)", empty_pos)
    assert sleep_pos > empty_pos
    # Regression guard: V1 used min(1.0, ...) and hammered discovery.
    block = source[empty_pos:sleep_pos]
    assert "min(1.0" not in block
