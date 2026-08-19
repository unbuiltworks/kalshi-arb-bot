"""Read-only MLB terminal-state proof gate for post-final sports arb research.

Pregame K/G/P rule tails can differ on postponement, interruption and cancellation.
Once MLB itself has repeatedly reported the exact game as Final with a stable score,
those pre-final branches may no longer be economically relevant for a full-game
half-run total. This module proves only that narrow terminal fact.

It deliberately does NOT upgrade settlement confidence, write execution approvals,
or submit orders. Any terminal-state opportunity remains research-only until a
separate reviewed policy explicitly authorizes it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import time
from typing import Any, Iterable, Mapping

import httpx

from src.mlb_game_side_normalization import canonical_team

D = Decimal
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
REQUIRED_FAMILY = "game_total"
REQUIRED_SCOPE = "full_contest"
REQUIRED_SUBJECT_TYPE = "contest"
REQUIRED_SUBJECT = "contest"
REQUIRED_STAT = "runs"
ALLOWED_COMPARATORS = frozenset({"GT", "LT"})
DEFAULT_MIN_STABLE_MS = 60_000
DEFAULT_MIN_OBSERVATIONS = 3
TERMINAL_SHADOW_CONFIDENCE = "MLB_OFFICIAL_FINAL_STABLE_RESEARCH_ONLY"


def _stable_hash(value: Mapping[str, Any]) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _is_half_run(line: Decimal) -> bool:
    twice = abs(line) * D("2")
    return twice == twice.to_integral_value() and int(twice) % 2 == 1


@dataclass(frozen=True)
class TerminalKeySpec:
    settlement_key: str
    participants: tuple[str, str]
    family: str
    scope: str
    subject_type: str
    subject: str
    stat: str
    comparator: str
    line: Decimal

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["line"] = str(self.line)
        return row


def parse_terminal_total_key(settlement_key: str) -> TerminalKeySpec:
    parts = str(settlement_key or "").split("|")
    if len(parts) != 10:
        raise ValueError("settlement key must have 10 canonical fields")
    sport, league, participants_raw, family, scope, subject_type, subject, stat, comparator, line_raw = parts
    if sport.lower() != "baseball" or league.lower() != "mlb":
        raise ValueError("terminal gate only supports MLB")
    participants = tuple(sorted(x.strip().lower() for x in participants_raw.split(",") if x.strip()))
    if len(participants) != 2 or len(set(participants)) != 2:
        raise ValueError("terminal gate requires exactly two MLB participants")
    line = _decimal(line_raw)
    if line is None or not _is_half_run(line):
        raise ValueError("terminal game-total gate requires a half-run line")
    comparator = comparator.upper()
    if (
        family.lower() != REQUIRED_FAMILY
        or scope.lower() != REQUIRED_SCOPE
        or subject_type.lower() != REQUIRED_SUBJECT_TYPE
        or subject.lower() != REQUIRED_SUBJECT
        or stat.lower() != REQUIRED_STAT
        or comparator not in ALLOWED_COMPARATORS
    ):
        raise ValueError("settlement key is outside the supported full-game total terminal lane")
    return TerminalKeySpec(
        settlement_key=str(settlement_key),
        participants=(participants[0], participants[1]),
        family=family.lower(),
        scope=scope.lower(),
        subject_type=subject_type.lower(),
        subject=subject.lower(),
        stat=stat.lower(),
        comparator=comparator,
        line=line,
    )


@dataclass(frozen=True)
class FinalGameEvidence:
    game_pk: int
    game_date: str
    away_team: str
    home_team: str
    away_runs: int
    home_runs: int
    abstract_state: str
    detailed_state: str
    status_code: str
    source: str = "MLB"

    @property
    def participants(self) -> tuple[str, str]:
        return tuple(sorted((self.away_team.lower(), self.home_team.lower())))

    @property
    def total_runs(self) -> int:
        return self.away_runs + self.home_runs

    @property
    def fingerprint(self) -> str:
        return _stable_hash({
            "game_pk": self.game_pk,
            "game_date": self.game_date,
            "away_team": self.away_team,
            "home_team": self.home_team,
            "away_runs": self.away_runs,
            "home_runs": self.home_runs,
            "abstract_state": self.abstract_state,
            "detailed_state": self.detailed_state,
            "status_code": self.status_code,
        })

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total_runs": self.total_runs, "fingerprint": self.fingerprint}


def _team_code(side: Mapping[str, Any]) -> str | None:
    team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
    for candidate in (
        team.get("abbreviation"), team.get("teamCode"), team.get("fileCode"),
        team.get("name"), team.get("teamName"), side.get("name"),
    ):
        code = canonical_team(candidate)
        if code:
            return code
    return None


def final_games_from_schedule(payload: Any) -> list[FinalGameEvidence]:
    """Extract only exact MLB Final rows with both integer scores present."""
    if not isinstance(payload, Mapping):
        return []
    out: list[FinalGameEvidence] = []
    for date_row in payload.get("dates") or []:
        if not isinstance(date_row, Mapping):
            continue
        date_text = str(date_row.get("date") or "")
        for game in date_row.get("games") or []:
            if not isinstance(game, Mapping):
                continue
            status = game.get("status") if isinstance(game.get("status"), Mapping) else {}
            abstract_state = str(status.get("abstractGameState") or "")
            detailed_state = str(status.get("detailedState") or "")
            status_code = str(status.get("codedGameState") or status.get("statusCode") or "")
            # Fail closed: only MLB's ordinary exact Final state is accepted.
            if abstract_state.lower() != "final" or detailed_state.lower() != "final":
                continue
            teams = game.get("teams") if isinstance(game.get("teams"), Mapping) else {}
            away = teams.get("away") if isinstance(teams.get("away"), Mapping) else {}
            home = teams.get("home") if isinstance(teams.get("home"), Mapping) else {}
            away_team = _team_code(away)
            home_team = _team_code(home)
            away_runs = _int(away.get("score"))
            home_runs = _int(home.get("score"))
            game_pk = _int(game.get("gamePk"))
            if None in (away_team, home_team, away_runs, home_runs, game_pk):
                continue
            assert away_team is not None and home_team is not None
            assert away_runs is not None and home_runs is not None and game_pk is not None
            if away_team == home_team or away_runs < 0 or home_runs < 0:
                continue
            out.append(FinalGameEvidence(
                game_pk=game_pk,
                game_date=str(game.get("officialDate") or date_text),
                away_team=away_team,
                home_team=home_team,
                away_runs=away_runs,
                home_runs=home_runs,
                abstract_state=abstract_state,
                detailed_state=detailed_state,
                status_code=status_code,
            ))
    return out


def match_final_game(games: Iterable[FinalGameEvidence], settlement_key: str) -> FinalGameEvidence | None:
    spec = parse_terminal_total_key(settlement_key)
    matches = [row for row in games if row.participants == spec.participants]
    return matches[0] if len(matches) == 1 else None


@dataclass
class _TrackedFinal:
    fingerprint: str
    first_seen_ms: int
    last_seen_ms: int
    observations: int


@dataclass(frozen=True)
class TerminalProof:
    settlement_key: str
    game_pk: int
    final_total_runs: int
    normalized_winning_outcome: str
    evidence_fingerprint: str
    first_seen_final_ms: int
    last_seen_final_ms: int
    stable_ms: int
    observations: int
    stable: bool
    confidence: str
    execution_eligible: bool = False
    order_entry: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TerminalFinalTracker:
    """Require repeated identical MLB Final observations before shadow eligibility."""

    def __init__(self, *, min_stable_ms: int = DEFAULT_MIN_STABLE_MS, min_observations: int = DEFAULT_MIN_OBSERVATIONS) -> None:
        if min_stable_ms < 0 or min_observations < 1:
            raise ValueError("invalid terminal stability thresholds")
        self.min_stable_ms = int(min_stable_ms)
        self.min_observations = int(min_observations)
        self._rows: dict[int, _TrackedFinal] = {}

    def observe(self, settlement_key: str, evidence: FinalGameEvidence, *, now_ms: int | None = None) -> TerminalProof:
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        spec = parse_terminal_total_key(settlement_key)
        if evidence.participants != spec.participants:
            raise ValueError("MLB Final evidence participants do not match settlement key")
        fp = evidence.fingerprint
        prior = self._rows.get(evidence.game_pk)
        if prior is None or prior.fingerprint != fp or now_ms < prior.last_seen_ms:
            row = _TrackedFinal(fp, now_ms, now_ms, 1)
        else:
            row = _TrackedFinal(fp, prior.first_seen_ms, now_ms, prior.observations + 1)
        self._rows[evidence.game_pk] = row
        stable_ms = max(0, row.last_seen_ms - row.first_seen_ms)
        stable = stable_ms >= self.min_stable_ms and row.observations >= self.min_observations
        total = D(evidence.total_runs)
        condition = total > spec.line if spec.comparator == "GT" else total < spec.line
        winning = "YES" if condition else "NO"
        return TerminalProof(
            settlement_key=settlement_key,
            game_pk=evidence.game_pk,
            final_total_runs=evidence.total_runs,
            normalized_winning_outcome=winning,
            evidence_fingerprint=fp,
            first_seen_final_ms=row.first_seen_ms,
            last_seen_final_ms=row.last_seen_ms,
            stable_ms=stable_ms,
            observations=row.observations,
            stable=stable,
            confidence=TERMINAL_SHADOW_CONFIDENCE if stable else "MLB_OFFICIAL_FINAL_OBSERVED_UNSTABLE",
            execution_eligible=False,
            order_entry=False,
        )


def terminal_shadow_annotation(proof: TerminalProof) -> dict[str, Any]:
    """Approval-shaped telemetry only; never authorizes transport."""
    return {
        **proof.as_dict(),
        "terminal_equivalence_candidate": bool(proof.stable),
        "rule_gate": "TERMINAL_STATE_RESEARCH_ONLY_NO_EXECUTION_APPROVAL",
        "approved_for_execution": False,
        "read_only": True,
        "order_entry": False,
    }


async def fetch_mlb_schedule(date_text: str, *, timeout: float = 10.0) -> Mapping[str, Any]:
    """Fetch MLB's public schedule feed for one YYYY-MM-DD date."""
    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.get(
            MLB_SCHEDULE_URL,
            params={"sportId": 1, "date": str(date_text), "hydrate": "team"},
        )
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, Mapping) else {}
