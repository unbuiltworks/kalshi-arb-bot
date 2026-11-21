"""How faithfully could PMUS have reproduced L5's actual BTC15m sequence?

PHASE 1 ONLY. The question is replication, not economics: can PMUS reproduce
what L5 DID. Nothing here may use a profitability heuristic to decide what
counts as an L5 action, and nothing here proposes a change to L5.

THREE SEPARATE OBJECTS, and keeping them separate is the whole point.

  L5_NATIVE       L5's observed sequence, translated as faithfully as PMUS
                  permits. This is the BASELINE and it is frozen. It is what
                  he did -- not what was affordable, in band, or wise.
  PMUS_PERMITTED  the subset of that sequence PMUS could actually have
                  reproduced at our receipt instant. Failures here are VENUE
                  facts: no book, a price already crossed through, a side not
                  quoted.
  SAFETY_OVERLAY  the subset OUR controls would additionally refuse. An
                  overlay refusal is a fact about our risk appetite. It may
                  stop a live probe. It may NEVER be read as "L5 would not
                  have done this" or "PMUS could not have done this".

A fidelity number that folded the overlay into the venue's capability would
measure our own caution and call it the venue's limitation, which is the
mistake this module exists to prevent.

PHASE 2 READINESS. Every field is kept per action and per window so a later
modification can be tested AGAINST this frozen baseline rather than against a
re-derived one. Nothing is aggregated away.
"""
from __future__ import annotations

import collections
import json
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: venue reasons an action could not be reproduced. NOT our controls.
VENUE_BLOCKS = ("NO_BOOK", "REQUIRES_CROSSING", "PRICE_MOVED_AWAY")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


class Path:
    """An inventory path built from an ordered action list."""

    def __init__(self) -> None:
        self.up = 0
        self.dn = 0
        self.signs: List[int] = []
        self.runs: List[int] = []
        self._run = 0
        self._last: Optional[str] = None
        self.first_opposite_i: Optional[int] = None
        self.after_opposite = 0
        self.imbalance: List[int] = []

    def add(self, i: int, side: str) -> None:
        if self._last is None:
            self._last = side
            self._run = 1
        elif side == self._last:
            self._run += 1
        else:
            self.runs.append(self._run)
            self._run = 1
            self._last = side
            if self.first_opposite_i is None:
                self.first_opposite_i = i
        if self.first_opposite_i is not None and i > self.first_opposite_i:
            self.after_opposite += 1
        if side == "UP":
            self.up += 1
        else:
            self.dn += 1
        r = self.up - self.dn
        self.imbalance.append(r)
        self.signs.append(1 if r > 0 else (-1 if r < 0 else 0))

    def close(self) -> None:
        if self._run:
            self.runs.append(self._run)
            self._run = 0

    @property
    def crossed(self) -> bool:
        nz = [s for s in self.signs if s != 0]
        return any(a != b for a, b in zip(nz, nz[1:]))

    @property
    def overshoot(self) -> int:
        """How far past zero the imbalance ever went after first changing
        sign -- L5's own corpus ends 84.3% of episodes this way."""
        nz = [s for s in self.signs if s != 0]
        if not any(a != b for a, b in zip(nz, nz[1:])):
            return 0
        first = nz[0]
        return max((abs(r) for r, s in zip(self.imbalance, self.signs)
                    if s != 0 and s != first), default=0)

    def summary(self) -> Dict[str, Any]:
        self.close()
        return {
            "actions": self.up + self.dn,
            "up": self.up, "dn": self.dn,
            "same_side_runs": self.runs,
            "longest_same_side_run": max(self.runs) if self.runs else 0,
            "repeated_same_side_before_opposite": (
                self.first_opposite_i if self.first_opposite_i is not None
                else self.up + self.dn),
            "opposite_acquired": self.first_opposite_i is not None,
            "actions_after_first_opposite": self.after_opposite,
            "terminal_matched": min(self.up, self.dn),
            "terminal_residual": self.up - self.dn,
            "max_abs_imbalance": max((abs(x) for x in self.imbalance),
                                     default=0),
            "inventory_crossed_zero": self.crossed,
            "overshoot_past_zero": self.overshoot,
            "imbalance_path": self.imbalance,
        }


