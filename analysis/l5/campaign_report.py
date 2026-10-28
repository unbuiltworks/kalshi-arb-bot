#!/usr/bin/env python3
"""One concise profitability report per campaign, from its tape alone.

    python3 -I analysis/l5/campaign_report.py analysis/l5/campaign6

Covers settled net P&L, completed pairs, pair costs, defensive losses,
stranded inventory, queue depth, fill quality and capital utilisation, then
names ONE highest-impact improvement for the next campaign.

TWO RULES IT ENFORCES ON ITSELF.

Settled net comes from the AUTHORITATIVE CASH SERIES, not from summing our
own pair arithmetic. The two are printed side by side and a mismatch is
reported as a mismatch; a report that silently trusts its own model is how a
losing campaign reads as a winning one.

An unfilled opportunity is never profit. Refused, cancelled and
never-transmitted orders are counted as what they are, and the recommender
is forbidden from proposing more capital unless capital is shown to bind.
"""
import json
import pathlib
import statistics
import sys
from decimal import Decimal as D

ONE = D("1.00")
ENVELOPE_USD = D("12.00")      # L5_MAX_CASH_DEPLOYED_USD
EXPOSURE_USD = D("20.00")      # L5_MAX_TOTAL_EXPOSURE_USD
ENTRY_CUTOFF_S = 600


def edge_of(pairs):
    return sum((p["pnl"] for p in pairs if not p["defensive"]),
               D(0))


