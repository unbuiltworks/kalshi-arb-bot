"""CONTINUOUS REALTIME L5 CLONE SHADOW -- both regimes, side by side.

WHAT RUNS HERE. The two behavioral clones trained in
`analysis/l5/clone/` (early = pre-2026-06-23, the primary economic
candidate; late = post-2026-06-23, the behavioral comparison) are driven
against the SAME live PMUS market states, each with its OWN virtual
inventory ledger. Nothing is shared between the two arms except the
observations, so a divergence is a policy difference and never a data
difference.

STRUCTURALLY READ-ONLY. This module imports no executor, holds no
credentials, and calls only the public gateway's market and book endpoints.
There is no code path from a decision here to an order. E1 stays disarmed.

THE DISTINCTION THIS MODULE EXISTS TO PRESERVE. The trained model answers
one question only:

    given a fill that just happened and the inventory it produced, what is
    the next OBSERVED action in this market -- same side, opposite side, or
    nothing?

That is NOT an order-placement policy. It says nothing about what price to
rest at, how long to wait, or whether a quote would ever be hit. So every
row this service emits carries two separable halves:

  * `model` -- the clone's probability distribution over EXTEND /
    COMPLEMENT / STOP, which is the trained artifact; and
  * `intended_order` -- OUR translation of that distribution into a side,
    a maker price, a size and a timing, which is a HAND-WRITTEN rule,
    labelled as such, and is not evidence about L5.

`model_domain` records whether the clone was even applicable. Before a
market has any virtual fill there is no "fill that just happened", so the
clone is OUT OF DOMAIN and the entry decision comes from an explicit
documented rule instead. Calling the model's output an order, or using it
where it has no state to condition on, is the single most likely way this
work could mislead us.

FILL FEASIBILITY IS DELIBERATELY PESSIMISTIC. A touch is not a fill. A
trade through our price is not a fill either, because the venue serves the
whole ladder and everything resting at or better than our price is ahead of
us. So a candidate becomes a VIRTUAL fill only when the volume that printed
through our threshold EXCEEDS the queue that was standing in front of us
when we would have posted, and then only for the excess. Statuses are
`NO_TOUCH`, `TOUCHED_NO_TRADE`, `QUEUE_UNPROVEN` and
`WOULD_FILL_QUEUE_CLEARED`. None of them is called a fill, and no realized
profit is ever claimed from any of them.

MARKOUTS AT 10 / 30 / 60 SECONDS, with the actual lag recorded. Polling is
not continuous, so the row that answers "what was the market 30 seconds
later" is the first observation at or after that instant and its true
offset is written beside it. Adverse selection is the mid move signed
against the side we would have bought.
"""
from __future__ import annotations

import json
import os
import pickle
import time
import urllib.request
from collections import deque
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from src import pmus_execution_shadow as EX
from src import pmus_fees as FEES
from src import pmus_market_clock as MC
from src import pmus_sports_shadow_breadth as SB
from src import pmus_status_report as ST

GATEWAY = os.getenv("PMUS_GATEWAY", "https://gateway.polymarket.us")
PREFIX = "PMUS_L5_CLONE_SHADOW "

MODEL_DIR = os.getenv("L5_CLONE_MODEL_DIR", "/app/analysis/l5/clone/models")
STATE_DIR = os.getenv("L5_CLONE_STATE", "/data/l5_clone_shadow")
RAW_MAX_BYTES = int(os.getenv("L5_CLONE_RAW_MAX_BYTES", str(3 * 1024 ** 3)))

WATCH_SLOTS = int(os.getenv("L5_CLONE_WATCH_SLOTS", "48"))
CYCLE_S = float(os.getenv("L5_CLONE_CYCLE_S", "20"))
POLL_PAUSE_S = float(os.getenv("L5_CLONE_POLL_PAUSE_S", "0.25"))
DISCOVERY_EVERY = int(os.getenv("L5_CLONE_DISCOVERY_EVERY", "15"))
OBS_KEEP = int(os.getenv("L5_CLONE_OBS_KEEP", "400"))

# Behavioral unit. Size is NOT cloned (out-of-sample R^2 0.094), so it is a
# risk parameter, fixed, and the same for both arms.
UNIT_SHARES = D(os.getenv("L5_CLONE_UNIT_SHARES", "1"))
# Spring discipline: materially cheaper pair completion, not near-$1.
PAIR_CEILING = D(os.getenv("L5_CLONE_PAIR_CEILING", "0.96"))
MAX_UNITS_PER_SIDE = int(os.getenv("L5_CLONE_MAX_UNITS_PER_SIDE", "4"))
# A first leg is not opened at any price. L5's own acquisition ceiling in the
# frozen execution notes is 0.90, and a leg above it leaves no room for a
# complement that could ever reach the pair ceiling.
MAX_FIRST_LEG_PX = D(os.getenv("L5_CLONE_MAX_FIRST_LEG_PX", "0.90"))
TICK = D("0.01")
ONE = D("1")

MARKOUT_HORIZONS = (10.0, 30.0, 60.0)
FOLLOW_S = float(os.getenv("L5_CLONE_FOLLOW_S", "300"))

LABELS = ["EXTEND", "COMPLEMENT", "STOP"]
FEATS = ["imbalance", "matched_frac", "residual_shares", "pair_cost_sofar",
         "price", "price_is_favourite", "n_fills", "log_size", "t_frac",
         "tte_s", "gap_prev_s", "dur_5m", "same_side_run", "opp_has_any"]


def _utc(ts: float) -> str:
    return SB._utc(ts)


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


# --------------------------------------------------------------- policies --
class Policy:
    """One loaded regime model. Pure function of a feature vector."""

    def __init__(self, name: str, path: str):
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
        self.name = name
        self.path = path
        self.model = blob["model"]
        self.edges = blob["edges"]
        self.feats = list(blob["features"])
        if self.feats != FEATS:
            raise ValueError("feature order drift: %s" % self.name)
        self.n_fit = blob.get("n_fit")

    def proba(self, x: Sequence[float]) -> Dict[str, float]:
        from analysis.l5.clone import gbm
        X = np.asarray([list(x)], dtype=np.float64)
        B = gbm.binify(X, self.edges)
        p = gbm.predict_proba(self.model, B)[0]
        return {LABELS[i]: float(p[i]) for i in range(3)}


def load_policies(model_dir: str = MODEL_DIR) -> Dict[str, Policy]:
    out: Dict[str, Policy] = {}
    for name in ("early", "late"):
        p = os.path.join(model_dir, "l5_policy_%s.pkl" % name)
        out[name] = Policy(name, p)
    return out


# -------------------------------------------------------------- inventory --
class VirtualInventory:
    """Per-market, per-arm. Advanced ONLY by queue-cleared virtual fills.

    `yes`/`no` are share counts; costs are exact Decimals so a pair cost is
    arithmetic and not a float artifact. There is no mark-to-market here and
    no P&L: this object answers "what state would the policy be in", which
    is a different question from "what are we worth".
    """

    def __init__(self) -> None:
        self.yes = D("0"); self.no = D("0")
        self.yes_cost = D("0"); self.no_cost = D("0")
        self.n_fills = 0
        self.last_side_yes: Optional[bool] = None
        self.last_price: Optional[D] = None
        self.last_size: Optional[D] = None
        self.last_t: Optional[float] = None
        self.prev_t: Optional[float] = None
        self.same_side_run = 0

    def add(self, *, side_yes: bool, px: D, qty: D, t: float) -> None:
        if side_yes:
            self.yes += qty; self.yes_cost += px * qty
        else:
            self.no += qty; self.no_cost += px * qty
        self.same_side_run = (self.same_side_run + 1
                              if self.last_side_yes is side_yes else 1)
        self.n_fills += 1
        self.prev_t = self.last_t
        self.last_side_yes = side_yes
        self.last_price = px
        self.last_size = qty
        self.last_t = t

    @property
    def residual(self) -> D:
        return self.yes - self.no

    @property
    def matched(self) -> D:
        return min(self.yes, self.no)

    def avg(self, side_yes: bool) -> Optional[D]:
        n, c = ((self.yes, self.yes_cost) if side_yes
                else (self.no, self.no_cost))
        return (c / n) if n > 0 else None

    def snapshot(self) -> dict:
        return {"yes_shares": str(self.yes), "no_shares": str(self.no),
                "residual_shares": str(self.residual),
                "matched_shares": str(self.matched),
                "yes_avg": (str(self.avg(True)) if self.yes else None),
                "no_avg": (str(self.avg(False)) if self.no else None),
                "n_virtual_fills": self.n_fills,
                "last_side": (None if self.last_side_yes is None
                              else ("YES" if self.last_side_yes else "NO")),
                "same_side_run": self.same_side_run,
                "SOURCE": "queue-cleared virtual fills only; never a fill"}


