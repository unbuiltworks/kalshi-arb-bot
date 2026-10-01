"""CASE-1 CALIBRATION: what does the venue do to a genuinely RESTING post-only
maker order that the market later reaches or crosses?

This is the ONE execution-semantic unknown that still bears on V1_EXTEND_ONLY,
the only L5-derived behaviour that survived the A/C/Case-2 repairs
(`analysis/l5/A_VS_CASE2_ATTRIBUTION.md`: +$0.3146/window, spent-window forensic,
bit-identical across the Case-2 repair).

WHAT THIS IS NOT. Not an economic probe. Not a profitability measurement. Not a
V3 rescue. Nothing it returns may be called realized profit, and a fill here is
EVIDENCE, not income. It informs the replay fill model and nothing else.

AUTHORIZED ENVELOPE, verbatim from the owner:
  q = 1; exactly one market and one submission; no repricing, chasing or
  replacement; entry genuinely non-marketable and post-only; entry price cap
  $0.25 or REFUSE rather than widen risk; maximum possible first-leg loss $0.25;
  no second experiment automatically; stop on UNKNOWN terminal, stale feed,
  private-stream failure, unexpected order state or any reconciliation mismatch;
  zero open orders proven before exit.

WHY THE PRICE MUST BE JUST BELOW THE TOUCH. The schema probe deliberately sat 20
ticks away so a fill was impossible. Case 1 needs the OPPOSITE: the order must be
non-marketable when submitted and then plausibly REACHED by natural movement,
with us never moving it. One tick below the own-terms best bid is the nearest
such price, so that is the rule -- and if that price exceeds the $0.25 cap we
REFUSE, because pricing further out to satisfy the cap would buy a near-certain
null, and pricing above the cap would widen risk past the authorization.

ARMING, exactly: `PMUS_Q1_LIVE` must be the literal string `YES`, and
`PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` and `PMUS_CASE1_ARM` must all equal the SAME
session id. `PMUS_Q1_LIVE` is NOT the session id.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import l5_order_body as OB
from src import pmus_q1_executor as X
from src import pmus_private_schema_probe as P

ONE = D("1")

LIVE_ENV = "PMUS_Q1_LIVE"
SESSION_ENV = "PMUS_Q1_SESSION_ID"
ARM_ENV = "PMUS_Q1_ARM"
CASE1_ARM_ENV = "PMUS_CASE1_ARM"

#: the venue grid for btc-updown-15m (documented family tick). A price off this
#: grid is rejected, and an earlier probe generation emitted 0.005 by arithmetic.
PRICE_TICK = D("0.01")
#: THE AUTHORIZED CAP. Entry price, in the side's own terms, may not exceed it.
MAX_ENTRY_PX = D("0.25")
#: and the authorized maximum possible loss on the single unhedged contract.
MAX_LOSS = D("0.25")
#: how far below the own-terms touch we rest: exactly one tick, the nearest
#: non-marketable price, because reachability is the whole point.
ENTRY_OFFSET_TICKS = 1
#: how long we are willing to hold the resting order waiting to be reached
OBSERVE_MAX_S = 420.0
#: and never inside this much of the window's end
WINDOW_TAIL_GUARD_S = 90.0
#: quantity. One contract, which with MAX_ENTRY_PX bounds the loss at MAX_LOSS.
QTY = 1


class Case1Refuse(RuntimeError):
    """Preflight or envelope check failed. Nothing was submitted."""


class Case1Stop(RuntimeError):
    """A mandated stop condition fired mid-observation. Exit via cleanup."""


def armed(session_id: str) -> bool:
    """All four, and `PMUS_Q1_LIVE` is the literal YES, never the session id."""
    return (os.getenv(LIVE_ENV, "").strip().upper() == "YES"
            and bool(session_id)
            and os.getenv(SESSION_ENV, "").strip() == session_id
            and os.getenv(ARM_ENV, "").strip() == session_id
            and os.getenv(CASE1_ARM_ENV, "").strip() == session_id)


def arming_contract() -> str:
    return (f"{LIVE_ENV} must be exactly 'YES', and {SESSION_ENV}, {ARM_ENV} "
            f"and {CASE1_ARM_ENV} must all equal the same session id")


# --------------------------------------------------------- own-terms geometry

def own_bid(side: str, bid: Any, ask: Any) -> Optional[D]:
    """Best price someone is BIDDING for `side`, in that side's own terms."""
    if side == "up":
        return None if bid is None else D(str(bid))
    return None if ask is None else ONE - D(str(ask))


