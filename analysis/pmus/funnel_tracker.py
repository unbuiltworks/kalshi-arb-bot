"""STAGED post-first-fill funnel. SHADOW/HYPOTHETICAL. Venues NEVER pooled.

FIRST FILL -> POLICY COMPLETE RECOMMENDATION -> COMPLEMENT QUOTED -> FEE-SAFE
-> REACHED -> COMPATIBLE FLOW -> HYPOTHETICAL OPPOSITE FILL -> MATCHED

Reports the two conditional rates the mandate asks for, each bracketed by an
OPTIMISTIC upper bound and a QUEUE-AWARE lower bound, never collapsed:

  P(COMPLETE-eligible | first fill)   how often the frozen policy ever ASKS
                                      for the opposite side. This is itself a
                                      transfer statistic, not a nuisance step.
  P(matched | COMPLETE-eligible)      conditional recycling, the number that
                                      needs >=25 PMUS eligible chains before
                                      it means anything.

COMPLETE-ELIGIBLE is defined as: after the first fill and while imbalanced, the
frozen v2 model returned recommended_relation == "complete" at least once.

Frozen policy used unmodified (Inventory, plan_action, complement_target,
pv2.proposal). MAX_ACTIONS is not applied because that module documents it as
bounded STATUS EMISSION, a display cap; following a chain to completion needs
it lifted. DECISION_MIN_GAP_S is kept. A fill is NEVER inferred from book reach
alone -- a compatible PRINT is always required beyond it.
"""
import sys, os, json, collections, time, statistics as stt
import datetime as dt
from decimal import Decimal as D
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src.pmus_sports_l5_decision import (Inventory, plan_action,
    DECISION_MIN_GAP_S, _proposal)
from src import pmus_l5_persistence_v2 as pv2

SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
RESOLUTION_HORIZON_S = 300.0
M = pv2.load_v2()

def ts(v):
    if isinstance(v, (int, float)): return float(v)
    try: return dt.datetime.fromisoformat(str(v)).timestamp()
    except Exception: return None

def books():
    out = collections.defaultdict(list)
    for venue, f in (("PMUS", "nfl_sunday.jsonl"), ("INTL", "intl_live.jsonl")):
        try: fh = open(SCR + "/" + f)
        except OSError: continue
        for l in fh:
            try: d = json.loads(l)
            except Exception: continue
            if d.get("_marker") or not d.get("live"): continue
            if d.get("bid") is None or d.get("ask") is None: continue
            t = ts(d.get("t"))
            if t is None or d["ask"] <= d["bid"]: continue
            out[(venue, d["slug"])].append(
                {"t": t, "bid": float(d["bid"]), "ask": float(d["ask"]),
                 "bid_qty": d.get("bid_qty"), "ask_qty": d.get("ask_qty"),
                 "league": d.get("league"), "sports_type": d.get("sports_type"),
                 "stratum": d.get("stratum")})
    for k in out: out[k].sort(key=lambda r: r["t"])
    return out

def flow():
    pm = collections.defaultdict(list)
    rows = collections.defaultdict(list)
    try:
        for l in open(SCR + "/flow_pmus.jsonl"):
            try: d = json.loads(l)
            except Exception: continue
            if d.get("_marker"): continue
            t = ts(d.get("t"))
            if t is not None: rows[d["slug"]].append((t, d))
    except OSError: pass
    for s, v in rows.items():
        v.sort(key=lambda r: r[0]); prev = None
        for t, d in v:
            if prev is not None:
                try: dv = float(d["shares_traded_cum"]) - float(prev["shares_traded_cum"])
                except (TypeError, ValueError, KeyError): dv = 0.0
                if dv > 0 and d.get("last_trade_px") is not None:
                    pm[("PMUS", s)].append({"t": t, "px": float(d["last_trade_px"]),
                                            "size": dv})
            prev = d
    try:
        for l in open(SCR + "/flow_intl.jsonl"):
            try: d = json.loads(l)
            except Exception: continue
            if d.get("_marker"): continue
            t = ts(d.get("t")) or ts(d.get("observed_t"))
            try: px = float(d["price"]); sz = float(d["size"])
            except (TypeError, ValueError, KeyError): continue
            if t is None: continue
            pm[("INTL", d["slug"])].append({"t": t, "px": px, "size": sz})
    except OSError: pass
    for k in pm: pm[k].sort(key=lambda r: r["t"])
    return pm

def compatible(prints, t0, t1, px, side_yes):
    lim = px if side_yes else (1.0 - px)
    return [p for p in prints if t0 < p["t"] <= t1
            and ((p["px"] <= lim + 1e-9) if side_yes else (p["px"] >= lim - 1e-9))]

