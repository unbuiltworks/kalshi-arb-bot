"""AUDIT of what the frozen $6.1167253591/window actually contains.

NOT a change to the frozen baseline. This walks the frozen fill model exactly
as `pmus_native_parity.replay_window` walks it -- that arm is 25/25 exact
against the scorer -- and records, for every maker fill it grants, whether a
REAL ORDER of that quantity would still have had quantity left to fill.

The frozen engine sets `resting[side] = {"px","qty"}` and, on a later
observation whose touch is unchanged, takes the `already resting exactly
there` branch and emits NO new REST intent. It also never clears `resting` on
a fill. So `maker_fill` can be granted the SAME clip repeatedly with no
re-quote in between. A venue order cannot do that: it fills once and is
terminal.

PARTITION
  A  RE-QUOTED       a REST intent was emitted for this side at or after the
                     previous fill, so a real new order justifies this fill
  B  PERSISTENT_QTY  the current order still had unfilled quantity: a partial
                     fill leaves a live remainder, which is executable
  C  PHANTOM         the order's quantity was exhausted and no new REST intent
                     intervened -- frozen grants a fill no live order could
                     have received
"""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P

A, B, C = "A_REQUOTED", "B_PERSISTENT_QTY", "C_PHANTOM"


def walk(rows, *, winner, clip=E.CLIP, suppress=(), replenish_cap=False):
    """Frozen's own loop, instrumented. `suppress` drops those categories."""
    eng = E.NativeEngine(clip=clip, prospective_cap=True)
    eng.prospective_cap = False
    venue = P.ReplayVenue(fill_model="queue_aware")
    ladder = None
    events = []
    # per side: the quote we believe is live, and how much of it has filled
    live = {"up": None, "down": None}
    t = 0

    for row in rows:
        if row.get("kind") == "book":
            ladder = row
            eng.ladder = row
            continue
        if row.get("kind") != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        t += 1

        def _tk(it):
            return it["qty"], it["cost"], it["fee"]

        try:
            intents = eng.on_book(row, ladder, execute_taker=_tk)
        except E.StaleBook:
            continue
        for it in intents:
            if it.kind == E.REST:
                # a NEW order for this side at this price
                live[it["side"]] = {"px": D(str(it["price"])),
                                    "qty": D(str(it["qty"])), "filled": D(0),
                                    "t": t}
            elif it.kind == E.CANCEL:
                live[it["side"]] = None
        if eng.locked_at is not None:
            continue

        for side in ("up", "down"):
            st = eng.resting.get(side)
            if not st:
                continue
            got = venue.fills_for(eng, row, side, D(str(st["px"])),
                                  D(str(st["qty"])), ladder)
            if got is None:
                continue
            qty, fpx = got
            qty = D(str(qty))
            if not eng._cap_permits(side, qty):
                continue

            o = live.get(side)
            px = D(str(st["px"]))
            if o is None or o["px"] != px:
                cat = C                      # no live order at this price
            elif o["filled"] + qty <= o["qty"]:
                cat = B if o["filled"] > 0 else A
            else:
                cat = C                      # the order is exhausted
            before = {"up": eng.bk.q["up"], "down": eng.bk.q["down"]}

            if cat in suppress:
                events.append({"t": t, "side": side, "px": str(px),
                               "qty": str(qty), "fill_px": str(fpx),
                               "cat": cat, "applied": False,
                               "inv_before": {k: str(v) for k, v in before.items()},
                               "inv_after": {k: str(v) for k, v in before.items()},
                               "order_qty": (None if o is None else str(o["qty"])),
                               "order_filled": (None if o is None else str(o["filled"])),
                               "requote_t": (None if o is None else o["t"])})
                continue
            if cat == C and replenish_cap and not eng._cap_permits(side, clip):
                # variant 3: the refill becomes an EXPLICIT replenishment, so
                # it must pass the cap as a fresh clip would at decision time
                events.append({"t": t, "side": side, "px": str(px),
                               "qty": str(qty), "cat": cat, "applied": False,
                               "refused": "REPLENISH_CAP",
                               "inv_before": {k: str(v) for k, v in before.items()},
                               "inv_after": {k: str(v) for k, v in before.items()}})
                continue

            eng.on_fill(side, qty, qty * fpx, maker=True)
            if o is not None and o["px"] == px:
                o["filled"] += qty
            else:
                live[side] = {"px": px, "qty": D(str(st["qty"])),
                              "filled": qty, "t": t}
            after = {"up": eng.bk.q["up"], "down": eng.bk.q["down"]}
            events.append({"t": t, "side": side, "px": str(px),
                           "qty": str(qty), "fill_px": str(fpx), "cat": cat,
                           "applied": True,
                           "inv_before": {k: str(v) for k, v in before.items()},
                           "inv_after": {k: str(v) for k, v in after.items()},
                           "order_qty": (None if o is None else str(o["qty"])),
                           "order_filled_before": (None if o is None
                                                   else str(o["filled"] - qty)),
                           "requote_t": (None if o is None else o["t"]),
                           "exhausted": bool(o is not None
                                             and o["filled"] > o["qty"])})
    res = eng.settle(winner)
    return res, events


def run(suppress=(), replenish_cap=False):
    from scripts.audit_economic_complete import load, USABLE
    by = load()
    out, all_ev = [], []
    for slug, w in USABLE.items():
        res, ev = walk(by[slug], winner=w, suppress=suppress,
                       replenish_cap=replenish_cap)
        for e in ev:
            e["window"] = slug[-5:]
        all_ev += ev
        out.append({"window": slug[-5:], "net": P.net_of(res),
                    "completions": res.get("executable_states"),
                    "matched_pnl": res.get("matched_pnl"),
                    "resid_pnl": res.get("resid_pnl"),
                    "fees": res.get("fees")})
    n = len(out) or 1
    return {"mean_net": sum((r["net"] for r in out), D(0)) / n,
            "completions": sum(int(r["completions"] or 0) for r in out),
            "matched_pnl": sum((r["matched_pnl"] for r in out), D(0)) / n,
            "resid_pnl": sum((r["resid_pnl"] for r in out), D(0)) / n,
            "fees": sum((r["fees"] for r in out), D(0)) / n,
            "per_window": out, "events": all_ev}


if __name__ == "__main__":
    base = run()
    ev = base["events"]
    applied = [e for e in ev if e["applied"]]
    from collections import Counter
    cnt = Counter(e["cat"] for e in applied)
    print("FROZEN, as-is: mean %s completions %s" % (base["mean_net"],
                                                     base["completions"]))
    print("maker fills granted: %d  ->  %s" % (len(applied), dict(cnt)))
    print()
    no_c = run(suppress=(C,))
    print("WITHOUT category C: mean %s completions %s" % (no_c["mean_net"],
                                                          no_c["completions"]))
    rep = run(replenish_cap=True)
    print("C AS EXPLICIT REPLENISHMENT: mean %s completions %s"
          % (rep["mean_net"], rep["completions"]))
    with open("/tmp/frozen_refill_events.json", "w") as fh:
        json.dump(ev, fh, default=str)
    print("\nevents written: %d" % len(ev))
