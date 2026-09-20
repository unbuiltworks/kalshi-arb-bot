"""A/B fidelity diagnostic: does PMUS fail because the MARKET cannot recycle
L5 inventory, or because the adapted 3-unit RISK WRAPPER terminates the L5
sequence before recycling normally occurs?

Both cohorts run on the EXACT SAME market observations, the same frozen
l5_action_model_v2.json, the same state/action logic, the same prices, the same
timing and the same fill evidence.

  A  SAFE_TRANSFER_CONTROL   MAX_RESIDUAL_UNITS = 3, absolutely unchanged.
  B  FIDELITY_DIAGNOSTIC     identical except the sequence is not terminated
                             merely because the adapted 3-unit cap is reached.

HOW B DIFFERS, EXACTLY. `plan_action` refuses open/extend with
reason="residual_cap:N>=3" by reading the module global MAX_RESIDUAL_UNITS.
Cohort B rebinds THAT ONE CONSTANT and nothing else, so the policy, the model,
the state key, the complement pricing and the fill evidence are byte-identical
between cohorts. Reimplementing plan_action would have made B a different
policy; rebinding one constant makes it the same policy under a different risk
envelope, which is the question asked. The constant is asserted before and
restored after every pass.

NO LIMIT IS TUNED. B is run UNCAPPED, and the residual trajectory is reported
against L5's OWN historical inventory bins (imb1-10 4.5%, imb10-50 19.9%,
imb>50 75.6%). Intermediate caps of 10 and 50 are reported only because they
are those bin boundaries -- they are a diagnostic frame, never a proposal.

B CAN NEVER SUBMIT AN ORDER AND CAN NEVER BECOME AN EXECUTION CONFIGURATION.
This module imports no executor, holds no credential and has no order verb; it
writes a JSON report and prints a table.

FILL ACCOUNTING IS BRACKETED, NEVER MERGED. Every cohort is replayed twice:
  OPTIMISTIC   a fill whenever a COMPATIBLE PRINT exists at or through the
               resting price. Upper bound. Queue position UNPROVEN.
  QUEUE_AWARE  a fill only once cumulative compatible volume EXCEEDS the depth
               resting at our price when we joined. Lower bound.
These are separate sequential replays because the inventory path itself
diverges. OPTIMISTIC_MATCHED and QUEUE_AWARE_MATCHED are reported separately
and a PMUS queue-aware number is never set against an International optimistic
one.

PROVENANCE IS NEVER BLENDED. PMUS flow is CAUSAL INFERRED flow from
sharesTraded deltas and is never called a real print. International flow is
REAL PRINTS with known direction.
"""
import sys, json, collections, time, statistics as stt
import datetime as dt
from decimal import Decimal as D
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import pmus_sports_l5_decision as dec
from src import pmus_l5_persistence_v2 as pv2

SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
HORIZON = 300.0
M = pv2.load_v2()
BASELINE_CAP = dec.MAX_RESIDUAL_UNITS
assert BASELINE_CAP == 3, "control cohort must be the shipped cap"

# PMUS maker fee is ZERO. The maker REBATE is credited by the venue but is
# NEVER counted here and never merged into modeled economics. International
# maker fee is likewise taken as zero. No rebate or reward is assumed anywhere.
MAKER_FEE = D("0")

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
            if d.get("_marker") or d.get("kind") or not d.get("slug"): continue
            if not d.get("live"): continue
            if d.get("bid") is None or d.get("ask") is None: continue
            t = ts(d.get("t"))
            if t is None or d["ask"] <= d["bid"]: continue
            out[(venue, d["slug"])].append(
                {"t": t, "bid": float(d["bid"]), "ask": float(d["ask"]),
                 "bid_qty": d.get("bid_qty"), "ask_qty": d.get("ask_qty"),
                 "league": d.get("league"), "sports_type": d.get("sports_type")})
    for k in out: out[k].sort(key=lambda r: r["t"])
    return out

def flow():
    pm = collections.defaultdict(list)
    rows = collections.defaultdict(list)
    try:
        for l in open(SCR + "/flow_pmus.jsonl"):
            try: d = json.loads(l)
            except Exception: continue
            if d.get("_marker") or d.get("kind") or not d.get("slug"): continue
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
                        "size": dv, "prov": "CAUSAL_INFERRED_NOT_A_PRINT"})
            prev = d
    try:
        for l in open(SCR + "/flow_intl.jsonl"):
            try: d = json.loads(l)
            except Exception: continue
            if d.get("_marker") or d.get("kind") or not d.get("slug"): continue
            t = ts(d.get("t")) or ts(d.get("observed_t"))
            try: px = float(d["price"]); sz = float(d["size"])
            except (TypeError, ValueError, KeyError): continue
            if t is None: continue
            pm[("INTL", d["slug"])].append({"t": t, "px": px, "size": sz,
                "prov": "REAL_PRINT_DIRECTION_KNOWN"})
    except OSError: pass
    for k in pm: pm[k].sort(key=lambda r: r["t"])
    return pm