def features(inv: VirtualInventory, clock: Mapping[str, Any],
             now: float) -> dict:
    """The 14 training features, or an explicit out-of-domain refusal.

    Computed to match `analysis/l5/clone/extract.py` term for term. The
    clone conditions on a fill that just happened; with no virtual fill
    there is nothing to condition on, and this returns `in_domain: False`
    rather than a zero vector -- a zero vector is a state the model was
    never fit on and would answer confidently about.
    """
    if inv.n_fills == 0 or inv.last_side_yes is None:
        return {"in_domain": False,
                "why": "NO_VIRTUAL_FILL_YET: the clone predicts the action "
                       "AFTER a fill; entry is not in its domain"}
    if not clock.get("resolved"):
        return {"in_domain": False,
                "why": "MARKET_CLOCK_UNRESOLVED:%s" % clock.get("basis")}
    side_yes = bool(inv.last_side_yes)
    same = inv.yes if side_yes else inv.no
    opp = inv.no if side_yes else inv.yes
    tot = same + opp
    matched = min(same, opp)
    vs = inv.avg(side_yes) or D("0")
    vo = inv.avg(not side_yes) or D("0")
    pair = (vs + vo) if (same > 0 and opp > 0) else D("1")
    px = inv.last_price or D("0")
    gap = (0.0 if inv.prev_t is None
           else min(300.0, float((inv.last_t or now) - inv.prev_t)))
    vec = [
        float((same - opp) / tot) if tot else 0.0,
        float((2 * matched) / tot) if tot else 0.0,
        float(abs(same - opp)),
        float(pair),
        float(px),
        1.0 if px > D("0.5") else 0.0,
        float(inv.n_fills),
        float(np.log1p(float(inv.last_size or 0))),
        float(clock["t_frac"]),
        float(clock["tte_s"]),
        gap,
        1.0 if abs(float(clock["duration_s"]) - 300.0) < 1.0 else 0.0,
        float(inv.same_side_run),
        1.0 if opp > 0 else 0.0,
    ]
    return {"in_domain": True, "vector": vec,
            "named": dict(zip(FEATS, vec))}


# ----------------------------------------------------------------- books ---
def own_side_quote(obs: Mapping[str, Any], side_yes: bool) -> dict:
    """Our own-side maker bid and taker price, in the side's OWN frame.

    The venue serves one YES frame. A NO buy at p is a YES sell at 1-p, so
    the NO maker bid is `1 - best_yes_ask` and the NO taker price is
    `1 - best_yes_bid`. Getting this backwards is the defect that produced a
    touch check comparing a NO price to a YES bid, so it is derived in one
    place and nowhere else.
    """
    bid = _f(obs.get("bid")); ask = _f(obs.get("ask"))
    if bid is None or ask is None:
        return {"two_sided": False}
    if side_yes:
        maker, taker = D(str(bid)), D(str(ask))
    else:
        maker, taker = ONE - D(str(ask)), ONE - D(str(bid))
    return {"two_sided": True, "maker_px": maker, "taker_px": taker,
            "yes_bid": D(str(bid)), "yes_ask": D(str(ask)),
            "mid": (D(str(bid)) + D(str(ask))) / 2}


def queue_ahead(obs: Mapping[str, Any], side_yes: bool, px: D) -> dict:
    """Shares resting at or better than our price, on our own side.

    This is the number that makes a trade-through insufficient. Everything
    at a better price fills before us, and everything already resting AT our
    price is in front of us in queue. A missing ladder returns
    `known: False`, which keeps the proposal unprovable rather than
    treating an unknown queue as an empty one.
    """
    lv = (obs.get("book_levels") or {})
    side = lv.get("bids") if side_yes else lv.get("offers")
    if not side:
        return {"known": False, "why": "NO_LADDER_ON_OUR_SIDE"}
    # our order, expressed in the venue's YES frame
    yes_px = px if side_yes else (ONE - px)
    ahead = 0.0
    at_level = 0.0
    for lvl in side:
        p = _f(lvl.get("px")); q = _f(lvl.get("qty"))
        if p is None or q is None:
            continue
        pp = D(str(p))
        if side_yes:
            better = pp > yes_px
        else:
            better = pp < yes_px
        if better:
            ahead += q
        elif pp == yes_px:
            at_level += q
    return {"known": True, "better_px_shares": round(ahead, 2),
            "same_px_shares": round(at_level, 2),
            "total_ahead": round(ahead + at_level, 2),
            "frame": "venue YES ladder; our NO order sits at 1-p on offers"}


# ------------------------------------------------------- order translation --
def entry_rule(obs: Mapping[str, Any], clock: Mapping[str, Any]) -> dict:
    """OUR entry rule. NOT the model. Explicit, so it can be criticised.

    The clone cannot express entry (see module docstring), and inventing a
    model-shaped justification for one would be the dishonest option. This
    rule is deliberately plain: post one maker unit on the cheaper side when
    the book is two-sided, the spread is at least two ticks, and the maker
    pair cost already available is at or below the spring ceiling -- i.e.
    when completing the pair is plausible at a materially sub-$1 cost rather
    than requiring the market to come to us.
    """
    if not clock.get("resolved"):
        return {"propose": False, "why": "MARKET_CLOCK_UNRESOLVED"}
    if clock.get("expired") or (clock.get("tte_s") or 0) < 30:
        return {"propose": False, "why": "TOO_CLOSE_TO_EXPIRY"}
    y = own_side_quote(obs, True)
    n = own_side_quote(obs, False)
    if not y.get("two_sided"):
        return {"propose": False, "why": "BOOK_NOT_TWO_SIDED"}
    spread = y["yes_ask"] - y["yes_bid"]
    if spread < TICK:
        return {"propose": False, "why": "BOOK_CROSSED_OR_LOCKED",
                "spread": str(spread)}
    maker_pair = y["maker_px"] + n["maker_px"]          # = 1 - spread
    side_yes = y["maker_px"] <= n["maker_px"]
    q = y if side_yes else n
    if q["maker_px"] > MAX_FIRST_LEG_PX:
        return {"propose": False, "why": "FIRST_LEG_ABOVE_ACQUISITION_CEILING",
                "own_side_maker_px": str(q["maker_px"]),
                "acquisition_ceiling": str(MAX_FIRST_LEG_PX)}
    if q["maker_px"] <= 0:
        return {"propose": False, "why": "NO_OWN_SIDE_BID"}
    return {"propose": True, "side_yes": side_yes,
            "px": q["maker_px"], "qty": UNIT_SHARES,
            "maker_pair_cost_now": str(maker_pair),
            "pair_ceiling_applies_to_the_complement": str(PAIR_CEILING),
            "RULE": "hand-written entry rule, not the trained model"}


def completion_due(inv: VirtualInventory, obs: Mapping[str, Any]) -> dict:
    """The patient fee-safe COMPLETE leg of the sequence. A RISK RULE.

    WHY THIS IS NOT THE MODEL. The clone predicts L5's next observed FILL,
    and at the per-fill level that is EXTEND about 70% of the time in both
    regimes -- so an argmax policy extends, extends again, and never
    completes. Live, that is not a strategy, it is unbounded one-sided
    inventory: 9 markets carried residual for hours in shadow and
    `matched_pair_cost` had a sample of ZERO, which is the one number that
    decides whether the pair thesis survives contact with this venue.

    The existing E1 discipline already says what to do -- maker OPEN,
    optional same-side EXTEND, then a PATIENT FEE-SAFE COMPLETE when price
    improves -- and completion there is a property of the inventory and the
    pair budget, never of a prediction. So this rule fires on the state: if
    we hold a residual and the opposite side's own maker price fits inside
    the pair ceiling minus what the residual already cost, post the
    complement. The model's own distribution is recorded beside it, so
    agreement and disagreement with L5's predicted action stay measurable
    rather than being blurred into one number.
    """
    resid = inv.residual
    if resid == 0:
        return {"due": False, "why": "NO_RESIDUAL"}
    long_yes = resid > 0
    side_yes = not long_yes                     # the complement side
    q = own_side_quote(obs, side_yes)
    if not q.get("two_sided"):
        return {"due": False, "why": "BOOK_NOT_TWO_SIDED"}
    avg = inv.avg(long_yes)
    if avg is None:
        return {"due": False, "why": "NO_RESIDUAL_COST_BASIS"}
    budget = PAIR_CEILING - avg
    px = q["maker_px"]
    if px > budget:
        return {"due": False, "why": "COMPLEMENT_ABOVE_PAIR_CEILING",
                "own_side_maker_px": str(px), "pair_budget": str(budget),
                "residual_avg": str(avg)}
    return {"due": True, "side_yes": side_yes, "px": px,
            "qty": min(UNIT_SHARES, abs(resid)),
            "pair_cost_if_filled": str(avg + px),
            "pair_ceiling": str(PAIR_CEILING),
            "residual_shares": str(resid),
            "RULE": ("patient fee-safe completion; a risk rule over "
                     "inventory, not a model prediction")}


