import json
from pathlib import Path

from src import gemini_retained_tape_coverage as coverage


def _write(path: Path, rows: list[dict | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_file_counts_public_trade_families_and_markets(tmp_path):
    path = tmp_path / "run-a" / "capture.jsonl"
    _write(path, [
        {"type":"SHADOW_PLAN","asset":"ETH","horizon":"5m","ticker":"E1"},
        {"type":"PUBLIC_TRADE","asset":"ETH","horizon":"5m","ticker":"E1"},
        {"type":"PUBLIC_TRADE","asset":"ETH","horizon":"5m","ticker":"E1"},
        {"type":"PUBLIC_TRADE","asset":"ETH","horizon":"5m","ticker":"E2"},
        {"type":"PUBLIC_TRADE","asset":"BTC","horizon":"5m","ticker":"B1"},
        "not-json",
    ])
    out = coverage.summarize_file(path)
    assert out["rows"] == 6
    assert out["malformed_rows"] == 1
    assert out["type_counts"]["PUBLIC_TRADE"] == 4
    assert out["public_trade_family_counts"] == {"BTC5m": 1, "ETH5m": 3}
    assert out["eth5_public_trades"] == 3
    assert out["eth5_trade_markets"] == 2
    assert out["public_trade_tickers"]["ETH5m"] == ["E1", "E2"]


def test_root_selects_run_with_most_eth5_trade_coverage(tmp_path):
    a = tmp_path / "run-a" / "capture.jsonl"
    b = tmp_path / "run-b" / "capture.jsonl"
    _write(a, [
        {"type":"PUBLIC_TRADE","asset":"ETH","horizon":"5m","ticker":"E1"},
    ])
    _write(b, [
        {"type":"PUBLIC_TRADE","asset":"ETH","horizon":"5m","ticker":"E2"},
        {"type":"PUBLIC_TRADE","asset":"ETH","horizon":"5m","ticker":"E3"},
    ])
    out = coverage.summarize_root(tmp_path, max_runs=20)
    assert out["classification"] == "READ_ONLY_PUBLIC_TAPE_ANALYSIS"
    assert out["order_entry"] is False
    assert out["credentials_loaded"] is False
    assert out["best_eth5_run_id"] == "run-b"
    assert out["best_eth5_public_trades"] == 2
    assert out["best_eth5_trade_markets"] == 2


def test_unknown_family_is_preserved_not_silently_dropped(tmp_path):
    path = tmp_path / "run-x" / "capture.jsonl"
    _write(path, [{"type":"PUBLIC_TRADE","ticker":"X"}])
    out = coverage.summarize_file(path)
    assert out["public_trade_family_counts"] == {"UNKNOWNunknown": 1}
    assert out["eth5_public_trades"] == 0
