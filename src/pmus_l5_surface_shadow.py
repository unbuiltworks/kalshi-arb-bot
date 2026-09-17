"""The PMUS sports passive surface, scored per side against the REAL trade tape.

WHY THIS EXISTS. The PMUS lane could reach BOOK_REACHED and no further: the
public gateway serves no print tape (five paths verified 404 on 2026-09-17) and
`sharesTraded` is a CUMULATIVE PER-MARKET counter, which can say a trade
happened somewhere in the market and never that one happened at OUR price. That
is exactly what VOLUME_AHEAD_CLEARED needs. The authenticated WS does carry a
real tape and src.pmus_ws_evidence_recorder already records it read-only; this
is the surface that consumes it.

NO CREDENTIAL LIVES HERE. It takes the recorder's per-market state as an
argument -- {bid, ask, bid_qty, ask_qty, trades[]} -- so it runs either inside
that service or over its raw tape, and holds nothing itself. A test asserts it
opens no socket and reads no environment.

THE TWO-SIDED MAPPING, WHICH IS WHY THE ENGINE NEEDS NO CHANGE. PMUS quotes one
slug, YES. Buying NO at p IS selling YES at 1-p, so a two-sided passive surface
on a PMUS slug is exactly the two-outcome surface the engine already emits:

    YES  bid = yes_bid          ask = yes_ask
    NO   bid = 1 - yes_ask      ask = 1 - yes_bid

Both sides are then BUY rungs, which is what `both_sides_policy` already means,
and L5_ADAPTED_PMUS_SPORTS drives it unmodified.

The engine is addressed in ITS names, not this venue's. The frozen spec's
`both_sides_policy` scope is literally ["Up", "Down"] -- a venue-neutral
observation about L5 -- so the surface is built under Up/Down and translated to
YES/NO at the venue boundary. Handing the engine YES/NO keys instead produced
`emitted: True` with zero rungs, because the scope matched nothing: a surface
that silently quotes nothing, which is the failure this module reports as
`no_rungs_in_scope` rather than as an empty ladder.

    Up  == YES        Down == NO

QUEUE-AHEAD IS CAPTURED AT PLACEMENT AND NEVER RE-READ. A quote that rests
while the level grows is not behind the new volume; it is behind what was there
when it joined. Re-reading depth later would silently re-rank us.

ENTRY IS NOT GATED ON PAIR COST. Per owner 2026-09-17: a <=0.96 pair filter is
not an entry requirement for this measurement. Pair economics are RECORDED per
observation so they can be analysed, and they admit nothing and refuse nothing.

NOTHING HERE IS A FILL UNTIL VOLUME_AHEAD_CLEARED, and that is a model of an
unpublished queue: it assumes we joined behind everything already resting.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.l5_surface import engine as EN
from src.l5_surface import lanes as LN
from src.l5_surface import params as PR
from src.l5_surface import pmus_prints as PP

LABEL = "PMUS_L5_SURFACE_SHADOW_V1"
STRICT = "VOLUME_AHEAD_CLEARED"
TICK = D("0.01")
RECYCLE_MARKS_S = (5, 15, 30, 60, 120, 300)

#: the per-side ladder, in order. PLACED is first because a side that was
#: never placed and a side that was placed and never filled are opposite
#: findings, and only counting from RESTABLE hides which one happened.
STAGES = ("PLACED", "RESTABLE", "BOOK_REACHED", "TRADE_FLOW",
          "EVIDENCED_FILL", "OPPOSITE_RECYCLING")


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except (TypeError, ValueError, ArithmeticError):
        return None


def outcomes_from(market: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Recorder state -> the engine's two-outcome view, or None.

    None when the book is not two-sided: a one-sided book cannot support a
    two-sided surface, and inventing the missing side is how a surface ends up
    quoting against nothing.
    """
    bid, ask = _d(market.get("bid")), _d(market.get("ask"))
    if bid is None or ask is None or not (D("0") < bid < ask < D("1")):
        return None
    return {
        # the ENGINE's names; VENUE_OUTCOME translates at the boundary
        "Up": {"bid": str(bid), "ask": str(ask), "tick": str(TICK)},
        "Down": {"bid": str(D("1") - ask), "ask": str(D("1") - bid),
                 "tick": str(TICK)},
    }


#: engine name -> venue name. Up is the YES side of the slug.
VENUE_OUTCOME = {"Up": "YES", "Down": "NO"}