def own_ask(side: str, bid: Any, ask: Any) -> Optional[D]:
    """Best price someone is OFFERING `side` at, in that side's own terms. A
    resting BUY at px is reached/crossed exactly when this falls to <= px."""
    if side == "up":
        return None if ask is None else D(str(ask))
    return None if bid is None else ONE - D(str(bid))


def on_grid(px: D) -> bool:
    return D(str(px)) % PRICE_TICK == 0


def worst_case_loss(px: D, qty: Any = QTY) -> D:
    """A single unhedged long contract that settles worthless loses its cost."""
    return D(str(px)) * D(str(qty))


def choose_entry(bbo: Mapping[str, Any]) -> Tuple[str, D]:
    """Pick the side and the single entry price, or refuse.

    Deterministic: the CHEAPER side in own terms, one tick below its own-terms
    best bid. Cheaper is chosen because the $0.25 cap can only ever be met there,
    not because cheapness is believed to predict anything -- the "buy the cheap
    side" hypothesis was tested and rejected at 55.8%.
    """
    bid, ask = bbo.get("bid"), bbo.get("ask")
    if bid is None or ask is None:
        raise Case1Refuse(f"NO_TWO_SIDED_TOUCH: {dict(bbo)}")
    cands: List[Tuple[D, str]] = []
    for side in ("up", "down"):
        ob = own_bid(side, bid, ask)
        if ob is not None:
            cands.append((ob, side))
    cands.sort()
    _, side = cands[0]                       # the cheaper side in own terms
    touch = own_bid(side, bid, ask)
    if touch is None:
        raise Case1Refuse(f"NO_TOUCH on {side}: {dict(bbo)}")
    if not on_grid(touch):
        raise Case1Refuse(f"OFF_GRID_TOUCH: own-terms touch {touch} on {side} "
                          f"is not a multiple of {PRICE_TICK}")
    px = touch - PRICE_TICK * ENTRY_OFFSET_TICKS
    if not on_grid(px):
        raise Case1Refuse(f"OFF_GRID: {px} is not a multiple of {PRICE_TICK}")
    if px <= 0:
        raise Case1Refuse(f"NON_POSITIVE_PRICE: {px} on {side}")
    #: THE CAP. Refuse rather than widen risk, and refuse rather than quietly
    #: price further out to satisfy it -- that buys a near-certain null.
    if px > MAX_ENTRY_PX:
        raise Case1Refuse(
            f"ABOVE_CAP: one tick below the {side} touch is {px}, over the "
            f"{MAX_ENTRY_PX} cap. Refusing rather than widening risk or "
            f"pricing further from the market")
    if worst_case_loss(px) > MAX_LOSS:
        raise Case1Refuse(f"LOSS_ENVELOPE: worst case {worst_case_loss(px)} "
                          f"exceeds {MAX_LOSS}")
    #: a BUY at or above our own-terms ask would be marketable and post-only
    #: would be refused by the venue. Must be strictly below.
    oa = own_ask(side, bid, ask)
    if oa is not None and px >= oa:
        raise Case1Refuse(f"MARKETABLE: {px} >= own ask {oa} on {side}")
    return side, px


def build_body(slug: str, side: str, px: D) -> Dict[str, Any]:
    """The proven serializer, post-only GTC, price in the side's OWN terms."""
    return OB.build(slug=slug, side=side, price=px, qty=QTY,
                    post_only=True, tif=OB.TIF_GTC)


