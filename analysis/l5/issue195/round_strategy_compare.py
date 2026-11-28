"""A / B / C: how many opening rounds should a window take?

THE QUESTION THE OWNER ASKED. 1230z completed one pair for +$0.0450, opened
a second round into a flat book, filled one leg of it and settled -$0.4100.
Three policies are possible and only evidence can choose between them:

    A  STOP AFTER THE FIRST PAIR. One pair per window, then nothing.
    B  REPEATED ROUNDS, as deployed. `probe_round` returns the next
       incomplete round and nothing prices it. This is what ran on 1230z.
    C  INVENTORY-AWARE AND RISK-BUDGETED. Rounds repeat, but every round
       past the first exposure-taking one must clear
       `l5_unmatched_disposition.reopen_decision`: a hard capacity bound on
       the worst case, then measured incremental expectancy.

WHAT THIS IS NOT. Not a P&L and not a forecast. It is the same recorded
books the queue-depletion work used, replayed sequentially per window under
three decision rules, so the rules can be compared on identical data.

THE CONSERVATIVE ASSUMPTIONS, stated so they can be attacked:

  * A LEG FILLS ONLY WHEN PROVEN -- the opposite touch reached our price.
    Real maker fills also happen inside the queue without the touch
    crossing, so pairing is UNDERSTATED here and the single-leg rate
    OVERSTATED. That is the direction that flatters A and C over B.
  * NO LOOK-AHEAD. A round opens at frame `i` using only the book at `i`.
    Fills are resolved from frames after `i`, never before the decision.
  * ROUNDS DO NOT OVERLAP IN TIME. The next round may open only at a frame
    after the previous round terminated, so no strategy gets to run two
    rounds concurrently that the engine would not have.
  * A LEG UNFILLED BY ITS TTL IS CANCELLED, and an unmatched leg is carried
    to settlement at the window's final mid -- maker-only, because taker
    completion is disabled by owner order.
  * FEES. Maker fills pay none under the published schedule; a zero
    commission is not a rebate and none is credited here.
"""
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_queue_depletion as QD                   # noqa: E402
from src import l5_unmatched_disposition as UD             # noqa: E402
from analysis.l5.issue195.queue_depletion_report import (  # noqa: E402
    IN_SAMPLE, OUT_OF_SAMPLE, _load, _mid, _touches)

TICK = D("0.01")
DEPTH = 2                 # the deployed depth
MAX_ROUNDS = 3            # what `acc_max_rounds` was set to
TTL_FRAMES = 60           # a leg's order deadline, in frames

#: WHICH FILL MODEL, and it is a BRACKET rather than an estimate. PROVEN
#: means the opposite touch reached our price -- sufficient, so a LOWER
#: bound on fills, and it over-samples the adverse case because the market
#: moved THROUGH us. POSSIBLE means enough size left the queue ahead of us
#: -- necessary only, since a decrease may be a cancellation, so an UPPER
#: bound. The truth is between them and neither is a point estimate.
#: `FILL_MODEL` selects which end; the sensitivity report runs both.
FILL_MODEL = QD.PROVEN


def _filled_frame(side, px, frames, i):
    """The frame this leg filled on under `FILL_MODEL`, or None."""
    v = QD.fill_verdict(side, px, frames, start=i)
    verdict = v.get("verdict")
    if FILL_MODEL == QD.PROVEN:
        if verdict != QD.PROVEN:
            return None
        return v.get("proven_at_frame")
    # POSSIBLE: count PROVEN too, since proven implies possible. A leg with
    # no proven frame is dated at the first frame its queue could have
    # cleared, which is the most generous reading the data supports.
    if verdict == QD.IMPOSSIBLE:
        return None
    return (v.get("proven_at_frame") if v.get("proven_at_frame") is not None
            else v.get("possible_at_frame"))
EXPOSURE_CAP = D("1.00")  # the owner's $1 aggregate worst case
FRAME_S = 1.0             # recorded sampling cadence
WINDOW_S = 900.0


def _leg_prices(frame):
    """Own prices for both legs resting `DEPTH` ticks behind each touch."""
    tt = _touches(frame)
    if tt is None:
        return None
    bb, ba = tt
    up, dn = bb - DEPTH * TICK, (D(1) - ba) - DEPTH * TICK
    if up <= 0 or dn <= 0:
        return None
    return up, dn, bb, ba


