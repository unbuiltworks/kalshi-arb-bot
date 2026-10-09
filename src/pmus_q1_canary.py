"""PMUS q=1 HOLD_OPEN_EXTEND canary — DISARMED BY DEFAULT.

WHAT THIS IS. The first bounded live probe of the frozen `HOLD_OPEN_EXTEND`
behaviour, on BTC 15m, q=1, one market, one campaign per invocation. It
COMPOSES the reviewed lifecycle primitives in `pmus_q1_executor` (preview
first, post-only maker, fresh-BBO cross guard, durable intent before
placement, authoritative fill polling, exact-ID cancel with verification,
market-scoped Cancel-All deadman on every exit path, strict accounting
labels) and adds only the behaviour under test. No primitive is modified,
so the reviewed paths stay byte-identical for the existing campaign.

WHAT IT IS NOT. It is not evidence that holding is profitable. The arm's
only prior evidence is a 5.01-minute partial slice measuring MECHANICS
(requotes avoided, fills, inventory formed); its settled economics have
never been computed. The success criterion here is a clean lifecycle and
genuinely completed paired inventory -- not $/window.

ARMING is triple-key and fail-closed. The executor's own dual key is not
sufficient: this behaviour is separately authorized, so it needs its own.
    PMUS_Q1_LIVE=YES
    PMUS_Q1_SESSION_ID=<fresh id>   PMUS_Q1_ARM=<same id>
    PMUS_CANARY_ARM=<the same id>            <-- canary-specific
    PMUS_Q1_MARKET_SLUG=<market>    PMUS_Q1_GATE_EVIDENCE=<same slug>
Any absence or mismatch -> DISARMED before a single network call.

BEHAVIOUR UNDER TEST, in the order the mandate states it:

1. Rest an economically valid maker OPEN/EXTEND.
2. Do NOT chase the touch merely because it moved. Keep the resting order,
   and with it the queue position, for as long as it stays valid.
3. On a fill, the objective switches immediately to reducing unmatched
   inventory. No new opening leg is considered while residual exists.
4. Keep the complement working continuously until matched or the deadline.
5. A partial complement fill reduces only the REMAINING quantity.
6. If a contemporaneous taker completion gives an acceptable fee-adjusted
   pair cost, complete immediately rather than waiting for a maker fill.
7. Never add to the long side unless the aggregate-basis rule permits it
   INSIDE the residual cap. At q=1 with RESIDUAL_CAP=1 a second long leg
   would put residual at 2 > cap, so an EXTEND is structurally impossible
   here; `extend_permitted` says so explicitly rather than leaving it to
   an accident of control flow.
8. Unconditional cancel/sweep on every exit path.

Every decision, submission, ACK, fill, cancel, inventory change, pair cost,
fee, rebate credit and cashflow is appended to the durable ledger. Realized
P&L stays null until the settlement audit reconciles it; this process never
guesses it.

Disarmed dry-run (safe anywhere, no network):
    python3 -m src.pmus_q1_canary
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_q1_executor as X
from src import pmus_fees as FEES
from src import pmus_order_lifecycle as LC
from src import pmus_ws_live_state as WS

CANARY_ARM_ENV = "PMUS_CANARY_ARM"
HOLD_DEADLINE_ENV = "PMUS_CANARY_DEADLINE_SECONDS"      # default 600
#: worst case for one unpaired long contract is its own cost, and `entry_ok`
#: already caps that at $0.80. This is the smallest practical loss cap for a
#: probe that actually trades: one contract, one market, one campaign.
CANARY_MAX_LOSS = D("0.80")
#: how many status reads a cancel gets to reach a TERMINAL state before the
#: campaign refuses to continue. The venue is eventually consistent (the
#: executor documents a 429 on /v1/orders/open outlasting five one-second
#: retries), so one read is not proof.
CANCEL_CONFIRM_POLLS = 6


class CancelUnconfirmed(Exception):
    """A cancel was ACK'd but the order never reached a terminal state."""


#: seconds reserved at the end of the window for authoritative cancellation
#: and the market-scoped sweep. The campaign must be finished BEFORE the
#: market closes, not at the same moment.
SWEEP_BUFFER_S = float(os.getenv("PMUS_CANARY_SWEEP_BUFFER_S", "90"))


def window_end_epoch(slug: str) -> Optional[float]:
    """The close time encoded in a BTC15m slug, e.g. ...-2026-09-30-1800z.

    Parsed from the slug rather than the clock because the slug is what the
    gate, the feed and the orders all name; if they ever disagree this
    returns None and the caller refuses rather than guessing a close.
    """
    import re
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})z$", slug)
    if not m:
        return None
    y, mo, d, hh, mm = (int(g) for g in m.groups())
    try:
        start = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc)
    except ValueError:
        return None
    return start.timestamp() + 900.0


def effective_deadline_s(slug: str, requested_s: float, *,
                         now: Optional[float] = None,
                         buffer_s: float = SWEEP_BUFFER_S) -> Optional[float]:
    """How long this campaign may run: never past the window's close.

    Railway's build time is consumed INSIDE the window (measured
    2026-09-30: the venue had not quoted 1745z three minutes after its own
    start, and 1730z was already EXPIRED), so a deadline set by hand before
    deployment cannot know how much runway the container will actually
    have. This derives it at startup instead, and returns None when there is
    no usable runway left -- in which case nothing is placed at all.
    """
    end = window_end_epoch(slug)
    if end is None:
        return None
    left = end - (time.time() if now is None else now) - buffer_s
    if left <= 0:
        return None
    return min(float(requested_s), left)


#: how much of a venue error body to keep, and what never to write down.
VENUE_ERROR_BODY_MAX = 2048
_REDACT_KEYS = ("signature", "secret", "key", "token", "authorization",
                "x-pm-access-key", "x-pm-signature")


def sanitize_venue_error(raw: bytes) -> str:
    """A venue error body, bounded, with anything credential-shaped cut.

    Never a passthrough: this is attacker-influenced text we are about to
    persist to the ledger and print to the platform log.
    """
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:                                        # noqa: BLE001
        return "<undecodable venue error body>"
    try:
        doc = json.loads(text)
    except Exception:                                        # noqa: BLE001
        return text[:VENUE_ERROR_BODY_MAX]
    if isinstance(doc, Mapping):
        clean = {k: ("<redacted>" if any(r in str(k).lower()
                                         for r in _REDACT_KEYS) else v)
                 for k, v in doc.items()}
        return json.dumps(clean, separators=(",", ":"))[:VENUE_ERROR_BODY_MAX]
    return text[:VENUE_ERROR_BODY_MAX]


