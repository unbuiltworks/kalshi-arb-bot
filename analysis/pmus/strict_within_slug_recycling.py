"""STRICT WITHIN-SLUG recycling from the PMUS_SURFACE per-market report.

The shipped recycling() pools fills across every surface, so its
OPPOSITE_RECYCLING can pair a BUY_YES fill in one market with a BUY_NO fill in
a different market. Nothing here crosses a slug boundary: every quantity is
computed inside one market's own MarketSurface state.

SHADOW / MODELED. EVIDENCED_FILL is a queue-ahead model of an unpublished
queue, never venue confirmation. Nothing here is submitted, filled, settled or
realized."""
import json, collections
from decimal import Decimal as D

p = json.load(open("progress2.json"))
pm = p["per_market"]

def league_of(slug):
    parts = slug.split("-")
    return parts[1] if len(parts) > 1 else "?"

rows = []
for slug, m in pm.items():
    lad = m["ladder"]; g = lambda k: int(lad.get(k, 0))
    mt, rs = D(m["matched"]), D(m["residual"])
    yf, nf = g("BUY_YES|EVIDENCED_FILL"), g("BUY_NO|EVIDENCED_FILL")
    rows.append(dict(
        slug=slug, lg=league_of(slug),
        YP=g("BUY_YES|PLACED"), NP=g("BUY_NO|PLACED"),
        YBR=g("BUY_YES|BOOK_REACHED"), NBR=g("BUY_NO|BOOK_REACHED"),
        YTF=g("BUY_YES|TRADE_FLOW"), NTF=g("BUY_NO|TRADE_FLOW"),
        YF=yf, NF=nf, fills=m["n_fills"], matched=mt, resid=rs,
        obs=m["observations"], tr=m["tape_readable"], tu=m["tape_unreadable"],
        two_sided=(yf > 0 and nf > 0)))

tot = lambda k: sum(r[k] for r in rows)
F = tot("YF") + tot("NF")
strict_two_sided_fills = sum(r["YF"] + r["NF"] for r in rows if r["two_sided"])
strict_matched = sum(r["matched"] for r in rows)

print("=== STRICT WITHIN-SLUG RECYCLING (no cross-market pairing) ===")
print("surfaces resident            %d" % len(rows))
print("markets with any fill        %d" % sum(1 for r in rows if r["fills"] > 0))
print("markets with BOTH sides      %d   <- the only same-market recyclers" %
      sum(1 for r in rows if r["two_sided"]))
print("markets with matched > 0     %d" % sum(1 for r in rows if r["matched"] > 0))
print("total evidenced fills        %d" % F)
print("fills inside a two-sided mkt %d  (%.1f%% of fills)" %
      (strict_two_sided_fills, 100 * strict_two_sided_fills / F if F else 0))
print("same-market matched units    %s" % strict_matched)
print()
print("POOLED statistic from the same row, NOT COMPARABLE and not used:")
print("  n_opposite_within_300s = %d of %d (%.1f%%) -- pairs fills ACROSS markets"
      % (p["recycling"]["n_opposite_within_300s"], p["recycling"]["n_fills"],
         100 * p["recycling"]["p_opposite_within_300s"]))
print()
print("=== FUNNEL PER MARKET (two-sided recyclers first) ===")
hdr = "%-42s %-7s %5s %5s %5s %5s %5s %6s %6s"
print(hdr % ("slug", "league", "PLCD", "FLOW", "FILL", "resid", "obs",
             "MATCH", "flow%"))
for r in sorted(rows, key=lambda r: (-float(r["matched"]), -r["fills"])):
    if r["fills"] == 0: continue
    pl, fl = r["YP"] + r["NP"], r["YTF"] + r["NTF"]
    print(hdr % (r["slug"], r["lg"], pl, fl, r["YF"] + r["NF"],
                 str(r["resid"]), r["obs"], str(r["matched"]),
                 "%.1f" % (100 * fl / pl) if pl else "-"))
print()
print("=== FUNNEL PER LEAGUE (category) ===")
cat = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    c = cat[r["lg"]]
    for k in ("YP","NP","YTF","NTF","YF","NF","obs"): c[k] += r[k]
    c["matched"] += float(r["matched"]); c["mkts"] += 1
    c["two_sided"] += 1 if r["two_sided"] else 0
hdr2 = "%-10s %5s %6s %6s %6s %7s %8s %8s %8s"
print(hdr2 % ("league","mkts","PLACED","FLOW","FILL","MATCHED","flow/plcd","fill/flow","match/fill"))
for lg, c in sorted(cat.items(), key=lambda kv: (-kv[1]["matched"], -(kv[1]["YF"]+kv[1]["NF"]))):
    pl, fl, fi = c["YP"]+c["NP"], c["YTF"]+c["NTF"], c["YF"]+c["NF"]
    if fi == 0 and fl == 0: continue
    print(hdr2 % (lg, c["mkts"], pl, fl, fi, "%.1f"%c["matched"],
                  "%.1f%%"%(100*fl/pl) if pl else "-",
                  "%.1f%%"%(100*fi/fl) if fl else "-",
                  "%.2f"%(c["matched"]/fi) if fi else "-"))
json.dump([{k:(str(v) if isinstance(v,D) else v) for k,v in r.items()} for r in rows],
          open("strict_per_market.json","w"), indent=0)
