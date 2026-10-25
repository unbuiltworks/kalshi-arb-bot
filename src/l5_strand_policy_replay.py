"""Four-policy timestamp-causal replay of the 2026-10-25 eight-window campaign.

OWNER QUESTION. One window (0130z) stranded a 1.87 DOWN leg and lost $1.1594,
which is 90.2% of what all seven completed pairs earned. Forensics showed the
pair was briefly executable by CROSSING at 0.95-0.96 while the engine, which
may only quote two ticks behind the touch, quoted 0.93 and never filled. So:
is selective taker completion worth authorizing, and does it survive the
counterexample of 0145z, which completed as a maker at 0.87 after 5m23s?

WHAT IS AUTHORITATIVE HERE. Every book frame is an `L5_RESIDUAL_BOOK_SAMPLE`
emitted by the live engine while inventory was unmatched: a full ladder from
the authenticated feed, plus a `taker_walk` the engine itself sized to the
exact residual. 290 frames across five windows. Realized fills and settlements
are the venue's. Nothing here is a simulated price.

WHAT IS MODELLED, AND THE DIRECTION OF EACH ASSUMPTION.

  LATENCY. A decision at frame t cannot execute at frame t. It executes at the
    first frame at or after t + lambda, against THAT frame's ladder. lambda is
    swept, including the 8.818 s this very window actually logged.

  THE ORDER IS A LIMIT, NOT A MARKET ORDER. Crossing is modelled as an IOC
    limit at the price that justified the decision. If the book has moved away
    by t + lambda it fills partially or not at all -- it does NOT chase. This
    is the conservative reading and it is also what the venue would do.

  PARTIAL FILLS are carried: whatever does not fill stays residual and is
    settled as such. A policy is never credited with a fill it could not get.

  MAKER COMPLETION is NOT modelled. Where a window actually completed as a
    maker, that realized fill and its realized pair cost are used as-is. The
    replay therefore never has to guess whether a maker order would have
    filled -- it only ever decides whether to pre-empt a fill that is known.

  WHAT THAT BIASES. Pre-empting a KNOWN-good maker completion is scored at its
    true cost, so the replay is HARSH on taker completion in the windows that
    completed. In 0130z, where nothing completed, taker completion is scored
    against the realized total loss. Both directions are the honest ones.

NOT A SOURCE OF AUTHORIZATION. The 0.96 pair ceiling bounds what a pair may
cost. It is not permission to submit a taker order, and nothing here is
deployed, armed, or enabled. READ-ONLY.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

TAKER_THETA = D("0.0695")          # published PMUS taker fee coefficient
TICK = D("0.01")
PAIR_CAP = D("0.96")               # the approved ceiling; a BOUND, not a permit
FRAMES = os.path.join("data", "l5_strand_replay",
                      "residual_books_20261025.jsonl")

#: Realized ground truth. Opening own cost, quantity, and -- where it happened
#: -- the realized maker completion. `complete_t` None means the leg stranded
#: and settled worthless. Prices are OWN cost, not wire.
EPISODES: List[Dict[str, Any]] = [
    {"w": "0030z", "held": "UP", "q": D("1.61"), "own": D("0.55"),
     "open_t": 1792888315.0, "complete_t": 1792888709.0,
     "complete_own": D("0.27"), "expiry_t": 1792888800.0},
    {"w": "0045z", "held": None, "q": D("1.66"), "own": D("0.50"),
     "open_t": 1792889188.0, "complete_t": 1792889188.0,
     "complete_own": D("0.45"), "expiry_t": 1792889700.0},
    {"w": "0100z", "held": "UP", "q": D("2.00"), "own": D("0.20"),
     "open_t": 1792890106.0, "complete_t": 1792890138.0,
     "complete_own": D("0.76"), "expiry_t": 1792890600.0},
    {"w": "0100z", "held": "DOWN", "q": D("0.43"), "own": D("0.75"),
     "open_t": 1792890206.0, "complete_t": 1792890271.0,
     "complete_own": D("0.17"), "expiry_t": 1792890600.0},
    {"w": "0115z", "held": "UP", "q": D("1.78"), "own": D("0.40"),
     "open_t": 1792890988.0, "complete_t": 1792891219.0,
     "complete_own": D("0.53"), "expiry_t": 1792891500.0},
    {"w": "0115z", "held": "UP", "q": D("1.68"), "own": D("0.48"),
     "open_t": 1792891290.0, "complete_t": 1792891426.0,
     "complete_own": D("0.24"), "expiry_t": 1792891500.0},
    {"w": "0130z", "held": "DOWN", "q": D("1.87"), "own": D("0.62"),
     "open_t": 1792891888.0, "complete_t": None,
     "complete_own": None, "expiry_t": 1792892400.0},
    {"w": "0145z", "held": "DOWN", "q": D("1.56"), "own": D("0.35"),
     "open_t": 1792892821.0, "complete_t": 1792893144.0,
     "complete_own": D("0.52"), "expiry_t": 1792893300.0},
]


def taker_fee(qty: D, px: D) -> D:
    """fee = theta * C * p * (1 - p), the published formula, to the cent."""
    return (TAKER_THETA * qty * px * (D(1) - px)).quantize(D("0.01"))


def load_frames(path: str = FRAMES) -> List[Dict[str, Any]]:
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def episode_frames(frames: List[Dict[str, Any]],
                   ep: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Frames belonging to one residual episode.

    Matched on window, held side and the signed residual the engine reported,
    so two episodes in the same window never blend."""
    if ep["held"] is None:
        return []
    want = (ep["q"] if ep["held"] == "UP" else -ep["q"])
    hi = ep["complete_t"] if ep["complete_t"] else ep["expiry_t"]
    sel = []
    for f in frames:
        if not f["slug"].endswith(ep["w"]) or f["held"] != ep["held"]:
            continue
        try:
            if D(str(f["residual"])) != want:
                continue
        except Exception:                                      # noqa: BLE001
            continue
        if ep["open_t"] <= f["t"] <= hi:
            sel.append(f)
    return sorted(sel, key=lambda f: f["t"])


