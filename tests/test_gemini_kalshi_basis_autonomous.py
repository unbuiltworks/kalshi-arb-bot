import json

from src.gemini_kalshi_basis_autonomous import _payout_wins, retro_score_candidates


def _write(path, payload):
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_payout_wins_handles_agreement_and_both_lose_or_win():
    assert _payout_wins("GEMINI_YES_KALSHI_NO", "yes", "yes") == 1
    assert _payout_wins("GEMINI_YES_KALSHI_NO", "no", "yes") == 0
    assert _payout_wins("GEMINI_YES_KALSHI_NO", "yes", "no") == 2
    assert _payout_wins("KALSHI_YES_GEMINI_NO", "yes", "yes") == 1
    assert _payout_wins("KALSHI_YES_GEMINI_NO", "yes", "no") == 0
    assert _payout_wins("KALSHI_YES_GEMINI_NO", "no", "yes") == 2


def test_retro_score_uses_actual_zero_one_two_leg_payout_and_dedupes(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    settlements = tmp_path / "settlements.jsonl"
    scores = tmp_path / "scores.jsonl"

    _write(candidates, {
        "candidate_key": "c1",
        "asset": "XRP",
        "window_end_utc": "2026-08-17T06:45:00+00:00",
        "gemini_symbol": "GEMI-XRP15M2608170645-UP",
        "kalshi_ticker": "KXXRP15M-TEST",
        "direction": "GEMINI_YES_KALSHI_NO",
        "q": 5,
        "capital": "4.75",
        "apparent_locked_profit": "0.25",
    })
    _write(settlements, {
        "key": "basis1",
        "asset": "XRP",
        "kalshi_ticker": "KXXRP15M-TEST",
        "gemini_outcome": "no",
        "kalshi_outcome": "yes",
        "outcomes_agree": False,
        "signed_basis_bps": "3.1",
        "abs_basis_bps": "3.1",
    })

    first = retro_score_candidates(
        candidate_path=candidates,
        settlement_path=settlements,
        score_path=scores,
    )
    assert first["scored_candidates"] == 1
    assert first["new_scores_this_refresh"] == 1
    assert first["zero_leg_settlements"] == 1
    assert first["settlement_adjusted_pnl"] == "-4.75"
    assert first["apparent_locked_profit"] == "0.25"

    second = retro_score_candidates(
        candidate_path=candidates,
        settlement_path=settlements,
        score_path=scores,
    )
    assert second["scored_candidates"] == 1
    assert second["new_scores_this_refresh"] == 0
    assert len(scores.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_retro_score_leaves_unsettled_candidate_unscored(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    settlements = tmp_path / "settlements.jsonl"
    scores = tmp_path / "scores.jsonl"
    _write(candidates, {
        "candidate_key": "c2",
        "asset": "BTC",
        "kalshi_ticker": "KXBTC15M-MISSING",
        "direction": "KALSHI_YES_GEMINI_NO",
        "q": 5,
        "capital": "4.83",
        "apparent_locked_profit": "0.17",
    })
    settlements.write_text("", encoding="utf-8")
    summary = retro_score_candidates(
        candidate_path=candidates,
        settlement_path=settlements,
        score_path=scores,
    )
    assert summary["scored_candidates"] == 0
    assert summary["unsettled_or_unmatched_candidates"] == 1
    assert summary["trade_authorized"] is False
