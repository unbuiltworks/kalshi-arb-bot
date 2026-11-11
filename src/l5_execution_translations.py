"""Four read-only counterfactual EXECUTION TRANSLATIONS of L5's sequence.

PHASE 1, SHADOW COUNTERFACTUALS ONLY. No order exists, no authorization is
sought, and none of this modifies the frozen production/shadow baseline. It
answers one question:

    what execution translation is required for PMUS to reproduce L5_NATIVE,
    and what fidelity remains impossible even after we remove our OWN
    maker-only and q=1 constraints?

WHY THIS EXISTS. `REQUIRES_CROSSING` was previously read as a venue
limitation. It is not. It proves only that OUR maker-only translation cannot
reproduce the action at L5's translated price; PMUS would have taken a
marketable order at the displayed touch. Attributing it to the venue
overstated what PMUS cannot do and understated what our translation chooses
not to do.

THE FOUR TRACKS. Two axes, both of which are OUR constraints, not L5's:

    T1  q=1            + maker-only          the current baseline
    T2  q=1            + cross when maker reproduction is impossible
    T3  proportional   + maker-only
    T4  proportional   + cross when maker reproduction is impossible

=== DEFINITIONS PRE-REGISTERED BEFORE ANY RESULT WAS COMPUTED ===

SIZING NORMALISATION. `LAMBDA_REF` scales every L5 action by the SAME factor,

    lambda = 1 / median(l5_size over the preserved corpus)

so the MEDIAN L5 action maps to exactly 1 share -- the same central scale as
q=1, which makes T3/T4 comparable to T1/T2 rather than merely larger. L5's
relative geometry is preserved exactly: every action is `l5_size * lambda`.
The factor is computed from L5's OWN SIZES ONLY. It never reads a price, a
fill, an outcome or a P&L, so it cannot be chosen to flatter a result. The
implied hypothetical capital per window is REPORTED, never tuned, and no
second cap is imposed because clipping would distort the geometry this track
exists to preserve.

CROSSING. Only the PMUS touch recorded in the row itself, i.e. observed AT OUR
RECEIPT TIMESTAMP. There is no backdating and no later, more favourable price.

    UP   (buy YES)  crosses at `ask`
    DOWN (buy NO)   crosses at `1 - bid`, because buying NO at p is selling
                    YES at 1-p, so the NO ask is one minus the YES bid

The fill is capped at the size DISPLAYED at that touch (`ask_shares` for UP,
`bid_shares` for DOWN). A touch with no displayed size does not fill and is
recorded as NO_DISPLAYED_SIZE rather than silently assumed. The applicable
PMUS TAKER FEE is charged on every crossed fill.

CROSSING IS USED ONLY WHERE MAKER REPRODUCTION IS IMPOSSIBLE, i.e. where the
venue class is REQUIRES_CROSSING. An action that WAS maker-reproducible and
simply never traded is a habitat failure, not something to cross into.

VENUE / HABITAT FAILURES ARE PRESERVED SEPARATELY AND NEVER BECOME
ACQUISITIONS IN ANY TRACK:

    NO_BOOK                   no readable two-sided PMUS book at receipt
    NO_SUBSEQUENT_INTERACTION maker-reproducible, but nothing ever traded

These are facts about the venue and the habitat. No change to our own
execution translation can remove them, which is precisely why they are kept
out of the four tracks' reproduction rates.

THE MAKER PATH IS UNCHANGED. A maker acquisition still requires authenticated
STRICT evidence (TRADE_THROUGH_PRICE); the counterfactual relaxes our sizing
and our willingness to cross, never our evidence standard.
"""
from __future__ import annotations

import collections
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_fees as fees

#: the median L5 action maps to this many shares under proportional sizing
REF_SHARES = D("1")

#: STRICT: only a print THROUGH our price proves the queue ahead was cleared
STRICT_CLASSES = ("TRADE_THROUGH_PRICE",)

TRACKS = ("T1_q1_maker", "T2_q1_cross", "T3_prop_maker", "T4_prop_cross")

#: never an acquisition in any track; a fact about the venue, not our method
HABITAT_FAILURES = ("NO_BOOK", "NO_SUBSEQUENT_INTERACTION", "NO_DISPLAYED_SIZE")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def lambda_ref(rows: Sequence[Mapping[str, Any]]) -> D:
    """The pre-registered sizing factor. L5 sizes only -- no price, no P&L."""
    sizes = [float(r["l5"]["l5_size"]) for r in rows
             if (r.get("l5") or {}).get("l5_size")]
    if not sizes:
        return D("1")
    med = D(str(statistics.median(sizes)))
    return (REF_SHARES / med) if med > 0 else D("1")