def fillable_at_limit(frame: Mapping[str, Any], held: str,
                      limit_own: D) -> Tuple[D, Optional[D]]:
    """Displayed complement quantity obtainable at an OWN price <= limit.

    Side semantics, verified empirically against the engine's own taker_walk:
    holding DOWN we buy UP and cross the ASK, so own price IS the wire price;
    holding UP we buy DOWN and cross the BID, so own price is 1 - wire.
    Returns (quantity, volume-weighted own price) over the rungs inside the
    limit. Displayed is displayed -- never guaranteed.
    """
    qty, cost = D(0), D(0)
    if held == "DOWN":
        for p, q in frame.get("asks") or []:
            px, qq = D(str(p)), D(str(q))
            if px <= limit_own:
                qty += qq
                cost += px * qq
    else:
        for p, q in frame.get("bids") or []:
            px, qq = D(str(p)), D(str(q))
            own = D(1) - px
            if own <= limit_own:
                qty += qq
                cost += own * qq
    return qty, (cost / qty if qty > 0 else None)


def touch_own(frame: Mapping[str, Any], held: str) -> Optional[D]:
    """Own cost of the complement at the touch, i.e. the best taker price."""
    if held == "DOWN":
        a = frame.get("asks") or []
        return D(str(a[0][0])) if a else None
    b = frame.get("bids") or []
    return (D(1) - D(str(b[0][0]))) if b else None


def walk_for_qty(frame: Mapping[str, Any], held: str, qty: D
                 ) -> Optional[Tuple[D, D]]:
    """Walk the complement side for `qty`. Returns (vwap_own, worst_own).

    THE TOUCH IS NOT THE PRICE. Several frames here do not hold the whole
    residual on the top rung -- the engine's own `taker_walk` reports a vwap
    of 0.513789 against a touch of 0.51 in one of them, because it had to go
    deeper. Pricing a crossing at the touch would credit a fill that is not
    there, so the economics use the vwap and the limit uses the worst rung
    the walk actually needs. Reproduces the engine's figure to 6dp; see
    tests/test_l5_strand_policy_replay.py.
    """
    rungs: List[Tuple[D, D]] = []
    if held == "DOWN":
        for p, q in frame.get("asks") or []:
            rungs.append((D(str(p)), D(str(q))))
    else:
        for p, q in frame.get("bids") or []:
            rungs.append((D(1) - D(str(p)), D(str(q))))
    need, cost, worst = qty, D(0), None
    for own, avail in rungs:
        if need <= 0:
            break
        take = min(need, avail)
        cost += own * take
        need -= take
        worst = own
    if need > 0 or worst is None:
        return None
    return (cost / qty, worst)


