"""Join the bot's ORDER / FILL events with the two-venue book recording.
Input: orders.json = [{"t": epoch or ISO, "kind": "ORDER"|"FILL"|"CANCEL", "slug":..., "side": "YES"|"NO", "px":..., "qty":...}, ...]
For each event: our touch, spread, depth, international mid at that moment, and where both
venues' mids were 30 s / 2 min / 5 min later (in the order's favour = positive)."""
import json, sys, bisect, datetime as dt
def ts(x):
    if isinstance(x, (int, float)): return float(x)
    return dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
ev = json.load(open(sys.argv[1])); recs = {}
for path in sys.argv[2:]:
    for l in open(path):
        r = json.loads(l)
        if r.get("ub") and r.get("ua") and r.get("ib") and r.get("ia"):
            recs.setdefault(r["slug"], []).append(r)
def at(slug, t):
    rs = recs.get(slug) or []
    i = bisect.bisect_right([r["t"] for r in rs], t) - 1
    return rs[i] if i >= 0 and t - rs[i]["t"] < 15 else None
print("%-8s %-6s %-22s %-3s %5s %3s | %-11s %-13s %6s | %s" % ("time", "kind", "market", "sd", "px", "qty", "us bid/ask", "depth b x a", "intl", "favourable move of intl mid +30s/+2m/+5m"))
for e in sorted(ev, key=lambda e: ts(e["t"])):
    t = ts(e["t"]); r = at(e["slug"], t); sd = e["side"]; px = float(e["px"])
    if not r:
        print("%-8s %-6s %-22s %-3s %5.2f %3s | no book sample" % (dt.datetime.utcfromtimestamp(t).strftime("%H:%M:%S"), e["kind"], e["slug"][-14:], sd, px, e.get("qty"))); continue
    imid = (r["ib"] + r["ia"]) / 2
    fair = imid if sd == "YES" else 1 - imid
    mv = []
    for d in (30, 120, 300):
        r2 = at(e["slug"], t + d)
        if r2:
            m2 = (r2["ib"] + r2["ia"]) / 2; f2 = m2 if sd == "YES" else 1 - m2
            mv.append("%+.3f" % (f2 - px))
        else: mv.append("  n/a")
    print("%-8s %-6s %-22s %-3s %5.2f %3s | %.2f/%.2f   %6.0f x %-6.0f %6.3f | value vs our price now %+.3f, later %s" % (
        dt.datetime.utcfromtimestamp(t).strftime("%H:%M:%S"), e["kind"], e["slug"][-14:], sd, px, e.get("qty"),
        r["ub"], r["ua"], r["ubq"], r["uaq"], imid, fair - px, " ".join(mv)))