def replay(venue, slug, se, prints):
    inv = Inventory(); last_rel = ""; t_prev = 0.0
    chains = []; cur = None
    for r in se:
        t = r["t"]
        if t - t_prev < DECISION_MIN_GAP_S: continue
        t_prev = t
        bk = {"bid": D(str(r["bid"])), "ask": D(str(r["ask"])), "t": t}
        prop = _proposal(M, float(r["bid"]), inv, last_rel)
        if prop.get("off_distribution"): continue
        rel = prop["recommended_relation"]
        if cur is not None and rel == "complete":
            cur["s2_policy_recommended_complete"] = True
        plan = plan_action(rel, inv, bk)
        if plan["decision"] != "quote":
            if cur is not None and rel == "complete":
                cur["s4_fee_safe"] = cur.get("s4_fee_safe") or False
                cur["no_legal_reason"] = plan.get("reason")
            continue
        px = float(plan["_px"]); side_yes = plan["_side_yes"]; rl = plan["relation"]
        depth_ahead = (r["bid_qty"] if side_yes else r["ask_qty"]) or 0.0
        comp = compatible(prints, t, t + RESOLUTION_HORIZON_S, px, side_yes)
        cum = 0.0; qa = False
        for p in comp:
            cum += p["size"]
            if cum > float(depth_ahead): qa = True; break
        reached = any((b["ask"] <= px if side_yes else b["bid"] >= (1.0 - px))
                      for b in se if t < b["t"] <= t + RESOLUTION_HORIZON_S)
        if cur is not None and rl == "complete":
            cur["s3_complement_quoted"] = True
            cur["s4_fee_safe"] = True
            cur["s5_reached"] = cur.get("s5_reached") or reached
            cur["s6_compatible_flow"] = cur.get("s6_compatible_flow") or bool(comp)
            cur["s7_opp_fill_opt"] = cur.get("s7_opp_fill_opt") or bool(comp)
            cur["s7_opp_fill_qa"] = cur.get("s7_opp_fill_qa") or qa
        if not comp:
            last_rel = rl; continue
        inv.record(side_yes, D(str(px)))
        last_rel = rl
        if cur is not None and inv.residual == 0:
            cur["s8_matched"] = inv.matched > cur["_m0"]
            cur["end_t"] = se[-1]["t"]; chains.append(cur); cur = None
        if rl == "open" and cur is None:
            cur = {"venue": venue, "slug": slug, "league": r["league"],
                   "sports_type": r["sports_type"], "stratum": r.get("stratum"),
                   "t_first": t, "_m0": inv.matched,
                   "s1_first_fill": True,
                   "s2_policy_recommended_complete": False,
                   "s3_complement_quoted": False, "s4_fee_safe": False,
                   "s5_reached": False, "s6_compatible_flow": False,
                   "s7_opp_fill_opt": False, "s7_opp_fill_qa": False,
                   "s8_matched": False, "qa_first_fill": qa}
    if cur is not None:
        cur["s8_matched"] = inv.matched > cur["_m0"] and inv.residual == 0
        cur["end_t"] = se[-1]["t"]; chains.append(cur)
    return chains

bk = books(); fl = flow()
allc = collections.defaultdict(list)
for (venue, slug), se in bk.items():
    if len(se) < 8: continue
    for c in replay(venue, slug, se, fl.get((venue, slug), [])):
        allc[venue].append(c)

STAGES = [("s1_first_fill", "FIRST FILL"),
          ("s2_policy_recommended_complete", "POLICY COMPLETE RECOMMENDATION"),
          ("s3_complement_quoted", "COMPLEMENT QUOTED"),
          ("s4_fee_safe", "FEE-SAFE"),
          ("s5_reached", "REACHED"),
          ("s6_compatible_flow", "COMPATIBLE FLOW"),
          ("s7_opp_fill_opt", "HYP. OPPOSITE FILL (optimistic)"),
          ("s7_opp_fill_qa", "HYP. OPPOSITE FILL (queue-aware)"),
          ("s8_matched", "MATCHED")]
out = {"labels": "SHADOW/HYPOTHETICAL. Nothing submitted, filled, settled or "
                 "realized. Venues never pooled.",
       "model_sha16": M["_sha256_16"],
       "as_of_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "complete_eligible_definition":
         "after the first fill and while imbalanced, v2 returned "
         "recommended_relation == 'complete' at least once"}
for venue in ("PMUS", "INTL"):
    cs = allc.get(venue) or []
    n = len(cs)
    if not n: continue
    elig = [c for c in cs if c["s2_policy_recommended_complete"]]
    m_opt = sum(1 for c in elig if c["s8_matched"])
    print("=" * 74)
    print("%s  -- %s" % (venue, "PRIMARY EXECUTABLE TRANSFER TARGET" if venue == "PMUS"
                         else "REFERENCE / FIDELITY ONLY, not executable"))
    print("=" * 74)
    print("  %-38s %6s %8s" % ("stage", "n", "per 100"))
    for k, lab in STAGES:
        c = sum(1 for x in cs if x.get(k))
        print("  %-38s %6d %8.1f" % (lab, c, 100.0 * c / n))
    print()
    print("  P(COMPLETE-eligible | first fill) = %d/%d = %.1f%%"
          % (len(elig), n, 100.0 * len(elig) / n))
    if elig:
        print("  P(matched | COMPLETE-eligible)    = %d/%d = %.1f%%"
              % (m_opt, len(elig), 100.0 * m_opt / len(elig)))
    else:
        print("  P(matched | COMPLETE-eligible)    = UNDEFINED (0 eligible chains)")
    print("  CHECKPOINT: %d of 25 COMPLETE-eligible chains%s"
          % (len(elig), "" if len(elig) >= 25 else "  -- BELOW THRESHOLD, no conditional conclusion"))
    print("  by league:", dict(collections.Counter(c["league"] for c in cs).most_common()))
    print("  diagnostic-stratum chains:",
          sum(1 for c in cs if c.get("stratum") == "p10_25_diagnostic"))
    print()
    out[venue] = {"first_fills": n, "complete_eligible": len(elig),
                  "p_complete_eligible_given_first_fill": round(100.0 * len(elig) / n, 1),
                  "p_matched_given_complete_eligible":
                    round(100.0 * m_opt / len(elig), 1) if elig else None,
                  "stage_counts": {lab: sum(1 for x in cs if x.get(k))
                                   for k, lab in STAGES},
                  "checkpoint_25_met": len(elig) >= 25}
json.dump({**out, "chains": {v: allc[v] for v in allc}},
          open(SCR + "/funnel_report.json", "w"), indent=1, default=str)