def maker_quote_own(frame: Mapping[str, Any], held: str,
                    depth_ticks: int = 2) -> Optional[D]:
    """What the engine WOULD quote: depth_ticks behind the touch, never across.

    This is the alternative that waiting buys, so it is the thing taker
    completion has to beat."""
    t = touch_own(frame, held)
    return None if t is None else t - depth_ticks * TICK


def cross_decision(frame: Mapping[str, Any], ep: Mapping[str, Any],
                   residual: D, horizon_s: Optional[float],
                   p_complete: D, max_row_age_s: float = 0.5
                   ) -> Optional[Dict[str, Any]]:
    """Should we cross at this frame? Returns the order, or None with a reason.

    EVERY GATE MUST PASS, and each exists because something in the forensics
    could otherwise be assumed away:

      1 DEPTH, from the engine's own walk: it sized `taker_walk` to the real
        residual and said whether displayed depth was short.
      2 FRESHNESS: a stale frame is not evidence about the book now.
      3 ECONOMICS: strictly positive AFTER the published taker fee, priced
        on the VWAP of a walk sized to the whole residual -- not the touch.
      4 PAIR CAP: the completed pair must still respect 0.96.
      5 REMAINING TIME: optional horizon, swept.
      6 EV OF WAITING: crossing must beat holding for the maker quote the
        engine would actually place. This is the gate that protects 0145z.
    """
    if frame.get("walk_short") is True:
        return None
    try:
        wq = D(str(frame.get("walk_qty")))
    except Exception:                                          # noqa: BLE001
        return None
    if wq < residual:
        return None
    age = frame.get("row_age_s")
    if age is None or float(age) > max_row_age_s:
        return None
    walk = walk_for_qty(frame, ep["held"], residual)
    if walk is None:
        return None
    a, worst = walk
    pair = ep["own"] + a
    if pair > PAIR_CAP:
        return None
    gross = residual * (D(1) - pair)
    net = gross - taker_fee(residual, a)
    if net <= 0:
        return None
    if horizon_s is not None:
        if (ep["expiry_t"] - frame["t"]) > horizon_s:
            return None
    mq = maker_quote_own(frame, ep["held"])
    if mq is not None:
        ev_wait = (p_complete * residual * (D(1) - (ep["own"] + mq))
                   - (D(1) - p_complete) * residual * ep["own"])
        if net <= ev_wait:
            return None
    return {"t": frame["t"], "limit_own": worst, "vwap_own": a, "pair": pair,
            "net_if_filled_now": net, "ev_wait": ev_wait if mq else None}


def execute(frames: List[Dict[str, Any]], i_decision: int, order: Mapping,
            ep: Mapping[str, Any], residual: D, lam: float
            ) -> Dict[str, Any]:
    """Fill the IOC limit at the first frame at or after decision + lambda.

    It does not chase. If the book has left the limit the order expires
    unfilled and the residual survives, which is the whole point of modelling
    latency rather than asserting it away."""
    t_exec = order["t"] + lam
    for f in frames[i_decision:]:
        if f["t"] < t_exec:
            continue
        got, vwap = fillable_at_limit(f, ep["held"], order["limit_own"])
        filled = min(got, residual)
        if filled <= 0 or vwap is None:
            return {"filled": D(0), "t": f["t"], "why": "BOOK_LEFT_THE_LIMIT"}
        pair = ep["own"] + vwap
        return {"filled": filled, "vwap": vwap, "pair": pair, "t": f["t"],
                "pnl": filled * (D(1) - pair) - taker_fee(filled, vwap),
                "why": "FILLED" if filled == residual else "PARTIAL"}
    return {"filled": D(0), "t": None, "why": "NO_FRAME_AFTER_LATENCY"}


