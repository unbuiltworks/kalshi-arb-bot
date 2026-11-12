"""BTC5m favorite accumulation, replayed on recorded PMUS books. READ-ONLY.

WHAT THIS IS. The real shipped controller (`src.l5_btc5m_controller.decide`,
ACCUMULATE mode, the production $0.45 ceiling) stepped every 5 s over the
recorded public book of real PMUS BTC 5-minute windows, with the venue's own
terminal quote as the settlement. It answers one question:

    if the favorite-accumulation policy had been armed over these windows,
    what would each entry price have been worth, net?

WHAT IT IS NOT. Not a backtest of realized money: nothing was submitted and
no fill is observed. The recorded tape is a QUOTE tape -- the public
`bestBidQuote` / `bestAskQuote` every ~2 s -- with no prints and no depth,
so WHETHER a resting maker order would have been filled cannot be read off
it. It is therefore bounded, not estimated, by two fill rules that are
reported side by side and never averaged:

  ADVERSE   our resting buy on side s at price x is filled at the first
            later observation where s's own bid has fallen BELOW x. The
            book left our level, so a seller came through it. This is the
            adversely-selected fill: we are filled exactly as the side we
            just bought gets cheaper.

  FAVOURED  ADVERSE, plus: filled when s's own bid STAYS at x for at least
            `DWELL_S` while we rest. This assumes front of queue at a price
            others are also bidding, which public data cannot support. It is
            an explicit UPPER BOUND on fills, not an estimate.

The truth is between them and nearer ADVERSE, because a 5-minute book has
few prints and our clip joins a queue it cannot see.

FEES. The venue's maker commission is zero and this is not an assumption:
every order of the 2026-11-12 overnight campaign read back
`commissionNotionalTotalCollected` of 0.0000 (service l5-btc5m-live, session
reports 05:00z and 06:30z). The fee column is carried through the
arithmetic all the same so a non-zero rate can be priced later.

ADVERSE SELECTION IS MEASURED HERE, NOT ASSUMED. The controller's
`adverse_selection_per_share` of $0.06 is a parameter borrowed from a
15-minute proxy. This replay reports, per entry-price bucket, the realized
win rate of the side bought against the price paid for it; the difference is
the adverse selection this policy actually meets on 5-minute books.

Usage:  python3 -I analysis/l5/btc5m_accumulation/replay.py [tape.jsonl]
"""
from __future__ import annotations

import collections
import json
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
os.environ["L5_BTC5M_CONTROLLER"] = "ON"          # the pure switch only

from src import l5_btc5m_controller as C          # noqa: E402

TAPE = os.path.join("analysis", "l5", "btc5m_accumulation",
                    "pmus_btc5m_quotes_20261112.jsonl")
STEP_S = 5.0
CANCEL_LEAD_S = 30.0
WINDOW_S = 300.0
DWELL_S = 10.0
CAP = D("0.45")
CLIP_USD = C.Params().clip_usd                # the shipped default
MAX_RESTING = C.Params().max_resting_entries   # the shipped default
MAKER_FEE_PER_SHARE = D("0")
#: a terminal quote this decided is the venue's own settlement
RESOLVE_AFTER_S = 240.0

PARAMS = C.Params(entry_mode=C.ACCUMULATE, hedge_enabled=False,
                  reserve_mode="NONE", max_exposure_usd=CAP,
                  clip_usd=CLIP_USD, max_resting_entries=MAX_RESTING,
                  accumulate_depth_ticks=0)


def load(path):
    w = collections.defaultdict(list)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            q = json.loads(line)
            w[q["slug"]].append(q)
    for rows in w.values():
        rows.sort(key=lambda r: r["t"])
    return w


def dec(x):
    return None if x is None else D(str(x))


def outcome(rows):
    """UP / DOWN / None, from the venue's own terminal quote."""
    for q in reversed(rows):
        if q["t"] < RESOLVE_AFTER_S:
            break
        bid, ask = dec(q["bid"]), dec(q["ask"])
        if bid is not None and bid >= D("0.90"):
            return C.UP
        if ask is not None and ask <= D("0.10"):
            return C.DOWN
    return None


def quote_at(rows, t):
    """The most recent observation at or before `t`, with its age."""
    best = None
    for q in rows:
        if q["t"] <= t:
            best = q
        else:
            break
    if best is None:
        return None
    return best, t - best["t"]


def own_bid(q, side):
    """The price of a BUY of `side`: YES bid for UP, 1 - YES ask for DOWN."""
    bid, ask = dec(q["bid"]), dec(q["ask"])
    if bid is None or ask is None:
        return None
    return bid if side == C.UP else D("1") - ask


