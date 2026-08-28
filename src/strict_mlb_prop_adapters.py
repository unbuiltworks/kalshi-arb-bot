"""Strict read-only MLB prop contract adapters for Polymarket US <-> Kalshi.

These adapters intentionally reject fuzzy cross-family candidates. A pair is only
normalized when all of the following agree:

* explicit/derived Polymarket prop family -> exact Kalshi series allowlist
* MLB event identity from structured slug/event metadata
* player identity
* exact stat family
* exact threshold
* explicit rule-audit evidence for both venues

Polymarket's sports UI/API shape has changed over time.  The adapter therefore does
not require one historical English sentence or one historical slug prefix.  Instead
it requires independent structured/semantic fields to corroborate each other and
fails closed on disagreement.

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

# Historical exact shape retained for backward compatibility/tests.
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

# Current production props still carry an MLB game identity in the slug, but the
# prefix and wording are not part of the contract semantics.  Parse only the stable
# game tuple and corroborate the stat/threshold independently below.
POLY_GAME_SLUG_RE = re.compile(
    r"(?:^|-)mlb-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:-|$)",
    re.I,
)
POLY_SLUG_THRESHOLD_RE = re.compile(
    r"(?:gte|ge|atleast|at-least)(?P<threshold>\d+(?:pt\d+)?)",
    re.I,
)
POLY_SLUG_FAMILY_PATTERNS: Mapping[str, tuple[re.Pattern[str], ...]] = {
    "baseball_player_hits_runs_rbis": (
        re.compile(r"(?:^|-)hrr(?:-|$)", re.I),
        re.compile(r"(?:^|-)hits-runs-rbis(?:-|$)", re.I),
    ),
    "baseball_player_total_bases": (
        re.compile(r"(?:^|-)tb(?:-|$)", re.I),
        re.compile(r"(?:^|-)total-bases(?:-|$)", re.I),
    ),
    "baseball_player_strikeouts": (
        re.compile(r"(?:^|-)(?:k|so|strikeouts)(?:-|$)", re.I),
    ),
    "baseball_player_hits": (
        re.compile(r"(?:^|-)hits(?:-|$)", re.I),
    ),
}

POLY_TEXT_PATTERNS = (
    re.compile(
        r"^Will\s+(?P<player>.+?)\s+(?:record|have|get)\s+"
        r"(?:at\s+least\s+)?(?P<threshold>\d+(?:\.\d+)?)\+?\s+"
        r"(?P<stat>hits\s*\+\s*runs\s*\+\s*RBIs|total\s+bases|pitching\s+strikeouts|strikeouts|hits)"
        r"(?:\s+in\s+(?P<a>[A-Z0-9]+)\s+vs\.?\s+(?P<b>[A-Z0-9]+))?\??$",
        re.I,
    ),
    re.compile(
        r"^(?P<player>.+?):\s*(?P<threshold>\d+(?:\.\d+)?)\+\s*"
        r"(?P<stat>hits\s*\+\s*runs\s*\+\s*RBIs|total\s+bases|pitching\s+strikeouts|strikeouts|hits)\??$",
        re.I,
    ),
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
    "hits+runs+rbis": "baseball_player_hits_runs_rbis",
    "total bases": "baseball_player_total_bases",
    "pitching strikeouts": "baseball_player_strikeouts",
    "strikeouts": "baseball_player_strikeouts",
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


def _family_from_stat_text(value: Any) -> str | None:
    text = _norm(value).replace(" + ", "+")
    if text == "hits+runs+rbis":
        return "baseball_player_hits_runs_rbis"
    if text == "total bases":
        return "baseball_player_total_bases"
    if text in {"pitching strikeouts", "strikeouts"}:
        return "baseball_player_strikeouts"
    if text == "hits":
        return "baseball_player_hits"
    return None


def _family_from_slug(slug: str) -> str | None:
    # Longest/most-specific family first so HRR never degrades into plain hits.
    matches = [
        family
        for family, patterns in POLY_SLUG_FAMILY_PATTERNS.items()
        if any(pattern.search(slug) for pattern in patterns)
    ]
    return matches[0] if len(matches) == 1 else None


def _parse_poly_text(value: Any) -> tuple[str, Decimal, str, tuple[str, str] | None] | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in POLY_TEXT_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        threshold = _decimal(match.group("threshold"))
        family = _family_from_stat_text(match.group("stat"))
        player = match.group("player").strip()
        if threshold is None or family is None or not player:
            return None
        a = match.groupdict().get("a")
        b = match.groupdict().get("b")
        teams = (a.lower(), b.lower()) if a and b else None
        return player, threshold, family, teams
    return None


def _poly_game_tuple(poly: Mapping[str, Any]) -> tuple[tuple[str, str], str] | None:
    slug = str(poly.get("slug") or "")
    historical = POLY_SLUG_RE.fullmatch(slug)
    if historical is not None:
        return (
            (historical.group("a").lower(), historical.group("b").lower()),
            historical.group("date"),
        )
    current = POLY_GAME_SLUG_RE.search(slug)
    if current is None:
        return None
    return (
        (current.group("a").lower(), current.group("b").lower()),
        current.group("date"),
    )


@dataclass(frozen=True)
class RuleAuditEvidence:
    """Minimum evidence needed before a strict pair may be called rule-audited."""

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
    """Return a strict semantic identity across historical and current Poly shapes.

    Required corroboration:
    - MLB game tuple/date from slug;
    - player/stat/threshold from question/title;
    - line equals the text threshold;
    - detailed sportsMarketType must agree when present;
    - otherwise sportsMarketTypeV2 must explicitly say PROP and the slug stat must
      independently agree with the text family;
    - any stat/threshold/team information present in the slug/question must agree.
    """

    slug = str(poly.get("slug") or "")
    game = _poly_game_tuple(poly)
    if game is None:
        return None
    slug_teams, game_date = game

    parsed_text = _parse_poly_text(poly.get("question") or poly.get("title"))
    if parsed_text is None:
        return None
    player, text_threshold, text_family, text_teams = parsed_text

    detailed_family = str(poly.get("sportsMarketType") or "")
    if detailed_family in FAMILY_SERIES:
        family = detailed_family
        if family != text_family:
            return None
    else:
        prop_type = str(poly.get("sportsMarketTypeV2") or "").upper()
        if prop_type not in {"PROP", "SPORTS_MARKET_TYPE_PROP"}:
            return None
        slug_family = _family_from_slug(slug)
        if slug_family is None or slug_family != text_family:
            return None
        family = text_family

    slug_family = _family_from_slug(slug)
    if slug_family is not None and slug_family != family:
        return None

    if text_teams is not None and set(text_teams) != set(slug_teams):
        return None

    line = _decimal(poly.get("line"))
    if line is None or line != text_threshold:
        return None

    historical = POLY_SLUG_RE.fullmatch(slug)
    if historical is not None:
        if STAT_CODE_TO_FAMILY.get(historical.group("stat").lower()) != family:
            return None
        historical_threshold = _decimal(historical.group("threshold"))
        if historical_threshold != line:
            return None
    else:
        threshold_match = POLY_SLUG_THRESHOLD_RE.search(slug)
        if threshold_match is not None:
            slug_threshold = _decimal(threshold_match.group("threshold"))
            if slug_threshold != line:
                return None

    return family, player, slug_teams, game_date, line


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
    """Return one strict shadow pair or None; fail closed on any disagreement."""

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