class CapturingTransport(X.Transport):
    """The reviewed transport plus the venue's error text.

    LIVE 2026-09-30T18:09:16Z: a 400 from /v1/order/preview raised with its
    body unread, so the venue's REASON was lost and a traceback was all we
    had. Capturing it belongs HERE, not in `pmus_q1_executor`: that module is
    execution-class in the R1 source-lineage guard, and editing it drifted the
    reviewed artifact (the guard caught it -- `undisclosed:
    src/pmus_q1_executor.py`). Subclassing changes nothing for any other
    caller of the reviewed transport.
    """

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = X.API) -> Any:
        import urllib.error
        try:
            return super().request(method, path, body, base=base)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(VENUE_ERROR_BODY_MAX) or b""
            except Exception:                                # noqa: BLE001
                raw = b""
            exc.venue_body = sanitize_venue_error(raw)       # type: ignore[attr-defined]
            exc.venue_path = path                            # type: ignore[attr-defined]
            raise


def canary_armed() -> bool:
    """The executor's dual key AND the canary-specific third key."""
    sid = os.getenv(X.SID_ENV, "").strip()
    return (X.armed() and bool(sid)
            and os.getenv(CANARY_ARM_ENV, "").strip() == sid)


def _ws_fields(snap: Mapping[str, Any]) -> dict:
    """The WS evidence every decision row must carry, per the mandate."""
    return {"ws_recv_t": snap.get("ws_recv_t"),
            "book_age_s": round(float(snap.get("book_age_s") or 0), 4),
            "bid": str(snap.get("bid")), "ask": str(snap.get("ask")),
            "bid_qty": str(snap.get("bid_qty")),
            "ask_qty": str(snap.get("ask_qty")),
            "depth": snap.get("depth"),
            "last_trade_px": str(snap.get("last_trade_px")),
            "last_trade_qty": str(snap.get("last_trade_qty")),
            "last_trade_venue_t": snap.get("last_trade_venue_t"),
            "last_trade_age_s": snap.get("last_trade_age_s"),
            "book_updates": snap.get("book_updates"),
            "feed": snap.get("source")}


#: lifecycle rows that must be visible in the platform log, not only in the
#: ledger on the mounted volume. LIVE 2026-09-30: the container's logs held a
#: traceback and nothing else -- no gate evidence, no decision, no report --
#: because every record() went to the volume and the volume is not readable
#: without the Railway agent.
STDOUT_KINDS = ("canary_start", "ws_ready", "resting", "submitted", "filled",
                "cancel", "decision", "preview_rejected", "taker_unfilled",
                "terminal_rejection", "halt", "ws_stale_refused",
                "cancel_unconfirmed", "tick_unavailable", "canary_end",
                "sweep")


def record(kind: str, **fields: Any) -> None:
    """One ledger row per decision, and the lifecycle ones to stdout too.

    Recording is part of the mandate, so a failure to record is not swallowed
    silently -- it is re-raised as a Halt so the campaign exits through the
    sweep.
    """
    if kind in STDOUT_KINDS:
        try:
            print("CANARY %s %s" % (kind, json.dumps(
                {k: v for k, v in fields.items() if k != "depth"},
                separators=(",", ":"), default=str)[:900]), flush=True)
        except Exception:                                    # noqa: BLE001
            print("CANARY %s <unserializable fields>" % kind, flush=True)
    try:
        X.ledger_append({"kind": kind, "campaign": "pmus-q1-canary", **fields})
    except Exception as exc:                                 # noqa: BLE001
        raise X.Halt(f"LEDGER_WRITE_FAILED:{kind}:{str(exc)[:80]}") from exc


def reconcile(t: X.Transport, oid: str, info: dict, inv: "Inventory", *,
              sleep=time.sleep) -> dict:
    """Read one order, classify it, and book ONLY the newly filled delta.

    PARTIAL FILLS. `X.authoritative_fill` returns None while
    `cumQuantity < QTY` unless the state says FILLED, and the old
    `terminal_no_fill` required cumulative zero. A q1 maker that filled 0.40
    and then cancelled therefore matched NEITHER classification: it was not a
    fill and not a no-fill, so it fell through to CANCEL_UNCONFIRMED while the
    account really owned 0.40 contracts and the report said residual 0.

    `pmus_order_lifecycle.classify` is the repo's existing primitive for this
    -- it understands PARTIALLY_FILLED, cumulative quantity, terminal partial
    fills and the own-terms price inversion, without the executor's coupling
    to a module-level QTY. Nothing new is invented here.

    `info["booked_qty"]` is the cumulative quantity ALREADY in inventory, so
    only the increase is added and no quantity can be counted twice. UNKNOWN
    is never treated as a zero fill: it halts.
    """
    st = X._order_status(t, oid, sleep=sleep)
    v = LC.classify(st, requested_qty=info.get("qty", D(X.QTY)),
                    side=info.get("inv_side"))
    if v["classification"] == LC.UNKNOWN:
        raise X.Halt(f"UNKNOWN_ORDER_STATE:{oid}:{v.get('why')}")
    filled = v["filled_qty"] or D(0)
    booked = D(str(info.get("booked_qty", 0)))
    if filled > booked:
        delta = filled - booked
        px = v["price_own"]
        if px is None:                       # classify raises before this, but
            raise X.Halt(f"FILL_WITHOUT_PRICE:{oid}")
        inv.add(info["inv_side"], delta, px)
        info["booked_qty"] = filled
        FILLED_LEGS.append({"order_id": oid, "stage": info.get("stage"),
                            "inv_side": info["inv_side"], "px": str(px),
                            "qty": str(delta),
                            "maker": bool(info.get("maker", True))})
        record("filled", stage=info.get("stage"), order_id=oid,
               price=str(px), qty=str(delta), cumulative=str(filled),
               classification=v["classification"],
               maker=bool(info.get("maker", True)),
               residual=str(inv.residual()), matched=str(inv.matched()))
    info["remaining"] = D(str(info.get("qty", X.QTY))) - filled
    return v


def terminal_no_fill(order: Mapping[str, Any]) -> bool:
    """Is this order PROVABLY off the venue with nothing filled?

    CANCEL and REJECT, plus EXPIRED because an IOC that finds no liquidity
    expires rather than being cancelled. Anything else -- including
    ORDER_STATE_OPEN -- is NOT proof, which is the whole point: the legacy
    `X.cancel_exact` predicate returns True for a still-working order, and
    treating that as terminal is how an order gets forgotten while live.
    """
    state = X.order_state_str(order)
    if X._filled_qty(order) != 0:
        return False
    return any(w in state for w in ("CANCEL", "REJECT", "EXPIRE"))


