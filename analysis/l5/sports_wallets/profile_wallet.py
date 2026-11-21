"""Profile one wallet's public activity file: what does it buy, at what price, one side or both,
and what did markets that settled inside the file pay. READ-ONLY arithmetic on a saved file.
P&L here covers only conditions with a REDEEM in the file, and counts only buys seen in the file,
so it is a SAMPLE of settled markets, biased toward short-lived ones - not the wallet's total."""
import gzip, collections, json, sys
rows = [json.loads(l) for l in (gzip.open(sys.argv[1],'rt') if sys.argv[1].endswith('.gz') else open(sys.argv[1]))]
tr = [r for r in rows if r["type"] == "TRADE"]
span_d = (max(r["timestamp"] for r in rows) - min(r["timestamp"] for r in rows)) / 86400
usd = sum(r["usdc_size"] for r in tr)
print("days %.2f  trades %d  usdc %.0f  per day: %d trades, $%.0f  median trade $%.1f  mean $%.1f" % (
    span_d, len(tr), usd, len(tr) / span_d, usd / span_d,
    sorted(r["usdc_size"] for r in tr)[len(tr) // 2], usd / len(tr)))
print("sides", collections.Counter(r["side"] for r in tr))
b = collections.defaultdict(lambda: [0, 0.0])
for r in tr:
    p = r["price"]
    k = "<0.05" if p < .05 else "0.05-0.2" if p < .2 else "0.2-0.4" if p < .4 else "0.4-0.6" if p < .6 else \
        "0.6-0.8" if p < .8 else "0.8-0.95" if p < .95 else "0.95-0.99" if p < .99 else ">=0.99"
    b[k][0] += 1; b[k][1] += r["usdc_size"]
print("price bucket | trades | usdc | share of usdc")
for k in ("<0.05", "0.05-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-0.95", "0.95-0.99", ">=0.99"):
    print("  %-10s %6d %12.0f %5.1f%%" % (k, b[k][0], b[k][1], 100 * b[k][1] / usd))
cond = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))  # cond -> outcome -> [shares, usdc]
for r in tr:
    s = 1 if r["side"] == "BUY" else -1
    c = cond[r["condition_id"]][r["outcome_index"]]; c[0] += s * r["size"]; c[1] += s * r["usdc_size"]
both = {k: v for k, v in cond.items() if len(v) >= 2}
print("conditions traded %d; both outcomes bought in %d (%.0f%%), holding %.0f%% of usdc" % (
    len(cond), len(both), 100 * len(both) / len(cond),
    100 * sum(sum(x[1] for x in v.values()) for v in both.values()) / usd))
m = pc = 0.0
for v in both.values():
    (s0, u0), (s1, u1) = list(v.values())[:2]
    q = min(s0, s1)
    if q > 0: m += q; pc += q * (u0 / s0 + u1 / s1)
print("matched shares across both-sided conditions %.0f at average pair cost %.4f (locked gross $%.0f)" % (m, pc / m if m else 0, m - pc))
red = collections.defaultdict(float)
for r in rows:
    if r["type"] == "REDEEM": red[r["condition_id"]] += r["usdc_size"] or 0
cost = pay = 0.0; n = w = 0
by = collections.defaultdict(lambda: [0, 0.0, 0.0])
slug = {r["condition_id"]: (r.get("slug") or "").split("-")[0] for r in tr}
for c, p in red.items():
    if c not in cond: continue
    k = sum(x[1] for x in cond[c].values()); n += 1; cost += k; pay += p; w += p > k
    g = by[slug.get(c, "?")]; g[0] += 1; g[1] += k; g[2] += p
print("SAMPLE settled in file: %d conditions, cost $%.0f, redeemed $%.0f, net $%.0f (%.2f%% of cost), %d won" % (
    n, cost, pay, pay - cost, 100 * (pay - cost) / cost if cost else 0, w))
print("  note: losing positions produce no REDEEM row, so this sample OVERSTATES profit; see below")
allc = sum(sum(x[1] for x in v.values()) for v in cond.values())
print("  all buys in file $%.0f vs all redeems in file $%.0f" % (allc, sum(red.values())))
for k, g in sorted(by.items(), key=lambda x: -x[1][1])[:12]:
    print("  %-6s n %4d cost %10.0f redeemed %10.0f" % (k, g[0], g[1], g[2]))
t = sorted(r["timestamp"] for r in tr)
gaps = [b - a for a, b in zip(t, t[1:])]
print("trades per tx hash: %.2f" % (len(tr) / len({r["transaction_hash"] for r in tr})))
kinds = collections.Counter()
for r in tr:
    s = r.get("slug") or ""
    kinds["spread" if "spread" in s else "total" if "total" in s else "btts" if "btts" in s else "other/moneyline"] += r["usdc_size"]
print("market kind by usdc", {k: round(v) for k, v in kinds.items()})
