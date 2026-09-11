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
        self.load()

    def load(self) -> None:
        try:
            with open(self._path) as fh:
                doc = json.load(fh)
            props = doc.get("proposals") or {}
            self.proposals = {k: dict(v) for k, v in props.items()}
        except Exception:                                       # noqa: BLE001
            return

    def save(self) -> bool:
        try:
            d = os.path.dirname(self._path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"proposals": self.proposals}, fh, default=str)
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
    """WHY completion succeeds or fails, not merely how often.

    OWNER's distinction, which a completion percentage cannot express:

      "People won't fill our maker order"   -- the market came and declined
      vs
      "Our economically acceptable order is too far away from the current
       market to become executable"         -- it never came at all

    The second is a consequence of where the FIRST LEG filled: the complement
    is priced at (ceiling - surplus_avg), so a first leg at 39c forces a
    complement at <=59c, and if the opposing side sits at 65c and never
    travels, the order is unreachable by construction rather than unpopular.
    """
    resolved = [p for p in props if p.get("status") in RESOLVED]
    with_r = [p for p in resolved if (p.get("reachability") or {}).get(
        "observations")]
    # A FILL IMPLIES REACH, exactly as it implies a touch. Reachability is
    # sampled from quotes; a trade can come through the price between
    # snapshots, so `reached` read from quotes alone can be False on a
    # proposal that demonstrably filled.
    reached = [p for p in with_r if p["reachability"].get("reached")
               or p.get("status") == VIRTUAL_MATCHED]
    never = [p for p in with_r if p not in reached]
    # MATCHED MUST BE COUNTED WITHIN THE MEASURED SUBSET. Counting it over
    # all `resolved` while `reached` counts only `with_r` produced
    # reached_but_unfilled = 0 - 1 = -1 live at 00:45Z -- a negative count,
    # which is the kind of impossibility that says the two numbers were not
    # drawn from the same population. Proposals that resolved before the
    # reachability measurement existed have no record and belong in neither.
    matched = [p for p in with_r if p.get("status") == VIRTUAL_MATCHED]
    matched_all = [p for p in resolved if p.get("status") == VIRTUAL_MATCHED]

    def _med(xs, key):
        vals = sorted(float(p["reachability"][key]) for p in xs
                      if p["reachability"].get(key) is not None)
        return _pct(vals, 0.5)

    research = [p.get("research") for p in props if p.get("research")]
    verdicts: dict = {}
    for r in research:
        verdicts[r.get("verdict")] = verdicts.get(r.get("verdict"), 0) + 1

    return {
        "kind": "execution_shadow_reachability",
        "resolved": len(resolved),
        "with_measurement": len(with_r),
        "reached_our_price": len(reached),
        "never_reached": len(never),
        "filled": len(matched_all),
        "filled_with_measurement": len(matched),
        "reached_but_unfilled": max(0, len(reached) - len(matched)),
        "resolved_without_measurement": len(resolved) - len(with_r),
        "median_initial_gap_cents": _med(with_r, "initial_gap_cents"),
        "median_min_gap_cents": _med(with_r, "min_gap_cents"),
        "median_favorable_excursion_cents": _med(
            with_r, "max_favorable_excursion_cents"),
        "median_initial_gap_cents_never_reached": _med(
            never, "initial_gap_cents"),
        "median_initial_gap_cents_reached": _med(reached, "initial_gap_cents"),
        "median_time_to_touch_s": _med(reached, "time_to_touch_s"),
        # the bottleneck is judged ONLY on proposals that carry the
        # measurement it is about.
        "bottleneck": _bottleneck(len(with_r), len(never), len(reached),
                                  len(matched)),
        "research_beyond_300s": {"n": len(research), "verdicts": verdicts,
                                 "horizon_s": EXTENDED_FOLLOW_S,
                                 "note": ("RESEARCH ONLY -- no rate, cohort "
                                          "or metric above reads this")},
        "labels": ("gap is how far the OPPOSING touch must still travel for a "
                   "fill. never_reached means the complement was priced "
                   "outside the market's realised range, which is a different "
                   "problem from an order the market declined to lift."),
    }


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
    verdict = None
    if live_stats and f_med is not None:
        if live_stats["n"] < 30:
            verdict = f"sample too small to compare (n={live_stats['n']}, need 30)"
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
            "labels": ("same quantity on both sides: ticks from the best "
                       "opposite touch, closest reached. Nothing historical "
                       "is reconstructed or modelled.")}