def compatible(prints, t0, t1, px, side_yes):
    lim = px if side_yes else (1.0 - px)
    return [p for p in prints if t0 < p["t"] <= t1
            and ((p["px"] <= lim + 1e-9) if side_yes else (p["px"] >= lim - 1e-9))]

def replay(venue, slug, se, prints, fill_rule):
    """One self-consistent sequential replay under the CURRENT module cap."""
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
        if cur is not None and rel == "complete":
            cur["eligible"] = True
        plan = dec.plan_action(rel, inv, bk)
        if plan["decision"] != "quote":
            if cur is not None and str(plan.get("reason", "")).startswith("residual_cap"):
                cur["hit_cap"] = True
            continue
        px = float(plan["_px"]); side_yes = plan["_side_yes"]; rl = plan["relation"]
        depth_ahead = float((r["bid_qty"] if side_yes else r["ask_qty"]) or 0.0)
        comp = compatible(prints, t, t + HORIZON, px, side_yes)
        cum = 0.0; qa_hit = False
        for p in comp:
            cum += p["size"]
            if cum > depth_ahead: qa_hit = True; break
        if cur is not None and rl == "complete":
            cur["complement_quoted"] = True
            cur["compatible_flow"] = cur.get("compatible_flow") or bool(comp)
        filled = bool(comp) if fill_rule == "OPTIMISTIC" else qa_hit
        if not filled:
            last_rel = rl; continue
        inv.record(side_yes, D(str(px)))
        last_rel = rl
        if cur is not None:
            if rl == "extend": cur["extends"] += 1
            if rl == "complete":
                cur["opposite_fill"] = True
                if cur["t_opp"] is None: cur["t_opp"] = t - cur["t_first"]
            cur["max_abs_residual"] = max(cur["max_abs_residual"], abs(inv.residual))
            if side_yes: cur["yes_cost"] += px; cur["yes_n"] += 1
            else: cur["no_cost"] += px; cur["no_n"] += 1
        if cur is not None and inv.residual == 0 and cur["yes_n"] and cur["no_n"]:
            m = min(cur["yes_n"], cur["no_n"])
            cur["matched"] = True
            cur["pair_basis"] = round((cur["yes_cost"] / cur["yes_n"]
                                       + cur["no_cost"] / cur["no_n"]), 4)
            cur["end_t"] = se[-1]["t"]; chains.append(cur); cur = None
            continue
        if rl == "open" and cur is None:
            cur = {"venue": venue, "slug": slug, "league": r["league"],
                   "sports_type": r["sports_type"], "t_first": t,
                   "eligible": False, "complement_quoted": False,
                   "compatible_flow": False, "opposite_fill": False,
                   "matched": False, "hit_cap": False, "extends": 0,
                   "t_opp": None, "max_abs_residual": abs(inv.residual),
                   "yes_cost": 0.0, "yes_n": 0, "no_cost": 0.0, "no_n": 0,
                   "pair_basis": None}
    if cur is not None:
        cur["end_t"] = se[-1]["t"]; chains.append(cur)
    return chains

BK = books(); FL = flow()

def run(cap, fill_rule):
    dec.MAX_RESIDUAL_UNITS = cap
    assert dec.MAX_RESIDUAL_UNITS == cap
    out = collections.defaultdict(list)
    for (venue, slug), se in BK.items():
        if len(se) < 8: continue
        for c in replay(venue, slug, se, FL.get((venue, slug), []), fill_rule):
            out[venue].append(c)
    dec.MAX_RESIDUAL_UNITS = BASELINE_CAP
    assert dec.MAX_RESIDUAL_UNITS == 3
    return out