def intended_order(pol_name: str, proba: Mapping[str, float],
                   inv: VirtualInventory, obs: Mapping[str, Any],
                   clock: Mapping[str, Any]) -> dict:
    """Translate a predicted next ACTION into an intended maker ORDER.

    Everything here is our construction. The model supplies only which
    action it expects; side comes from the action's meaning relative to the
    last virtual fill, price is the own-side best bid (maker, never
    crossing), size is a fixed risk unit because size is not cloneable, and
    timing is immediate-or-wait depending on whether STOP dominates.
    """
    action = max(proba, key=lambda k: proba[k])
    conf = float(proba[action])
    out = {"policy": pol_name, "model_action": action,
           "model_confidence": round(conf, 4),
           "model_proba": {k: round(float(v), 4) for k, v in proba.items()},
           "TRANSLATION_IS_OURS": True}
    if action == "STOP":
        out.update({"propose": False, "timing": "WAIT",
                    "why": "model expects no further action in this market"})
        return out
    last_yes = bool(inv.last_side_yes)
    side_yes = last_yes if action == "EXTEND" else (not last_yes)
    q = own_side_quote(obs, side_yes)
    if not q.get("two_sided"):
        out.update({"propose": False, "why": "BOOK_NOT_TWO_SIDED"})
        return out
    held = inv.yes if side_yes else inv.no
    if action == "EXTEND" and held >= MAX_UNITS_PER_SIDE * UNIT_SHARES:
        out.update({"propose": False, "why": "SIDE_UNIT_CAP_REACHED",
                    "cap_units": MAX_UNITS_PER_SIDE})
        return out
    px = q["maker_px"]
    if action == "COMPLEMENT":
        surplus = inv.avg(last_yes)
        if surplus is not None:
            budget = PAIR_CEILING - surplus
            if px > budget:
                out.update({"propose": False,
                            "why": "COMPLEMENT_ABOVE_PAIR_CEILING",
                            "own_side_maker_px": str(px),
                            "pair_budget": str(budget),
                            "pair_ceiling": str(PAIR_CEILING)})
                return out
            out["pair_cost_if_filled"] = str(surplus + px)
    out.update({"propose": True, "side_yes": side_yes,
                "side": "YES" if side_yes else "NO",
                "px": px, "qty": UNIT_SHARES, "timing": "POST_NOW",
                "price_rule": "join own-side best bid; maker only, never "
                              "cross",
                "size_rule": "fixed risk unit; size is not cloneable "
                             "(out-of-sample R2 0.094)"})
    return out


# ------------------------------------------------------------- feasibility --
NO_TOUCH = "NO_TOUCH"
TOUCHED_NO_TRADE = "TOUCHED_NO_TRADE"
QUEUE_UNPROVEN = "QUEUE_UNPROVEN"
QUEUE_CLEARED = "WOULD_FILL_QUEUE_CLEARED"
PENDING = "PENDING"


def open_candidate(*, policy: str, slug: str, side_yes: bool, px: D, qty: D,
                   t_post: float, obs: Mapping[str, Any],
                   clock: Mapping[str, Any], inv: VirtualInventory,
                   model: Optional[Mapping[str, Any]], origin: str) -> dict:
    qa = queue_ahead(obs, side_yes, px)
    q = own_side_quote(obs, side_yes)
    return {
        "id": "%s|%s|%.3f|%s|%s" % (policy, slug, t_post, side_yes, px),
        "policy": policy, "slug": slug, "origin": origin,
        "side_yes": bool(side_yes), "side": "YES" if side_yes else "NO",
        "px": str(px), "qty": str(qty),
        "t_post": t_post, "t_post_utc": _utc(t_post),
        "status": PENDING,
        "queue_ahead_at_post": qa,
        "state_at_post": {
            "yes_bid": str(q.get("yes_bid")) if q.get("two_sided") else None,
            "yes_ask": str(q.get("yes_ask")) if q.get("two_sided") else None,
            "mid": str(q.get("mid")) if q.get("two_sided") else None,
            "own_side_maker_px": str(q.get("maker_px")) if q.get("two_sided") else None,
            "own_side_taker_px": str(q.get("taker_px")) if q.get("two_sided") else None,
            "spread": (str(q["yes_ask"] - q["yes_bid"])
                       if q.get("two_sided") else None),
            "bid_qty": obs.get("bid_qty"), "ask_qty": obs.get("ask_qty"),
            "shares_traded_cum": obs.get("shares_traded_cum"),
            "t_frac": clock.get("t_frac"), "tte_s": clock.get("tte_s"),
            "clock_basis": clock.get("basis"),
        },
        "inventory_before": inv.snapshot(),
        "model": dict(model) if model else None,
        "markouts": {},
        "NEVER_A_FILL": True,
    }


def advance_candidate(cand: dict, obs: Sequence[Mapping[str, Any]],
                      now: float) -> dict:
    """Judge one candidate against the tape, pessimistically.

    Reuses `pmus_execution_shadow`'s fill threshold and traded-through
    attribution -- the same arithmetic the sports execution shadow is judged
    by, including the frame correction -- and then applies the queue gate it
    does not: volume must exceed what was resting at or better than our
    price when we would have posted.
    """
    if cand.get("status") in (NO_TOUCH, TOUCHED_NO_TRADE, QUEUE_UNPROVEN,
                              QUEUE_CLEARED):
        return cand
    try:
        t_post = float(cand["t_post"])
        px = D(str(cand["px"]))
        side_yes = bool(cand["side_yes"])
        later = [o for o in obs if (o.get("_poll_t") or 0) > t_post]
        trades = ST.infer_trades(list(obs))
        touched = EX._touched(later, side_yes, px)
        through = EX._volume_through(trades, t_post, now, side_yes, px)
        qa = cand.get("queue_ahead_at_post") or {}
        ahead = _f(qa.get("total_ahead")) if qa.get("known") else None
        cand["touched"] = bool(touched)
        cand["traded_through"] = through
        cand["queue_ahead_shares"] = ahead
        cand["excess_over_queue"] = (None if ahead is None
                                     else round(through - ahead, 2))
        _mark(cand, later)
        window_open = now < t_post + FOLLOW_S
        if ahead is None:
            cand["status"] = PENDING if window_open else QUEUE_UNPROVEN
            cand["why"] = "QUEUE_UNKNOWN_AT_POST: an unseen queue is never "\
                          "assumed empty"
            return cand
        if through > ahead:
            fillable = min(float(D(str(cand["qty"]))), through - ahead)
            cand["status"] = QUEUE_CLEARED
            cand["virtual_fill_qty"] = round(fillable, 4)
            cand["t_cleared"] = now
            cand["latency_s"] = round(now - t_post, 1)
            return cand
        if not window_open:
            cand["status"] = (QUEUE_UNPROVEN if through > 0
                              else (TOUCHED_NO_TRADE if touched else NO_TOUCH))
        return cand
    except Exception as exc:                                   # noqa: BLE001
        cand["advance_error"] = "%s:%s" % (type(exc).__name__, str(exc)[:120])
        return cand


