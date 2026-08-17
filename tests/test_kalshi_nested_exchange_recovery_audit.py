from pathlib import Path


def test_exchange_recovery_audit_has_no_order_write_surface():
    source = Path("src/kalshi_nested_exchange_recovery_audit.py").read_text(encoding="utf-8")
    assert ".create(" not in source
    assert ".cancel(" not in source
    assert "execute_seed_first" not in source
    assert '"actual_order_placed": False' in source
    assert '"read_only": True' in source
    assert '"order_entry": False' in source
