"""Compare quoting policies on REALIZED pair cost and SETTLED net P&L.

WHY THIS EXISTS. The 2026-11-06 20:15z live window quoted a 0.96 pair and
realized 0.98748 -- 2.75 cents of legging slippage, leaving 1.25 cents of
gross margin per pair. Fill rate was NOT the problem: 58% at two ticks
behind the touch, and queue depth was shown not to discriminate (fills at
1,946 ahead in 5.9 s, cancels at 65 ahead). The problem is that each leg
fills on an ADVERSE move, so the two legs do not compose into the quoted
pair.

So this optimises settled net P&L per window, not quoted margin and not
fill rate.

THE FILL MODEL, and why it is this one. Every fill in the 20:15z window
landed when the market came to us: each UP fill at own 0.38-0.39 while the
bid stood at 0.40-0.41 when placed, each DOWN fill at own 0.60-0.62 while
the ask was at or below the matching wire. So an order fills when the book
crosses its price, and NOT by queue drainage -- draining 1,946 contracts in
six seconds is not plausible. Queue priority is therefore deliberately not
modelled, and that omission is justified by the measurement rather than by
convenience.

  an UP bid resting at own p   fills when best_bid <= p
  a DOWN order at wire w=1-q   fills when best_ask >= w

Both are the adverse direction, which is the point.

WHAT IS CONSERVATIVE HERE.
  * maker rebates are counted as ZERO. The schedule estimates 0.0029-0.0031
    per order but every `venue_actual` on the live tape is null/PENDING, and
    a zero commission is not a rebate.
  * a taker completion pays the published fee, theta*q*p*(1-p) rounded UP
    to the cent, theta = 0.0695.
  * a residual settles at the window's REAL venue outcome, not at a model.
  * one contract per leg per round, which is what the live grid expressed.

WHAT THIS CANNOT SAY. The recorded frames begin about 115 s into each
window because the 93-second roll stall also blocked the sampler, so the
opening of each window is unobserved. And the samples were taken while
unmatched inventory existed, so they are conditioned on a residual already
having occurred. Both limits bias the sample toward the legging state and
away from clean simultaneous fills; neither is corrected here, and no
figure below is presented as an unconditional expectation.
"""
from __future__ import annotations

import json
from decimal import Decimal as D, ROUND_CEILING
from typing import Any, Dict, List, Mapping, Optional, Sequence

TAKER_THETA = D("0.0695")
TICK = D("0.01")
#: counted as zero until a venue_actual confirms one
MAKER_REBATE_CONFIRMED = D("0")


def taker_fee(qty: Any, own_px: Any) -> D:
    """theta * qty * p * (1-p), rounded UP to the cent, per the schedule."""
    q, p = D(str(qty)), D(str(own_px))
    raw = TAKER_THETA * q * p * (D(1) - p)
    return raw.quantize(D("0.01"), rounding=ROUND_CEILING)