def _mark(cand: dict, later: Sequence[Mapping[str, Any]]) -> None:
    """Market conditions 10/30/60s after the candidate, with the true lag.

    Adverse selection is the mid move SIGNED AGAINST the side we would have
    bought: a YES buyer is hurt when the mid falls. Complement economics are
    recomputed from the book at that instant, both maker and taker, with the
    taker fee charged from the published schedule -- a maker rebate is never
    netted in.
    """
    t_post = float(cand["t_post"])
    side_yes = bool(cand["side_yes"])
    px = D(str(cand["px"]))
    base = _f((cand.get("state_at_post") or {}).get("mid"))
    for h in MARKOUT_HORIZONS:
        key = "t+%ds" % int(h)
        if key in (cand.get("markouts") or {}):
            continue
        row = next((o for o in later if (o.get("_poll_t") or 0) >= t_post + h),
                   None)
        if row is None:
            continue
        q = own_side_quote(row, side_yes)
        c = own_side_quote(row, not side_yes)
        mk: Dict[str, Any] = {
            "observed_at_utc": row.get("t"),
            "actual_lag_s": round(float(row["_poll_t"]) - t_post, 1),
            "two_sided": bool(q.get("two_sided")),
        }
        if q.get("two_sided") and base is not None:
            mid = float(q["mid"])
            move = mid - base
            mk["mid"] = mid
            mk["mid_move"] = round(move, 5)
            # A YES buyer is hurt when the mid FALLS, a NO buyer when it
            # rises. Signing this the other way round reports a market
            # running away from us as edge in our favour.
            mk["adverse_selection_per_share"] = round(
                move if side_yes else -move, 5)
            mk["adverse_selection_sign_note"] = (
                "negative = the market moved against the side we would "
                "have bought")
        if c.get("two_sided"):
            maker_c = c["maker_px"]; taker_c = c["taker_px"]
            tf = FEES.taker_fee(taker_c, D(str(cand.get("qty") or 1)))
            qty = D(str(cand.get("qty") or 1))
            per_share_fee = (tf / qty) if qty else D("0")
            mk["complement_available"] = True
            mk["complement_maker_px"] = str(maker_c)
            mk["complement_taker_px"] = str(taker_c)
            mk["pair_cost_maker"] = str(px + maker_c)
            mk["pair_cost_taker_fee_inclusive"] = str(px + taker_c
                                                      + per_share_fee)
            mk["completable_at_or_below_ceiling_maker"] = bool(
                px + maker_c <= PAIR_CEILING)
            mk["completable_under_one_taker_fee_inclusive"] = bool(
                px + taker_c + per_share_fee < ONE)
            mk["fee_source"] = FEES.SOURCE
            mk["maker_rebate_netted"] = False
        else:
            mk["complement_available"] = False
        cand.setdefault("markouts", {})[key] = mk


# ------------------------------------------------------------- discovery ---
# The venue serves `categories=` on /v1/markets (see
# pmus_live_discovery_diag). An UNFILTERED walk is capped by max_pages, and
# the first live cycle hit that cap exactly -- 15,000 rows, 30 pages of 500
# -- so whatever sits past it was invisible. Walking per category first
# guarantees the short-dated crypto windows are reached even when a few
# thousand sports markets are served ahead of them.
CATEGORIES = tuple((os.getenv("L5_CLONE_CATEGORIES") or
                    "crypto,sports,economics,politics,commodities,weather,"
                    "culture").split(","))


def discover(get_json, *, max_pages: int = 30, page: int = 500,
             sleep=time.sleep, pause: float = 0.35,
             categories: Optional[Sequence[str]] = None) -> List[dict]:
    """Every live open market the gateway will serve, all categories.

    Market-agnostic on purpose (CLAUDE.md): crypto, sports, econ, politics
    and anything else lawful compete for the same observation slots. Paced,
    because this shares a gateway with other services and an unpaced walk
    has drawn 429s before. Degrades to what it gathered rather than raising.

    Each category is walked on its own, then the unfiltered slate, and the
    union is de-duplicated by slug. A category the venue does not know
    simply contributes nothing.
    """
    seen: Dict[str, dict] = {}
    cats = list(categories if categories is not None else CATEGORIES)
    walks = [("&categories=%s" % c.strip(), c.strip())
             for c in cats if c.strip()] + [("", None)]
    for suffix, cat in walks:
        for p in range(max_pages):
            if sleep:
                sleep(pause)
            try:
                resp = get_json("/v1/markets?active=true&closed=false"
                                "&limit=%d&offset=%d%s"
                                % (page, p * page, suffix))
            except Exception:                                  # noqa: BLE001
                break
            ms = ((resp or {}).get("markets")
                  if isinstance(resp, Mapping) else None)
            if not isinstance(ms, list) or not ms:
                break
            for m in ms:
                if isinstance(m, Mapping) and m.get("slug"):
                    row = dict(m)
                    if cat and not row.get("category"):
                        row["category"] = cat
                    seen.setdefault(str(row["slug"]), row)
            if len(ms) < page:
                break
    return list(seen.values())


TIME_KEY_HINTS = ("time", "date", "expir", "close", "start", "end", "resolve")


def clock_diagnostic(markets: Sequence[Mapping[str, Any]], now: float,
                     n: int = 3) -> dict:
    """Why nothing resolved, from the venue's own payload.

    Emitted when discovery yields zero watchable markets. Without this the
    service reports `clock_resolvable_and_live: 0` forever and the reason
    lives only on a host this session cannot reach.
    """
    samples = []
    for m in list(markets)[:n]:
        slug = str(m.get("slug"))
        dur, start, basis = MC.resolve(slug, m)
        samples.append({
            "slug": slug, "basis": basis, "duration_s": dur,
            "category": m.get("category"),
            "time_like_keys": {k: m.get(k) for k in m
                               if any(h in str(k).lower()
                                      for h in TIME_KEY_HINTS)},
            "all_keys": sorted(str(k) for k in m)})
    return {"samples": samples,
            "bases": _mix([MC.resolve(str(m.get("slug")), m)[2]
                           for m in markets])}


def eligible_markets(markets: Sequence[Mapping[str, Any]], now: float
                     ) -> List[dict]:
    """Markets whose clock we can actually resolve, nearest expiry first.

    A market with an UNRESOLVED clock is not watched at all: two of the
    policy's features are clock features and there is no honest value to
    give them. This is the fix for the duration assumption -- the engine
    stays market-agnostic, but it declines to pretend about time.
    """
    out = []
    for m in markets:
        slug = str(m.get("slug"))
        pos = MC.position(slug, m, now)
        if not pos.get("resolved") or pos.get("expired"):
            continue
        if (pos.get("tte_s") or 0) < 60:
            continue
        out.append({"slug": slug, "clock": pos,
                    "category": m.get("category"),
                    "market": m})
    out.sort(key=lambda r: r["clock"]["tte_s"])
    return out


# ----------------------------------------------------------- settlement ---
def resolved_outcome(market: Mapping[str, Any]) -> dict:
    """Did the venue's LONG side win, per the venue's own resolution?

    The engine keeps one frame: `price.value` is always the long (YES) side,
    and `outcomes[0]`/`outcomePrices[0]` is that same side. A resolution is
    only read when the prices are an unambiguous 0/1 pair; anything else --
    still trading, a mid-price, a malformed array -- returns
    `resolved: False`. A settlement is the one number in this whole service
    that is not a model output, so it is never inferred.
    """
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (TypeError, ValueError):
            prices = None
    if not isinstance(prices, (list, tuple)) or len(prices) != 2:
        return {"resolved": False, "why": "NO_TWO_OUTCOME_PRICES"}
    try:
        a, b = D(str(prices[0])), D(str(prices[1]))
    except (InvalidOperation, ValueError, ArithmeticError):
        return {"resolved": False, "why": "OUTCOME_PRICES_UNPARSEABLE"}
    if {a, b} != {D("0"), D("1")}:
        return {"resolved": False, "why": "OUTCOME_PRICES_NOT_ZERO_ONE",
                "outcome_prices": [str(a), str(b)]}
    return {"resolved": True, "yes_won": a == D("1"),
            "outcomes": market.get("outcomes"),
            "outcome_prices": [str(a), str(b)],
            "closed": market.get("closed"), "status": market.get("status"),
            "SOURCE": "venue resolution; not modelled"}


