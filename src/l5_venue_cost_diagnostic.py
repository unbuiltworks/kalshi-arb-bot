"""READ-ONLY diagnostic: why the venue's fills and this ledger disagree.

WHY IT EXISTS (2026-11-19). The account-wide cumulative loss gate compares a
$75 ceiling with a ledger total of -$136.78. 159 of the 468 settled rows in
that total are costed at an assumed $1.00 a contract because the ledger never
saw their price. `l5_venue_cost_attestation` replaces that bound from the
venue's own fills, but only where the quantities reconcile EXACTLY -- and for
97 rows they did not (94 `VENUE_QTY_NE_LEDGER_QTY`, 3 cost cross-checks). The
attestation computes both quantities, refuses, and keeps only the REASON. So
nobody can say whether those rows are a stale ledger quantity, a missed fill,
or a real disagreement, and the loss figure cannot be trusted or corrected.

WHAT THIS DOES. For each row the attestation gave up on, it reads the venue's
activity feed with the same reader and prints, side by side: the ledger's
quantities and cost, the venue's fill-derived quantities, cost and fees, the
difference, and which reconciliation form (if any) would have matched.

WHAT IT CANNOT DO, which is the whole design:

  * It calls NO method of the store. It reads `store.state` and never
    assigns into it. No cost basis, settlement, realized figure, attempt
    counter or correction record is written. The ledger file is not saved.
  * It never calls `attest_retired_market_cost` or `note_venue_cost_attempt`.
    The attestation's own durable state -- which rows are exhausted -- is
    untouched, so this cannot cause a correction by re-opening a row.
  * The only transport call is the activity feed's GET, through
    `l5_cost_basis_audit.walk_activities`. It submits, cancels and amends
    nothing.
  * Its progress lives in MEMORY only. A restart simply repeats it.
  * Every figure it prints that is not a venue or ledger fact is labelled
    HYPOTHETICAL. Nothing downstream reads its output.
  * OFF unless `L5_VENUE_COST_DIAGNOSTIC` is exactly `READ_ONLY`. Any
    exception is swallowed by the caller: it can never stop a tick.

No risk gate reads anything here, so it cannot loosen or tighten one.
"""
from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_cost_basis_audit as CBA
from src import l5_venue_cost_attestation as VCA

GATE_ENV = "L5_VENUE_COST_DIAGNOSTIC"
GATE_TOKEN = "READ_ONLY"

#: reports per step and per log line, so one line stays small.
MAX_REPORTS_PER_STEP = 8

LABEL = ("READ-ONLY DIAGNOSTIC. Nothing was corrected: the $1/share bound, "
         "every realized figure and every risk gate are exactly as they "
         "were. hypothetical_* is what the row WOULD read if the venue's "
         "fills were taken as the position; it is not a result.")


def enabled() -> bool:
    return (os.environ.get(GATE_ENV) or "").strip() == GATE_TOKEN


def _s(x: Any) -> Optional[str]:
    return None if x is None else str(x)


def targets(store: Any) -> List[str]:
    """Markets still at the bound that the attestation has given up on,
    oldest settlement first. Reads `store.state`; writes nothing."""
    st = getattr(store, "state", None)
    if not isinstance(st, Mapping):
        return []
    floor = VCA.retro_floor_day()
    book = ((st.get(VCA.STATE_KEY) or {}).get("markets") or {})
    out = []
    for m, row in (st.get("retired_markets") or {}).items():
        if not isinstance(row, Mapping):
            continue
        if row.get("pnl_provenance") != VCA.UPPER_BOUND:
            continue
        if row.get("cost_superseded") or row.get("venue_cost_attested"):
            continue
        t = str(row.get("t") or "")
        if not t or t[:10] < floor:
            continue
        rec = book.get(m)
        if not isinstance(rec, Mapping) \
                or rec.get("status") != "UPPER_BOUND_KEPT":
            continue
        out.append((t, str(m)))
    out.sort()
    return [m for _t, m in out]


def last_rebase_t(state: Mapping[str, Any]) -> Optional[str]:
    """When the realized total was last re-anchored, or None. The gate's
    figure counts only what was posted AFTER this instant."""
    ts = [str(r.get("t")) for r in (state.get("accounting_rebase_log") or [])
          if isinstance(r, Mapping) and r.get("t")]
    return max(ts) if ts else None


