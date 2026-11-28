import json, collections, statistics as S
from classify import ORDER
ESP = ("cs2", "valorant", "lol", "dota2")
rows = [json.loads(l) for l in open("paying_sample.jsonl")]
def med(v, nd=5):
    v = [x for x in v if x is not None]; return None if not v else round(S.median(v), nd)
def rng(v, nd=4):
    v = [x for x in v if x is not None]; return "-" if not v else "%s (%s..%s)" % (round(S.median(v), nd), round(min(v), nd), round(max(v), nd))
print("rows", len(rows), "errors", sum(1 for r in rows if r.get("err")))
print("| type | n | live | est rows (two-sided) | period mix | spread ticks | touch qty bid | touch qty ask | q1 touch $/h | q1 touch $/h/$ | q5 touch $/h/$ | q1 behind $/h/$ | q5 behind $/h/$ | q5 touch share | M_event med | q5 touch $/h/$ event-shared | q5 touch $/h/$ programme-shared(window) | admissible(band,spread<=6t,touch>=10) |")
allr = []
for t in ORDER:
    rs = [r for r in rows if r["type"] == t and not r.get("err")]
    if not rs: continue
    est = [r for r in rs if r.get("q1_d0")]
    g = lambda k, f: [r[k][f] for r in est if r.get(k)]
    adm = [r for r in est if 0.25 <= r["bid"] <= 0.75 and r["spread_ticks"] <= 6 and r["bid_qty"] >= 10 and r["ask_qty"] >= 10]
    print("|", t, "|", len(rs), "|", sum(r["live"] for r in rs), "|", len(est), "|", dict(collections.Counter(r["paying"]["period"] for r in rs if r.get("paying"))), "|", rng([r["spread_ticks"] for r in est], 1), "|", rng([r["bid_qty"] for r in est], 1), "|", rng([r["ask_qty"] for r in est], 1), "|",
          rng(g("q1_d0", "usd_h")), "|", rng(g("q1_d0", "usd_h_per_usd")), "|", rng(g("q5_d0", "usd_h_per_usd")), "|", med(g("q1_d1", "usd_h_per_usd")), "|", med(g("q5_d1", "usd_h_per_usd")), "|", med(g("q5_d0", "share")), "|",
          med([r["paying"]["M_event"] for r in est]), "|", med([r["q5_d0"]["usd_h_per_usd"] / r["paying"]["M_event"] for r in est if r.get("q5_d0")]), "|", med([r["q5_d0"]["usd_h_per_usd"] / r["paying"]["M_program"] for r in est if r.get("q5_d0")], 6), "|", len(adm), "|")
    for r in est: allr.append(r)
def top(title, key, flt):
    print("\n##", title)
    print("| slug | type | live | period | pool | target | bid/ask | touch b/a | share | $/h (per-mkt reading) | capital $ | $/h/$ | M_event | $/h/$ event-shared | M_prog | $/h/$ prog-shared |")
    xs = sorted([r for r in allr if r.get(key) and flt(r)], key=lambda r: -r[key]["usd_h_per_usd"])[:15]
    for r in xs:
        p = r["paying"]; e = r[key]
        print("|", r["slug"], "|", r["type"], "|", r["live"], "|", p["period"], "|", p["pool"], "|", p["targetSize"], "|", "%s/%s" % (r["bid"], r["ask"]), "|", "%.1f/%.1f" % (r["bid_qty"], r["ask_qty"]), "|", round(e["share"], 4), "|", round(e["usd_h"], 4), "|", e["cap"], "|", round(e["usd_h_per_usd"], 4), "|", p["M_event"], "|", round(e["usd_h_per_usd"] / p["M_event"], 5), "|", p["M_program"], "|", round(e["usd_h_per_usd"] / p["M_program"], 6), "|")
noesp = lambda r: r["league"] not in ESP
top("TOP 15 q=5 at touch, all sampled (ex cricket, ex esports)", "q5_d0", noesp)
top("TOP 15 q=1 at touch, all sampled (ex cricket, ex esports)", "q1_d0", noesp)
adm = lambda r: noesp(r) and 0.25 <= r["bid"] <= 0.75 and r["spread_ticks"] <= 6 and r["bid_qty"] >= 10 and r["ask_qty"] >= 10
top("TOP 15 q=5 at touch, controller-admissible book (band, spread<=6 ticks, both touches >=10)", "q5_d0", adm)
top("TOP 15 q=5 one tick behind, controller-admissible book", "q5_d1", adm)
print("\n## ACTIVITY (paying sample)\n| type | n | sharesTraded | notionalTraded $ | openInterest | zero volume |")
def f(x):
    try: return float(x)
    except Exception: return 0.0
for t in ORDER:
    rs = [r for r in rows if r["type"] == t and r.get("stats") is not None]
    if rs: print("|", t, "|", len(rs), "|", rng([f(r["stats"].get("sharesTraded")) for r in rs], 1), "|", rng([f(r["stats"].get("notionalTraded")) for r in rs], 1), "|", rng([f(r["stats"].get("openInterest")) for r in rs], 1), "|", sum(1 for r in rs if f(r["stats"].get("sharesTraded")) == 0), "|")
