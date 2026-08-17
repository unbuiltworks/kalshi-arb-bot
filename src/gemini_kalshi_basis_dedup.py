"""No-lookahead deduplicated paper backtest for Gemini/Kalshi basis research.

The live shadow journal can contain many threshold-qualified episode entries for the
same asset/window as quotes flicker in and out of qualification.  Those entries are
useful microstructure evidence, but summing all of them is not a realistic trading
backtest.  This module selects at most one hypothetical paper trade per asset/window,
chronologically, and only then reads the corresponding post-settlement score.

Research only: no order authorization or order-entry path exists here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

D = Decimal
CLASSIFICATION = "BASIS_RESEARCH_ONLY"


def _dec(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    import json

    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _group(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("asset") or "UNKNOWN"), str(row.get("window_end_utc") or ""))


def _receive_ms(row: dict[str, Any]) -> int:
    try:
        return int(row.get("candidate_receive_ms") or 0)
    except (TypeError, ValueError):
        return 0


def _lenient(candidate: dict[str, Any]) -> bool:
    try:
        return int(candidate.get("quote_skew_ms")) <= 100
    except (TypeError, ValueError):
        return False


def _strict(candidate: dict[str, Any]) -> bool:
    apparent = _dec(candidate.get("apparent_locked_profit"))
    return (
        _lenient(candidate)
        and candidate.get("basis_context_available") is True
        and candidate.get("proxy_fresh_250ms") is True
        and apparent is not None
        and apparent >= D("0.05")
    )


def _select(
    candidates: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    eligible: Callable[[dict[str, Any]], bool],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]:
    """Select first eligible candidate per settled asset/window without lookahead.

    A group is considered settled if at least one score exists for that asset/window.
    Once the first eligible candidate is encountered, that group is consumed.  If its
    exact candidate_key is unexpectedly missing from the score file, we do *not* skip
    forward to a later candidate because doing so would introduce lookahead bias.
    """
    score_by_key = {
        str(row.get("candidate_key")): row
        for row in scores
        if row.get("candidate_key")
    }
    settled_groups = {_group(row) for row in scores if row.get("window_end_utc")}
    consumed: set[tuple[str, str]] = set()
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    missing_selected_score = 0

    ordered = sorted(candidates, key=lambda row: (_receive_ms(row), str(row.get("asset") or "")))
    for candidate in ordered:
        group = _group(candidate)
        if group in consumed or group not in settled_groups or not eligible(candidate):
            continue
        consumed.add(group)
        key = str(candidate.get("candidate_key") or "")
        score = score_by_key.get(key)
        if score is None:
            missing_selected_score += 1
            continue
        selected.append((candidate, score))
    return selected, missing_selected_score


def _metrics(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    apparent_total = D("0")
    actual_total = D("0")
    capital_total = D("0")
    zero_leg = one_leg = two_leg = 0
    profitable = losing = flat = 0
    worst: Decimal | None = None

    timeline: list[tuple[int, str, Decimal]] = []
    for candidate, score in rows:
        apparent = _dec(score.get("apparent_locked_profit")) or D("0")
        actual = _dec(score.get("settlement_adjusted_pnl")) or D("0")
        capital = _dec(score.get("capital")) or D("0")
        apparent_total += apparent
        actual_total += actual
        capital_total += capital
        wins = int(score.get("settlement_winning_legs") or 0)
        if wins == 0:
            zero_leg += 1
        elif wins == 1:
            one_leg += 1
        elif wins == 2:
            two_leg += 1
        if actual > 0:
            profitable += 1
        elif actual < 0:
            losing += 1
        else:
            flat += 1
        worst = actual if worst is None or actual < worst else worst
        timeline.append((_receive_ms(candidate), str(candidate.get("asset") or ""), actual))

    running = D("0")
    peak = D("0")
    max_drawdown = D("0")
    for _ts, _asset, pnl in sorted(timeline):
        running += pnl
        if running > peak:
            peak = running
        drawdown = peak - running
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    n = len(rows)
    return {
        "independent_trades": n,
        "capital_deployed_sum": str(capital_total),
        "apparent_locked_profit": str(apparent_total),
        "settlement_adjusted_pnl": str(actual_total),
        "basis_effect": str(actual_total - apparent_total),
        "zero_leg_losses": zero_leg,
        "one_leg_settlements": one_leg,
        "two_leg_wins": two_leg,
        "profitable_trades": profitable,
        "losing_trades": losing,
        "flat_trades": flat,
        "average_pnl_per_trade": str(actual_total / D(n)) if n else None,
        "worst_single_trade_pnl": str(worst) if worst is not None else None,
        "chronological_max_drawdown": str(max_drawdown),
    }


def _policy_summary(
    candidates: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    eligible: Callable[[dict[str, Any]], bool],
    policy_name: str,
) -> dict[str, Any]:
    selected, missing = _select(candidates, scores, eligible)
    by_asset: dict[str, Any] = {}
    for asset in sorted({str(candidate.get("asset") or "UNKNOWN") for candidate, _ in selected}):
        by_asset[asset] = _metrics([(c, s) for c, s in selected if str(c.get("asset") or "UNKNOWN") == asset])
    ex_btc = [(c, s) for c, s in selected if str(c.get("asset") or "UNKNOWN") != "BTC"]
    zero_leg_windows = []
    for candidate, score in selected:
        if int(score.get("settlement_winning_legs") or 0) != 0:
            continue
        zero_leg_windows.append({
            "asset": candidate.get("asset"),
            "window_end_utc": candidate.get("window_end_utc"),
            "candidate_receive_ms": candidate.get("candidate_receive_ms"),
            "direction": candidate.get("direction"),
            "capital": score.get("capital"),
            "apparent_locked_profit": score.get("apparent_locked_profit"),
            "settlement_adjusted_pnl": score.get("settlement_adjusted_pnl"),
            "quote_skew_ms": candidate.get("quote_skew_ms"),
            "spot_abs_margin_bps": candidate.get("spot_abs_margin_bps"),
        })

    return {
        "policy": policy_name,
        "all_assets": _metrics(selected),
        "by_asset": by_asset,
        "excluding_btc": _metrics(ex_btc),
        "zero_leg_windows": zero_leg_windows,
        "selected_candidate_missing_score": missing,
        "selection_rule": "FIRST_ELIGIBLE_CANDIDATE_PER_SETTLED_ASSET_WINDOW_CHRONOLOGICALLY",
        "classification": CLASSIFICATION,
        "read_only": True,
        "trade_authorized": False,
        "order_entry": False,
    }


def build_deduplicated_backtest(candidate_path: Path, score_path: Path) -> dict[str, Any]:
    candidates = _load_jsonl(candidate_path)
    scores = _load_jsonl(score_path)
    scored_groups = {_group(row) for row in scores if row.get("window_end_utc")}
    return {
        "scored_episode_entries": len(scores),
        "distinct_scored_asset_windows": len(scored_groups),
        "lenient_first_candidate": _policy_summary(
            candidates,
            scores,
            _lenient,
            "quote_skew_ms<=100; first qualifying candidate per asset/window",
        ),
        "strict_first_candidate": _policy_summary(
            candidates,
            scores,
            _strict,
            "quote_skew_ms<=100 + basis context + fresh<=250ms proxy + apparent profit>=0.05; first eligible per asset/window",
        ),
        "warning": "Episode-entry aggregate is microstructure evidence, not an independent-trade backtest.",
        "classification": CLASSIFICATION,
        "read_only": True,
        "trade_authorized": False,
        "order_entry": False,
    }
