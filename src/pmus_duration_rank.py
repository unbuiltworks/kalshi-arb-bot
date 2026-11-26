"""Expected remaining quoting time per market (pure; ranking only).

Owner 2026-11-26: a market about to end should not take a selection slot
from one with a long remaining life. This module estimates how long a
candidate can still be quoted before its late cut-off, from data the
sports bot already holds:

  competition   the slug's competition prefix (`aec-<comp>-...`)
  game_start    the scheduled start the slate listing carried (epoch s)
  live state    the venue match state the bot already reads (`ended`,
                and whether the venue lists the market as live at all)

DURATION TABLE. Minutes from the start of play to the late cut-off (the
point where `late_for` refuses new exposure). Defaults are deliberately
SHORT (a shorter life can only cost a market rank, never buy one):

  setkame*   40   Setka Cup table tennis (setkameua, setkamecz, ...)
  atp, wta  120   tour singles, best of three, to the deciding set
  atpdb,     80   tour doubles (match tie-break deciding set)
  wtadb
  utr        75   UTR pro tennis
  itf*      110   ITF men's / women's (itfme, itfwo, ...)

A key ending in `*` matches every competition starting with it; an exact
key beats a pattern, a longer pattern beats a shorter one. Override or
extend with PMUS_RN1_DURATION_MIN="comp:minutes,comp*:minutes,...".

REMAINING TIME (seconds), or None when it cannot be known:

  state says ended                -> 0
  competition not in the table    -> None ("unknown_competition")
  no scheduled start              -> None ("unknown_start")
  before the start                -> (start - now) + duration
  start passed, venue lists live  -> start + duration - now (floor 0)
  start passed, not listed live   -> None ("start_passed_not_live"): the
                                     start slipped; when play begins is
                                     not known

FACTOR = min(remaining, horizon) / horizon, in [0, 1]. Unknown remaining
gets a fixed, conservative factor (default 0.5): no full-life credit, and
never excluded only for being unknown. A known remaining below the
minimum (default 900 s) excludes the candidate.

Nothing here places, cancels or reads the venue.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

DEFAULT_MINUTES: Dict[str, float] = {
    "setkame*": 40.0,
    "atp": 120.0, "wta": 120.0,
    "atpdb": 80.0, "wtadb": 80.0,
    "utr": 75.0,
    "itf*": 110.0,
}


def parse_table(raw: Optional[str],
                base: Optional[Mapping[str, float]] = None
                ) -> Dict[str, float]:
    """`base` (default DEFAULT_MINUTES) updated by "comp:min,..." entries.
    Malformed or non-positive entries are ignored."""
    out = dict(DEFAULT_MINUTES if base is None else base)
    for part in str(raw or "").split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        try:
            m = float(v.strip())
        except ValueError:
            continue
        if k and m > 0:
            out[k] = m
    return out


def competition(slug: str) -> str:
    parts = str(slug).split("-")
    return parts[1].lower() if len(parts) > 1 else ""


def duration_s(comp: str, table: Mapping[str, float]) -> Optional[float]:
    comp = str(comp or "").lower()
    if not comp:
        return None
    if comp in table:
        return float(table[comp]) * 60.0
    best = None
    for k, m in table.items():
        if k.endswith("*") and comp.startswith(k[:-1]):
            if best is None or len(k) > len(best[0]):
                best = (k, m)
    return None if best is None else float(best[1]) * 60.0


def remaining_s(slug: str, now: float, game_start: Optional[float],
                state: Optional[Mapping[str, Any]], listed_live: bool,
                table: Mapping[str, float]
                ) -> Tuple[Optional[float], str]:
    """(seconds left to the late cut-off, or None; how it was decided)."""
    if state and state.get("ended"):
        return 0.0, "ended"
    dur = duration_s(competition(slug), table)
    if dur is None:
        return None, "unknown_competition"
    if game_start is None:
        return None, "unknown_start"
    gs = float(game_start)
    if now < gs:
        return (gs - now) + dur, "pregame"
    if not listed_live:
        return None, "start_passed_not_live"
    return max(0.0, gs + dur - now), "live"


def factor(rem: Optional[float], horizon_s: float,
           unknown_factor: float) -> float:
    if rem is None:
        return max(0.0, min(1.0, float(unknown_factor)))
    if horizon_s <= 0:
        return 1.0
    return max(0.0, min(float(rem), horizon_s)) / horizon_s


def excluded(rem: Optional[float], min_remaining_s: float) -> bool:
    """Only a KNOWN remaining time below the minimum excludes."""
    return rem is not None and min_remaining_s > 0 and rem < min_remaining_s
