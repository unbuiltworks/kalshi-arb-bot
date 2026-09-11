"""Arm B, followed forward: WOULD_POST -> WOULD_FILL -> VIRTUAL_MATCHED.

OWNER: "turn Arm B from 'would post' into an execution-quality shadow... Do
not count `would_post` as a completion. Keep statuses explicit... Use the same
conservative fill evidence/calibration already validated against PMUS live
fills. Never assume a fill merely because the order was theoretically
attractive."

WHAT ARM B PROVED, AND WHAT IT DID NOT. `completion_would_post: true` says the
frozen policy's COMPLETE branch would price and post an order here. It says
NOTHING about whether that order ever trades. An attractive quote that nobody
lifts is not a completion, and counting it as one would reproduce -- with more
machinery -- exactly the "theoretical fill" error the accounting rules forbid.

So a proposal is FOLLOWED FORWARD through the tape it was posted into:

  WOULD_POST       the policy priced and posted it. A proposal, not a fill.
  WOULD_FILL       a later trade came THROUGH the resting price in the
                   hitting direction, inside the follow window.
  VIRTUAL_MATCHED  that fill would have paired inventory: pair cost, matched
                   quantity and remaining residual all computed.
  NO_FILL          followed to the end of the window and the market never
                   came. This is a RESULT, not a missing measurement.
  PENDING          still inside the window; no verdict yet.

PENDING AND NO_FILL ARE DIFFERENT AND ARE NEVER COLLAPSED. "We have not
finished watching" and "the market never came" produce the same empty
completion column, and treating them alike is how a rate gets computed on a
denominator that has not happened yet.

THE FILL RULE IS THE ONE ALREADY VALIDATED, NOT A NEW ONE.
pmus_sports_l5_decision._fill_evidence: the first LATER inferred trade at or
through the resting maker price in the hitting direction, strictly causal, one
trade per quote. That rule was measured against the live game tape on
2026-09-10 and classed 3 of 3 real quotes filled, with trade direction
resolving 8 of 8. It is an UPPER BOUND -- queue position is unproven -- and
every label here says so. Nothing in this module loosens it, and a test
asserts the module does not define a fill rule of its own.

QUANTITY IS BOUNDED BY WHAT ACTUALLY TRADED THROUGH THE PRICE, not by what
was resting. We cannot be ahead of a queue we cannot see, so the volume that
printed through the quote is the ceiling on what could have filled.

STATE IS CAPTURED AT PROPOSAL TIME, BEFORE THE OUTCOME EXISTS. The predictor
question ("which observable variables predict completion") is only answerable
prospectively; recording flow, spread, movement, band position, market type
and residual geometry AFTER seeing whether the thing filled would fit the
predictors to the answer. A test asserts the state snapshot is written on the
WOULD_POST transition and never rewritten.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Mapping, Optional, Sequence

from src import pmus_sports_l5_decision as DEC
from src import pmus_sports_opportunity_rank as rank_mod
from src import pmus_status_report as ST

WOULD_POST = "WOULD_POST"
WOULD_FILL = "WOULD_FILL"
VIRTUAL_MATCHED = "VIRTUAL_MATCHED"
NO_FILL = "NO_FILL"
PENDING = "PENDING"
# A re-quote at a NEW price replaces the resting order rather than adding a
# second one: the live engine holds ONE outstanding order per market. The
# replaced proposal never got its full follow window, so it is terminal but
# EXCLUDED from the completion rate -- it neither filled nor failed to fill,
# and counting it either way would be a verdict the evidence does not support.
SUPERSEDED = "SUPERSEDED_BY_REQUOTE"

TERMINAL = (VIRTUAL_MATCHED, NO_FILL, SUPERSEDED)
# What a completion RATE may be computed over: outcomes that actually
# resolved for or against a fill.
RESOLVED = (VIRTUAL_MATCHED, NO_FILL)

# How long a posted complement is followed before the market is judged to have
# never come. The live engine rests a complement and re-quotes on touch moves;
# 300s matches SLOT_S, the window the validated fill rule already uses.
FOLLOW_S = float(os.getenv("PMUS_EXEC_FOLLOW_S", str(ST.SLOT_S)))

PAIR_CEILING = D("1.00")

STATE_PATH = os.getenv("PMUS_EXEC_STATE",
                       "/data/pmus_shadow/execution_shadow.json")

# The venue's price increment. Distances are reported in BOTH cents and ticks
# because "6 cents away" and "12 ticks away" answer different questions.
TICK = D(os.getenv("PMUS_EXEC_TICK", "0.005"))

# RESEARCH ONLY, and deliberately separate. OWNER: "Do not assume 300 seconds
# is the correct horizon... Keep the frozen R5F 300s result unchanged. We need
# to know whether they are unreachable or merely slow." The 300s verdict is
# written once and never revised; everything learned afterwards lands in a
# `research` sub-object that no rate, cohort or metric reads.
EXTENDED_FOLLOW_S = float(os.getenv("PMUS_EXEC_EXTENDED_S", "1800"))


def gap_to_fill(o: Mapping[str, Any], side_yes: bool, px: D) -> Optional[D]:
    """How far the OPPOSING touch still has to travel for a fill, in dollars.

        > 0   the market has not reached our price -- UNREACHABLE so far
        <= 0  the opposing touch is at or through it -- reachable now

    This is the measurement the owner's example turns on: a complement at 59c
    against an opposing side sitting at 65c is not "an order nobody will
    fill", it is an order 6c away from being executable at all. The two have
    completely different remedies, and a completion percentage cannot tell
    them apart.

    Frames follow the fill rule exactly. A YES buy fills on a SELL at or
    below px, so the ASK must come down to it. A NO buy fills on a BUY at or
    above ONE - px, so the BID must come up to it.
    """
    thresh = fill_threshold(side_yes, px)
    q = o.get("ask") if side_yes else o.get("bid")
    if q is None:
        return None
    return (D(str(q)) - thresh) if side_yes else (thresh - D(str(q)))


def reachability(prop: Mapping[str, Any], obs: Sequence[Mapping[str, Any]],
                 now: float, horizon_s: Optional[float] = None) -> dict:
    """Why a complement completed or did not -- not merely whether it did."""
    horizon_s = FOLLOW_S if horizon_s is None else horizon_s
    try:
        t_post = float(prop["t_post"])
        px = D(str(prop["px"]))
        side_yes = bool(prop["side_yes"])
    except Exception:                                       # noqa: BLE001
        return {}
    until = t_post + horizon_s
    series = []
    for o in obs or []:
        t = ST._ts(o)
        if t is None or t <= t_post or t > until:
            continue
        g = gap_to_fill(o, side_yes, px)
        if g is not None:
            series.append((t, g))
    if not series:
        return {"observations": 0, "note": "no two-sided observation after post"}

    initial = series[0][1]
    min_gap = min(g for _, g in series)
    touch_t = next((t for t, g in series if g <= 0), None)
    return {
        "observations": len(series),
        "horizon_s": horizon_s,
        "initial_gap_cents": float(initial * 100),
        "initial_gap_ticks": float(initial / TICK),
        "min_gap_cents": float(min_gap * 100),
        "min_gap_ticks": float(min_gap / TICK),
        # how far the opposing market travelled TOWARD us from where it
        # started: the excursion that was favourable to completing.
        "max_favorable_excursion_cents": float((initial - min_gap) * 100),
        "reached": bool(min_gap <= 0),
        "time_to_touch_s": None if touch_t is None else round(touch_t - t_post, 1),
        "final_gap_cents": float(series[-1][1] * 100),
        "labels": ("gap > 0 means the market never came to our price -- the "
                   "complement was UNREACHABLE, not merely unfilled"),
    }


def proposal_id(slug: str, t_post: float, px) -> str:
    return f"{slug}|{round(float(t_post), 1)}|{px}"


def state_at_post(row: Optional[Mapping[str, Any]],
                  inv_residual, surplus_avg, complement_px) -> dict:
    """Everything observable ABOUT THE MARKET at the moment of posting.

    Written once, on the WOULD_POST transition, so the predictor analysis is
    prospective. Anything unavailable is None -- an absent predictor and a
    zero one are different facts.
    """
    row = row or {}
    bid, ask = row.get("bid"), row.get("ask")
    mid = None if bid is None or ask is None else (float(bid) + float(ask)) / 2
    return {
        "flow_usd_per_min": row.get("flow_usd_per_min"),
        "spread": row.get("spread"),
        "median_spread": row.get("median_spread"),
        "mid_stdev": row.get("mid_stdev"),
        "mid_range": row.get("mid_range"),
        # DEPTH at the touch. Added 2026-09-11: the band x spread grid found
        # depth collapsing 82x as the spread widens, and the per-proposal row
        # cannot test whether thin books strand without carrying it.
        "bid_touch_qty": row.get("bid_touch_qty"),
        "ask_touch_qty": row.get("ask_touch_qty"),
        # THE FULL LADDER when the pipeline retains it. The venue's book
        # endpoint serves `bids`/`offers` arrays; the collector's normalizer
        # currently keeps only level 0. Carried here so queue-ahead populates
        # the moment that changes, with no further code edit.
        "book_levels": row.get("book_levels"),
        "shape_span_s": row.get("shape_span_s"),
        "mid": mid,
        "in_band": None if mid is None else (rank_mod.BAND_LO <= mid
                                             <= rank_mod.BAND_HI),
        "band_position": None if mid is None else round(
            (mid - rank_mod.BAND_LO) / (rank_mod.BAND_HI - rank_mod.BAND_LO), 4),
        "market_type": market_type(row.get("slug")),
        "sport": sport_of(row.get("slug")),
        "residual_before": inv_residual,
        "surplus_avg": None if surplus_avg is None else str(surplus_avg),
        "complement_px": None if complement_px is None else str(complement_px),
        "implied_pair_cost": (None if surplus_avg is None
                              or complement_px is None
                              else str(D(str(surplus_avg)) + D(str(complement_px)))),
        "distance_to_touch": (None if complement_px is None or bid is None
                              else round(float(complement_px) - float(bid), 4)),
    }


def sport_of(slug: Optional[str]) -> Optional[str]:
    parts = str(slug or "").split("-")
    return parts[1] if len(parts) > 1 else None


def market_type(slug: Optional[str]) -> Optional[str]:
    s = str(slug or "")
    if not s:
        return None
    head = s.split("-", 1)[0]
    return {"aec": "moneyline", "asc": "spread", "atc": "three_way",
            "tsc": "total"}.get(head, head or None)


def open_proposal(slug: str, plan: Mapping[str, Any], t_post: float,
                  ranked_row: Optional[Mapping[str, Any]],
                  residual_before, surplus_avg, tier=None,
                  first_leg: Optional[Mapping[str, Any]] = None) -> dict:
    """Record a posted complement. This is a PROPOSAL, not a completion."""
    px = plan.get("target_price")
    return {
        "schema": SCHEMA,
        "id": proposal_id(slug, t_post, px),
        "slug": slug, "tier": tier,
        "t_post": t_post,
        "side_yes": bool(plan.get("_side_yes", plan.get("side") == "YES")),
        "px": str(px),
        "q1_size": plan.get("q1_size", 1),
        "max_pair_cost": plan.get("max_pair_cost"),
        "status": WOULD_POST,
        "state_at_post": state_at_post({**(ranked_row or {}), "slug": slug},
                                       residual_before, surplus_avg, px),
        "surplus_avg": None if surplus_avg is None else str(surplus_avg),
        "residual_before": residual_before,
        # WHERE THE FIRST LEG CAME FROM. The complement price is derived from
        # it (max_pair - surplus_avg), so a complement that sits unreachable
        # may be a consequence of where the first leg filled rather than of
        # anything about the complement itself.
        "first_leg": dict(first_leg or {}),
        # filled in as the market is followed
        "t_fill": None, "latency_s": None, "fillable_qty": None,
        "touched": None, "traded_through": None,
        "pair_cost": None, "matched_after": None, "residual_after": None,
        "risk_returned": None, "risk_created": None,
        "labels": ("WOULD_POST is a posted price, never a fill. The fill rule "
                   "is pmus_sports_l5_decision._fill_evidence -- an UPPER "
                   "bound with queue position unproven."),
    }


def follow(prop: Mapping[str, Any], obs: Sequence[Mapping[str, Any]],
           now: float) -> dict:
    """Advance one proposal against the tape it was posted into.

    Never raises; an unfollowable proposal stays PENDING rather than being
    resolved by default in either direction.
    """
    p = dict(prop)
    if p.get("status") in TERMINAL:
        return p
    try:
        t_post = float(p["t_post"])
        px = D(str(p["px"]))
        side_yes = bool(p["side_yes"])
        until = min(t_post + FOLLOW_S, now)
        later = [o for o in obs or [] if (ST._ts(o) or 0) > t_post]
        trades = ST.infer_trades(list(obs or []))

        # did the market ever TOUCH the quote, whether or not it traded?
        touched_by_quote = _touched(later, side_yes, px)

        p["reachability"] = reachability(p, obs, now)

        ev = DEC._fill_evidence(trades, t_post, side_yes, px, until)
        # A FILL IMPLIES A TOUCH. Quotes are snapshots and trades happen
        # between them, so a print can come through the price with no
        # snapshot ever showing the opposite touch at it. Reading those two
        # sources independently would let a filled proposal report
        # touched=False, which is not a sampling nuance the summary should
        # inherit -- a trade through the price IS the market reaching it.
        p["touched"] = bool(touched_by_quote or ev is not None)
        if ev is None:
            # window still open -> PENDING; window closed -> a real NO_FILL
            p["status"] = PENDING if now < t_post + FOLLOW_S else NO_FILL
            if p["status"] == NO_FILL:
                p["latency_s"] = None
                p["traded_through"] = 0.0
            return p

        p["status"] = WOULD_FILL
        p["t_fill"] = ev["t"]
        p["latency_s"] = round(ev["t"] - t_post, 1)
        through = _volume_through(trades, t_post, ev["t"], side_yes, px)
        p["traded_through"] = through
        # We cannot be ahead of a queue we cannot see: what printed through
        # the price is the ceiling on what could have filled.
        p["fillable_qty"] = min(float(p.get("q1_size") or 1), through)
        return _match(p)
    except Exception as exc:                                    # noqa: BLE001
        p["status"] = PENDING
        p["follow_error"] = repr(exc)
        return p


def fill_threshold(side_yes: bool, px: D) -> D:
    """The price a trade must reach to fill this order, defined EXACTLY as
    pmus_sports_l5_decision._fill_evidence defines it:

        thresh = px          if side_yes   (a SELL at or below it fills us)
        thresh = ONE - px    otherwise     (a BUY at or above it fills us)

    Derived in one place because the first draft got it wrong in two: the
    touch check read the BID and required it to RISE to px, when a resting
    YES buy is reached by the ASK DESCENDING to px -- the opposite side and
    the opposite inequality. It returned True on 10 of 10 live proposals
    whose traded-through volume was 0.0, and "the market reached our price
    and declined to trade" is a completely different conclusion from "the
    market never came near it". Volume attribution had the matching error,
    using px instead of ONE - px on the NO side.
    """
    return px if side_yes else (DEC.ONE - px)


def _touched(later, side_yes: bool, px: D) -> bool:
    """Did the OPPOSITE touch reach the price a fill would have to come from?"""
    thresh = fill_threshold(side_yes, px)
    for o in later:
        # a YES buy is reached by the ask coming down; a NO buy (a YES sell
        # at ONE-px) is reached by the bid coming up.
        q = o.get("ask") if side_yes else o.get("bid")
        if q is None:
            continue
        if side_yes and D(str(q)) <= thresh:
            return True
        if not side_yes and D(str(q)) >= thresh:
            return True
    return False


def _volume_through(trades, t0: float, t1: float, side_yes: bool, px: D) -> float:
    """Volume that printed through the SAME threshold the fill rule uses."""
    thresh = fill_threshold(side_yes, px)
    total = 0.0
    for t in trades:
        tt = t.get("t")
        if tt is None or tt <= t0 or tt > t1:
            continue
        if side_yes and t["dir_inferred"] == "sell" and t["px"] <= thresh:
            total += float(t.get("vol") or 0)
        elif not side_yes and t["dir_inferred"] == "buy" and t["px"] >= thresh:
            total += float(t.get("vol") or 0)
    return round(total, 2)


def _match(p: dict) -> dict:
    """A WOULD_FILL that pairs inventory becomes VIRTUAL_MATCHED."""
    qty = float(p.get("fillable_qty") or 0)
    if qty <= 0:
        p["status"] = WOULD_FILL          # would trade, but nothing to pair
        return p
    surplus = p.get("surplus_avg")
    if surplus is not None:
        pair = D(str(surplus)) + D(str(p["px"]))
        p["pair_cost"] = str(pair)
        p["pair_at_or_below_ceiling"] = bool(pair <= PAIR_CEILING)
    before = p.get("residual_before")
    if isinstance(before, (int, float)):
        # the complement reduces |residual| by the matched quantity
        sign = 1 if before < 0 else -1
        after = before + sign * min(abs(before), qty)
        p["residual_after"] = after
        p["matched_after"] = min(abs(before), qty)
        p["risk_returned"] = round(abs(before) - abs(after), 4)
        p["risk_created"] = 0.0        # a completion creates none
    p["status"] = VIRTUAL_MATCHED
    return p


# ------------------------------------------------------- the durable tracker
class ExecutionShadow:
    """Follows every Arm-B proposal across cycles, and across redeploys.

    Proposals outlive the cycle that made them -- a complement posted at
    22:52 is judged by trades that arrive minutes later -- so the tracker
    MUST be durable. Keeping it in memory would resolve every open proposal
    to PENDING on each restart and silently reset the denominator.
    """

    def __init__(self, path: Optional[str] = None, now=time.time):
        self._path = path or STATE_PATH
        self._now = now
        self.proposals: dict[str, dict] = {}
        # Bounded: the counterfactual is a rate, not a tape.
        self.opportunities: list = []
        self.load()

    def load(self) -> None:
        try:
            with open(self._path) as fh:
                doc = json.load(fh)
            props = doc.get("proposals") or {}
            self.proposals = {k: dict(v) for k, v in props.items()}
            self.opportunities = list(doc.get("opportunities") or [])[-2000:]
        except Exception:                                       # noqa: BLE001
            return

    def save(self) -> bool:
        try:
            d = os.path.dirname(self._path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"proposals": self.proposals,
                           "opportunities": self.opportunities[-2000:]},
                          fh, default=str)
            os.replace(tmp, self._path)
            return True
        except Exception:                                       # noqa: BLE001
            return False

    def record(self, slug: str, plan: Mapping[str, Any], t_post: float,
               ranked_row=None, residual_before=None, surplus_avg=None,
               tier=None, first_leg: Optional[Mapping[str, Any]] = None
               ) -> Optional[dict]:
        """One resting complement per market, not one per replay.

        decide_market replays its whole window every cycle, so arm B
        re-proposes the SAME complement for as long as the residual stands.
        Measured live: asc-nfl-sf-lar-2026-09-10-pos-4pt5 was proposed three
        times at an identical 0.52, differing only in the replayed residual.
        Recording those as three proposals would treble the denominator of
        the completion rate with orders that never existed -- the same
        double-counting that once turned five markets into "29 first fills".

        The live engine holds ONE outstanding order per market, so:
          * same price while still open -> the SAME order; not recorded again
          * different price             -> a re-quote; the old one is
                                           SUPERSEDED and the new one opens
        """
        px = str(plan.get("target_price"))
        for pid, prev in self.proposals.items():
            if prev.get("slug") != slug or prev.get("status") in TERMINAL:
                continue
            if str(prev.get("px")) == px:
                return None                     # same resting order
            prev = dict(prev)
            prev["status"] = SUPERSEDED
            prev["superseded_by_px"] = px
            self.proposals[pid] = prev
            break
        p = open_proposal(slug, plan, t_post, ranked_row, residual_before,
                          surplus_avg, tier, first_leg)
        if p["id"] in self.proposals:
            return None
        self.proposals[p["id"]] = p
        return p

    def advance(self, obs_by_slug: Mapping[str, Sequence[Mapping[str, Any]]]
                ) -> dict:
        now = self._now()
        moved = researched = 0
        for pid, p in list(self.proposals.items()):
            obs = obs_by_slug.get(p["slug"])
            if not obs:
                continue
            if p.get("status") in TERMINAL:
                # The 300s verdict is FROZEN. Anything learned after it lands
                # in `research` and is read by no rate, cohort or metric --
                # that separation is what lets us ask whether a complement was
                # unreachable or merely slow without contaminating the result.
                after = extend_research(p, obs, now)
                if after is not p:
                    self.proposals[pid] = after
                    researched += 1
                continue
            after = follow(p, obs, now)
            if after.get("status") != p.get("status"):
                moved += 1
            self.proposals[pid] = after
        return {"advanced": moved, "research_updated": researched,
                "open": self.open_count(), "total": len(self.proposals)}

    def open_count(self) -> int:
        return sum(1 for p in self.proposals.values()
                   if p.get("status") not in TERMINAL)

    def by_status(self) -> dict:
        out: dict = {}
        for p in self.proposals.values():
            out[p.get("status")] = out.get(p.get("status"), 0) + 1
        return out

    def observe_selector(self, snapshot) -> Optional[dict]:
        """Record one counterfactual pair if the situation arose."""
        e = selector_opportunity(snapshot)
        if e is not None:
            self.opportunities.append(e)
            del self.opportunities[:-2000]
        return e

    def summary(self) -> dict:
        return summarize(list(self.proposals.values()))

    def predictors_report(self) -> dict:
        return predictors(list(self.proposals.values()))


# ------------------------------------------------- the metric and predictors
def summarize(props: Sequence[Mapping[str, Any]]) -> dict:
    """The owner's metric, computed only over RESOLVED proposals.

        P(virtual <=$1 completion | first fill) x risk returned
        --------------------------------------------------------
                 time capital remains exposed

    RESOLVED means terminal: VIRTUAL_MATCHED or NO_FILL. A proposal still
    inside its follow window has not failed to fill -- it has not finished
    being watched -- and including it in the denominator would compute a
    completion rate against events that have not happened yet. `pending` is
    reported separately so a thin sample is visible rather than flattering.
    """
    resolved = [p for p in props if p.get("status") in RESOLVED]
    pending = [p for p in props if p.get("status") not in TERMINAL]
    matched = [p for p in resolved if p.get("status") == VIRTUAL_MATCHED]
    at_or_below = [p for p in matched
                   if p.get("pair_at_or_below_ceiling") is True]

    n = len(resolved)
    p_completion = (len(at_or_below) / n) if n else None
    risk_returned = sum(float(p.get("risk_returned") or 0) for p in matched)
    exposure_s = sum(_exposure_s(p) for p in resolved)

    metric = None
    if p_completion is not None and exposure_s > 0:
        metric = round(p_completion * risk_returned / (exposure_s / 3600.0), 6)

    lat = sorted(float(p["latency_s"]) for p in matched
                 if p.get("latency_s") is not None)
    pairs = sorted(float(p["pair_cost"]) for p in matched
                   if p.get("pair_cost") is not None)
    return {
        "kind": "execution_shadow_summary",
        "counts": {"total": len(props), "resolved": n, "pending": len(pending),
                   **{s: sum(1 for p in props if p.get("status") == s)
                      for s in (WOULD_POST, PENDING, WOULD_FILL,
                                VIRTUAL_MATCHED, NO_FILL, SUPERSEDED)}},
        "p_completion_given_first_fill": p_completion,
        "risk_returned_units": round(risk_returned, 4),
        "risk_created_units": round(
            sum(float(p.get("risk_created") or 0) for p in matched), 4),
        "exposure_hours": round(exposure_s / 3600.0, 4) if exposure_s else 0.0,
        "metric_completion_x_risk_returned_per_exposure_hour": metric,
        "time_to_fill_s": {"median": _pct(lat, 0.5), "p90": _pct(lat, 0.9),
                           "n": len(lat)},
        "pair_cost_at_completion": {"median": _pct(pairs, 0.5),
                                    "min": pairs[0] if pairs else None,
                                    "max": pairs[-1] if pairs else None,
                                    "n": len(pairs)},
        "touched_but_never_filled": sum(
            1 for p in resolved if p.get("status") == NO_FILL
            and p.get("touched") is True),
        "labels": (
            "WOULD_POST is a posted price and is NEVER counted as a "
            "completion. Rates are computed over RESOLVED proposals only; "
            "PENDING is 'not finished watching', not 'did not fill'. Fills "
            "are the validated _fill_evidence upper bound with queue "
            "position unproven -- no fill here is a venue fill, and no "
            "figure here is realized P&L."),
        "realized_pnl": None,
    }


def _exposure_s(p: Mapping[str, Any]) -> float:
    """How long capital would have stayed exposed for this proposal."""
    try:
        if p.get("status") == VIRTUAL_MATCHED and p.get("latency_s") is not None:
            return float(p["latency_s"])
        return float(FOLLOW_S)            # NO_FILL held for the full window
    except Exception:                                           # noqa: BLE001
        return float(FOLLOW_S)


def _pct(xs: Sequence[float], q: float):
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[i], 4)


# EVIDENCE WINDOW. Stamped on every proposal at creation. A proposal recorded
# before the reachability measurement existed carries no gap series, and it
# must never be counted in the same population as one that does -- that
# mixture is what produced `reached_our_price: 0, filled: 1,
# reached_but_unfilled: -1` in the deployed rows at 00:45Z: a single report
# simultaneously implying a complement filled and that none was ever reached.
SCHEMA = "reachability-v1"

MEASURED = "measured"
UNMEASURED = "unmeasured_legacy"


def cohort_of(p: Mapping[str, Any]) -> str:
    """Which evidence population a proposal belongs to.

    Membership is decided by whether the proposal actually CARRIES the
    measurement, not by when we think the code changed: a proposal stamped
    with the current schema that never saw a two-sided book after posting is
    still unmeasured for reachability purposes.
    """
    r = p.get("reachability") or {}
    return MEASURED if r.get("observations") else UNMEASURED


# Neither outcome class may be thinner than this before a split is readable.
MIN_CLASS = int(os.getenv("PMUS_EXEC_MIN_CLASS", "10"))

NUMERIC_PREDICTORS = ("flow_usd_per_min", "spread", "median_spread",
                      "mid_stdev", "mid_range", "mid", "band_position",
                      "distance_to_touch", "residual_before")
CATEGORICAL_PREDICTORS = ("sport", "market_type", "in_band")


def predictors(props: Sequence[Mapping[str, Any]]) -> dict:
    """Which observable state at POSTING time separates matched from not.

    Descriptive only, and deliberately so: it reports the split of each
    variable between resolved outcomes and does NOT fit, rank, or select. The
    owner asked to determine what predicts the outcome BEFORE optimising the
    ranker, and a module that scored candidates here would be the ranker.
    """
    resolved = [p for p in props if p.get("status") in RESOLVED]
    won = [p for p in resolved if p.get("status") == VIRTUAL_MATCHED]
    lost = [p for p in resolved if p.get("status") == NO_FILL]

    num: dict = {}
    for k in NUMERIC_PREDICTORS:
        a = [float(p["state_at_post"][k]) for p in won
             if _has(p, k)]
        b = [float(p["state_at_post"][k]) for p in lost if _has(p, k)]
        num[k] = {"matched_median": _pct(sorted(a), 0.5), "matched_n": len(a),
                  "no_fill_median": _pct(sorted(b), 0.5), "no_fill_n": len(b),
                  "separation": (None if not a or not b else
                                 round(_pct(sorted(a), 0.5)
                                       - _pct(sorted(b), 0.5), 6))}
    cat: dict = {}
    for k in CATEGORICAL_PREDICTORS:
        buckets: dict = {}
        for p in resolved:
            v = str((p.get("state_at_post") or {}).get(k))
            d = buckets.setdefault(v, {"matched": 0, "no_fill": 0})
            d["matched" if p.get("status") == VIRTUAL_MATCHED
              else "no_fill"] += 1
        for v, d in buckets.items():
            tot = d["matched"] + d["no_fill"]
            d["rate"] = round(d["matched"] / tot, 4) if tot else None
        cat[k] = buckets

    return {
        "kind": "execution_shadow_predictors",
        "resolved": len(resolved), "matched": len(won), "no_fill": len(lost),
        "numeric": num, "categorical": cat,
        # A MINIMUM ON THE SMALLER CLASS, not just the total. 50 resolved
        # with 1 matched and 49 no-fill passed the old bar and reported a
        # "separation" of 3,573 usd/min between a single matched proposal and
        # 49 others -- a difference computed from one observation. Requiring
        # both classes to clear a floor is what stops a median-of-one being
        # read as a predictor.
        "sufficient_sample": (len(resolved) >= 30 and len(won) >= MIN_CLASS
                              and len(lost) >= MIN_CLASS),
        "min_per_class": MIN_CLASS,
        "matched_n": len(won), "no_fill_n": len(lost),
        "labels": (
            "state captured at POSTING time, before the outcome existed. "
            "Descriptive split only -- nothing here fits, ranks or selects, "
            "because determining what predicts the outcome must come before "
            "optimising the ranker on it. A separation with a tiny n is not "
            "a finding: check sufficient_sample."),
    }


def _has(p: Mapping[str, Any], k: str) -> bool:
    v = (p.get("state_at_post") or {}).get(k)
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def by_cohort(props: Sequence[Mapping[str, Any]], key: str = "tier") -> dict:
    """The owner's metric per ranked cohort, computed the same way in each.

    Cohorts are compared on the metric that matters -- completion probability
    times risk returned per hour of exposure -- not on proposal counts. A
    tier that posts many complements nobody lifts is WORSE than one that
    posts few and completes them, and a count-based comparison says the
    opposite.

    Every cohort reports its own `resolved` and `sufficient_sample`, because
    the usual way this comparison goes wrong is a tier with three resolved
    proposals appearing to beat one with eighty.
    """
    groups: dict = {}
    for p in props:
        k = str(p.get(key) if key == "tier"
                else (p.get("state_at_post") or {}).get(key))
        groups.setdefault(k, []).append(p)
    out = {}
    for k, ps in sorted(groups.items()):
        s = summarize(ps)
        out[k] = {
            "resolved": s["counts"]["resolved"],
            "pending": s["counts"]["pending"],
            "p_completion_given_first_fill": s["p_completion_given_first_fill"],
            "risk_returned_units": s["risk_returned_units"],
            "exposure_hours": s["exposure_hours"],
            "metric": s["metric_completion_x_risk_returned_per_exposure_hour"],
            "median_time_to_fill_s": s["time_to_fill_s"]["median"],
            "median_pair_cost": s["pair_cost_at_completion"]["median"],
            "sufficient_sample": s["counts"]["resolved"] >= 30,
        }
    return {"kind": "execution_shadow_cohorts", "grouped_by": key,
            "cohorts": out,
            "labels": ("compared on completion x risk returned per exposure "
                       "hour, NOT on proposal counts: a tier that posts many "
                       "complements nobody lifts is worse than one that posts "
                       "few and completes them. Check each cohort's "
                       "sufficient_sample before reading a difference.")}


def extend_research(prop: Mapping[str, Any], obs: Sequence[Mapping[str, Any]],
                    now: float) -> Mapping[str, Any]:
    """Keep watching a RESOLVED proposal, without touching its verdict.

    OWNER: "Continue observing unresolved virtual complements after 300s in a
    separate research field if market data remains available. Keep the frozen
    R5F 300s result unchanged. We need to know whether they are unreachable or
    merely slow."

    So this writes ONLY under `research`. A test asserts the 300s status,
    pair cost, residual and risk figures are byte-identical before and after,
    because a research field that can move the headline number is not a
    research field.
    """
    try:
        t_post = float(prop["t_post"])
        if prop.get("status") == VIRTUAL_MATCHED:
            return prop                      # already completed inside 300s
        if now - t_post > EXTENDED_FOLLOW_S:
            return prop                      # research horizon exhausted
        px = D(str(prop["px"]))
        side_yes = bool(prop["side_yes"])
        trades = ST.infer_trades(list(obs or []))
        ev = DEC._fill_evidence(trades, t_post, side_yes, px,
                                min(t_post + EXTENDED_FOLLOW_S, now))
        reach = reachability(prop, obs, now, horizon_s=EXTENDED_FOLLOW_S)
        p = dict(prop)
        p["research"] = {
            "horizon_s": EXTENDED_FOLLOW_S,
            "would_fill_later": ev is not None,
            "later_fill_latency_s": (None if ev is None
                                     else round(ev["t"] - t_post, 1)),
            "reachability": reach,
            "verdict": ("SLOW_BUT_REACHABLE" if ev is not None else
                        "REACHED_BUT_UNFILLED" if reach.get("reached")
                        else "UNREACHABLE_SO_FAR"),
            "labels": ("RESEARCH ONLY. The frozen 300s status above is "
                       "unchanged and no rate, cohort or metric reads this."),
        }
        return p
    except Exception:                                       # noqa: BLE001
        return prop


def reachability_report(props: Sequence[Mapping[str, Any]]) -> dict:
    """WHY completion succeeds or fails, partitioned so it cannot contradict itself.

    OWNER: "reconcile the VIRTUAL_MATCHED event with the current reachability
    summary so one report can never simultaneously imply 'filled' and 'never
    reached' without explicitly separating cohorts/deploy windows."

    That contradiction was real and shipped: at 00:45Z the deployed row read
    reached_our_price 0, filled 1, reached_but_unfilled -1. The filled
    proposal predated the reachability measurement, so it carried no gap
    series -- `filled` was counted over every resolved proposal while
    `reached` was counted only over measured ones. Two populations, one
    subtraction.

    Every rate below is now computed INSIDE a named cohort. The unmeasured
    cohort reports its own counts and feeds NO reachability figure, and the
    coherence block asserts the arithmetic that makes a self-contradicting
    report impossible to emit.
    """
    resolved = [p for p in props if p.get("status") in RESOLVED]
    measured = [p for p in resolved if cohort_of(p) == MEASURED]
    unmeasured = [p for p in resolved if cohort_of(p) == UNMEASURED]

    # A FILL IMPLIES REACH. Reachability is sampled from quotes and a trade
    # can print through between snapshots, so quotes alone can say False on a
    # proposal that demonstrably filled.
    reached = [p for p in measured if reached_at_300s(p)]
    never = [p for p in measured if p not in reached]
    filled_m = [p for p in measured if p.get("status") == VIRTUAL_MATCHED]
    filled_u = [p for p in unmeasured if p.get("status") == VIRTUAL_MATCHED]

    def _med(xs, key):
        vals = sorted(float(p["reachability"][key]) for p in xs
                      if (p.get("reachability") or {}).get(key) is not None)
        return _pct(vals, 0.5)

    research = [p.get("research") for p in props if p.get("research")]
    verdicts: dict = {}
    for r in research:
        verdicts[r.get("verdict")] = verdicts.get(r.get("verdict"), 0) + 1

    out = {
        "kind": "execution_shadow_reachability",
        "resolved_total": len(resolved),
        MEASURED: {
            "resolved": len(measured),
            "reached_our_price": len(reached),
            "never_reached": len(never),
            "filled": len(filled_m),
            "reached_but_unfilled": len(reached) - len(filled_m),
            "median_initial_gap_cents": _med(measured, "initial_gap_cents"),
            "median_min_gap_cents": _med(measured, "min_gap_cents"),
            "median_favorable_excursion_cents": _med(
                measured, "max_favorable_excursion_cents"),
            "median_initial_gap_cents_never_reached": _med(
                never, "initial_gap_cents"),
            "median_initial_gap_cents_reached": _med(
                reached, "initial_gap_cents"),
            "median_time_to_touch_s": _med(reached, "time_to_touch_s"),
            "bottleneck": _bottleneck(len(measured), len(never), len(reached),
                                      len(filled_m)),
        },
        UNMEASURED: {
            "resolved": len(unmeasured), "filled": len(filled_u),
            "note": ("no gap series -- recorded before the measurement "
                     "existed, or never saw a two-sided book after posting. "
                     "Feeds NO reachability figure above."),
        },
        "research_beyond_300s": {"n": len(research), "verdicts": verdicts,
                                 "horizon_s": EXTENDED_FOLLOW_S,
                                 "note": ("RESEARCH ONLY -- no rate, cohort "
                                          "or metric above reads this")},
        "labels": ("gap is how far the OPPOSING touch must still travel for a "
                   "fill. never_reached means the complement was priced "
                   "outside the market's realised range, which is a different "
                   "problem from an order the market declined to lift. Rates "
                   "are computed INSIDE a cohort and never across them."),
    }
    out["coherence"] = _coherence(out, len(measured), len(reached),
                                  len(never), len(filled_m))
    return out


def _coherence(out: Mapping[str, Any], n_measured: int, reached: int,
               never: int, filled: int) -> dict:
    """The arithmetic that makes a self-contradicting report unemittable.

    Checked at emission rather than asserted in a test alone, because the
    contradiction that shipped was in a deployed row, not in a unit test.
    """
    v = []
    if filled > reached:
        v.append(f"filled({filled}) > reached({reached}): a fill implies reach")
    if reached + never != n_measured:
        v.append(f"reached({reached}) + never({never}) != measured({n_measured})")
    if out[MEASURED]["reached_but_unfilled"] < 0:
        v.append("reached_but_unfilled is negative")
    if n_measured == 0 and (reached or never or filled):
        v.append("figures reported against an empty measured cohort")
    return {"ok": not v, "violations": v,
            "labels": ("a report that implies both 'filled' and 'never "
                       "reached' inside ONE cohort is incoherent; across "
                       "cohorts it is merely two different populations, "
                       "which is why they are reported separately")}


def _bottleneck(n, never, reached, matched) -> Optional[str]:
    """Name the constraint, or refuse to when the sample cannot support one."""
    if n < 10:
        return None
    if never / n >= 0.7:
        return ("REACHABILITY: most complements were never reached by the "
                "market. Look at first-leg price, not at fill mechanics.")
    if reached and matched / max(reached, 1) <= 0.3:
        return ("QUEUE/LIQUIDITY: the market reached our price and mostly did "
                "not trade through it.")
    return "MIXED: neither reachability nor queue dominates at this sample."


# ------------------------------- comparison against REAL R5F completions
HISTORICAL_FORENSICS = os.path.join(
    "analysis", "l5", "complete_no_fill_forensics.json")


def historical_benchmark(path: str = HISTORICAL_FORENSICS) -> dict:
    """Complement distance-at-rest on our OWN live completes, filled vs not.

    OWNER: "We already have live tennis/esports completions. Reconstruct their
    complement-distance-at-post if the raw evidence permits it... Don't
    fabricate unavailable historical book state."

    IT PERMITS IT, for the public rest book. `opp_ask_authenticated` is on 8
    of 716 accepted completes (1.1%) and stays UNAVAILABLE -- that is the
    stratum the earlier study refused, and it is still refused here. But the
    REST BOOK gap was captured on 749 orders, and that is the quantity this
    comparison needs.

    Returns the measured distribution and nothing derived: no model, no fit,
    no reconstruction of book state that was never recorded.
    """
    try:
        with open(path) as fh:
            doc = json.load(fh)
        filled = (doc.get("filled_for_contrast") or {})
        unfilled = (doc.get("unfilled") or {})
        return {
            "kind": "historical_complement_distance",
            "source": path,
            "filled": filled.get("closest_book_gap_ticks"),
            "unfilled": unfilled.get("closest_book_gap_ticks"),
            "filled_orders": filled.get("orders"),
            "unfilled_orders": unfilled.get("orders"),
            "unavailable": ("opp_ask_authenticated is on 8 of 716 accepted "
                            "completes (1.1%) -- the authenticated book is "
                            "NOT reconstructed and no figure here uses it"),
            "labels": ("distance to the best OPPOSITE ask at rest, in ticks, "
                       "from our own venue-confirmed completes. The closest "
                       "gap reached -- the same quantity as min_gap_ticks in "
                       "the prospective measurement."),
        }
    except Exception as exc:                                # noqa: BLE001
        return {"kind": "historical_complement_distance",
                "error": repr(exc), "filled": None, "unfilled": None}


def compare_to_history(props: Sequence[Mapping[str, Any]],
                       path: str = HISTORICAL_FORENSICS) -> dict:
    """Prospective gap distribution beside the historical one.

    The comparison the owner framed: if live complements rest far outside the
    range where our own completes historically filled, the bottleneck is
    REACHABILITY -- the economically acceptable price is not where the market
    is -- rather than anything about queue or aggression.
    """
    hist = historical_benchmark(path)
    live = sorted(float(p["reachability"]["min_gap_ticks"])
                  for p in props
                  if p.get("status") in RESOLVED
                  and (p.get("reachability") or {}).get("min_gap_ticks")
                  is not None)
    live_stats = None
    if live:
        live_stats = {"n": len(live), "min": round(live[0], 2),
                      "p25": _pct(live, 0.25), "median": _pct(live, 0.5),
                      "p75": _pct(live, 0.75), "max": round(live[-1], 2),
                      "share_at_or_inside_our_price": round(
                          sum(1 for g in live if g <= 0) / len(live), 4)}
    f_med = (hist.get("filled") or {}).get("median")
    # DECISION-READY IS ABOUT THE CONTRAST, NOT THE TOTAL. OWNER: "Do not wait
    # for 30 total if one outcome class remains tiny. The comparison is
    # decision-ready only when both successful and unsuccessful completion
    # states have enough observations to support the contrast." A 30-total bar
    # passes on 29 failures and 1 success, which is the shape that has misled
    # this project twice tonight.
    won = sum(1 for p in props if p.get("status") == VIRTUAL_MATCHED
              and (p.get("reachability") or {}).get("min_gap_ticks") is not None)
    lost = (live_stats or {}).get("n", 0) - won
    verdict = None
    if live_stats and f_med is not None:
        if won < MIN_CLASS or lost < MIN_CLASS:
            verdict = (f"not decision-ready: filled={won}, unfilled={lost}, "
                       f"need {MIN_CLASS} of each to support the contrast")
        elif live_stats["median"] > (hist.get("unfilled") or {}).get("p75", 1e9):
            verdict = ("live complements rest FARTHER than the p75 of our own "
                       "UNFILLED completes -- consistent with a reachability "
                       "bottleneck, not a queue one")
        elif live_stats["median"] <= f_med:
            verdict = ("live complements rest as close as our own FILLED "
                       "completes -- reachability is not the constraint")
        else:
            verdict = "live complements rest between the filled and unfilled medians"
    return {"kind": "execution_shadow_vs_history",
            "historical": hist, "prospective": live_stats, "verdict": verdict,
            "filled_n": won, "unfilled_n": lost, "min_per_class": MIN_CLASS,
            "labels": ("same quantity on both sides: ticks from the best "
                       "opposite touch, closest reached. Nothing historical "
                       "is reconstructed or modelled.")}


# ------------------------------------------- completion-time distribution
TIME_BUCKETS_S = (60, 120, 300, 600, 1800)


def completion_time_report(props: Sequence[Mapping[str, Any]]) -> dict:
    """Time-to-fill, frozen-horizon and research-horizon kept apart.

    OWNER: "We now have one fill at 260s, close to the frozen 300s horizon."
    That proximity is the point: a horizon that sits just above the only
    observed fill cannot be assumed generous, and the buckets below are what
    would show it.

    THE HISTORICAL HALF IS NOT AVAILABLE AT THIS GRANULARITY and is not
    invented. analysis/l5 carries `median_rest_s` (7.71s filled, 6.99s
    unfilled) and NO per-order timing rows, so median/p75/p90 and the <=60 /
    <=120 / <=300 / <=600 / <=1800 fractions cannot be computed for our own
    historical completes. Worse, that median is not even the same quantity:
    the earlier study found rest_s is determined by the OUTCOME -- a fill ends
    the rest, no fill runs to the deadline -- so it measures the live engine's
    cancel/re-quote deadline, not how long a resting complement takes to
    trade. Our shadow rests 300s without re-quoting. Comparing 7.71s to 260.4s
    would be comparing two different mechanisms.
    """
    resolved = [p for p in props if p.get("status") in RESOLVED]
    filled = [p for p in resolved if p.get("status") == VIRTUAL_MATCHED
              and p.get("latency_s") is not None]
    lat = sorted(float(p["latency_s"]) for p in filled)

    # research horizon: fills that arrived AFTER the frozen verdict
    later = [p for p in props
             if (p.get("research") or {}).get("would_fill_later")
             and (p.get("research") or {}).get("later_fill_latency_s")
             is not None]
    lat_later = sorted(float(p["research"]["later_fill_latency_s"])
                       for p in later)

    n_res = len(resolved)
    frozen_buckets = {
        f"le_{b}s": (None if not n_res else
                     round(sum(1 for x in lat if x <= b) / n_res, 4))
        for b in TIME_BUCKETS_S if b <= FOLLOW_S}
    research_buckets = {
        f"le_{b}s": (None if not n_res else
                     round((sum(1 for x in lat if x <= b)
                            + sum(1 for x in lat_later if x <= b)) / n_res, 4))
        for b in TIME_BUCKETS_S}

    return {
        "kind": "execution_shadow_completion_time",
        "frozen_300s": {
            "horizon_s": FOLLOW_S,
            "resolved": n_res, "filled": len(lat),
            "median_s": _pct(lat, 0.5), "p75_s": _pct(lat, 0.75),
            "p90_s": _pct(lat, 0.9),
            "max_s": lat[-1] if lat else None,
            "fraction_filled_by": frozen_buckets,
            "share_of_fills_in_final_fifth": (
                None if not lat else
                round(sum(1 for x in lat if x > 0.8 * FOLLOW_S) / len(lat), 4)),
        },
        "research_1800s": {
            "horizon_s": EXTENDED_FOLLOW_S,
            "additional_fills_after_frozen_verdict": len(lat_later),
            "median_later_fill_s": _pct(lat_later, 0.5),
            "fraction_filled_by": research_buckets,
            "note": ("RESEARCH ONLY. These fills arrived after the frozen "
                     "verdict was written and change none of it."),
        },
        "historical": {
            "available": False,
            "median_rest_s_filled": 7.71,
            "median_rest_s_unfilled": 6.99,
            "why_unavailable": (
                "analysis/l5 carries no per-order timing rows, so the "
                "percentiles and buckets cannot be computed. The medians that "
                "DO exist measure rest-to-fill-or-deadline under the live "
                "engine's cancel/re-quote behaviour, not time for a resting "
                "complement to trade -- a different quantity from the "
                "prospective figures above, and not comparable to them."),
        },
        "labels": ("a fill at 260.4s against a 300s horizon is near the edge; "
                   "share_of_fills_in_final_fifth is the number that would "
                   "show the horizon is binding."),
    }


# ----------------------------------- reachability as the predictor, by bucket
TICK_BUCKETS = (0, 1, 2, 4, 8)


def reachability_buckets(props: Sequence[Mapping[str, Any]],
                         key: Optional[str] = None) -> dict:
    """Did the acceptable complement get within 0/1/2/4/8 ticks, and how fast?

    OWNER: "Reachability -- not proposal count -- is the key predictor."

    So the unit here is the FIRST-LEG STATE: for each proposal, how close the
    market came to the price that first leg forces, and how long it took. A
    market that produces many proposals none of which come within 8 ticks is
    worse than one producing few that reach 1 tick, and a count-based view
    inverts that.

    Grouped by any state-at-post field (sport, market_type, tier) so the
    comparison the owner asked for -- reachability against flow, movement,
    spread, sport and market type -- is read off the same table.
    """
    measured = [p for p in props
                if p.get("status") in RESOLVED and cohort_of(p) == MEASURED]

    def _group(p):
        if key is None:
            return "all"
        if key == "tier":
            return str(p.get("tier"))
        return str((p.get("state_at_post") or {}).get(key))

    groups: dict = {}
    for p in measured:
        groups.setdefault(_group(p), []).append(p)

    out: dict = {}
    for g, ps in sorted(groups.items()):
        n = len(ps)
        mins = [float(p["reachability"]["min_gap_ticks"]) for p in ps
                if p["reachability"].get("min_gap_ticks") is not None]
        row = {"n": n,
               "within": {f"le_{b}_ticks":
                          (None if not mins else
                           round(sum(1 for m in mins if m <= b) / len(mins), 4))
                          for b in TICK_BUCKETS},
               "median_min_gap_ticks": _pct(sorted(mins), 0.5),
               "p90_min_gap_ticks": _pct(sorted(mins), 0.9),
               "median_time_to_touch_s": _pct(sorted(
                   float(p["reachability"]["time_to_touch_s"]) for p in ps
                   if p["reachability"].get("time_to_touch_s") is not None), 0.5),
               "filled": sum(1 for p in ps
                             if p.get("status") == VIRTUAL_MATCHED),
               }
        # the predictors the owner named, measured ON THE SAME ROWS so the
        # comparison is against this group's reachability, not a global one.
        for f in ("flow_usd_per_min", "spread", "mid_stdev", "mid_range"):
            vals = sorted(float(p["state_at_post"][f]) for p in ps
                          if isinstance((p.get("state_at_post") or {}).get(f),
                                        (int, float)))
            row[f"median_{f}"] = _pct(vals, 0.5)
        row["sufficient_sample"] = n >= MIN_CLASS
        out[g] = row

    return {"kind": "execution_shadow_reachability_buckets",
            "grouped_by": key or "all", "groups": out,
            "tick_buckets": list(TICK_BUCKETS),
            "labels": ("the unit is the FIRST-LEG STATE: how close the market "
                       "came to the price that first leg forces. A group "
                       "producing many proposals none of which come within 8 "
                       "ticks is worse than one producing few that reach 1 "
                       "tick -- a proposal COUNT inverts that ranking. Each "
                       "group carries its own sufficient_sample.")}


# --------------------------- initial-distance bands, the owner's exact edges
DISTANCE_BANDS = (("0-1", 0.0, 1.0), ("2", 1.0, 2.0), ("3-4", 2.0, 4.0),
                  ("5-8", 4.0, 8.0), ("gt8", 8.0, float("inf")))


def distance_band(ticks: Optional[float]) -> Optional[str]:
    """Which band an INITIAL complement distance falls in.

    INITIAL, not minimum: the question is what the first-leg state COMMITTED
    us to, before the market moved. The minimum reached is an outcome; the
    initial gap is the decision input, and only the second can inform a
    selector.
    """
    if ticks is None:
        return None
    for name, lo, hi in DISTANCE_BANDS:
        if (ticks <= hi) if lo == 0.0 else (lo < ticks <= hi):
            return name
    return None


def reached_at_300s(p: Mapping[str, Any]) -> bool:
    """Did the market come to our price inside the frozen horizon?

    A FILL IMPLIES REACH. Reachability is sampled from quotes and a trade can
    print through between snapshots, so quotes alone say False on a proposal
    that demonstrably filled. This predicate is shared rather than re-derived:
    it lived inline in reachability_report, the band report open-coded the
    unconditioned version, and the deployed bands read

        band "2": touched_le_300s 0, filled_le_300s 4

    which is impossible -- the same class of defect as reached_but_unfilled
    reading -1, and from the same cause: one rule written twice.
    """
    return bool((p.get("reachability") or {}).get("reached")
                or p.get("status") == VIRTUAL_MATCHED)


def reached_in_research(p: Mapping[str, Any]) -> bool:
    """The same question at the 1800s research horizon. Anything reached or
    filled inside 300s is necessarily reached inside 1800s."""
    r = p.get("research") or {}
    return bool((r.get("reachability") or {}).get("reached")
                or r.get("would_fill_later") or reached_at_300s(p))


def band_coherence(bands: Mapping[str, Any]) -> dict:
    """The band report's own impossibilities, checked where the row is built.

    The contradiction that shipped was in a deployed row and in no unit test,
    so the check belongs at emission, not only in the suite."""
    v: list = []
    for name, b in bands.items():
        if not b or not b.get("n"):
            continue
        t3, f3 = b.get("touched_le_300s"), b.get("filled_le_300s")
        t18, f18 = (b.get("touched_le_1800s_research"),
                    b.get("filled_le_1800s_research"))
        if f3 > t3:
            v.append(f"{name}: filled_le_300s({f3}) > touched_le_300s({t3})")
        if f18 > t18:
            v.append(f"{name}: filled_1800({f18}) > touched_1800({t18})")
        if t3 > t18:
            v.append(f"{name}: touched_300({t3}) > touched_1800({t18})")
        if f3 > f18:
            v.append(f"{name}: filled_300({f3}) > filled_1800({f18})")
        if t3 > b["n"] or t18 > b["n"]:
            v.append(f"{name}: touched exceeds n({b['n']})")
    return {"ok": not v, "violations": v,
            "labels": ("a band cannot fill more complements than the market "
                       "reached, nor reach more inside 300s than inside 1800s")}


def distance_band_report(props: Sequence[Mapping[str, Any]]) -> dict:
    """Outcomes per initial-distance band, at both horizons.

    OWNER: "For every measured first-leg state, bucket initial complement
    distance... report touched/fill at 300s and at the 1800s research horizon,
    median time to touch/fill, risk returned, residual still outstanding, and
    flow/spread/movement/sport/market type."

    The frozen 300s columns and the 1800s research columns are computed side
    by side but never merged: a band whose complements are reached only after
    900s is a different proposition from one reached inside the policy
    horizon, and collapsing them would hide exactly that.
    """
    measured = [p for p in props
                if p.get("status") in RESOLVED and cohort_of(p) == MEASURED]
    bands: dict = {}
    for p in measured:
        b = distance_band((p.get("reachability") or {}).get("initial_gap_ticks"))
        if b:
            bands.setdefault(b, []).append(p)

    def _med(xs):
        xs = sorted(x for x in xs if x is not None)
        return _pct(xs, 0.5)

    out: dict = {}
    for name, _lo, _hi in DISTANCE_BANDS:
        ps = bands.get(name) or []
        n = len(ps)
        if not n:
            out[name] = {"n": 0}
            continue
        r300 = [p for p in ps if reached_at_300s(p)]
        f300 = [p for p in ps if p.get("status") == VIRTUAL_MATCHED]
        res = [p.get("research") or {} for p in ps]
        r1800 = [p for p in ps if reached_in_research(p)]
        f1800 = len(f300) + sum(1 for x in res if x.get("would_fill_later"))
        # residual still outstanding: a completed proposal returns part of it;
        # an unfilled one leaves the whole first leg standing.
        outstanding = sum(abs(float(p.get("residual_after")
                                    if p.get("residual_after") is not None
                                    else p.get("residual_before") or 0))
                          for p in ps)
        st = [p.get("state_at_post") or {} for p in ps]
        out[name] = {
            "n": n,
            "touched_le_300s": len(r300),
            "filled_le_300s": len(f300),
            "touched_le_1800s_research": len(r1800),
            "filled_le_1800s_research": f1800,
            "fill_rate_300s": round(len(f300) / n, 4),
            "fill_rate_1800s_research": round(f1800 / n, 4),
            "median_time_to_touch_s": _med(
                [(p.get("reachability") or {}).get("time_to_touch_s")
                 for p in ps]),
            "median_time_to_fill_s": _med([p.get("latency_s") for p in f300]),
            "risk_returned": round(sum(float(p.get("risk_returned") or 0)
                                       for p in f300), 4),
            "residual_outstanding_units": round(outstanding, 4),
            "median_flow_usd_per_min": _med([x.get("flow_usd_per_min")
                                             for x in st]),
            "median_spread": _med([x.get("spread") for x in st]),
            "median_mid_stdev": _med([x.get("mid_stdev") for x in st]),
            "sports": sorted({str(x.get("sport")) for x in st}),
            "market_types": sorted({str(x.get("market_type")) for x in st}),
            "sufficient_sample": n >= MIN_CLASS,
        }
    filled_total = sum(b.get("filled_le_300s", 0) for b in out.values())
    unfilled_total = sum(b.get("n", 0) for b in out.values()) - filled_total
    return {
        "kind": "execution_shadow_distance_bands",
        "bands": out,
        "coherence": band_coherence(out),
        "measured_total": len(measured),
        "filled_total": filled_total, "unfilled_total": unfilled_total,
        "decision_ready": (filled_total >= MIN_CLASS
                           and unfilled_total >= MIN_CLASS),
        "min_per_class": MIN_CLASS,
        "labels": ("bands are INITIAL distance -- what the first-leg state "
                   "committed us to before the market moved -- because the "
                   "minimum reached is an outcome and only the input can "
                   "inform a selector. Frozen 300s and 1800s research columns "
                   "are never merged. decision_ready requires BOTH outcome "
                   "classes to clear the floor, not a total."),
    }


# ------------------------------------------- selector opportunity loss (shadow)
FAR_TICKS = float(os.getenv("PMUS_EXEC_FAR_TICKS", "4"))
NEAR_TICKS = float(os.getenv("PMUS_EXEC_NEAR_TICKS", "2"))


def selector_opportunity(snapshot: Sequence[Mapping[str, Any]]) -> Optional[dict]:
    """What flow-ranking chose vs what was closest to completing.

    OWNER: "when the top-flow market is 4-8 ticks from acceptable completion
    but another eligible market is 0-2 ticks away, record what each frozen
    R5F would have done. Shadow only -- do not change ranking yet."

    `snapshot` is one row per eligible market at one instant:
    {slug, flow_usd_per_min, initial_gap_ticks, sport, market_type}.

    Returns None when the situation does not arise -- there is no top-flow
    market, no gap measurement, or the flow leader is ALREADY near. An absent
    observation and a zero-loss observation are different facts, and only the
    second belongs in a rate.

    NOTHING HERE RANKS OR SELECTS. It records a counterfactual pair so the
    question "did flow-ranking cost us reachability?" can be answered from
    evidence gathered before any ranking changed.
    """
    rows = [r for r in snapshot or []
            if r.get("flow_usd_per_min") is not None
            and r.get("initial_gap_ticks") is not None]
    if len(rows) < 2:
        return None
    chosen = max(rows, key=lambda r: float(r["flow_usd_per_min"]))
    nearest = min(rows, key=lambda r: float(r["initial_gap_ticks"]))
    if chosen["slug"] == nearest["slug"]:
        return None
    c_gap = float(chosen["initial_gap_ticks"])
    n_gap = float(nearest["initial_gap_ticks"])
    if not (c_gap >= FAR_TICKS and n_gap <= NEAR_TICKS):
        return None
    return {
        "kind": "selector_opportunity_loss",
        "flow_ranked_choice": {"slug": chosen["slug"],
                               "flow_usd_per_min": chosen["flow_usd_per_min"],
                               "initial_gap_ticks": c_gap,
                               "sport": chosen.get("sport"),
                               "market_type": chosen.get("market_type")},
        "nearest_complement": {"slug": nearest["slug"],
                               "flow_usd_per_min": nearest["flow_usd_per_min"],
                               "initial_gap_ticks": n_gap,
                               "sport": nearest.get("sport"),
                               "market_type": nearest.get("market_type")},
        "gap_difference_ticks": round(c_gap - n_gap, 2),
        "flow_ratio": (None if not float(nearest["flow_usd_per_min"]) else
                       round(float(chosen["flow_usd_per_min"])
                             / float(nearest["flow_usd_per_min"]), 2)),
        "eligible_considered": len(rows),
        "labels": ("SHADOW ONLY -- the ranker is unchanged. Frozen R5F takes "
                   "the flow leader; this records the reachability it passed "
                   "over, so the cost of that rule can be measured before it "
                   "is altered."),
    }


def resolve_opportunities(events: Sequence[Mapping[str, Any]],
                          props: Sequence[Mapping[str, Any]]) -> list:
    """Attach what SUBSEQUENTLY happened to BOTH sides of each pair.

    OWNER item 4: "how often are we currently choosing a >=4-tick flow leader
    while a <=2-tick eligible alternative exists, and what happens
    subsequently to both?"

    The count alone cannot answer whether the rule costs anything. A pair
    where the far flow leader completes anyway and the near alternative never
    does is evidence FOR the current ranking. So each side is resolved from
    the proposals actually tracked for those slugs, and a side with no
    tracked proposal is left explicitly unresolved rather than assumed to
    have failed -- "we did not follow it" and "it did not complete" are the
    distinction this whole module exists to preserve.
    """
    by_slug: dict = {}
    for p in props or []:
        if p.get("status") in RESOLVED:
            by_slug.setdefault(p["slug"], []).append(p)

    def _outcome(slug):
        ps = by_slug.get(slug)
        if not ps:
            return {"resolved": False, "note": "no tracked proposal"}
        filled = [x for x in ps if x.get("status") == VIRTUAL_MATCHED]
        return {"resolved": True, "proposals": len(ps),
                "filled": len(filled),
                "fill_rate": round(len(filled) / len(ps), 4),
                "risk_returned": round(sum(float(x.get("risk_returned") or 0)
                                           for x in filled), 4),
                "median_time_to_fill_s": _pct(
                    sorted(float(x["latency_s"]) for x in filled
                           if x.get("latency_s") is not None), 0.5)}

    out = []
    for e in events or []:
        if not e:
            continue
        row = dict(e)
        row["chosen_outcome"] = _outcome(e["flow_ranked_choice"]["slug"])
        row["passed_over_outcome"] = _outcome(e["nearest_complement"]["slug"])
        c, n = row["chosen_outcome"], row["passed_over_outcome"]
        if c.get("resolved") and n.get("resolved"):
            row["cost_of_the_rule"] = round(
                float(n.get("risk_returned") or 0)
                - float(c.get("risk_returned") or 0), 4)
        else:
            row["cost_of_the_rule"] = None
        out.append(row)
    return out


def opportunity_loss_report(events: Sequence[Mapping[str, Any]]) -> dict:
    """Aggregate the recorded counterfactual pairs."""
    ev = [e for e in events or [] if e]
    if not ev:
        return {"kind": "execution_shadow_opportunity_loss", "n": 0,
                "note": "situation has not arisen in any observed cycle",
                "sufficient_sample": False}
    diffs = sorted(float(e["gap_difference_ticks"]) for e in ev)
    return {
        "kind": "execution_shadow_opportunity_loss",
        "n": len(ev),
        "median_gap_difference_ticks": _pct(diffs, 0.5),
        "p90_gap_difference_ticks": _pct(diffs, 0.9),
        "max_gap_difference_ticks": diffs[-1],
        "far_threshold_ticks": FAR_TICKS, "near_threshold_ticks": NEAR_TICKS,
        "sports_passed_over": sorted({str(e["nearest_complement"].get("sport"))
                                      for e in ev}),
        "sufficient_sample": len(ev) >= MIN_CLASS,
        "both_sides_resolved": sum(
            1 for e in ev if e.get("cost_of_the_rule") is not None),
        "median_cost_of_the_rule": _pct(
            sorted(float(e["cost_of_the_rule"]) for e in ev
                   if e.get("cost_of_the_rule") is not None), 0.5),
        "rule_cost_note": ("positive means the passed-over market returned "
                           "more risk than the one flow-ranking chose. "
                           "Computed ONLY where both sides resolved; a pair "
                           "with an unfollowed side is excluded rather than "
                           "scored as a loss."),
        "labels": ("how far the flow-ranked choice sat from completion versus "
                   "the nearest eligible alternative, at the same instant. "
                   "SHADOW ONLY; no ranking changed."),
    }


# ------------- does initial distance survive stratification? (owner item 3)
STRATA = ("sport", "market_type", "flow_tercile", "spread_tercile",
          "movement_tercile")


def _tercile(vals: Sequence[float], v: Optional[float]) -> Optional[str]:
    if v is None or len(vals) < 3:
        return None
    lo, hi = _pct(vals, 0.333), _pct(vals, 0.667)
    if lo is None or hi is None:
        return None
    return "low" if v <= lo else ("high" if v > hi else "mid")


def stratified_distance(props: Sequence[Mapping[str, Any]]) -> dict:
    """Is initial distance still predictive INSIDE each stratum?

    OWNER: "Measure whether initial distance remains predictive after
    stratifying by flow, spread, movement, sport and market type."

    THE POINT OF STRATIFYING. If close complements happen to occur in
    high-flow markets, an aggregate "close fills better" could be flow
    talking, not distance. The test is whether the near-vs-far contrast
    survives WITHIN a stratum, where flow (or sport, or spread) is held
    roughly fixed.

    Reports the contrast per stratum level and REFUSES to summarise: no
    pooled statistic, no "distance is/isn't predictive" verdict. A stratum
    with one outcome class empty contributes nothing and says so, because a
    fill rate of 1.0 from one observation is not evidence that survived
    anything.
    """
    measured = [p for p in props
                if p.get("status") in RESOLVED and cohort_of(p) == MEASURED
                and (p.get("reachability") or {}).get("initial_gap_ticks")
                is not None]
    flows = sorted(float(p["state_at_post"]["flow_usd_per_min"]) for p in measured
                   if isinstance((p.get("state_at_post") or {}).get(
                       "flow_usd_per_min"), (int, float)))
    spreads = sorted(float(p["state_at_post"]["spread"]) for p in measured
                     if isinstance((p.get("state_at_post") or {}).get("spread"),
                                   (int, float)))
    moves = sorted(float(p["state_at_post"]["mid_stdev"]) for p in measured
                   if isinstance((p.get("state_at_post") or {}).get("mid_stdev"),
                                 (int, float)))

    def _level(p, stratum):
        st = p.get("state_at_post") or {}
        if stratum == "sport":
            return str(st.get("sport"))
        if stratum == "market_type":
            return str(st.get("market_type"))
        if stratum == "flow_tercile":
            return _tercile(flows, st.get("flow_usd_per_min"))
        if stratum == "spread_tercile":
            return _tercile(spreads, st.get("spread"))
        return _tercile(moves, st.get("mid_stdev"))

    out: dict = {}
    for stratum in STRATA:
        levels: dict = {}
        for p in measured:
            lv = _level(p, stratum)
            if lv is None or lv == "None":
                continue
            levels.setdefault(lv, []).append(p)
        rows = {}
        for lv, ps in sorted(levels.items()):
            near = [p for p in ps
                    if float(p["reachability"]["initial_gap_ticks"]) <= NEAR_TICKS]
            far = [p for p in ps
                   if float(p["reachability"]["initial_gap_ticks"]) >= FAR_TICKS]
            n_fill = sum(1 for p in near if p.get("status") == VIRTUAL_MATCHED)
            f_fill = sum(1 for p in far if p.get("status") == VIRTUAL_MATCHED)
            usable = (len(near) >= MIN_CLASS and len(far) >= MIN_CLASS)
            rows[lv] = {
                "n": len(ps),
                "near_n": len(near), "near_filled": n_fill,
                "near_fill_rate": (round(n_fill / len(near), 4) if near else None),
                "far_n": len(far), "far_filled": f_fill,
                "far_fill_rate": (round(f_fill / len(far), 4) if far else None),
                "contrast": (None if not usable else
                             round(n_fill / len(near) - f_fill / len(far), 4)),
                "usable": usable,
                "why_not": (None if usable else
                            f"need {MIN_CLASS} near and {MIN_CLASS} far; "
                            f"have {len(near)} and {len(far)}"),
            }
        out[stratum] = rows

    usable_levels = sum(1 for s in out.values() for r in s.values()
                        if r["usable"])
    return {
        "kind": "execution_shadow_stratified_distance",
        "measured": len(measured),
        "near_ticks": NEAR_TICKS, "far_ticks": FAR_TICKS,
        "strata": out,
        "usable_levels": usable_levels,
        "any_stratum_decidable": usable_levels > 0,
        "labels": ("the contrast is near_fill_rate - far_fill_rate INSIDE a "
                   "stratum, so flow/sport/spread is held roughly fixed. If "
                   "close complements merely cluster in high-flow markets, "
                   "the aggregate is flow talking and the within-stratum "
                   "contrast collapses. NOTHING is pooled and no verdict is "
                   "offered: a level with one class empty contributes nothing "
                   "and says why."),
    }