def cross_price(side: str, bbo: Mapping[str, Any]) -> Optional[D]:
    """The price WE would pay to take, from the touch observed at receipt."""
    bid, ask = _d(bbo.get("bid")), _d(bbo.get("ask"))
    if side == "UP":
        return ask
    return (D(1) - bid) if bid is not None else None


def displayed_size(side: str, bbo: Mapping[str, Any]) -> Optional[D]:
    return _d(bbo.get("ask_shares") if side == "UP" else bbo.get("bid_shares"))


class Leg:
    """One acquired leg, with how it was acquired and what it cost."""

    def __init__(self, i, side, price, qty, how, fee=D(0), rebate=D(0)):
        self.i, self.side, self.price, self.qty = i, side, price, qty
        self.how, self.fee, self.rebate = how, fee, rebate

    @property
    def cost(self) -> D:
        return self.price * self.qty


def _venue_class(row: Mapping[str, Any]) -> str:
    return (row.get("copyability") or {}).get("klass") or "UNKNOWN"


def run_track(rows: Sequence[Mapping[str, Any]],
              authenticated: Mapping[str, Any],
              *, proportional: bool, allow_cross: bool,
              lam: D) -> Dict[str, Any]:
    """One translation over one window's actions, in chronological order."""
    acts = sorted(rows, key=lambda r: r["l5_event_utc"])
    legs: List[Leg] = []
    blocked = collections.Counter()
    up = D(0)
    dn = D(0)
    imbalance: List[float] = []
    pair_costs: List[float] = []
    matched_path: List[float] = []
    residual_path: List[float] = []
    sides_native: List[str] = []
    sides_track: List[str] = []

    for i, r in enumerate(acts):
        l5 = r.get("l5") or {}
        side = l5.get("pmus_side")
        if side is None:
            continue
        sides_native.append(side)

        want = (_d(l5.get("l5_size")) or D(0)) * lam if proportional else D(1)
        if want <= 0:
            blocked["ZERO_SIZE"] += 1
            continue

        klass = _venue_class(r)
        bbo = ((r.get("pmus") or {}).get("bbo")) or {}
        leg: Optional[Leg] = None

        if klass == "NO_BOOK":
            blocked["NO_BOOK"] += 1
        elif klass == "MAKER_COPYABLE":
            got = authenticated.get(r.get("action_key"))
            kl = (got or {}).get("klass") if isinstance(got, Mapping) else got
            if kl in STRICT_CLASSES:
                px = _d((r.get("copyability") or {}).get("l5_price"))
                if px is None:
                    blocked["OTHER"] += 1
                else:
                    leg = Leg(i, side, px, want, "MAKER",
                              rebate=fees.maker_rebate(px, want))
            elif kl is None:
                blocked["STRICT_NOT_EVALUABLE"] += 1
            else:
                blocked["NO_SUBSEQUENT_INTERACTION"
                        if kl == "NO_INTERACTION" else "QUEUE_AMBIGUOUS"] += 1
        elif klass == "REQUIRES_CROSSING":
            if not allow_cross:
                blocked["REQUIRES_CROSSING"] += 1
            else:
                px = cross_price(side, bbo)
                avail = displayed_size(side, bbo)
                if px is None:
                    blocked["NO_BOOK"] += 1
                elif avail is None or avail <= 0:
                    blocked["NO_DISPLAYED_SIZE"] += 1
                else:
                    qty = min(want, avail)
                    leg = Leg(i, side, px, qty, "CROSS",
                              fee=fees.taker_fee(px, qty))
        else:
            blocked["OTHER"] += 1

        if leg is None:
            continue
        legs.append(leg)
        sides_track.append(side)
        if side == "UP":
            up += leg.qty
        else:
            dn += leg.qty
        imbalance.append(float(up - dn))
        m = min(up, dn)
        matched_path.append(float(m))
        residual_path.append(float(up - dn))
        if m > 0:
            cu = sum((l.cost for l in legs if l.side == "UP"), D(0))
            cd = sum((l.cost for l in legs if l.side == "DOWN"), D(0))
            qu = sum((l.qty for l in legs if l.side == "UP"), D(0))
            qd = sum((l.qty for l in legs if l.side == "DOWN"), D(0))
            if qu > 0 and qd > 0:
                pair_costs.append(float(cu / qu + cd / qd))

    return {
        "legs": legs, "blocked": dict(blocked),
        "up": float(up), "dn": float(dn),
        "imbalance_path": imbalance,
        "matched_path": matched_path,
        "residual_path": residual_path,
        "pair_cost_path": pair_costs,
        "sides_native": sides_native, "sides_track": sides_track,
        "l5_actions": len(sides_native),
        "reproduced": len(legs),
    }