def resolve_ioc(t: X.Transport, order_id: str, slug: str, info: dict,
                inv: "Inventory", sleep=time.sleep) -> dict:
    """Resolve a crossing IOC on POSITIVE VENUE PROOF only.

    An IOC normally terminates immediately, but the safety design here does
    not assume venue lifecycle behaviour -- that assumption is exactly what
    produced the forgotten-order defect on the maker path. So this reads the
    order's own record until it proves one of two things:

      * a parseable fill, or
      * a terminal state with no fill (cancelled / rejected / expired).

    If neither is proven it escalates to `cancel_and_resolve`, which also
    demands proof and raises `CancelUnconfirmed` if it cannot get it. The
    caller must keep the order in `unresolved` unless this returns.
    """
    for _ in range(CANCEL_CONFIRM_POLLS):
        v = reconcile(t, order_id, info, inv, sleep=sleep)
        if v["terminal"]:
            return {"terminal": True, "state": v["state"],
                    "classification": v["classification"]}
        sleep(X.POLL_S)
    # still non-terminal: take it off the book, with proof required
    return cancel_and_resolve(t, order_id, slug, info, inv, sleep=sleep)


def cancel_and_resolve(t: X.Transport, order_id: str, slug: str,
                       info: Optional[dict] = None,
                       inv: Optional["Inventory"] = None,
                       sleep=time.sleep) -> dict:
    """Cancel an order and POSITIVELY resolve what happened to it.

    BLOCKER FIX. `X.cancel_exact` returns
    `"CANCEL" in s or "REJECT" in s or "FILLED" not in s`, which is **True for
    a still-working `ORDER_STATE_OPEN`**. The canary used to record that value
    and then clear `resting` unconditionally, so a cancel the venue ACK'd but
    did not apply left a live maker the controller had forgotten -- and the
    next iteration placed a second order. Measured consequence: two live YES
    orders, $1.59 committed against an authorized $0.80 cap, residual 2
    against a cap of 1, while the report said `residual=0`.

    So this returns only on PROOF:
      * `{"fill": ...}`  -- the venue says filled, with a parseable price;
      * `{"terminal": True, "state": ..., "classification": ...}` and, when an
        inventory is supplied, `"filled_qty"` -- a terminal CANCEL, or a REJECT
        with no fill. There is NO `"cancelled"` key; this docstring used to
        claim one and a caller read `res.get("cancelled")`, silently reporting
        no terminal proof for a cancellation the venue had positively
        confirmed.
    Anything else raises, and the caller must NOT clear its record of the
    order. Refusing to trade on is the only safe response to "we do not know
    whether our order is live".
    """
    try:
        t.request("POST", f"/v1/order/{order_id}/cancel", {"marketSlug": slug})
    except Exception as exc:                                 # noqa: BLE001
        raise CancelUnconfirmed(
            f"CANCEL_REQUEST_FAILED:{order_id}:{str(exc)[:80]}") from exc
    for _ in range(CANCEL_CONFIRM_POLLS):
        if info is not None and inv is not None:
            v = reconcile(t, order_id, info, inv, sleep=sleep)
            if v["terminal"]:
                return {"terminal": True, "state": v["state"],
                        "classification": v["classification"],
                        "filled_qty": str(v["filled_qty"] or 0)}
        else:
            st = X._order_status(t, order_id, sleep=sleep)
            v = LC.classify(st, requested_qty=D(X.QTY))
            if v["terminal"]:
                return {"terminal": True, "state": v["state"],
                        "classification": v["classification"]}
        sleep(X.POLL_S)
    raise CancelUnconfirmed(
        f"CANCEL_UNCONFIRMED:{order_id}: still not terminal after "
        f"{CANCEL_CONFIRM_POLLS} reads; refusing to place another order "
        f"while this one may be live")


# ----------------------------------------------------------------- inventory

class Inventory:
    """Authoritative inventory: only venue-confirmed fills move it."""

    def __init__(self) -> None:
        self.qty = {"yes": D(0), "no": D(0)}
        self.cost = {"yes": D(0), "no": D(0)}

    def add(self, side: str, qty: D, price: D) -> None:
        self.qty[side] += D(str(qty))
        self.cost[side] += D(str(qty)) * D(str(price))

    def residual(self) -> D:
        """Positive means long YES, negative long NO; 0 means fully paired."""
        return self.qty["yes"] - self.qty["no"]

    def deficient(self) -> Optional[str]:
        """The side that would REDUCE residual, or None when flat/paired."""
        r = self.residual()
        if r > 0:
            return "no"
        if r < 0:
            return "yes"
        return None

    def basis(self, side: str) -> D:
        """Average acquisition cost of the side we hold."""
        q = self.qty[side]
        return (self.cost[side] / q) if q > 0 else D(0)

    def matched(self) -> D:
        return min(self.qty["yes"], self.qty["no"])


def worst_case_loss(inv: "Inventory", resting: Optional[Mapping[str, Any]]
                    ) -> D:
    """The most this campaign can still lose, in dollars.

    A matched pair cannot lose more than its cost above $1, and this probe
    only ever pairs below $1, so the exposure that matters is the UNMATCHED
    long plus any opening order still able to add one. The complement reduces
    residual, so it adds nothing.

    The $0.80 bound used to be an emergent property of `entry_ok` + q=1 + one
    order, with nothing checking it. The reviewer's point stands: any defect
    in the order-count invariant breached it silently. Now it is measured.
    """
    r = inv.residual()
    loss = (r * inv.basis("yes")) if r > 0 else (
        (-r * inv.basis("no")) if r < 0 else D(0))
    if resting is not None and resting.get("stage") in ("open", "extend"):
        loss += D(str(resting["px"])) * D(str(resting["qty"]))
    return loss


def extend_permitted(inv: Inventory, *, residual_cap: int = X.RESIDUAL_CAP
                     ) -> bool:
    """Rule 7. An EXTEND adds to the side we are ALREADY long.

    Two independent conditions, both required:
      * the resulting residual must stay inside the cap; and
      * the new lot must lower the aggregate basis of that side -- which at
        q=1 cannot even be evaluated, because there is no second lot.
    At RESIDUAL_CAP = 1 the first condition already fails, so this returns
    False for every reachable state of this canary. It is written out so the
    prohibition is a stated rule rather than a side effect.
    """
    r = abs(inv.residual())
    return (r + 1) <= residual_cap


# -------------------------------------------------------- validity of a rest

def open_still_valid(px: D, snap: Mapping[str, Any]) -> bool:
    """Rule 2. May a resting OPEN keep its price, and its queue position?

    A better touch is explicitly NOT a reason to move. The order is given up
    only when it stops being a valid opening leg: its price leaves the
    entry band, or it would now cross the live opposite quote (a post-only
    rejection waiting to happen).
    """
    if not X.entry_ok(D(str(px))):
        return False
    return not X.would_cross("OUTCOME_SIDE_YES", D(str(px)), snap)


