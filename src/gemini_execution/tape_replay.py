"""FULL-SEQUENTIAL-TAPE REPLAY: the tape is the clock, not our cycles.

Every earlier study stepped cycle by cycle and asked which prints fell in the
interval. That is the wrong clock. A fill changes inventory, inventory changes
what the policy wants, and the next print an instant later meets a different
quote. So this engine walks the tape PRINT BY PRINT, and after every
hypothetical execution it recomputes inventory and the Model-A action BEFORE
looking at the next print.

Each window starts FLAT. Nothing is carried in.

THE TWO POLICIES, and only two:

    A  FROZEN_L5_NATIVE   the frozen policy, unchanged and recorded.
    B  GEMINI_RECYCLE     flat -> native L5. Once residual exists, quote the
                          COMPLETE leg and observationally VETO the EXTEND the
                          frozen policy still asks for. TTE 60-30 stays
                          COMPLETE-only, nothing crosses, and no new exposure
                          opens below TTE 60.

B's late-phase and no-new-exposure clauses are the FROZEN policy's own bounds
(`NO_NEW_OPEN_EXTEND_TTE_S`, `WITHDRAW_ALL_TTE_S`), restated so the policy
reads whole. They are not new bounds and nothing here moves them.

STRICT QUEUE ACCOUNTING. A reaching print eats the size ahead of us first and
only what is left can fill us. Gemini publishes a ladder for the YES side and
none for NO, so a NO rung's queue is UNKNOWN -- treated as zero, which is
OPTIMISTIC, and every fill that depended on that assumption is counted so the
result can be read without it.

EVERY EXECUTION HERE IS SHADOW/THEORETICAL. No order existed, nothing was
submitted, filled, settled or realized, and our own size would have changed
the book this is measured against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

POLICY_A = "FROZEN_L5_NATIVE"
POLICY_B = "GEMINI_RECYCLE"
POLICIES = (POLICY_A, POLICY_B)

TICK = D("0.01")
BURST_CONTRACTS = D("100")

#: the frontier's buckets, in ticks of improvement over Model A's own price
FRONTIER_BUCKETS = ("0", "1", "2", "3-5", ">5", "crossing_required",
                    "unreachable_at_any_maker_price")

#: ANALYTICAL buckets for reading the frontier. NOT gates, not proposed, and
#: not wired to anything -- the owner ruled against a pair-cost ceiling in the
#: live path and this does not reopen that.
PAIR_CEILINGS = ("0.98", "0.99", "1.00", "1.01", "1.02", "1.05")

#: a window needs enough recorded book to price a quote across the tape
MIN_CYCLES_FOR_A_WINDOW = 5

THEORETICAL = ("SHADOW/THEORETICAL. No order existed, nothing was submitted, "
               "filled, settled or realized, and our own size would have "
               "changed the book this is measured against.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def complement_leg(held: str) -> str:
    return "NO" if str(held).upper() == "YES" else "YES"


def reaching_aggressor(leg: str) -> str:
    """A YES rung is reached by a SELLER; a NO rung by a BUYER, because
    selling NO to us IS buying YES. The tape is priced in YES throughout."""
    return "sell" if str(leg).upper() == "YES" else "buy"


def reaches(leg: str, price: Any, print_: Mapping[str, Any]) -> bool:
    p, tp = _dec(price), _dec(print_.get("price"))
    if p is None or tp is None:
        return False
    if str(print_.get("type")) != reaching_aggressor(leg):
        return False
    return (tp <= p) if str(leg).upper() == "YES" else (tp >= (D("1") - p))


#: how a rung's queue position is modelled.
#:
#:   SYMMETRIC     both legs are queued against the ladder an aggressor
#:                 actually consumes. The correct default.
#:   FRONT_OF_QUEUE  no queue on either leg -- the absolute upper bound on
#:                 what a rung could have caught. Kept so the pre-correction
#:                 numbers stay reproducible and comparable; never a default.
QUEUE_SYMMETRIC = "SYMMETRIC"
QUEUE_FRONT = "FRONT_OF_QUEUE"


def size_ahead(depth: Any, leg: str, price: Any, *,
               mode: str = QUEUE_SYMMETRIC) -> Optional[D]:
    """Displayed size an aggressor must clear before it reaches OUR rung.

    THIS WAS THE BUG, AND IT HAD A DIRECTION. Until 2026-09-20 this returned
    None for every NO leg ("YES ladder only"), and `Quote.__post_init__`
    turns None into ZERO size ahead. So a NO rung filled from the front of
    the queue on any reaching print however small, while a YES rung had to
    clear the whole displayed ladder first. Nine consecutive BTC15m windows
    ended long NO and never once long YES, and a symmetric book with mirrored
    prints filled the NO leg and not the YES leg. That is not the market and
    it is not L5 -- it is this function.

    WHAT IS AND IS NOT DERIVED. `metadata.contract_depth` refuses to relabel
    an ask size as a "NO bid size", and that refusal stands: no such number is
    invented here. What is computed is OUR QUEUE POSITION. Our NO bid at n
    rests, through the complement identity `outcome_book` already verifies, at
    ask price (1 - n); an aggressor BUY walks the ASK ladder upward and clears
    everything displayed at or below that price before reaching us. Those are
    ask sizes used as ask sizes -- the quantity the buyer actually eats.

    UNKNOWN STAYS UNKNOWN, AND NOW SYMMETRICALLY. An unreadable ladder still
    returns None for BOTH legs, so neither is silently advantaged.
    """
    p = _dec(price)
    if p is None:
        return None
    if mode == QUEUE_FRONT:
        return D("0")
    if not isinstance(depth, Mapping) or not depth.get("readable"):
        return None
    if str(leg).upper() == "YES":
        # a SELLER walks the BID ladder down; everything displayed at or
        # above our price is ahead of us
        side, ahead_of_us = "bids", (lambda px: px >= p)
    else:
        # a BUYER walks the ASK ladder up to (1 - n)
        q = D("1") - p
        side, ahead_of_us = "asks", (lambda px: px <= q)
    tot = D("0")
    for lv in ((depth.get(side) or {}).get("levels") or []):
        px, qty = _dec(lv.get("price")), _dec(lv.get("qty"))
        if px is not None and qty is not None and ahead_of_us(px):
            tot += qty
    return tot


def ask_for(book: Mapping[str, Any], leg: str) -> Optional[D]:
    side = "yes" if str(leg).upper() == "YES" else "no"
    return _dec(((book or {}).get(side) or {}).get("ask"))


@dataclass
class Quote:
    """A live rung and the queue it actually joined."""
    leg: str
    price: D
    qty: D
    placed_ms: D
    ahead: Optional[D]
    queue_known: bool
    kind: str
    ahead_left: D = field(default_factory=lambda: D("0"))
    filled: D = field(default_factory=lambda: D("0"))

    def __post_init__(self):
        self.ahead_left = self.ahead if self.ahead is not None else D("0")

    def take(self, volume: D) -> Tuple[D, D]:
        """Strict queue accounting: the queue eats first, we get the rest."""
        eat = min(volume, self.ahead_left)
        self.ahead_left -= eat
        left = volume - eat
        got = min(self.qty - self.filled, left) if left > 0 else D("0")
        self.filled += got
        return got, eat


def desired(policy: str, *, symbol: str, tte_s: Any, inv: Any,
            book: Mapping[str, Any]) -> Dict[str, Any]:
    """What this policy wants live, from the FROZEN policy's own decision.

    Model A is asked every time and its answer is recorded in full. Policy B
    filters that answer -- it never produces an action the frozen policy did
    not ask for, and it never changes a price.
    """
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import large_prints as LP

    st = LP.state_from_recorded_book(symbol, tte_s, inv, book)
    got = CP.plan(st, inv, actions_taken=0)
    acts = list(got.get("actions") or [])
    out = {"model_a": [dict(a) for a in acts],
           "window_phase": got.get("window_phase"),
           "vetoed": [], "kept": acts}
    if policy == POLICY_A:
        return out
    # ---- POLICY B: once residual exists, work the COMPLETE and nothing else
    if inv.residual() != 0:
        keep, veto = [], []
        for a in acts:
            (keep if a.get("action") == CP.COMPLETE else veto).append(a)
        out["kept"], out["vetoed"] = keep, veto
    return out


def _reconcile(live: Dict[str, Quote], want: Sequence[Mapping[str, Any]], *,
               now_ms: D, depth: Any, default_qty: D,
               queue_mode: str = QUEUE_SYMMETRIC) -> Dict[str, Any]:
    """Bring the live rungs in line. A PRICE CHANGE LOSES QUEUE POSITION.

    Keeping the same price keeps the place in the queue; any other change is a
    cancel and a new order at the back of its level. That is the cost the
    earlier studies could not see, and it is charged here.
    """
    by_leg = {str(a.get("leg")).upper(): a for a in want}
    events = {"placed": 0, "requoted": 0, "kept": 0, "cancelled": 0}
    for leg in list(live):
        if leg not in by_leg:
            del live[leg]
            events["cancelled"] += 1
    for leg, a in by_leg.items():
        p = _dec(a.get("price"))
        if p is None:
            continue
        q = _dec(a.get("qty")) or default_qty
        cur = live.get(leg)
        if cur is not None and cur.price == p:
            # THE QUEUE IS KEPT, THE LABEL IS NOT. A rung resting at the same
            # price keeps its place, but what that rung MEANS changes with
            # inventory: the NO quote that was an OPEN while we were flat is a
            # COMPLETE the instant a YES fill lands. Carrying the old label
            # forward would have booked complements as opens.
            cur.kind = str(a.get("action"))
            events["kept"] += 1
            continue
        ahead = size_ahead(depth, leg, p, mode=queue_mode)
        live[leg] = Quote(leg=leg, price=p, qty=q, placed_ms=now_ms,
                          ahead=ahead, queue_known=ahead is not None,
                          kind=str(a.get("action")))
        events["requoted" if cur is not None else "placed"] += 1
    return events


def replay_window(*, symbol: str, policy: str,
                  cycles: Sequence[Mapping[str, Any]],
                  prints: Sequence[Mapping[str, Any]],
                  resolution: Mapping[str, Any], expiry_ms: Any,
                  exclude_bursts: bool = False,
                  default_qty: Any = "0.01") -> Dict[str, Any]:
    """One window, start to settlement, walked print by print."""
    from src.gemini_execution import continuous_probe as CP

    inv = CP.Inventory()
    dq = _dec(default_qty) or D("0.01")
    cyc = sorted([c for c in cycles if _dec(c.get("utc_ms")) is not None],
                 key=lambda c: _dec(c["utc_ms"]))
    if len(cyc) < MIN_CYCLES_FOR_A_WINDOW:
        return {"symbol": symbol, "policy": policy, "sufficient": False,
                "cycles": len(cyc),
                "why": ("%d recorded cycles is below the %d needed to price a "
                        "quote across this tape" % (len(cyc),
                                                    MIN_CYCLES_FOR_A_WINDOW)),
                "labels": THEORETICAL}

    tape = [p for p in prints if _dec(p.get("utc_ms")) is not None]
    if exclude_bursts:
        tape = [p for p in tape
                if (_dec(p.get("amount")) or D("0")) < BURST_CONTRACTS]
    tape.sort(key=lambda p: _dec(p["utc_ms"]))

    events: List[Tuple[D, int, Any]] = (
        [(_dec(c["utc_ms"]), 0, c) for c in cyc]
        + [(_dec(p["utc_ms"]), 1, p) for p in tape])
    events.sort(key=lambda e: (e[0], e[1]))

    live: Dict[str, Quote] = {}
    book: Dict[str, Any] = {}
    depth: Any = None
    tte: Any = None
    fills: List[Dict[str, Any]] = []
    episodes: List[Dict[str, Any]] = []       # complement quotes, for the frontier
    vetoed_n = 0
    unknown_queue_fills = 0
    requotes = 0
    n_initial = 0

    def _retarget(now_ms: D) -> None:
        nonlocal vetoed_n, requotes
        d = desired(policy, symbol=symbol, tte_s=tte, inv=inv, book=book)
        vetoed_n += len(d["vetoed"])
        for leg in list(live):
            q = live[leg]
            if q.kind == CP.COMPLETE and not any(
                    str(a.get("leg")).upper() == leg
                    and _dec(a.get("price")) == q.price for a in d["kept"]):
                episodes.append(_episode(q, now_ms, inv, book, depth))
        ev = _reconcile(live, d["kept"], now_ms=now_ms, depth=depth,
                        default_qty=dq)
        requotes += ev["requoted"]

    for ts, kind, obj in events:
        if kind == 0:
            book = obj.get("book") or {}
            depth = obj.get("depth")
            tte = obj.get("tte_s")
            _retarget(ts)
            continue
        amt = _dec(obj.get("amount")) or D("0")
        for leg in list(live):
            q = live.get(leg)
            if q is None or not reaches(leg, q.price, obj):
                continue
            got, ate = q.take(amt)
            if got <= 0:
                continue
            was_flat = inv.residual() == 0 and inv.matched() == 0
            inv.apply_fill(leg, got, q.price,
                           source="SHADOW_TAPE_REPLAY_THEORETICAL")
            if was_flat:
                n_initial += 1
            if not q.queue_known:
                unknown_queue_fills += 1
            fills.append({"leg": leg, "qty": str(got), "price": str(q.price),
                          "kind": q.kind, "utc_ms": str(ts), "tte_s": tte,
                          "queue_known": q.queue_known,
                          "size_ahead_at_place": (str(q.ahead)
                                                  if q.ahead is not None
                                                  else None),
                          "tid": obj.get("tid"),
                          "print_amount": str(amt)})
            if q.filled >= q.qty:
                del live[leg]
            # THE POINT OF THIS ENGINE: inventory changed, so what the policy
            # wants changed, BEFORE the next print is looked at.
            _retarget(ts)

    end_ms = _dec(expiry_ms) or (events[-1][0] if events else D("0"))
    for leg, q in list(live.items()):
        if q.kind == CP.COMPLETE:
            episodes.append(_episode(q, end_ms, inv, book, depth))

    return {
        "symbol": symbol, "policy": policy, "sufficient": True,
        "cycles": len(cyc), "prints_walked": len(tape),
        "excluded_bursts": exclude_bursts,
        "initial_fills": n_initial,
        "fills": fills,
        "complement_fills": sum(1 for f in fills if f["kind"] == CP.COMPLETE),
        "extend_fills": sum(1 for f in fills if f["kind"] == CP.EXTEND),
        "open_fills": sum(1 for f in fills if f["kind"] == CP.OPEN),
        "extends_vetoed": vetoed_n,
        "requotes": requotes,
        "fills_resting_on_an_unknown_queue": unknown_queue_fills,
        "missed_complement_episodes": episodes,
        "inventory": inv.report(),
        "settlement": CP.settlement_report(inv, dict(resolution or {})),
        "settlement_caveat": ("which way the contract resolved is a coin flip "
                              "and is NOT used to justify any behaviour"),
        "labels": THEORETICAL,
    }


def _episode(q: Quote, ended_ms: D, inv: Any, book: Mapping[str, Any],
             depth: Any) -> Dict[str, Any]:
    """A COMPLETE quote that came down without filling. The frontier's unit."""
    held = "YES" if inv.residual() > 0 else "NO" if inv.residual() < 0 else None
    avg = (inv.avg_yes() if held == "YES"
           else inv.avg_no() if held == "NO" else None)
    return {
        "leg": q.leg, "price": str(q.price), "qty": str(q.qty),
        "placed_ms": str(q.placed_ms), "ended_ms": str(ended_ms),
        "rested_s": float((ended_ms - q.placed_ms) / D("1000")),
        "filled": str(q.filled),
        "size_ahead_at_place": (str(q.ahead) if q.ahead is not None else None),
        "queue_known": q.queue_known,
        "ask": (str(ask_for(book, q.leg)) if ask_for(book, q.leg) is not None
                else None),
        "held_leg": held,
        "held_avg_price": (str(avg) if avg is not None else None),
        "depth": depth,
    }