def _round_outcome(frames, i):
    """Open a round at frame `i`. Returns what became of it.

    `end` is the frame the round terminated on, so the next round cannot
    open before it.
    """
    px = _leg_prices(frames[i])
    if px is None:
        return None
    up, dn, bb, ba = px
    fu = _filled_frame("UP", up, frames, i)
    fd = _filled_frame("DOWN", dn, frames, i)
    # a fill after the TTL is a cancel, not a fill
    if fu is not None and fu - i > TTL_FRAMES:
        fu = None
    if fd is not None and fd - i > TTL_FRAMES:
        fd = None
    end = max([f for f in (fu, fd) if f is not None] or [i + TTL_FRAMES])
    return {"i": i, "end": min(end, len(frames) - 1),
            "up": up, "dn": dn, "bid": bb, "ask": ba,
            "filled_up": fu is not None, "filled_dn": fd is not None}


def _settle(r, final_mid):
    """Settled P&L of one round, maker-only, at the window's final mid."""
    if r["filled_up"] and r["filled_dn"]:
        return D(1) - (r["up"] + r["dn"]), "PAIR"
    if r["filled_up"]:
        return final_mid - r["up"], "SINGLE_UP"
    if r["filled_dn"]:
        return (D(1) - final_mid) - r["dn"], "SINGLE_DOWN"
    return D(0), "NO_FILL"


def replay(frames, policy):
    """Sequential rounds under one policy. Returns the window's record."""
    final_mid = None
    for f in reversed(frames):
        final_mid = _mid(f)
        if final_mid is not None:
            break
    if final_mid is None:
        return None

    out = {"rounds": 0, "pairs": 0, "singles": 0, "no_fill": 0,
           "pnl": D(0), "pair_pnl": D(0), "single_pnl": D(0),
           "worst_peak": D(0), "refusals": {}, "exposure_taking": 0}
    held = D(0)          # signed unmatched contracts
    held_cost = held_pnl = D(0)   # its basis, and the single P&L booked
    cursor = 0
    while out["rounds"] < MAX_ROUNDS and cursor < len(frames) - 1:
        # find the next frame a round could open at
        r = None
        for i in range(cursor, len(frames) - 1):
            r = _round_outcome(frames, i)
            if r is not None:
                break
        if r is None:
            break

        # ---- the policy decides whether this round opens
        if out["exposure_taking"] >= 1:
            if policy == "A":
                out["refusals"]["A_ONE_PAIR_ONLY"] = \
                    out["refusals"].get("A_ONE_PAIR_ONLY", 0) + 1
                break
            if policy == "C":
                # A RESIDUAL STILL TO REDUCE IS NOT AN OPENING, and the
                # engine does not open a fresh PAIR there: `probe_leg_order`
                # places the REDUCING leg first and `probe_leg_qty_cap` caps
                # it at the residual's magnitude, so the action is a
                # completion of what is already held. Modelled as such --
                # one leg, capped -- because modelling it as another full
                # two-leg round would charge C for exposure the
                # implementation cannot take.
                if held != 0:
                    red = "DOWN" if held > 0 else "UP"
                    px = r["dn"] if red == "DOWN" else r["up"]
                    f = _filled_frame(red, px, frames, r["i"])
                    if f is not None and f - r["i"] > TTL_FRAMES:
                        f = None
                    out["rounds"] += 1
                    out["reductions"] = out.get("reductions", 0) + 1
                    if f is None:
                        # not filled: the residual is still carried, and it
                        # was already settled when the leg that made it did
                        cursor = min(r["i"] + TTL_FRAMES, len(frames) - 1) + 1
                        continue
                    # filled: it MATCHES the held leg, so the pair's
                    # economics replace the single's. Undo the single we
                    # already booked and book the pair instead.
                    basis = held_cost
                    out["singles"] -= 1
                    out["single_pnl"] -= held_pnl
                    out["pnl"] -= held_pnl
                    pair = D(1) - (basis + px)
                    out["pairs"] += 1
                    out["pair_pnl"] += pair
                    out["pnl"] += pair
                    out["late_completions"] = \
                        out.get("late_completions", 0) + 1
                    held = D(0)
                    cursor = f + 1
                    continue
                else:
                    worst = max(r["up"], r["dn"])
                    runway = WINDOW_S - (r["i"] * FRAME_S)
                    d = UD.reopen_decision(
                        market="replay", rounds_used=out["exposure_taking"],
                        locked_pair_pnl_usd=out["pair_pnl"],
                        worst_leg_own_px=worst, qty=D(1),
                        bid=r["bid"], ask=r["ask"], tick=TICK,
                        depth_ticks=DEPTH,
                        now_s=0.0, window_end_s=runway,
                        exposure_headroom_usd=EXPOSURE_CAP,
                        outstanding_worst_case_usd=abs(held) * worst)
                    if not d["reopen"]:
                        k = d["reason"]
                        out["refusals"][k] = out["refusals"].get(k, 0) + 1
                        break

        pnl, kind = _settle(r, final_mid)
        out["rounds"] += 1
        out["pnl"] += pnl
        if kind == "PAIR":
            out["pairs"] += 1
            out["pair_pnl"] += pnl
            out["exposure_taking"] += 1
            out["worst_peak"] = max(out["worst_peak"],
                                    abs(held) + max(r["up"], r["dn"]))
        elif kind == "NO_FILL":
            out["no_fill"] += 1
        else:
            out["singles"] += 1
            out["single_pnl"] += pnl
            out["exposure_taking"] += 1
            held += D(1) if kind == "SINGLE_UP" else D(-1)
            held_cost = r["up"] if kind == "SINGLE_UP" else r["dn"]
            held_pnl = pnl
            out["worst_peak"] = max(out["worst_peak"],
                                    abs(held) * max(r["up"], r["dn"]))
        cursor = r["end"] + 1
    return out