def queue_ahead_at(market: Mapping[str, Any], outcome: str, px: D
                   ) -> Optional[D]:
    """Volume resting at the level we would join, at the moment we join it.

    A YES rung joins the BID side; a NO rung at p is a YES offer at 1-p and
    joins the ASK side. Returns None when the venue did not serve that side's
    depth -- unknown, which is not zero. Treating an unserved depth as zero
    would put us at the front of a queue we cannot see.
    """
    if outcome == "Up":
        top, qty = _d(market.get("bid")), _d(market.get("bid_qty"))
    else:
        top, qty = _d(market.get("ask")), _d(market.get("ask_qty"))
        px = D("1") - px                       # back to YES terms
    if top is None or qty is None:
        return None
    if px == top:
        return qty
    # joining behind the touch: the venue's top-level qty does not describe a
    # deeper level, so the depth there is UNKNOWN rather than zero
    return None


class Quote:
    __slots__ = ("outcome", "px", "placed_t", "queue_ahead", "cleared",
                 "restable", "book_reached", "trade_flow", "filled",
                 "counted")

    def __init__(self, outcome: str, px: D, t: float,
                 queue_ahead: Optional[D]):
        self.outcome, self.px, self.placed_t = outcome, px, t
        self.queue_ahead = queue_ahead
        self.cleared = D("0")
        self.restable = True
        self.book_reached = self.trade_flow = self.filled = False
        # stages already counted for THIS quote. Every ladder number is
        # "distinct quotes that reached this stage", so a quote surviving
        # many observations counts once -- mixing per-order PLACED with
        # per-observation stages makes the funnel unreadable and can print
        # RESTABLE above PLACED.
        self.counted: set = set()

    @property
    def side(self) -> str:
        """BUY YES / BUY NO are the two sides of the passive surface."""
        return "BUY_YES" if self.outcome == "Up" else "BUY_NO"

    def key(self) -> Tuple[str, str]:
        return (self.outcome, str(self.px))


