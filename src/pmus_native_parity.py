"""Replay the live decision module against the frozen tape and reconcile.

THE ACCEPTANCE TEST. `NativeEngine` is a port of `run_window`'s decision rules;
this drives it over the SAME 25-window tape with a simulated venue adapter and
compares its settled economics, window by window, against the frozen scorer.

THE SEPARATION THE OWNER ASKED FOR, made mechanical here:

  queue_aware is a FILL MODEL, not a decision rule. The engine never contains
  it. In replay the ADAPTER supplies it -- `maker_fill(..., fill_model=
  "queue_aware")` -- so the engine's intents meet exactly the fills the frozen
  scorer assumed. Live, the same adapter seat is taken by authenticated venue
  fills and nothing in the engine changes.

  The residual cap is a DECISION rule, and frozen applies it to the qty that
  filled. `cap_at_fill=True` reproduces that placement for parity; live the
  engine applies it prospectively to the clip, which is strictly tighter.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E


class ReplayVenue:
    """The frozen fill model, wearing the venue's hat."""

    def __init__(self, *, fill_model: str = "queue_aware",
                 respect_limit: bool = False) -> None:
        self.fill_model = fill_model
        self.qstate: Dict[str, Any] = {}
        #: HONOUR THE RESTING LIMIT. False is the frozen arm byte-for-byte:
        #: `maker_fill` recomputes the price from the CURRENT touch and ignores
        #: the limit we passed it, which was harmless only because the frozen
        #: engine requotes to that same touch every tick -- audited, and no
        #: frozen fill was ever worse than its own limit.
        #:
        #: The complement controller breaks that coincidence on purpose: it
        #: rests BELOW the touch and waits. A resting buy cannot fill above its
        #: limit, so with the controller on the adapter must enforce that, or
        #: the cap would be priced and then ignored. This is a correctness
        #: requirement of the fill model, not a policy choice.
        self.respect_limit = bool(respect_limit)

    def fills_for(self, eng: "E.NativeEngine", row: Mapping[str, Any],
                  side: str, px: D, clip: D,
                  ladder: Optional[Mapping[str, Any]]):
        """Would our resting clip have filled on THIS observation?"""
        qty, fpx = S.maker_fill(row, side, clip, queue_aware=True,
                                fill_model=self.fill_model,
                                ladder=ladder, qstate=self.qstate)
        if qty <= 0 or fpx is None:
            return None
        if self.respect_limit:
            if fpx > px:
                # the market is richer than our limit: we are behind it and
                # nothing trades against us. No fill, no cost, keep waiting.
                return None
            # the touch is at or below our limit, so our order is at the front
            # of the book at OUR price and that is what we pay. Never the
            # touch: filling at a price we did not post would be a discount
            # the venue never gave us.
            return qty, px
        return qty, fpx


def replay_window(rows, *, winner, clip=E.CLIP, cap_at_fill=True,
                  fill_model="queue_aware",
                  complement_controller=False,
                  residual_backstop=False,
                  extend_requires_balance=False) -> Dict[str, Any]:
    """Drive the engine over one window exactly as run_window walks it.

    `complement_controller=False` is the frozen arm and is the default
    everywhere, so every existing caller and the pinned checkpoint are
    untouched. True turns on the inventory-aware complement quote AND the
    limit-respecting fill model it requires.
    """
    # parity: the cap runs where FROZEN runs it (on the filled qty), so the
    # engine's strictly-tighter prospective cap is switched off for the replay
    # and pinned separately by its own test.
    eng = E.NativeEngine(clip=clip, prospective_cap=not cap_at_fill,
                         complement_controller=complement_controller,
                         residual_backstop=residual_backstop,
                         extend_requires_balance=extend_requires_balance)
    venue = ReplayVenue(fill_model=fill_model,
                        respect_limit=complement_controller)
    ladder: Optional[Mapping[str, Any]] = None

    for row in rows:
        kind = row.get("kind")
        if kind == "book":
            ladder = row
            eng.ladder = row
            continue
        if kind != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue

        # a taker leg is confirmed INLINE, which is where frozen applies it:
        # the lock test and acquisition later in this row read the
        # post-completion book. The replay venue fills a marketable completion
        # at exactly the walked ladder cost, which is what frozen assumes.
        def _execute_taker(it):
            return it["qty"], it["cost"], it["fee"]

        try:
            intents = eng.on_book(row, ladder, execute_taker=_execute_taker)
        except E.StaleBook:
            continue                    # fail closed: no decision, no order
        del intents                     # inventory moved inline; REST/CANCEL
                                        # move none and the venue answers below

        if eng.locked_at is not None:
            continue

        # the adapter decides fills for whatever is resting, in frozen order
        for side in ("up", "down"):
            st = eng.resting.get(side)
            if not st:
                continue
            got = venue.fills_for(eng, row, side, D(str(st["px"])),
                                  D(str(st["qty"])), ladder)
            if got is None:
                continue
            qty, fpx = got
            if cap_at_fill and not eng._cap_permits(side, qty):
                continue
            eng.on_fill(side, qty, qty * fpx, maker=True)

    return eng.settle(winner)


def frozen_window(rows, *, winner, clip=E.CLIP) -> Dict[str, Any]:
    """The frozen arm, called exactly as p1_profit_lock.py calls it."""
    return S.run_window(rows, clip=D(str(clip)), theta=E.THETA, winner=winner,
                        variant=E.VARIANT, fill_model="queue_aware",
                        profit_lock_usd=E.PROFIT_LOCK_USD)


def net_of(r: Mapping[str, Any]) -> D:
    """SHADOW TRADING NET, the single primary criterion's quantity."""
    return r["matched_pnl"] - r["fees"] + r["resid_pnl"]


def compare(by, usable, *, clip=E.CLIP) -> Dict[str, Any]:
    rows_out = []
    for slug, winner in usable.items():
        fr = frozen_window(by[slug], winner=winner, clip=clip)
        lv = replay_window(by[slug], winner=winner, clip=clip)
        rows_out.append({
            "window": slug[-5:],
            "frozen_net": net_of(fr), "live_net": net_of(lv),
            "delta_net": net_of(lv) - net_of(fr),
            "frozen_matched_qty": fr["matched_qty"],
            "live_matched_qty": lv["matched_qty"],
            "frozen_locked": bool(fr.get("locked_at")),
            "live_locked": bool(lv.get("locked_at")),
            "frozen_lock_via": fr.get("lock_via"),
            "live_lock_via": lv.get("lock_via"),
            "frozen_completions": fr.get("completions"),
            "live_completions": lv.get("completions"),
            "frozen_peak_resid": fr.get("peak_resid"),
            "live_peak_resid": lv.get("peak_resid"),
        })
    n = D(len(rows_out))
    return {
        "per_window": rows_out,
        "frozen_mean_net": sum(x["frozen_net"] for x in rows_out) / n,
        "live_mean_net": sum(x["live_net"] for x in rows_out) / n,
        "exact_net_matches": sum(1 for x in rows_out if x["delta_net"] == 0),
        "n": int(n),
    }
