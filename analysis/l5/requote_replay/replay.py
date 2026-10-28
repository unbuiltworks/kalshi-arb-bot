"""Counterfactual replay of the four live windows of 2026-10-28.

TWO CORRECTIONS, TWO DIFFERENT WINDOWS. They do not overlap, which is the
first finding and the reason they are replayed together rather than
separately:

  * the CAMPAIGN IDENTITY fix removes `NO_COMPLETION_CAPACITY_RESERVED`,
    which refused 25 decisions in windows 3 and 4 because 27 claim locks
    from earlier campaigns were counted against this campaign's budget;
  * the DOWNWARD-ONLY REQUOTE removes `PRESEND_WOULD_CROSS`, which refused
    27 decisions in windows 1 and 2 against a price the book had left.

WHAT A FILL MAY BE CLAIMED FROM, and nothing else. A post-only maker order
quoted `depth_ticks` BEHIND the touch does not fill because it is valid; it
fills only if the market comes to it. The recorded books are snapshots at
decision times, so the only fill evidence they can carry is TRAVERSAL: a
resting UP bid at own `p` is filled when a later snapshot inside its TTL
shows the best bid strictly BELOW `p`, because the book cannot get there
without passing through our level. A DOWN leg rests as an ASK at wire
`1 - p` and is filled when a later best ask is strictly ABOVE it.

That rule is deliberately one-sided:

  * it CANNOT invent a fill, since it needs the touch to have moved past a
    price we actually quoted;
  * it UNDERSTATES fills, because the snapshots are sparse -- the touch can
    dip through a level and recover between two records and leave no trace.

So every fill below is traversal-supported, every "no fill" is "not
supported by this tape" rather than "proved absent", and the net figures are
a FLOOR on the requote's throughput effect, not an estimate of it. Nothing
here is realized, settled or confirmed: it is a replay.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))

TICK = D("0.01")
DEPTH_TICKS = 2
TTL_S = 120.0
ENTRY_CUTOFF_S = 600.0
COMPLETE_CUTOFF_S = 720.0
PAIR_CAP = D("0.96")
FLOOR = D("0.20")
FEE_PER_ORDER = D("0.01")
MAX_BOOK_AGE_S = D("2.0")

WINDOW_START = {           # UTC seconds-of-day of each window's open
    "0015z": 15 * 60, "0030z": 30 * 60,
    "0045z": 45 * 60, "0100z": 60 * 60,
}


def secs(ts: str) -> float:
    hh, mm, ss = ts[11:19].split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def load():
    rows = {}
    for name in ("presend_cross_20261028.jsonl", "decisions_20261028.jsonl"):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            continue
        for line in open(p):
            r = json.loads(line)
            if not r.get("market") or r.get("bid") is None:
                continue
            rows[(r["t"], r.get("key"))] = r
    out = sorted(rows.values(), key=lambda r: r["t"])
    for r in out:
        r["w"] = r["market"].split("-")[-1]
        r["sod"] = secs(r["t"])
    return [r for r in out if r["w"] in WINDOW_START]


def touch_own(side, bid, ask):
    return D(bid) if side == "UP" else (D(1) - D(ask))


def quote_own(side, bid, ask):
    return touch_own(side, bid, ask) - D(DEPTH_TICKS) * TICK


def crosses(side, own, bid, ask):
    wire = own if side == "UP" else (D(1) - own)
    return wire >= D(ask) if side == "UP" else wire <= D(bid)


def requote(side, refused_own, bid, ask, age, completing):
    """The deployed gate stack of `presend_requote`, in order."""
    if age is None or D(str(age)) > MAX_BOOK_AGE_S:
        return None, "BOOK_STALE"
    b, a = D(bid), D(ask)
    if b <= 0 or a <= 0 or a <= b:
        return None, "BOOK_NOT_TWO_SIDED"
    px = quote_own(side, bid, ask)
    if px >= refused_own:
        return None, "NOT_STRICTLY_LOWER"
    if px <= 0 or px >= 1:
        return None, "OUT_OF_RANGE"
    if crosses(side, px, bid, ask):
        return None, "STILL_CROSSES"
    if not completing and px < FLOOR:
        return None, "BELOW_FIRST_LEG_FLOOR"
    return px, None


def filled_by(rows, i, side, own, place_sod):
    """The first later snapshot whose touch traversed our level, or None."""
    wire = own if side == "UP" else (D(1) - own)
    for r in rows[i + 1:]:
        if r["sod"] - place_sod > TTL_S:
            return None
        if side == "UP" and D(r["bid"]) < wire:
            return r
        if side == "DOWN" and D(r["ask"]) > wire:
            return r
    return None


def replay_window(rows, w, open_post_divergence=True):
    """One window as an EVENT LOOP over the recorded snapshots.

    Modelled on the live engine rather than on the recorded refusals: one
    resting order at a time, COMPLETE before OPEN, the pair-cost ceiling on
    every complement, the entry cutoff on every opening and the completion
    cutoff on every complement. A requote OPEN is attempted only at a
    snapshot where the live run actually refused PRESEND_WOULD_CROSS, so no
    order is invented at a state the engine never reached.

    WHERE IT NECESSARILY DIVERGES. Once a counterfactual leg fills, the live
    run's later decisions were taken from a DIFFERENT inventory state, so
    their recorded sides and plan prices no longer apply. The loop therefore
    derives complements from the recorded BOOK -- which is state-independent
    -- and never reuses a recorded decision after the first divergence.
    """
    sub = [r for r in rows if r["w"] == w]
    t0 = WINDOW_START[w]
    out = {"window": w, "records": len(sub), "orders": [], "pairs": [],
           "notes": [], "tape_ends_s": None}
    if not sub:
        out["notes"].append("no recorded book snapshots")
        return out
    out["tape_ends_s"] = round(sub[-1]["sod"] - t0, 1)

    inv = {"UP": D(0), "DOWN": D(0)}
    basis = {"UP": None, "DOWN": None}
    live = None                    # the one resting order, or None
    diverged = False               # has any counterfactual leg filled yet
    matched = D(0)                 # the matched book already paired

    for i, r in enumerate(sub):
        inw = r["sod"] - t0
        # ---- 1. the resting order: fill, or expire
        if live is not None:
            side, px, qty, placed = (live["side"], live["px"],
                                     live["qty"], live["sod"])
            wire = px if side == "UP" else (D(1) - px)
            hit = ((side == "UP" and D(r["bid"]) < wire)
                   or (side == "DOWN" and D(r["ask"]) > wire))
            if hit:
                live["rec"]["filled"] = True
                live["rec"]["fill_t"] = r["t"][11:19]
                live["rec"]["fill_evidence"] = (
                    "touch traversed to %s/%s" % (r["bid"], r["ask"]))
                inv[side] += qty
                basis[side] = px
                diverged = True
                # ONLY THE INCREMENT. `min(UP, DOWN)` is the matched book
                # to date, not this fill's contribution: emitting it whole
                # each time counted the same DOWN basis into a second pair
                # and reported a 1.10 pair that was never bought.
                now_matched = min(inv["UP"], inv["DOWN"])
                new_matched = now_matched - matched
                if new_matched > 0:
                    cost = basis["UP"] + basis["DOWN"]
                    out["pairs"].append(
                        {"t": r["t"][11:19], "qty": str(new_matched),
                         "up_own": str(basis["UP"]),
                         "down_own": str(basis["DOWN"]),
                         "pair_cost": str(cost),
                         "gross_edge": str((D(1) - cost) * new_matched)})
                    matched = now_matched
                live = None
            elif r["sod"] - placed > TTL_S:
                live["rec"]["expired_s"] = round(r["sod"] - placed, 1)
                live = None
        if live is not None:
            continue                            # the slot is taken

        # ---- 2. COMPLETE before OPEN, which is the strategy's priority
        if inv["UP"] != inv["DOWN"] and inw <= COMPLETE_CUTOFF_S:
            need = "DOWN" if inv["UP"] > inv["DOWN"] else "UP"
            held = "UP" if need == "DOWN" else "DOWN"
            px = quote_own(need, r["bid"], r["ask"])
            pair = (basis[held] or D(0)) + px
            if px <= 0 or px >= 1:
                pass
            elif crosses(need, px, r["bid"], r["ask"]):
                pass
            elif pair > PAIR_CAP:
                out["notes"].append(
                    "+%.0fs complement %s %s refused PAIR_ABOVE_CAP %s"
                    % (inw, need, px, pair))
            else:
                qty = abs(inv["UP"] - inv["DOWN"])
                rec = {"t": r["t"][11:19], "in_window_s": round(inw, 1),
                       "role": "COMPLETE", "side": need,
                       "requote_own": str(px), "qty": str(qty),
                       "bid": r["bid"], "ask": r["ask"],
                       "pair_cost_if_filled": str(pair),
                       "filled": False, "fill_t": None,
                       "fill_evidence": None}
                out["orders"].append(rec)
                live = {"side": need, "px": px, "qty": qty,
                        "sod": r["sod"], "rec": rec}
            continue

        # ---- 3. the requote OPEN at a snapshot the live run refused
        if inw > ENTRY_CUTOFF_S:
            continue
        key = r.get("key") or ""
        if "PRESEND_WOULD_CROSS" in key:
            side = key.rsplit(":", 1)[-1]
            legs = {l["side"]: l for l in r["legs"] if l.get("side")}
            leg = legs.get(side)
            if not leg or leg.get("price") is None:
                continue
            refused = D(leg["price"])
            qty = D(str(leg.get("qty") or "1.87"))
            px, why = requote(side, refused, r["bid"], r["ask"],
                              r.get("stream_age_s"), completing=False)
            if px is None:
                out["notes"].append("+%.0fs %s requote refused %s"
                                    % (inw, side, why))
                continue
            rec = {"t": r["t"][11:19], "in_window_s": round(inw, 1),
                   "role": "OPEN", "side": side,
                   "refused_own": str(refused), "requote_own": str(px),
                   "ticks_lower": str((refused - px) / TICK),
                   "qty": str(qty), "bid": r["bid"], "ask": r["ask"],
                   "filled": False, "fill_t": None, "fill_evidence": None}
            out["orders"].append(rec)
            live = {"side": side, "px": px, "qty": qty,
                    "sod": r["sod"], "rec": rec}
            continue

        # ---- 4. POST-DIVERGENCE OPENINGS, and why they must be modelled.
        #
        # Once a counterfactual leg has filled, the live run's later
        # decisions came from a different inventory state, so there are no
        # recorded refusals left to anchor an opening to. Stopping there
        # would silently report a window that ENDS FLAT -- which reads as
        # "the requote removed the stranded leg" when all it means is that
        # the replay stopped opening rounds. So after the first divergence
        # the loop plans from the recorded BOOK, exactly as `build_orders`
        # does: both legs `depth_ticks` behind their touch, the pair cap on
        # the pair, the floor on the opening leg.
        if not diverged or not open_post_divergence:
            continue
        up_px = quote_own("UP", r["bid"], r["ask"])
        dn_px = quote_own("DOWN", r["bid"], r["ask"])
        if up_px + dn_px > PAIR_CAP or up_px < FLOOR or dn_px < FLOOR:
            continue
        side, px = "UP", up_px
        if crosses(side, px, r["bid"], r["ask"]):
            continue
        qty = D("1.87")
        rec = {"t": r["t"][11:19], "in_window_s": round(inw, 1),
               "role": "OPEN*", "side": side, "requote_own": str(px),
               "planned_pair": str(up_px + dn_px), "qty": str(qty),
               "bid": r["bid"], "ask": r["ask"],
               "filled": False, "fill_t": None, "fill_evidence": None}
        out["orders"].append(rec)
        live = {"side": side, "px": px, "qty": qty,
                "sod": r["sod"], "rec": rec}

    if live is not None:
        live["rec"]["open_at_tape_end"] = True
        out["notes"].append(
            "an order was still resting when the tape ended at +%.0fs: its "
            "fill is UNKNOWN, not absent" % out["tape_ends_s"])
    out["residual"] = {"UP": str(inv["UP"]), "DOWN": str(inv["DOWN"]),
                       "unmatched": str(abs(inv["UP"] - inv["DOWN"]))}
    return out


def main():
    rows = load()
    res = [replay_window(rows, w) for w in sorted(WINDOW_START)]
    print("FOUR-WINDOW COUNTERFACTUAL -- traversal-supported fills only\n")
    for o in res:
        fills = [x for x in o["orders"] if x["filled"]]
        print("== %s  snapshots=%d (tape ends +%ss)  orders=%d fills=%d "
              "pairs=%d" % (o["window"], o["records"], o["tape_ends_s"],
                            len(o["orders"]), len(fills), len(o["pairs"])))
        for x in o["orders"]:
            extra = ("FILL @%s  %s" % (x["fill_t"], x["fill_evidence"])
                     if x["filled"] else
                     ("STILL RESTING at tape end -- fill UNKNOWN"
                      if x.get("open_at_tape_end") else
                      "no traversal within TTL%s"
                      % ("" if "expired_s" in x else " (TTL ran past tape)")))
            print("   +%6.1fs %-8s %-4s own %s%s qty %-5s %s"
                  % (x["in_window_s"], x["role"], x["side"],
                     x["requote_own"],
                     (" (was %s)" % x["refused_own"]) if "refused_own" in x
                     else "", x["qty"], extra))
        for p in o["pairs"]:
            print("   PAIR %s qty %s  %s + %s = %s  gross edge $%s"
                  % (p["t"], p["qty"], p["up_own"], p["down_own"],
                     p["pair_cost"], p["gross_edge"]))
        for n in o["notes"][:5]:
            print("     . " + n)
        print("   residual unmatched: %s" % o["residual"]["unmatched"])
    with open(os.path.join(HERE, "requote_counterfactual.json"), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    print("\nwrote requote_counterfactual.json")


if __name__ == "__main__":
    main()
