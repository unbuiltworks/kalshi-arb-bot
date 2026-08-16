"""Strict read-only MLB prop contract adapters for Polymarket US <-> Kalshi.

These adapters intentionally reject fuzzy cross-family candidates. A pair is only
normalized when all of the following agree:

* explicit Polymarket sportsMarketType -> Kalshi series allowlist
* MLB event identity from structured slugs/tickers
* player identity
* exact stat family
* exact threshold
* explicit rule-audit evidence for both venues

The module has no order-entry code and never returns executable confidence. Even a
fully audited pair is emitted as FULL_RULES_AUDITED_SHADOW for research only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping


FAMILY_SERIES: Mapping[str, str] = {
    "baseball_player_hits": "KXMLBHIT",
    "baseball_player_hits_runs_rbis": "KXMLBHRR",
    "baseball_player_total_bases": "KXMLBTB",
    "baseball_player_strikeouts": "KXMLBKS",
}

FAMILY_LABELS: Mapping[str, tuple[str, ...]] = {
    "baseball_player_hits": ("hits",),
    "baseball_player_hits_runs_rbis": ("hits + runs + rbis", "hits+runs+rbis"),
    "baseball_player_total_bases": ("total bases",),
    "baseball_player_strikeouts": ("strikeouts", "pitching strikeouts"),
}

POLY_SLUG_RE = re.compile(
    r"^astatc-mlb-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<stat>hits|hrr|tb|k)-"
    r"(?P<player>[a-z0-9]+)-gte(?P<threshold>\d+(?:pt\d+)?)$",
    re.I,
)
POLY_QUESTION_RE = re.compile(
    r"^Will (?P<player>.+?) record at least (?P<threshold>\d+(?:\.\d+)?) "
    r"(?P<stat>hits|hits \+ runs \+ RBIs|total bases|pitching strikeouts) in "
    r"(?P<a>[A-Z0-9]+) vs (?P<b>[A-Z0-9]+)\?$",
    re.I,
)
KALSHI_PLAYER_LINE_RE = re.compile(
    r"^(?P<player>.+?):\s*(?P<threshold>\d+(?:\.\d+)?)\+\s*"
    r"(?P<stat>hits|hits \+ runs \+ RBIs|total bases|strikeouts)\?",
    re.I,
)
EVENT_SUFFIX_RE = re.compile(r"(?P<a>[A-Z]{2,4})(?P<b>[A-Z]{2,4})$")

STAT_CODE_TO_FAMILY = {
    "hits": "baseball_player_hits",
    "hrr": "baseball_player_hits_runs_rbis",
    "tb": "baseball_player_total_bases",
    "k": "baseball_player_strikeouts",
}
QUESTION_STAT_TO_FAMILY = {
    "hits": "baseball_player_hits",
    "hits + runs + rbis": "baseball_player_hits_runs_rbis",
    "total bases": "baseball_player_total_bases",
    "pitching strikeouts": "baseball_player_strikeouts",
}
KALSHI_STAT_TO_FAMILY = {
    "hits": "baseball_player_hits",
    "hits + runs + rbis": "baseball_player_hits_runs_rbis",
    "total bases": "baseball_player_total_bases",
    "strikeouts": "baseball_player_strikeouts",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("’", "'").split())


def _name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _norm(value))


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace("pt", "."))
    except (InvalidOperation, ValueError):
        return None


def _market_text(market: Mapping[str, Any]) -> str:
    return " | ".join(
        str(market.get(k) or "")
        for k in ("title", "subtitle", "yes_sub_title", "no_sub_title")
        if str(market.get(k) or "").strip()
    )


@dataclass(frozen=True)
class RuleAuditEvidence:
    """Minimum evidence needed before a strict pair may be called rule-audited.

    The calling audit collector is responsible for fetching these from the public
    venue metadata/rules surfaces. URLs alone are not enough: both rule summaries
    must have been reviewed and positively marked equivalent for the exact family.
    """

    kalshi_series: str
    kalshi_settlement_sources: tuple[str, ...]
    kalshi_contract_terms_url: str
    polymarket_rules_ref: str
    kalshi_rule_summary: str
    polymarket_rule_summary: str
    equivalent: bool

    def valid_for(self, expected_series: str) -> bool:
        return bool(
            self.equivalent
            and self.kalshi_series == expected_series
            and self.kalshi_settlement_sources
            and self.kalshi_contract_terms_url.strip()
            and self.polymarket_rules_ref.strip()
            and self.kalshi_rule_summary.strip()
            and self.polymarket_rule_summary.strip()
        )


@dataclass(frozen=True)
class StrictMLBPropPair:
    family: str
    settlement_key: str
    poly_slug: str
    kalshi_ticker: str
    player: str
    teams: tuple[str, str]
    game_date: str
    threshold: Decimal
    settlement_confidence: str = "FULL_RULES_AUDITED_SHADOW"
    read_only: bool = True
    order_entry: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "settlement_key": self.settlement_key,
            "poly_slug": self.poly_slug,
            "kalshi_ticker": self.kalshi_ticker,
            "player": self.player,
            "teams": list(self.teams),
            "game_date": self.game_date,
            "threshold": str(self.threshold),
            "settlement_confidence": self.settlement_confidence,
            "read_only": self.read_only,
            "order_entry": self.order_entry,
        }


def _poly_identity(poly: Mapping[str, Any]) -> tuple[str, str, tuple[str, str], str, Decimal] | None:
    family = str(poly.get("sportsMarketType") or "")
    if family not in FAMILY_SERIES:
        return None

    slug = str(poly.get("slug") or "")
    sm = POLY_SLUG_RE.fullmatch(slug)
    if sm is None or STAT_CODE_TO_FAMILY.get(sm.group("stat").lower()) != family:
        return None

    question = str(poly.get("question") or poly.get("title") or "")
    qm = POLY_QUESTION_RE.fullmatch(question)
    if qm is None or QUESTION_STAT_TO_FAMILY.get(_norm(qm.group("stat"))) != family:
        return None

    slug_teams = (sm.group("a").lower(), sm.group("b").lower())
    question_teams = (qm.group("a").lower(), qm.group("b").lower())
    if set(slug_teams) != set(question_teams):
        return None

    slug_threshold = _decimal(sm.group("threshold"))
    question_threshold = _decimal(qm.group("threshold"))
    line = _decimal(poly.get("line"))
    if None in (slug_threshold, question_threshold, line):
        return None
    assert slug_threshold is not None and question_threshold is not None and line is not None
    if not (slug_threshold == question_threshold == line):
        return None

    player = qm.group("player").strip()
    if not player:
        return None
    return family, player, slug_teams, sm.group("date"), line


def _kalshi_identity(
    event: Mapping[str, Any], market: Mapping[str, Any], expected_family: str
) -> tuple[str, Decimal] | None:
    expected_series = FAMILY_SERIES[expected_family]
    series = str(event.get("series_ticker") or market.get("series_ticker") or "")
    if series != expected_series:
        return None

    ticker = str(market.get("ticker") or "")
    if not ticker.startswith(expected_series + "-"):
        return None

    event_ticker = str(event.get("event_ticker") or market.get("event_ticker") or "")
    if not event_ticker.startswith(expected_series + "-"):
        return None

    # Require the exact stat phrase in the event title; this prevents Hits from
    # matching Hits+Runs+RBIs and vice versa even when player/threshold align.
    event_title = _norm(event.get("title") or event.get("sub_title"))
    if not any(label in event_title for label in FAMILY_LABELS[expected_family]):
        return None
    if expected_family == "baseball_player_hits" and "hits + runs + rbis" in event_title:
        return None

    text = _market_text(market)
    mm = KALSHI_PLAYER_LINE_RE.search(text)
    if mm is None or KALSHI_STAT_TO_FAMILY.get(_norm(mm.group("stat"))) != expected_family:
        return None
    threshold = _decimal(mm.group("threshold"))
    if threshold is None:
        return None
    return mm.group("player").strip(), threshold


def strict_match(
    poly: Mapping[str, Any],
    kalshi_event: Mapping[str, Any],
    kalshi_market: Mapping[str, Any],
    audit: RuleAuditEvidence | None,
) -> StrictMLBPropPair | None:
    """Return one strict shadow pair or None.

    Fail closed on any identity, family, threshold, or rule-audit mismatch.
    """

    p = _poly_identity(poly)
    if p is None:
        return None
    family, poly_player, teams, game_date, threshold = p
    expected_series = FAMILY_SERIES[family]

    if audit is None or not audit.valid_for(expected_series):
        return None

    k = _kalshi_identity(kalshi_event, kalshi_market, family)
    if k is None:
        return None
    kalshi_player, kalshi_threshold = k
    if _name(poly_player) != _name(kalshi_player) or threshold != kalshi_threshold:
        return None

    # Event ticker must contain the exact ordered or reversed team-code pair.
    event_ticker = str(kalshi_event.get("event_ticker") or kalshi_market.get("event_ticker") or "").upper()
    a, b = (x.upper() for x in teams)
    if not (event_ticker.endswith(a + b) or event_ticker.endswith(b + a)):
        return None

    key = ":".join(
        [
            "mlb",
            game_date,
            "-".join(sorted(teams)),
            family,
            _name(poly_player),
            format(threshold.normalize(), "f"),
        ]
    )
    return StrictMLBPropPair(
        family=family,
        settlement_key=key,
        poly_slug=str(poly.get("slug") or ""),
        kalshi_ticker=str(kalshi_market.get("ticker") or ""),
        player=poly_player,
        teams=teams,
        game_date=game_date,
        threshold=threshold,
    )


def supported_families() -> dict[str, str]:
    return dict(FAMILY_SERIES)