def preflight(t: X.Transport, slug: str, *, public_frames: int,
              private_frames: Any, sleep=time.sleep) -> Dict[str, Any]:
    """Prove both sockets ready, choose the one price, submit nothing.

    Socket liveness reuses the schema probe's established invariants: a fresh
    frame count on the high-rate public market socket, and a COMPLETED order
    snapshot on the private socket, which is event-driven and silent when idle.
    """
    if public_frames < P.READY_FRAMES:
        raise Case1Refuse(f"PUBLIC_SOCKET_NOT_PROVEN: {public_frames} frames "
                          f"< {P.READY_FRAMES}")
    proof = P.order_snapshot_proof(private_frames)
    if proof is None:
        n = len(private_frames) if isinstance(private_frames,
                                             (list, tuple)) else "?"
        raise Case1Refuse(f"PRIVATE_SOCKET_NOT_PROVEN: no completed order "
                          f"snapshot (eof=true) among {n} frames")
    bbo = X.fresh_bbo(t, slug, sleep=sleep)          # raises on a stale feed
    side, px = choose_entry(bbo)
    if X.would_cross(OB.outcome_side(side), px, bbo):
        raise Case1Refuse(f"WOULD_CROSS: {side} {px} against {bbo}")
    return {"side": side, "price": px, "bbo": bbo,
            "own_touch": str(own_bid(side, bbo.get("bid"), bbo.get("ask"))),
            "worst_case_loss": str(worst_case_loss(px)),
            "private_readiness_frame": proof}


# ------------------------------------------------- reach / cross detection

def reach_state(side: str, px: D, bid: Any, ask: Any) -> Dict[str, bool]:
    """Did the book come to us, and did it cross us?

    TOUCHED  our price is at or above the own-terms best bid -- our level is the
             front edge, but nobody has offered to sell to us there.
    REACHED   the own-terms best ASK has fallen to <= our price: the market is
             offering at our price or better, which is the Case-1 condition.
    """
    ob, oa = own_bid(side, bid, ask), own_ask(side, bid, ask)
    px = D(str(px))
    return {"touched": ob is not None and ob <= px,
            "reached": oa is not None and oa <= px}


#: which public aggressor consumes a resting BUY of each side. Established
#: empirically on 6,874 trades: taker.side alone decides, outcomeSide does not
#: flip it. Our resting BUY of `up` can only be consumed by a SELL.
CONSUMED_BY = {"up": "SELL", "down": "BUY"}


def consuming_trades(side: str, px: D, trades: Any) -> List[Dict[str, Any]]:
    """Public prints that could have hit our resting BUY: correctly directed
    and at a own-terms price at or below ours. Direction is never inferred from
    price when the venue states it."""
    out: List[Dict[str, Any]] = []
    want = CONSUMED_BY[side]
    px = D(str(px))
    for tr in (trades or []):
        if not isinstance(tr, Mapping):
            continue
        #: THE RECORDER'S OWN ROW SHAPE. `apply_events` emits `px` and `dir`
        #: (lowercase "buy"/"sell"), NOT `price`/`direction`. Reading only the
        #: latter found zero consuming prints on every real tape and would have
        #: manufactured a false null.
        direction = str(tr.get("dir") or tr.get("direction")
                        or tr.get("side") or "").upper()
        if direction not in ("BUY", "SELL"):
            continue                      # unknown direction fills nothing
        if direction != want:
            continue
        raw = tr.get("px") if tr.get("px") is not None else tr.get("price")
        if raw is None:
            continue
        try:
            yes_px = D(str(raw))
        except Exception:                                   # noqa: BLE001
            continue
        own = yes_px if side == "up" else ONE - yes_px
        if own <= px:
            out.append(dict(tr))
    return out


# ---------------------------------------------------------- qualification

QUALIFYING_CONDITIONS = (
    "rest_proved",            # 1 private NEW + REST OPEN: we were really resting
    "reached",               # 2 the book came to/through our price on its own
    "private_terminal",      # 3 private evidence of what the venue says happened
    "public_sequence",       # 4 public trade/book sequence complete enough to order
)


def qualifies(obs: Mapping[str, Any]) -> Dict[str, Any]:
    """All four preregistered conditions, reported individually.

    A run that fails condition 2 is a VALID NULL, not a failure: the market
    simply never came to us and we did not chase it.
    """
    have = {k: bool(obs.get(k)) for k in QUALIFYING_CONDITIONS}
    return {"conditions": have, "qualifies": all(have.values()),
            "missing": [k for k, v in have.items() if not v]}