def _bucket(ticks: Optional[int]) -> str:
    if ticks is None:
        return "crossing_required"
    if ticks <= 0:
        return "0"
    if ticks == 1:
        return "1"
    if ticks == 2:
        return "2"
    return "3-5" if ticks <= 5 else ">5"


def frontier_for(ep: Mapping[str, Any],
                 prints: Sequence[Mapping[str, Any]], *,
                 max_ticks: int = 40) -> Dict[str, Any]:
    """MINIMUM improvement over Model A's own COMPLETE that gets a fill.

    Strict queue accounting at every candidate price -- moving up a tick joins
    a DIFFERENT level with a different queue in front of it, so the queue is
    re-measured at each candidate rather than carried across.

    A price that would reach the ask is not an answer: it is a CROSS, which is
    not authorized, and the episode is reported as needing one.
    """
    leg = str(ep.get("leg")).upper()
    p0 = _dec(ep.get("price"))
    ask = _dec(ep.get("ask"))
    lo, hi = _dec(ep.get("placed_ms")), _dec(ep.get("ended_ms"))
    want = _dec(ep.get("qty")) or D("0.01")
    held_avg = _dec(ep.get("held_avg_price"))
    depth = ep.get("depth")
    if p0 is None:
        return {"computable": False, "why": "no Model-A price to improve on"}
    window = [t for t in prints
              if _dec(t.get("utc_ms")) is not None
              and (lo is None or _dec(t["utc_ms"]) >= lo)
              and (hi is None or _dec(t["utc_ms"]) <= hi)]

    for k in range(0, max_ticks + 1):
        p = p0 + D(k) * TICK
        if p <= 0 or p >= D("1"):
            break
        if ask is not None and p >= ask:
            return {"computable": True, "ticks": None,
                    "bucket": "crossing_required", "reachable": False,
                    "price": None, "ask": str(ask),
                    "why": ("improving to %s would reach the ask %s; crossing "
                            "is not authorized" % (p, ask)),
                    "prints_in_window": len(window)}
        ahead = size_ahead(depth, leg, p)
        left = ahead if ahead is not None else D("0")
        got = D("0")
        for t in window:
            if not reaches(leg, p, t):
                continue
            v = _dec(t.get("amount")) or D("0")
            eat = min(v, left)
            left -= eat
            v -= eat
            if v > 0:
                got += min(want - got, v)
            if got >= want:
                break
        if got >= want:
            basis = (held_avg + p) if held_avg is not None else None
            return {"computable": True, "ticks": k, "bucket": _bucket(k),
                    "reachable": True, "price": str(p),
                    "ask": (str(ask) if ask is not None else None),
                    "queue_known": bool(ahead is not None),
                    "size_ahead_at_that_price": (str(ahead) if ahead is not None
                                                 else None),
                    "effective_pair_basis": (str(basis) if basis is not None
                                             else None),
                    "prints_in_window": len(window)}
    # NOT ">5 ticks". ">5" means reachable but expensive; this means no maker
    # price reached it at all. Conflating the two read as "expensive" what was
    # actually "impossible".
    return {"computable": True, "ticks": None,
            "bucket": "unreachable_at_any_maker_price",
            "reachable": False, "price": None,
            "why": ("no maker price within %d ticks of Model A's own would "
                    "have been filled by the flow while this quote rested"
                    % max_ticks),
            "prints_in_window": len(window)}


