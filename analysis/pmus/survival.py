"""Compatible-flow survival curves on COMPLETE-eligible chains. SHADOW ONLY.

The residual-cap hypothesis is closed, so this runs ONLY cohort A, the shipped
SAFE_TRANSFER_CONTROL at MAX_RESIDUAL_UNITS=3, unchanged.

Clock starts at the COMPLEMENT QUOTE, not the first fill: the question is how
long opposing flow takes to arrive once we are actually asking for it.

CENSORING IS IDENTICAL ACROSS VENUES AND HORIZONS. A chain contributes to the
"by t" denominator only if it was OBSERVED for t seconds after its complement
quote. A chain observed for less than t cannot answer the question and is
EXCLUDED at that horizon rather than scored a miss. "market end" uses whatever
observation each chain actually has.

Optimistic and queue-aware are separate sequential replays and their economics
are reported separately. PMUS flow is CAUSAL INFERRED from sharesTraded deltas
and is never called a real print; International flow is REAL PRINTS.
"""
import sys, json, collections, time, statistics as stt
from decimal import Decimal as D
sys.path.insert(0, "/home/user/kalshi-arb-bot")
sys.path.insert(0, "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad")
import cohorts as C
from src import pmus_sports_l5_decision as dec

assert dec.MAX_RESIDUAL_UNITS == 3, "control cohort must be the shipped cap"
HOR = [10.0, 30.0, 60.0, 120.0, 300.0, 600.0]
LOOKAHEAD = 600.0   # bounded, and equal to the largest reported horizon
M = C.M

def mkt_features(se, prints):
    mins = (se[-1]["t"] - se[0]["t"]) / 60.0
    mv = 0.0
    if mins > 0:
        mv = sum(1 for i in range(1, len(se))
                 if se[i]["bid"] != se[i-1]["bid"] or se[i]["ask"] != se[i-1]["ask"]) / mins
    sp = stt.median([r["ask"] - r["bid"] for r in se])
    dep = [r["bid_qty"] for r in se if r.get("bid_qty")]
    return {"spread_med": round(sp, 4),
            "depth_med": round(stt.median(dep), 1) if dep else None,
            "move_min": round(mv, 2),
            "flow_per_min": round(len(prints) / mins, 3) if mins > 0 else None,
            "obs_min": round(mins, 1)}

def replay(venue, slug, se, prints, fill_rule, feats):
    inv = dec.Inventory(); last_rel = ""; t_prev = 0.0
    chains = []; cur = None
    for r in se:
        t = r["t"]
        if t - t_prev < dec.DECISION_MIN_GAP_S: continue
        t_prev = t
        bk = {"bid": D(str(r["bid"])), "ask": D(str(r["ask"])), "t": t}
        prop = dec._proposal(M, float(r["bid"]), inv, last_rel)
        if prop.get("off_distribution"): continue
        rel = prop["recommended_relation"]
        if cur is not None and rel == "complete": cur["eligible"] = True
        plan = dec.plan_action(rel, inv, bk)
        if plan["decision"] != "quote": continue
        px = float(plan["_px"]); side_yes = plan["_side_yes"]; rl = plan["relation"]
        depth_ahead = float((r["bid_qty"] if side_yes else r["ask_qty"]) or 0.0)
        comp = C.compatible(prints, t, t + LOOKAHEAD, px, side_yes)
        cum = 0.0; t_qa = None
        for p in comp:
            cum += p["size"]
            if cum > depth_ahead: t_qa = p["t"]; break
        if cur is not None and rl == "complete":
            if cur["t_quote"] is None:
                cur["t_quote"] = t
                cur["quote_spread"] = round(r["ask"] - r["bid"], 4)
                cur["quote_depth"] = depth_ahead
            if comp and cur["t_flow"] is None: cur["t_flow"] = comp[0]["t"]
            if t_qa is not None and cur["t_qafill"] is None: cur["t_qafill"] = t_qa
        t_fill = (comp[0]["t"] if comp else None) if fill_rule == "OPTIMISTIC" else t_qa
        if t_fill is None:
            last_rel = rl; continue
        inv.record(side_yes, D(str(px)))
        last_rel = rl
        if cur is not None:
            if side_yes: cur["yes_cost"] += px; cur["yes_n"] += 1
            else: cur["no_cost"] += px; cur["no_n"] += 1
        if cur is not None and inv.residual == 0 and cur["yes_n"] and cur["no_n"]:
            cur["matched"] = True; cur["t_matched"] = t_fill
            cur["pair_basis"] = round(cur["yes_cost"]/cur["yes_n"]
                                      + cur["no_cost"]/cur["no_n"], 4)
            cur["end_t"] = se[-1]["t"]; chains.append(cur); cur = None
            continue
        if rl == "open" and cur is None:
            cur = {"venue": venue, "slug": slug, "league": r["league"],
                   "sports_type": r["sports_type"], "t_first": t,
                   "eligible": False, "t_quote": None, "t_flow": None,
                   "t_qafill": None, "t_matched": None, "matched": False,
                   "quote_spread": None, "quote_depth": None,
                   "yes_cost": 0.0, "yes_n": 0, "no_cost": 0.0, "no_n": 0,
                   "pair_basis": None, **feats}
    if cur is not None:
        cur["end_t"] = se[-1]["t"]; chains.append(cur)
    return chains

def survival(chains, key):
    """cumulative conversion by t, right-censored chains EXCLUDED per horizon."""
    out = {}
    for h in HOR:
        elig = [c for c in chains if c["t_quote"] is not None
                and (c["end_t"] - c["t_quote"]) >= h]
        hit = sum(1 for c in elig
                  if c[key] is not None and (c[key] - c["t_quote"]) <= h)
        out["by_%ds" % int(h)] = {"n": len(elig),
            "pct": round(100.0 * hit / len(elig), 1) if elig else None}
    elig = [c for c in chains if c["t_quote"] is not None]
    hit = sum(1 for c in elig if c[key] is not None)
    out["by_market_end"] = {"n": len(elig),
        "pct": round(100.0 * hit / len(elig), 1) if elig else None}
    return out