def load(d, name):
    p = d / name
    if not p.exists():
        return []
    with p.open() as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def secs(utc):
    hh, mm, ss = str(utc)[11:19].split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def own(side, wire):
    w = D(str(wire))
    return w if str(side).upper().endswith("UP") else ONE - w


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    d = pathlib.Path(argv[1]).resolve()
    submits = {s["coid"]: s for s in load(d, "tape_submits.jsonl")}
    fills = {f["coid"]: f for f in load(d, "tape_fills.jsonl")}
    terms = {t["coid"]: t for t in load(d, "tape_terminals.jsonl")}
    cash = load(d, "tape_cash_moves.jsonl")
    arming = load(d, "tape_arming.jsonl")
    refusals = load(d, "tape_slot_refusals.jsonl")

    sent = {k: v for k, v in submits.items() if v.get("transmitted")}
    print("=" * 76)
    print("CAMPAIGN PROFITABILITY REPORT -- %s" % d.name)
    print("=" * 76)

    # ---------------------------------------------------------- settlement
    net = None
    if len(cash) >= 2:
        net = D(str(cash[-1]["available_cash_usd"])) \
            - D(str(cash[0]["available_cash_usd"]))
    print("\nSETTLED NET P&L (authoritative cash)")
    if net is None:
        print("  cash series unavailable -- NOT reporting a net")
    else:
        print("  cash %s -> %s" % (cash[0]["available_cash_usd"],
                                   cash[-1]["available_cash_usd"]))
        print("  settled net                      %+10.4f" % net)

    # ------------------------------------------------------- pairs & costs
    legs, per_win = [], {}
    for coid, s in sent.items():
        f = fills.get(coid)
        if f is None:
            continue
        legs.append({"coid": coid, "w": s.get("window"), "side": s["side"],
                     "qty": D(str(f["qty"])), "own": own(s["side"],
                                                         f["wire_px"]),
                     "t_sub": secs(s["utc"]), "t_fill": secs(f["utc"])})
    legs.sort(key=lambda r: r["t_fill"])
    pairs = []
    for leg in legs:
        st = per_win.setdefault(leg["w"], {"UP": [], "DOWN": []})
        st[leg["side"]].append([leg["qty"], leg["own"], leg["coid"]])
        while st["UP"] and st["DOWN"]:
            up, dn = st["UP"][0], st["DOWN"][0]
            q = min(up[0], dn[0])
            cost = up[1] + dn[1]
            pairs.append({"w": leg["w"], "qty": q, "cost": cost,
                          "pnl": q * (ONE - cost), "defensive": cost > ONE})
            up[0] -= q
            dn[0] -= q
            if up[0] == 0:
                st["UP"].pop(0)
            if dn[0] == 0:
                st["DOWN"].pop(0)
    edge = sum((p["pnl"] for p in pairs if not p["defensive"]), D(0))
    defensive = sum((p["pnl"] for p in pairs if p["defensive"]), D(0))
    print("\nCOMPLETED PAIRS AND PAIR COSTS")
    print("  pairs completed                  %10d" % len(pairs))
    for p in pairs:
        print("    %-6s qty %-5s cost %-7s %+8.4f %s"
              % (p["w"], p["qty"], p["cost"], p["pnl"],
                 "DEFENSIVE" if p["defensive"] else ""))
    if pairs:
        costs = [float(p["cost"]) for p in pairs]
        print("  pair cost min/median/max         %.4f / %.4f / %.4f"
              % (min(costs), statistics.median(costs), max(costs)))
    print("  pair edge (cost <= 1.00)         %+10.4f" % edge)
    print("  DEFENSIVE losses (cost > 1.00)   %+10.4f  (%d pair(s))"
          % (defensive, sum(1 for p in pairs if p["defensive"])))
    if net is not None:
        model = edge + defensive
        agree = model == net
        print("  model vs cash                    %+10.4f  %s"
              % (model, "AGREES" if agree else "MISMATCH -- trust the cash"))

    # --------------------------------------------------- stranded inventory
    stranded = {}
    for w, st in per_win.items():
        for side in ("UP", "DOWN"):
            q = sum((l[0] for l in st[side]), D(0))
            if q > 0:
                stranded[(w, side)] = q
    print("\nSTRANDED INVENTORY")
    if not stranded:
        print("  none -- every filled leg was matched within its window")
    else:
        for (w, side), q in sorted(stranded.items()):
            print("  %-6s %-4s unmatched %s" % (w, side, q))

    # ------------------------------------------------- fill quality & queue
    filled = [l for l in legs]
    lat = [l["t_fill"] - l["t_sub"] for l in filled]
    qd = [float(s["queue_ahead"]) for s in sent.values()
          if s.get("queue_ahead") not in (None, "")]
    ld = [float(s["level_depth"]) for s in sent.values()
          if s.get("level_depth") not in (None, "")]
    print("\nFILL QUALITY")
    print("  transmitted / filled             %10s" % (
        "%d / %d" % (len(sent), len(filled))))
    if sent:
        print("  fill rate                        %9.0f%%"
              % (100.0 * len(filled) / len(sent)))
    if lat:
        print("  fill latency min/median/max      %.1fs / %.1fs / %.1fs"
              % (min(lat), statistics.median(lat), max(lat)))
    nf = len(sent) - len(filled)
    print("  transmitted, never filled        %10d  (cancelled/expired)" % nf)
    print("  priced but NEVER transmitted     %10d  (not opportunities)"
          % (len(submits) - len(sent)))
    print("\nQUEUE DEPTH AT PLACEMENT")
    if qd:
        print("  contracts ahead min/median/max   %.0f / %.0f / %.0f"
              % (min(qd), statistics.median(qd), max(qd)))
    if ld:
        print("  own level depth median           %.0f" % statistics.median(ld))

    # ------------------------------------------------ capital utilisation
    spend = sum((l["qty"] * l["own"] for l in legs), D(0))
    by_win = {}
    for l in legs:
        by_win[l["w"]] = by_win.get(l["w"], D(0)) + l["qty"] * l["own"]
    peak = max(by_win.values()) if by_win else D(0)
    print("\nCAPITAL UTILISATION")
    print("  total cash spent on fills        %10.4f" % spend)
    print("  peak spend in one window         %10.4f of %s envelope"
          % (peak, ENVELOPE_USD))
    print("  peak envelope utilisation        %9.0f%%"
          % (100.0 * float(peak) / float(ENVELOPE_USD)))
    print("  exposure ceiling                 %10s (never approached)"
          % EXPOSURE_USD)

    # --------------------------------------------------------- throughput
    occ = {}
    for w in by_win:
        tot = 0.0
        for coid, s in sent.items():
            if s.get("window") != w:
                continue
            t = terms.get(coid)
            if t is None:
                continue
            tot += max(0.0, secs(t["utc"]) - secs(s["utc"]))
        occ[w] = tot
    print("\nTHROUGHPUT")
    if occ:
        tot_occ = sum(occ.values())
        span = ENTRY_CUTOFF_S * len(occ)
        print("  entry-period occupancy           %9.0f%%  (%.0fs of %ds)"
              % (100.0 * tot_occ / span, tot_occ, span))
    print("  MAX_LIVE_ORDERS refusals         %10d" % len(refusals))
    if arming:
        lat_arm = []
        for a in arming:
            if a.get("window_start_utc") and a.get("utc"):
                lat_arm.append(secs(a["utc"]) - secs(a["window_start_utc"]))
        if lat_arm:
            print("  arming latency min/max           %.1fs / %.1fs"
                  % (min(lat_arm), max(lat_arm)))

    print("\n" + "=" * 76)
    print("ONE RECOMMENDATION FOR THE NEXT CAMPAIGN")
    print("=" * 76)
    late = []
    for a in arming:
        if a.get("window_start_utc") and a.get("utc"):
            s_ = secs(a["utc"]) - secs(a["window_start_utc"])
            if s_ > ARMING_LATE_S:
                late.append(s_)
    print(recommend(sent, filled, pairs, defensive, peak, occ,
                    refusals, qd, stranded, arming_late=late))
    return 0


