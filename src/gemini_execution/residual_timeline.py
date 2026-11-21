"""GEMINI_RESIDUAL_TIMELINE_V1 -- when residual accumulated, and what the
tape says could have been done about it.

WHY THIS EXISTS. COHORT-2 settled at matched +$3.04 / residual -$8.00 /
net -$4.96. `residual_analysis` proves that decomposition on the TERMINAL
position, and a terminal number cannot say whether the residual was
avoidable. The strategy question is not "was the residual expensive" -- it
was -- but:

    at the moment the residual existed, was there a complementary maker
    fill available, at what price, and would the pair have been cheap?

That question is answerable from artifacts already on disk, because every
window artifact carries a per-cycle `book_state` (inventory, YES/NO touch,
depth) AND a per-cycle sample of the venue's PUBLIC TRADE TAPE.

THE LADDER, and it is confirmed by our own fill record rather than assumed.
The venue publishes ONE contract ladder (the UP contract) and mirrors it:

    NO bid = 1 - YES ask        NO ask = 1 - YES bid

`book_state.presented.complement_identity` reports the deviation of that
mirror and it reads 0.00 on every cycle of every COHORT-2 window. The
public tape `/v1/trades/{symbol}` prints in UP price terms with an AGGRESSOR
side. So our own maker fills appear on the public tape as the OPPOSITE side:

    our YES buy at 0.46  ->  public print  0.46 SELL   (a taker sold to us)
    our NO  buy at 0.44  ->  public print  0.56 BUY    (a taker bought from us)

All 56 COHORT-2 fills appear on the reconstructed tape at exactly those
prices, which is what licenses the fill model below. `calibrate` re-proves
it rather than citing this docstring.

THE FILL MODEL, stated as a rule about PRICE PRIORITY and not about hope:

    a resting BUY YES at q fills when a SELL-aggressor print occurs at <= q
    a resting BUY NO  at p fills when a BUY-aggressor  print occurs at >= 1-p

    STRICT (`through`)   the print is strictly past our level. Under price
                         priority a taker cannot reach a worse price while
                         our better-priced order rests, so our order was
                         filled first. This is the tier conclusions use.
    AT    (`at_level`)   the print is exactly at our level. Queue position
                         decides, and queue position is NOT observable here.
                         Reported, never assumed.
    TOUCH (`quote`)      the quote reached our level with no print at all.
                         The weakest tier; reported for contrast only.

A print that IS one of our own fills is EXCLUDED as evidence: it cannot
also have filled a second, hypothetical order. `_ours` does that by tid.

NEVER ASSUME A COMPLEMENT FILLED MERELY BECAUSE ITS PRICE TRADED THROUGH is
the operating instruction, so no tier is collapsed into "it would have
filled". Every counterfactual here is labelled with the tier that produced
it and the tiers are reported side by side.

THE COUNTERFACTUAL ARITHMETIC is an identity, not a model. Buying the r
residual units' complement at price p converts r directional units into r
matched pairs, and a matched pair pays exactly $1:

    delta_net = r x (1 - p - [dominant leg won])

so completion costs p per unit and buys back an exposure worth $1 if the
dominant leg lost and $0 if it won. `effective_pair_cost = avg_dominant + p`
is the number the operating model actually cares about: below $1 the
completed pair is locked-profitable whatever the outcome.

COVERAGE IS MEASURED, NOT PRESUMED. The tape is POLLED: each read returns
the venue's most recent prints, so a burst between two reads can be lost.
`tape()` reports prints recovered, read count, the per-read maximum, and
whether any read came back at that maximum (the truncation signal). A
missed print can only make the counterfactual CONSERVATIVE -- it removes
evidence of an opportunity, never invents one.

NOTHING HERE IS REALIZED, and nothing here is a recommendation. It is a
pure, offline, read-only function over artifacts already on disk.
"""
from __future__ import annotations

from decimal import Decimal as D
from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_RESIDUAL_TIMELINE_V1"

TICK = D("0.01")

#: evidence tiers, strongest first. Conclusions use THROUGH.
THROUGH, AT_LEVEL, QUOTE = "through", "at_level", "quote"
TIERS = (THROUGH, AT_LEVEL, QUOTE)