class Order:
    __slots__ = ("oid", "side", "price", "qty", "placed_t", "at_price_since")

    def __init__(self, oid, side, price, qty, t):
        self.oid, self.side, self.price, self.qty = oid, side, price, qty
        self.placed_t = t
        self.at_price_since = t


#: OPTIONAL ENTRY GATE, default off. A callable (q, t, age, rows) -> bool;
#: when it returns False, new BUY placements for that instant are
#: suppressed while cancellations and fills proceed exactly as before --
#: i.e. "do not rest here", which is the only thing a gate may do. With
#: GATE None the loop is byte-for-byte the path that produced every number
#: in ECONOMICS.md, so a gate experiment cannot silently move the baseline.
GATE = None


def run_window(rows, *, tier, gate=None):
    """One window under one fill rule. Returns the fills and the refusals."""
    gate = gate if gate is not None else GATE
    won = outcome(rows)
    if won is None:
        return None
    inv = {C.UP: {"qty": D(0), "cost": D(0)},
           C.DOWN: {"qty": D(0), "cost": D(0)}}
    resting, fills, whys = {}, [], collections.Counter()
    #: the shared SUBMISSION CLASS each placement would be charged to, by
    #: the same rule as `Btc5mExecutor._order_class`: COMPLETE when it buys
    #: the side we are short of and cannot overshoot the unmatched amount,
    #: REPLACE when an order of ours on that side was cancelled before,
    #: OPEN otherwise. It decides WHICH owner-set budget the window spends,
    #: so accumulation's requoting is charged to REPLACE, not OPEN.
    klasses = collections.Counter()
    cancelled_side = set()
    nid, placed = 0, 0
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:
        got = quote_at(rows, t)
        if got is None:
            t += STEP_S
            continue
        q, age = got

        # ---- fills on the orders that are resting, before this decision
        for o in list(resting.values()):
            b = own_bid(q, o.side)
            if b is None:
                continue
            if b < o.price:
                hit, why = True, "BID_FELL_THROUGH"
            elif tier == "FAVOURED" and b == o.price and (
                    t - o.at_price_since) >= DWELL_S:
                hit, why = True, "DWELL_AT_PRICE"
            else:
                if b != o.price:
                    o.at_price_since = t
                hit, why = False, None
            if hit:
                inv[o.side]["qty"] += o.qty
                inv[o.side]["cost"] += o.qty * o.price
                fills.append({"side": o.side, "price": o.price,
                              "qty": o.qty, "t": t, "why": why,
                              "won": o.side == won})
                del resting[o.oid]

        # ---- the real decision
        out = C.decide(
            book={"bid": q["bid"], "ask": q["ask"], "age_s": age},
            t_in_window_s=t,
            inventory={s: {"qty": str(inv[s]["qty"]),
                           "cost": str(inv[s]["cost"])} for s in C.SIDES},
            resting=[{"id": o.oid, "side": o.side, "price": o.price,
                      "qty": o.qty, "age_s": t - o.placed_t}
                     for o in resting.values()],
            params=PARAMS)
        whys[(out["evaluation"].get("entry") or {}).get("why")
             or out["why"]] += 1
        for op in out["orders"]:
            if op["op"] == "CANCEL":
                gone = resting.pop(op["id"], None)
                if gone is not None:
                    cancelled_side.add(gone.side)
            else:
                if gate is not None and not gate(q, t, age, rows):
                    whys["GATED_OUT"] += 1
                    continue
                side = op["side"]
                other = C.DOWN if side == C.UP else C.UP
                unmatched = inv[other]["qty"] - inv[side]["qty"]
                working = sum((o.qty for o in resting.values()
                               if o.side == side), D(0))
                if unmatched > 0 and op["qty"] + working <= unmatched:
                    klasses["COMPLETE"] += 1
                elif side in cancelled_side:
                    klasses["REPLACE"] += 1
                else:
                    klasses["OPEN"] += 1
                nid += 1
                resting["o%d" % nid] = Order("o%d" % nid, op["side"],
                                             op["price"], op["qty"], t)
                placed += 1
        t += STEP_S

    return {"won": won, "fills": fills, "placed": placed, "whys": whys,
            "gated": whys.get("GATED_OUT", 0),
            "inv": inv, "klasses": klasses}


