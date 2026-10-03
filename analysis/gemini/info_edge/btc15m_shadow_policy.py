"""The smallest executable continuous-inventory policy, in SHADOW on BTC15m.

SHADOW ONLY. Nothing here can reach a venue: there is no transport import, no
signer, no order path. A test asserts that.

NO SECOND ENGINE. The action vocabulary and the OPEN/EXTEND/COMPLETE
classification are IMPORTED from `src.gemini_execution.continuous_probe`
(`classify`), and the inventory accounting is `fi_core.Inventory` with its
`snapshot()` and `incremental_effect()`. This file contributes the POLICY --
when to act and on what evidence -- and nothing else.

WHAT THE POLICY IS, in full:

  OPEN        one bounded initial position, only when the book is COHERENT
              (two-sided, not crossed) and the chosen side is ACTUALLY
              EXECUTABLE. Fixed small size; no sizing optimisation.
  EXTEND      a further same-side buy ONLY when it improves the
              weighted-average acquisition price of the existing residual
              side by at least EXTEND_MIN_AVG_IMPROVEMENT, inside hard
              residual-principal and total-capital bounds. It is NOT required
              to improve instantaneous worst-case settlement P&L, which by
              construction it cannot. NEVER because the position is losing:
              `want_extend_avg` reads the residual AVERAGE and the OFFERED
              PRICE and has no access to an unrealised mark.
  COMPLEMENT  the opposite side is evaluated on EVERY tick. Preferred when it
              reduces residual AND raises the worst-case settlement floor.
              PARTIAL complements are allowed; nothing must flatten at once.
  LOCK        when BOTH settlement outcomes are positive after confirmed
              fees, stop adding directional risk. A locked book is NEVER
              unlocked to generate another trade.

EXECUTION, first implementation: MARKETABLE LIMIT with a HARD MAXIMUM price,
so that "does the inventory policy work" is separable from "does maker
queueing work". No unrestricted market order exists in this file. At every
action the MAKER COUNTERFACTUAL is recorded -- what the same decision would
have cost resting at the bid, and what rebate it would have been eligible for
-- so maker execution can be evaluated later WITHOUT changing the policy.

RISK, hard caps, all of them checked together:
  total dollars per market, directional residual, same-side consecutive
  extensions, number of actions, worst-case settlement loss.
No martingale sizing. Size is a CONSTANT; there is no progression anywhere.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, "analysis/gemini/favorite_inventory")
from fi_core import Inventory, make_fill, maker_fee, rebate, taker_fee  # noqa

from src.gemini_execution.continuous_probe import (COMPLETE, EXTEND,  # noqa
                                                   HOLD, OPEN)

SYM = re.compile(r"^GEMI-(BTC15M\d{10})-UP$")

# ------------------------------------------------------------------ policy
#: fixed shadow size. A CONSTANT. There is no progression and no sizing rule.
LOT = D("2")

#: OPEN only inside this TTE band: late enough that a strike exists, early
#: enough that a complement has time to appear. 15m window = 900 s.
OPEN_TTE_LO, OPEN_TTE_HI = 120.0, 840.0

#: marketable limit: never pay more than this for either side.
HARD_MAX_PRICE = D("0.90")

#: EXTEND acceptance. FROZEN, PREREGISTERED, NOT FITTED.
#:
#: The worst-case-floor criterion that used to live here was structurally
#: wrong and is GONE, not loosened: every same-side purchase necessarily
#: worsens the opposite outcome's settlement P&L at that instant, so a gate
#: demanding the floor be preserved can essentially never fire.
#:
#: The replacement concept: an EXTEND must IMPROVE THE WEIGHTED-AVERAGE
#: ACQUISITION PRICE of the existing residual side by at least this much.
#: That is a statement about future locking capacity, not about instantaneous
#: P&L. 5 cents, chosen before looking at the windows and not tuned on them.
EXTEND_MIN_AVG_IMPROVEMENT = D("0.05")

#: EXTENDs permitted before a complement must intervene.
MAX_EXTENDS_BEFORE_COMPLEMENT = 2

#: hard residual principal at risk, dollars. A residual of n contracts bought
#: at average A can lose n*A if it settles the other way.
MAX_RESIDUAL_PRINCIPAL = D("4.00")

#: COMPLEMENT acceptance: the matched pair cost after the complement must be
#: MATERIALLY BELOW $1 (CLAUDE.md spring discipline). Raising the worst-case
#: floor is NOT sufficient -- completing a residual always raises the floor,
#: including at a pair cost above $1, which locks a guaranteed loss. This is
#: the gate that makes COMPLETE patient instead of immediate.
MAX_PAIR_COST = D("0.96")

# ------------------------------------------------------------------- risk
MAX_TOTAL_COST = D("12.00")      # dollars per market
MAX_RESIDUAL = D("6")            # directional residual, contracts
MAX_ACTIONS = 8                  # actions per market
MAX_WORST_CASE_LOSS = D("-6.00")  # worst-case settlement floor


def coherent(bid: Optional[D], ask: Optional[D]) -> bool:
    """Two-sided and not crossed. An unreadable book is never traded into."""
    return bid is not None and ask is not None and bid < ask


def risk_block(inv: Inventory, actions: int, run_len: int) -> List[str]:
    """Hard caps. The consecutive-same-side cap lives in the EXTEND rule
    (MAX_EXTENDS_BEFORE_COMPLEMENT) rather than here, because a cap that
    HALTS the window would also forbid the complement that is supposed to
    follow the extensions."""
    hit = []
    if actions >= MAX_ACTIONS:
        hit.append("max_actions %d" % MAX_ACTIONS)
    if inv.total_cost > MAX_TOTAL_COST:
        hit.append("total_cost %s > %s" % (inv.total_cost, MAX_TOTAL_COST))
    if inv.residual > MAX_RESIDUAL:
        hit.append("residual %s > %s" % (inv.residual, MAX_RESIDUAL))
    rp = inv.residual * (inv.avg_up if inv.residual_side == "UP"
                         else inv.avg_down) if inv.residual_side else D("0")
    if rp > MAX_RESIDUAL_PRINCIPAL:
        hit.append("residual_principal %s > %s" % (rp, MAX_RESIDUAL_PRINCIPAL))
    wc = min(inv.up_settlement_pnl, inv.down_settlement_pnl)
    if inv.fills and wc < MAX_WORST_CASE_LOSS:
        hit.append("worst_case %s < %s" % (wc, MAX_WORST_CASE_LOSS))
    return hit


def locked(inv: Inventory) -> bool:
    """BOTH outcomes positive after confirmed fees. Never unlocked."""
    return (bool(inv.fills)
            and inv.up_settlement_pnl > 0 and inv.down_settlement_pnl > 0)


def want_complement(eff: Dict[str, Any]) -> Dict[str, Any]:
    """A complement is accepted when it REDUCES residual, RAISES the
    worst-case floor, AND lands the matched pair cost at or below
    MAX_PAIR_COST. Partial is fine; nothing must flatten at once.

    The pair-cost condition is not decoration. Completing a residual ALWAYS
    raises the worst-case floor -- even at a pair cost of 1.05, where the
    "improvement" is locking in a certain loss. Without this gate the policy
    degenerates into instant round-tripping at a negative edge.
    """
    reduces = eff["d_residual"] < 0
    raises = eff["worst_case_after"] > eff["worst_case_before"]
    pair = eff["matched_pair_cost_after"]
    cheap = pair is not None and D(str(pair)) <= MAX_PAIR_COST
    ok = reduces and raises and cheap
    return {"accept": bool(ok),
            "reduces_residual": reduces, "raises_floor": raises,
            "pair_cost_after": (None if pair is None else str(pair)),
            "pair_cost_ok": bool(cheap),
            "d_floor": eff["worst_case_after"] - eff["worst_case_before"],
            "why": ("reduces residual by %s, raises the floor by %s, and the "
                    "matched pair costs %s <= %s"
                    % (-eff["d_residual"],
                       eff["worst_case_after"] - eff["worst_case_before"],
                       pair, MAX_PAIR_COST)
                    if ok else
                    "REFUSED: reduces_residual=%s raises_floor=%s "
                    "pair_cost_after=%s (needs <= %s)"
                    % (reduces, raises, pair, MAX_PAIR_COST))}


def required_complement_price(inv: Inventory, qty: D,
                              target: D = MAX_PAIR_COST) -> Optional[D]:
    """The HIGHEST price we could pay for `qty` of the opposite side and
    still land a matched book at or below `target`.

    This is the number that gives EXTEND a measurable purpose: it is the
    width of the complement opportunity set. Own 2 UP at 0.45 and DOWN must
    come to <= 0.51; drop the UP average to 0.375 and DOWN as dear as 0.585
    still locks a 0.96 book. The extension bought 7.5 cents of opportunity.

    Solved on PRINCIPAL averages (fi_core `avg_up`/`avg_down` exclude fees),
    which is the same basis the 0.96 discipline is written on.

    Returns None when there is no residual side to complement.
    """
    rs = inv.residual_side
    if rs is None:
        return None
    if rs == "UP":
        own_avg, other_q, other_c = inv.avg_up, inv.q_down, inv.c_down
    else:
        own_avg, other_q, other_c = inv.avg_down, inv.q_up, inv.c_up
    # need own_avg + (other_c + qty*p)/(other_q + qty) <= target
    return ((target - own_avg) * (other_q + qty) - other_c) / qty


def want_extend_avg(inv: Inventory, side: str, px: D,
                    qty: D, n_extends: int) -> Dict[str, Any]:
    """The FROZEN 5-cent average-improvement EXTEND rule.

    Hard requirements, all of them:
      * same side as the current residual
      * the new price beats the existing residual average by >= 5 cents
      * fewer than MAX_EXTENDS_BEFORE_COMPLEMENT extensions so far
      * residual principal at risk stays within MAX_RESIDUAL_PRINCIPAL
      * fixed qty (LOT is a constant; no progression exists in this file)

    Deliberately NOT a requirement: that the instantaneous worst case be
    preserved. By construction it cannot be.

    Nothing here is a function of whether the position is losing. The rule
    reads the residual AVERAGE and the offered PRICE; it has no access to an
    unrealised mark, so "price moved against us" can never by itself be the
    reason an extension is accepted -- only "this lot is 5c cheaper than what
    we already own" can be.
    """
    rs = inv.residual_side
    same = rs == side
    avg = (inv.avg_up if side == "UP" else inv.avg_down)
    improves = avg is not None and px <= avg - EXTEND_MIN_AVG_IMPROVEMENT
    room = n_extends < MAX_EXTENDS_BEFORE_COMPLEMENT
    new_resid = inv.residual + qty if same else None
    new_avg = (((inv.c_up if side == "UP" else inv.c_down) + qty * px)
               / ((inv.q_up if side == "UP" else inv.q_down) + qty))
    within = (new_resid is not None
              and new_resid * new_avg <= MAX_RESIDUAL_PRINCIPAL)
    ok = bool(same and improves and room and within)
    req_before = required_complement_price(inv, qty)
    return {
        "accept": ok,
        "same_side_as_residual": same,
        "residual_avg_before": (None if avg is None else str(avg)),
        "extension_price": str(px),
        "avg_improvement": (None if avg is None else str(avg - px)),
        "required_improvement": str(EXTEND_MIN_AVG_IMPROVEMENT),
        "improves_avg": bool(improves),
        "residual_avg_after": str(new_avg),
        "required_complement_price_for_0.96_before":
            (None if req_before is None else str(req_before)),
        "extends_so_far": n_extends,
        "extend_room": bool(room),
        "residual_principal_after": (None if new_resid is None
                                     else str(new_resid * new_avg)),
        "within_residual_principal_cap": bool(within),
        "why": (("avg %s -> %s, a %s improvement >= %s; residual principal %s "
                 "within %s" % (avg, new_avg, (avg - px) if avg else None,
                                EXTEND_MIN_AVG_IMPROVEMENT,
                                new_resid * new_avg, MAX_RESIDUAL_PRINCIPAL))
                if ok else
                ("REFUSED: same_side=%s improves_avg=%s (price %s vs avg %s, "
                 "need <= %s) room=%s within_cap=%s"
                 % (same, improves, px, avg,
                    (None if avg is None
                     else avg - EXTEND_MIN_AVG_IMPROVEMENT), room, within))),
    }


def maker_counterfactual(side: str, bid: D, qty: D) -> Dict[str, Any]:
    """What the SAME decision would have cost resting at the bid instead.
    Recorded at every action; it never influences the policy."""
    return {"maker_price": str(bid), "maker_fee": str(maker_fee(qty, bid)),
            "rebate_if_credited_NOT_REALIZED": str(rebate(qty, bid)),
            "labels": ("counterfactual only. No fill is claimed: resting at "
                       "the bid is not a fill, and the rebate is not realized "
                       "until credited.")}


# ------------------------------------------------------------------ replay
def _apply(book, rows):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def windows_from_tape(outdir="data/gemini_infoedge_15m"):
    """Reconstruct each BTC15m window's WS book timeline from the live tape."""
    ev, expiry, settled = defaultdict(list), {}, {}
    for f in sorted(Path(outdir).glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                if (r.get("ticker") or "").startswith("BTC15M"):
                    settled[r["ticker"]] = r
                continue
            m = SYM.match(r.get("market") or "")
            if not m:
                continue
            tk = m.group(1)
            if k in ("window", "window_poll") and r.get("expiry"):
                from datetime import datetime
                expiry[tk] = datetime.fromisoformat(
                    r["expiry"].replace("Z", "+00:00")).timestamp()
            elif k in ("p_depth", "p_snapshot"):
                ev[tk].append(r)
    out = {}
    for tk, rows in ev.items():
        E = expiry.get(tk)
        if E is None:
            continue
        bids, asks = {}, {}
        seeded, last_u = False, None
        tl = []
        for r in sorted(rows, key=lambda x: x["t_ns"]):
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_snapshot":
                nb, na = {}, {}
                _apply(nb, r.get("bids"))
                _apply(na, r.get("asks"))
                bids, asks = nb, na
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                _apply(bids, r.get("b"))
                _apply(asks, r.get("a"))
                last_u = u
            b = max(bids) if bids else None
            a = min(asks) if asks else None
            tl.append((t, b, a,
                       bids.get(b) if b is not None else None,
                       asks.get(a) if a is not None else None))
        if tl:
            out[tk] = {"expiry": E, "timeline": tl,
                       "settled": settled.get(tk)}
    return out


def shadow_window(tk: str, w: Dict[str, Any],
                  allow_extend: bool = True) -> Dict[str, Any]:
    """Run the policy over ONE window. Returns the full sequential ledger.

    `allow_extend=False` reproduces the previous OPEN -> COMPLEMENT -> LOCK
    policy exactly, so the two can be settled side by side on the same tape.
    """
    E = w["expiry"]
    inv = Inventory()
    ledger: List[Dict[str, Any]] = []
    actions = 0
    run_leg, run_len = None, 0
    n_extends = 0
    extends: List[Dict[str, Any]] = []
    #: WAIT rows are sampled, not emitted every tick, so the ledger shows the
    #: state machine declining and why without drowning the trace.
    WAIT_EVERY = 60.0
    last_wait = [None]
    refusals: List[str] = []

    for (t, b, a, bsz, asz) in w["timeline"]:
        tte = E - t
        if tte <= 0:
            break
        bid = D(str(round(b, 2))) if b is not None else None
        ask = D(str(round(a, 2))) if a is not None else None

        def row(action, side=None, price=None, qty=None, reason="", extra=None):
            s = inv.snapshot()
            r = {"t": round(t, 2), "tte": round(tte, 1),
                 "ws_bid": (None if bid is None else str(bid)),
                 "ws_ask": (None if ask is None else str(ask)),
                 "action": action, "side": side,
                 "price": (None if price is None else str(price)),
                 "qty": (None if qty is None else str(qty)),
                 "reason": reason,
                 "UP_qty": s["UP_qty"], "DOWN_qty": s["DOWN_qty"],
                 "total_cost": s["total_cost"], "residual": s["residual_qty"],
                 "pnl_if_UP": s["UP_settlement_PnL"],
                 "pnl_if_DOWN": s["DOWN_settlement_PnL"],
                 "worst_case": s["worst_case"], "state": s["state"]}
            if extra:
                r.update(extra)
            ledger.append(r)
            return r

        if not coherent(bid, ask):
            continue                      # never trade into an unreadable book
        if locked(inv):
            row(HOLD, reason="LOCKED: both outcomes positive after fees; a "
                             "locked book is never unlocked for another trade")
            break
        blocked = risk_block(inv, actions, run_len)
        if blocked:
            row(HOLD, reason="RISK_BLOCK: " + "; ".join(blocked))
            break

        # the two candidate buys, marketable limit, hard max price.
        # UP costs the ask; DOWN costs 1 - bid.
        cands = []
        if ask <= HARD_MAX_PRICE:
            cands.append(("UP", ask, asz))
        dn = D("1") - bid
        if dn <= HARD_MAX_PRICE:
            cands.append(("DOWN", dn, bsz))
        if not cands:
            continue

        acted = False
        refusals = []
        for side, px, sz in cands:
            if sz is None or D(str(sz)) < LOT:
                continue                  # not actually executable for our lot
            outcome = "Up" if side == "UP" else "Down"
            from src.gemini_execution.continuous_probe import Inventory as CPInv
            cp = CPInv(yes_qty=inv.q_up, no_qty=inv.q_down)
            act = __import__(
                "src.gemini_execution.continuous_probe",
                fromlist=["classify"]).classify(outcome, cp)
            eff = inv.incremental_effect(side, px, LOT, mode="taker")
            mc = maker_counterfactual(side, bid if side == "UP" else D("1") - ask,
                                      LOT)

            if act == OPEN:
                if inv.fills:
                    continue              # one bounded OPEN per market
                if not (OPEN_TTE_LO <= tte <= OPEN_TTE_HI):
                    continue
                inv.add(make_fill(t, side, "taker", px, LOT, "shadow OPEN"))
                inv.assert_identities()
                actions += 1
                run_leg, run_len = side, 1
                row(OPEN, side, px, LOT,
                    "book coherent, %s executable for %s at <= hard max %s"
                    % (side, LOT, HARD_MAX_PRICE),
                    {"maker_counterfactual": mc, "incremental": eff})
                acted = True
                break

            if act == EXTEND:
                if not allow_extend:
                    refusals.append("EXTEND %s @ %s REFUSED: extensions "
                                    "disabled in this arm" % (side, px))
                    continue
                d = want_extend_avg(inv, side, px, LOT, n_extends)
                if not d["accept"]:
                    refusals.append("EXTEND %s @ %s %s" % (side, px, d["why"]))
                    continue
                before = inv.snapshot()
                req_before = required_complement_price(inv, LOT)
                inv.add(make_fill(t, side, "taker", px, LOT, "shadow EXTEND"))
                inv.assert_identities()
                actions += 1
                n_extends += 1
                run_len = run_len + 1 if run_leg == side else 1
                run_leg = side
                req_after = required_complement_price(inv, LOT)
                d["required_complement_price_for_0.96_after"] = (
                    None if req_after is None else str(req_after))
                r = row(EXTEND, side, px, LOT, "EXTEND: " + d["why"],
                        {"maker_counterfactual": mc, "incremental": eff,
                         "extend_rule": d,
                         "before": before, "after": inv.snapshot()})
                extends.append({
                    "t": r["t"], "tte": r["tte"], "side": side,
                    "old_avg": d["residual_avg_before"],
                    "extension_price": str(px),
                    "new_avg": d["residual_avg_after"],
                    "req_complement_before":
                        None if req_before is None else str(req_before),
                    "req_complement_after":
                        None if req_after is None else str(req_after),
                    "opportunity_set_widened_by":
                        (None if (req_before is None or req_after is None)
                         else str(req_after - req_before)),
                    "additional_residual_risk": str(LOT * px),
                    "residual_principal_after": d["residual_principal_after"],
                    "why": d["why"],
                    "d_worst_case": str(eff["worst_case_after"]
                                        - eff["worst_case_before"]),
                    "d_up": str(eff["d_up_settlement_pnl"]),
                    "d_down": str(eff["d_down_settlement_pnl"])})
                acted = True
                break

            if act == COMPLETE:
                d = want_complement(eff)
                if not d["accept"]:
                    refusals.append("COMPLEMENT %s @ %s %s"
                                    % (side, px, d["why"]))
                    continue
                inv.add(make_fill(t, side, "taker", px, LOT,
                                  "shadow COMPLEMENT"))
                inv.assert_identities()
                actions += 1
                n_extends = 0          # a complement re-opens the extend budget
                run_leg, run_len = side, 1
                row(COMPLETE, side, px, LOT, "COMPLEMENT: " + d["why"],
                    {"maker_counterfactual": mc, "incremental": eff})
                acted = True
                break
        if not acted:
            if refusals and (last_wait[0] is None
                             or t - last_wait[0] >= WAIT_EVERY):
                last_wait[0] = t
                row(HOLD, reason="WAIT (price not good enough yet): "
                                 + " | ".join(refusals))
            continue

    # ---- settlement decomposition
    s = w.get("settled") or {}
    res = s.get("resolution_side")
    out = {"ticker": tk, "arm": ("EXTEND" if allow_extend else "NO_EXTEND"),
           "ledger": ledger, "extends": extends,
           "n_actions": actions, "final": inv.snapshot(),
           "worst_exposure": str(min(
               (D(r["worst_case"]) for r in ledger
                if r["worst_case"] is not None), default=D("0"))),
           "resolution_side": res}
    if res in ("yes", "no"):
        oc = "UP" if res == "yes" else "DOWN"
        out["settlement"] = {
            "outcome": oc,
            "matched_pair_pnl": str(inv.matched_pnl()),
            "directional_residual_pnl": str(inv.residual_pnl_if(oc)),
            "fees": str(inv.fees),
            "maker_counterfactual_rebates_NOT_REALIZED": str(sum(
                (D(r["maker_counterfactual"]["rebate_if_credited_NOT_REALIZED"])
                 for r in ledger if r.get("maker_counterfactual")), D("0"))),
            "net_settlement_pnl": str(inv.net_if(oc)),
            "labels": ("SHADOW. No order was sent and nothing is realized. "
                       "matched and residual are never summed into one "
                       "number; the rebate line is a counterfactual."),
        }
    return out


def _print_ledger(r):
    print("--- %s   arm=%s   actions=%d   resolution=%s"
          % (r["ticker"], r["arm"], r["n_actions"], r["resolution_side"]))
    if not r["ledger"]:
        print("  (no coherent tradeable tick)")
        return
    print("  %-9s %6s %9s %-9s %-5s %-6s %-4s %6s %6s %9s %8s %9s %9s %9s"
          % ("t(rel)", "tte", "bid/ask", "action", "side", "price", "qty",
             "UPq", "DNq", "totcost", "resid", "ifUP", "ifDOWN", "worst"))
    t0 = r["ledger"][0]["t"]
    for x in r["ledger"]:
        print("  %-9.1f %6.1f %9s %-9s %-5s %-6s %-4s %6s %6s %9s %8s "
              "%9s %9s %9s"
              % (x["t"] - t0, x["tte"], "%s/%s" % (x["ws_bid"], x["ws_ask"]),
                 x["action"], x["side"] or "", x["price"] or "", x["qty"] or "",
                 x["UP_qty"], x["DOWN_qty"], x["total_cost"], x["residual"],
                 x["pnl_if_UP"], x["pnl_if_DOWN"], x["worst_case"]))
        if x["action"] == HOLD:
            print("      reason: %s" % x["reason"])
    for e in r["extends"]:
        print("  EXTEND @tte %.1f %s: old avg %s -> price %s -> new avg %s"
              % (e["tte"], e["side"], e["old_avg"], e["extension_price"],
                 e["new_avg"]))
        print("      required complement price for 0.96: %s -> %s "
              "(opportunity set widened by %s)"
              % (e["req_complement_before"], e["req_complement_after"],
                 e["opportunity_set_widened_by"]))
        print("      additional residual risk %s | residual principal now %s"
              % (e["additional_residual_risk"], e["residual_principal_after"]))
    if r.get("settlement"):
        st = r["settlement"]
        print("  SETTLEMENT (%s): matched %s | residual %s | fees %s | "
              "worst exposure %s | maker-cf rebate %s | NET %s"
              % (st["outcome"], st["matched_pair_pnl"],
                 st["directional_residual_pnl"], st["fees"],
                 r["worst_exposure"],
                 st["maker_counterfactual_rebates_NOT_REALIZED"],
                 st["net_settlement_pnl"]))


def main():
    ws = windows_from_tape()
    print("=" * 104)
    print("BTC15m CONTINUOUS-INVENTORY POLICY -- SHADOW. NO ORDER IS SENT.")
    print("EXTEND rule FROZEN: new_price <= residual_avg - %s. Not fitted to "
          "these windows." % EXTEND_MIN_AVG_IMPROVEMENT)
    print("COMPLEMENT discipline FROZEN at MAX_PAIR_COST = %s." % MAX_PAIR_COST)
    print("=" * 104)
    done = [(tk, w) for tk, w in ws.items() if w.get("settled")]
    print("windows with a reconstructed WS book: %d | settled: %d" % (len(ws), len(done)))
    print()

    results, rows = [], []
    for tk, w in sorted(done, key=lambda x: x[0]):
        a = shadow_window(tk, w, allow_extend=True)
        b = shadow_window(tk, w, allow_extend=False)
        results += [a, b]
        if not a["ledger"] and not b["ledger"]:
            continue
        _print_ledger(a)
        print()
        _print_ledger(b)
        print()
        def g(r, k):
            return (r.get("settlement") or {}).get(k, "-")
        rows.append((tk, a["resolution_side"], len(a["extends"]),
                     g(a, "matched_pair_pnl"), g(a, "directional_residual_pnl"),
                     g(a, "fees"), a["worst_exposure"],
                     g(a, "net_settlement_pnl"),
                     g(b, "matched_pair_pnl"), g(b, "directional_residual_pnl"),
                     g(b, "fees"), b["worst_exposure"],
                     g(b, "net_settlement_pnl")))

    print("=" * 104)
    print("EXTEND arm vs NO-EXTEND arm, same tape, same complement discipline")
    print("=" * 104)
    print("%-20s %-4s %3s | %8s %8s %6s %8s %8s | %8s %8s %6s %8s %8s"
          % ("window", "res", "ext", "matched", "resid", "fees", "worst",
             "NET", "matched", "resid", "fees", "worst", "NET"))
    tot_a = tot_b = D("0")
    for r in rows:
        print("%-20s %-4s %3d | %8s %8s %6s %8s %8s | %8s %8s %6s %8s %8s" % r)
        if r[7] != "-":
            tot_a += D(r[7])
        if r[12] != "-":
            tot_b += D(r[12])
    print("%-20s %-4s %3s | %8s %8s %6s %8s %8s | %8s %8s %6s %8s %8s"
          % ("TOTAL", "", "", "", "", "", "", tot_a, "", "", "", "", tot_b))
    print()
    print("SHADOW. n=%d windows. Nothing above is realized and no order was sent." % len(rows))
    Path("analysis/gemini/info_edge/btc15m_shadow_result.json").write_text(
        json.dumps(results, indent=2, default=str))
    print("wrote analysis/gemini/info_edge/btc15m_shadow_result.json")


if __name__ == "__main__":
    main()