def load_frames(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """Recorded book frames per window slug, oldest first."""
    raw = json.load(open(path))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for s in raw["samples"]:
        r = s.get("rec") if isinstance(s, Mapping) and "rec" in s else s
        row = (r.get("book") or {}).get("row") or {}
        bids, asks = row.get("bids") or [], row.get("asks") or []
        if not bids or not asks:
            continue
        out.setdefault(str(r.get("slug")), []).append({
            "t": float(r["t"]),
            "bid": D(str(bids[0][0])), "ask": D(str(asks[0][0])),
            "bid_sz": D(str(bids[0][1])), "ask_sz": D(str(asks[0][1])),
        })
    for k in out:
        out[k].sort(key=lambda f: f["t"])
    return out


class Policy:
    """A quoting rule: where to rest each leg relative to the touch.

    `depth_ticks` is how far BEHIND the own-terms touch each leg rests.
    `pair_ceiling` refuses a quote pair whose combined own cost exceeds it.
    `complete_residual` allows one bounded taker completion of a residual
    when the improvement floor is met.
    """

    def __init__(self, name: str, depth_ticks: int,
                 pair_ceiling: Any = "0.96",
                 complete_residual: bool = False,
                 min_improvement: Any = "0.25",
                 imbalance_cap: Optional[int] = None) -> None:
        self.name = name
        self.depth = int(depth_ticks)
        self.ceiling = D(str(pair_ceiling))
        self.complete = bool(complete_residual)
        self.min_improvement = D(str(min_improvement))
        #: INVENTORY CONTROL. The largest |up_filled - dn_filled| this
        #: policy will carry before it stops adding to the side that is
        #: ahead. None means uncapped, which is what the live grid does and
        #: is why 0315z accumulated four UP against zero DOWN.
        self.imbalance_cap = (None if imbalance_cap is None
                              else int(imbalance_cap))

    def quotes(self, f: Mapping[str, Any]) -> Optional[Dict[str, D]]:
        """Own-terms prices for (UP, DOWN), or None if the pair is refused."""
        d = TICK * self.depth
        up = f["bid"] - d                      # rest behind the bid
        dn = (D(1) - f["ask"]) - d             # rest behind the DOWN bid
        if up <= 0 or dn <= 0 or up >= 1 or dn >= 1:
            return None
        if up + dn > self.ceiling:
            return None
        return {"UP": up, "DOWN": dn}


def simulate(frames: Sequence[Mapping[str, Any]], pol: Policy, *,
             settled_up: bool, max_pairs: int = 4) -> Dict[str, Any]:
    """Run `pol` over one window's frames. Returns settled economics."""
    up_fills: List[D] = []
    dn_fills: List[D] = []
    resting: Dict[str, Optional[D]] = {"UP": None, "DOWN": None}
    refused = 0
    for f in frames:
        # fills first: an order already resting fills when the book crosses it
        if resting["UP"] is not None and f["bid"] <= resting["UP"]:
            up_fills.append(resting["UP"]); resting["UP"] = None
        if resting["DOWN"] is not None and f["ask"] >= (D(1) - resting["DOWN"]):
            dn_fills.append(resting["DOWN"]); resting["DOWN"] = None
        if len(up_fills) >= max_pairs and len(dn_fills) >= max_pairs:
            break
        q = pol.quotes(f)
        if q is None:
            refused += 1
            continue
        # INVENTORY CONTROL, applied WITHIN the window. The side that is
        # already ahead stops being quoted once the imbalance reaches the
        # cap, so a trending book cannot keep filling one leg.
        imb = len(up_fills) - len(dn_fills)
        cap = pol.imbalance_cap
        may_up = cap is None or imb < cap
        may_dn = cap is None or -imb < cap
        if resting["UP"] is None and len(up_fills) < max_pairs and may_up:
            resting["UP"] = q["UP"]
        elif not may_up:
            resting["UP"] = None               # withdraw the leading side
        if resting["DOWN"] is None and len(dn_fills) < max_pairs and may_dn:
            resting["DOWN"] = q["DOWN"]
        elif not may_dn:
            resting["DOWN"] = None

    matched = min(len(up_fills), len(dn_fills))
    up_c = sum(up_fills[:matched], D(0))
    dn_c = sum(dn_fills[:matched], D(0))
    matched_pnl = D(matched) - (up_c + dn_c)

    # the residual: whichever side over-filled
    res_side = "UP" if len(up_fills) > len(dn_fills) else (
        "DOWN" if len(dn_fills) > len(up_fills) else None)
    res_n = abs(len(up_fills) - len(dn_fills))
    res_cost = D(0)
    if res_side == "UP":
        res_cost = sum(up_fills[matched:matched + res_n], D(0))
    elif res_side == "DOWN":
        res_cost = sum(dn_fills[matched:matched + res_n], D(0))

    fee = D(0)
    completed = 0
    if pol.complete and res_side is not None and res_n > 0 and frames:
        # one bounded taker completion of the residual at the LAST frame
        f = frames[-1]
        want = "DOWN" if res_side == "UP" else "UP"
        px = (D(1) - f["bid"]) if want == "DOWN" else f["ask"]
        if 0 < px < 1:
            improvement = D(1) - px            # worst-case reduction per unit
            if improvement >= pol.min_improvement:
                fee += taker_fee(res_n, px)
                res_cost += px * res_n
                matched += res_n
                completed = res_n
                res_side, res_n = None, 0

    # the residual settles at the venue's REAL outcome
    if res_side is None or res_n == 0:
        res_redeem = D(0)
        res_pnl = D(0)
        matched_pnl = D(matched) - (up_c + dn_c + (
            res_cost if completed else D(0)))
    else:
        won = (res_side == "UP") == bool(settled_up)
        res_redeem = D(res_n) if won else D(0)
        res_pnl = res_redeem - res_cost

    net = matched_pnl + res_pnl - fee + MAKER_REBATE_CONFIRMED * 0
    pair_cost = ((up_c + dn_c) / matched) if matched else None
    return {
        "policy": pol.name,
        "up_fills": len(up_fills), "dn_fills": len(dn_fills),
        "matched": matched, "completed_by_taker": completed,
        "realized_pair_cost": (None if pair_cost is None
                               else pair_cost.quantize(D("0.00001"))),
        "matched_pnl": matched_pnl.quantize(D("0.000001")),
        "residual_side": res_side, "residual_n": res_n,
        "residual_pnl": res_pnl.quantize(D("0.000001")),
        "taker_fee_usd": fee,
        "settled_net_usd": net.quantize(D("0.000001")),
        "pair_refused_frames": refused,
    }


def policies() -> List[Policy]:
    return [
        Policy("P0 current: 2 ticks, ceil 0.96", 2),
        Policy("P1 at touch, ceil 0.96", 0),
        Policy("P2 1 tick, ceil 0.96", 1),
        Policy("P3 3 ticks, ceil 0.96", 3),
        Policy("P4 2 ticks, ceil 0.94", 2, pair_ceiling="0.94"),
        Policy("P5 2 ticks, ceil 0.92", 2, pair_ceiling="0.92"),
        Policy("P6 2 ticks, 0.96 + completion", 2, complete_residual=True),
        Policy("P7 2 ticks, 0.94 + completion", 2, pair_ceiling="0.94",
               complete_residual=True),
        # INVENTORY-AWARE: stop adding to the leg that is already ahead
        Policy("P8 2 ticks, imbalance cap 1", 2, imbalance_cap=1),
        Policy("P9 2 ticks, cap 1 + completion", 2, imbalance_cap=1,
               complete_residual=True),
        Policy("P10 1 tick, imbalance cap 1", 1, imbalance_cap=1),
        Policy("P11 2 ticks, imbalance cap 2", 2, imbalance_cap=2),
    ]