def frontier_census(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The frontier pooled, and completion at each ANALYTICAL pair ceiling.

    THE CEILINGS ARE BUCKETS FOR READING THE RESULT. They are not gates, not
    proposed, and nothing is wired to them: the owner ruled against a pair-cost
    ceiling in the live path and this does not reopen it.
    """
    ok = [r for r in rows if r.get("computable")]
    by_bucket: Dict[str, int] = {b: 0 for b in FRONTIER_BUCKETS}
    for r in ok:
        b = r.get("bucket") or "unreachable_at_any_maker_price"
        by_bucket[b] = by_bucket.get(b, 0) + 1
    reach = [r for r in ok if r.get("reachable")]
    bases = sorted(b for b in (_dec(r.get("effective_pair_basis"))
                               for r in reach) if b is not None)
    ceilings = {}
    for c in PAIR_CEILINGS:
        cap = D(c)
        n = sum(1 for b in bases if b <= cap)
        ceilings[c] = {"complements_available": n,
                       "completion_rate": (round(n / len(ok), 4) if ok
                                           else None)}
    ceilings["unrestricted_maker"] = {
        "complements_available": len(reach),
        "completion_rate": (round(len(reach) / len(ok), 4) if ok else None)}
    return {
        "episodes": len(ok),
        "uncomputable": len(rows) - len(ok),
        "by_minimum_improvement": by_bucket,
        "reachable_at_some_maker_price": len(reach),
        "needing_a_cross": by_bucket.get("crossing_required", 0),
        "unreachable_at_any_maker_price": by_bucket.get(
            "unreachable_at_any_maker_price", 0),
        "median_pair_basis_when_reachable": (str(bases[len(bases) // 2])
                                             if bases else None),
        "pair_basis_min": (str(bases[0]) if bases else None),
        "pair_basis_max": (str(bases[-1]) if bases else None),
        "at_pair_ceiling": ceilings,
        "ceilings_are": ("ANALYTICAL BUCKETS for reading this table. They are "
                         "not gates, not proposed and nothing is wired to "
                         "them."),
        "labels": THEORETICAL,
    }


# ------------------------------------------------- economics, kept separate

def economics(fills: Sequence[Mapping[str, Any]], inv: Any,
              settlement: Mapping[str, Any]) -> Dict[str, Any]:
    """Trading economics and incentive economics, never merged.

    Rebates are computed from Gemini's PUBLISHED formula at the actual
    hypothetical execution prices, with the published eligibility band and the
    published rounding -- floored to the cent at the DAY, which is where the
    venue rounds. An accrual is not a rebate and a rebate is not trading P&L.
    """
    from src.gemini_execution import rebate_program as RP

    gross = D("0")
    maker_fee = D("0")
    rows = []
    for f in fills:
        q, p = _dec(f.get("qty")), _dec(f.get("price"))
        if q is None or p is None:
            continue
        gross += q * p
        maker_fee += q * p * RP.MAKER_RATE
        a = RP.accrual(q, p)
        rows.append({"qty": str(q), "price": str(p),
                     "eligible": bool((a.get("eligibility") or {}).get(
                         "eligible")),
                     "why_ineligible": (a.get("eligibility") or {}).get("why"),
                     "accrued_usd": a.get("accrued_usd")})
    day = RP.daily_accrual([{"contracts": r["qty"], "price": r["price"]}
                            for r in rows])
    return {
        "gross_filled_usd": str(gross),
        "gross_contracts": str(sum((_dec(f.get("qty")) or D("0"))
                                   for f in fills)),
        "maker_fees_usd": str(maker_fee),
        "maker_fee_rate": str(RP.MAKER_RATE),
        "rebate_accrued_usd": day.get("accrued_usd"),
        "rebate_payable_after_rounding_usd": day.get("would_pay_usd"),
        "rebate_pays_nothing": day.get("pays_nothing"),
        "rebate_shortfall_to_one_cent_usd": day.get(
            "shortfall_to_one_cent_usd"),
        "rebate_eligible_fills": sum(1 for r in rows if r.get("eligible")),
        "rebate_ineligible_fills": sum(1 for r in rows
                                       if not r.get("eligible")),
        "rebate_rows": rows,
        "settlement_matched": (settlement or {}).get(
            "matched_pair_economics"),
        "settlement_residual": (settlement or {}).get(
            "directional_residual_pnl"),
        "separation": ("trading economics and incentive economics are "
                       "different lines and are never summed. An accrual is "
                       "not a rebate; a rebate is not trading P&L; and "
                       "nothing here is realized."),
    }


def policy_census(windows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """EVERY primary output, per policy. The unit is the WINDOW.

    Independent windows, not seeds and not arm-replicated rows: an earlier
    pass counted 72 states where there were 3, because every arm replayed the
    same moments. The window is the thing the venue actually gave us.
    """
    from src.gemini_execution import continuous_probe as CP
    ok = [w for w in windows if w.get("sufficient")]
    if not ok:
        return {"independent_windows": 0, "labels": THEORETICAL}

    filled = [w for w in ok if w["fills"]]
    paired = [w for w in ok
              if _dec((w["inventory"] or {}).get("matched_qty")) > 0]
    matched_c = sum(_dec(w["inventory"]["matched_qty"]) or D("0") for w in ok)
    gross_c = sum(sum((_dec(f["qty"]) or D("0")) for f in w["fills"])
                  for w in ok)
    resid_c = sum(abs(_dec(w["inventory"]["directional_residual"]) or D("0"))
                  for w in ok)
    cap = sum(_dec(w["inventory"]["total_spend_usd"]) or D("0") for w in ok)
    bases = sorted(b for b in
                   (_dec((w["inventory"] or {}).get(
                       "effective_matched_pair_cost")) for w in ok)
                   if b is not None)
    econ = [w.get("economics") or {} for w in ok]
    fee = sum(_dec(e.get("maker_fees_usd")) or D("0") for e in econ)
    acc = sum(_dec(e.get("rebate_accrued_usd")) or D("0") for e in econ)
    pay = sum(_dec(e.get("rebate_payable_after_rounding_usd")) or D("0")
              for e in econ)
    res_pnl = []
    for w in ok:
        r = ((w.get("settlement") or {}).get(
            "directional_residual_pnl") or {})
        v = _dec(r.get("gross_usd"))
        if v is not None:
            res_pnl.append(v)

    return {
        "policy": ok[0].get("policy"),
        "independent_windows": len(ok),
        "windows_filled": len(filled),
        "windows_paired": len(paired),
        "initial_fills": sum(w["initial_fills"] for w in ok),
        "complete_fills": sum(w["complement_fills"] for w in ok),
        "extend_fills": sum(w["extend_fills"] for w in ok),
        "extends_vetoed": sum(w["extends_vetoed"] for w in ok),
        "matched_contracts": str(matched_c),
        "gross_contracts": str(gross_c),
        "matched_over_gross": (str(round(matched_c / gross_c, 4))
                               if gross_c > 0 else None),
        "pair_basis_distribution": {
            "n": len(bases),
            "min": (str(bases[0]) if bases else None),
            "median": (str(bases[len(bases) // 2]) if bases else None),
            "max": (str(bases[-1]) if bases else None),
            "at_or_below_par": sum(1 for b in bases if b <= D("1")),
        },
        "terminal_residual_contracts": str(resid_c),
        "capital_at_risk_usd": str(cap),
        "settlement_residual_pnl_usd": (str(sum(res_pnl, D("0")))
                                        if res_pnl else None),
        "settlement_residual_windows": len(res_pnl),
        "maker_fees_usd": str(fee),
        "maker_rebate_accrued_usd": str(acc),
        "maker_rebate_payable_after_rounding_usd": str(pay),
        "fills_resting_on_an_unknown_queue": sum(
            w["fills_resting_on_an_unknown_queue"] for w in ok),
        "requotes": sum(w["requotes"] for w in ok),
        "insufficient_windows_skipped": len(windows) - len(ok),
        "labels": ("matched-pair economics, directional-residual exposure, "
                   "fees and rebate accruals are SEPARATE lines and are never "
                   "summed. Nothing here is realized. " + THEORETICAL),
    }