def rebase_overlap(store: Any) -> Dict[str, Any]:
    """Rows that settled BEFORE the last re-anchor, split by whether a cost
    correction was posted to the total AFTER it. PURE; reads `store.state`.

    WHY. A re-anchor replaces the total, so a row settled before it is no
    longer in the gate's figure. A correction posted afterwards for such a
    row still ADDS its delta to that figure: a credit against a loss the
    figure no longer holds. This counts them; it changes nothing.
    """
    st = getattr(store, "state", None)
    st = st if isinstance(st, Mapping) else {}
    at = last_rebase_t(st)
    out: Dict[str, Any] = {"last_rebase_t": at, "applied": False}
    if at is None:
        return out
    n_before = n_bound = n_fix = 0
    credit = D(0)
    unreadable = 0
    for row in (st.get("retired_markets") or {}).values():
        if not isinstance(row, Mapping):
            continue
        t = str(row.get("t") or "")
        if not t or t >= at:
            continue
        n_before += 1
        if row.get("pnl_provenance") == VCA.UPPER_BOUND:
            n_bound += 1
        fixed_at = str(row.get("cost_corrected_at") or "")
        old = row.get("cost_superseded")
        if fixed_at > at and isinstance(old, Mapping):
            new_p = CBA._d(row.get("settlement_pnl"))
            old_p = CBA._d(old.get("settlement_pnl"))
            if new_p is None or old_p is None:
                unreadable += 1
                continue
            n_fix += 1
            credit += new_p - old_p
    out.update({
        "rows_settled_before_rebase": n_before,
        "of_which_still_at_bound": n_bound,
        "of_which_corrected_after_rebase": n_fix,
        "corrections_unreadable": unreadable,
        "delta_added_after_rebase_for_rows_settled_before_it": str(credit),
        "labels": ("HYPOTHESIS TO CHECK, not a finding: the delta is what "
                   "venue cost attestation added to the account-wide total "
                   "for rows whose settlement the re-anchor had already "
                   "removed from it. Timestamps are this ledger's own "
                   "ISO-8601 UTC strings, compared as text.")})
    return out


def _side_word(venue: D, ledger: D) -> str:
    if venue == ledger:
        return "EQUAL"
    return "VENUE_MORE" if venue > ledger else "VENUE_LESS"


def compare(market: str, row: Mapping[str, Any], got: Mapping[str, Any],
            *, last_reason: Any = None,
            rebase_t: Optional[str] = None) -> Dict[str, Any]:
    """One row's ledger figures beside the venue's. PURE: reads its
    arguments, returns a new dict of strings, changes nothing."""
    out: Dict[str, Any] = {"market": market,
                           "attestation_last_reason": _s(last_reason)}
    held_up, held_dn = CBA._d(row.get("up_qty")), CBA._d(row.get("dn_qty"))
    old_cost = CBA._d(row.get("cost_total"))
    old_pnl = CBA._d(row.get("settlement_pnl"))
    v = CBA._d(row.get("yes_value"))
    out["ledger"] = {
        "up_qty": _s(held_up), "down_qty": _s(held_dn),
        "cost_total": _s(old_cost), "settlement_pnl": _s(old_pnl),
        "yes_value": _s(v), "settled_t": _s(row.get("t")),
        "unknown_basis_keys": [str(k) for k in
                               (row.get("unverified_cost_basis_keys") or [])],
        "origin": _s(row.get("origin")),
        "settled_before_last_rebase": (
            None if not rebase_t or not row.get("t")
            else bool(str(row.get("t")) < rebase_t))}
    if not got.get("ok"):
        out["venue"] = {"readable": False, "reason": _s(got.get("reason")),
                        "detail": {k: _s(x) for k, x in got.items()
                                   if k not in ("ok", "reason", "legs")
                                   and not isinstance(x, (dict, list))}}
        out["form"] = "VENUE_FILLS_UNREADABLE"
        return out
    legs = got.get("legs") or {}
    venue: Dict[str, D] = {}
    cost: Dict[str, D] = {}
    block: Dict[str, Any] = {"readable": True}
    for side, word in (("UP", "up"), ("DOWN", "down")):
        leg = legs.get(side) or {}
        q = CBA._d(leg.get("qty")) or D(0)
        c = CBA._d(leg.get("cost")) or D(0)
        venue[side], cost[side] = q, c
        fills = list(leg.get("fills") or ())
        block[word] = {"qty": str(q), "fill_derived_cost": str(c),
                       "fees": _s(leg.get("fees")), "fills": len(fills),
                       "avg_own_price": (None if q == 0
                                         else str((c / q).quantize(
                                             D("0.0001"))))}
    block["cost_total"] = str(cost["UP"] + cost["DOWN"])
    out["venue"] = block
    if held_up is None or held_dn is None:
        out["form"] = "LEDGER_QTY_UNREADABLE"
        return out
    out["difference"] = {
        "up_qty_venue_minus_ledger": str(venue["UP"] - held_up),
        "down_qty_venue_minus_ledger": str(venue["DOWN"] - held_dn),
        "net_venue": str(venue["UP"] - venue["DOWN"]),
        "net_ledger": str(held_up - held_dn),
        "up": _side_word(venue["UP"], held_up),
        "down": _side_word(venue["DOWN"], held_dn)}
    #: the SAME two forms `attest_retired_market_cost` accepts, restated so a
    #: reader can see which (if either) this row was close to. The rule
    #: itself still lives only in the store; this decides nothing.
    if held_up == venue["UP"] and held_dn == venue["DOWN"]:
        out["form"] = "GROSS_MATCH"
    elif min(held_up, held_dn) == 0 \
            and held_up - held_dn == venue["UP"] - venue["DOWN"]:
        out["form"] = "VENUE_NET_MATCH"
    else:
        out["form"] = "QUANTITY_MISMATCH"
    if v is not None and v in (D(0), D(1)) and old_pnl is not None:
        redeem = venue["UP"] * v + venue["DOWN"] * (D(1) - v)
        hyp = redeem - (cost["UP"] + cost["DOWN"])
        out["hypothetical_if_venue_fills_were_the_position"] = {
            "redemption": str(redeem), "settlement_pnl": str(hyp),
            "minus_booked_pnl": str(hyp - old_pnl),
            "applied": False}
    return out