def stats(cs):
    n = len(cs)
    if not n: return None
    elig = [c for c in cs if c["eligible"]]
    mt = [c for c in cs if c["matched"]]
    me = [c for c in elig if c["matched"]]
    ext = [c["extends"] for c in mt] or [c["extends"] for c in cs]
    topp = [c["t_opp"] for c in cs if c["t_opp"] is not None]
    pb = [c["pair_basis"] for c in mt if c["pair_basis"] is not None]
    res = [c["max_abs_residual"] for c in cs]
    bins = collections.Counter()
    for x in res:
        bins["imb1-10" if x <= 10 else ("imb10-50" if x <= 50 else "imb>50")] += 1
    return {"first_fills": n, "eligible": len(elig), "matched": len(mt),
            "matched_and_eligible": len(me),
            "p_elig_given_first": round(100.0 * len(elig) / n, 1),
            "p_matched_given_elig": round(100.0 * len(me) / len(elig), 1) if elig else None,
            "p_matched_given_first": round(100.0 * len(mt) / n, 1),
            "median_extends_before_opposite": round(stt.median(ext), 1) if ext else None,
            "median_t_to_opposite_s": round(stt.median(topp), 1) if topp else None,
            "pair_basis_n": len(pb),
            "pair_basis_median": round(stt.median(pb), 4) if pb else None,
            "pct_pair_basis_below_1":
              round(100.0 * sum(1 for x in pb if x < 1.0) / len(pb), 1) if pb else None,
            "modeled_after_fee_edge_per_pair":
              round(1.0 - stt.median(pb), 4) if pb else None,
            "chains_that_hit_cap": sum(1 for c in cs if c["hit_cap"]),
            "max_abs_residual_median": round(stt.median(res), 1),
            "max_abs_residual_max": max(res),
            "residual_trajectory_bins": dict(bins)}

COH = [("A_SAFE_TRANSFER_CONTROL", 3), ("B_FIDELITY_DIAGNOSTIC", 10**9)]
FRAME = [("frame_cap_10_imb1-10_boundary", 10), ("frame_cap_50_imb10-50_boundary", 50)]
report = {"labels": "SHADOW/HYPOTHETICAL. No order exists. Cohort B can never "
                    "submit an order and can never become an execution "
                    "configuration.",
          "model_sha16": M["_sha256_16"],
          "as_of_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "fee_model": "maker fee ZERO both venues; maker rebate credited by "
                       "PMUS but NEVER counted and never merged into modeled "
                       "economics; no reward assumed",
          "provenance": {"PMUS": "CAUSAL INFERRED flow from sharesTraded "
                                 "deltas -- never a real print",
                         "INTL": "REAL PRINTS, direction known"}}
for name, cap in COH + FRAME:
    for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
        res = run(cap, rule)
        for venue in ("PMUS", "INTL"):
            s = stats(res.get(venue) or [])
            if s: report["%s|%s|%s" % (name, venue, rule)] = s
assert dec.MAX_RESIDUAL_UNITS == 3, "control cap must be restored"
json.dump(report, open(SCR + "/cohorts.json", "w"), indent=1, default=str)

for venue in ("PMUS", "INTL"):
    tag = ("PRIMARY EXECUTABLE TRANSFER TARGET" if venue == "PMUS"
           else "REFERENCE / FIDELITY ONLY -- not executable")
    print("=" * 108)
    print("%s  --  %s" % (venue, tag))
    print("   flow provenance: %s" % report["provenance"][venue])
    print("=" * 108)
    H = ("  %-30s %-12s %6s %6s %7s %8s %9s %9s %7s %8s"
         % ("cohort", "fill rule", "1stFll", "elig", "matched", "P(el|1st)",
            "P(mt|el)", "P(mt|1st)", "medEXT", "medTopp"))
    print(H); print("  " + "-" * (len(H) - 2))
    for name, cap in COH + FRAME:
        for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
            k = "%s|%s|%s" % (name, venue, rule)
            s = report.get(k)
            if not s: continue
            print("  %-30s %-12s %6d %6d %7d %8s %9s %9s %7s %8s"
                  % (name[:30], rule, s["first_fills"], s["eligible"],
                     s["matched"], s["p_elig_given_first"],
                     s["p_matched_given_elig"], s["p_matched_given_first"],
                     s["median_extends_before_opposite"],
                     s["median_t_to_opposite_s"]))
    print()
    for name, cap in COH:
        for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
            s = report.get("%s|%s|%s" % (name, venue, rule))
            if not s: continue
            print("  %-30s %-12s residual bins %s  hit_cap=%d  pair_basis med=%s "
                  "(<1: %s%%, n=%d)  modeled edge/pair=%s"
                  % (name[:30], rule, s["residual_trajectory_bins"],
                     s["chains_that_hit_cap"], s["pair_basis_median"],
                     s["pct_pair_basis_below_1"], s["pair_basis_n"],
                     s["modeled_after_fee_edge_per_pair"]))
    print()
