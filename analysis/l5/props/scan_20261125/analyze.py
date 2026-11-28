import json, collections, statistics as S
from classify import ORDER
rows = [json.loads(l) for l in open("book_sample.jsonl")]
def med(v):
    v = [x for x in v if x is not None]; return None if not v else round(S.median(v), 5)
def rng(v):
    v = [x for x in v if x is not None]; return "-" if not v else "%s (%s..%s)" % (round(S.median(v), 4), round(min(v), 4), round(max(v), 4))
def f(x):
    try: return float(x)
    except Exception: return None
out = []
print("errors", sum(1 for r in rows if r.get("err")), "rows", len(rows))
print("\n## BOOK STATE\n| type | n | live | two-sided | empty both | bid | spread ticks med (min..max) | bid touch qty | ask touch qty | bid depth<=3t | ask depth<=3t | in band+spread<=6t | touch>=10 both |")
for t in ORDER:
    rs = [r for r in rows if r["type"] == t and not r.get("err")]
    if not rs: continue
    for sub, rr in (("all", rs), ("main", [r for r in rs if r["role"] == "main"]), ("ladder", [r for r in rs if r["role"] == "ladder"])):
        if not rr: continue
        two = [r for r in rr if r.get("two_sided")]
        empty = sum(1 for r in rr if not r.get("two_sided") and r.get("bid") is None and r.get("ask") is None)
        ok = sum(1 for r in two if 0.25 <= r["bid"] <= 0.75 and r["spread_ticks"] <= 6)
        t10 = sum(1 for r in two if r["bid_qty"] >= 10 and r["ask_qty"] >= 10)
        print("|", t, sub, "|", len(rr), "|", sum(r["live"] for r in rr), "|", len(two), "|", empty, "|", rng([r["bid"] for r in two]), "|", rng([r["spread_ticks"] for r in two]), "|",
              rng([r["bid_qty"] for r in two]), "|", rng([r["ask_qty"] for r in two]), "|", rng([r["bid_depth3"] for r in two]), "|", rng([r["ask_depth3"] for r in two]), "|", ok, "|", t10, "|")
print("\n## ACTIVITY\n| type | n | sharesTraded med (min..max) | zero-volume | openInterest med (min..max) | has lastTradePx |")
for t in ORDER:
    rs = [r for r in rows if r["type"] == t and not r.get("err")]
    if not rs: continue
    st = [f(r["bbo"].get("sharesTraded")) or 0.0 for r in rs]; oi = [f(r["bbo"].get("openInterest")) or 0.0 for r in rs]
    print("|", t, "|", len(rs), "|", rng(st), "|", sum(1 for x in st if x == 0), "|", rng(oi), "|", sum(1 for r in rs if r["bbo"].get("lastTradePx") is not None), "|")
    for sub in ("live", "pre"):
        rr = [r for r in rs if bool(r["live"]) == (sub == "live")]
        if rr: print("|  ", sub, "|", len(rr), "|", rng([f(r["bbo"].get("sharesTraded")) or 0.0 for r in rr]), "| | |")
print("\n## REWARD (THEORETICAL)\n| type | n | has programme | paying now | period mix | pool $ med | pool $/h med (per-market reading) | M_event med | est rows | q1 touch $/h med | q1 touch $/h/$ med | q5 touch $/h/$ med | q1 1-behind $/h/$ med | q5 1-behind $/h/$ med | share q5 touch med | nonzero q1 touch |")
allr = []
for t in ORDER:
    rs = [r for r in rows if r["type"] == t and not r.get("err")]
    if not rs: continue
    pay = [r for r in rs if r.get("paying")]
    est = [r for r in pay if r.get("q1_d0")]
    g = lambda k, fld: [r[k][fld] for r in est if r.get(k)]
    print("|", t, "|", len(rs), "|", sum(1 for r in rs if r.get("programme")), "|", len(pay), "|", dict(collections.Counter(r["paying"]["period"] for r in pay)), "|", med([r["paying"]["pool"] for r in pay]), "|",
          med([r["paying"]["pool_rate_usd_s"] * 3600 for r in pay]), "|", med([r["paying"]["M_event"] for r in pay]), "|", len(est), "|", med(g("q1_d0", "usd_h")), "|", med(g("q1_d0", "usd_h_per_usd")), "|", med(g("q5_d0", "usd_h_per_usd")), "|",
          med(g("q1_d1", "usd_h_per_usd")), "|", med(g("q5_d1", "usd_h_per_usd")), "|", med(g("q5_d0", "share")), "|", sum(1 for x in g("q1_d0", "usd_h") if x > 0), "|")
    for r in est:
        for k in ("q1_d0", "q5_d0", "q1_d1", "q5_d1"):
            if r.get(k): allr.append((r[k]["usd_h_per_usd"], k, r))
for k in ("q1_d0", "q5_d0", "q5_d1"):
    print("\n## TOP 15 by THEORETICAL $/h per $ --", k, "(per-market pool reading; /M_event = event-shared reading)")
    print("| slug | type | live | period | pool | target | bid/ask | touch qty b/a | share | $/h | capital | $/h/$ | M_event | $/h/$ if pool shared |")
    for v, kk, r in sorted([x for x in allr if x[1] == k and "cricket" not in str(x[2]["smt"])], key=lambda x: -x[0])[:15]:
        p = r["paying"]; e = r[k]
        print("|", r["slug"], "|", r["type"], "|", r["live"], "|", p["period"], "|", p["pool"], "|", p["targetSize"], "|", "%s/%s" % (r["bid"], r["ask"]), "|", "%s/%s" % (r["bid_qty"], r["ask_qty"]), "|", e["share"], "|", round(e["usd_h"], 4), "|", e["cap"], "|", round(v, 4), "|", p["M_event"], "|", round(v / p["M_event"], 5), "|")