def complement_still_valid(px: D, first_cost: D, tick: Optional[D],
                           snap: Mapping[str, Any]) -> bool:
    """Rule 4. A resting complement keeps its place while it still pairs.

    The bound is recomputed from the CURRENT basis every poll. If the bound
    has moved BELOW our resting price the order no longer locks the required
    edge and must go; if the bound has moved above it, our price is still
    fee-safe and we keep our queue position rather than improving it.
    """
    bound = X.complement_bound(D(str(first_cost)), tick=tick)
    if bound is None or D(str(px)) > bound:
        return False
    return not X.would_cross("OUTCOME_SIDE_NO", D(str(px)), snap)


#: Every leg this process submitted, with the maker/taker fact from OUR OWN
#: submission rather than the venue's reply -- the venue returned
#: `participateDontInitiate: null` on both legs of the 2026-09-30 campaign,
#: so inferring it from the reply defaults a crossing leg to maker.
SUBMITTED_LEGS: list = []

#: Every EXECUTED fill: the venue's own price and the quantity actually
#: booked, with maker/taker taken from our submission record rather than the
#: venue's reply. Fee and rebate economics must use the EXECUTED price -- a
#: post-only maker normally fills at its own limit, but a fill price is what
#: the venue charges on, so the submitted price is the wrong input.
FILLED_LEGS: list = []


def place_order(t: X.Transport, slug: str, side: str, price: D, *,
                post_only: bool, stage: str, sleep=time.sleep) -> str:
    """ONE placement path for this canary, on THE proven serializer.

    LIVE 2026-09-30T18:09:16Z: `/v1/order/preview` answered HTTP 400 and the
    campaign died on its first action. The cause was serializer drift, not the
    venue: this canary reached the venue through hand-built bodies that sent
    `price` without `"currency": "USD"` and POSTed the bare order body to the
    preview endpoint. `src/l5_order_body` is the body that has ACTUALLY TRADED
    on PMUS -- `pmus_micro_executor._body` delegates to it, and both it and
    `pmus_q1_executor_v4` send preview as `{"request": body}`.

    So nothing about the wire format is decided here. `OB.build` owns price
    (including the `currency` field), quantity as a JSON number, the outcome
    enum, TIF, post-only, action and the manual-order indicator;
    `OB.preview_envelope` owns the preview wrapper. `/v1/orders` gets the
    bare body, exactly as the proven callers send it.

    PRICE IS PASSED IN THE OUTCOME'S OWN TERMS. `OB.venue_price` performs the
    NO inversion, so this function must never pre-convert -- inverting twice
    sends a wildly wrong price, which is the failure the serializer's own
    docstring records from 24 of 24 rejected NO orders.
    """
    from src import l5_order_body as OB
    body = OB.build(slug=slug, side=side, price=D(str(price)), qty=X.QTY,
                    post_only=post_only,
                    tif=OB.TIF_GTC if post_only else OB.TIF_IOC)
    try:
        preview = t.request("POST", "/v1/order/preview",
                            OB.preview_envelope(body))
    except Exception as exc:                                 # noqa: BLE001
        detail = getattr(exc, "venue_body", "") or str(exc)[:200]
        record("preview_rejected", stage=stage, side=side, px=str(price),
               venue=str(detail)[:500])
        raise X.Halt(f"PREVIEW_REJECTED:{stage}:{str(detail)[:200]}") from exc
    if preview.get("error") or preview.get("warnings"):
        record("preview_rejected", stage=stage, side=side, px=str(price),
               venue=json.dumps(
                   {k: preview.get(k) for k in ("error", "warnings")},
                   default=str)[:500])
        raise X.Halt(f"PREVIEW_REJECTED:{stage}")
    # a MAKER price that meets the live opposite quote is refused locally
    # rather than submitted; a crossing leg is meant to cross, so it is not.
    if post_only and X.would_cross(OB.outcome_side(side), D(str(price)),
                                   X.fresh_bbo(t, slug, sleep=sleep)):
        raise X.StaleRefuse(f"WOULD_CROSS:{stage}:{price}")
    X.persist_intent({"stage": stage, "slug": slug,
                      "side": OB.outcome_side(side), "price": str(price),
                      "qty": X.QTY, "post_only": post_only,
                      "submitted_utc": datetime.now(timezone.utc).isoformat()})
    resp = t.request("POST", "/v1/orders", body)
    order_id = str(resp.get("id") or resp.get("orderId")
                   or (resp.get("order") or {}).get("id") or "")
    if not order_id:
        raise X.Halt(f"NO_ORDER_ID:{stage}")
    # ACCEPTANCE TIMESTAMPS. The ACK is the moment our quote joined the queue,
    # which sets the causal `placed_t` for every later fill judgement. It was
    # previously persisted with no time at all, so a lifecycle could not be
    # joined to the ~0.1s market-data sequence. Wall clock AND monotonic are
    # recorded, plus any venue-supplied timestamp verbatim.
    X.persist_intent({"stage": stage + ":accepted", "order_id": order_id,
                      "accepted_utc": datetime.now(timezone.utc).isoformat(),
                      "accepted_monotonic": time.monotonic(),
                      "venue_ts": {k: resp.get(k) for k in
                                   ("createdAt", "created_at", "transactTime",
                                    "updatedAt", "timestamp")
                                   if resp.get(k) is not None} or None,
                      "venue_state": resp.get("state")})
    record("submitted", stage=stage, slug=slug, side=OB.outcome_side(side),
           price=str(price), order_id=order_id, maker=post_only,
           wire_price=body["price"]["value"])
    SUBMITTED_LEGS.append({"order_id": order_id, "stage": stage,
                           "side": OB.outcome_side(side), "px": str(price),
                           "qty": X.QTY, "maker": bool(post_only)})
    return order_id


# -------------------------------------------------------------- the campaign

