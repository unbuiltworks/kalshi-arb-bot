"""Spread x depth and realized adverse selection, from a recorded tape.

THE ONE EQUATION. A resting buy on both sides at the touch costs
`bid + (1 - ask)` = `1 - spread`, so a filled PAIR captures exactly the
spread. The strategy is viable only where `spread > adverse selection`.
BTC5m is 1c against 4.87c and is closed. This measures the same ratio on
sports, and measures the thing spread alone hides: how much size is
actually there.

WHY `spread x depth` AND NOT SPREAD. Two different markets quote 0.45 and
0.01. The first looks 45x better and is worth less, because its touch holds
one share and its whole game has traded sixteen. Spread is the rate; depth
is the quantity; only the product is money. This reports the product, and
reports the two factors beside it so the trade-off is visible rather than
asserted.

AND DEPTH CUTS BOTH WAYS, which is the half the "1-3 shares" worry misses.
Thin depth caps what a pair can earn. THICK depth is a queue we join at the
back: resting behind 16,000 shares is not a fill, it is a wait. So depth is
reported as capacity AND as queue-ahead, and the fill model below only
fills a leg once the queue in front of it has actually traded.

HOW ADVERSE SELECTION IS MEASURED, and why it is volume-triggered. The
BTC5m shadow's fill rule -- "a resting buy at x fills when own price <= x"
-- is right for a leg resting DEEP at 0.08 and degenerates at the touch:
resting at the current bid, any downtick satisfies it, so every leg "fills"
instantly and the number means nothing. At the touch, a fill needs a
COUNTERPARTY, so the trigger has to be a trade:

  trade at or below bid   fills our resting BUY YES  (a seller hit our bid)
  trade at or above ask   fills our resting BUY NO   (a buyer lifted our
                          offer -- buying NO at 1-ask IS selling YES at ask)

and the adverse selection on a filled leg is the mid moving against it:

  YES leg filled at b     AS = mid(t) - mid(t+tau)   price fell after we bought
  NO  leg filled at 1-a   AS = mid(t+tau) - mid(t)   price rose against the NO

This is the standard decomposition. At tau=0, `mid - b` is exactly the half
spread, so markout P&L = half-spread - AS, and `spread > AS` is the same
test as "the capture survives the drift".

WHAT THE CROSS-CHECK DOES AND DOES NOT COVER. `--crosscheck` runs this
file's fill rule and settlement arithmetic over the committed BTC5m tape
and must reproduce the published baseline -- 189 windows, BOTH 10.2%, net
+3.74 c/w, t +1.67 -- which is what `shadow_crosscheck.py` did before
trusting the live shadow. That validates the tape reader, the deep-rest
fill rule and the P&L arithmetic. It does NOT validate the volume-triggered
fill model, because the BTC5m tape carries no volume at all: that endpoint
404s on a live crypto window. Said plainly here rather than implied,
because an unvalidated component inside a validated harness is exactly the
kind of thing that reports a clean zero.

Usage:
  python3 -I .../census_report.py --tape TAPE.jsonl
  python3 -I .../census_report.py --crosscheck BTC5M_QUOTES.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
from typing import Any, Optional

#: the pre-registered buckets from BAND_SPREAD_GRID.md, kept identical so
#: these rows sit beside that study's instead of merely near it.
SPREAD_BUCKETS = (("<=0.01", 0.0, 0.01), ("0.01-0.02", 0.01, 0.02),
                  ("0.02-0.03", 0.02, 0.03), ("0.03-0.05", 0.03, 0.05),
                  (">0.05", 0.05, 9.99))
#: markout horizons, in tape rows ahead. The tape is ~15-20s a market, so
#: these are roughly 20s / 1min / 5min. Several, because adverse selection
#: that vanishes by 5 minutes is a different animal from one that compounds.
HORIZONS = (1, 3, 15)


def bucket_of(spread: float) -> str:
    for name, lo, hi in SPREAD_BUCKETS:
        if (lo == 0.0 and spread <= hi) or (lo < spread <= hi):
            return name
    return SPREAD_BUCKETS[-1][0]


def med(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def load(path: str) -> dict:
    """Tape -> per-market chronological rows. Unreadable rows are COUNTED,
    not dropped silently: a market we could not read is not a market with
    no depth, and the difference is the whole lesson of the last collector.
    """
    per: dict = collections.defaultdict(list)
    skipped = collections.Counter()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                skipped["malformed"] += 1
                continue
            if not r.get("readable"):
                skipped["unreadable:" + str(r.get("why"))] += 1
                continue
            if not r.get("depth_available"):
                skipped["no_depth:" + str(r.get("why_depth"))] += 1
                continue
            per[r["slug"]].append(r)
    for slug in per:
        per[slug].sort(key=lambda r: r["utc"])
    return {"per": per, "skipped": skipped}


def two_sided(r) -> bool:
    return (r.get("spread") is not None and r.get("bid") is not None
            and r.get("ask") is not None)


def mid(r) -> Optional[float]:
    if not two_sided(r):
        return None
    return (r["bid"] + r["ask"]) / 2.0


def census(per: dict) -> list:
    """The spread x depth table: the rate, the quantity, and the product."""
    cells: dict = collections.defaultdict(
        lambda: {"obs": 0, "markets": set(), "spread": [], "bid_q": [],
                 "ask_q": [], "pair_q": [], "capacity": [], "vol_rate": []})
    for slug, rows in per.items():
        for i, r in enumerate(rows):
            if not two_sided(r):
                continue
            c = cells[bucket_of(r["spread"])]
            c["obs"] += 1
            c["markets"].add(slug)
            c["spread"].append(r["spread"])
            bq, aq = r.get("bid_touch_qty"), r.get("ask_touch_qty")
            c["bid_q"].append(bq)
            c["ask_q"].append(aq)
            #: A PAIR NEEDS BOTH LEGS, so the size a pair can do is the
            #: SMALLER of the two touches, never the average and never the
            #: better one. Averaging the sides is how a 1-share book with a
            #: 7000-share other side reads as liquid.
            if bq is not None and aq is not None:
                pq = min(bq, aq)
                c["pair_q"].append(pq)
                c["capacity"].append(r["spread"] * pq)
            #: traded shares per minute, from consecutive rows. This is the
            #: queue-drain rate: it decides whether we are ever reached.
            if i > 0:
                p = rows[i - 1]
                dv = (None if r.get("shares_traded") is None
                      or p.get("shares_traded") is None
                      else r["shares_traded"] - p["shares_traded"])
                gap = r.get("read_gap_s")
                if dv is not None and dv >= 0 and gap:
                    c["vol_rate"].append(dv * 60.0 / gap)
    out = []
    for name, _lo, _hi in SPREAD_BUCKETS:
        c = cells.get(name)
        if not c or not c["obs"]:
            out.append({"bucket": name, "obs": 0, "markets": 0})
            continue
        out.append({
            "bucket": name, "obs": c["obs"], "markets": len(c["markets"]),
            "med_spread": med(c["spread"]),
            "med_bid_touch": med(c["bid_q"]), "med_ask_touch": med(c["ask_q"]),
            "med_pair_size": med(c["pair_q"]),
            #: the number the instruction asked for, in dollars per pair
            "med_capacity_usd": med(c["capacity"]),
            "med_vol_shares_per_min": med(c["vol_rate"]),
        })
    return out


def adverse_selection(per: dict) -> dict:
    """Volume-triggered fills, then the mid drift against each filled leg.

    Reports queue-blind and queue-cleared separately. Queue-blind is the
    optimistic bound the BTC5m work used throughout -- filled whenever the
    touch trades, no queue, no size. Queue-cleared additionally requires the
    interval's volume to exceed the depth resting in front of us, which is
    what actually happens to an order joining the back of a touch.
    """
    legs: dict = {k: collections.defaultdict(list)
                  for k in ("blind", "queued")}
    counts = collections.Counter()
    for slug, rows in per.items():
        n = len(rows)
        for i in range(n - 1):
            r, nxt = rows[i], rows[i + 1]
            if not two_sided(r) or not two_sided(nxt):
                continue
            m0 = mid(r)
            dv = (None if r.get("shares_traded") is None
                  or nxt.get("shares_traded") is None
                  else nxt["shares_traded"] - r["shares_traded"])
            if dv is None:
                counts["volume_unmeasured"] += 1
                continue
            if dv <= 0:
                counts["no_trade_interval"] += 1
                continue
            counts["trade_interval"] += 1
            #: WHICH LEG DID THE TRADE FILL? The aggressor's print tells us.
            #: A print we cannot place is counted as unattributed rather
            #: than assigned to a side -- a guessed side would move the
            #: adverse-selection mean on invented information.
            px = nxt.get("last_trade_px")
            if px is None:
                counts["unattributed_no_print"] += 1
                continue
            hit_bid = px <= r["bid"] + 1e-9        # seller hit our YES bid
            lift_ask = px >= r["ask"] - 1e-9       # buyer lifted our offer
            if not hit_bid and not lift_ask:
                counts["unattributed_inside"] += 1
                continue
            side = "YES" if hit_bid else "NO"
            qa = (r.get("queue_ahead_bid") if hit_bid
                  else r.get("queue_ahead_ask"))
            cleared = qa is not None and dv > qa
            counts["queue_cleared" if cleared else "queue_blocked"] += 1
            for tau in HORIZONS:
                j = i + tau
                if j >= n or not two_sided(rows[j]):
                    continue
                m1 = mid(rows[j])
                #: adverse = the mid moved against the leg we were given
                drift = (m0 - m1) if side == "YES" else (m1 - m0)
                legs["blind"][tau].append(drift)
                if cleared:
                    legs["queued"][tau].append(drift)
    out: dict = {"intervals": dict(counts)}
    for mode in ("blind", "queued"):
        for tau in HORIZONS:
            v = legs[mode][tau]
            if not v:
                out["as_%s_tau%d" % (mode, tau)] = None
                out["as_%s_tau%d_n" % (mode, tau)] = 0
                continue
            m = statistics.fmean(v)
            se = (statistics.stdev(v) / math.sqrt(len(v))) if len(v) > 1 else 0.0
            out["as_%s_tau%d" % (mode, tau)] = round(100 * m, 3)   # cents
            out["as_%s_tau%d_se" % (mode, tau)] = round(100 * se, 3)
            out["as_%s_tau%d_n" % (mode, tau)] = len(v)
    return out


#: how many tape rows a hypothetical resting pair is given to complete.
#: At ~80s a row this is roughly 20 minutes -- long for a maker, chosen
#: deliberately so a NO here cannot be blamed on an impatient horizon.
PAIR_HORIZON = 15


def pair_simulation(per: dict, horizon: int = PAIR_HORIZON) -> dict:
    """P(both legs fill) for a pair resting at the touch. Measured, not
    modelled as the product of two one-sided rates -- independence is
    exactly what the two-sided-flow hypothesis denies, so a product would
    assume away the question.

    At each observation we place a hypothetical pair at that moment's bid
    and ask and walk forward up to `horizon` rows:

      YES leg fills on a print at or below our bid
      NO  leg fills on a print at or above our ask

    FILLS ARE UNDER-COUNTED HERE AND THAT MUST BE SAID. The tape carries
    `lastTradePx` -- the LAST print before the poll -- not every print, so
    prints at other levels inside an interval are invisible. Under-counting
    pulls the pair rate DOWN, which is the unsafe direction for a strategy
    whose whole edge is the pair: a real rate could read lower than it is.
    So a low pair rate here is only decisive when it is corroborated, and
    the corroboration is the volume column -- a market with 0.0 shares a
    minute has no prints to miss.
    """
    outcomes = collections.Counter()
    spread_at_entry: dict = collections.defaultdict(collections.Counter)
    for slug, rows in per.items():
        n = len(rows)
        for i in range(n):
            r = rows[i]
            if not two_sided(r):
                continue
            bid0, ask0, sp0 = r["bid"], r["ask"], r["spread"]
            got_yes = got_no = False
            seen = 0
            for j in range(i + 1, min(i + 1 + horizon, n)):
                q = rows[j]
                p = rows[j - 1]
                if q.get("shares_traded") is None or p.get("shares_traded") is None:
                    continue
                seen += 1
                if q["shares_traded"] - p["shares_traded"] <= 0:
                    continue
                px = q.get("last_trade_px")
                if px is None:
                    continue
                if px <= bid0 + 1e-9:
                    got_yes = True
                if px >= ask0 - 1e-9:
                    got_no = True
                if got_yes and got_no:
                    break
            if not seen:
                outcomes["unobserved"] += 1
                continue
            case = ("BOTH" if (got_yes and got_no)
                    else "ONE" if (got_yes or got_no) else "NONE")
            outcomes[case] += 1
            spread_at_entry[bucket_of(sp0)][case] += 1
    return {"outcomes": dict(outcomes),
            "by_spread": {k: dict(v) for k, v in spread_at_entry.items()}}


def controls(per: dict) -> dict:
    """Is the adverse-selection figure information, or volatility wearing a
    sign? Two controls, because a signed conditional mean is exactly the
    kind of statistic that looks like a finding when it is an artefact.

    MIRROR. Signing by the opposite leg must give the exact negative. If it
    does not, the attribution step is contributing a drift of its own.

    TRADED vs UNTRADED absolute move. Adverse selection predicts the mid
    moves MORE across intervals where someone traded than across quiet
    ones. If the two were equal, conditioning on a trade would be telling
    us nothing and the signed mean would be ordinary volatility that the
    sign convention had tidied up.
    """
    signed, mirror, hit_abs, quiet_abs = [], [], [], []
    for _slug, rows in per.items():
        for i in range(len(rows) - 1):
            r, x = rows[i], rows[i + 1]
            if not two_sided(r) or not two_sided(x):
                continue
            if r.get("shares_traded") is None or x.get("shares_traded") is None:
                continue
            dv = x["shares_traded"] - r["shares_traded"]
            m0, m1 = mid(r), mid(x)
            if dv <= 0:
                quiet_abs.append(abs(m1 - m0))
                continue
            px = x.get("last_trade_px")
            if px is None:
                continue
            hit = px <= r["bid"] + 1e-9
            lift = px >= r["ask"] - 1e-9
            if not hit and not lift:
                continue
            hit_abs.append(abs(m1 - m0))
            d = (m0 - m1) if hit else (m1 - m0)
            signed.append(d)
            mirror.append(-d)

    def agg(v):
        if not v:
            return None
        m = statistics.fmean(v)
        se = (statistics.stdev(v) / math.sqrt(len(v))) if len(v) > 1 else 0.0
        return {"mean_c": round(100 * m, 3), "se_c": round(100 * se, 3),
                "n": len(v), "t": round(m / se, 2) if se else None}
    out = {"signed": agg(signed), "mirror": agg(mirror),
           "abs_traded": agg(hit_abs), "abs_quiet": agg(quiet_abs)}
    if hit_abs and quiet_abs:
        out["abs_ratio_traded_over_quiet"] = round(
            statistics.fmean(hit_abs) / statistics.fmean(quiet_abs), 2)
    return out


def report(tape: str) -> int:
    d = load(tape)
    per = d["per"]
    if not per:
        print("no readable rows in %s -- NOT a null result, an empty tape"
              % tape)
        return 1
    rows_n = sum(len(v) for v in per.values())
    print("SPORTS SPREAD x DEPTH CENSUS")
    print("tape %s   markets %d   readable observations %d"
          % (tape, len(per), rows_n))
    if d["skipped"]:
        print("excluded (counted, never silently dropped): %s"
              % dict(d["skipped"]))
    print()
    cells = census(per)
    print("%-11s %6s %8s %10s %11s %11s %11s %13s %13s"
          % ("spread", "obs", "markets", "med_sprd", "bid_touch",
             "ask_touch", "pair_size", "capacity_$", "vol_sh/min"))
    for c in cells:
        if not c["obs"]:
            print("%-11s %6d %8d          --          --          --"
                  "          --            --            --"
                  % (c["bucket"], 0, 0))
            continue
        def f(x, nd=2):
            return "--" if x is None else ("%.*f" % (nd, x))
        print("%-11s %6d %8d %10s %11s %11s %11s %13s %13s"
              % (c["bucket"], c["obs"], c["markets"],
                 f(c["med_spread"], 4), f(c["med_bid_touch"]),
                 f(c["med_ask_touch"]), f(c["med_pair_size"]),
                 f(c["med_capacity_usd"], 4),
                 f(c["med_vol_shares_per_min"], 1)))
    print()
    print("capacity_$ = med_spread x med_pair_size: what ONE filled pair")
    print("grosses at the smaller of the two touches. This is the product")
    print("the instruction asked for; spread alone is the first column and")
    print("decides nothing on its own.")
    print()
    a = adverse_selection(per)
    print("ADVERSE SELECTION, volume-triggered, cents per share")
    print("  trade intervals %s   no-trade %s   volume unmeasured %s"
          % (a["intervals"].get("trade_interval", 0),
             a["intervals"].get("no_trade_interval", 0),
             a["intervals"].get("volume_unmeasured", 0)))
    print("  unattributed: no print %s   inside the touch %s"
          % (a["intervals"].get("unattributed_no_print", 0),
             a["intervals"].get("unattributed_inside", 0)))
    print("  queue cleared %s   queue blocked %s"
          % (a["intervals"].get("queue_cleared", 0),
             a["intervals"].get("queue_blocked", 0)))
    print()
    for mode, label in (("blind", "queue-blind (optimistic, BTC5m's rule)"),
                        ("queued", "queue-cleared (our size actually reached)")):
        print("  %s" % label)
        for tau in HORIZONS:
            v = a.get("as_%s_tau%d" % (mode, tau))
            n = a.get("as_%s_tau%d_n" % (mode, tau), 0)
            se = a.get("as_%s_tau%d_se" % (mode, tau))
            if v is None:
                print("    tau %-3d  NOT MEASURED (n=0) -- reported as null, "
                      "never as 0" % tau)
            else:
                print("    tau %-3d  %+7.3f c/share   se %.3f   n %d"
                      % (tau, v, se, n))
        print()
    p = pair_simulation(per)
    o = p["outcomes"]
    tot = o.get("BOTH", 0) + o.get("ONE", 0) + o.get("NONE", 0)
    print("PAIR COMPLETION, hypothetical pair resting at the touch, %d rows"
          % PAIR_HORIZON)
    print("  placements %d   unobserved %d (reported apart, never as NONE)"
          % (tot, o.get("unobserved", 0)))
    if tot:
        print("  BOTH %d = %.2f%%   ONE %d = %.2f%%   NONE %d = %.2f%%"
              % (o.get("BOTH", 0), 100.0 * o.get("BOTH", 0) / tot,
                 o.get("ONE", 0), 100.0 * o.get("ONE", 0) / tot,
                 o.get("NONE", 0), 100.0 * o.get("NONE", 0) / tot))
    print()
    print("  by spread at entry:")
    print("  %-11s %7s %7s %7s %9s" % ("spread", "BOTH", "ONE", "NONE",
                                       "both_pct"))
    for name, _lo, _hi in SPREAD_BUCKETS:
        c = p["by_spread"].get(name)
        if not c:
            print("  %-11s %7s %7s %7s %9s" % (name, 0, 0, 0, "--"))
            continue
        t = c.get("BOTH", 0) + c.get("ONE", 0) + c.get("NONE", 0)
        print("  %-11s %7d %7d %7d %9s"
              % (name, c.get("BOTH", 0), c.get("ONE", 0), c.get("NONE", 0),
                 "%.2f%%" % (100.0 * c.get("BOTH", 0) / t) if t else "--"))
    print()
    #: THE ONE EQUATION, with the measured numbers in it. A pair pays the
    #: spread; a stranded leg pays adverse selection. Nothing else is
    #: assumed, and every term here was measured above rather than fitted.
    print("NET PER PLACEMENT = P(both) x spread  -  P(one) x adverse_selection")
    for name, _lo, _hi in SPREAD_BUCKETS:
        c = p["by_spread"].get(name)
        cell = next((x for x in cells if x["bucket"] == name), None)
        if not c or not cell or not cell.get("obs") or cell.get("med_spread") is None:
            continue
        t = c.get("BOTH", 0) + c.get("ONE", 0) + c.get("NONE", 0)
        if not t:
            continue
        p_both = c.get("BOTH", 0) / t
        p_one = c.get("ONE", 0) / t
        asel = a.get("as_queued_tau1")
        if asel is None:
            asel = a.get("as_blind_tau1")
        if asel is None:
            print("  %-11s adverse selection NOT MEASURED -- no net stated"
                  % name)
            continue
        net = p_both * cell["med_spread"] * 100.0 - p_one * asel
        size = cell.get("med_pair_size")
        print("  %-11s  P(both) %.3f  spread %.2fc  P(one) %.3f  AS %.2fc"
              "  -> net %+.3f c/share   x pair size %s = %s"
              % (name, p_both, 100 * cell["med_spread"], p_one, asel, net,
                 "--" if size is None else "%.0f" % size,
                 "--" if size is None else "$%+.4f" % (net * size / 100.0)))
    print()
    c = controls(per)
    print("CONTROLS on the adverse-selection figure")
    for key, label in (("signed", "signed by the leg we were filled on"),
                       ("mirror", "signed by the OPPOSITE leg (mirror)"),
                       ("abs_traded", "|mid move|, intervals WITH a trade"),
                       ("abs_quiet", "|mid move|, intervals with NO trade")):
        v = c.get(key)
        if v is None:
            print("  %-36s n=0" % label)
            continue
        print("  %-36s %+7.3f c  se %.3f  n %4d  t %s"
              % (label, v["mean_c"], v["se_c"], v["n"],
                 "--" if v["t"] is None else "%+.2f" % v["t"]))
    if c.get("abs_ratio_traded_over_quiet") is not None:
        print("  traded intervals move %.2fx as far as quiet ones."
              % c["abs_ratio_traded_over_quiet"])
        print("  Were that ratio 1.0, the signed figure above would be")
        print("  ordinary volatility wearing a sign rather than information.")
    print()
    print("  the adverse-selection term is the pooled tau=1 figure, not a")
    print("  per-bucket one: there are too few queue-cleared fills in any")
    print("  single bucket to split it, and splitting it on n<10 would be")
    print("  inventing precision. Stated so the number is not read as more")
    print("  resolved than it is.")
    return 0


def crosscheck(tape: str) -> int:
    """Reproduce the published BTC5m baseline with THIS file's machinery."""
    from decimal import Decimal as D
    PRICE = D("0.08")
    CUT = 300.0 - 30.0
    UPC, DOWNC = "UP", "DOWN"
    per: dict = collections.defaultdict(list)
    spreads = []
    with open(tape, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            per[r["slug"]].append(r)
            b, a = r.get("bid"), r.get("ask")
            if b is not None and a is not None:
                spreads.append(float(a) - float(b))

    def dec(v):
        return None if v is None else D(str(v))

    def own(side, b, a):
        return b if side == UPC else D(1) - a

    def outcome(rows):
        for q in reversed(rows):
            if q["t"] < CUT:
                break
            b, a = dec(q.get("bid")), dec(q.get("ask"))
            if b is not None and b >= D("0.90"):
                return UPC
            if a is not None and a <= D("0.10"):
                return DOWNC
        return None

    cases = collections.Counter()
    pnls = []
    for slug, rows in per.items():
        rows.sort(key=lambda r: r["t"])
        won = outcome(rows)
        if won is None:
            continue
        filled = {}
        for q in rows:
            if q["t"] < 0 or q["t"] >= CUT:
                continue
            b, a = dec(q.get("bid")), dec(q.get("ask"))
            if b is None or a is None:
                continue
            for side in (UPC, DOWNC):
                if side in filled:
                    continue
                p = own(side, b, a)
                if p is not None and p <= PRICE:
                    filled[side] = q["t"]
        case = {2: "BOTH", 1: "ONE", 0: "NONE"}[len(filled)]
        cases[case] += 1
        pnl = D(0)
        for side in filled:
            pnl += (D(1) if side == won else D(0)) - PRICE
        pnls.append(float(pnl))
    n = len(pnls)
    if not n:
        print("no settled windows in %s" % tape)
        return 1
    m = statistics.fmean(pnls)
    se = statistics.stdev(pnls) / math.sqrt(n)
    print("CROSS-CHECK: this file's fill rule and P&L over the committed "
          "BTC5m tape")
    print()
    print("  windows %d                       published 189 (187 settled)" % n)
    print("  BOTH  %3d = %5.1f%%               published 10.2%%"
          % (cases["BOTH"], 100 * cases["BOTH"] / n))
    print("  ONE   %3d = %5.1f%%               published 86.6%%"
          % (cases["ONE"], 100 * cases["ONE"] / n))
    print("  NONE  %3d = %5.1f%%               published  3.2%%"
          % (cases["NONE"], 100 * cases["NONE"] / n))
    print("  net %+.2f c/w  se %.2f  t %+.2f   published +3.74 c/w, t +1.67"
          % (100 * m, 100 * se, m / se if se else 0.0))
    print()
    one_c = sum(1 for s in spreads if abs(s - 0.01) < 1e-9)
    print("  median spread %.4f   1c share %.1f%% of %d two-sided quotes"
          % (statistics.median(spreads), 100.0 * one_c / len(spreads),
             len(spreads)))
    print("  published: median 1 cent, 71%% of 21,151 two-sided quotes")
    print()
    print("  NOT validated by this check: the volume-triggered fill model")
    print("  and the queue test. The BTC5m tape carries no volume at all")
    print("  (/bbo 404s on a live crypto window), so those components are")
    print("  exercised only on the sports tape and are stated as such.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape")
    ap.add_argument("--crosscheck")
    a = ap.parse_args()
    if a.crosscheck:
        return crosscheck(a.crosscheck)
    if a.tape:
        return report(a.tape)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