def run(group, label):
    rows = {}
    for policy in ("A", "B", "C"):
        agg = {"windows": 0, "rounds": 0, "pairs": 0, "singles": 0,
               "no_fill": 0, "pnl": D(0), "pair_pnl": D(0),
               "single_pnl": D(0), "worst": D(0), "pos": 0,
               "with_single": 0, "refusals": {}}
        for path, shape in group:
            for slug, frames in sorted(_load(path, shape).items()):
                if len(frames) < 10:
                    continue
                w = replay(frames, policy)
                if w is None:
                    continue
                agg["windows"] += 1
                for k in ("rounds", "pairs", "singles", "no_fill"):
                    agg[k] += w[k]
                for k in ("reductions", "late_completions"):
                    if w.get(k):
                        agg[k] = agg.get(k, 0) + w[k]
                for k in ("pnl", "pair_pnl", "single_pnl"):
                    agg[k] += w[k]
                agg["worst"] = max(agg["worst"], w["worst_peak"])
                agg["pos"] += 1 if w["pnl"] > 0 else 0
                agg["with_single"] += 1 if w["singles"] else 0
                for k, v in w["refusals"].items():
                    agg["refusals"][k] = agg["refusals"].get(k, 0) + v
        rows[policy] = agg

    print("=" * 78)
    print(label)
    print("=" * 78)
    names = {"A": "A  stop after first pair",
             "B": "B  repeated rounds (deployed)",
             "C": "C  inventory-aware, risk-budgeted"}
    hdr = ("  %-34s %7s %7s %7s %7s" % ("", "A", "B", "C", ""))
    del hdr
    metrics = [
        ("windows replayed", lambda a: a["windows"]),
        ("rounds opened", lambda a: a["rounds"]),
        ("rounds per window", lambda a: _q(D(a["rounds"]) / a["windows"])),
        ("pairs completed", lambda a: a["pairs"]),
        ("unmatched single legs", lambda a: a["singles"]),
        ("rounds that never filled", lambda a: a["no_fill"]),
        ("of which capped reductions", lambda a: a.get("reductions", 0)),
        ("late completions of a single",
            lambda a: a.get("late_completions", 0)),
        ("pair P&L", lambda a: _q(a["pair_pnl"])),
        ("single-leg P&L", lambda a: _q(a["single_pnl"])),
        ("net P&L", lambda a: _q(a["pnl"])),
        ("net per window", lambda a: _q(a["pnl"] / a["windows"])),
        ("net per round", lambda a: _q(a["pnl"] / a["rounds"])
            if a["rounds"] else "n/a"),
        ("peak worst-case exposure $", lambda a: _q(a["worst"])),
        ("windows net positive", lambda a: a["pos"]),
        ("windows carrying a single", lambda a: a["with_single"]),
    ]
    for name, fn in metrics:
        print("  %-30s %12s %12s %12s"
              % (name, fn(rows["A"]), fn(rows["B"]), fn(rows["C"])))
    print()
    for p in ("A", "C"):
        if rows[p]["refusals"]:
            print("  %s refusals: %s" % (names[p], rows[p]["refusals"]))


def _q(v):
    return str(v.quantize(D("0.0001"))) if isinstance(v, D) else v


if __name__ == "__main__":
    run(IN_SAMPLE, "IN SAMPLE   (2026-10-28 x3 windows, 2026-11-06 0300z)")
    print()
    run(OUT_OF_SAMPLE,
        "OUT OF SAMPLE   (2026-10-25 x12 windows, 2026-11-06 1130z)")
    print()
    print("Maker-only throughout; taker completion is disabled by owner")
    print("order and is not priced. PROVEN fills only, so pairing is a")
    print("LOWER bound. Overlapping windows share futures; these are")
    print("correlated observations, not independent trades.")
