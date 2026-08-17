"""Railpack entrypoint that augments basis research with deduplicated paper metrics."""
from __future__ import annotations

from src import gemini_kalshi_basis_autonomous as base
from src.gemini_kalshi_basis_dedup import build_deduplicated_backtest


_original_retro = base.retro_score_candidates


def _retro_with_dedup(*, candidate_path, settlement_path, score_path):
    summary = _original_retro(
        candidate_path=candidate_path,
        settlement_path=settlement_path,
        score_path=score_path,
    )
    summary["deduplicated_paper_backtest"] = build_deduplicated_backtest(candidate_path, score_path)
    return summary


def main() -> None:
    base.retro_score_candidates = _retro_with_dedup
    base.main()


if __name__ == "__main__":
    main()
