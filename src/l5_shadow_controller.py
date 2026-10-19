"""Inventory-driven BTC15m shadow controller — STRUCTURALLY UNABLE TO TRADE.

WHAT THIS IS. An INDEPENDENT decision source, not a translator of L5's
trades. `l5_live_execution.handle` derives our side, price and size from
L5's own action (`want = dec["l5_size"] * self.lam`, `raw_px =
dec["price"]`, `dec["side"]` throughout), so the deployed engine follows
L5 rather than deciding. This object decides for itself from OUR inventory,
the live authenticated book and the ACTUAL time to expiry, and reaches no
order path at all.

The decision logic is NOT reimplemented here. It is
`pmus_sports_l5_decision.plan_action`, which is already inventory-driven:
the model's relation is overridden from our residual (`if inv.residual ==
0: rel = "open"`), a completion takes the DEFICIT side and is priced from
OUR OWN average cost through `complement_target`, an extend takes the
SURPLUS side, and accumulation stops at MAX_RESIDUAL_UNITS. This module
supplies the three things that layer needs and the runner has: our
inventory, the book, and the window's real clock.

THE SAFETY PROPERTY IS STRUCTURAL, NOT CONDITIONAL. This class holds no
transport, no executor and no credentials, and defines no method that
could place, cancel or amend an order. It is not disarmed by a flag that
something else could flip -- there is nothing here to arm. An arming
environment variable cannot change what an object does not have, and
`tests/test_l5_shadow_controller.py` asserts the absence rather than
trusting it.

PROVENANCE IS KEPT SEPARATE, DELIBERATELY. Every record carries
`provenance: CONTROLLER_GENERATED`. It is never written through
`FT.l5_native`, never labelled `rested_at_l5_price`, and never enters the
L5 decision tape. Mixing our generated decisions with L5's observed ones
is the error this programme has caught repeatedly; here the two cannot
meet because this path does not touch that tape.

WHAT A FILL MEANS HERE, AND WHAT IT DOES NOT. The runner hands this object
a BBO snapshot, not a ladder with queue depth. On BBO alone a fill cannot
be established: an existing quote is never a fill, and the touch arriving
at our price is executability, not execution. So inventory advances under
ONE named rule, `TOUCH_THROUGH_OPTIMISTIC` -- the opposite side traded
strictly THROUGH our resting price -- and every emission carries that
label. It is an UPPER BOUND on fills and the reported inventory is an
upper bound on inventory. The queue-aware three-scenario model already
exists in `l5_maker_fill_model` and is the upgrade path once this
controller is fed the recorder's ladder frames; it is not silently
approximated here.

READ-ONLY on the venue. Writes exactly one file: its own state.
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal as D
from typing import Any, Callable, Dict, Mapping, Optional

from src import pmus_l5_persistence_v2 as pv2
from src import pmus_sports_l5_decision as l5d

#: never L5_NATIVE. The two populations must stay separable at a glance.
CONTROLLER_PROVENANCE = "CONTROLLER_GENERATED"

#: the only rule by which simulated inventory advances, named in every
#: record so a reader can never mistake it for a measured fill rate
FILL_RULE = "TOUCH_THROUGH_OPTIMISTIC"

#: attribute names this class must NEVER acquire. Asserted by test, so a
#: future edit that wires an order path in has to delete this line first.
FORBIDDEN_ATTRS = ("submit", "place", "cancel", "amend", "transport",
                   "executor", "credentials", "arm", "disarm")

STATE_VERSION = 2


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                      # noqa: BLE001
        return None


class ShadowController:
    """One inventory-driven decision per tick, for one market at a time."""

    def __init__(self, *, state_path: str,
                 model: Optional[Mapping[str, Any]] = None,
                 pair_cap: Optional[D] = None,
                 emit: Optional[Callable[[str, Mapping[str, Any]], None]] = None
                 ) -> None:
        self.state_path = str(state_path)
        self._model = model
        # BTC15m completes at the spring target, not the incentive-era
        # near-$1 tolerance. None would mean the sports cap.
        self.pair_cap = l5d.BTC15M_PAIR_CAP if pair_cap is None else pair_cap
        self._emit = emit
        self.state: Dict[str, Any] = {"v": STATE_VERSION, "markets": {}}
        self.decisions = 0
        self.counts: Dict[str, int] = {}
        self._load()

    # ------------------------------------------------------------ the model
    @property
    def model(self) -> Mapping[str, Any]:
        """Loaded lazily and cached: a missing artifact must not stop the
        runner's tick, it must stop only this controller."""
        if self._model is None:
            self._model = pv2.load_v2()
        return self._model

    # -------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(doc, Mapping) and doc.get("v") == STATE_VERSION:
            self.state = {"v": STATE_VERSION,
                          "markets": dict(doc.get("markets") or {})}

    def _save(self) -> None:
        """Atomic: a crash mid-write must not leave unreadable state, which
        would silently reset simulated inventory and flatter the record."""
        d = os.path.dirname(self.state_path) or "."
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)
        except OSError:
            pass                      # state is a diagnostic, never a gate

    # ---------------------------------------------------------- inventory
    def _slot(self, slug: str) -> Dict[str, Any]:
        m = self.state.setdefault("markets", {})
        return m.setdefault(slug, {"yes": 0, "no": 0, "yes_cost": "0",
                                   "no_cost": "0", "last_rel": "",
                                   "resting": None, "settled": None})

    def inventory_for(self, slug: str) -> l5d.Inventory:
        s = self._slot(slug)
        inv = l5d.Inventory()
        inv.yes, inv.no = int(s["yes"]), int(s["no"])
        inv.yes_cost, inv.no_cost = D(s["yes_cost"]), D(s["no_cost"])
        return inv

    def _store_inventory(self, slug: str, inv: l5d.Inventory) -> None:
        s = self._slot(slug)
        s["yes"], s["no"] = inv.yes, inv.no
        s["yes_cost"], s["no_cost"] = str(inv.yes_cost), str(inv.no_cost)

    # ------------------------------------------------------- the fill rule
    def _advance_resting(self, slug: str, book: Mapping[str, Any]) -> Optional[dict]:
        """Did the opposite side trade strictly THROUGH our resting price?

        Strictly through, never at: arriving at our price is executability,
        and crediting it would be crediting a touch as a fill.
        """
        s = self._slot(slug)
        rest = s.get("resting")
        if not rest:
            return None
        px = _d(rest.get("price"))
        if px is None:
            s["resting"] = None
            return None
        # our BUY of YES rests as a bid at px; it is taken out when the
        # ask comes strictly below it. Our BUY of NO rests as an ask at
        # 1-px; it is taken out when the bid goes strictly above that.
        if rest.get("side") == "YES":
            opp = _d(book.get("ask"))
            through = opp is not None and opp < px
        else:
            bid = _d(book.get("bid"))
            through = bid is not None and bid > (D(1) - px)
        if not through:
            return None
        inv = self.inventory_for(slug)
        inv.record(rest.get("side") == "YES", px)
        self._store_inventory(slug, inv)
        s["resting"] = None
        s["last_rel"] = str(rest.get("relation") or s.get("last_rel") or "")
        return {"filled_by": FILL_RULE, "side": rest.get("side"),
                "price": str(px), "relation": rest.get("relation"),
                "inventory_after": inv.snapshot(),
                "labels": ("UPPER BOUND. Queue position is unmodelled on a "
                           "BBO feed; a touch is not a fill and this rule "
                           "credits only a print strictly through us.")}

    # ------------------------------------------------------------- the tick
    def step(self, now: float, *, slug: str, book: Optional[Mapping[str, Any]],
             window_end_ts: Optional[float],
             window_source: Optional[str] = None) -> Dict[str, Any]:
        """One decision. Returns the record; never raises into the runner.

        `window_source` names where the clock came from (the venue, or our
        own grid). It decides nothing here and is carried because a tte is
        only as good as the clock under it, and a reader must not have to
        assume which one was used.
        """
        base = {"provenance": CONTROLLER_PROVENANCE, "market": slug,
                "fill_rule": FILL_RULE, "window_source": window_source}
        try:
            if not book or book.get("bid") is None:
                return self._out({**base, "decision": "no_action",
                                  "reason": "no_two_sided_book"})
            fill = self._advance_resting(slug, book)
            inv = self.inventory_for(slug)
            s = self._slot(slug)
            tte = l5d.tte_at(window_end_ts, now)
            prop = l5d._proposal(self.model, float(_d(book["bid"]) or 0),
                                 inv, str(s.get("last_rel") or ""),
                                 tte_s=tte)
            # the window clock is kept with the inventory it dates, so
            # settlement can find a closed window later without reparsing a
            # slug and without a second source of truth for the same fact.
            s["window_end_ts"] = window_end_ts
            s["window_source"] = window_source
            rec = {**base, "tte_s": round(float(tte), 1),
                   "state": prop.get("state"),
                   "inventory": inv.snapshot(),
                   "window_end_ts": window_end_ts,
                   "modelled_fill": fill}
            if prop.get("off_distribution"):
                return self._out({**rec, "decision": "no_action",
                                  "reason": "v2_off_distribution"})
            rel = prop.get("recommended_relation")
            plan = l5d.plan_action(rel, inv, book, pair_cap=self.pair_cap)
            rec = {**rec, **l5d._public(plan)}
            if plan.get("decision") == "quote":
                # recorded as RESTING, not as inventory: nothing is owned
                # until the fill rule above says a print went through us.
                s["resting"] = {"side": plan["side"],
                                "price": str(plan["_px"]),
                                "relation": plan.get("relation"),
                                "t": now}
                s["last_rel"] = str(plan.get("relation") or "")
            self._save()
            return self._out(rec)
        except BaseException as exc:                        # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return self._out({**base, "decision": "no_action",
                              "reason": "controller_error:%s"
                                        % type(exc).__name__})

    # ------------------------------------------------------- settlement
    def settle(self, slug: str, yes_value: D) -> Dict[str, Any]:
        """Matched and residual economics at the venue's settlement price.

        Separate lines, never summed: a matched pair redeems $1 whatever
        happens; residual pays 1 or 0. PMUS maker fees are $0.0000 CONFIRMED
        over 293 fills and the rebate quantises to zero at this size, so
        both are reported as zero rather than modelled.
        """
        inv = self.inventory_for(slug)
        s = self._slot(slug)
        matched = inv.matched
        resid = inv.residual
        cost = inv.yes_cost + inv.no_cost
        matched_redeem = D(matched)
        resid_redeem = (D(abs(resid)) * (yes_value if resid > 0
                                         else (D(1) - yes_value)))
        out = {"provenance": CONTROLLER_PROVENANCE, "market": slug,
               "yes_value": str(yes_value),
               "matched_units": matched, "residual_units": resid,
               "cost_total": str(cost),
               "redemption_total": str(matched_redeem + resid_redeem),
               "modelled_net": str(matched_redeem + resid_redeem - cost),
               "maker_fee_usd": "0.0000", "maker_rebate_usd": "0.0000",
               "fill_rule": FILL_RULE,
               "labels": ("MODELLED under an UPPER-BOUND fill rule. NOT "
                          "realized P&L: no order was ever submitted. Fees "
                          "confirmed zero for maker fills; the rebate "
                          "quantises to $0.00 below ~1.6 contracts.")}
        s["settled"] = out
        s["yes"], s["no"] = 0, 0
        s["yes_cost"], s["no_cost"] = "0", "0"
        s["last_rel"], s["resting"] = "", None
        self._save()
        return out

    # ------------------------------------------------------- window close
    def window_close(self, slug: str) -> Dict[str, Any]:
        """The window has rolled. Report what it leaves behind.

        A resting order does not survive expiry, so it is dropped -- NOT
        credited: an order that was never proved filled is not inventory.
        Whatever inventory the window accumulated stays on the books,
        unsettled, until the VENUE prices it. That is the stranded-inventory
        line Phase A asks for, and it is the honest one: nothing here is
        marked to an expired book.
        """
        s = self._slot(slug)
        inv = self.inventory_for(slug)
        dropped, s["resting"] = s.get("resting"), None
        self._save()
        return {"provenance": CONTROLLER_PROVENANCE, "market": slug,
                "inventory": inv.snapshot(),
                "cost_total": str(inv.yes_cost + inv.no_cost),
                "dropped_resting": dropped,
                "settled": bool(s.get("settled")),
                "fill_rule": FILL_RULE,
                "labels": ("STRANDED and PENDING SETTLEMENT. Matched pairs "
                           "redeem $1 each whatever the outcome; the residual "
                           "is UNKNOWN until the venue states a settlement "
                           "price. Not marked to the expired book.")}

    def pending_settlement(self, now: float, *,
                           grace_s: float = 60.0) -> list:
        """Closed windows still holding simulated inventory, for the CALLER
        to price from the venue.

        This object reads no price and guesses none. Without the venue's own
        number the window stays unsettled and is reported stranded, because
        an expired book's last price reaching a ledger as a payout is a
        defect this programme has already paid for once.
        """
        out = []
        for slug, s in (self.state.get("markets") or {}).items():
            if s.get("settled"):
                continue
            if not (int(s.get("yes") or 0) or int(s.get("no") or 0)):
                continue
            end = s.get("window_end_ts")
            if end is None or float(now) < float(end) + float(grace_s):
                continue
            out.append(slug)
        return sorted(out)

    def stranded(self) -> Dict[str, Any]:
        """Inventory on the books with no settlement applied, by market."""
        out = {}
        for slug, s in (self.state.get("markets") or {}).items():
            if s.get("settled"):
                continue
            inv = self.inventory_for(slug)
            if inv.yes or inv.no:
                out[slug] = {"inventory": inv.snapshot(),
                             "cost_total": str(inv.yes_cost + inv.no_cost),
                             "window_end_ts": s.get("window_end_ts")}
        return out

    def economics(self) -> Dict[str, Any]:
        """MODELLED economics across settled windows. Never realized P&L.

        Matched and residual are kept on separate lines because they are
        different claims: a matched pair's $1 redemption is determinate,
        the residual's is a bet. Summing them into one number is how a
        directional loss hides inside a pair profit.
        """
        settled = [s["settled"] for s in (self.state.get("markets") or {}).values()
                   if s.get("settled")]
        tot = {"windows_settled": len(settled),
               "cost_total": D(0), "redemption_total": D(0),
               "matched_units": 0, "residual_units_abs": 0}
        for x in settled:
            tot["cost_total"] += D(str(x.get("cost_total") or 0))
            tot["redemption_total"] += D(str(x.get("redemption_total") or 0))
            tot["matched_units"] += int(x.get("matched_units") or 0)
            tot["residual_units_abs"] += abs(int(x.get("residual_units") or 0))
        return {"windows_settled": tot["windows_settled"],
                "matched_units": tot["matched_units"],
                "residual_units_abs": tot["residual_units_abs"],
                "cost_total": str(tot["cost_total"]),
                "redemption_total": str(tot["redemption_total"]),
                "modelled_net": str(tot["redemption_total"] - tot["cost_total"]),
                "stranded_markets": len(self.stranded()),
                "fill_rule": FILL_RULE,
                "labels": ("MODELLED under an UPPER-BOUND fill rule, from "
                           "venue settlement prices. NOT realized, NOT "
                           "filled, NOT submitted: no order was ever sent.")}

    # ---------------------------------------------------------- reporting
    def _out(self, rec: Mapping[str, Any]) -> Dict[str, Any]:
        d = dict(rec)
        key = str(d.get("relation") or d.get("decision") or "?").upper()
        self.counts[key] = self.counts.get(key, 0) + 1
        self.decisions += 1
        if self._emit is not None:
            try:
                self._emit("L5_SHADOW_DECISION", d)
            except BaseException:                           # noqa: BLE001
                pass
        return d

    def report(self) -> Dict[str, Any]:
        return {"provenance": CONTROLLER_PROVENANCE,
                "decisions": self.decisions,
                "counts": dict(self.counts),
                "markets": len(self.state.get("markets") or {}),
                "pair_cap": str(self.pair_cap),
                "fill_rule": FILL_RULE,
                "state_path": self.state_path,
                "economics": self.economics(),
                "stranded": self.stranded(),
                "labels": ("independent inventory-driven decision source; "
                           "holds no transport and defines no order path")}
