"""PROSPECTIVE residual-episode diagnostics. PREREGISTERED 2026-09-17, before
any prospective data existed.

OWNER 2026-09-17. MATCHED_SHARE_LINEAGE_V1 found, on history, that the
complement has to arrive fast: 5-30s pairs earned +138 bp/$ out of sample while
pairs completed after 120s were value-DESTROYING (-192 bp/$) and after 300s
badly so (-475 bp/$). It also found matched economics concentrated late in the
window and residual loss rising monotonically with run position.

THOSE ARE NOT GATES AND ARE NOT BECOMING GATES. No `TTE<60`, no
`complement_timeout=120`, no `run cap=N` is created from those historical
observations. This module instead measures, FORWARD, whether the same
relationships appear at all -- which is the only thing that would justify a
gate later.

THE MARKS ARE FIXED HERE AND NOW: 5, 15, 30, 60, 120, 300 seconds from the
first evidenced same-side fill. They were chosen to straddle the historical
5-30s / 120s+ boundary so the prospective data can CONTRADICT it. Changing a
mark after data exists invalidates the preregistration.

EVIDENCE DISCIPLINE. A complement counts only when it clears the same strict
criterion the inventory does. The weaker rungs -- the quote merely being
RESTABLE, the book reaching it, flow printing through it -- are recorded
separately at every mark because the GAP between them is the finding, and are
never promoted into a complement.

NOTHING HERE IS A FILL AND NOTHING HERE IS REALIZED. Settlement cashflow is
attached only when an authoritative settlement exists, and until then the field
is the string SETTLEMENT_PENDING rather than a zero or an estimate.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "RESIDUAL_EPISODE_CLOCK_V1"
#: PREREGISTERED. Frozen 2026-09-17 before any prospective observation.
MARKS_S = (5, 15, 30, 60, 120, 300)
STRICT = "VOLUME_AHEAD_CLEARED"
PENDING = "SETTLEMENT_PENDING"

#: recorded at every mark, each one separately, never collapsed
RUNGS = ("quote_restable", "book_reached_quote", "trade_flow_through_quote",
         STRICT)


class Episode:
    """One residual episode, opened by an evidenced first-side fill."""

    __slots__ = ("key", "venue", "symbol", "opened_t", "first_side",
                 "first_px", "first_qty", "tte_at_open_s", "run_position",
                 "marks", "complement", "settled", "closed")

    def __init__(self, key: str, *, venue: str, symbol: str, t: float,
                 side: str, px: float, qty: float, tte_s: Optional[float],
                 run_position: int):
        self.key = key
        self.venue, self.symbol = venue, symbol
        self.opened_t = t
        self.first_side, self.first_px, self.first_qty = side, px, qty
        self.tte_at_open_s = tte_s
        self.run_position = run_position
        self.marks: Dict[int, dict] = {}
        self.complement: Optional[dict] = None
        self.settled: Any = PENDING
        self.closed = False

    def due(self, now: float) -> List[int]:
        return [m for m in MARKS_S
                if m not in self.marks and now >= self.opened_t + m]

    def record(self, mark: int, snapshot: Mapping[str, Any]) -> dict:
        """`snapshot` is supplied by the venue-specific recorder and must carry
        every rung separately plus the inventory and pair economics as that
        venue defines them."""
        row = {
            "kind": "residual_episode_mark", "label": LABEL,
            "episode": self.key, "venue": self.venue, "symbol": self.symbol,
            "mark_s": mark,
            "opened_t": self.opened_t,
            "first_side": self.first_side, "first_px": self.first_px,
            "first_qty": self.first_qty,
            "tte_at_open_s": self.tte_at_open_s,
            "run_position_at_open": self.run_position,
            # --- what actually happened by this mark
            "evidenced_complement": bool(snapshot.get("evidenced_complement")),
            "complement_dt_s": snapshot.get("complement_dt_s"),
            "rungs": {r: snapshot.get(r) for r in RUNGS},
            "inventory": snapshot.get("inventory"),
            "residual": snapshot.get("residual"),
            "quote_validity": snapshot.get("quote_validity"),
            "pair_economics": snapshot.get("pair_economics"),
            "settled_cashflow": self.settled,
            "labels": (
                "PREREGISTERED prospective diagnostic, not a gate. A complement "
                "counts only at %s; the weaker rungs are reported separately "
                "and are never promoted. settled_cashflow is %s until an "
                "authoritative settlement exists and is never a zero or an "
                "estimate." % (STRICT, PENDING)),
        }
        self.marks[mark] = row
        return row

    def close(self, settled_cashflow: Any) -> dict:
        self.settled = settled_cashflow
        self.closed = True
        return {
            "kind": "residual_episode_settled", "label": LABEL,
            "episode": self.key, "venue": self.venue, "symbol": self.symbol,
            "opened_t": self.opened_t,
            "first_side": self.first_side, "first_px": self.first_px,
            "tte_at_open_s": self.tte_at_open_s,
            "run_position_at_open": self.run_position,
            "marks_recorded": sorted(self.marks),
            "complement": self.complement,
            "settled_cashflow": settled_cashflow,
            "labels": ("settled_cashflow is AUTHORITATIVE venue cash. The "
                       "matched/residual split of it is an ACCOUNTING "
                       "ATTRIBUTION and is reported elsewhere with its "
                       "convention named -- never as a cashflow."),
        }


class Clock:
    """Holds the open episodes for one recorder."""

    def __init__(self, venue: str):
        self.venue = venue
        self.open: Dict[str, Episode] = {}
        self.n_opened = 0

    def open_episode(self, key: str, **kw) -> Episode:
        ep = Episode(key, venue=self.venue, **kw)
        self.open[key] = ep
        self.n_opened += 1
        return ep

    def due_marks(self, now: float):
        for key, ep in list(self.open.items()):
            for m in ep.due(now):
                yield ep, m

    def retire(self, key: str) -> None:
        self.open.pop(key, None)


# ---------------------------------------------------------------------------
# venue-specific economic mapping, kept explicitly APART
#
# Polymarket and Gemini do not have the same object and pretending they do is
# the error this function exists to prevent.

def pair_economics(venue: str, *, buy_px: Optional[float],
                   sell_px: Optional[float] = None,
                   complement_buy_px: Optional[float] = None,
                   buy_fee: Any = None, sell_fee: Any = None) -> dict:
    """PMUS: a pair is BUY YES at b and BUY NO at c -- two separate tokens,
    both owned, cost b + c.

    GEMINI: there is ONE contract (the -DOWN book does not exist), so the
    analogue is BUY at b and SELL at s on the same instrument. The sale is a
    SYNTHETIC complement worth (1 - s) as a cost, giving b + (1 - s). The legs
    are kept apart -- buy_cost, sell_proceeds, synthetic_complement_cost -- so
    nothing reads as if a second contract were owned.
    """
    if venue == "GEMINI":
        synth = (1.0 - sell_px) if sell_px is not None else None
        pair = (buy_px + synth) if (buy_px is not None and synth is not None) else None
        return {
            "venue": venue, "mapping": "BUY b + SELL s on ONE contract",
            "buy_cost": buy_px,
            "sell_proceeds": sell_px,
            "synthetic_complement_cost": synth,
            "effective_pair_cost": pair,
            "edge_per_unit": (1.0 - pair) if pair is not None else None,
            "buy_fee": buy_fee, "sell_fee": sell_fee,
            "fees_netted": False,
            "realized": False,
            "labels": (
                "SYNTHETIC. Not a realized pair. It becomes realized only when "
                "BOTH orders actually fill AND authoritative settlement/cash "
                "accounting confirms it. One leg filled is an open position, "
                "not half a pair. No second contract is owned -- the sale is a "
                "synthetic complement, and fees on BOTH transactions are "
                "carried separately and never netted in here."),
        }
    pair = ((buy_px + complement_buy_px)
            if (buy_px is not None and complement_buy_px is not None) else None)
    return {
        "venue": venue, "mapping": "BUY YES b + BUY NO c, two tokens owned",
        "buy_cost": buy_px, "complement_buy_cost": complement_buy_px,
        "effective_pair_cost": pair,
        "edge_per_unit": (1.0 - pair) if pair is not None else None,
        "buy_fee": buy_fee, "complement_fee": sell_fee,
        "fees_netted": False, "realized": False,
        "labels": ("Both legs are owned outright. Still NOT realized until "
                   "settlement cash confirms it."),
    }
