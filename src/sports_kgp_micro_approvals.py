"""Fail-closed approval registry for micro-live sports prediction-market pairs.

The registry is deliberately separate from venue transports.  It can describe
Kalshi/Gemini, Kalshi/Polymarket, or Gemini/Polymarket pairs, but never a
sportsbook.  An approval is executable only when exact market IDs, canonical
side orientation, and a SHA-256 rule fingerprint are present.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from src.sports_kgp_micro_policy import prediction_pair_only

_ALLOWED_SIDES = frozenset({"YES", "NO", "CONTRACT"})


def _valid_sha256(value: str) -> bool:
    raw = str(value or "").strip().lower()
    return len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw)


@dataclass(frozen=True)
class MicroApproval:
    settlement_key: str
    venues: tuple[str, str]
    market_ids: dict[str, str]
    canonical_sides: dict[str, str]
    rule_fingerprint_sha256: str
    approved_for_execution: bool

    @property
    def approval_id(self) -> str:
        return f"{self.settlement_key}|{'+'.join(sorted(self.venues))}"

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "MicroApproval":
        venues_raw = row.get("venues") or []
        venues = tuple(str(v).strip().lower() for v in venues_raw)
        if len(venues) != 2:
            raise ValueError("approval requires exactly two venues")
        market_ids_raw = row.get("market_ids") or {}
        sides_raw = row.get("canonical_sides") or {}
        return cls(
            settlement_key=str(row.get("settlement_key") or "").strip(),
            venues=(venues[0], venues[1]),
            market_ids={str(k).strip().lower(): str(v).strip() for k, v in dict(market_ids_raw).items()},
            canonical_sides={str(k).strip().lower(): str(v).strip().upper() for k, v in dict(sides_raw).items()},
            rule_fingerprint_sha256=str(row.get("rule_fingerprint_sha256") or "").strip().lower(),
            approved_for_execution=bool(row.get("approved_for_execution")),
        )

    def validate(self) -> None:
        if not self.approved_for_execution:
            raise PermissionError("approval is not execution-approved")
        if not self.settlement_key:
            raise ValueError("settlement_key is required")
        if not prediction_pair_only(self.venues):
            raise ValueError("approval must be a supported prediction-market pair")
        venue_set = set(self.venues)
        if set(self.market_ids) != venue_set or any(not self.market_ids[v] for v in venue_set):
            raise ValueError("market_ids must exactly cover both venues")
        if set(self.canonical_sides) != venue_set:
            raise ValueError("canonical_sides must exactly cover both venues")
        if any(self.canonical_sides[v] not in _ALLOWED_SIDES for v in venue_set):
            raise ValueError("invalid canonical side")
        if not _valid_sha256(self.rule_fingerprint_sha256):
            raise ValueError("rule approval requires a SHA-256 fingerprint")


def load_micro_approvals(path: Path) -> dict[str, MicroApproval]:
    if not path.exists():
        return {}
    body = json.loads(path.read_text(encoding="utf-8"))
    rows = body.get("approvals") or []
    if not isinstance(rows, list):
        raise ValueError("approvals must be a list")
    out: dict[str, MicroApproval] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("approval row must be an object")
        approval = MicroApproval.from_mapping(raw)
        if not approval.approved_for_execution:
            continue
        approval.validate()
        if approval.approval_id in out:
            raise ValueError(f"duplicate approval {approval.approval_id}")
        out[approval.approval_id] = approval
    return out