YES, NO = "YES", "NO"

UNREALIZED = ("settlement-IMPLIED and COUNTERFACTUAL throughout. REALIZED "
              "requires an attributable cash movement and no counterfactual "
              "produces one.")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _f(v: Any) -> Optional[str]:
    """Format Decimal or exact Fraction money to a stable 6-place string."""
    if v is None:
        return None
    if isinstance(v, F):
        v = D(v.numerator) / D(v.denominator)
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _fr(v: Any) -> Optional[F]:
    try:
        return F(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _int(v: Any) -> Optional[int]:
    try:
        return int(D(str(v)))
    except Exception:                                          # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# the tape
# --------------------------------------------------------------------------
def tape(rep: Mapping[str, Any]) -> Dict[str, Any]:
    """Reconstruct the window's public trade tape from the per-cycle samples.

    Deduped by `tid`, ordered by (utc_ms, tid). Coverage is reported because
    the source is polled and a burst between reads is unrecoverable.
    """
    prints: Dict[int, Dict[str, Any]] = {}
    reads = 0
    readable = 0
    per_read: List[int] = []
    for dec in rep.get("decisions") or []:
        flow = ((dec.get("book_state") or {}).get("flow")) or {}
        if not flow:
            continue
        reads += 1
        if flow.get("readable"):
            readable += 1
        rows = flow.get("prints") or []
        per_read.append(len(rows))
        for row in rows:
            tid = _int(row.get("tid"))
            px = _d(row.get("price"))
            qty = _d(row.get("qty"))
            side = str(row.get("side") or "").lower()
            ms = _int(row.get("utc_ms"))
            if tid is None or px is None or ms is None or side not in ("buy", "sell"):
                continue
            prints[tid] = {"tid": tid, "utc_ms": ms, "price": px,
                           "qty": qty, "side": side}
    ordered = sorted(prints.values(), key=lambda p: (p["utc_ms"], p["tid"]))
    cap = max(per_read) if per_read else 0
    at_cap = sum(1 for n in per_read if n == cap and cap > 0)
    return {
        "label": LABEL,
        "prints": ordered,
        "n_prints": len(ordered),
        "reads": reads,
        "reads_readable": readable,
        "max_prints_per_read": cap,
        "reads_at_max": at_cap,
        "truncation_possible": bool(cap and at_cap > 1),
        "source": "GET /v1/trades/{symbol} (PUBLIC), sampled once per cycle",
        "note": ("a POLLED sample. Missing prints remove evidence of an "
                 "opportunity; they cannot invent one."),
    }


def _ours(rep: Mapping[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Our own fills by tid -- excluded from counterfactual evidence."""
    out: Dict[int, Dict[str, Any]] = {}
    for t in ((rep.get("after") or {}).get("trades") or []):
        tid = _int(t.get("tid"))
        if tid is not None:
            out[tid] = dict(t)
    return out


def our_orders(rep: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Our resting orders with their rest interval, from `after.orders_seen`.

    `createdAt` is when the order began resting and `updatedAt` is when it
    reached its terminal state. For a filled maker order that pair is the
    exact interval during which it sat in the book at a known price.
    """
    rows: List[Dict[str, Any]] = []
    by_oid = {}
    for t in ((rep.get("after") or {}).get("trades") or []):
        oid = str(t.get("order_id") or "")
        if oid:
            by_oid.setdefault(oid, []).append(t)
    for o in ((rep.get("after") or {}).get("orders_seen") or []):
        oid = str(o.get("orderId") or "")
        px = _d(o.get("price"))
        filled = _d(o.get("filledQuantity")) or D(0)
        leg = YES if str(o.get("outcome") or "").lower() == "yes" else NO
        trades = by_oid.get(oid) or []
        rows.append({
            "order_id": oid,
            "client_order_id": o.get("clientOrderId"),
            "leg": leg,
            "price": px,
            "qty": _d(o.get("quantity")),
            "filled_qty": filled,
            "status": o.get("status"),
            "time_in_force": o.get("timeInForce"),
            "created_at": o.get("createdAt"),
            "updated_at": o.get("updatedAt"),
            "fill_ms": min((_int(t.get("timestampms")) for t in trades),
                           default=None),
            "tids": sorted(_int(t.get("tid")) for t in trades
                           if _int(t.get("tid")) is not None),
        })
    return rows


def _iso_ms(v: Any) -> Optional[int]:
    """'2026-09-22T12:45:39.036Z' -> epoch milliseconds."""
    if not v:
        return None
    import datetime as _dt
    s = str(v).replace("Z", "+00:00")
    try:
        return int(_dt.datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:                                          # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# fill model, and its calibration against our own fills
# --------------------------------------------------------------------------
def qualifies(leg: str, price: D, pr: Mapping[str, Any]) -> Optional[str]:
    """Tier at which public print `pr` would fill a resting BUY of `leg`@`price`.

    A YES buy at q is a bid at q: a SELL aggressor at <= q hits it.
    A NO buy at p is an ASK at 1-p: a BUY aggressor at >= 1-p lifts it.
    """
    px = pr.get("price")
    side = pr.get("side")
    if px is None or side not in ("buy", "sell"):
        return None
    if leg == YES:
        if side != "sell":
            return None
        if px < price:
            return THROUGH
        return AT_LEVEL if px == price else None
    ask = D(1) - price
    if side != "buy":
        return None
    if px > ask:
        return THROUGH
    return AT_LEVEL if px == ask else None


def calibrate(reps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Re-prove the ladder/tape mapping against our OWN confirmed fills.

    For every filled maker order, its own print must appear on the tape on
    the aggressor side and at the mirrored price the model predicts. A model
    that cannot reproduce fills we KNOW happened may not be used to assert
    fills that did not.
    """
    checked = 0
    on_tape = 0
    side_ok = 0
    price_ok = 0
    misses: List[Dict[str, Any]] = []
    for rep in reps:
        tp = {p["tid"]: p for p in tape(rep)["prints"]}
        for o in our_orders(rep):
            if not o["tids"] or o["price"] is None:
                continue
            for tid in o["tids"]:
                checked += 1
                pr = tp.get(tid)
                if pr is None:
                    misses.append({"order_id": o["order_id"], "tid": tid,
                                   "why": "fill not present on sampled tape"})
                    continue
                on_tape += 1
                want_side = "sell" if o["leg"] == YES else "buy"
                want_px = o["price"] if o["leg"] == YES else D(1) - o["price"]
                if pr["side"] == want_side:
                    side_ok += 1
                else:
                    misses.append({"order_id": o["order_id"], "tid": tid,
                                   "why": f"side {pr['side']} != {want_side}"})
                if pr["price"] == want_px:
                    price_ok += 1
                else:
                    misses.append({"order_id": o["order_id"], "tid": tid,
                                   "why": f"price {pr['price']} != {want_px}"})
    return {
        "label": LABEL,
        "fills_checked": checked,
        "present_on_tape": on_tape,
        "aggressor_side_as_modelled": side_ok,
        "mirrored_price_as_modelled": price_ok,
        "misses": misses,
        "verdict": ("CONFIRMED" if checked and side_ok == checked
                    and price_ok == checked and on_tape == checked
                    else "NOT CONFIRMED"),
        "why": ("every confirmed maker fill appears on the public tape as an "
                "aggressor print on the opposite side at the mirrored price"),
    }


# --------------------------------------------------------------------------
# the timeline
# --------------------------------------------------------------------------
def timeline(rep: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Per-cycle matched/residual decomposition with the book beside it."""
    steps: List[Dict[str, Any]] = []
    for dec in rep.get("decisions") or []:
        inv = dec.get("inventory_before_decision") or {}
        y = _int(inv.get("yes_qty")) or 0
        n = _int(inv.get("no_qty")) or 0
        pres = ((dec.get("book_state") or {}).get("presented")) or {}
        yes = pres.get("yes") or {}
        no = pres.get("no") or {}
        m, r = min(y, n), abs(y - n)
        dom = None if y == n else (YES if y > n else NO)
        steps.append({
            "cycle": _int(dec.get("cycle")),
            "utc": dec.get("utc"),
            "utc_ms": _iso_ms(dec.get("utc")),
            "tte_s": _d(dec.get("tte_s")),
            "yes_qty": y, "no_qty": n,
            "matched": m, "residual": r, "dominant": dom,
            "avg_yes": _d(inv.get("avg_yes_cost")),
            "avg_no": _d(inv.get("avg_no_cost")),
            "spend_usd": _d(inv.get("total_spend_usd")),
            "book_readable": bool(pres.get("readable")),
            "yes_bid": _d(yes.get("bid")), "yes_ask": _d(yes.get("ask")),
            "no_bid": _d(no.get("bid")), "no_ask": _d(no.get("ask")),
            "passive_pair_cost": _d(pres.get("passive_pair_cost")),
            "crossing_pair_cost": _d(pres.get("crossing_pair_cost")),
            "complement_identity": pres.get("complement_identity"),
            "working": [{"leg": w.get("leg"), "price": _d(w.get("price"))}
                        for w in (dec.get("working_orders_before") or [])],
            "actions": [{"action": a.get("action"), "leg": a.get("leg"),
                         "price": _d(a.get("price")), "qty": _d(a.get("qty"))}
                        for a in (dec.get("actions") or [])],
        })
    return steps


def terminal_residual_onset(steps: Sequence[Mapping[str, Any]]
                            ) -> Dict[str, Any]:
    """The moment the TERMINAL residual became irreversible.

    Walking backwards from the last cycle, the onset is the earliest cycle
    from which the dominant leg never changed and the residual never returned
    to zero. Before that moment the book was still balanced or leaning the
    other way, so no complementary action taken earlier could have been
    aimed at this residual.
    """
    if not steps:
        return {"onset": None, "why": "no cycles recorded"}
    last = steps[-1]
    r_end, dom_end = last["residual"], last["dominant"]
    if r_end == 0 or dom_end is None:
        return {"onset": None, "terminal_residual": 0, "dominant": None,
                "why": "window ended matched; no terminal residual"}
    idx = len(steps) - 1
    while idx > 0:
        prev = steps[idx - 1]
        if prev["residual"] == 0 or prev["dominant"] != dom_end:
            break
        idx -= 1
    s = steps[idx]
    return {
        "onset": idx,
        "onset_cycle": s["cycle"],
        "onset_utc": s["utc"],
        "onset_utc_ms": s["utc_ms"],
        "onset_tte_s": s["tte_s"],
        "onset_residual": s["residual"],
        "terminal_residual": r_end,
        "dominant": dom_end,
        "cycles_exposed": len(steps) - idx,
        "seconds_exposed": (_f(s["tte_s"] - last["tte_s"])
                            if s["tte_s"] is not None
                            and last["tte_s"] is not None else None),
        "why": ("earliest cycle from which the dominant leg never changed and "
                "the residual never returned to zero"),
    }


def complement_opportunity(rep: Mapping[str, Any],
                           steps: Optional[Sequence[Mapping[str, Any]]] = None
                           ) -> Dict[str, Any]:
    """Cheapest complement price that the tape supports, after residual onset.

    For the complement leg C, a resting maker BUY at price p fills when a
    qualifying aggressor print occurs (see `qualifies`). The CHEAPEST p the
    tape supports at a tier is therefore set by the most extreme qualifying
    print after onset -- and that print must not be one of our own fills.
    """
    steps = list(steps if steps is not None else timeline(rep))
    onset = terminal_residual_onset(steps)
    out: Dict[str, Any] = {"label": LABEL, "onset": onset,
                           "labels": UNREALIZED}
    if onset.get("onset") is None:
        out["verdict"] = "NO_TERMINAL_RESIDUAL"
        return out

    dom = onset["dominant"]
    comp = NO if dom == YES else YES
    t0 = onset["onset_utc_ms"]
    ours = _ours(rep)
    tp = tape(rep)
    after = [p for p in tp["prints"]
             if t0 is not None and p["utc_ms"] >= t0 and p["tid"] not in ours]
    self_excluded = sum(1 for p in tp["prints"]
                        if t0 is not None and p["utc_ms"] >= t0
                        and p["tid"] in ours)

    # cheapest price at each tier
    best: Dict[str, Optional[D]] = {THROUGH: None, AT_LEVEL: None}
    evidence: Dict[str, Any] = {}
    for p in after:
        if comp == YES:
            if p["side"] != "sell":
                continue
            at = p["price"]                      # a bid AT the print fills
            th = p["price"] + TICK               # a bid ABOVE it is through
        else:
            if p["side"] != "buy":
                continue
            at = D(1) - p["price"]
            th = D(1) - p["price"] + TICK
        for tier, price in ((AT_LEVEL, at), (THROUGH, th)):
            if best[tier] is None or price < best[tier]:
                best[tier] = price
                evidence[tier] = {"tid": p["tid"], "utc_ms": p["utc_ms"],
                                  "print_price": _f(p["price"]),
                                  "print_qty": _f(p["qty"]),
                                  "side": p["side"]}

    # cheapest price the QUOTE alone reached after onset (weakest tier)
    quote_best: Optional[D] = None
    for s in steps[onset["onset"]:]:
        q = s["yes_bid"] if comp == YES else s["no_bid"]
        if q is not None and (quote_best is None or q < quote_best):
            quote_best = q

    out.update({
        "complement_leg": comp,
        "dominant_leg": dom,
        "terminal_residual": onset["terminal_residual"],
        "prints_after_onset": len(after),
        "own_prints_excluded": self_excluded,
        "cheapest_complement_price": {
            THROUGH: _f(best[THROUGH]),
            AT_LEVEL: _f(best[AT_LEVEL]),
            QUOTE: _f(quote_best),
        },
        "evidence": evidence,
        "tape_coverage": {k: tp[k] for k in
                          ("n_prints", "reads", "max_prints_per_read",
                           "reads_at_max", "truncation_possible")},
        "verdict": ("OPPORTUNITY" if best[THROUGH] is not None
                    else "NO_QUALIFYING_PRINT"),
    })
    return out


def counterfactual(rep: Mapping[str, Any],
                   *, resolution_side: Optional[str],
                   avg_dominant: Any,
                   tier: str = THROUGH,
                   opportunity: Optional[Mapping[str, Any]] = None
                   ) -> Dict[str, Any]:
    """What completing the terminal residual at the tape-supported price does.

        delta_net = r x (1 - p - [dominant leg won])

    and `effective_pair_cost = avg_dominant + p`. Below $1 the completed pair
    is locked-profitable whatever the outcome -- that, not the outcome, is
    what the operating model asks of a completion.
    """
    opp = dict(opportunity or complement_opportunity(rep))
    row: Dict[str, Any] = {"label": LABEL, "tier": tier, "labels": UNREALIZED}
    if opp.get("verdict") == "NO_TERMINAL_RESIDUAL":
        row["verdict"] = "NO_TERMINAL_RESIDUAL"
        return row
    p = _fr((opp.get("cheapest_complement_price") or {}).get(tier))
    r = opp.get("terminal_residual")
    dom = opp.get("dominant_leg")
    avg = _fr(avg_dominant)
    if p is None or r is None or avg is None:
        row["verdict"] = "UNAVAILABLE"
        row["why"] = "no tape-supported complement price at this tier"
        return row
    won = None
    if resolution_side is not None:
        side = str(resolution_side).strip().lower()
        if side in ("yes", "up", "true"):
            won = YES
        elif side in ("no", "down", "false"):
            won = NO
    if won is None:
        row["verdict"] = "UNRESOLVED"
        # stated, not omitted: an absent key reads as "nobody looked", and
        # an unread resolution is a fact we DID look for.
        row["delta_net_usd"] = None
        row["why"] = "resolution side unreadable; delta_net not computable"
        row["effective_pair_cost"] = _f(avg + p)
        row["complement_price"] = _f(p)
        return row
    dom_won = F(1) if won == dom else F(0)
    delta = F(r) * (F(1) - p - dom_won)
    row.update({
        "complement_price": _f(p),
        "terminal_residual": r,
        "dominant_leg": dom,
        "winning_leg": won,
        "dominant_won": bool(dom_won),
        "avg_dominant_cost": _f(avg),
        "effective_pair_cost": _f(avg + p),
        "pair_locked_profitable": bool(avg + p < 1),
        "delta_net_usd": _f(delta),
        "verdict": "COMPUTED",
        "why": ("completing converts r directional units into r matched pairs; "
                "a matched pair pays exactly $1"),
    })
    return row


# --------------------------------------------------------------------------
# the causal simulator
# --------------------------------------------------------------------------
# `complement_opportunity` answers "was there ever a price the tape supports",
# and it answers it WITH HINDSIGHT: it scans the whole post-onset tape and
# takes the best. That is an upper bound on what any rule can achieve and it
# is not a rule. What follows is CAUSAL: at each cycle a policy may price a
# complement order using only what that cycle knew, the order rests until the
# next cycle, and the tape decides whether it filled. Nothing after cycle i
# is visible to the decision at cycle i.

def _interval_prints(prints: Sequence[Mapping[str, Any]],
                     lo_ms: Optional[int], hi_ms: Optional[int]
                     ) -> List[Mapping[str, Any]]:
    if lo_ms is None:
        return []
    return [p for p in prints
            if p["utc_ms"] > lo_ms and (hi_ms is None or p["utc_ms"] <= hi_ms)]


def join_touch(*, comp: str, step: Mapping[str, Any], improve: int = 0,
               **_: Any) -> Optional[D]:
    """Join the complement's best bid, optionally `improve` ticks inside it.

    Improving is still MAKER: the price stays strictly on our side of the
    complement's own ask, so the order rests rather than crossing. `None`
    when the book is unreadable or improving would cross.
    """
    bid = step["no_bid"] if comp == NO else step["yes_bid"]
    ask = step["no_ask"] if comp == NO else step["yes_ask"]
    if bid is None:
        return None
    p = bid + TICK * improve
    if ask is not None and p >= ask:
        return None
    return p


def pair_target(*, comp: str, step: Mapping[str, Any], avg_dom: Optional[D],
                target: D = D("0.96"), improve: int = 0, **_: Any
                ) -> Optional[D]:
    """Only complete when the resulting PAIR cost would be <= `target`.

    This is the spring discipline written as a policy so it can be measured
    against the alternatives rather than assumed to be the right one.
    """
    if avg_dom is None:
        return None
    want = target - avg_dom
    touch = join_touch(comp=comp, step=step, improve=improve)
    if want <= 0:
        return None
    if touch is None:
        return None
    return want if want <= touch else None


def cost_aware(*, comp: str, step: Mapping[str, Any], avg_dom: Optional[D],
               threshold: D = D("0.50"), improve: int = 0,
               target: D = D("0.96"), **_: Any) -> Optional[D]:
    """Join the touch when the residual is CHEAP; otherwise hold out for a
    cheap pair.

    The threshold is not tuned on COHORT-2. It comes from L5's own history:
    residual carried at a unit cost below roughly $0.50 returns heavily
    negative across 1,007 of his windows, and residual above $0.65 returns
    positive across 874. A cheap residual is a LOSING residual, so its
    completion is worth paying up for and an expensive one is not.
    """
    if avg_dom is not None and avg_dom < threshold:
        return join_touch(comp=comp, step=step, improve=improve)
    return pair_target(comp=comp, step=step, avg_dom=avg_dom,
                       target=target, improve=improve)


POLICIES = {"join_touch": join_touch, "pair_target": pair_target,
            "cost_aware": cost_aware}


def simulate_completion(rep: Mapping[str, Any], *,
                        policy: Any = join_touch,
                        tier: str = THROUGH,
                        action_size: int = 1,
                        max_extra_actions: Optional[int] = None,
                        steps: Optional[Sequence[Mapping[str, Any]]] = None,
                        **policy_kwargs: Any) -> Dict[str, Any]:
    """Walk the window forward and let `policy` try to complete the residual.

    CAUSAL. At cycle i the policy sees cycle i's inventory and book and
    nothing later. The order it prices rests over (t_i, t_{i+1}] and the
    PUBLIC tape decides the fill at the requested evidence `tier`. Our own
    prints are excluded: a print that was our real fill cannot also fill a
    second, hypothetical order.

    NOT MODELLED, and stated rather than buried: queue position, the working
    -order slot this would occupy, and the effect our extra size would have
    had on the counterparties who traded. `tier=THROUGH` is chosen because
    price priority makes it the one tier that does not need queue position.
    """
    steps = list(steps if steps is not None else timeline(rep))
    tp = tape(rep)
    ours = _ours(rep)
    prints = [p for p in tp["prints"] if p["tid"] not in ours]

    fills: List[Dict[str, Any]] = []
    # A MUTATION IS A PRICE CHANGE, not a cycle. The controller keeps an
    # order resting across cycles and only sends a mutation when the price it
    # wants differs from the price already working. Counting cycles instead
    # would invent hundreds of actions against a 12-slot ledger and make every
    # policy here look unaffordable for a reason that is not real.
    mutations = 0
    resting: Optional[D] = None
    considered = 0
    no_price = 0
    for i, step in enumerate(steps):
        r = step["residual"]
        dom = step["dominant"]
        if not r or dom is None:
            continue
        # every hypothetical fill so far has already reduced the residual
        r -= len(fills) * action_size
        if r <= 0:
            continue
        considered += 1
        comp = NO if dom == YES else YES
        avg_dom = step["avg_yes"] if dom == YES else step["avg_no"]
        price = policy(comp=comp, step=step, avg_dom=avg_dom, **policy_kwargs)
        if price is None:
            no_price += 1
            continue
        if price != resting:
            if (max_extra_actions is not None
                    and mutations >= max_extra_actions):
                # budget spent: the order already working stays where it is
                price = resting
                if price is None:
                    continue
            else:
                mutations += 1
                resting = price
        hi = steps[i + 1]["utc_ms"] if i + 1 < len(steps) else None
        hit = None
        for pr in _interval_prints(prints, step["utc_ms"], hi):
            got = qualifies(comp, price, pr)
            if got == THROUGH or (tier == AT_LEVEL and got == AT_LEVEL):
                hit = pr
                break
        if hit is None:
            continue
        fills.append({"cycle": step["cycle"], "utc": step["utc"],
                      "tte_s": _f(step["tte_s"]), "leg": comp,
                      "price": _f(price), "qty": action_size,
                      "avg_dominant_at_fill": _f(avg_dom),
                      "effective_pair_cost": _f((avg_dom or D(0)) + price),
                      "print_tid": hit["tid"], "print_price": _f(hit["price"]),
                      "print_side": hit["side"]})
        resting = None                     # the working order is now consumed
    return {
        "label": LABEL, "tier": tier, "causal": True,
        "mutations": mutations, "cycles_with_residual": considered,
        "cycles_policy_declined": no_price,
        "completions": fills, "units_completed": len(fills) * action_size,
        "spend_added_usd": _f(sum((_fr(f["price"]) or F(0)) for f in fills)
                              * F(action_size)) if fills else "0",
        "not_modelled": ("queue position, the working-order slot this would "
                         "occupy, and our own size's effect on the tape"),
        "labels": UNREALIZED,
    }


def settle_simulation(sim: Mapping[str, Any], *, resolution_side: Any,
                      dominant: str) -> Dict[str, Any]:
    """delta_net for a simulated completion: each completed unit pays $1."""
    side = str(resolution_side or "").strip().lower()
    won = YES if side in ("yes", "up", "true") else (
        NO if side in ("no", "down", "false") else None)
    if won is None:
        return {"verdict": "UNRESOLVED", "delta_net_usd": None}
    dom_won = F(1) if won == dominant else F(0)
    delta = F(0)
    for f in sim.get("completions") or []:
        p = _fr(f["price"]) or F(0)
        delta += F(f["qty"]) * (F(1) - p - dom_won)
    return {"verdict": "COMPUTED", "delta_net_usd": _f(delta),
            "units": sim.get("units_completed"),
            "dominant_won": bool(dom_won),
            "why": "each completed unit converts a directional unit into a "
                   "matched pair, and a matched pair pays exactly $1"}