def bucket(price):
    lo = (int(price * 100) // 5) * 5
    return "%.2f-%.2f" % (lo / 100.0, (lo + 4) / 100.0)


def report(tape):
    w = load(tape)
    print("TAPE %s" % tape)
    print("windows on tape: %d   quotes: %d"
          % (len(w), sum(len(v) for v in w.values())))
    for tier in ("ADVERSE", "FAVOURED"):
        rs = {s: run_window(rows, tier=tier) for s, rows in sorted(w.items())}
        usable = {s: r for s, r in rs.items() if r}
        print()
        print("=" * 74)
        print("FILL RULE: %s   (windows resolved: %d of %d)"
              % (tier, len(usable), len(w)))
        print("=" * 74)
        by = collections.defaultdict(list)
        placed = sum(r["placed"] for r in usable.values())
        allf = []
        for r in usable.values():
            for f in r["fills"]:
                by[bucket(f["price"])].append(f)
                allf.append(f)
        print("entries placed: %d    filled: %d    fill rate: %s"
              % (placed, len(allf),
                 "%.1f%%" % (100.0 * len(allf) / placed) if placed else "n/a"))
        if not allf:
            print("no fills under this rule")
            continue
        print()
        print("%-12s %6s %9s %9s %9s %9s %10s %11s"
              % ("price", "fills", "shares", "paid$", "win_rate",
                 "breakeven", "adv_sel/sh", "net$"))
        print("-" * 86)
        tot_sh = tot_paid = tot_net = D(0)
        for b in sorted(by):
            fs = by[b]
            sh = sum(f["qty"] for f in fs)
            paid = sum(f["qty"] * f["price"] for f in fs)
            avg = paid / sh
            wins = sum(f["qty"] for f in fs if f["won"])
            wr = wins / sh
            net = wins - paid - MAKER_FEE_PER_SHARE * sh
            print("%-12s %6d %9s %9s %9s %9s %10s %11s"
                  % (b, len(fs), f6(sh), f6(paid), "%.3f" % wr,
                     "%.3f" % avg, "%+.4f" % (wr - avg), "%+.4f" % net))
            tot_sh += sh
            tot_paid += paid
            tot_net += net
        wins = sum(f["qty"] for f in allf if f["won"])
        print("-" * 86)
        print("%-12s %6d %9s %9s %9s %9s %10s %11s"
              % ("ALL", len(allf), f6(tot_sh), f6(tot_paid),
                 "%.3f" % (wins / tot_sh), "%.3f" % (tot_paid / tot_sh),
                 "%+.4f" % (wins / tot_sh - tot_paid / tot_sh),
                 "%+.4f" % tot_net))
        pw = [sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                  for f in r["fills"]) for r in usable.values()
              if r["fills"]]
        print()
        print("net per filled share : %+.4f" % (tot_net / tot_sh))
        print("maker fee per share  : %s (venue read-back, overnight orders)"
              % MAKER_FEE_PER_SHARE)
        print("windows with a fill  : %d of %d" % (len(pw), len(usable)))
        if len(pw) > 1:
            print("net per window       : mean %+.4f  median %+.4f  "
                  "stdev %.4f  worst %+.4f  best %+.4f"
                  % (statistics.fmean(float(x) for x in pw),
                     statistics.median(float(x) for x in pw),
                     statistics.stdev(float(x) for x in pw),
                     min(float(x) for x in pw), max(float(x) for x in pw)))
        wonw = sum(1 for x in pw if x > 0)
        print("winning windows      : %d of %d" % (wonw, len(pw)))
        print()
        kl = collections.Counter()
        per = collections.defaultdict(list)
        for r in usable.values():
            kl.update(r["klasses"])
            for k in ("OPEN", "COMPLETE", "REPLACE"):
                per[k].append(r["klasses"].get(k, 0))
        print("submission class each placement is charged to"
              " (owner-set budgets):")
        for k in ("OPEN", "COMPLETE", "REPLACE"):
            n = kl.get(k, 0)
            print("   %-10s %4d total   per window: mean %4.1f  max %d"
                  % (k, n, statistics.fmean(per[k]) if per[k] else 0.0,
                     max(per[k]) if per[k] else 0))
        print()
        print("why no entry (decision counts):")
        agg = collections.Counter()
        for r in usable.values():
            agg.update(r["whys"])
        for k, n in agg.most_common():
            print("   %-24s %d" % (k, n))


def f6(d):
    return ("%.4f" % d).rstrip("0").rstrip(".") or "0"


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else TAPE)


# --------------------------------------------------------------- sweeps