def _tick(t: X.Transport, slug: str, sleep=time.sleep) -> Optional[D]:
    """The venue's own price grid for this market, or None.

    Uses the canonical row route `/v1/market/slug/{slug}` via
    `pmus_market_row`, which matches the slug on the row the venue returned
    and treats a missing tick as an error rather than a default.

    This was `GET /v1/markets/{slug}`, which the gateway answers with an
    ERROR OBJECT (verified 2026-09-30 17:35Z), so `tick_for_market` returned
    None and `complement_bound` silently fell back to `TICK` = 0.005. PMUS
    BTC15m is a 0.01-grid market that REJECTS such a price, so the probe
    would have placed its opening leg, then had the complement refused at
    preview and Halted holding an unpaired long. Fail-closed, but it burns
    the one authorized campaign.
    """
    from src import pmus_market_row as MR
    try:
        # through the injected transport: authenticated, bounded retries, and
        # testable. market_row takes any getter and matches the slug on the
        # row the venue returned.
        row = MR.market_row(
            slug,
            get=lambda path: X._get_with_retry(t, path, base=X.GATEWAY,
                                               sleep=sleep))
        return X.tick_for_market(row)
    except Exception as exc:                                 # noqa: BLE001
        record("tick_unavailable", slug=slug, err=str(exc)[:160])
        return None


def _poll_fill(t: X.Transport, order_id: str, sleep) -> dict:
    """One non-blocking look at an order: fill, terminal, or still working."""
    order = X._order_status(t, order_id, sleep=sleep)
    if X.terminal_rejection(order):
        return {"terminal": True}
    fill = X.authoritative_fill(order)
    return {"fill": fill} if fill is not None else {"working": True}


#: bounded proof that the market has no live orders of ours left
SWEEP_VERIFY_POLLS = 6


def open_order_ids(t: X.Transport, slug: str, sleep=time.sleep) -> list:
    """Ids of OUR still-open orders in this market, from the venue."""
    data = X._get_with_retry(t, "/v1/orders/open", sleep=sleep)
    out = []
    for o in (data.get("orders") or data.get("openOrders") or []):
        if not isinstance(o, Mapping):
            continue
        if str(o.get("marketSlug") or o.get("slug") or "") != slug:
            continue
        oid = o.get("id") or o.get("orderId")
        if oid:
            out.append(str(oid))
    return out


def finalize(t: X.Transport, slug: str, inv: "Inventory",
             unresolved: Dict[str, dict], *, sleep=time.sleep) -> dict:
    """Reconcile, cancel, and PROVE the market is clear -- before reporting.

    The old exit logged `sweep` merely because the cancel request had been
    sent, and built the report BEFORE that sweep ran. So an order filling
    during the shutdown race produced a report claiming residual 1 / matched 0
    while the venue had in fact completed the pair. That breaks the strict
    accounting rule: the terminal report must describe POST-SWEEP
    authoritative state.

    Order matters: reconcile first (a fill found now is real inventory),
    then cancel-all, then prove zero open orders for this market.
    """
    out: dict = {"booked_during_shutdown": [], "sweep_errors": []}
    # 1/2. reconcile every unresolved id; a fill found now is booked ONCE,
    #      because the id is removed from `unresolved` only on proof.
    for oid in list(unresolved):
        info = unresolved[oid]
        before = D(str(info.get("booked_qty", 0)))
        try:
            # reconcile() inside this BOOKS any newly filled delta itself, so
            # nothing is added here -- adding again is exactly the
            # double-count this refactor exists to prevent.
            resolve_ioc(t, oid, slug, info, inv, sleep=sleep)
        except Exception as exc:                             # noqa: BLE001
            out["sweep_errors"].append(f"{oid}:{type(exc).__name__}")
            continue
        gained = D(str(info.get("booked_qty", 0))) - before
        if gained > 0:
            out["booked_during_shutdown"].append(
                {"order_id": oid, "stage": info.get("stage"),
                 "qty": str(gained),
                 "cumulative": str(info.get("booked_qty"))})
            record("filled", stage=str(info.get("stage")) + "_shutdown",
                   order_id=oid, qty=str(gained),
                   cumulative=str(info.get("booked_qty")),
                   residual=str(inv.residual()), matched=str(inv.matched()))
        unresolved.pop(oid, None)
    # 3. market-scoped cancel-all. An error here is recorded, never swallowed
    #    into a claim of success.
    try:
        t.request("POST", "/v1/orders/open/cancel", {"slugs": [slug]})
    except Exception as exc:                                 # noqa: BLE001
        out["sweep_errors"].append(f"cancel_all:{type(exc).__name__}")
    # 4. PROVE zero open orders for this market, bounded.
    open_ids: list = []
    for _ in range(SWEEP_VERIFY_POLLS):
        try:
            open_ids = open_order_ids(t, slug, sleep=sleep)
        except Exception as exc:                             # noqa: BLE001
            out["sweep_errors"].append(f"open_read:{type(exc).__name__}")
            open_ids = ["<unreadable>"]
        if not open_ids:
            break
        sleep(X.POLL_S)
    out["open_orders"] = len(open_ids)
    out["open_order_ids"] = open_ids
    out["unresolved_orders"] = sorted(unresolved)
    out["sweep_verified"] = (not open_ids and not unresolved
                             and not out["sweep_errors"])
    record("sweep", sweep_verified=out["sweep_verified"],
           open_orders=out["open_orders"], open_order_ids=open_ids,
           unresolved=out["unresolved_orders"],
           errors=out["sweep_errors"],
           booked_during_shutdown=out["booked_during_shutdown"],
           note="market-scoped cancel-all, then proof of zero open orders")
    return out


#: The executor OWNS this boundary now. It used to be defined here, which
#: meant two copies of the same staleness contract could drift apart; the
#: alias keeps every existing reference working with one implementation.
_StateBackedFeed = X.StateBackedFeed