def _venue_klass_and_overlay(row: Mapping[str, Any],
                             cop: Mapping[str, Any]):
    """The VENUE's answer, with the band stripped back out of it.

    Rows written before the band was demoted carry `BAND_INADMISSIBLE` as a
    copyability class, which asserts that PMUS could not reproduce an action
    when the venue was never asked -- PMUS quotes 0.01-0.99 and would have
    taken it. Those rows preserve the book and the price, so the venue
    question can be answered properly after the fact instead of inheriting a
    contaminated label.
    """
    klass = cop.get("klass")
    ov = (row.get("safety_overlay") or {}).get("would_block")
    if klass != "BAND_INADMISSIBLE":
        return klass, bool(ov)

    # legacy row: recompute from what was recorded at the time
    lp = _d(cop.get("l5_price") or (row.get("l5") or {}).get("l5_price"))
    side = (row.get("l5") or {}).get("pmus_side")
    bbo = ((row.get("pmus") or {}).get("bbo")) or {}
    yes_bid, yes_ask = _d(bbo.get("bid")), _d(bbo.get("ask"))
    if lp is None or side is None or yes_bid is None or yes_ask is None:
        return "NO_BOOK", True
    our_ask = yes_ask if side == "UP" else (D(1) - yes_bid)
    return ("REQUIRES_CROSSING" if lp >= our_ask else "MAKER_COPYABLE"), True


def fidelity(actions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """One window. `actions` are the observer's realtime rows, in order."""
    acts = sorted(actions, key=lambda r: r["l5_event_utc"])
    native = Path()
    permitted = Path()
    venue_blocked = collections.Counter()
    overlay_blocked = 0
    price_deltas: List[float] = []
    sizes: List[float] = []
    lat: List[float] = []
    reproduced = 0

    for i, r in enumerate(acts):
        l5 = r.get("l5") or {}
        side = l5.get("pmus_side")
        if side is None:
            continue
        native.add(i, side)
        cop = r.get("copyability") or {}
        klass, ov = _venue_klass_and_overlay(r, cop)
        if ov:
            overlay_blocked += 1
        if klass == "MAKER_COPYABLE":
            reproduced += 1
            permitted.add(i, side)
            dv = cop.get("price_delta_vs_our_touch")
            if dv is not None:
                price_deltas.append(float(dv))
        else:
            venue_blocked[klass or "UNKNOWN"] += 1
        sz = _d(l5.get("l5_size"))
        if sz is not None:
            sizes.append(float(sz))
        if r.get("latency_s") is not None:
            lat.append(float(r["latency_s"]))

    n = native.up + native.dn
    ns, ps = native.summary(), permitted.summary()
    return {
        "L5_NATIVE": ns,
        "PMUS_PERMITTED": ps,
        "SAFETY_OVERLAY": {
            "actions_our_controls_would_refuse": overlay_blocked,
            "pct": round(100.0 * overlay_blocked / n, 1) if n else None,
            "note": ("a fact about OUR risk appetite. The venue would have "
                     "taken these and L5 did take them."),
        },
        "reproduction": {
            "l5_actions": n,
            "pmus_reproducible": reproduced,
            "fidelity_pct": round(100.0 * reproduced / n, 1) if n else None,
            "venue_blocks": dict(venue_blocked),
            "why_blocked": {
                "NO_BOOK": "PMUS unreadable or one-sided at our receipt",
                "REQUIRES_CROSSING": "L5's price was already at or through "
                                     "our ask, so copying it would take",
                "PRICE_MOVED_AWAY": "the side existed but the price was no "
                                    "longer reachable",
            },
        },
        "sequence_fidelity": {
            "side_sequence_preserved": ns["up"] > 0 and ns["dn"] > 0
            and ps["up"] > 0 and ps["dn"] > 0,
            "opposite_acquired_native": ns["opposite_acquired"],
            "opposite_acquired_permitted": ps["opposite_acquired"],
            "longest_run_native": ns["longest_same_side_run"],
            "longest_run_permitted": ps["longest_same_side_run"],
            "crossed_zero_native": ns["inventory_crossed_zero"],
            "crossed_zero_permitted": ps["inventory_crossed_zero"],
            "overshoot_native": ns["overshoot_past_zero"],
            "overshoot_permitted": ps["overshoot_past_zero"],
            "terminal_residual_native": ns["terminal_residual"],
            "terminal_residual_permitted": ps["terminal_residual"],
        },
        "timing": {
            "observation_latency_s_median": (sorted(lat)[len(lat) // 2]
                                             if lat else None),
            "note": "latency from L5's event to our receipt; the PMUS state "
                    "was snapshotted at receipt, never at the event",
        },
        "price": {
            "l5_minus_our_touch_median": (sorted(price_deltas)[
                len(price_deltas) // 2] if price_deltas else None),
            "n": len(price_deltas),
        },
        "relative_size": {
            "l5_size_median": sorted(sizes)[len(sizes) // 2] if sizes else None,
            "l5_size_min": min(sizes) if sizes else None,
            "l5_size_max": max(sizes) if sizes else None,
            "our_q": 1,
            "note": "we mirror at q=1 regardless; L5's size is recorded so "
                    "Phase 2 can test relative sizing against this baseline",
        },
        "labels": ("PHASE 1 -- replication fidelity only. No economics, no "
                   "optimization, no change to L5."),
    }
