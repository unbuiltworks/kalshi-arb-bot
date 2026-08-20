import json
from pathlib import Path

from src.gemini_kalshi_maker_shadow_report import summarize


def test_report_ranks_profit_and_tracks_unhedgeable(tmp_path: Path):
    path = tmp_path / "maker_shadow_sol.jsonl"
    rows = [
        {
            "event": "OPEN", "asset": "SOL",
            "key": "KALSHI_YES_GEMINI_NO|JOIN|0.03",
        },
        {
            "event": "SIMULATED_FILL", "asset": "SOL",
            "key": "KALSHI_YES_GEMINI_NO|JOIN|0.03",
            "direction": "KALSHI_YES_GEMINI_NO", "policy": "JOIN",
            "target_profit": "0.03", "simulated_fill_profit": "0.04",
            "quote_age_ms": 500, "hedge_book_age_ms": 25,
        },
        {
            "event": "FILL_HEDGE_UNAVAILABLE", "asset": "SOL",
            "key": "KALSHI_YES_GEMINI_NO|JOIN|0.03",
        },
        {
            "event": "OPEN", "asset": "SOL",
            "key": "KALSHI_YES_GEMINI_NO|MAX_SAFE|0.02",
        },
        {
            "event": "SIMULATED_FILL", "asset": "SOL",
            "key": "KALSHI_YES_GEMINI_NO|MAX_SAFE|0.02",
            "direction": "KALSHI_YES_GEMINI_NO", "policy": "MAX_SAFE",
            "target_profit": "0.02", "simulated_fill_profit": "0.07",
            "quote_age_ms": 300, "hedge_book_age_ms": 15,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report = summarize([path])
    assert report[0]["policy"] == "MAX_SAFE"
    assert report[0]["simulated_net_profit"] == "0.07"
    join = next(row for row in report if row["policy"] == "JOIN")
    assert join["fills"] == 1
    assert join["hedge_unavailable"] == 1
    assert join["hedge_success_pct"] == 50.0