res = {"OPTIMISTIC": collections.defaultdict(list),
       "QUEUE_AWARE": collections.defaultdict(list)}
for (venue, slug), se in C.BK.items():
    if len(se) < 8: continue
    pr = C.FL.get((venue, slug), [])
    f = mkt_features(se, pr)
    for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
        for c in replay(venue, slug, se, pr, rule, f):
            res[rule][venue].append(c)

report = {"labels": "SHADOW/HYPOTHETICAL. Cohort A only (MAX_RESIDUAL_UNITS=3, "
                    "shipped, unchanged). Nothing submitted, filled, settled "
                    "or realized.",
          "clock": "t=0 is the COMPLEMENT QUOTE",
          "censoring": "a chain enters the 'by t' denominator only if observed "
                       "for t seconds after its complement quote; otherwise "
                       "EXCLUDED at that horizon, never scored a miss",
          "provenance": {"PMUS": "CAUSAL INFERRED flow (sharesTraded deltas) "
                                 "-- never a real print",
                         "INTL": "REAL PRINTS, direction known"},
          "model_sha16": M["_sha256_16"],
          "as_of_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
    for venue in ("PMUS", "INTL"):
        cs = [c for c in res[rule][venue] if c["eligible"]]
        if not cs: continue
        report["%s|%s|P_compatible_flow_by_t" % (venue, rule)] = survival(cs, "t_flow")
        report["%s|%s|P_queue_aware_fill_by_t" % (venue, rule)] = survival(cs, "t_qafill")
        report["%s|%s|P_matched_by_t" % (venue, rule)] = survival(cs, "t_matched")
        pb = [c["pair_basis"] for c in cs if c["matched"] and c["pair_basis"]]
        report["%s|%s|economics" % (venue, rule)] = {
            "matched": sum(1 for c in cs if c["matched"]),
            "pair_basis_n": len(pb),
            "pair_basis_median": round(stt.median(pb), 4) if pb else None,
            "pct_below_1": round(100.0*sum(1 for x in pb if x < 1.0)/len(pb), 1) if pb else None,
            "fees_rebates": "maker fee ZERO both venues. PMUS maker rebate is "
                            "venue-credited but NOT attributable at fill level "
                            "(/v1/transfers carries no order id), so it is "
                            "EXCLUDED, not estimated. No reward assumed.",
            "label": "MODELED pair basis. NOT edge, NOT profit, NOT expectancy."}

print("=" * 104)
print("P(compatible flow by t | COMPLETE-eligible)   clock = complement quote")
print("=" * 104)
H = "  %-22s %-12s" % ("venue|rule", "metric") + "".join("%13s" % ("by%ds"%int(h)) for h in HOR) + "%13s" % "mkt end"
print(H); print("  " + "-"*(len(H)-2))
for venue in ("PMUS", "INTL"):
    for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
        for lab, k in (("compat flow", "P_compatible_flow_by_t"),
                       ("qa fill", "P_queue_aware_fill_by_t"),
                       ("matched", "P_matched_by_t")):
            s = report.get("%s|%s|%s" % (venue, rule, k))
            if not s: continue
            line = "  %-22s %-12s" % ("%s|%s" % (venue, rule[:4]), lab)
            for h in HOR:
                c = s["by_%ds" % int(h)]
                line += "%13s" % ("%s%% n=%d" % (c["pct"], c["n"]) if c["pct"] is not None else "-")
            c = s["by_market_end"]
            line += "%13s" % ("%s%% n=%d" % (c["pct"], c["n"]) if c["pct"] is not None else "-")
            print(line)
    print()

# ---- PMUS stratification of COMPLETE-eligible chains
print("=" * 104)
print("PMUS COMPLETE-ELIGIBLE CHAINS, STRATIFIED (cohort A)")
print("=" * 104)
for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
    cs = [c for c in res[rule]["PMUS"] if c["eligible"]]
    if not cs: continue
    print("  -- %s: %d eligible, %d matched" % (rule, len(cs), sum(1 for c in cs if c["matched"])))
    def strat(fn, label):
        g = collections.defaultdict(list)
        for c in cs: g[fn(c)].append(c)
        for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
            fl = sum(1 for c in v if c["t_flow"] is not None)
            mt = sum(1 for c in v if c["matched"])
            print("     %-14s %-26s n=%-3d flow=%-3d matched=%-2d  sprd=%-7s depth=%-8s mv/min=%-5s flow/min=%s"
                  % (label, str(k)[:26], len(v), fl, mt,
                     v[0]["spread_med"], v[0]["depth_med"], v[0]["move_min"],
                     v[0]["flow_per_min"]))
    strat(lambda c: c["league"], "sport")
    strat(lambda c: c["sports_type"], "market_type")
    print("     %-14s" % "matched chains:")
    for c in cs:
        if c["matched"]:
            print("        %-42s %s %s sprd=%s depth=%s mv/min=%s flow/min=%s basis=%s"
                  % (c["slug"][:42], c["league"], c["sports_type"][:18],
                     c["spread_med"], c["depth_med"], c["move_min"],
                     c["flow_per_min"], c["pair_basis"]))
    print()

json.dump(report, open("survival.json", "w"), indent=1, default=str)
json.dump({r: {v: res[r][v] for v in res[r]} for r in res},
          open("survival_chains.json", "w"), indent=1, default=str)
