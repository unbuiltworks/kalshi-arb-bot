"""Read-only structured sports counterpart probe for Polymarket US <-> Kalshi.

Discovery/prioritization only. It uses Polymarket's structured sportsMarketType plus
conservative player/event/threshold evidence to identify Kalshi series worth
promoting into explicit strict adapters. No credentials or order-entry code.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any

import httpx
from src.sports_universe_inventory import fetch_kalshi_sports, fetch_poly_sports

logger = logging.getLogger("kalshi_arb.sports_overlap_probe")
TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?", re.I)
NUM_RE = re.compile(r"(?<![a-z])\d+(?:\.\d+)?", re.I)
DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
PLAYER_RE = re.compile(r"^Will\s+(.+?)\s+(?:record|have|hit|allow|pitch|score)\b", re.I)
STOP = {"a","an","and","are","at","be","by","for","from","game","in","is","of","on","or","the","to","vs","will","with","over","under","more","than","record","have","upcoming","event","scheduled"}
STAT_HINTS: dict[str, tuple[str, ...]] = {
    "baseball_player_hits": ("hit", "hits"),
    "baseball_player_home_runs": ("home run", "home runs", "homer", "hr"),
    "baseball_player_total_bases": ("total bases", "bases"),
    "baseball_player_strikeouts": ("strikeout", "strikeouts"),
    "baseball_player_hits_runs_rbis": ("hits runs rbis", "hits plus runs plus rbis", "hrr"),
    "baseball_player_hits_allowed": ("hits allowed",),
    "baseball_player_earned_runs_allowed": ("earned runs", "earned runs allowed"),
    "baseball_player_outs": ("outs", "outs recorded"),
    "baseball_player_walks_allowed": ("walks", "walks allowed"),
    "football_player_passing_yards": ("passing yards",),
    "football_player_receiving_yards": ("receiving yards",),
    "football_player_rushing_yards": ("rushing yards",),
    "football_player_touchdowns": ("touchdown", "touchdowns"),
    "tennis_match_winner": ("winner", "win"),
    "tennis_match_total_games": ("total games", "games"),
    "tennis_match_total_sets": ("total sets", "sets"),
    "tennis_match_games_spread": ("games spread", "game spread", "games"),
    "tennis_match_sets_spread": ("sets spread", "set spread", "sets"),
}
PRIORITY_PREFIXES = ("baseball_", "football_", "tennis_", "basketball_", "hockey_")

@dataclass(frozen=True)
class Candidate:
    poly_slug: str
    poly_type: str
    poly_question: str
    poly_line: str
    kalshi_series: str
    kalshi_event_ticker: str
    kalshi_ticker: str
    kalshi_event_title: str
    kalshi_market_text: str
    score: int
    evidence: tuple[str, ...]
    def as_dict(self) -> dict[str, Any]:
        return {
            "poly_slug": self.poly_slug, "poly_type": self.poly_type,
            "poly_question": self.poly_question, "poly_line": self.poly_line,
            "kalshi_series": self.kalshi_series, "kalshi_event_ticker": self.kalshi_event_ticker,
            "kalshi_ticker": self.kalshi_ticker, "kalshi_event_title": self.kalshi_event_title,
            "kalshi_market_text": self.kalshi_market_text, "score": self.score,
            "evidence": list(self.evidence), "execution_eligible": False,
            "status": "CANDIDATE_ONLY_NEEDS_RULE_ADAPTER",
        }

def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().replace("+", " plus ").split())

def _tokens(text: Any) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(str(text or "")) if len(t) > 1 and t.lower() not in STOP}

def _numbers(text: Any) -> set[str]:
    return set(NUM_RE.findall(str(text or "")))

def _line(row: dict[str, Any]) -> str:
    raw = row.get("line")
    if raw in (None, ""): return ""
    try: return format(Decimal(str(raw)).normalize(), "f")
    except (InvalidOperation, ValueError): return str(raw)

def _player_name(question: str) -> str:
    m = PLAYER_RE.search(question.strip())
    return _norm(m.group(1)).strip(" ?") if m else ""

def _slug_parts(slug: str) -> tuple[str, str, str, str] | None:
    parts = slug.lower().split("-")
    if len(parts) < 7: return None
    for idx in range(1, min(5, len(parts)-4)):
        date = "-".join(parts[idx+3:idx+6])
        if DATE_RE.fullmatch(date):
            return parts[idx], parts[idx+1], parts[idx+2], date
    return None

def _kalshi_text(event: dict[str, Any], market: dict[str, Any]) -> str:
    return " ".join(str(x or "") for x in (
        event.get("title"), event.get("sub_title"), event.get("event_ticker"),
        market.get("title"), market.get("subtitle"), market.get("yes_sub_title"),
        market.get("no_sub_title"), market.get("rules_primary")))

def _stat_evidence(poly_type: str, kalshi_norm: str) -> bool:
    hints = STAT_HINTS.get(poly_type)
    return bool(hints and any(_norm(h) in kalshi_norm for h in hints))

def _candidate(poly: dict[str, Any], event: dict[str, Any], market: dict[str, Any]) -> Candidate | None:
    poly_type = str(poly.get("sportsMarketType") or "")
    if not poly_type.startswith(PRIORITY_PREFIXES): return None
    slug = str(poly.get("slug") or "")
    question = str(poly.get("question") or poly.get("title") or "")
    p_line, p_tokens, p_nums = _line(poly), _tokens(question), _numbers(question)
    if p_line: p_nums.add(p_line)
    k_text = _kalshi_text(event, market); k_norm = _norm(k_text); k_tokens = _tokens(k_text); k_nums = _numbers(k_text)
    evidence: list[str] = []; score = 0
    parts = _slug_parts(slug)
    if parts:
        league, side_a, side_b, date = parts
        et = str(event.get("event_ticker") or "").lower(); title = _norm(event.get("title") or event.get("sub_title"))
        if league in et or league in _norm(event.get("series_ticker")) or league in title:
            score += 1; evidence.append("league_anchor")
        team_hits = sum(1 for side in (side_a, side_b) if side in et or side in title)
        if team_hits == 2: score += 4; evidence.append("both_event_sides")
        elif team_hits == 1: score += 1; evidence.append("one_event_side")
        if date in _norm(k_text): score += 2; evidence.append("date")
    player = _player_name(question)
    if "_player_" in poly_type:
        if not player: return None
        pts = [t for t in _tokens(player) if len(t) > 1]
        hits = [t for t in pts if t in k_tokens]
        if len(pts) >= 2 and len(hits) < 2: return None
        if len(pts) == 1 and not hits: return None
        score += 5; evidence.append("player_name")
    if _stat_evidence(poly_type, k_norm): score += 4; evidence.append("stat_family")
    elif poly_type in STAT_HINTS: return None
    if p_line:
        if p_line not in k_nums: return None
        score += 3; evidence.append("threshold")
    elif p_nums and p_nums & k_nums:
        score += 1; evidence.append("number_overlap")
    shared = p_tokens & k_tokens
    if shared: score += min(3, len(shared)); evidence.append(f"shared_tokens:{len(shared)}")
    if score < 10: return None
    return Candidate(
        poly_slug=slug, poly_type=poly_type, poly_question=question, poly_line=p_line,
        kalshi_series=str(event.get("series_ticker") or ""), kalshi_event_ticker=str(event.get("event_ticker") or ""),
        kalshi_ticker=str(market.get("ticker") or ""), kalshi_event_title=str(event.get("title") or event.get("sub_title") or ""),
        kalshi_market_text=" | ".join(str(x or "") for x in (market.get("title"), market.get("yes_sub_title"), market.get("no_sub_title")) if str(x or "").strip())[:500],
        score=score, evidence=tuple(evidence))

def _query_tokens(poly: dict[str, Any]) -> set[str]:
    question = str(poly.get("question") or poly.get("title") or "")
    player = _player_name(question)
    if player:
        return _tokens(player)
    # For non-player families, use the proposition text; the rarest token in the
    # Kalshi inverted index will select a small candidate pool.
    return _tokens(question)

def find_candidates(poly_rows: list[dict[str, Any]], kalshi_events: list[dict[str, Any]]) -> list[Candidate]:
    flat: list[tuple[dict[str, Any], dict[str, Any], set[str]]] = []
    postings: dict[str, list[int]] = defaultdict(list)
    for event in kalshi_events:
        for market in event.get("markets") or []:
            tokens = _tokens(_kalshi_text(event, market))
            idx = len(flat); flat.append((event, market, tokens))
            for token in tokens: postings[token].append(idx)

    out: list[Candidate] = []
    for poly in poly_rows:
        poly_type = str(poly.get("sportsMarketType") or "")
        if not poly_type.startswith(PRIORITY_PREFIXES): continue
        qtokens = [t for t in _query_tokens(poly) if t in postings]
        if not qtokens: continue
        # Choose the rarest available anchor. Player props normally anchor on a
        # surname; other markets anchor on the most discriminating team/player word.
        anchor = min(qtokens, key=lambda t: len(postings[t]))
        for idx in postings[anchor]:
            event, market, _ = flat[idx]
            row = _candidate(poly, event, market)
            if row is not None: out.append(row)
    out.sort(key=lambda x: (x.score, x.poly_type, x.poly_slug), reverse=True)
    return out

def summarize(candidates: list[Candidate]) -> dict[str, Any]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for row in candidates: grouped[row.poly_type].append(row)
    families: dict[str, Any] = {}
    for family, rows in sorted(grouped.items()):
        series = Counter(r.kalshi_series for r in rows)
        families[family] = {
            "candidate_pairs": len(rows),
            "unique_polymarket_markets": len({r.poly_slug for r in rows}),
            "unique_kalshi_markets": len({r.kalshi_ticker for r in rows}),
            "kalshi_series": dict(series.most_common(8)),
            "top_examples": [r.as_dict() for r in rows[:5]],
        }
    return {"candidate_pairs": len(candidates), "families_with_candidates": len(families), "families": families,
            "read_only": True, "order_entry": False, "execution_eligible_pairs": 0,
            "note": "Candidate discovery only; strict family rule adapters are required before execution eligibility."}

async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        poly, kalshi = await asyncio.gather(fetch_poly_sports(http), fetch_kalshi_sports(http))
    result = summarize(find_candidates(poly, kalshi))
    logger.info("SPORTS_OVERLAP_PROBE %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True)); return result

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())
if __name__ == "__main__": main()
