"""Strict, venue-agnostic sports market identity and rule-audit layer.

Purpose: let every sports venue adapter normalize game lines and player props into
one canonical schema without weakening settlement safety. This module performs no
network I/O and contains no order-entry code.

A headline match is never enough for execution. ``contract_key`` identifies the
normal-event proposition (event + market family + subject + stat + comparator +
line). ``RuleAudit`` separately fingerprints overtime, postponement, correction,
push/tie, and official-source semantics. Two contracts can therefore be useful for
shadow research while still failing closed for execution eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Iterable

D = Decimal

MARKET_FAMILIES = {
    "moneyline",
    "spread",
    "game_total",
    "team_total",
    "player_prop",
    "team_prop",
    "game_prop",
}

COMPARATORS = {"YES", "GT", "GE", "LT", "LE", "EQ"}


@dataclass(frozen=True)
class SportsEventIdentity:
    sport: str
    league: str
    participants: tuple[str, ...]
    scheduled_start_ms: int

    def __post_init__(self) -> None:
        if not self.sport.strip() or not self.league.strip():
            raise ValueError("sport and league are required")
        normalized = tuple(sorted({p.strip().lower() for p in self.participants if p.strip()}))
        if len(normalized) < 2:
            raise ValueError("at least two participants are required")
        object.__setattr__(self, "participants", normalized)
        if self.scheduled_start_ms <= 0:
            raise ValueError("scheduled_start_ms must be positive")

    def event_key(self) -> str:
        return "|".join((
            self.sport.strip().lower(),
            self.league.strip().lower(),
            ",".join(self.participants),
        ))


@dataclass(frozen=True)
class RuleAudit:
    official_source: str
    overtime: str
    postponement: str
    stat_corrections: str
    push_tie: str
    complete: bool = False

    def fingerprint(self) -> str:
        fields = (
            self.official_source.strip().lower(),
            self.overtime.strip().lower(),
            self.postponement.strip().lower(),
            self.stat_corrections.strip().lower(),
            self.push_tie.strip().lower(),
        )
        if not all(fields):
            return "INCOMPLETE"
        return sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SportsContractIdentity:
    venue: str
    market_id: str
    event: SportsEventIdentity
    market_family: str
    scope: str
    subject_type: str
    subject: str
    stat: str
    comparator: str
    line: Decimal | None
    rule_audit: RuleAudit

    def __post_init__(self) -> None:
        if not self.venue.strip() or not self.market_id.strip():
            raise ValueError("venue and market_id are required")
        if self.market_family not in MARKET_FAMILIES:
            raise ValueError(f"unsupported market_family: {self.market_family}")
        if self.comparator not in COMPARATORS:
            raise ValueError(f"unsupported comparator: {self.comparator}")
        if not self.scope.strip():
            raise ValueError("scope is required")

        subject = self.subject.strip().lower()
        stat = self.stat.strip().lower()
        subject_type = self.subject_type.strip().lower()
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "stat", stat)
        object.__setattr__(self, "subject_type", subject_type)

        if self.market_family in {"spread", "game_total", "team_total", "player_prop", "team_prop"}:
            if self.line is None:
                raise ValueError(f"line is required for {self.market_family}")
        if self.market_family == "player_prop":
            if subject_type != "player" or not subject or not stat:
                raise ValueError("player_prop requires player subject and stat")
        if self.market_family == "team_total" and (subject_type != "team" or not subject):
            raise ValueError("team_total requires team subject")
        if self.market_family == "moneyline" and subject_type != "team":
            raise ValueError("moneyline requires team subject")

    def contract_key(self) -> str:
        line = "" if self.line is None else format(self.line.normalize(), "f")
        fields = (
            self.event.event_key(),
            self.market_family,
            self.scope.strip().lower(),
            self.subject_type,
            self.subject,
            self.stat,
            self.comparator,
            line,
        )
        return "|".join(fields)

    @property
    def execution_rule_fingerprint(self) -> str:
        return self.rule_audit.fingerprint()

    @property
    def fully_rule_audited(self) -> bool:
        return self.rule_audit.complete and self.execution_rule_fingerprint != "INCOMPLETE"


@dataclass(frozen=True)
class SportsCounterpart:
    left: SportsContractIdentity
    right: SportsContractIdentity
    start_skew_ms: int
    normal_event_match: bool
    rules_match: bool
    execution_eligible: bool


def compare_contracts(
    left: SportsContractIdentity,
    right: SportsContractIdentity,
    *,
    max_start_skew_ms: int = 5 * 60 * 1000,
) -> SportsCounterpart | None:
    if left.venue == right.venue:
        return None
    if left.contract_key() != right.contract_key():
        return None
    skew = abs(left.event.scheduled_start_ms - right.event.scheduled_start_ms)
    if skew > max_start_skew_ms:
        return None
    rules_match = (
        left.fully_rule_audited
        and right.fully_rule_audited
        and left.execution_rule_fingerprint == right.execution_rule_fingerprint
    )
    return SportsCounterpart(
        left=left,
        right=right,
        start_skew_ms=skew,
        normal_event_match=True,
        rules_match=rules_match,
        execution_eligible=rules_match,
    )


def find_counterparts(
    contracts: Iterable[SportsContractIdentity],
    *,
    max_start_skew_ms: int = 5 * 60 * 1000,
) -> list[SportsCounterpart]:
    rows = list(contracts)
    out: list[SportsCounterpart] = []
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            match = compare_contracts(left, right, max_start_skew_ms=max_start_skew_ms)
            if match is not None:
                out.append(match)
    out.sort(key=lambda x: (x.left.contract_key(), x.start_skew_ms, x.left.venue, x.right.venue))
    return out


# Canonical stat names are intentionally small and explicit. Venue adapters may add
# aliases in config, but the universal core never fuzzy-matches free text.
CANONICAL_STATS = {
    "baseball": {
        "hits", "home_runs", "total_bases", "rbis", "runs", "strikeouts", "walks", "outs_recorded",
    },
    "football": {
        "passing_yards", "passing_tds", "interceptions", "rushing_yards", "receiving_yards",
        "receptions", "rushing_tds", "receiving_tds", "anytime_td",
    },
    "basketball": {
        "points", "rebounds", "assists", "three_pointers", "steals", "blocks", "turnovers",
        "points_rebounds_assists",
    },
    "hockey": {
        "goals", "assists", "points", "shots_on_goal", "goalie_saves",
    },
}


def stat_supported(sport: str, stat: str) -> bool:
    return stat.strip().lower() in CANONICAL_STATS.get(sport.strip().lower(), set())