def replay_episode(frames: List[Dict[str, Any]], ep: Mapping[str, Any],
                   lam: float, horizon_s: Optional[float], p_complete: D
                   ) -> Dict[str, Any]:
    """One residual episode under selective taker completion."""
    fs = episode_frames(frames, ep)
    residual = ep["q"]
    crossed: List[Dict[str, Any]] = []
    for i, f in enumerate(fs):
        if residual <= 0:
            break
        order = cross_decision(f, ep, residual, horizon_s, p_complete)
        if order is None:
            continue
        res = execute(fs, i, order, ep, residual, lam)
        crossed.append({"decided_t": order["t"], "limit": order["limit_own"],
                        **res})
        if res["filled"] > 0:
            residual -= res["filled"]
        break            # one completion attempt per episode: bounded by design
    # score what is left exactly as the venue did
    if residual <= 0:
        tail = D(0)
        tail_why = "fully taker-completed"
    elif ep["complete_t"] is not None:
        tail = residual * (D(1) - (ep["own"] + ep["complete_own"]))
        tail_why = "realized maker completion at pair %s" % (
            ep["own"] + ep["complete_own"])
    else:
        tail = -(residual * ep["own"])
        tail_why = "STRANDED, settled worthless"
    taker_pnl = sum((c.get("pnl") or D(0)) for c in crossed)
    return {"window": ep["w"], "held": ep["held"], "q": ep["q"],
            "own": ep["own"], "frames": len(fs), "crossed": crossed,
            "taker_pnl": taker_pnl, "residual_left": residual,
            "tail_pnl": tail, "tail_why": tail_why,
            "total": taker_pnl + tail}


#: The planned complement own price for the episode that never completed.
#: 0130z's UP leg `be31b2bd` was presubmitted at own 0.3300 against a
#: 0.3500/0.3600 book, so the plan was 0.62 + 0.33 = 0.95.
PLANNED_COMPLEMENT_OWN = {"0130z": D("0.33")}


def dearer_leg_own(ep: Mapping[str, Any]) -> D:
    """The leg the cap sizes on: the dearer of the two in the opening plan.

    For a completed pair both own prices are the realized ones. For the
    episode that stranded, the complement's own price is the one its
    presubmit record carries.
    """
    comp = ep.get("complete_own")
    if comp is None:
        comp = PLANNED_COMPLEMENT_OWN.get(ep["w"], D(0))
    return max(ep["own"], comp)


def capped_qty(ep: Mapping[str, Any], budget: Optional[D],
               min_qty: D = D("0.01")) -> Optional[D]:
    """The quantity `build_orders` would emit under a notional budget.

    Mirrors the implementation: bound by budget / dearer, quantised DOWN to
    the venue grid, and refused outright when one grid step already costs
    more than the budget. None means the leg is NOT OPENED.
    """
    if budget is None:
        return ep["q"]
    dear = dearer_leg_own(ep)
    if dear <= 0:
        return ep["q"]
    if min_qty * dear > budget:
        return None
    by_notional = budget / dear
    q = min(ep["q"], by_notional)
    q = (q / min_qty).to_integral_value(rounding="ROUND_FLOOR") * min_qty
    return q if q >= min_qty else None


def capped_episode(ep: Mapping[str, Any], budget: Optional[D]
                   ) -> Dict[str, Any]:
    """Settled outcome of one episode under the cap, scaled from realized.

    THE ONE MODELLING ASSUMPTION, stated plainly: a smaller clip is assumed
    to fill and complete exactly as the realized one did. That is
    CONSERVATIVE for the cap -- a smaller maker clip fills at least as
    easily, never less -- and it is the only assumption, because every price
    here is the realized one.
    """
    q = capped_qty(ep, budget)
    if q is None:
        return {"window": ep["w"], "qty": D(0), "pnl": D(0),
                "residual": D(0), "opened": False,
                "completed": ep["complete_t"] is not None}
    if ep["complete_t"] is None:
        return {"window": ep["w"], "qty": q,
                "pnl": -(q * ep["own"]), "residual": q,
                "opened": True, "completed": False}
    pair = ep["own"] + ep["complete_own"]
    return {"window": ep["w"], "qty": q, "pnl": q * (D(1) - pair),
            "residual": D(0), "opened": True, "completed": True}


def baseline_pnl(ep: Mapping[str, Any]) -> D:
    if ep["complete_t"] is None:
        return -(ep["q"] * ep["own"])
    return ep["q"] * (D(1) - (ep["own"] + ep["complete_own"]))
