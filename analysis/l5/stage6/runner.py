"""The Stage-6 runner. **DISARMED. NOT AUTHORIZED. NOT DEPLOYED.**

    This module has never placed an order. None of the four PMUS_S6P_*
    arming variables is set anywhere in this repository, no owner value
    appears in it, and nothing order-capable from this package is deployed
    to Railway. Run as committed, `main()` reaches the DISARMED refusal and
    stops before any order mutation.

WHAT RUNNING IT AS COMMITTED ACTUALLY DOES. It asserts the frozen spec, the
frozen fee schedule and the bound coherence; prints the fingerprint, the
diff against XR2W, every bound with its dollar value, the arming state and
the latch state; and then refuses. It makes no POST of any kind, because the
arming gate is evaluated BEFORE the first leg's preflight returns and the
machine terminates DISARMED without touching the adapter.

THE LIVE ADAPTER IS WRITTEN BUT NOT REACHED. `LiveVenueAdapter` delegates
every mutation to `analysis.l5.live_exec2.orders` -- the only order-mutation
path in the repository, with its four permitted routes -- and every order
body to `src.l5_order_body`, the proven serializer. It adds no transport, no
route and no serializer of its own. It is written here rather than left to
be written later precisely so the owner reviews the real code tomorrow and
not a sketch of it.

WHAT THE OWNER WOULD BE AUTHORIZING IF THEY SAID YES. See
`spec.DIFF["authorization"]["what_the_owner_would_be_authorizing"]` and
`analysis/l5/stage6/STAGE6_PROTOCOL.md`. In one sentence: one post-only
first leg of at most 1.00 share at a price at or below $0.60 in one named
btc-updown-15m window, then at most one post-only complement sized to the
first leg's ACTUAL fill at a price keeping the fee-safe pair cost at or
below $0.96, a cancel of whatever rests at TTE 120 s, no repeat, maximum
arithmetic loss $0.60.

MONEY-MOVING SESSIONS FAIL CLOSED. Per CLAUDE.md, this runner does not
relaunch itself after a crash or a completion, has no retry loop, and has no
supervisor. Read-only collectors self-recover; this is not one.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import timing as T
from analysis.l5.stage6 import bounds as B
from analysis.l5.stage6 import machine as M
from analysis.l5.stage6 import pair_latch as PL
from analysis.l5.stage6 import spec as S


def frozen_slug() -> Optional[str]:
    """The ONE market, from the owner's gate evidence. No default, because
    a default is a substitution and a substitution is a different
    experiment."""
    return os.getenv("PMUS_S6P_GATE_EVIDENCE", "").strip() or None


class LiveVenueAdapter(M.VenueAdapter):
    """The real adapter. Every mutation delegated; nothing re-implemented.

    NOT REACHED while disarmed: `PairMachine` terminates DISARMED inside
    the first leg's preflight, which runs before `place` is called.
    """

    def __init__(self, *, slug: str, window_start_unix: Optional[float],
                 session_id: Optional[str] = None) -> None:
        self.slug = slug
        self.window_start_unix = window_start_unix
        self.session_id = session_id
        self._t = O.BoundedOrderTransport()

    # ----------------------------------------------------------- read-only
    def now(self) -> float:
        return time.time()

    def tte_s(self) -> Optional[float]:
        return T.tte_s(self.window_start_unix, self.now())

    def book(self) -> Dict[str, Any]:
        """The authenticated book, through the already-proven reader."""
        from src import l5_pmus_auth_book as AB
        snap = AB.best_bid_ask(self.slug)
        return {"bid": snap.get("bid"), "ask": snap.get("ask"),
                "book_t": snap.get("t")}

    def executions(self, order_id: str) -> List[Dict[str, Any]]:
        """Our own order's executions. GET only.

        Liquidity role is read downstream from `execution.isAggressor`
        only; nothing here consults a bare `aggressor` field.
        """
        if not order_id:
            return []
        obj = self._t.request("GET", "/v1/order/%s" % order_id) or {}
        out: List[Dict[str, Any]] = []
        for key in ("executions", "trades", "fills"):
            v = obj.get(key)
            if isinstance(v, list):
                out.extend([e for e in v if isinstance(e, dict)])
        return out

    # ----------------------------------------------------------- mutations
    def place(self, *, leg: str, side: str, price: Any,
              qty: Any) -> Dict[str, Any]:
        """Preview -> echo -> guard -> THIS LEG's latch slot -> one POST.

        `orders.submit_one` carries the preview, the echo requirement, the
        two guard calls around latch consumption and the single POST. The
        only thing supplied here is WHICH latch slot pays for it, via a
        latch shim that routes `live_exec2.latch.consume` to this probe's
        two-slot latch -- so the proven submission path is reused verbatim
        rather than copied.
        """
        guard = self._tte_guard(leg)
        with _slot_latch(leg, session_id=self.session_id):
            return O.submit_one(self._t, slug=self.slug, outcome_side=side,
                                price=price, qty=qty,
                                checks={"ok": True}, guard=guard)

    def cancel(self, order_id: str) -> Dict[str, Any]:
        try:
            return {"ok": True,
                    "response": O.cancel_exact(self._t, order_id=order_id,
                                               slug=self.slug)}
        except BaseException as exc:                        # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__,
                    "NOTE": "a failed cancel escalates to the deadman"}

    def deadman(self) -> Dict[str, Any]:
        """Market-scoped cancel-all. Not gated on arming or on the latch --
        refusing to cancel because a flag is unset is the dangerous
        direction. Never retried."""
        return O.deadman_cancel_all(self._t, slug=self.slug)

    # --------------------------------------------------------------- guard
    def _tte_guard(self, leg: str):
        """Re-evaluated immediately before latch consumption and again
        immediately before the POST, inherited from XR2. Unknown TTE counts
        as past the boundary."""
        floor = (B.COMPLEMENT_HANDOVER_TTE_S if leg == "FIRST_LEG"
                 else B.TTE_CUTOFF_S)

        def guard() -> Dict[str, Any]:
            tte = self.tte_s()
            if tte is None:
                return {"ok": False,
                        "reason": "TTE_UNKNOWN counts as past the boundary"}
            if tte <= B.TTE_CUTOFF_S:
                return {"ok": False,
                        "reason": "TTE %.3fs <= the %.1fs placement cutoff"
                                  % (tte, B.TTE_CUTOFF_S)}
            if tte <= floor:
                return {"ok": False,
                        "reason": "TTE %.3fs <= the %.1fs floor for %s"
                                  % (tte, floor, leg)}
            return {"ok": True, "tte_s": round(tte, 3)}

        return guard


class _slot_latch:
    """Route `live_exec2.latch.consume` to this probe's named slot.

    `orders.submit_one` is the proven submission path and consumes "the"
    latch between the echo check and the POST. Stage-6 has two named slots,
    so rather than fork `submit_one` -- which would be a second submission
    path, the same mistake class as a second serializer -- the consume call
    is routed to the right slot for the duration of exactly one `place`.

    The substitution is narrow and loud: it is scoped to one call, it is
    restored in `finally`, and the slot it consumes is recorded in the
    latch file itself.
    """

    def __init__(self, leg: str, *, session_id: Optional[str] = None,
                 latch_dir: Optional[str] = None) -> None:
        self.leg = PL._check_slot(leg)
        self.session_id = session_id
        self.latch_dir = latch_dir
        self._saved = None

    def __enter__(self) -> "_slot_latch":
        from analysis.l5.live_exec2 import latch as L1
        self._saved = L1.consume
        leg, sid, ld = self.leg, self.session_id, self.latch_dir

        def _consume(*, slug: str, side: str, price: Any,
                     qty: Any, session_id: Optional[str] = None
                     ) -> Dict[str, Any]:
            return PL.consume(leg, slug=slug, side=side, price=price,
                              qty=qty, session_id=sid or session_id,
                              latch_dir=ld)

        L1.consume = _consume
        return self

    def __exit__(self, *exc: Any) -> bool:
        from analysis.l5.live_exec2 import latch as L1
        if self._saved is not None:
            L1.consume = self._saved
        return False


def preflight_report() -> Dict[str, Any]:
    """Everything a reviewer needs, computed without touching the network."""
    S.assert_frozen()
    slug = frozen_slug()
    from analysis.l5.stage6 import pair_economics as PE
    return {
        "probe": S.PROBE,
        "HEADER": "NOT AUTHORIZED / DISARMED",
        "spec_fingerprint": S.S6P_FINGERPRINT,
        "fee_schedule_fingerprint": PE.FS.FINGERPRINT,
        "parent": {"probe": "LIVE-L5-EXEC-1-XR2W",
                   "fingerprint": S.XR2W_FINGERPRINT,
                   "modified_by_this_build": False},
        "frozen_slug": slug,
        "arming": S.may_submit(slug or ""),
        "fingerprint_pin": S.fingerprint_pinned(),
        "latch": PL.state(),
        "bounds": B.summary(),
        "terminals": list(S.TERMINALS),
        "cannot_establish": list(S.CANNOT_ESTABLISH),
        "pair_cost_headroom": PE.headroom_report(),
        "diff_vs_parent": S.diff_report(),
        "what_the_owner_would_be_authorizing":
            S.SPEC["authorization"]
            ["what_the_owner_would_be_authorizing"],
    }


def run_probe(adapter: M.VenueAdapter, *, slug: str, first_side: str,
              first_price: Any,
              session_id: Optional[str] = None,
              latch_dir: Optional[str] = None) -> Dict[str, Any]:
    """One pass of the machine. No repeat, no supervisor, no relaunch."""
    mach = M.PairMachine(adapter, slug=slug, first_side=first_side,
                         first_price=first_price, session_id=session_id,
                         latch_dir=latch_dir)
    return mach.run()


def main(argv: Optional[List[str]] = None) -> int:
    """DISARMED entrypoint. Prints the frozen protocol state and refuses."""
    rep = preflight_report()
    print(json.dumps(rep, indent=2, sort_keys=True, default=str))
    arming = rep["arming"]
    if not arming["may_submit"]:
        print("\nSTAGE-6-S6P: DISARMED. " + str(arming["refusal"]))
        print("No order was previewed, submitted, or cancelled. "
              "No latch slot was consumed.")
        return 0
    # Unreachable as committed. Left as the single place where a live run
    # would begin, so the owner can see exactly what arming turns on.
    print("\nSTAGE-6-S6P: ARMED. This path is not exercised in this commit.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
