"""SECTION K (live leg): behavioural-share adapter divergence.

For every live decision on the tape, compare

  behavioural residual (what state_key was actually fed)
     = up_shares_behavioral - dn_shares_behavioral, i.e. +/- p50 per FILL

  faithfully scaled executable residual
     = (executable residual in contracts) * (p50 shares per behavioural unit)
                                          / (behavioural_unit contracts)

and bucket both with the model's IMBALANCE_BUCKETS. The dangerous case is
behavioural == 0 while the executable book is NOT flat: that is the one that
flips the lookup into the 100%-OPEN bucket.
"""
import json, collections

BUCKETS = ((1.0, "flat"), (10.0, "imb1-10"), (50.0, "imb10-50"), (1e12, "imb>50"))


def bucket(v):
    v = abs(v)
    for hi, lab in BUCKETS:
        if v < hi:
            return lab
    return BUCKETS[-1][1]


def num(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


rows = sorted((json.loads(l) for l in open("tape.jsonl")), key=lambda r: r["_ts"])
recs = []
for r in rows:
    if r.get("kind") != "session_end":
        continue
    slug = r.get("slug")
    for d in (r.get("decisions") or []):
        lk = d.get("lookup") or {}
        inv = d.get("inventory_before") or {}
        up, dn = num(inv.get("up"), 0.0), num(inv.get("dn"), 0.0)
        if up is None or dn is None:
            continue
        exec_resid = up - dn
        unit = num(d.get("behavioral_unit"))
        p50 = None
        for st in (d.get("size_transformations") or []):
            if st.get("step") == "l5_target_p50_shares":
                p50 = num(st.get("value"))
        shares_b = num(lk.get("residual_shares_behavioral"))
        if shares_b is None:                      # pre-fill / unit fallback
            shares_b = num(lk.get("residual_units"), 0.0)
        if not unit or not p50:
            continue
        faithful = exec_resid * (p50 / unit)
        recs.append({
            "slug": slug, "idx": d.get("decision_idx"), "acted": d.get("acted"),
            "state": d.get("state"), "up": up, "dn": dn,
            "exec_resid": exec_resid, "matched": min(up, dn),
            "shares_b": shares_b, "faithful": faithful,
            "b_bucket": bucket(shares_b), "f_bucket": bucket(faithful),
            "unit": unit, "p50": p50,
        })

n = len(recs)
print(f"live decisions with both scales computable: {n}\n")

agree = [r for r in recs if r["b_bucket"] == r["f_bucket"]]
differ = [r for r in recs if r["b_bucket"] != r["f_bucket"]]
print(f"BUCKET AGREEMENT      {len(agree):4} / {n}  ({len(agree)/n:.1%})")
print(f"BUCKET DIVERGENCE     {len(differ):4} / {n}  ({len(differ)/n:.1%})")

danger = [r for r in recs
          if abs(r["shares_b"]) < 1.0 and abs(r["exec_resid"]) > 1e-9]
print(f"\nDANGEROUS: behavioural reads FLAT while executable book is NOT flat")
print(f"   {len(danger):4} / {n}  ({len(danger)/n:.1%})")
truly_zero = [r for r in danger if abs(r["shares_b"]) < 1e-9]
print(f"   of which behavioural is EXACTLY 0.0: {len(truly_zero)}")

print("\n   by market:")
for slug, c in collections.Counter(r["slug"] for r in danger).most_common():
    tot = sum(1 for r in recs if r["slug"] == slug)
    print(f"      {slug:42} {c:3}/{tot:3}")

print("\n   what the clone DID in those decisions:")
for a, c in collections.Counter(r["acted"] for r in danger).most_common():
    print(f"      {a:10} {c:3}  ({c/len(danger):.1%})")

print("\n   what the FAITHFUL bucket would have been instead:")
for b, c in collections.Counter(r["f_bucket"] for r in danger).most_common():
    print(f"      {b:10} {c:3}")

print("\n   executable residual in those decisions (contracts):")
er = sorted(abs(r["exec_resid"]) for r in danger)
if er:
    print(f"      min {er[0]:.2f}  p50 {er[len(er)//2]:.2f}  max {er[-1]:.2f}")
    mt = sorted(r["matched"] for r in danger)
    print(f"      matched alongside: min {mt[0]:.2f}  p50 {mt[len(mt)//2]:.2f}  max {mt[-1]:.2f}")

print("\n   worked examples:")
seen = set()
for r in danger:
    k = (r["slug"], round(r["exec_resid"], 2))
    if k in seen:
        continue
    seen.add(k)
    print(f"      {r['slug'][:34]:34} #{r['idx']:3} acted={r['acted']:8} "
          f"YES {r['up']:.2f} NO {r['dn']:.2f} matched {r['matched']:.2f} "
          f"resid {r['exec_resid']:+.2f}c -> behav {r['shares_b']:+.1f}sh ({r['b_bucket']}) "
          f"vs faithful {r['faithful']:+.1f}sh ({r['f_bucket']})")