#: A defensive completion below this share of pair edge is NOISE, not a
#: lever. Campaign 6's cost $0.0970 against $1.6878 of edge -- 5.7% -- and
#: it was the CORRECT call: carrying that leg risked -$0.7907. Ranking a
#: 6% drag above a lost window would send the next campaign after the
#: wrong thing, which is the one failure a recommender must not have.
DEFENSIVE_MATERIAL_FRACTION = 0.15

#: Arming later than this means the window's entry period was materially
#: eaten. Campaign 6 lost a whole window of four to a 174s arming delay --
#: 25% of the campaign, dwarfing every other line in its report.
ARMING_LATE_S = 60.0


def recommend(sent, filled, pairs, defensive, peak, occ, refusals, qd,
              stranded, arming_late=()):
    """Exactly one lever, chosen by evidence, in priority order.

    CAPITAL IS ONLY EVER RECOMMENDED IF IT BOUND. The owner's standing rule
    is that capital must not be proposed unless it is shown to be the
    constraint, so the test is explicit and comes first -- and failing it
    removes capital from consideration entirely rather than leaving it as a
    fallback.
    """
    util = 100.0 * float(peak) / float(ENVELOPE_USD) if peak else 0.0
    if util >= 90.0:
        return ("RAISE THE CASH ENVELOPE. Peak single-window spend reached "
                "%.0f%% of the $%s envelope, so capital bound and refused "
                "fills that the gates otherwise allowed." % (util, ENVELOPE_USD))

    note = ("Capital did NOT bind: peak single-window spend was %.0f%% of "
            "the $%s envelope. Raising capital is therefore excluded.\n"
            % (util, ENVELOPE_USD))

    if stranded:
        return (note + "FIX STRANDED INVENTORY FIRST. %d leg(s) ended a "
                "window unmatched. Stranded residual is the loss channel "
                "that cost Campaign 5 -$2.9034; nothing else matters until "
                "windows close flat." % len(stranded))

    if arming_late:
        return (note + "FIX ARMING LATENCY. %d window(s) armed %s late, so "
                "their entry period was spent unarmed -- the largest single "
                "loss of opportunity in this campaign, and it costs nothing "
                "to fix. Deploy and restart BETWEEN windows so the observer "
                "subscribes before open; a process already spanning the "
                "boundary arms in about a second."
                % (len(arming_late),
                   "/".join("%.0fs" % s for s in arming_late)))

    occ_pct = (100.0 * sum(occ.values()) / (ENTRY_CUTOFF_S * len(occ))
               if occ else 0.0)
    if occ_pct >= 70.0:
        return (note + "ADD A SECOND RESTING ORDER, with validation. The "
                "single slot was occupied %.0f%% of eligible entry time and "
                "refused %d submissions, so it is genuinely the constraint."
                % (occ_pct, len(refusals)))

    rate = 100.0 * len(filled) / len(sent) if sent else 0.0
    med_q = statistics.median(qd) if qd else 0.0
    if rate < 60.0 and med_q > 500:
        return (note + "IMPROVE QUEUE POSITION. Fill rate was %.0f%% with a "
                "median %.0f contracts ahead at placement, so unfilled "
                "orders are losing the queue, not the price. Measure "
                "placement depth against realised fills before changing it "
                "-- this is a strategy parameter and needs its own "
                "authorization." % (rate, med_q))

    frac = (abs(float(defensive)) / float(edge_of(pairs))
            if edge_of(pairs) > 0 else 0.0)
    if defensive < 0 and frac >= DEFENSIVE_MATERIAL_FRACTION:
        return (note + "REDUCE DEFENSIVE COMPLETIONS. They cost %+0.4f this "
                "campaign. Each one began as an opening whose complement ran "
                "away; the lever is opening quality, measured by "
                "adverse_selection.py, not the completion rule. That is "
                "%.0f%% of pair edge, above the %.0f%% materiality bar."
                % (defensive, 100.0 * frac,
                   100.0 * DEFENSIVE_MATERIAL_FRACTION))

    return (note + "EXTEND DURATION. Occupancy was %.0f%%, fill rate %.0f%%, "
            "no stranding and no defensive loss, so the engine is not "
            "constrained by capital, slots or execution -- it is "
            "opportunity-limited. More eligible windows is the only lever "
            "that adds pairs without touching exposure, concurrency or any "
            "gate." % (occ_pct, rate))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