class MarketSurface:
    """One slug's persistent surface and its per-side ladder counters."""

    def __init__(self, slug: str, spec: PR.SurfaceParams):
        self.slug, self.spec = slug, spec
        self.quotes: Dict[Tuple[str, str], Quote] = {}
        self.ladder = collections.Counter()      # "SIDE|STAGE"
        self.fills: List[dict] = []
        self.qty: Dict[str, D] = {"Up": D("0"), "Down": D("0")}
        self.observations = 0
        self.empty_reason = collections.Counter()

    @property
    def residual(self) -> D:
        return self.qty["Up"] - self.qty["Down"]

    @property
    def matched(self) -> D:
        return min(self.qty["Up"], self.qty["Down"])

    def queue_report(self) -> Dict[str, Any]:
        """Live queue depth per side, and how much of it has cleared."""
        out: Dict[str, Any] = {}
        for side, outcome in (("BUY_YES", "Up"), ("BUY_NO", "Down")):
            qs = [q for q in self.quotes.values() if q.outcome == outcome]
            known = sorted(q.queue_ahead for q in qs
                           if q.queue_ahead is not None)
            out[side] = {
                "live_quotes": len(qs),
                "queue_unknown": sum(1 for q in qs if q.queue_ahead is None),
                "median_queue_ahead_at_placement": (
                    str(known[len(known) // 2]) if known else None),
                "median_cleared_since": (
                    str(sorted(q.cleared for q in qs)[len(qs) // 2])
                    if qs else None),
                "n_past_their_queue": sum(
                    1 for q in qs if q.queue_ahead is not None
                    and q.cleared > q.queue_ahead),
            }
        out["labels"] = ("depth FROZEN at placement: a quote that rests while "
                         "the level grows is behind what was there when it "
                         "joined, not behind the new volume. Unknown depth is "
                         "counted, never treated as zero.")
        return out


def step(surf: MarketSurface, market: Mapping[str, Any], now: float,
         *, tte_s: Optional[float] = None) -> List[dict]:
    """One observation for one slug. Returns evidence rows.

    The whole per-side ladder advances here, and only VOLUME_AHEAD_CLEARED
    moves inventory.
    """
    rows: List[dict] = []
    surf.observations += 1

    outs = outcomes_from(market)
    if outs is None:
        surf.empty_reason["book_not_two_sided"] += 1
        return rows

    res = surf.residual
    state = {
        "cohort": "PMUS_SPORTS", "market_key": surf.slug,
        "state_id": "%s|%d" % (surf.slug, surf.observations),
        "t_utc": now, "tte_s": (tte_s if tte_s is not None else 0.0),
        "outcomes": outs,
        "inventory": {"Up": str(surf.qty["Up"]),
                      "Down": str(surf.qty["Down"])},
    }
    want = EN.desired_surface(state, surf.spec)
    if not want.get("emitted"):
        surf.empty_reason[str(want.get("reason") or "withheld")[:60]] += 1
        return rows

    wanted: Dict[Tuple[str, str], D] = {}
    for r in want["surface"].live_rungs():
        if r.outcome not in VENUE_OUTCOME:
            continue
        wanted[(r.outcome, r.max_acceptable_price)] = D(r.max_acceptable_price)
    if not wanted:
        # emitted, but every rung fell outside the spec's outcome scope. A
        # surface that quotes nothing must SAY so rather than look quiet.
        surf.empty_reason["no_rungs_in_scope"] += 1
        return rows

    # retire what the surface no longer wants
    for k in list(surf.quotes):
        if k not in wanted:
            surf.quotes.pop(k, None)

    # place what is missing, capturing queue depth AT PLACEMENT
    for (oc, pxs), px in wanted.items():
        if (oc, pxs) in surf.quotes:
            continue
        q = Quote(oc, px, now, queue_ahead_at(market, oc, px))
        surf.quotes[(oc, pxs)] = q
        surf.ladder["%s|PLACED" % q.side] += 1

    prints = PP.to_prints(market.get("trades"))

    for k, q in list(surf.quotes.items()):
        # RESTABLE: a passive order must not cross. In YES terms a NO rung at
        # p is an offer at 1-p, so it must sit ABOVE the YES bid.
        y_bid, y_ask = _d(market.get("bid")), _d(market.get("ask"))
        if q.outcome == "Up":
            q.restable = bool(y_ask is not None and q.px < y_ask)
            q.book_reached = bool(y_bid is not None and y_bid <= q.px)
        else:
            y_px = D("1") - q.px
            q.restable = bool(y_bid is not None and y_px > y_bid)
            q.book_reached = bool(y_ask is not None and y_ask >= y_px)

        if prints is not None:
            side = "BUY" if q.outcome == "Up" else "SELL"
            y_px = q.px if q.outcome == "Up" else (D("1") - q.px)
            f = PP.trade_flow_for(y_px, market.get("trades"), side=side,
                                  placed_at=q.placed_t)
            if f.get("readable") and f.get("prints_through"):
                q.trade_flow = True
                q.cleared = D(str(f["traded_through"]))

        # Count the stages this quote reached BEFORE resolving a fill. A
        # filled quote is retired, and counting after the fill would drop its
        # TRADE_FLOW -- leaving a ladder that shows a fill with no flow behind
        # it, which is incoherent because flow is a prerequisite for the fill.
        for stage, hit in (("RESTABLE", q.restable),
                           ("BOOK_REACHED", q.book_reached),
                           ("TRADE_FLOW", q.trade_flow)):
            if hit and stage not in q.counted:
                q.counted.add(stage)
                surf.ladder["%s|%s" % (q.side, stage)] += 1

        # EVIDENCED_FILL: the queue ahead of us actually cleared. An UNKNOWN
        # queue can never clear -- we do not know what we were behind, and
        # assuming zero would manufacture a fill.
        if (not q.filled and q.trade_flow and q.queue_ahead is not None
                and q.cleared > q.queue_ahead):
            q.filled = True
            want_qty = D("1")
            got = min(want_qty, q.cleared - q.queue_ahead)
            surf.qty[q.outcome] += got
            surf.fills.append({"side": q.side, "outcome": q.outcome,
                               "px": str(q.px), "qty": str(got), "t": now})
            surf.ladder["%s|EVIDENCED_FILL" % q.side] += 1
            rows.append({"kind": "evidenced_fill", "label": LABEL,
                         "slug": surf.slug, "side": q.side,
                         "outcome": q.outcome,
                         "venue_outcome": VENUE_OUTCOME[q.outcome],
                         "px": str(q.px),
                         "qty": str(got), "t": now, "evidence": STRICT,
                         "queue_ahead_at_placement": str(q.queue_ahead),
                         "volume_cleared": str(q.cleared),
                         "labels": ("EVIDENCED, not venue-confirmed: queue "
                                    "position is unpublished and this assumes "
                                    "we joined behind everything resting.")})
            surf.quotes.pop(k, None)

    return rows


def recycling(fills: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """An opposite-OUTCOME evidenced fill within each mark of a fill.

    Both legs evidenced. A resting opposite quote is not an interaction, and
    neither is a print we did not clear the queue for."""
    fs = sorted(fills, key=lambda f: float(f.get("t") or 0))
    out: Dict[str, Any] = {"n_fills": len(fs)}
    for m in RECYCLE_MARKS_S:
        hit = 0
        for i, f in enumerate(fs):
            t0, oc = float(f.get("t") or 0), f.get("outcome")
            if any(g.get("outcome") != oc
                   and 0 < float(g.get("t") or 0) - t0 <= m
                   for g in fs[i + 1:]):
                hit += 1
        out["n_opposite_within_%ds" % m] = hit
        out["p_opposite_within_%ds" % m] = (
            None if not fs else round(hit / len(fs), 4))
    out["labels"] = ("both legs EVIDENCED. A resting opposite quote is not "
                     "recycling.")
    return out


def report(surfaces: Mapping[str, MarketSurface]) -> Dict[str, Any]:
    """The per-side ladder, across every slug, with the queue picture."""
    tot = collections.Counter()
    per: Dict[str, Any] = {}
    all_fills: List[dict] = []
    for slug, s in surfaces.items():
        per[slug] = {
            "observations": s.observations,
            "ladder": {k: v for k, v in sorted(s.ladder.items())},
            "queue": s.queue_report(),
            "residual": str(s.residual), "matched": str(s.matched),
            "n_fills": len(s.fills),
            "not_emitted": dict(s.empty_reason),
        }
        tot.update(s.ladder)
        all_fills.extend(s.fills)

    def side(sd: str) -> Dict[str, int]:
        return {st: int(tot.get("%s|%s" % (sd, st), 0)) for st in STAGES}

    yes, no = side("BUY_YES"), side("BUY_NO")
    rec = recycling(all_fills)
    yes["OPPOSITE_RECYCLING"] = no["OPPOSITE_RECYCLING"] = int(
        rec.get("n_opposite_within_300s", 0))
    return {
        "label": LABEL, "markets": len(surfaces),
        "per_side": {"BUY_YES": yes, "BUY_NO": no},
        "recycling": rec,
        "per_market": per,
        "verdict": _verdict(yes, no),
        "labels": [
            "every number is DISTINCT QUOTES that reached that stage, counted "
            "once each, so the funnel is monotone and PLACED is comparable "
            "to the rest.",
            "PLACED is counted because a side never placed and a side placed "
            "and never filled are opposite findings.",
            "queue-ahead is frozen at placement and an UNKNOWN queue can "
            "never clear -- assuming zero would manufacture a fill.",
            "pair cost is not an entry gate here; nothing is admitted or "
            "refused on it.",
            "VOLUME_AHEAD_CLEARED is a model of an unpublished queue, never "
            "venue confirmation.",
        ],
    }


def _verdict(yes: Mapping[str, int], no: Mapping[str, int]) -> str:
    fy, fn = yes["EVIDENCED_FILL"], no["EVIDENCED_FILL"]
    if fy and fn:
        return "TWO_SIDED_EVIDENCED_FILLS"
    placed_both = yes["PLACED"] and no["PLACED"]
    flow_both = yes["TRADE_FLOW"] and no["TRADE_FLOW"]
    if fy or fn:
        got = "BUY_YES" if fy else "BUY_NO"
        other = "BUY_NO" if fy else "BUY_YES"
        if not (no if fy else yes)["PLACED"]:
            return "%s_ONLY_%s_NEVER_PLACED" % (got, other)
        return "%s_ONLY_%s_RESTED_UNFILLED" % (got, other)
    if not placed_both:
        return "SURFACE_NEVER_PLACED_BOTH_SIDES"
    if flow_both:
        return "NO_FILLS_BOTH_SIDES_QUEUE_BOUND"
    if yes["TRADE_FLOW"] or no["TRADE_FLOW"]:
        return "NO_FILLS_PARTIAL_FLOW"
    return "NO_FILLS_NO_FLOW"


def spec() -> PR.SurfaceParams:
    return PR.load_frozen()[LN.PMUS_SPORTS]