def _campaign(ctx: dict, t: Optional[X.Transport] = None,
              clock=time.monotonic, sleep=time.sleep,
              state: Optional["WS.LiveState"] = None,
              feed: Optional["WS.WsFeed"] = None,
              max_book_age_s: float = WS.DEFAULT_MAX_BOOK_AGE_S) -> dict:
    """Run the campaign and return its OUTCOME. Never the final report.

    The caller finalizes afterwards and assembles the report from post-sweep
    state, so `ctx` carries out the live objects the finalizer needs.
    """
    slug = os.getenv(X.MARKET_ENV, "").strip()
    report: dict[str, Any] = {
        "campaign": "pmus-q1-canary", "arm": "HOLD_OPEN_EXTEND",
        "slug": slug, "qty": X.QTY, "realized_pnl": None,
        "max_loss_cap": str(CANARY_MAX_LOSS),
        "labels": ("submitted->filled->settled->realized; fees and rebates "
                   "reported separately; realized null until the settlement "
                   "audit reconciles"),
    }
    if not canary_armed():
        return {**report, "status": "DISARMED",
                "note": f"triple key absent/mismatched ({CANARY_ARM_ENV} must "
                        f"equal {X.SID_ENV}); no network activity"}
    if not X.has_credentials():
        return {**report, "status": "NO_CREDENTIALS"}
    if not X.gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED"}
    if X.realized_today() <= X.DAILY_LOSS_STOP:
        return {**report, "status": "DAILY_LOSS_STOP"}

    t = t or CapturingTransport()
    ctx["t"] = t
    requested_s = float(os.getenv(HOLD_DEADLINE_ENV, "600"))
    stop_at = clock()          # set inside the try, so NO_RUNWAY still sweeps
    inv = Inventory()
    ctx["inv"] = inv
    #: what is resting right now: at most ONE order, per the mandate
    resting: Optional[dict] = None
    #: EVERY order id this campaign has placed and not POSITIVELY resolved.
    #: The take refuses while this is non-empty, because "resting is None" is
    #: not the same as "nothing of ours is live on the venue".
    unresolved: Dict[str, dict] = {}
    ctx["unresolved"] = unresolved
    holds = requotes = cancels = takes = 0
    first_cost: Optional[D] = None

    def _exit(status: str, **extra: Any) -> dict:
        """Record the OUTCOME only. Inventory numbers are deliberately NOT
        put here: they are assembled after `finalize`, because a report built
        before the sweep can claim residual 1 while the venue has just
        completed the pair during the shutdown race."""
        ctx.update(counters={"holds": holds, "requotes": requotes,
                             "cancels": cancels, "takes": takes},
                   first_cost=first_cost)
        return {"status": status, **extra}

    try:
        # The campaign must be finished BEFORE the window closes, with time
        # left to cancel and sweep. Railway's build time is spent inside the
        # window, so this is derived at startup rather than estimated when
        # the variables were set.
        deadline_s = effective_deadline_s(slug, requested_s)
        if deadline_s is None:
            return _exit("NO_RUNWAY",
                         note=f"no usable time before {slug} closes "
                              f"(reserving {SWEEP_BUFFER_S:.0f}s for "
                              f"cancel/sweep); placed nothing")
        stop_at = clock() + deadline_s
        # THE PRICING BOUNDARY MUST EXIST BEFORE ANYTHING PRICES. `preflight`
        # itself calls `fresh_bbo`, so attaching after it still halts an armed
        # run: the feed is resolved and attached here, ahead of the first
        # read, and the strategy loop below uses the SAME `state`.
        if state is None:
            feed = feed or WS.WsFeed(slug).start()
            state = feed.state
        X.attach_ws_feed(_StateBackedFeed(state, max_book_age_s))
        # STALENESS IS CLASSIFIED BEFORE PREFLIGHT, not swallowed by it.
        # `preflight` prices too, so a stale feed reached here first and came
        # out as a generic WS_BBO_UNAVAILABLE halt -- losing the distinct
        # WS_STALE_REFUSED status that says WHY nothing was placed. The
        # readiness wait owns that classification and runs ahead of it.
        ready = WS.wait_ready(state, slug, max_age_s=max_book_age_s,
                              sleep=sleep)
        reason = X.preflight(t, slug, sleep=sleep)
        if reason:
            return _exit("PREFLIGHT_FAILED", reason=reason)
        tick = _tick(t, slug, sleep)
        if tick is None:
            # Refuse BEFORE any order exists. A wrong grid makes the
            # complement unquotable, which would strand the opening leg.
            return _exit("TICK_UNAVAILABLE",
                         note="venue grid not served; placed nothing")
        # DEFECT 1: strategy decisions come from the authenticated WS only.
        record("ws_ready", **{k: str(v) for k, v in ready.items()})
        record("canary_start", slug=slug, tick=str(tick),
               requested_deadline_s=requested_s,
               effective_deadline_s=deadline_s,
               sweep_buffer_s=SWEEP_BUFFER_S,
               window_end=window_end_epoch(slug),
               max_loss=str(CANARY_MAX_LOSS),
               decision_feed="AUTHENTICATED_WS",
               rest_role="pre-submit cross check, order status, account only")

        while clock() < stop_at:
            # A stale feed REFUSES. There is deliberately no REST fallback
            # for a strategy decision: a cached book is not market state.
            snap = state.snapshot(slug, max_age_s=max_book_age_s)
            want_side = inv.deficient()

            # ---------------------------------------- 1 look at what rests
            if resting is not None:
                oid_now = resting["order_id"]
                v = reconcile(t, oid_now, resting, inv, sleep=sleep)
                if inv.qty["yes"] > 0 and first_cost is None:
                    first_cost = inv.basis("yes")
                if v["terminal"]:
                    # FILLED, or finished with a partial: either way it is off
                    # the venue and everything filled has been booked.
                    record("terminal", stage=resting["stage"], order_id=oid_now,
                           classification=v["classification"],
                           cumulative=str(v["filled_qty"] or 0),
                           residual=str(inv.residual()),
                           matched=str(inv.matched()), **_ws_fields(snap))
                    unresolved.pop(oid_now, None)
                    resting = None
                    continue
                if v["classification"] == LC.PARTIAL:
                    # rule 5: the REMAINDER keeps working, and its queue
                    # position with it.
                    record("partial_remainder_kept", stage=resting["stage"],
                           order_id=oid_now,
                           cumulative=str(v["filled_qty"]),
                           remaining=str(resting.get("remaining")))

                # ------------------------------ 2/4 hold, or give it up
                still = (open_still_valid(D(str(resting["px"])), snap)
                         if resting["stage"] == "open"
                         else complement_still_valid(
                             D(str(resting["px"])), first_cost, tick, snap))
                if still:
                    holds += 1
                    record("hold", stage=resting["stage"],
                           px=str(resting["px"]),
                           queue_depth_at_our_price=str(state.depth_at(
                               slug,
                               "bids" if resting["inv_side"] == "yes"
                               else "asks", D(str(resting["px"])))),
                           residual=str(inv.residual()),
                           note="valid; queue position preserved; a better "
                                "touch is not a reason to move",
                           **_ws_fields(snap))
                else:
                    oid = resting["order_id"]
                    res = cancel_and_resolve(t, oid, slug, resting, inv,
                                             sleep=sleep)
                    cancels += 1
                    record("cancel", stage=resting["stage"],
                           px=str(resting["px"]), order_id=oid,
                           resolution=res.get("state"),
                           confirmed_terminal=True,
                           reason="no_longer_economically_valid",
                           residual=str(inv.residual()), **_ws_fields(snap))
                    # cancel_and_resolve -> reconcile already booked any
                    # quantity that filled during the race, including a
                    # PARTIAL. Re-adding it here would double-count.
                    if inv.qty["yes"] > 0 and first_cost is None:
                        first_cost = inv.basis("yes")
                    # ONLY now, with a terminal state proven, is the order
                    # off the venue and safe to forget.
                    unresolved.pop(oid, None)
                    resting = None
                    requotes += 1
                    continue

            # ------------------------------------------- 6 reactive take
            if want_side is not None and first_cost is not None:
                # 1. contemporaneous EXECUTABLE price, from WS state
                ask = (snap.get("ask") if want_side == "yes"
                       else ((X.ONE - D(str(snap["bid"])))
                             if snap.get("bid") is not None else None))
                # 2. fee-adjusted pair-cost check
                if ask is not None and X.taker_completion_ok(first_cost,
                                                             D(str(ask))):
                    record("decision", action="taker_complete",
                           ask_cost=str(ask), first_cost=str(first_cost),
                           residual=str(inv.residual()),
                           reason="fee_adjusted_pair_cost_acceptable_now",
                           **_ws_fields(snap))
                    # 3. Resolve EVERY order this campaign has placed and not
                    #    positively resolved -- not merely `resting`. A cancel
                    #    the venue ACK'd but did not apply leaves a live maker
                    #    that `resting is None` cannot see, and crossing on top
                    #    of it is the double fill this guard exists to stop.
                    aborted = False
                    for oid in list(unresolved):
                        info = unresolved[oid]
                        before = D(str(info.get("booked_qty", 0)))
                        res = cancel_and_resolve(t, oid, slug, info, inv,
                                                 sleep=sleep)
                        gained = D(str(info.get("booked_qty", 0))) - before
                        record("cancel", stage=info.get("stage"), order_id=oid,
                               resolution=res.get("state"),
                               confirmed_terminal=True,
                               reason="pre_take_double_fill_guard",
                               booked_in_race=str(gained))
                        unresolved.pop(oid, None)
                        if resting is not None and \
                                resting["order_id"] == oid:
                            resting = None
                        if gained > 0:
                            # the maker filled during the race; reconcile has
                            # already booked it, so do NOT also cross.
                            aborted = True
                    if aborted and inv.deficient() is None:
                        record("decision", action="take_aborted",
                               reason="maker_filled_during_cancel_race",
                               residual=str(inv.residual()))
                        continue
                    # 4. durable intent then POST, inside place_order
                    side_wire = ("OUTCOME_SIDE_YES" if want_side == "yes"
                                 else "OUTCOME_SIDE_NO")
                    try:
                        tid = place_order(t, slug, side_wire, D(str(ask)),
                                          post_only=False,
                                          stage="taker_complete", sleep=sleep)
                        unresolved[tid] = {"stage": "taker_complete",
                                           "inv_side": want_side,
                                           "px": D(str(ask)),
                                           "qty": D(X.QTY),
                                           #: REQUIRED. reconcile() defaults a
                                           #: missing `maker` to True, so
                                           #: omitting this logged the crossing
                                           #: leg as a maker fill on
                                           #: 2026-09-30 and let the maker
                                           #: rebate be applied to it.
                                           "maker": False}
                    except X.Halt:
                        raise
                    takes += 1
                    # Resolve THAT order on POSITIVE PROOF, never on the
                    # legacy cancel predicate, and only then forget it.
                    # `resolve_ioc` raises CancelUnconfirmed if the venue
                    # will not prove terminality, and the campaign then exits
                    # fail-closed with the order still tracked for the sweep.
                    tinfo = unresolved.get(tid, {})
                    res = resolve_ioc(t, tid, slug, tinfo, inv, sleep=sleep)
                    filled_qty = D(str(tinfo.get("booked_qty", 0)))
                    if filled_qty == 0:
                        record("taker_unfilled", order_id=tid,
                               resolution=res.get("state"),
                               confirmed_terminal=True,
                               note="proven terminal with no fill; "
                                    "inventory unchanged")
                    else:
                        record("taker_filled", order_id=tid,
                               cumulative=str(filled_qty),
                               taker_fee_separate=str(
                                   X.taker_fee(D(str(ask)))),
                               residual=str(inv.residual()),
                               matched=str(inv.matched()))
                    unresolved.pop(tid, None)   # reached only on proof
                    break

            # --------------------------------------- place what is missing
            if resting is None:
                if want_side is None:
                    # flat and paired. An OPEN is allowed; an EXTEND is not.
                    if inv.matched() > 0:
                        record("decision", action="stop",
                               reason="paired_inventory_complete")
                        break
                    bid = snap.get("bid")
                    if bid is None or not X.entry_ok(D(str(bid))):
                        record("decision", action="wait",
                               reason="entry_out_of_band",
                               **_ws_fields(snap))
                        sleep(X.POLL_S)
                        continue
                    stage, side, px = "open", "yes", D(str(bid))
                else:
                    if want_side == "yes":
                        record("decision", action="stop",
                               reason="long_NO_residual_unsupported_at_q1")
                        break
                    bound = X.complement_bound(first_cost, tick=tick)
                    if bound is None:
                        record("decision", action="hold_one_sided",
                               reason="no_fee_safe_complement",
                               first_cost=str(first_cost))
                        sleep(X.POLL_S)
                        continue
                    stage, side, px = "complement", "no", bound
                projected = worst_case_loss(
                    inv, {"stage": stage, "px": px, "qty": D(X.QTY)})
                if projected > CANARY_MAX_LOSS:
                    record("decision", action="refuse_placement",
                           reason="worst_case_loss_cap",
                           projected_loss=str(projected),
                           cap=str(CANARY_MAX_LOSS))
                    break
                if unresolved:
                    raise X.Halt(
                        "UNRESOLVED_ORDERS_BEFORE_PLACEMENT:"
                        + ",".join(sorted(unresolved)))
                try:
                    oid = place_order(
                        t, slug,
                        "OUTCOME_SIDE_YES" if side == "yes"
                        else "OUTCOME_SIDE_NO",
                        px, post_only=True, stage=stage, sleep=sleep)
                except X.StaleRefuse as stale:
                    record("stale_refused", stage=stage, reason=str(stale))
                    sleep(X.POLL_S)
                    continue
                resting = {"order_id": oid, "stage": stage, "px": px,
                           "inv_side": side, "qty": D(X.QTY), "maker": True}
                unresolved[oid] = dict(resting)
                record("resting", stage=stage, px=str(px), order_id=oid,
                       residual=str(inv.residual()), **_ws_fields(snap))
            sleep(X.POLL_S)

        # The status is decided from POST-SWEEP inventory by the caller, so
        # this only says the campaign reached its end without refusing.
        return _exit("COMPLETED")
    except CancelUnconfirmed as unc:
        record("cancel_unconfirmed", reason=str(unc),
               note="an order may still be live; placed nothing further")
        return _exit("CANCEL_UNCONFIRMED", reason=str(unc))
    except WS.StaleFeed as stale:
        record("ws_stale_refused", reason=str(stale),
               note="no REST fallback for a strategy decision")
        return _exit("WS_STALE_REFUSED", reason=str(stale))
    except X.StaleRefuse as stale:
        record("stale_refused", reason=str(stale))
        return _exit("STALE_PRICE_REFUSED", reason=str(stale))
    except X.Halt as halt:
        record("halt", reason=str(halt))
        return _exit("HALT", reason=str(halt))
    except Exception as exc:                                 # noqa: BLE001
        # LIVE 2026-09-30: an unhandled urllib HTTPError from the preview
        # endpoint escaped every handler, so the campaign died by traceback
        # instead of returning its own report. Any venue/transport failure is
        # a HALT with the sanitized venue text, and the sweep still runs.
        detail = getattr(exc, "venue_body", "") or f"{type(exc).__name__}: {exc}"
        record("halt", reason=f"UNEXPECTED:{str(detail)[:300]}")
        return _exit("HALT", reason=f"UNEXPECTED:{type(exc).__name__}",
                     venue=str(detail)[:500])



