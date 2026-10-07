"""The Stage-6 state machine. Pure decisions; every effect goes through an
injected adapter, so the whole machine runs offline against fakes.

WHY AN ADAPTER AND NOT A TRANSPORT. The previous probes put the HTTP calls
inside the runner, which meant the only way to exercise the decision path
was to arm it. That is the wrong way round for the central thing under test:
partial-fill accounting is exactly what a one-shot live run observes WORST,
because the venue decides how much fills and we only get one sample. So the
machine takes an adapter with six methods and knows nothing about HTTP. The
live adapter in `runner.py` wires `analysis.l5.live_exec2.orders`; the test
adapter in `tests/test_stage6_matched_pair.py` wires fakes and can produce
any partial-fill sequence we need to assert against.

THE STATE MACHINE

    START
      |
      v
    ARMING_GATE ---------- not armed -----------> DISARMED            [T]
      |  armed + gated + fingerprint-pinned
      v
    PREFLIGHT ------------ any local refusal ---> REFUSED_PREFLIGHT   [T]
      |  TTE > 120 and TTE > 510 (first leg's turn), book two-sided,
      |  price on tick, would not cross, FIRST_LEG latch unspent
      v
    PLACE_FIRST_LEG  (preview -> echo -> guard -> latch -> POST)
      |                 |                      |
      |                 +-- PreviewRejected --> PREVIEW_REJECTED      [T]
      |                 +-- SubmissionAmbig. -> AMBIGUOUS_SUBMISSION  [T]
      |                 +-- anything else ----> UNCLASSIFIED          [T]
      v
    OBSERVE_FIRST  (poll; inventory.apply_fill ONCE PER EXECUTION;
      |             bounds re-checked after every single fill)
      |
      +-- filled in full -----------------+
      +-- TTE <= 510 (handover) ----------+
      |                                   v
      |                            CANCEL_FIRST_LEG
      |                                   |
      |            filled_qty == 0 -------+--> NO_FIRST_LEG_FILL      [T]
      |                                   |
      v                                   v
    PAIR_GATE  (max_complement_price from the ACTUAL fill VWAP)
      |                 |
      |                 +-- no grid price clears $0.96 -->
      |                        REFUSED_PAIR_COST                      [T]
      v
    PLACE_COMPLEMENT  (qty = inventory.complement_qty(), which reads
      |                filled_qty and nothing else)
      |                 +-- PreviewRejected --> PREVIEW_REJECTED      [T]
      |                 +-- SubmissionAmbig. -> AMBIGUOUS_SUBMISSION  [T]
      |                 +-- anything else ----> UNCLASSIFIED          [T]
      v
    OBSERVE_COMPLEMENT  (poll; patient -- no timer forces completion)
      |
      +-- complement filled == first filled --> PAIR_COMPLETE         [T]
      +-- TTE <= 120 ------------------------+
                                             v
                                       WITHDRAW (cancel_exact, then the
                                         market-scoped deadman in `finally`)
                                             |
                           residual > 0 and some matched -->
                                  PAIR_PARTIAL_RESIDUAL_BOUNDED       [T]
                           residual > 0 and none matched -->
                                  RESIDUAL_BOUNDED_DEADMAN            [T]

    Every [T] terminal then goes through RECONCILE
    (`settlement.reconcile`), which labels nothing REALIZED without
    confirmed settlement cash and a source identity.

TWO RULES THAT ARE NOT NEGOTIABLE IN THIS FILE

  1. `_place_complement` computes its quantity from
     `inventory.complement_qty()`. There is no code path in this module in
     which the complement's quantity is derived from the first leg's
     INTENDED quantity, and the offline suite asserts it with a 0.37/1.00
     partial fill.
  2. On `AMBIGUOUS_SUBMISSION` the machine stops. It does not re-send, does
     not place the other leg, and does not cancel-by-id (we may not have an
     id). It runs the market-scoped deadman and records that venue state
     must be reconciled by reading open orders and executions.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Optional

from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import timing as T
from analysis.l5.stage6 import bounds as B
from analysis.l5.stage6 import inventory as INV
from analysis.l5.stage6 import pair_economics as PE
from analysis.l5.stage6 import pair_latch as PL
from analysis.l5.stage6 import settlement as SE
from analysis.l5.stage6 import spec as S

ZERO = D("0")


class VenueAdapter:
    """What the machine is allowed to do to the world. Six methods.

    The live implementation lives in `runner.py` and delegates every
    mutation to `analysis.l5.live_exec2.orders`, the only order-mutation
    path. No implementation may add a seventh method: a wider adapter is a
    wider blast radius.
    """

    def now(self) -> float:                                 # pragma: no cover
        raise NotImplementedError

    def tte_s(self) -> Optional[float]:                     # pragma: no cover
        """Seconds to window end, or None. None counts as PAST the
        boundary everywhere it is used."""
        raise NotImplementedError

    def book(self) -> Dict[str, Any]:                       # pragma: no cover
        """{'bid':..,'ask':..,'book_t':..} from the authenticated book."""
        raise NotImplementedError

    def place(self, *, leg: str, side: str, price: Any,
              qty: Any) -> Dict[str, Any]:                  # pragma: no cover
        """Preview, echo-check, consume THAT LEG's latch slot, POST once.

        Raises `orders.PreviewRejected`, `orders.SubmissionAmbiguous`, or
        another exception. Returns {'order_id': ...} on success.
        """
        raise NotImplementedError

    def executions(self, order_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError                           # pragma: no cover

    def cancel(self, order_id: str) -> Dict[str, Any]:      # pragma: no cover
        raise NotImplementedError

    def deadman(self) -> Dict[str, Any]:                    # pragma: no cover
        raise NotImplementedError


def classify_exception(exc: BaseException) -> str:
    """Terminal class from the EXCEPTION TYPE, never from the latch.

    A preview is a what-if that creates nothing and runs strictly before
    latch consumption; only a failure at or after `POST /v1/orders` is
    genuinely ambiguous. An exception naming neither phase is UNCLASSIFIED
    and is never resolved by guess.
    """
    if isinstance(exc, O.PreviewRejected):
        return "PREVIEW_REJECTED"
    if isinstance(exc, O.SubmissionAmbiguous):
        return "AMBIGUOUS_SUBMISSION"
    if isinstance(exc, (PL.LatchSpent, PL.LatchOutOfOrder,
                        PL.LatchUnavailable, O.MutationRefused,
                        O.GuardRefused)):
        return "REFUSED_PREFLIGHT"
    return "UNCLASSIFIED"


class PairMachine:
    """One market, two sequential post-only legs, one pass. No repeat."""

    def __init__(self, adapter: VenueAdapter, *, slug: str,
                 first_side: str, first_price: Any,
                 session_id: Optional[str] = None,
                 latch_dir: Optional[str] = None,
                 poll: Optional[Callable[[], None]] = None) -> None:
        S.assert_frozen()
        self.a = adapter
        self.slug = slug
        self.session_id = session_id
        self.latch_dir = latch_dir
        self.poll = poll or (lambda: None)
        self.inv = INV.Inventory(slug=slug, first_side=first_side,
                                 first_price=first_price)
        self.events: List[Dict[str, Any]] = []
        self.terminal: Optional[str] = None
        self.terminal_detail: Dict[str, Any] = {}
        self.deadman_result: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------- record
    def _ev(self, state: str, **kw: Any) -> None:
        self.events.append({"state": state, "tte_s": self.a.tte_s(), **kw})

    def _finish(self, terminal: str, **detail: Any) -> str:
        if terminal not in S.TERMINALS:
            raise AssertionError("not a declared terminal: %r" % terminal)
        self.terminal = terminal
        self.terminal_detail = detail
        self._ev("TERMINAL", terminal=terminal, **detail)
        return terminal

    # -------------------------------------------------------------- gates
    def arming_gate(self) -> Dict[str, Any]:
        return S.may_submit(self.slug)

    def preflight(self, *, for_leg: str, price: Any,
                  qty: Any) -> Dict[str, Any]:
        """Every local refusal, together, before any network call."""
        S.assert_frozen()
        refusals: List[str] = []
        gate = self.arming_gate()
        if not gate["may_submit"]:
            refusals.append("DISARMED")
        tte = self.a.tte_s()
        pl = T.placement_allowed(tte)
        if not pl["allowed"]:
            refusals.append("TTE:%s" % pl["reason"])
        if for_leg == "FIRST_LEG" and (
                tte is None or tte <= B.COMPLEMENT_HANDOVER_TTE_S):
            refusals.append(
                "PAST_FIRST_LEG_HANDOVER: the complement's half of the "
                "window has begun; a first leg placed now would leave the "
                "complement no patience")
        bk = self.a.book() or {}
        bid, ask = bk.get("bid"), bk.get("ask")
        if bid is None or ask is None:
            refusals.append("BOOK_NOT_TWO_SIDED")
        p = D(str(price))
        if (p / B.VENUE_TICK) % 1 != 0:
            refusals.append("PRICE_OFF_TICK:%s" % p)
        if not (B.VENUE_MIN_PRICE <= p <= B.VENUE_MAX_PRICE):
            refusals.append("PRICE_OFF_VENUE_RANGE:%s" % p)
        if ask is not None and p >= D(str(ask)):
            # post-only must not be able to take
            refusals.append("WOULD_CROSS:%s>=%s" % (p, ask))
        if D(str(qty)) <= 0:
            refusals.append("NON_POSITIVE_QTY:%s" % qty)
        if for_leg == "FIRST_LEG" and D(str(qty)) != B.FIRST_LEG_QTY:
            refusals.append("FIRST_LEG_QTY_NOT_FROZEN:%s" % qty)
        if for_leg == "FIRST_LEG" and p > B.MAX_FIRST_LEG_PRICE:
            refusals.append("FIRST_LEG_PRICE_ABOVE_CEILING:%s" % p)
        slot = PL.slot_state(for_leg, self.session_id, self.latch_dir)
        if slot.get("spent"):
            refusals.append("LATCH_SLOT_SPENT:%s" % for_leg)
        chk = self.inv.bound_check()
        if not chk["ok"]:
            refusals.extend(chk["breaches"])
        return {"ok": not refusals, "refusals": refusals, "arming": gate,
                "tte": pl, "latch_slot": slot,
                "probe": S.PROBE, "spec_fingerprint": S.S6P_FINGERPRINT}

    # ---------------------------------------------------------- the fills
    def _ingest(self, leg_name: str, order_id: str) -> Dict[str, Any]:
        """Poll once and count EVERY execution, one at a time.

        Bounds are re-checked inside `Inventory.apply_fill` after each
        single execution, so a bound cannot be crossed and uncrossed inside
        one poll batch without being seen.
        """
        counted, skipped, refused = 0, 0, []
        for ex in (self.a.executions(order_id) or []):
            try:
                out = self.inv.apply_fill(leg_name, ex)
            except INV.UncountableFill as exc:
                refused.append(str(exc))
                continue
            if out.get("counted"):
                counted += 1
            else:
                skipped += 1
        return {"counted": counted, "duplicates_skipped": skipped,
                "refused_uncountable": refused}

    # --------------------------------------------------------------- legs
    def _place(self, leg_name: str, *, side: str, price: Any,
               qty: Any) -> Dict[str, Any]:
        pre = self.preflight(for_leg=leg_name, price=price, qty=qty)
        self._ev("PREFLIGHT_%s" % leg_name, preflight=pre)
        if not pre["ok"]:
            if "DISARMED" in pre["refusals"]:
                raise _Terminal("DISARMED", preflight=pre)
            raise _Terminal("REFUSED_PREFLIGHT", preflight=pre)
        self._ev("PLACE_%s" % leg_name, side=side, price=str(price),
                 qty=str(qty), post_only=True)
        return self.a.place(leg=leg_name, side=side, price=price, qty=qty)

    def _observe(self, leg_name: str, order_id: str, *,
                 until_tte_s: float,
                 target_qty: D) -> Dict[str, Any]:
        """Poll until the leg is filled to `target_qty` or the TTE boundary.

        The boundary is a CLOCK boundary derived from the window, not a
        timer on our order: see `bounds.COMPLEMENT_HANDOVER_TTE_S`.
        """
        leg = self.inv.first if leg_name == "FIRST_LEG" else \
            self.inv.complement
        rounds = 0
        while True:
            rounds += 1
            ing = self._ingest(leg_name, order_id)
            self._ev("OBSERVE_%s" % leg_name, round=rounds, ingest=ing,
                     filled_qty=str(leg.filled_qty),
                     inventory=self.inv.snapshot())
            if leg.filled_qty >= target_qty:
                return {"reason": "FILLED_TO_TARGET", "rounds": rounds}
            tte = self.a.tte_s()
            if tte is None or tte <= until_tte_s:
                return {"reason": ("TTE_UNKNOWN" if tte is None
                                   else "TTE_BOUNDARY"),
                        "rounds": rounds, "tte_s": tte}
            self.poll()

    # ---------------------------------------------------------------- run
    def run(self) -> Dict[str, Any]:
        """One pass. Always returns a record; never raises past this point
        except for a programming error, and always runs the deadman."""
        S.assert_frozen()
        try:
            self._run_inner()
        except _Terminal as t:
            self._finish(t.terminal, **t.detail)
        except (INV.InventoryBoundBreached,) as exc:
            self._finish("REFUSED_PREFLIGHT",
                         bound_breach=str(exc),
                         note="FAIL CLOSED on a bound breach")
        except BaseException as exc:                        # noqa: BLE001
            self._finish(classify_exception(exc),
                         error=type(exc).__name__, message=str(exc)[:600],
                         detail=getattr(exc, "detail", None))
        finally:
            # the market-scoped deadman runs whatever happened, and is
            # never retried. Cancelling is how we stop risk, so it is not
            # gated on arming or on the latch.
            if self.terminal != "DISARMED":
                try:
                    self.deadman_result = self.a.deadman()
                except BaseException as exc:                # noqa: BLE001
                    self.deadman_result = {"ok": False,
                                           "error": type(exc).__name__,
                                           "NOTE": "never retried"}
                self._ev("DEADMAN", result=self.deadman_result)
            else:
                self.deadman_result = {
                    "skipped": True,
                    "why": ("DISARMED: nothing was ever placed, so there is "
                            "nothing to cancel and no POST is made")}
        return self.record()

    def _run_inner(self) -> None:
        # ---- FIRST LEG
        placed = self._place("FIRST_LEG", side=self.inv.first.side,
                             price=self.inv.first.price,
                             qty=self.inv.first.intended_qty)
        self.inv.first.order_id = placed.get("order_id")
        self._observe("FIRST_LEG", self.inv.first.order_id,
                      until_tte_s=B.COMPLEMENT_HANDOVER_TTE_S,
                      target_qty=self.inv.first.intended_qty)
        # the first leg's turn is over; free the single outstanding-order
        # slot before the complement is quoted.
        if self.inv.first.filled_qty < self.inv.first.intended_qty:
            self._ev("CANCEL_FIRST_LEG",
                     result=self.a.cancel(self.inv.first.order_id))
            self.inv.first.cancelled = True
            # a cancel can race a fill: poll once more and count anything
            # that landed before the cancel took effect.
            self._ev("POST_CANCEL_SWEEP_FIRST_LEG",
                     ingest=self._ingest("FIRST_LEG",
                                         self.inv.first.order_id),
                     inventory=self.inv.snapshot())
        if self.inv.first.filled_qty <= 0:
            raise _Terminal("NO_FIRST_LEG_FILL",
                            inventory=self.inv.snapshot(),
                            note=("the cancellation path was exercised and "
                                  "nothing else was. This is evidence "
                                  "about mechanism, not about "
                                  "profitability."))

        # ---- PAIR GATE, from the ACTUAL fill
        vwap = self.inv.first.vwap()
        cap = PE.max_complement_price(vwap)
        self._ev("PAIR_GATE", first_fill_vwap=str(vwap), ceiling=cap)
        if not cap["feasible"]:
            raise _Terminal("REFUSED_PAIR_COST", ceiling=cap,
                            inventory=self.inv.snapshot())
        comp_price = D(cap["max_complement_price"])
        gate = PE.pair_gate(vwap, comp_price)
        if not gate["ok"]:
            raise _Terminal("REFUSED_PAIR_COST", gate=gate,
                            inventory=self.inv.snapshot())

        # ---- COMPLEMENT. Quantity from the ACTUAL fill, never intended.
        comp = self.inv.open_complement(price=comp_price)
        placed2 = self._place("COMPLEMENT", side=comp.side,
                              price=comp.price, qty=comp.intended_qty)
        comp.order_id = placed2.get("order_id")
        self._observe("COMPLEMENT", comp.order_id,
                      until_tte_s=B.TTE_CUTOFF_S,
                      target_qty=comp.intended_qty)
        if comp.filled_qty < comp.intended_qty:
            self._ev("CANCEL_COMPLEMENT",
                     result=self.a.cancel(comp.order_id))
            comp.cancelled = True
            self._ev("POST_CANCEL_SWEEP_COMPLEMENT",
                     ingest=self._ingest("COMPLEMENT", comp.order_id),
                     inventory=self.inv.snapshot())

        if self.inv.residual_qty() <= 0:
            raise _Terminal("PAIR_COMPLETE", inventory=self.inv.snapshot())
        if self.inv.matched_qty() > 0:
            raise _Terminal("PAIR_PARTIAL_RESIDUAL_BOUNDED",
                            inventory=self.inv.snapshot())
        raise _Terminal("RESIDUAL_BOUNDED_DEADMAN",
                        inventory=self.inv.snapshot(),
                        note=("the complement never filled. The residual is "
                              "within bounds and was NOT flattened on a "
                              "timer; no predictive skill is claimed for "
                              "it."))

    # ------------------------------------------------------------- record
    def record(self, settlement: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
        return {
            "probe": S.PROBE,
            "spec_fingerprint": S.S6P_FINGERPRINT,
            "fee_schedule_fingerprint": PE.FS.FINGERPRINT,
            "slug": self.slug,
            "terminal": self.terminal,
            "terminal_detail": self.terminal_detail,
            "terminal_is_declared": self.terminal in S.TERMINALS,
            "crossed_order_boundary": (
                self.terminal == "AMBIGUOUS_SUBMISSION"
                or (self.inv.first.order_id is not None)),
            "retry_performed": False,
            "deadman": self.deadman_result,
            "events": self.events,
            "reconciliation": SE.reconcile(self.inv, settlement=settlement),
            "LANGUAGE": ("SUBMITTED is not a fill; FILLED is not REALIZED. "
                         "Nothing here is realized profit without confirmed "
                         "settlement cash and a source identity."),
        }


class _Terminal(Exception):
    """Internal: carry a declared terminal out of `_run_inner`."""

    def __init__(self, terminal: str, **detail: Any) -> None:
        super().__init__(terminal)
        self.terminal = terminal
        self.detail = detail