#: the fields the authorization names, captured from the private frames
PRIVATE_CAPTURE_FIELDS = (
    "type", "tradeId", "aggressor", "transactTime", "lastShares", "lastPx",
    "commissionNotionalCollected", "cumQuantity", "leavesQuantity",
)


def extract_private(frames: Any, order_id: str) -> Dict[str, Any]:
    """Pull the preregistered fields from every private frame naming our order.

    Structural, not schema-presuming: it searches the frame tree rather than a
    guessed path, records each field VERBATIM, and marks a field that never
    appeared as UNAVAILABLE rather than defaulting it. The 2026-10-01 run proved
    `tradeId` EXISTS but was empty on NEW/CANCELED, so a fill populating it is
    still unestablished.
    """
    found: Dict[str, List[Any]] = {k: [] for k in PRIVATE_CAPTURE_FIELDS}
    naming: List[Any] = []

    def mentions(node: Any) -> bool:
        if isinstance(node, str):
            return bool(order_id) and order_id in node
        if isinstance(node, Mapping):
            return any(mentions(v) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(mentions(v) for v in node)
        return False

    def harvest(node: Any) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                if k in found and not isinstance(v, (dict, list)):
                    found[k].append(v)
                harvest(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                harvest(v)

    for f in (frames or []):
        if mentions(f):
            naming.append(f)
            harvest(f)
    return {
        "frames_naming_our_order": len(naming),
        "frames_verbatim": naming,
        "fields": {k: (v if v else "UNAVAILABLE") for k, v in found.items()},
        "fields_unavailable": sorted(k for k, v in found.items() if not v),
    }


def classify(*, submitted: bool, rest_proved: bool, reach: Mapping[str, Any],
             filled_qty: D, qual: Mapping[str, Any]) -> str:
    """The outcome label. A fill is evidence; a null is a result; neither is
    profit, and nothing here is ever labelled realized."""
    if not submitted:
        return "REFUSED"
    if not rest_proved:
        return "STOPPED_REST_NOT_PROVED"
    if not reach.get("reached"):
        return "NULL_NOT_REACHED"
    if qual.get("qualifies"):
        return ("CASE1_OBSERVED_FILLED" if D(str(filled_qty)) > 0
                else "CASE1_OBSERVED_NOT_FILLED")
    return "CASE1_INCONCLUSIVE"


# =====================================================================
# THE LIVE-REPORT VERDICT TAXONOMY
#
# Six mutually exclusive labels, so "no fill" and "no opportunity" can never
# collapse into the same bucket -- which is the distinction Case 1 turns on.
# =====================================================================

VERDICTS = (
    #: null. We rested, the market never came to us, we did not chase it.
    "RESTING_NOT_REACHED",
    #: QUALIFYING OBSERVATIONAL result: proven resting, the book moved to or
    #: through our price AFTERWARDS with our order unchanged, still unfilled.
    "REACHED_OR_CROSSED_UNFILLED",
    "FILLED_AS_MAKER",
    "FILLED_AS_AGGRESSOR",
    #: the event order, or our role in the fill, cannot be established from the
    #: evidence. Record and DO NOT OVERINTERPRET.
    "AMBIGUOUS_EVENT_ORDER",
    #: any safety or lifecycle deviation. Never an evidence claim.
    "PROBE_FAILURE",
)

#: Candidate orientation for the venue's `aggressor` enum relative to OUR order,
#: which is always an ORDER_ACTION_BUY. UNVERIFIED: the 2026-10-01 schema run saw
#: the field only on NEW/CANCELED, where it was empty, so no fill has ever
#: established this mapping for our account. It is therefore only ever used as
#: ONE of two signals that must AGREE.
AGGRESSOR_ROLE_RULE = {"BUY": "AGGRESSOR", "SELL": "MAKER"}
AGGRESSOR_RULE_VERIFIED = False


def _first(values: Any) -> Optional[Any]:
    if isinstance(values, (list, tuple)) and values:
        return values[-1]            # the last observed value for the field
    return None


def role_from_aggressor(value: Any) -> Optional[str]:
    """Our role implied by the venue's `aggressor` string, or None.

    Deliberately crude and deliberately not trusted alone: it looks for BUY or
    SELL anywhere in the enum (`AGGRESSOR_SIDE_SELL`) and maps it through the
    UNVERIFIED rule above.
    """
    s = str(value or "").upper()
    if not s:
        return None
    has_buy, has_sell = "BUY" in s, "SELL" in s
    if has_buy == has_sell:          # neither, or contradictory
        return None
    return AGGRESSOR_ROLE_RULE["BUY" if has_buy else "SELL"]


def role_from_commission(value: Any, px: Any, qty: Any = QTY) -> Optional[str]:
    """Our role implied by what the venue actually CHARGED, or None.

    Economically grounded and independent of the enum's orientation: a taker is
    charged on the published schedule, a maker is not charged (and may be
    credited). Compared against `pmus_fees.taker_fee` with `cents=True`, the
    venue's own statement rounding on the whole order quantity.
    """
    if value is None or value == "UNAVAILABLE":
        return None
    raw = value
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    try:
        charged = D(str(raw))
    except Exception:                                        # noqa: BLE001
        return None
    if charged <= 0:
        return "MAKER"               # nothing taken, or credited to us
    from src import pmus_fees as FEES
    expect = FEES.taker_fee(D(str(px)), D(str(qty)), cents=True)
    if expect <= 0:
        return None
    #: within half a cent of the taker charge is the taker charge
    if abs(charged - expect) <= D("0.005"):
        return "AGGRESSOR"
    return None                      # charged something we cannot account for


def fill_role(fields: Mapping[str, Any], px: Any,
              qty: Any = QTY) -> Dict[str, Any]:
    """Our role in the fill, from TWO independent signals that must AGREE.

    Returns MAKER, AGGRESSOR or UNRESOLVED. One signal alone is never enough:
    the `aggressor` enum's orientation relative to our BUY has never been
    established by a real fill, and a commission we cannot account for is not
    evidence of anything.
    """
    agg_raw = _first(fields.get("aggressor"))
    com_raw = _first(fields.get("commissionNotionalCollected"))
    by_agg = role_from_aggressor(agg_raw)
    by_com = role_from_commission(com_raw, px, qty)
    out = {"aggressor_raw": agg_raw, "commission_raw": com_raw,
           "role_by_aggressor": by_agg, "role_by_commission": by_com,
           "aggressor_rule_verified": AGGRESSOR_RULE_VERIFIED}
    if by_agg is not None and by_com is not None and by_agg == by_com:
        out["role"] = by_agg
        out["basis"] = "two independent signals agree"
    elif by_agg is not None and by_com is not None:
        out["role"] = "UNRESOLVED"
        out["basis"] = "the two signals DISAGREE; not interpreted"
    else:
        out["role"] = "UNRESOLVED"
        out["basis"] = "only one signal available; one is never enough"
    return out


def verdict(*, safety_ok: bool, rest_proved: bool,
            reached_before_rest: bool, reached_after_rest: bool,
            filled_qty: Any, role: Mapping[str, Any]) -> str:
    """The single live-report label. Safety first, then event order, then role.

    `reached_before_rest` is why this exists: if the book was ALREADY at or
    through our price when we were first proved resting, the experiment's whole
    ordering claim -- private NEW + REST OPEN, THEN the book moves, with our
    order unchanged -- is unestablished, and the honest label is ambiguity.
    """
    if not safety_ok or not rest_proved:
        return "PROBE_FAILURE"
    if reached_before_rest:
        return "AMBIGUOUS_EVENT_ORDER"
    if D(str(filled_qty or 0)) > 0:
        r = str(role.get("role") or "UNRESOLVED")
        if r == "MAKER":
            return "FILLED_AS_MAKER"
        if r == "AGGRESSOR":
            return "FILLED_AS_AGGRESSOR"
        return "AMBIGUOUS_EVENT_ORDER"
    if reached_after_rest:
        return "REACHED_OR_CROSSED_UNFILLED"
    return "RESTING_NOT_REACHED"