def run_canary(t: Optional[X.Transport] = None, clock=time.monotonic,
               sleep=time.sleep, state: Optional["WS.LiveState"] = None,
               feed: Optional["WS.WsFeed"] = None,
               max_book_age_s: float = WS.DEFAULT_MAX_BOOK_AGE_S) -> dict:
    """Run the campaign, FINALIZE, then report post-sweep authoritative state.

    The report is assembled last, deliberately. A report built before the
    sweep can say residual 1 / matched 0 while the venue completed the pair
    during the shutdown cancel race, and `sweep` used to be logged as success
    merely because the cancel request had been sent.
    """
    slug = os.getenv(X.MARKET_ENV, "").strip()
    report: dict[str, Any] = {
        "campaign": "pmus-q1-canary", "arm": "HOLD_OPEN_EXTEND",
        "slug": slug, "qty": X.QTY, "realized_pnl": None,
        "max_loss_cap": str(CANARY_MAX_LOSS),
        "labels": ("submitted->filled->settled->realized; fees and rebates "
                   "reported separately; realized null until the settlement "
                   "audit reconciles"),
    }
    ctx: dict = {}
    del SUBMITTED_LEGS[:]
    del FILLED_LEGS[:]
    try:
        outcome = _campaign(ctx, t, clock=clock, sleep=sleep, state=state,
                            feed=feed, max_book_age_s=max_book_age_s)
    finally:
        if feed is not None:
            feed.close()
    inv = ctx.get("inv")
    if inv is None:                      # refused before any campaign state
        return {**report, **outcome}
    tt, unresolved = ctx["t"], ctx.get("unresolved") or {}
    fin = finalize(tt, slug, inv, unresolved, sleep=sleep)

    matched = inv.matched()
    pair_cost = ((inv.cost["yes"] + inv.cost["no"]) / matched
                 if matched > 0 else None)
    status = outcome.get("status")
    if status == "COMPLETED":
        status = ("MATCHED" if matched > 0 else
                  "ONE_SIDED_HOLD" if inv.residual() != 0 else "NO_FILL")
    #: An unproven sweep overrides a CLEAN-looking outcome, because an order
    #: that may still be live is the most important fact about the run. It
    #: does NOT overwrite a status that already says the campaign refused --
    #: CANCEL_UNCONFIRMED names the specific order and is strictly more
    #: informative than the generic label; `sweep_verified` is reported
    #: alongside either way.
    CLEAN = ("COMPLETED", "MATCHED", "ONE_SIDED_HOLD", "NO_FILL")
    if not fin["sweep_verified"] and status in CLEAN:
        status = "SWEEP_UNVERIFIED"
    counters = ctx.get("counters") or {}
    #: EXECUTED legs, not submitted ones: the venue charges on the fill price.
    ctx["maker_legs"] = [lg for lg in FILLED_LEGS if lg["maker"]]
    ctx["taker_legs"] = [lg for lg in FILLED_LEGS if not lg["maker"]]
    ctx["submitted_legs"] = list(SUBMITTED_LEGS)
    out = {**report, **{k: v for k, v in outcome.items() if k != "status"},
           "status": status,
           "matched_contracts": str(matched),
           "residual": str(inv.residual()),
           "pair_cost": (str(pair_cost) if pair_cost is not None else None),
           "first_cost": (str(ctx.get("first_cost"))
                          if ctx.get("first_cost") is not None else None),
           "holds": counters.get("holds", 0),
           "requotes": counters.get("requotes", 0),
           "cancels": counters.get("cancels", 0),
           "takes": counters.get("takes", 0),
           #: MAKER LEGS ONLY. This used to sum maker_rebate over every side
           #: holding inventory, which credited the crossing leg with a maker
           #: rebate it can never earn. `maker_legs` carries what we actually
           #: submitted, from our own record, not from the venue's reply.
           "modeled_maker_rebate_bound": str(
               sum((FEES.maker_rebate(D(str(lg["px"])), D(str(lg["qty"])))
                    for lg in ctx.get("maker_legs") or []), D(0))),
           "modeled_maker_rebate_cashflow": str(
               sum((FEES.maker_rebate(D(str(lg["px"])), D(str(lg["qty"])),
                                      cents=True)
                    for lg in ctx.get("maker_legs") or []), D(0))),
           "modeled_taker_fee_bound": str(
               sum((FEES.taker_fee(D(str(lg["px"])), D(str(lg["qty"])))
                    for lg in ctx.get("taker_legs") or []), D(0))),
           "modeled_taker_fee_cashflow": str(
               sum((FEES.taker_fee(D(str(lg["px"])), D(str(lg["qty"])),
                                   cents=True)
                    for lg in ctx.get("taker_legs") or []), D(0))),
           "fee_note": ("bound figures are unrounded per-contract economics; "
                        "cashflow figures apply the venue's cent rounding to "
                        "the WHOLE order quantity. Venue commission is "
                        "authoritative after execution."),
           "sweep_verified": fin["sweep_verified"],
           "open_orders_after_sweep": fin["open_orders"],
           "open_order_ids": fin["open_order_ids"],
           "unresolved_orders": fin["unresolved_orders"],
           "booked_during_shutdown": fin["booked_during_shutdown"],
           "sweep_errors": fin["sweep_errors"],
           "settlement": "PENDING — realized only after settlement rows "
                         "reconcile"}
    record("canary_end", **{k: v for k, v in out.items() if k != "labels"})
    return out


def main() -> int:
    out = run_canary()
    print("CANARY_REPORT " + json.dumps(out, default=str), flush=True)
    print(json.dumps(out, indent=1, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