def settle_inventory(inv: "VirtualInventory", outcome: Mapping[str, Any]
                     ) -> dict:
    """Shadow settlement economics for one market, decomposed.

    THREE COMPONENTS, NEVER SUMMED INTO ONE HEADLINE WITHOUT THE PARTS,
    because the whole question is which one carries the result:

      * matched pairs    -- a matched share pays exactly 1.00 at
                            resolution whichever way it resolves, so its
                            economics are 1 - (yes_avg + no_avg) per pair
                            and carry no directional risk;
      * directional residual -- the unmatched shares, which pay 1.00 or 0.00
                            depending on the outcome;
      * fees             -- zero here, and that is a FACT about what was
                            simulated, not an assumption: every shadow fill
                            is a maker fill at the own-side bid, and the
                            published PMUS schedule charges takers only.
                            Maker rebates are NOT netted in.

    Labelled SHADOW_SETTLED. It is arithmetic over virtual fills that a
    pessimistic queue test admitted; no order was ever placed.
    """
    matched = inv.matched
    ya, na = inv.avg(True), inv.avg(False)
    out: Dict[str, Any] = {
        "matched_shares": str(matched),
        "yes_shares": str(inv.yes), "no_shares": str(inv.no),
        "yes_avg": (str(ya) if ya is not None else None),
        "no_avg": (str(na) if na is not None else None),
        "fees_usd": "0",
        "fees_note": ("every shadow fill is a maker fill at the own-side "
                      "bid; the published schedule charges takers only. "
                      "Maker rebates are excluded, not netted."),
        "maker_rebate_netted": False,
        "LABEL": "SHADOW_SETTLED_NOT_REALIZED",
    }
    if not outcome.get("resolved"):
        out["settled"] = False
        out["why"] = outcome.get("why")
        return out
    yes_won = bool(outcome["yes_won"])
    pair_pnl = D("0")
    if matched > 0 and ya is not None and na is not None:
        pair_pnl = matched * (ONE - (ya + na))
    resid = inv.residual                      # + means long YES
    resid_pnl = D("0")
    if resid != 0:
        side_yes = resid > 0
        qty = abs(resid)
        avg = inv.avg(side_yes) or D("0")
        payout = ONE if (side_yes == yes_won) else D("0")
        resid_pnl = qty * (payout - avg)
    out.update({
        "settled": True,
        "yes_won": yes_won,
        "outcome_prices": outcome.get("outcome_prices"),
        "matched_pair_cost": (str(ya + na) if (ya is not None
                                               and na is not None) else None),
        "matched_pair_pnl_usd": str(pair_pnl),
        "residual_shares_signed": str(resid),
        "residual_pnl_usd": str(resid_pnl),
        "net_usd": str(pair_pnl + resid_pnl),
    })
    return out


