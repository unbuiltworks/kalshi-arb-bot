"""Replay the SECOND-SLOT decision over recorded market states.

Read-only. It opens no socket, uses no credential, submits nothing and
treats no grant as a fill. For every recorded decision tick in the
preserved BTC15m windows it reconstructs the live-order and claim state
from that window's own order records and asks the DEPLOYED
`second_leg_slot_refusal` whether the complement could have rested beside
the first leg at that instant -- then records the book, the price the
complement would have carried, and whether every other gate would have
let it through.

The one-order engine's actual behaviour at the same instants is the
comparison arm, taken from the same records rather than re-derived.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import os
from decimal import Decimal as D

from src import l5_two_sided_probe as TS

PRESERVED = "analysis/l5/residual_exit/preserved/*.ndjson"


def T(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def load(path):
    """Decisions, presubmits and order progress for one window."""
    dec, pre, prog = [], [], []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            d = json.loads(r["body"])
        except Exception:
            continue
        lab = r.get("label")
        if lab == "L5_LIVE_DECISION":
            dec.append((T(r["ts"]), d))
        elif lab == "L5_FILL_CALIB_PRESUBMIT" and d.get("kind") == "pre_submit":
            if not str(d.get("coid", "")).startswith("shadow|"):
                pre.append((T(r["ts"]), d))
        elif lab == "L5_LIVE_LIFECYCLE" and d.get("event") == "ORDER_PROGRESS":
            prog.append((T(r["ts"]), d))
    for s in (dec, pre, prog):
        s.sort(key=lambda x: x[0])
    return dec, pre, prog


def order_timeline(pre, prog):
    """coid -> {side, own, qty, submit, first_fill, terminal}."""
    out = {}
    for ts, d in pre:
        out[d["coid"]] = dict(side=d["side"], own=D(str(d["own_px"])),
                              qty=D(str(d["qty"])), submit=ts,
                              first_fill=None, terminal=None, cum=D(0))
    for ts, d in prog:
        o = out.get(d.get("coid"))
        if o is None:
            continue
        cum = D(str(d.get("cum_qty") or 0))
        if cum > o["cum"]:
            o["cum"] = cum
            if o["first_fill"] is None:
                o["first_fill"] = ts
        st = str(d.get("state") or "")
        if st in ("ORDER_STATE_FILLED", "ORDER_STATE_CANCELED",
                  "ORDER_STATE_CANCELLED", "ORDER_STATE_REJECTED"):
            o["terminal"] = ts
    return out


def live_at(orders, t):
    """The live_orders map the store would have held at instant t."""
    rows = {}
    for coid, o in orders.items():
        if o["submit"] > t:
            continue
        if o["terminal"] is not None and o["terminal"] <= t:
            continue
        rows[coid] = {"market": "M", "side": o["side"],
                      "price": str(o["own"]), "qty": str(o["qty"])}
    return rows


def claims_at(orders, t):
    """legs_claimed for the round: a side is claimed once its order was
    submitted. One round per window in every preserved record."""
    c = {TS.UP: False, TS.DOWN: False}
    for o in orders.values():
        if o["submit"] <= t:
            c[o["side"]] = True
    return c


def run():
    tick = D("0.01")
    dticks = int(os.getenv("L5_TS_DEPTH_TICKS", "2"))
    report = []
    for path in sorted(glob.glob(PRESERVED)):
        win = path.split("_")[-1].replace(".ndjson", "")
        dec, pre, prog = load(path)
        if not pre:
            continue
        orders = order_timeline(pre, prog)
        grants, refusals = [], collections.Counter()
        for t, d in dec:
            bk = d.get("book") or {}
            if not (bk.get("bid") and bk.get("ask")):
                continue
            rows = live_at(orders, t)
            if len(rows) != 1:
                refusals["NOT_EXACTLY_ONE_LIVE:%d" % len(rows)] += 1
                continue
            held = list(rows.values())[0]["side"]
            want = TS.DOWN if held == TS.UP else TS.UP
            why = TS.second_leg_slot_refusal(
                market="M", side=want, live_orders=rows,
                legs_claimed=claims_at(orders, t))
            if why is not None:
                refusals[str(why)] += 1
                continue
            # the price the complement would carry AT THIS INSTANT
            px = TS.quote_own_price(want, bid=bk["bid"], ask=bk["ask"],
                                    tick=tick, depth_ticks=dticks)
            basis = D(str(list(rows.values())[0]["price"]))
            grants.append(dict(t=t, side=want, px=px, held=held,
                               basis=basis,
                               pair=(None if px is None else basis + px),
                               bid=bk["bid"], ask=bk["ask"],
                               age=d.get("stream_age_s")))
        report.append((win, grants, refusals, orders))
    return report


if __name__ == "__main__":
    cap = TS.max_pair_cost()
    print("DEPLOYED second_leg_slot_refusal replayed on recorded states")
    print("flag currently: enabled=%s ; pair cap %s ; depth_ticks %s\n"
          % (TS.two_order_pair_enabled(), cap,
             os.getenv("L5_TS_DEPTH_TICKS", "2")))
    print("%-7s %7s %9s %9s %9s  %s" % (
        "win", "grants", "pair<=cap", "min pair", "median", "top refusal"))
    tot_g = tot_ok = 0
    for win, grants, refusals, orders in run():
        ok = [g for g in grants if g["pair"] is not None and g["pair"] <= cap]
        pairs = sorted(g["pair"] for g in grants if g["pair"] is not None)
        top = refusals.most_common(1)
        tot_g += len(grants)
        tot_ok += len(ok)
        print("%-7s %7d %9d %9s %9s  %s" % (
            win, len(grants), len(ok),
            pairs[0] if pairs else "-",
            pairs[len(pairs) // 2] if pairs else "-",
            ("%s x%d" % top[0]) if top else "-"))
    print("\ntotal slot grants on recorded states: %d" % tot_g)
    print("of those, a complement priced AT THAT INSTANT clears the %s cap: %d"
          % (cap, tot_ok))
    print("\nA GRANT IS NOT A FILL AND NOT AN ORDER. It says only that the "
          "ceiling\nwould have permitted a second resting order at that "
          "instant.")


def compare():
    """First grant vs what the one-order engine actually did, same states."""
    cap = TS.max_pair_cost()
    print("\n\nHEAD TO HEAD on identical recorded market states")
    print("%-7s %-12s %-8s %-7s | %-12s %-8s %-7s %s" % (
        "win", "2-ord first", "px", "pair", "1-ord actual", "px", "pair",
        "filled?"))
    for path in sorted(glob.glob(PRESERVED)):
        win = path.split("_")[-1].replace(".ndjson", "")
        dec, pre, prog = load(path)
        if not pre:
            continue
        orders = order_timeline(pre, prog)
        rep = [r for r in run() if r[0] == win]
        grants = rep[0][1] if rep else []
        # the one-order engine's actual complement: the SECOND order submitted
        subs = sorted(orders.items(), key=lambda kv: kv[1]["submit"])
        comp = subs[1][1] if len(subs) > 1 else None
        g = grants[0] if grants else None
        basis = subs[0][1]["own"] if subs else None
        print("%-7s %-12s %-8s %-7s | %-12s %-8s %-7s %s" % (
            win,
            g["t"].strftime("%H:%M:%S") if g else "-",
            g["px"] if g else "-",
            g["pair"] if g else "-",
            comp["submit"].strftime("%H:%M:%S") if comp else "-",
            comp["own"] if comp else "-",
            (basis + comp["own"]) if (comp and basis) else "-",
            ("yes" if (comp and comp["cum"] > 0) else "NO") if comp else "-"))
