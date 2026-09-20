"""P0-B: audit the p10-25c COMPLETE rule against ACTUAL L5 history.

Question: is "v2 only recommends COMPLETE in p10-25c" (A) a real L5 behavioral
rule, or (B) a model/discretization/training-distribution artifact?

Method: replicate build_model's OWN decision loop exactly -- same
state_key(last_price, tte, residual), same _relation(prev_residual, outcome,
side), same per-window ordering, same SYSTEMATIC_REGIME_EPOCH cohort -- then
compare the EMPIRICAL next-action frequencies against what v2 recommends for
the same state. Nothing here modifies v2.

Source is the canonical L5 sequence corpus only. No Gemini and no PMUS outcome
is used anywhere in this file.
"""
import sys, csv, gzip, json, collections, statistics as st
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src.polymarket_l5.action_model import (
    state_key, _relation, SYSTEMATIC_REGIME_EPOCH, PRICE_BANDS)
from src import pmus_l5_persistence_v2 as pv2

CSV = "/home/user/kalshi-arb-bot/analysis/l5/l5_canonical_sequence.csv.gz"
M = pv2.load_v2()
OUT = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad/p10_25_audit.json"

def windows():
    with gzip.open(CSV, "rt", newline="", encoding="utf-8") as fh:
        cur, wid = [], None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur

def v2_rec(state, last_rel):
    try:
        d = pv2.v2_dist(M, state, last_rel or "open", "q<5")
    except Exception:
        return "ERROR", True
    if d.get("backed_off"):
        return "OFF_DISTRIBUTION", True
    try:
        p = pv2.proposal(M, state, last_rel or "open", "q<5", -1.0, "0.5")
        return p["recommended_relation"], False
    except Exception:
        return "ERROR", True

rows = []           # one record per imbalanced DECISION
for w in windows():
    if not w:
        continue
    try:
        if int(float(w[0]["timestamp"])) < SYSTEMATIC_REGIME_EPOCH:
            continue
    except (TypeError, ValueError):
        continue
    w.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
    last_rel = ""
    run = 0
    for i, (prev, nxt) in enumerate(zip(w, w[1:])):
        try:
            last_price = float(prev["price"])
            residual = float(prev["residual_qty_signed"] or 0.0)
            tte = float(nxt["seconds_to_settlement"])
            t_prev = float(prev["timestamp"])
        except (TypeError, ValueError):
            continue
        rel = _relation(residual, nxt["outcome"], nxt["side"])
        sk = state_key(last_price, tte, residual)
        if abs(residual) >= 1.0 and rel in ("extend", "complete"):
            # time until the FIRST opposite-side acquisition from here
            t_opp = None
            for f in w[i + 1:]:
                try:
                    r2 = _relation(residual, f["outcome"], f["side"])
                except Exception:
                    continue
                if r2 == "complete":
                    try:
                        t_opp = float(f["timestamp"]) - t_prev
                    except (TypeError, ValueError):
                        pass
                    break
            rows.append({"state": sk, "price_b": sk.split("|")[0],
                         "tte_b": sk.split("|")[1], "imb_b": sk.split("|")[2],
                         "last_rel": last_rel or "open", "run": run,
                         "action": rel, "t_opp": t_opp})
        run = run + 1 if rel == last_rel else 1
        last_rel = rel

print("imbalanced decisions in the systematic-regime cohort: %d" % len(rows))

def summarise(group_fn, label, min_n=30):
    g = collections.defaultdict(list)
    for r in rows:
        g[group_fn(r)].append(r)
    out = []
    for k, v in g.items():
        n = len(v)
        if n < min_n:
            continue
        comp = sum(1 for r in v if r["action"] == "complete")
        rec, oos = v2_rec(v[0]["state"], v[0]["last_rel"])
        oos_n = sum(1 for r in v if v2_rec(r["state"], r["last_rel"])[1])
        t = [r["t_opp"] for r in v if r["t_opp"] is not None]
        out.append({"key": k, "n": n,
                    "actual_complete_pct": round(100.0 * comp / n, 1),
                    "actual_extend_pct": round(100.0 * (n - comp) / n, 1),
                    "v2_action": rec,
                    "oos_pct": round(100.0 * oos_n / n, 1),
                    "median_t_to_opposite_s": round(st.median(t), 1) if t else None,
                    "opp_within_30s": round(100.0 * sum(1 for x in t if x <= 30) / n, 1),
                    "opp_within_60s": round(100.0 * sum(1 for x in t if x <= 60) / n, 1),
                    "opp_within_120s": round(100.0 * sum(1 for x in t if x <= 120) / n, 1),
                    "opp_within_300s": round(100.0 * sum(1 for x in t if x <= 300) / n, 1)})
    out.sort(key=lambda r: -r["n"])
    return out

by_price = summarise(lambda r: r["price_b"], "price")
print()
print("=" * 118)
print("ACTUAL L5 BEHAVIOUR BY PRICE BUCKET (imbalanced states only, "
      "systematic-regime cohort)")
print("=" * 118)
H = ("%-12s %7s %10s %9s %-18s %6s %8s %7s %7s %7s %7s"
     % ("price", "n", "COMPLETE%", "EXTEND%", "v2 says", "OOS%",
        "medT_opp", "<=30s", "<=60s", "<=120s", "<=300s"))
print(H); print("-" * len(H))
order = [l for _, l in PRICE_BANDS]
for b in order:
    r = next((x for x in by_price if x["key"] == b), None)
    if not r:
        print("%-12s %7s  (no cohort at n>=30)" % (b, "-")); continue
    print("%-12s %7d %10.1f %9.1f %-18s %6.1f %8s %7.1f %7.1f %7.1f %7.1f"
          % (r["key"], r["n"], r["actual_complete_pct"], r["actual_extend_pct"],
             r["v2_action"], r["oos_pct"],
             ("%.0f" % r["median_t_to_opposite_s"])
             if r["median_t_to_opposite_s"] is not None else "-",
             r["opp_within_30s"], r["opp_within_60s"],
             r["opp_within_120s"], r["opp_within_300s"]))

by_full = summarise(lambda r: (r["price_b"], r["last_rel"], r["imb_b"],
                               r["tte_b"]), "full")
json.dump({"n_decisions": len(rows), "by_price": by_price,
           "by_price_lastrel_imb_tte": by_full,
           "model_sha16": M["_sha256_16"]},
          open(OUT, "w"), indent=1, default=str)
print()
print("full cross-tab cells at n>=30: %d  (written to p10_25_audit.json)"
      % len(by_full))