# ----------------------------------------------------------- the service ---
class CloneShadow:
    """One cycle: discover -> poll books -> both policies decide -> judge.

    Both arms see the same observation list. Each keeps its own
    `VirtualInventory` per market and its own candidate records, so the
    early/late comparison is between policies and not between samples.
    """

    def __init__(self, get_json=None, sleep=time.sleep, now=time.time,
                 policies: Optional[Mapping[str, Policy]] = None,
                 state_dir: Optional[str] = None,
                 slots: int = WATCH_SLOTS):
        self._get = get_json or self._http
        self._sleep = sleep
        self._now = now
        self._slots = slots
        self.state_dir = state_dir or STATE_DIR
        # `or` would make an explicitly EMPTY policy map reload from
        # disk, which is a silent surprise in any caller that means
        # 'no policies'.
        self.policies = dict(load_policies() if policies is None
                             else policies)
        self.obs: Dict[str, deque] = {}
        self.clocks: Dict[str, dict] = {}
        self.inv: Dict[str, Dict[str, VirtualInventory]] = {
            k: {} for k in self.policies}
        self.candidates: Dict[str, Dict[str, dict]] = {
            k: {} for k in self.policies}
        self.cycles = 0
        self.rows_emitted = 0
        # One durable row per (candidate, status) TRANSITION. Appending every
        # non-pending candidate on every cycle wrote the same 5 candidates
        # 30 times in a 30-cycle soak (637 KB for 329 rows), which both
        # wastes the volume the breadth recorder already filled once and
        # makes any offline count of candidates wrong unless the reader
        # happens to de-duplicate.
        self._rows_seen: set = set()
        # Most of the 332 clock-resolvable crypto windows are FUTURE windows
        # whose book endpoint does not serve yet, and the slate is sorted by
        # expiry, so the slots filled with markets that could not be read at
        # all -- 5 of 48 polled on the first live run. Counting failures per
        # slug lets the slots migrate to markets that actually quote.
        self._poll_fail: Dict[str, int] = {}
        self.one_sided: Dict[str, dict] = {}
        self.settled: Dict[str, dict] = {}
        self._load()

    # ---- transport ------------------------------------------------------
    @staticmethod
    def _http(path: str) -> dict:
        req = urllib.request.Request(GATEWAY + path)
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)

    # ---- durability -----------------------------------------------------
    def _path(self, name: str) -> str:
        return os.path.join(self.state_dir, name)

    def _over_budget(self) -> bool:
        try:
            total = sum(os.path.getsize(os.path.join(self.state_dir, f))
                        for f in os.listdir(self.state_dir))
            return total >= RAW_MAX_BYTES
        except Exception:                                      # noqa: BLE001
            return False

    def append_rows(self, kind: str, rows: Sequence[Mapping[str, Any]]
                    ) -> bool:
        """Append-only evidence. A derived report is never the evidence."""
        if not rows:
            return True
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            if self._over_budget():
                return False
            day = time.strftime("%Y-%m-%d", time.gmtime(self._now()))
            with open(self._path("%s-%s.jsonl" % (kind, day)), "a") as fh:
                for r in rows:
                    fh.write(json.dumps(r, separators=(",", ":"),
                                        default=str) + "\n")
            self.rows_emitted += len(rows)
            return True
        except Exception:                                      # noqa: BLE001
            return False

    def save(self) -> bool:
        """Inventories and open candidates survive a redeploy.

        A shadow whose ledgers reset on every push measures nothing but the
        last few minutes, which is the defect that cost the breadth service
        its cohort tallies on 2026-09-10.
        """
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            out = {"cycles": self.cycles,
                   "saved_utc": _utc(self._now()),
                   "settled": self.settled,
                   "arms": {}}
            for pol in self.policies:
                out["arms"][pol] = {
                    "inventories": {s: {
                        "yes": str(i.yes), "no": str(i.no),
                        "yes_cost": str(i.yes_cost), "no_cost": str(i.no_cost),
                        "n_fills": i.n_fills,
                        "last_side_yes": i.last_side_yes,
                        "last_price": (str(i.last_price) if i.last_price
                                       is not None else None),
                        "last_size": (str(i.last_size) if i.last_size
                                      is not None else None),
                        "last_t": i.last_t, "prev_t": i.prev_t,
                        "same_side_run": i.same_side_run}
                        for s, i in self.inv[pol].items()},
                    "candidates": list(self.candidates[pol].values()),
                }
            tmp = self._path("state.json.tmp")
            with open(tmp, "w") as fh:
                json.dump(out, fh, default=str)
            os.replace(tmp, self._path("state.json"))
            return True
        except Exception:                                      # noqa: BLE001
            return False

    def _load(self) -> None:
        try:
            with open(self._path("state.json")) as fh:
                blob = json.load(fh)
        except Exception:                                      # noqa: BLE001
            return
        self.cycles = int(blob.get("cycles") or 0)
        self.settled = dict(blob.get("settled") or {})
        for pol, arm in (blob.get("arms") or {}).items():
            if pol not in self.policies:
                continue
            for slug, d in (arm.get("inventories") or {}).items():
                i = VirtualInventory()
                i.yes = D(str(d.get("yes") or 0)); i.no = D(str(d.get("no") or 0))
                i.yes_cost = D(str(d.get("yes_cost") or 0))
                i.no_cost = D(str(d.get("no_cost") or 0))
                i.n_fills = int(d.get("n_fills") or 0)
                i.last_side_yes = d.get("last_side_yes")
                i.last_price = (D(str(d["last_price"]))
                                if d.get("last_price") is not None else None)
                i.last_size = (D(str(d["last_size"]))
                               if d.get("last_size") is not None else None)
                i.last_t = d.get("last_t"); i.prev_t = d.get("prev_t")
                i.same_side_run = int(d.get("same_side_run") or 0)
                self.inv[pol][slug] = i
            for c in (arm.get("candidates") or []):
                if isinstance(c, Mapping) and c.get("id"):
                    self.candidates[pol][str(c["id"])] = dict(c)

    # ---- observation ----------------------------------------------------
    def poll(self, rows: Sequence[Mapping[str, Any]]) -> int:
        now = self._now()
        ok = 0
        for r in rows:
            slug = r["slug"]
            try:
                md = (self._get("/v1/markets/%s/book" % slug) or {}).get(
                    "marketData")
            except Exception:                                  # noqa: BLE001
                self._poll_fail[slug] = self._poll_fail.get(slug, 0) + 1
                continue
            if not isinstance(md, Mapping):
                self._poll_fail[slug] = self._poll_fail.get(slug, 0) + 1
                continue
            self._poll_fail.pop(slug, None)
            row = SB.observation_row(slug, md, now)
            if row.get("bid") is None or row.get("ask") is None:
                # A ONE-SIDED BOOK IS THE SINGLE BIGGEST REASON NOTHING IS
                # PROPOSED (9 of 14 decisions a cycle), and "not two-sided"
                # is indistinguishable from a field we are reading under the
                # wrong name -- which has happened on this venue before
                # (`asks` vs `offers`, pmus_sports_offers_hotfix). So the
                # payload's own shape is recorded, bounded to the keys and
                # the level counts.
                self.one_sided[slug] = {
                    "t_utc": _utc(now),
                    "bid": row.get("bid"), "ask": row.get("ask"),
                    "n_bid_levels": len(md.get("bids") or []),
                    "n_offer_levels": len(md.get("offers") or []),
                    "n_asks_key_levels": len(md.get("asks") or []),
                    "state": md.get("state"),
                    "payload_keys": sorted(str(k) for k in md),
                }
            else:
                self.one_sided.pop(slug, None)
            self.obs.setdefault(slug, deque(maxlen=OBS_KEEP)).append(row)
            self.clocks[slug] = MC.position(slug, r.get("market"), now)
            ok += 1
            if self._sleep:
                self._sleep(POLL_PAUSE_S)
        return ok

    # ---- decisions ------------------------------------------------------
    FRESH_S = float(os.getenv("L5_CLONE_FRESH_S", "90"))

    def decide(self, slugs: Sequence[str]) -> List[dict]:
        """One decision row per (policy, market) with a FRESH observation.

        STALENESS WAS SILENTLY ADMITTED. `self.obs` keeps the last 400
        observations per slug forever, so a market polled an hour ago and
        unreadable ever since still had a `dq[-1]` -- and the live run
        duly produced 44 decisions a cycle off 5 successful polls, 42 of
        them refused on a book that was read long ago. A decision priced
        against a stale book is not an observation of anything, and in an
        economic measurement it is worse than no decision at all.
        """
        now = self._now()
        out: List[dict] = []
        for slug in slugs:
            dq = self.obs.get(slug)
            if not dq:
                continue
            obs = dq[-1]
            age = now - float(obs.get("_poll_t") or 0)
            if age > self.FRESH_S:
                continue
            clock = self.clocks.get(slug) or {}
            for pol, policy in self.policies.items():
                inv = self.inv[pol].setdefault(slug, VirtualInventory())
                feat = features(inv, clock, now)
                row = {"t_utc": _utc(now), "policy": pol, "slug": slug,
                       "cycle": self.cycles,
                       "market_state": {
                           "yes_bid": obs.get("bid"), "yes_ask": obs.get("ask"),
                           "bid_qty": obs.get("bid_qty"),
                           "ask_qty": obs.get("ask_qty"),
                           "spread": (None if obs.get("bid") is None
                                      or obs.get("ask") is None else
                                      round(_f(obs["ask"]) - _f(obs["bid"]), 4)),
                           "shares_traded_cum": obs.get("shares_traded_cum"),
                           "last_trade_px": obs.get("last_trade_px"),
                           "observed_utc": obs.get("t"),
                           "t_frac": clock.get("t_frac"),
                           "tte_s": clock.get("tte_s"),
                           "clock_basis": clock.get("basis")},
                       "inventory": inv.snapshot(),
                       "model_domain": {"in_domain": bool(feat.get("in_domain")),
                                        "why": feat.get("why")}}
                if feat.get("in_domain"):
                    proba = policy.proba(feat["vector"])
                    row["model"] = {"proba": {k: round(v, 4)
                                              for k, v in proba.items()},
                                    "features": feat["named"],
                                    "artifact": os.path.basename(policy.path),
                                    "PREDICTS": "L5's next observed action, "
                                                "not an order"}
                    comp = completion_due(inv, obs)
                    row["completion_rule"] = comp
                    if comp.get("due"):
                        # the pair leg is a risk obligation and takes
                        # precedence over the predicted next fill; what the
                        # model wanted is kept on the row so fidelity stays
                        # measurable
                        intent = dict(comp)
                        intent.update({
                            "propose": True, "policy": pol,
                            "side": "YES" if comp["side_yes"] else "NO",
                            "timing": "POST_NOW",
                            "model_action": max(proba, key=lambda k: proba[k]),
                            "model_proba": {k: round(float(v), 4)
                                            for k, v in proba.items()},
                            "model_confidence": round(
                                float(max(proba.values())), 4),
                            "ACTION_SOURCE": "COMPLETION_RULE_NOT_MODEL",
                            "TRANSLATION_IS_OURS": True})
                        row["intended_order"] = intent
                        origin = "COMPLETION_RULE"
                    else:
                        intent = intended_order(pol, proba, inv, obs, clock)
                        row["intended_order"] = intent
                        origin = "CLONE_%s" % intent.get("model_action")
                else:
                    intent = entry_rule(obs, clock)
                    intent["policy"] = pol
                    intent["model_action"] = None
                    intent["TRANSLATION_IS_OURS"] = True
                    row["intended_order"] = intent
                    row["model"] = None
                    origin = "ENTRY_RULE_NOT_MODEL"
                if intent.get("propose"):
                    cand = open_candidate(
                        policy=pol, slug=slug,
                        side_yes=bool(intent["side_yes"]),
                        px=D(str(intent["px"])), qty=D(str(intent["qty"])),
                        t_post=now, obs=obs, clock=clock, inv=inv,
                        model=row.get("model"), origin=origin)
                    # ONE OPEN CANDIDATE PER SIDE, not per market. The
                    # per-market version is why no pair was ever completed
                    # in 8.5 hours of shadow: a first leg stays PENDING for
                    # FOLLOW_S (300 s), which is a third of a 15-minute
                    # window, and the COMPLEMENT is a quote in the SAME
                    # market -- so it was suppressed every time, and the
                    # pair economics the whole thesis rests on could never
                    # be measured. The live one-outstanding-order discipline
                    # is a money constraint on the executor, not a
                    # constraint on observing the other side of a pair.
                    if not any(c.get("slug") == slug
                               and bool(c.get("side_yes")) == bool(
                                   intent["side_yes"])
                               and c.get("status") == PENDING
                               for c in self.candidates[pol].values()):
                        self.candidates[pol][cand["id"]] = cand
                        row["candidate_id"] = cand["id"]
                    else:
                        row["candidate_suppressed"] = \
                            "ONE_OPEN_CANDIDATE_PER_SIDE_PER_MARKET_PER_ARM"
                out.append(row)
        return out

    def rank_watch(self, universe: Sequence[Mapping[str, Any]]
                   ) -> List[dict]:
        """Slots go to TRADABLE markets, nearest expiry breaking ties.

        Expiry alone was the wrong ordering. The watched set filled with
        far-out-of-the-money hourly strikes -- measured live:
        `cpc-btc-above-hr-...-90800` served zero bid levels and a single
        1-cent offer -- so 8 or 9 of every 10 decisions refused with
        BOOK_NOT_TWO_SIDED and nothing could be proposed at all. A market
        nobody bids in is not an opportunity whatever its expiry.

        Order: markets whose book reads, then markets seen two-sided, then
        nearest expiry. A market whose book has failed three times running
        or that is currently one-sided sinks rather than holding a slot.
        """
        held = {s for pol in self.policies
                for s, inv in self.inv[pol].items()
                if inv.n_fills and s not in self.settled}

        def key(r):
            slug = r["slug"]
            # A MARKET WE HOLD INVENTORY IN IS NOT NEGOTIABLE. It can only
            # be completed before it expires, and tradability ranking had
            # begun rotating held markets OUT of the watch set -- so the
            # residual sat there until the window closed and settled
            # directionally, which is the opposite of the strategy.
            if slug in held:
                return (-1, 0, 0, r["clock"]["tte_s"])
            fails = min(3, self._poll_fail.get(slug, 0))
            one_sided = 1 if slug in self.one_sided else 0
            dq = self.obs.get(slug)
            never_seen = 0 if dq else 1
            return (fails, one_sided, never_seen, r["clock"]["tte_s"])
        return sorted(universe, key=key)

    def new_candidate_rows(self) -> List[dict]:
        """Candidates whose status changed since the last emission."""
        out: List[dict] = []
        for pol in self.policies:
            for cid, c in self.candidates[pol].items():
                st = c.get("status")
                if st == PENDING:
                    continue
                key = (cid, st)
                if key in self._rows_seen:
                    continue
                self._rows_seen.add(key)
                out.append(c)
        return out

    # ---- judgement ------------------------------------------------------
    def advance(self) -> dict:
        now = self._now()
        tally: Dict[str, Dict[str, int]] = {}
        newly_cleared: List[dict] = []
        for pol in self.policies:
            t: Dict[str, int] = {}
            for cid, cand in list(self.candidates[pol].items()):
                obs = list(self.obs.get(cand["slug"]) or [])
                before = cand.get("status")
                advance_candidate(cand, obs, now)
                if (before != QUEUE_CLEARED
                        and cand.get("status") == QUEUE_CLEARED):
                    qty = D(str(cand.get("virtual_fill_qty") or 0))
                    if qty > 0:
                        self.inv[pol][cand["slug"]].add(
                            side_yes=bool(cand["side_yes"]),
                            px=D(str(cand["px"])), qty=qty,
                            t=float(cand.get("t_cleared") or now))
                        cand["inventory_after"] = \
                            self.inv[pol][cand["slug"]].snapshot()
                        newly_cleared.append(cand)
                t[cand.get("status") or "?"] = t.get(
                    cand.get("status") or "?", 0) + 1
            tally[pol] = t
        return {"by_status": tally, "newly_queue_cleared": newly_cleared}

    # ---- settlement -----------------------------------------------------
    def settle_expired(self, watched: Sequence[Mapping[str, Any]],
                       now: float) -> List[dict]:
        """Resolve markets we hold virtual inventory in, once expired.

        This is the stage that makes the shadow END TO END. Without it the
        service could only ever report opportunities -- pair costs that
        were available -- and never what the sequence was worth once the
        market resolved, which is the only question that decides whether a
        live probe is justified.

        Bounded: one resolution GET per market, once, and only for markets
        that are past their window and carry inventory in some arm.
        """
        rows: List[dict] = []
        held = set()
        for pol in self.policies:
            for slug, inv in self.inv[pol].items():
                if inv.n_fills:
                    held.add(slug)
        for slug in sorted(held):
            if slug in self.settled:
                continue
            clock = self.clocks.get(slug) or {}
            if clock.get("resolved") and not clock.get("expired"):
                continue
            try:
                md = self._get("/v1/markets/%s" % slug) or {}
            except Exception as exc:                           # noqa: BLE001
                continue
            market = md.get("market") if isinstance(md.get("market"),
                                                    Mapping) else md
            outcome = resolved_outcome(market)
            if not outcome.get("resolved"):
                # not yet resolved is not a settlement; try again later
                continue
            for pol in self.policies:
                inv = self.inv[pol].get(slug)
                if inv is None or not inv.n_fills:
                    continue
                row = {"t_utc": _utc(now), "policy": pol, "slug": slug,
                       "cycle": self.cycles,
                       "inventory_at_settlement": inv.snapshot(),
                       **settle_inventory(inv, outcome)}
                rows.append(row)
            self.settled[slug] = {"t_utc": _utc(now),
                                  "yes_won": outcome.get("yes_won"),
                                  "outcome_prices":
                                      outcome.get("outcome_prices")}
        return rows

    def settlement_totals(self) -> dict:
        """Cumulative shadow settlement economics per arm, from the tape.

        Read back from the durable settlement rows rather than kept as a
        counter, so a restart cannot silently reset the only economic
        bottom line this service produces.
        """
        out: Dict[str, Any] = {"LABEL": "SHADOW_SETTLED_NOT_REALIZED",
                               "arms": {}}
        try:
            files = [f for f in os.listdir(self.state_dir)
                     if f.startswith("settlements-")]
        except Exception:                                      # noqa: BLE001
            files = []
        rows: List[dict] = []
        for f in sorted(files):
            try:
                with open(self._path(f)) as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            rows.append(json.loads(line))
            except Exception:                                  # noqa: BLE001
                continue
        for pol in self.policies:
            mine = [r for r in rows if r.get("policy") == pol
                    and r.get("settled")]
            def _sum(k):
                t = D("0")
                for r in mine:
                    try:
                        t += D(str(r.get(k) or 0))
                    except (InvalidOperation, ValueError, ArithmeticError):
                        continue
                return t
            pair = _sum("matched_pair_pnl_usd")
            resid = _sum("residual_pnl_usd")
            pair_costs = [float(r["matched_pair_cost"]) for r in mine
                          if r.get("matched_pair_cost") is not None]
            out["arms"][pol] = {
                "markets_settled": len(mine),
                "matched_pair_pnl_usd": str(pair),
                "residual_pnl_usd": str(resid),
                "net_usd": str(pair + resid),
                "fees_usd": "0",
                "matched_pair_cost": {
                    "n": len(pair_costs),
                    "mean": (round(float(np.mean(pair_costs)), 4)
                             if pair_costs else None),
                    "frac_at_or_below_ceiling": (
                        round(float(np.mean([c <= float(PAIR_CEILING)
                                             for c in pair_costs])), 4)
                        if pair_costs else None)},
                "EXCLUDES": ["maker rebates", "liquidity rewards",
                             "taker fees (no taker fill was simulated)"],
                "NOT_REALIZED": True,
            }
        return out

    # ---- comparison -----------------------------------------------------
    def compare(self) -> dict:
        """Early vs late on the five dimensions the owner asked for.

        Every figure is labelled SHADOW. None of it is realized profit, and
        the expectancy line counts only candidates whose queue was proven
        cleared by volume -- the most pessimistic set available.
        """
        out: Dict[str, Any] = {"LABEL": "SHADOW_NOT_REALIZED",
                               "arms": {}}
        for pol in self.policies:
            cands = list(self.candidates[pol].values())
            cleared = [c for c in cands if c.get("status") == QUEUE_CLEARED]
            invs = list(self.inv[pol].values())
            pair_costs = []
            for i in invs:
                if i.yes > 0 and i.no > 0:
                    a, b = i.avg(True), i.avg(False)
                    if a is not None and b is not None:
                        pair_costs.append(float(a + b))
            adverse = []
            for c in cands:
                mk = (c.get("markouts") or {}).get("t+30s") or {}
                v = mk.get("adverse_selection_per_share")
                if isinstance(v, (int, float)):
                    adverse.append(float(v))
            comp_avail = [bool(((c.get("markouts") or {}).get("t+30s")
                                or {}).get("complement_available"))
                          for c in cands if (c.get("markouts") or {}).get("t+30s")]
            completable = [bool(((c.get("markouts") or {}).get("t+30s") or {})
                                .get("completable_at_or_below_ceiling_maker"))
                           for c in cands if (c.get("markouts") or {}).get("t+30s")]
            # Imbalance is a statement about markets we HOLD something in.
            # Averaging in every watched market with no virtual fill pulls
            # it toward zero and reads as balance rather than absence.
            held = [i for i in invs if i.n_fills]
            residuals = [float(i.residual) for i in held]
            out["arms"][pol] = {
                "candidates_opened": len(cands),
                "actions_per_market": (round(len(cands) / len(invs), 3)
                                       if invs else None),
                "status_mix": _mix([c.get("status") for c in cands]),
                "origin_mix": _mix([c.get("origin") for c in cands]),
                "queue_cleared": len(cleared),
                "queue_cleared_rate": (round(len(cleared) / len(cands), 4)
                                       if cands else None),
                "virtual_fill_shares": round(sum(
                    float(c.get("virtual_fill_qty") or 0) for c in cleared), 3),
                "markets_with_virtual_inventory": sum(
                    1 for i in invs if i.n_fills),
                "inventory_imbalance": {
                    "basis": "markets with at least one virtual fill",
                    "n_markets": len(held),
                    "mean_abs_residual": (round(float(np.mean(
                        [abs(r) for r in residuals])), 4) if residuals else None),
                    "max_abs_residual": (round(max(abs(r) for r in residuals), 4)
                                         if residuals else None),
                    "two_sided_markets": sum(
                        1 for i in held if i.yes > 0 and i.no > 0)},
                "matched_pair_cost": {
                    "n": len(pair_costs),
                    "mean": (round(float(np.mean(pair_costs)), 4)
                             if pair_costs else None),
                    "median": (round(float(np.median(pair_costs)), 4)
                               if pair_costs else None),
                    "frac_at_or_below_ceiling": (
                        round(float(np.mean([p <= float(PAIR_CEILING)
                                             for p in pair_costs])), 4)
                        if pair_costs else None)},
                "adverse_selection_t30": {
                    "n": len(adverse),
                    "mean_per_share": (round(float(np.mean(adverse)), 5)
                                       if adverse else None),
                    "frac_adverse": (round(float(np.mean(
                        [a < 0 for a in adverse])), 4) if adverse else None)},
                "complement_availability_t30": {
                    "n": len(comp_avail),
                    "frac_available": (round(float(np.mean(comp_avail)), 4)
                                       if comp_avail else None),
                    "frac_completable_at_ceiling_maker": (
                        round(float(np.mean(completable)), 4)
                        if completable else None)},
                "defensibly_executable_expectancy": _expectancy(cleared),
            }
        return out