class Diagnostic:
    """The paced pass. Everything it knows is in memory."""

    def __init__(self) -> None:
        self.walk: Optional[VCA.Walk] = None
        self.done: set = set()
        self.last_step = 0.0
        self.next_allowed = 0.0
        self.last_error: Optional[str] = None
        self.reported = 0
        self.forms: Dict[str, int] = {}

    def step(self, store: Any, transport: Any, now: float) -> Dict[str, Any]:
        if not enabled():
            return {"idle": "DISABLED"}
        if transport is None:
            return {"idle": "NO_TRANSPORT"}
        if now < self.next_allowed \
                or now - self.last_step < VCA.STEP_INTERVAL_S:
            return {"idle": "PACED"}
        self.last_step = now
        if self.walk is None:
            todo = [m for m in targets(store) if m not in self.done]
            if not todo:
                return {"idle": "NOTHING_LEFT", "reported": self.reported,
                        "forms": dict(self.forms)}
            self.walk = VCA.Walk(todo[:VCA.MAX_MARKETS_PER_WALK],
                                 started_at=dt.datetime.now(dt.timezone.utc))
        w = self.walk
        if not w.sufficient and w.ended is None:
            w.step(transport, VCA.PAGES_PER_STEP)
        if not w.sufficient:
            if w.ended is None:
                return {"walking": w.proof()}
            self.walk = None
            if str(w.ended).startswith("error"):
                self.last_error = w.ended
                self.next_allowed = now + VCA.ERROR_BACKOFF_S
                return {"walk_failed": w.ended}
            #: stopped short with no proof the feed was read far enough: a
            #: quantity taken from it could be a missed fill, so nothing is
            #: reported as a comparison. Not retried in this process.
            self.done.update(w.markets)
            return {"walk_insufficient": w.proof(),
                    "markets": list(w.markets)}
        st = getattr(store, "state", None)
        st = st if isinstance(st, Mapping) else {}
        book = ((st.get(VCA.STATE_KEY) or {}).get("markets") or {})
        rows = st.get("retired_markets") or {}
        out = []
        for m in w.markets:
            if m in self.done:
                continue
            if len(out) >= MAX_REPORTS_PER_STEP:
                break
            self.done.add(m)
            row = rows.get(m)
            if not isinstance(row, Mapping):
                out.append({"market": m, "form": "ROW_GONE"})
                continue
            rec = book.get(m) if isinstance(book.get(m), Mapping) else {}
            rep = compare(m, row, VCA.legs_from_rows(w.rows, m),
                          last_reason=rec.get("last_reason"),
                          rebase_t=last_rebase_t(st))
            self.forms[rep["form"]] = self.forms.get(rep["form"], 0) + 1
            out.append(rep)
        self.reported += len(out)
        proof = w.proof()
        if all(m in self.done for m in w.markets):
            self.walk = None
        return {"compared": out, "walk": proof, "reported": self.reported,
                "forms": dict(self.forms),
                "rebase_overlap": rebase_overlap(store), "labels": LABEL}