def sweep(tape=TAPE):
    """The one parameter the replay says matters: where the band starts.

    The owner's contract sets the floor at $0.51. This prices moving it,
    on the same tape, under the ADVERSE fill rule. Nothing here changes a
    default; it is the number the owner needs to raise the floor or keep it.
    """
    global PARAMS
    w = load(tape)
    print()
    print("=" * 74)
    print("BAND FLOOR SENSITIVITY   (ADVERSE fill rule, %d windows)"
          % len(w))
    print("=" * 74)
    print("%-10s %7s %7s %10s %11s %11s %9s"
          % ("band_lo", "placed", "fills", "shares", "net$", "net/share",
             "net/window"))
    print("-" * 74)
    keep = PARAMS
    for lo in ("0.51", "0.55", "0.60", "0.65", "0.70", "0.75"):
        PARAMS = C.Params(**dict(vars(keep), band_lo=D(lo)))
        rs = [r for r in (run_window(rows, tier="ADVERSE")
                          for _s, rows in sorted(w.items())) if r]
        fills = [f for r in rs for f in r["fills"]]
        placed = sum(r["placed"] for r in rs)
        sh = sum(f["qty"] for f in fills) or D(1)
        net = sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                  for f in fills)
        print("%-10s %7d %7d %10s %11s %11s %9s"
              % (lo, placed, len(fills), f6(sh), "%+.4f" % net,
                 "%+.4f" % (net / sh), "%+.4f" % (net / len(rs))))
    PARAMS = keep


def clip_sweep(tape=TAPE):
    """Clip size against the fixed $0.45 ceiling. A bigger clip buys more
    of each entry and runs out of cap sooner; a smaller one spreads the
    same capital over more entries and more of the window."""
    global PARAMS
    w = load(tape)
    print()
    print("=" * 74)
    print("CLIP SIZE SENSITIVITY   (ADVERSE, band_lo $0.51, cap $0.45)")
    print("=" * 74)
    print("%-10s %7s %7s %10s %11s %11s"
          % ("clip$", "placed", "fills", "shares", "net$", "net/window"))
    print("-" * 74)
    keep = PARAMS
    for clip in ("0.05", "0.10", "0.15", "0.22"):
        PARAMS = C.Params(**dict(vars(keep), clip_usd=D(clip)))
        rs = [r for r in (run_window(rows, tier="ADVERSE")
                          for _s, rows in sorted(w.items())) if r]
        fills = [f for r in rs for f in r["fills"]]
        sh = sum(f["qty"] for f in fills) or D(1)
        net = sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                  for f in fills)
        print("%-10s %7d %7d %10s %11s %11s"
              % (clip, sum(r["placed"] for r in rs), len(fills), f6(sh),
                 "%+.4f" % net, "%+.4f" % (net / len(rs))))
    PARAMS = keep


def depth_sweep(tape=TAPE):
    """WHERE IN THE RANGE TO REST -- the last lever that is purely "maker
    orders in that band".

    Resting below the favorite's bid pays less per share, so the naive
    expectation is that it earns more. It does the OPPOSITE, monotonically.
    The reason is the fill mechanism: a resting buy is reached when the bid
    falls THROUGH it, so resting k ticks lower means only filling when the
    side has moved k ticks further against us. The adverse selection grows
    faster than the k cents of price concession.
    """
    global PARAMS
    import math
    import statistics
    w = load(tape)
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    keep = PARAMS
    print()
    print("=" * 78)
    print("RESTING DEPTH BELOW THE FAVORITE BID (ADVERSE, $0.45 cap)")
    print("=" * 78)
    print("%-7s %7s %7s %9s %10s %9s %7s %11s"
          % ("ticks", "placed", "fills", "fill%", "net/share", "net/win",
             "t", "OOS net/win"))
    print("-" * 78)
    for k in range(0, 7):
        PARAMS = C.Params(**dict(vars(keep), accumulate_depth_ticks=k))
        globals()["PARAMS"] = PARAMS
        us = {s: r for s, r in ((s, run_window(w[s], tier="ADVERSE"))
                                for s in slugs) if r}
        f = [x for r in us.values() for x in r["fills"]]
        placed = sum(r["placed"] for r in us.values())
        if not f:
            continue
        ps = [float((D(1) if x["won"] else D(0)) - x["price"]) for x in f]
        pw = [float(sum((x["qty"] if x["won"] else D(0))
                        - x["qty"] * x["price"] for x in r["fills"]))
              for r in us.values()]
        oos = [float(sum((x["qty"] if x["won"] else D(0))
                         - x["qty"] * x["price"] for x in us[s]["fills"]))
               for s in slugs[cut:] if s in us]
        m = statistics.fmean(pw)
        se = statistics.stdev(pw) / math.sqrt(len(pw))
        print("%-7d %7d %7d %8.1f%% %+10.4f %+9.4f %+7.2f %+11.4f"
              % (k, placed, len(f), 100.0 * len(f) / max(1, placed),
                 statistics.fmean(ps), m, m / se, statistics.fmean(oos)))
    PARAMS = keep
    globals()["PARAMS"] = keep