def _mix(vals: Sequence[Any]) -> dict:
    out: Dict[str, int] = {}
    for v in vals:
        k = str(v)
        out[k] = out.get(k, 0) + 1
    return out


def _expectancy(cleared: Sequence[Mapping[str, Any]]) -> dict:
    """Per-share economics of the ONLY candidates with a defensible fill.

    Three components, kept apart: the pair economics of candidates whose
    complement was completable at the ceiling 30s later, the adverse
    selection on the rest, and the count that is still undecided. A single
    net number over a mixed set would hide which of the two is carrying it.
    No rebate, no reward, no mark-to-market, no settlement is included --
    this is an edge estimate on hypothetical fills, not profit.
    """
    pair_edges: List[float] = []
    bare_adverse: List[float] = []
    undecided = 0
    for c in cleared:
        mk = (c.get("markouts") or {}).get("t+30s") or {}
        if not mk:
            undecided += 1
            continue
        pc = mk.get("pair_cost_maker")
        if mk.get("complement_available") and pc is not None:
            pair_edges.append(1.0 - float(pc))
        a = mk.get("adverse_selection_per_share")
        if isinstance(a, (int, float)):
            bare_adverse.append(float(a))
    return {
        "basis": "queue-cleared candidates only (WOULD_FILL_QUEUE_CLEARED)",
        "n_queue_cleared": len(cleared),
        "n_undecided_no_markout_yet": undecided,
        "pair_edge_per_share_if_completed_maker": {
            "n": len(pair_edges),
            "mean": (round(float(np.mean(pair_edges)), 5)
                     if pair_edges else None),
            "note": "1 - (first leg + complement maker price); a COMPLETION "
                    "WAS NOT ATTEMPTED, so this is an opportunity, not an "
                    "outcome"},
        "adverse_selection_per_share_t30": {
            "n": len(bare_adverse),
            "mean": (round(float(np.mean(bare_adverse)), 5)
                     if bare_adverse else None)},
        "EXCLUDES": ["maker rebates", "liquidity rewards", "settlement",
                     "mark-to-market", "taker rescue"],
        "LABEL": "SHADOW_NOT_REALIZED",
    }


