"""Surface -> execution adapter. It proposes; it does not decide, and it barely submits.

    L5_ADAPTED_V1 -> desired surface -> THIS ADAPTER -> pmus_q1_executor primitives

WHAT IS DELIBERATELY NOT HERE. No action selection, no controller, no
stochastic draw, no R1 logic of any kind. The surface engine is the ONLY
decision source; this module's entire job is to take a rung that already
exists and ask the venue's own safety primitives whether it may be placed.
Replacing the decision source is the point, so inheriting the old one would
defeat it.

WHY IT IS A SEPARATE MODULE. `src.l5_surface.shadow` has no client, no signer
and no credential, and a test asserts that closure in a fresh subprocess. This
module DOES import the order path, so it lives apart and the shadow's
invariant survives untouched. Nothing here is imported by the shadow.

THE SUBMIT FUNCTION IS UNREACHABLE BY DEFAULT. `submit` raises unless ALL of
nine independent preconditions hold, and each is evaluated fresh at call time
rather than cached from a proposal built seconds earlier. A proposal is a
question; it is never a permission.

ACCOUNTING IS NAMESPACED. Every write this module makes while not armed is
prefixed `SIM:` and carries `simulated: true`. `realized_today` reads only
rows of kind `realized`, which this module never writes under any
circumstances -- a simulated run cannot reach realized P&L even by accident.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src import pmus_q1_executor as X

LABEL = "L5_SURFACE_EXEC_ADAPTER_V1"
SCHEMA = "l5_surface_exec/v1"

#: how old an authenticated BBO may be when we act on it. A book read is a
#: statement about a moment; acting on a stale one is acting on a price that
#: may no longer exist.
MAX_BBO_AGE_S = 2.0

#: the lifecycle. Nothing skips, and SETTLED/REALIZED are venue facts this
#: module never asserts on its own.
PROPOSED, SUBMITTED, FILLED, SETTLED, REALIZED = (
    "PROPOSED", "SUBMITTED", "FILLED", "SETTLED", "REALIZED")
LIFECYCLE = (PROPOSED, SUBMITTED, FILLED, SETTLED, REALIZED)

#: every write made while not armed carries this prefix and flag
SIM_PREFIX = "SIM:"


class GateClosed(RuntimeError):
    """submit() was reached without all nine preconditions holding."""


class ReconciliationError(RuntimeError):
    """A provenance mapping is duplicated or unknown. Fails closed."""


# --------------------------------------------------------------- kill switch

class Kill:
    """A kill that outlives the process.

    File-backed on the executor's own state dir, because a kill that only
    exists in memory is not a kill: the thing you most want to stop is a
    process you may have to restart."""

    FILE = "SURFACE_KILL"

    @classmethod
    def path(cls):
        return X.state_dir() / cls.FILE

    @classmethod
    def engage(cls, reason: str) -> None:
        p = cls.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"engaged_utc": time.time(),
                                 "reason": reason}), encoding="utf-8")

    @classmethod
    def clear(cls) -> None:
        try:
            cls.path().unlink()
        except FileNotFoundError:
            pass

    @classmethod
    def engaged(cls) -> Optional[dict]:
        try:
            return json.loads(cls.path().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return None


# ------------------------------------------------------------- risk envelope

@dataclass(frozen=True)
class Limits:
    max_order_usd: D
    max_market_usd: D
    max_abs_residual: D
    max_total_usd: D
    max_concurrent_markets: int
    daily_loss_stop_usd: D

    def report(self) -> Dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}


# ------------------------------------------------------- durable inventory

def durable_inventory(slug: str, *, ledger: Optional[Sequence[dict]] = None
                      ) -> Dict[str, Any]:
    """Complete inventory for a market from the DURABLE ledger, all sessions.

    Reading only this session's fills is how a residual carried over from a
    previous session becomes invisible to a risk check -- the exact shape of
    the venue_vs_ledger scope artifact already seen on R1. So every fill row
    the ledger ever wrote for this slug is replayed, and the simulated rows
    are excluded from the real position.
    """
    rows = ledger
    if rows is None:
        rows = []
        p = X.state_dir() / "ledger.jsonl"
        if p.exists():
            for line in p.open(encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    qty: Dict[str, D] = {}
    spend = D("0")
    n = 0
    sims = 0
    for r in rows:
        if r.get("slug") != slug:
            continue
        if r.get("simulated"):
            sims += 1
            continue
        if r.get("kind") not in ("fill", "filled"):
            continue
        side = str(r.get("side") or "")
        oc = "Up" if side.upper().endswith("YES") else "Down"
        try:
            q = D(str(r.get("qty") or r.get("quantity") or 0))
            px = D(str(r.get("price") or 0))
        except Exception:                                     # noqa: BLE001
            continue
        qty[oc] = qty.get(oc, D("0")) + q
        spend += q * px
        n += 1
    up, dn = qty.get("Up", D("0")), qty.get("Down", D("0"))
    return {"slug": slug, "qty": {k: str(v) for k, v in qty.items()},
            "up": str(up), "dn": str(dn),
            "matched": str(min(up, dn)), "residual": str(up - dn),
            "spend_usd": str(spend), "fill_rows": n,
            "simulated_rows_excluded": sims,
            "labels": ("every session's fills, not this one's. Simulated rows "
                       "are excluded from the real position.")}


def proposed_state(inv: Mapping[str, Any], outcome: str, qty: D, price: D
                   ) -> Dict[str, Any]:
    """Inventory AFTER the proposed order fills, which is what the risk
    envelope must be judged against. Judging the current state permits an
    order that breaches the moment it fills."""
    up = D(str(inv.get("up") or 0))
    dn = D(str(inv.get("dn") or 0))
    if outcome == "Up":
        up += qty
    else:
        dn += qty
    spend = D(str(inv.get("spend_usd") or 0)) + qty * price
    return {"up": str(up), "dn": str(dn), "matched": str(min(up, dn)),
            "residual": str(up - dn), "spend_usd": str(spend),
            "order_usd": str(qty * price),
            "labels": "POST-ORDER state, not current state"}


def risk_verdict(prop: Mapping[str, Any], limits: Limits, *,
                 total_usd: D, concurrent: int, realized_today: D
                 ) -> Dict[str, Any]:
    """Every bound checked against the PROPOSED state. Fails list every
    breach rather than the first, so one fix does not reveal another."""
    fails: List[str] = []
    order_usd = D(str(prop["order_usd"]))
    spend = D(str(prop["spend_usd"]))
    resid = abs(D(str(prop["residual"])))
    if order_usd > limits.max_order_usd:
        fails.append("order_usd %s > %s" % (order_usd, limits.max_order_usd))
    if spend > limits.max_market_usd:
        fails.append("market_usd %s > %s" % (spend, limits.max_market_usd))
    if resid > limits.max_abs_residual:
        fails.append("|residual| %s > %s" % (resid, limits.max_abs_residual))
    if (total_usd + order_usd) > limits.max_total_usd:
        fails.append("total_usd %s > %s" % (total_usd + order_usd,
                                            limits.max_total_usd))
    if concurrent > limits.max_concurrent_markets:
        fails.append("concurrent %d > %d" % (concurrent,
                                             limits.max_concurrent_markets))
    if realized_today <= -abs(limits.daily_loss_stop_usd):
        fails.append("daily realized %s at or past stop -%s"
                     % (realized_today, abs(limits.daily_loss_stop_usd)))
    return {"ok": not fails, "fails": fails, "limits": limits.report(),
            "proposed": dict(prop), "total_usd": str(total_usd),
            "concurrent": concurrent, "realized_today": str(realized_today)}


# ----------------------------------------------- authenticated, passive check

def outcome_side(outcome: str) -> str:
    return "OUTCOME_SIDE_YES" if outcome == "Up" else "OUTCOME_SIDE_NO"


def wire_price(outcome: str, price: D) -> D:
    """The venue takes the LONG-side price, so a NO order at NO price X goes
    on the wire as 1 - X. Sending X itself would place a completely different
    order at a completely different price."""
    return (X.ONE - D(str(price))) if outcome == "Down" else D(str(price))


def bbo_check(t, slug: str, outcome: str, price: D, *,
              max_age_s: float = MAX_BBO_AGE_S, clock=time.time,
              fresh=None) -> Dict[str, Any]:
    """Read the AUTHENTICATED bbo immediately before acting, and refuse on
    anything short of a clean, current, non-crossing answer.

    Unreadable is a REFUSAL, never a pass: a book we could not read is not a
    book that said yes.
    """
    fresh = fresh or X.fresh_bbo
    t0 = clock()
    try:
        bbo = fresh(t, slug)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "refusal": "BBO_UNREADABLE",
                "err": f"{type(exc).__name__}: {exc}"[:160],
                "source": "authenticated /v1/markets/{slug}/bbo"}
    age = clock() - t0
    side = outcome_side(outcome)
    out: Dict[str, Any] = {
        "source": "authenticated /v1/markets/%s/bbo" % slug,
        "bbo": bbo, "read_started_utc": t0, "bbo_age_s": round(age, 4),
        "max_age_s": max_age_s,
        "desired_price": str(price), "wire_price": str(wire_price(outcome,
                                                                  price)),
        "outcome_side": side,
    }
    if bbo.get("bid") is None or bbo.get("ask") is None:
        out.update(ok=False, refusal="BBO_NOT_TWO_SIDED")
        return out
    if age > max_age_s:
        out.update(ok=False, refusal="BBO_STALE")
        return out
    if X.would_cross(side, D(str(price)), bbo):
        out.update(ok=False, refusal="WOULD_CROSS")
        return out
    out.update(ok=True, refusal=None, post_only=True,
               post_only_field="participateDontInitiate")
    return out


# --------------------------------------------------------------- provenance

PROVENANCE_CHAIN = ("spec_sha256", "surface_id", "rung_index",
                    "authenticated_bbo", "order_intent", "order_id",
                    "authoritative_fill", "durable_inventory", "settlement")


def rung_id(spec_sha: str, surface_id: str, rung_index: int, slug: str,
            outcome: str, price: str) -> str:
    """Stable across ticks, unique per (spec, surface, rung, market, level).

    Stable because the same rung re-proposed must reconcile to the same
    identity; unique because two different rungs that reconciled to one
    identity would make a second order look like the first."""
    import hashlib
    raw = "|".join((spec_sha, surface_id, str(rung_index), slug, outcome,
                    str(price)))
    return "rung_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class Reconciler:
    """rung_id <-> order_id, one to one, failing closed on either ambiguity."""

    def __init__(self) -> None:
        self.by_rung: Dict[str, str] = {}
        self.by_order: Dict[str, str] = {}

    def bind(self, rid: str, order_id: str) -> None:
        if rid in self.by_rung and self.by_rung[rid] != order_id:
            raise ReconciliationError(
                "rung %s already bound to order %s, refusing to rebind to %s"
                % (rid, self.by_rung[rid], order_id))
        if order_id in self.by_order and self.by_order[order_id] != rid:
            raise ReconciliationError(
                "order %s already bound to rung %s, refusing to rebind to %s"
                % (order_id, self.by_order[order_id], rid))
        self.by_rung[rid] = order_id
        self.by_order[order_id] = rid

    def rung_for(self, order_id: str) -> str:
        if order_id not in self.by_order:
            raise ReconciliationError(
                "order %s maps to no known rung; an order we cannot attribute "
                "is not an order we may account for" % order_id)
        return self.by_order[order_id]

    def healthy(self) -> bool:
        return (len(self.by_rung) == len(self.by_order)
                and all(self.by_order.get(o) == r
                        for r, o in self.by_rung.items()))


# ------------------------------------------------------------ the proposal

@dataclass
class Proposal:
    rung_id: str
    spec_sha256: str
    surface_id: str
    rung_index: int
    slug: str
    outcome: str
    outcome_side: str
    desired_price: str
    wire_price: str
    desired_qty: str
    submit_qty: int
    status: str = PROPOSED
    bbo: Dict[str, Any] = field(default_factory=dict)
    inventory: Dict[str, Any] = field(default_factory=dict)
    proposed: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    refusals: List[str] = field(default_factory=list)
    order_id: Optional[str] = None

    def ok(self) -> bool:
        return not self.refusals

    def report(self) -> Dict[str, Any]:
        d = asdict(self)
        d.update(schema=SCHEMA, label=LABEL,
                 provenance_chain=list(PROVENANCE_CHAIN),
                 labels=["PROPOSED is a question, never a permission.",
                         "submit_qty is the executor's fixed q=1, not the "
                         "surface's desired_qty."])
        return d


def propose(t, *, rung, slug: str, spec_sha256: str, surface_id: str,
            limits: Limits, total_usd: D, concurrent: int,
            ledger: Optional[Sequence[dict]] = None,
            realized: Optional[D] = None, bbo_fn=None,
            clock=time.time) -> Proposal:
    """Everything a submission would need, computed and recorded, submitting
    nothing. Refusals accumulate so one fix does not merely reveal the next."""
    price = D(str(rung.max_acceptable_price))
    qty = D(str(X.QTY))                # what would actually go on the wire
    p = Proposal(
        rung_id=rung_id(spec_sha256, surface_id, rung.rung_index, slug,
                        rung.outcome, rung.max_acceptable_price),
        spec_sha256=spec_sha256, surface_id=surface_id,
        rung_index=rung.rung_index, slug=slug, outcome=rung.outcome,
        outcome_side=outcome_side(rung.outcome),
        desired_price=str(price), wire_price=str(wire_price(rung.outcome,
                                                            price)),
        desired_qty=str(rung.desired_qty), submit_qty=X.QTY)

    p.bbo = bbo_check(t, slug, rung.outcome, price, fresh=bbo_fn, clock=clock)
    if not p.bbo.get("ok"):
        p.refusals.append(str(p.bbo.get("refusal")))

    p.inventory = durable_inventory(slug, ledger=ledger)
    p.proposed = proposed_state(p.inventory, rung.outcome, qty, price)
    rt = realized if realized is not None else X.realized_today()
    p.risk = risk_verdict(p.proposed, limits, total_usd=total_usd,
                          concurrent=concurrent, realized_today=rt)
    if not p.risk["ok"]:
        p.refusals.extend("RISK:" + f for f in p.risk["fails"])

    k = Kill.engaged()
    if k:
        p.refusals.append("KILL_ENGAGED:" + str(k.get("reason"))[:60])
    return p


# ------------------------------------------------------------- the submit gate

#: the nine. Every one is re-evaluated at submit time; none is inherited from
#: a proposal, because a proposal is a statement about a moment that has
#: already passed by the time anyone acts on it.
PRECONDITIONS = (
    "armed", "has_credentials", "gate_asserted", "fresh_authenticated_bbo",
    "non_crossing_post_only", "mandate_present", "inventory_risk_pass",
    "reconciliation_healthy", "deadman_healthy",
)


def gate(t, prop: Proposal, *, reconciler: Reconciler,
         mandate: Optional[str], deadman_ok: bool,
         limits: Limits, total_usd: D, concurrent: int,
         ledger: Optional[Sequence[dict]] = None,
         realized: Optional[D] = None, bbo_fn=None,
         clock=time.time) -> Dict[str, Any]:
    """Evaluate all nine, fresh. Returns every failure, not the first."""
    checks: Dict[str, bool] = {}
    why: Dict[str, str] = {}

    checks["armed"] = bool(X.armed())
    if not checks["armed"]:
        why["armed"] = ("PMUS_Q1_LIVE/SESSION_ID/ARM are not set to an armed "
                        "triple")
    checks["has_credentials"] = bool(X.has_credentials())
    if not checks["has_credentials"]:
        why["has_credentials"] = "no venue key/secret in the environment"
    checks["gate_asserted"] = bool(X.gate_asserted(prop.slug))
    if not checks["gate_asserted"]:
        why["gate_asserted"] = "PMUS_Q1_GATE_EVIDENCE does not name this slug"

    # the book is re-read HERE, not reused from the proposal
    b = bbo_check(t, prop.slug, prop.outcome, D(prop.desired_price),
                  fresh=bbo_fn, clock=clock)
    checks["fresh_authenticated_bbo"] = bool(
        b.get("ok") or b.get("refusal") == "WOULD_CROSS")
    checks["non_crossing_post_only"] = bool(b.get("ok"))
    if not checks["fresh_authenticated_bbo"]:
        why["fresh_authenticated_bbo"] = str(b.get("refusal"))
    if not checks["non_crossing_post_only"]:
        why["non_crossing_post_only"] = str(b.get("refusal"))

    checks["mandate_present"] = bool(mandate)
    if not mandate:
        why["mandate_present"] = "no authorization id supplied"

    inv = durable_inventory(prop.slug, ledger=ledger)
    ps = proposed_state(inv, prop.outcome, D(str(X.QTY)),
                        D(prop.desired_price))
    rt = realized if realized is not None else X.realized_today()
    rv = risk_verdict(ps, limits, total_usd=total_usd, concurrent=concurrent,
                      realized_today=rt)
    checks["inventory_risk_pass"] = bool(rv["ok"])
    if not rv["ok"]:
        why["inventory_risk_pass"] = "; ".join(rv["fails"])[:200]

    checks["reconciliation_healthy"] = bool(reconciler.healthy())
    if not checks["reconciliation_healthy"]:
        why["reconciliation_healthy"] = "rung/order mapping is not one to one"

    k = Kill.engaged()
    checks["deadman_healthy"] = bool(deadman_ok) and k is None
    if not checks["deadman_healthy"]:
        why["deadman_healthy"] = ("kill engaged: " + str(k.get("reason"))[:80]
                                  if k else "deadman reported unhealthy")

    failed = [c for c in PRECONDITIONS if not checks.get(c)]
    return {"all_pass": not failed, "checks": checks, "failed": failed,
            "why": why, "bbo": b, "risk": rv, "proposed": ps,
            "preconditions": list(PRECONDITIONS),
            "labels": ("every precondition is re-evaluated here. A proposal "
                       "built seconds ago is not evidence about now.")}


def submit(t, prop: Proposal, *, reconciler: Reconciler, mandate: str,
           deadman_ok: bool, limits: Limits, total_usd: D, concurrent: int,
           stage: str = "surface", **kw) -> str:
    """THE ONLY PATH THAT PLACES AN ORDER, and it refuses by default.

    Raises GateClosed unless all nine hold. When they do it delegates to the
    reviewed `place_limit_buy`, which previews first, re-reads the BBO once
    more itself, sends participateDontInitiate (post-only) and writes the
    durable intent BEFORE the mutation. Nothing about placement is
    reimplemented here."""
    g = gate(t, prop, reconciler=reconciler, mandate=mandate,
             deadman_ok=deadman_ok, limits=limits, total_usd=total_usd,
             concurrent=concurrent, **kw)
    if not g["all_pass"]:
        raise GateClosed("submit refused; failed: %s | %s"
                         % (g["failed"], json.dumps(g["why"])[:300]))
    order_id = X.place_limit_buy(t, prop.slug, prop.outcome_side,
                                 D(prop.desired_price), stage)
    reconciler.bind(prop.rung_id, order_id)
    prop.order_id = order_id
    prop.status = SUBMITTED
    return order_id


# ------------------------------------------------------- kill / cancel paths

def kill(t, slug: str, reason: str, *, cancel_all=None) -> Dict[str, Any]:
    """Engage the kill and cancel everything open on the market.

    The kill is engaged FIRST. If cancellation then fails, the process is
    already forbidden from submitting, which is the order that matters: a
    kill that cancels first and latches second has a window in it."""
    Kill.engage(reason)
    fn = cancel_all or X.cancel_all_deadman
    err = None
    try:
        fn(t, slug)
    except Exception as exc:                                  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"[:200]
    return {"killed": True, "reason": reason, "cancel_error": err,
            "submissions_blocked": True,
            "labels": ("kill latched before cancelling. A cancel that failed "
                       "leaves orders outstanding and they stay accounted "
                       "for until the venue says otherwise.")}


def cancel_and_confirm(t, order_id: str, slug: str, *, cancel=None,
                       status=None) -> Dict[str, Any]:
    """Cancel, then CONFIRM from venue state. Ambiguity fails closed.

    `cancel_exact` returns True when a status string merely lacks "FILLED",
    which treats an unreadable status as a cancellation. An order we cannot
    read is an order that may still be live, so here that is OUTSTANDING."""
    cancel = cancel or X.cancel_exact
    try:
        cancel(t, order_id, slug)
    except Exception as exc:                                  # noqa: BLE001
        return {"order_id": order_id, "terminal": False,
                "state": "CANCEL_CALL_FAILED", "outstanding": True,
                "err": f"{type(exc).__name__}: {exc}"[:160]}
    try:
        o = (status or X._order_status)(t, order_id)
        st = X.order_state_str(o)
    except Exception as exc:                                  # noqa: BLE001
        return {"order_id": order_id, "terminal": False,
                "state": "STATUS_UNREADABLE", "outstanding": True,
                "err": f"{type(exc).__name__}: {exc}"[:160],
                "labels": "unreadable is not cancelled"}
    terminal = any(k in st for k in ("CANCEL", "REJECT", "FILLED"))
    return {"order_id": order_id, "state": st, "terminal": terminal,
            "outstanding": not terminal,
            "labels": ("outstanding until the venue reports a terminal "
                       "state, whatever we asked it to do")}


# --------------------------------------------------------------- accounting

def record(row: Mapping[str, Any], *, simulated: bool) -> Dict[str, Any]:
    """Append to the durable ledger, namespaced when not real.

    A simulated row can never be read as realized P&L: `realized_today` counts
    only rows of kind `realized`, and this refuses to write that kind at all.
    """
    kind = str(row.get("kind") or "")
    if kind == "realized" or kind.startswith(SIM_PREFIX + "realized"):
        raise ValueError(
            "this module never writes realized rows; realized is a settlement "
            "fact and is written by the settlement audit alone")
    out = dict(row)
    if simulated:
        out["kind"] = SIM_PREFIX + kind
        out["simulated"] = True
        out["labels"] = ("SIMULATED. No order was placed and this row is "
                         "excluded from every real position and from "
                         "realized P&L.")
    X.ledger_append(out)
    return out


def fill_from_venue(t, order_id: str, *, status=None) -> Dict[str, Any]:
    """The venue's own account of a fill, or nothing. Never inferred."""
    try:
        o = (status or X._order_status)(t, order_id)
    except Exception as exc:                                  # noqa: BLE001
        return {"order_id": order_id, "fill": None,
                "why": f"status unreadable: {type(exc).__name__}"[:120]}
    f = X.authoritative_fill(o)
    return {"order_id": order_id, "fill": f,
            "state": X.order_state_str(o),
            "labels": ("authoritative_fill only. A fill we inferred is not a "
                       "fill.")}