def _crossed_zero(path: Sequence[float]) -> bool:
    nz = [1 if x > 0 else (-1 if x < 0 else 0) for x in path]
    nz = [s for s in nz if s != 0]
    return any(a != b for a, b in zip(nz, nz[1:]))


def _overshoot(path: Sequence[float]) -> float:
    nz = [(x, 1 if x > 0 else (-1 if x < 0 else 0)) for x in path]
    signs = [s for _, s in nz if s != 0]
    if not any(a != b for a, b in zip(signs, signs[1:])):
        return 0.0
    first = signs[0]
    return max((abs(x) for x, s in nz if s != 0 and s != first), default=0.0)


def _longest_run(sides: Sequence[str]) -> int:
    best = run = 0
    last = None
    for s in sides:
        run = run + 1 if s == last else 1
        last = s
        best = max(best, run)
    return best


def summarize(track: Mapping[str, Any], yes_value: Optional[D]) -> Dict[str, Any]:
    """Fidelity and complete-sequence economics for one track/window."""
    legs: List[Leg] = list(track["legs"])
    up, dn = D(str(track["up"])), D(str(track["dn"]))
    matched = min(up, dn)
    residual = up - dn

    cu = sum((l.cost for l in legs if l.side == "UP"), D(0))
    cd = sum((l.cost for l in legs if l.side == "DOWN"), D(0))
    qu = sum((l.qty for l in legs if l.side == "UP"), D(0))
    qd = sum((l.qty for l in legs if l.side == "DOWN"), D(0))
    au = (cu / qu) if qu > 0 else None
    ad = (cd / qd) if qd > 0 else None
    pair = (au + ad) if (au is not None and ad is not None) else None

    fee = sum((l.fee for l in legs), D(0))
    reb = sum((l.rebate for l in legs), D(0))
    matched_pnl = ((D(1) - pair) * matched) if pair is not None else D(0)

    resid_pnl = D(0)
    resid_basis = D(0)
    if residual != 0 and yes_value is not None:
        if residual > 0:
            resid_basis = (au or D(0)) * residual
            resid_pnl = yes_value * residual - resid_basis
        else:
            q = -residual
            resid_basis = (ad or D(0)) * q
            resid_pnl = (D(1) - yes_value) * q - resid_basis

    sides_n, sides_t = track["sides_native"], track["sides_track"]
    return {
        "l5_actions": track["l5_actions"],
        "reproduced": track["reproduced"],
        "reproduction_rate_pct": (round(100.0 * track["reproduced"]
                                        / track["l5_actions"], 1)
                                  if track["l5_actions"] else None),
        "by_how": dict(collections.Counter(l.how for l in legs)),
        "blocked": track["blocked"],
        "habitat_failures": {k: v for k, v in track["blocked"].items()
                             if k in HABITAT_FAILURES},
        "side_sequence": {
            "native_first_side": sides_n[0] if sides_n else None,
            "track_first_side": sides_t[0] if sides_t else None,
            "native_longest_run": _longest_run(sides_n),
            "track_longest_run": _longest_run(sides_t),
            "native_up": sides_n.count("UP"), "native_dn": sides_n.count("DOWN"),
            "track_up": sides_t.count("UP"), "track_dn": sides_t.count("DOWN"),
        },
        "opposite_acquired": len(set(sides_t)) > 1,
        "inventory_crossed_zero": _crossed_zero(track["imbalance_path"]),
        "overshoot_past_zero": round(_overshoot(track["imbalance_path"]), 4),
        "max_abs_imbalance": round(max((abs(x) for x in track["imbalance_path"]),
                                       default=0.0), 4),
        "matched_final": float(matched),
        "residual_final": float(residual),
        "residual_direction": ("UP" if residual > 0 else
                               ("DOWN" if residual < 0 else "FLAT")),
        "pair_cost_max": (round(max(track["pair_cost_path"]), 4)
                          if track["pair_cost_path"] else None),
        "pair_cost_min": (round(min(track["pair_cost_path"]), 4)
                          if track["pair_cost_path"] else None),
        "pair_cost_final": (round(track["pair_cost_path"][-1], 4)
                            if track["pair_cost_path"] else None),
        "matched_pair_pnl_usd": round(float(matched_pnl), 4),
        "residual_cost_basis_usd": round(float(resid_basis), 4),
        "residual_pnl_usd": round(float(resid_pnl), 4),
        "maker_rebate_accrual_usd": round(float(reb), 4),
        "taker_fees_usd": round(float(fee), 4),
        "capital_committed_usd": round(float(cu + cd), 4),
        "total_settlement_implied_shadow_usd":
            round(float(matched_pnl + resid_pnl + reb - fee), 4),
        "labels": ("SHADOW COUNTERFACTUAL. No order exists. Nothing here is "
                   "submitted, filled, settled or realized."),
    }