# ------------------------------------------------------------------ loop ---
def run(cycles: Optional[int] = None, *, get_json=None,
        sleep=time.sleep, now=time.time) -> int:
    """Continuous. Runs forever by default; a service that exits reads as a
    crash, whose repair is a rollback to something older."""
    cs = CloneShadow(get_json=get_json, sleep=sleep, now=now)
    print(PREFIX + json.dumps({
        "kind": "boot",
        "policies": {k: {"artifact": os.path.basename(p.path),
                         "n_fit": p.n_fit} for k, p in cs.policies.items()},
        "primary_economic_candidate": "early",
        "behavioral_comparison": "late",
        "read_only": True, "credentials_held": False,
        "order_path_imported": False,
        "state_dir": cs.state_dir,
        "resumed_cycles": cs.cycles}, default=str), flush=True)
    universe: List[dict] = []
    n = 0
    while cycles is None or n < cycles:
        n += 1
        cs.cycles += 1
        try:
            if n == 1 or not universe or n % DISCOVERY_EVERY == 0:
                markets = discover(cs._get, sleep=sleep)
                universe = eligible_markets(markets, now())
                disc = {"kind": "discovery", "cycle": cs.cycles,
                        "markets_served": len(markets),
                        "clock_resolvable_and_live": len(universe),
                        "categories": _mix([r.get("category")
                                            for r in universe])}
                if not universe and markets:
                    disc["why_nothing_resolved"] = clock_diagnostic(
                        markets, now())
                print(PREFIX + json.dumps(disc, default=str), flush=True)
            watch = cs.rank_watch(universe)[:cs._slots]
            polled = cs.poll(watch)
            decisions = cs.decide([r["slug"] for r in watch])
            judged = cs.advance()
            cs.append_rows("decisions", decisions)
            cs.append_rows("observations", [
                {"t_utc": _utc(now()), "cycle": cs.cycles,
                 "rows": [list(cs.obs[r["slug"]])[-1]
                          for r in watch if cs.obs.get(r["slug"])]}])
            if judged["newly_queue_cleared"]:
                cs.append_rows("virtual_fills", judged["newly_queue_cleared"])
            settlements = cs.settle_expired(watch, now())
            if settlements:
                cs.append_rows("settlements", settlements)
            cs.append_rows("candidates", cs.new_candidate_rows())
            saved = cs.save()
            rep = {"kind": "cycle", "cycle": cs.cycles,
                   "t_utc": _utc(now()),
                   "watched": polled, "universe": len(universe),
                   "poll_failed": len(watch) - polled,
                   "slugs_with_failing_books": len(cs._poll_fail),
                   "decisions": len(decisions),
                   "in_domain": sum(1 for d in decisions
                                    if d["model_domain"]["in_domain"]),
                   "proposed": sum(1 for d in decisions
                                   if (d.get("intended_order")
                                       or {}).get("propose")),
                   # WHY NOTHING WAS PROPOSED. The first live run reported
                   # `proposed: 0` with no way to tell whether the gate, the
                   # book or the clock was responsible, and the answer was a
                   # gate that could not fire on this venue's spreads.
                   "refusal_reasons": _mix([
                       (d.get("intended_order") or {}).get("why")
                       for d in decisions
                       if not (d.get("intended_order") or {}).get("propose")]),
                   "out_of_domain_reasons": _mix([
                       (d.get("model_domain") or {}).get("why")
                       for d in decisions
                       if not (d.get("model_domain") or {}).get("in_domain")]),
                   "judged": judged["by_status"],
                   "newly_queue_cleared": len(judged["newly_queue_cleared"]),
                   "settled_this_cycle": len(settlements),
                   "one_sided_books": len(cs.one_sided),
                   "markets_held_unsettled": len({
                       s for pol in cs.policies
                       for s, inv in cs.inv[pol].items()
                       if inv.n_fills and s not in cs.settled}),
                   "completion_rule": _mix([
                       (d.get("completion_rule") or {}).get("why")
                       or ("DUE" if (d.get("completion_rule") or {}).get("due")
                           else None)
                       for d in decisions if d.get("completion_rule")]),
                   "rows_emitted_total": cs.rows_emitted,
                   "state_saved": saved}
            if cs.cycles % 5 == 0 or judged["newly_queue_cleared"]:
                rep["comparison"] = cs.compare()
                rep["settlement_totals"] = cs.settlement_totals()
                # one example of a one-sided book, so the single biggest
                # refusal reason stays diagnosable rather than just counted
                if cs.one_sided:
                    k = sorted(cs.one_sided)[0]
                    rep["one_sided_example"] = {k: cs.one_sided[k]}
            print(PREFIX + json.dumps(rep, separators=(",", ":"),
                                      default=str), flush=True)
        except Exception as exc:                               # noqa: BLE001
            print(PREFIX + json.dumps(
                {"kind": "cycle_error", "cycle": cs.cycles,
                 "error": "%s:%s" % (type(exc).__name__, str(exc)[:200])}),
                flush=True)
        if sleep:
            sleep(CYCLE_S)
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(run())
