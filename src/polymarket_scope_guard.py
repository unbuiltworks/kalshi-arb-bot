"""Fail-closed Polymarket MLB scope guard for the initial full-game arb lane."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping

_PERIOD_RE = re.compile(
    r"(?:^|[-_\s])(?:f[3579]|first[-_\s]?(?:3|5|7|9)|1st[-_\s]?(?:3|5|7|9)|"
    r"first[-_\s]?(?:three|five|seven|nine))(?:$|[-_\s])|"
    r"\b(?:first|1st)\s+(?:three|five|seven|nine|3|5|7|9)\s+innings?\b|"
    r"\binnings?\s+(?:1\s*[-–]\s*5|1\s+through\s+5)\b",
    re.I,
)


def _d(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(market: Mapping[str, Any]) -> str:
    return " | ".join(str(market.get(k) or "") for k in ("slug","question","title","subtitle","description","spreadTotalSuffix"))


def explicit_non_full_game(market: Mapping[str, Any]) -> bool:
    return bool(_PERIOD_RE.search(_text(market)))


def is_main_full_game_market(event: Mapping[str, Any], market: Mapping[str, Any]) -> bool:
    """Accept only an unqualified full-game main line for the first production lane."""
    if explicit_non_full_game(market):
        return False
    typ = str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or "").upper()
    typ = typ.removeprefix("SPORTS_MARKET_TYPE_")
    if typ == "MONEYLINE":
        return True
    line = _d(market.get("line"))
    if line is None:
        return False
    state = event.get("gameState") if isinstance(event.get("gameState"), Mapping) else {}
    if typ == "SPREAD":
        main = _d(event.get("mainSpreadLine")) or _d(state.get("mainSpreadLine"))
        # Some payloads omit the event main-line field; in that case the explicit
        # period guard is the only available signal, so keep research-only but never
        # elevate to executable until rule/scope fingerprinting later proves it.
        return main is None or abs(line) == abs(main)
    if typ == "TOTAL":
        main = _d(event.get("mainTotalLine")) or _d(state.get("mainTotalLine"))
        return main is None or abs(line) == abs(main)
    return False


def full_game_only_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        copied = deepcopy(dict(event))
        copied["markets"] = [
            dict(m) for m in event.get("markets") or []
            if isinstance(m, Mapping) and is_main_full_game_market(event, m)
        ]
        out.append(copied)
    return out
