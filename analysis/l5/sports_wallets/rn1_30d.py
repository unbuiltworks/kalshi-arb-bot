"""RN1 30-day reconciliation from its own public activity. READ-ONLY arithmetic on saved public files.
HISTORICAL, another trader on another venue - nothing here is our P&L.

usage: rn1_30d.py <activity.jsonl.gz> <taker_trades.jsonl.gz> <gamma_cache.json> <platform_pnl.json>

Method
  payout per condition = sum over outcomes of shares bought * gamma outcomePrice (closed markets only), so a losing
  position (no REDEEM row) counts as zero. usdc_size of a taker buy already includes the taker fee (verified below),
  so P&L = payout - usdc spent is NET of fees and excludes rebates/rewards.
  Every buy row gets its own P&L (shares*payout price - usdc), which sums exactly to the condition P&L; per-buy
  cuts (timing, price, size, maker/taker) use that.
  taker flag: data-api /v2/trades default (takerOnly=true) returns only the wallet's taker fills; joined on
  (transaction_hash, token_id).
  se: standard error of the cell's dollars treating each game (event) as one independent draw. ESTIMATE."""
import collections, datetime as dt, gzip, json, math, sys
ACT, TAK, GAM, PLAT = sys.argv[1:5]
W0 = dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc).timestamp()
LEAD = 36 * 3600          # a condition is INCLUDED only if its game starts >= W0 + LEAD (see lead-time table)
def iso(s):
    if not s: return None
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    if s.endswith("+00"): s += ":00"
    try: return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        head, tz = s[:19], s[-6:]
        return dt.datetime.fromisoformat(head + tz).timestamp()
def day(ts): return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
def pct(xs, p):
    xs = sorted(xs); return xs[int(p * (len(xs) - 1))] if xs else float("nan")

taker = set()
for l in gzip.open(TAK, "rt"):
    r = json.loads(l); taker.add((r["transaction_hash"], r["token_id"]))
gam = json.load(open(GAM))
trades, red, other, seen, ndup = [], collections.defaultdict(float), collections.defaultdict(float), set(), 0
redts = collections.defaultdict(list)
for l in gzip.open(ACT, "rt"):
    r = json.loads(l)
    if r["type"] == "TRADE":
        trades.append(r)
        if l in seen: ndup += 1
        seen.add(l)
    elif r["type"] == "REDEEM":
        red[r["condition_id"]] += r["usdc_size"] or 0; redts[r["condition_id"]].append((r["timestamp"], r["usdc_size"] or 0))
    else: other[r["type"]] += r["usdc_size"] or 0
del seen
trades.sort(key=lambda r: r["timestamp"])
T0, T1 = trades[0]["timestamp"], trades[-1]["timestamp"]
usd_all = sum(r["usdc_size"] for r in trades)
print("=== 0. DATA ===")
print("activity file: %s .. %s UTC (%.2f days)  TRADE rows %d  usdc %.0f  byte-identical duplicate TRADE rows %d (kept)" % (
    dt.datetime.fromtimestamp(T0, dt.timezone.utc).isoformat()[:16], dt.datetime.fromtimestamp(T1, dt.timezone.utc).isoformat()[:16],
    (T1 - T0) / 86400, len(trades), usd_all, ndup))
print("sides", dict(collections.Counter(r["side"] for r in trades)), " REDEEM usdc %.0f on %d conditions" % (sum(red.values()), len(red)),
      " other rows usdc:", {k: round(v) for k, v in other.items()})
assert all(r["side"] == "BUY" for r in trades), "sell rows present - P&L code assumes buys only"

# ---------- per-condition state ----------
class C: pass
conds = {}
for r in trades:
    c = conds.get(r["condition_id"])
    if c is None:
        c = conds[r["condition_id"]] = C(); c.rows = []; c.id = r["condition_id"]; c.slug = r["slug"] or ""; c.event = (r["event_slug"] or r["slug"] or "").replace("-more-markets", "")
    c.rows.append(r)
    r["taker"] = (r["transaction_hash"], r["token_id"]) in taker
tk_usd = sum(r["usdc_size"] for r in trades if r["taker"]); tk_n = sum(1 for r in trades if r["taker"])
def mtype(m, slug):
    t = (m or {}).get("sportsMarketType") or ""
    if t == "moneyline": return "moneyline"
    if t == "spreads": return "spread"
    if t == "totals": return "total"
    if not t:
        return "spread" if "spread" in slug else "total" if "total" in slug else "other(no type)"
    return "other"
status = collections.Counter(); status_usd = collections.Counter()
for c in conds.values():
    m = gam.get(c.id); c.m = m
    c.cost = sum(r["usdc_size"] for r in c.rows)
    c.sh = collections.defaultdict(float); c.us = collections.defaultdict(float)
    for r in c.rows: c.sh[r["outcome_index"]] += r["size"]; c.us[r["outcome_index"]] += r["usdc_size"]
    c.league = c.slug.split("-")[0]; c.mt = mtype(m, c.slug); c.rawtype = (m or {}).get("sportsMarketType") or "none"
    c.gst = iso(m.get("gameStartTime")) if m else None
    c.closed = iso(m.get("closedTime")) if m and m.get("closed") else None
    c.px = None
    if m and m.get("closed") and m.get("outcomePrices"):
        px = [float(x) for x in json.loads(m["outcomePrices"])]
        if abs(sum(px) - 1) < 1e-6: c.px = px
    if m is None: c.st = "no gamma record"
    elif c.px is None: c.st = "not closed / no final price at fetch"
    elif c.closed is None or c.closed > T1: c.st = "closed after last activity row"
    elif c.gst is None: c.st = "resolved, no gameStartTime"
    elif c.gst < W0 + LEAD: c.st = "resolved, game starts < window start + 36h (cost may be incomplete)"
    else: c.st = "INCLUDED"
    status[c.st] += 1; status_usd[c.st] += c.cost
    if c.px:
        c.pay = sum(c.sh[i] * c.px[i] for i in c.sh); c.pnl = c.pay - c.cost
        for r in c.rows: r["pnl"] = r["size"] * c.px[r["outcome_index"]] - r["usdc_size"]
print("\n=== 1. COVERAGE (share of the file's buy dollars) ===")
for k, v in status.most_common(): print("  %-70s n %6d  usdc %11.0f  %5.1f%%" % (k, v, status_usd[k], 100 * status_usd[k] / usd_all))
INC = [c for c in conds.values() if c.st == "INCLUDED"]
RES = [c for c in conds.values() if c.px]
inc_cost = sum(c.cost for c in INC); inc_pnl = sum(c.pnl for c in INC)
# lead time evidence for the 36h rule: games starting in the middle of the window
mid = [c for c in RES if c.gst and c.gst >= W0 + 10 * 86400]
tot = sum(c.cost for c in mid); lead = collections.Counter()
for c in mid:
    for r in c.rows:
        d = c.gst - r["timestamp"]
        lead["in-game/after start" if d <= 0 else "<1h before" if d < 3600 else "1-12h before" if d < 43200 else "12-36h before" if d < LEAD else ">36h before"] += r["usdc_size"]
print("lead-time of buys, games starting >=10 days into the window (justifies the 36h rule):")
for k in ("in-game/after start", "<1h before", "1-12h before", "12-36h before", ">36h before"): print("  %-20s %5.2f%% of usdc" % (k, 100 * lead[k] / tot))
# redeem cross-check on included conditions
chk = [(c, red[c.id]) for c in INC if c.id in red]
exact = sum(1 for c, v in chk if abs(v - c.pay) <= 0.01 + 0.001 * c.pay); more = sum(1 for c, v in chk if v > c.pay * 1.001 + 0.01)
print("redeem cross-check, INCLUDED conditions with a REDEEM row: %d; redeemed usdc %.0f vs gamma-computed payout %.0f; equal within 0.1%%: %d; redeemed MORE than bought in file: %d (excess $%.0f); less: %d" % (
    len(chk), sum(v for _, v in chk), sum(c.pay for c, _ in chk), exact, more, sum(v - c.pay for c, v in chk if v > c.pay * 1.001 + 0.01), len(chk) - exact - more))
nored = [c for c in INC if c.id not in red]
print("INCLUDED conditions with NO redeem row: %d, cost $%.0f, computed payout $%.0f (payout>0 means won but not yet redeemed in file)" % (
    len(nored), sum(c.cost for c in nored), sum(c.pay for c in nored)))
print("a redeemed-only tally would have said: cost $%.0f redeemed $%.0f net $%.0f  vs all INCLUDED net $%.0f" % (
    sum(c.cost for c, _ in chk), sum(v for _, v in chk), sum(v - c.cost for c, v in chk), inc_pnl))

print("\n=== 2. SETTLED P&L, INCLUDED CONDITIONS (net of taker fees, before rebates/rewards) ===")
ev = collections.defaultdict(float)
for c in INC: ev[c.event] += c.pnl
print("conditions %d  games %d  cost $%.0f  payout $%.0f  P&L $%.0f  = %.3f%% of cost;  conditions won-money %d (%.0f%%)" % (
    len(INC), len(ev), inc_cost, inc_cost + inc_pnl, inc_pnl, 100 * inc_pnl / inc_cost, sum(1 for c in INC if c.pnl > 0), 100 * sum(1 for c in INC if c.pnl > 0) / len(INC)))
top = sorted(INC, key=lambda c: -c.pnl)
print("concentration: best 10 conditions $%.0f, worst 10 $%.0f; best 10 games $%.0f, worst 10 games $%.0f" % (
    sum(c.pnl for c in top[:10]), sum(c.pnl for c in top[-10:]), sum(sorted(ev.values())[-10:]), sum(sorted(ev.values())[:10])))

# ---------- 3. reconciliation ----------
print("\n=== 3. RECONCILIATION with platform series (realized_market_pnl) ===")
pts = json.load(open(PLAT))["data"]["points"]; P = {p["timestamp"]: p for p in pts}
byclose = collections.defaultdict(float); byredeem = collections.defaultdict(float); byclose_inc = collections.defaultdict(float)
for c in RES:
    if c.closed: byclose[day(c.closed)] += c.pnl
    if c.st == "INCLUDED": byclose_inc[day(c.closed)] += c.pnl
# cash view: buys negative on trade day, redeem positive on redeem day
cash = collections.defaultdict(float)
for r in trades: cash[day(r["timestamp"])] -= r["usdc_size"]
for cid, lst in redts.items():
    for ts, v in lst: cash[day(ts)] += v
fees_day = collections.defaultdict(float)
for r in trades:
    if r["taker"]: fees_day[day(r["timestamp"])] += r["usdc_size"] - r["size"] * r["price"]
days = sorted(d for d in byclose if "2026-10-22" <= d <= "2026-11-20")
print("day(UTC)   | platform d(realized_market_pnl) | mine by close day (all resolved) | mine INCLUDED only | diff | platform d(fees_paid) | my taker fees")
sx = sy = sxx = syy = sxy = 0.0; n = 0; rows_out = []
for d in days:
    t = dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    if t not in P or t + 86400 not in P: continue
    pl = P[t + 86400]["realized_market_pnl"] - P[t]["realized_market_pnl"]; pf = P[t + 86400]["fees_paid"] - P[t]["fees_paid"]
    x = byclose[d]; rows_out.append((d, pl, x, byclose_inc[d], pf, fees_day[d]))
    print("%s | %10.0f | %10.0f | %10.0f | %9.0f | %8.0f | %8.0f" % (d, pl, x, byclose_inc[d], x - pl, pf, fees_day[d]))
for a, b in (("2026-10-22", "2026-11-20"), ("2026-10-24", "2026-11-20")):
    sel = [r for r in rows_out if a <= r[0] <= b]
    pl = sum(r[1] for r in sel); mine = sum(r[2] for r in sel); minc = sum(r[3] for r in sel); pf = sum(r[4] for r in sel); mf = sum(r[5] for r in sel)
    xs = [r[1] for r in sel]; ys = [r[2] for r in sel]; mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
    cor = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    print("SUM %s..%s (%d days): platform d(realized_market_pnl) $%.0f | mine all-resolved by close day $%.0f (gap %+.0f) | mine INCLUDED-only $%.0f | daily correlation %.3f" % (
        a, b, len(sel), pl, mine, mine - pl, minc, cor))
    print("     fees: platform d(fees_paid) $%.0f | my taker fees (usdc_size - size*price on taker rows) $%.0f | if platform P&L were BEFORE fees the gap would be %+.0f" % (pf, mf, mine + mf - pl))
print("positive days (mine, by close day): %d of %d; mean $%.0f/day, sd $%.0f" % (
    sum(1 for r in rows_out if r[2] > 0), len(rows_out), sum(r[2] for r in rows_out) / len(rows_out),
    math.sqrt(sum((r[2] - sum(q[2] for q in rows_out) / len(rows_out)) ** 2 for r in rows_out) / (len(rows_out) - 1))))
print("cash check whole file: buys $%.0f, redeems $%.0f, net cash $%.0f (ignores inventory open at both ends)" % (usd_all, sum(red.values()), sum(red.values()) - usd_all))
print("income rows in file: %s ; platform deltas 10-22 00:00 -> last point: maker_rebate %.0f taker_rebate %.0f reward %.0f fees_paid %.0f" % (
    {k: round(v) for k, v in other.items()}, pts[-1]["maker_rebate"] - P[W0]["maker_rebate"], pts[-1]["taker_rebate"] - P[W0]["taker_rebate"],
    pts[-1]["reward_income"] - P[W0]["reward_income"], pts[-1]["fees_paid"] - P[W0]["fees_paid"]))

# ---------- 4. breakdowns ----------
def table(title, keyed, order=None, minn=30, top=None):
    """keyed: iterable of (key, cond, cost, pnl) contributions"""
    g = collections.defaultdict(lambda: [0.0, 0.0, set(), collections.defaultdict(float)])
    for k, c, cost, pnl in keyed:
        x = g[k]; x[0] += cost; x[1] += pnl; x[2].add(c.id); x[3][c.event] += pnl
    print("\n" + title); print("  %-26s %12s %11s %9s %7s %6s %9s %s" % ("cell", "cost $", "P&L $", "P&L/$", "conds", "games", "se $", ""))
    keys = order or [k for k, _ in sorted(g.items(), key=lambda x: -x[1][0])]
    if top: rest = keys[top:]; keys = keys[:top]
    for k in keys:
        if k not in g: continue
        x = g[k]; ev = list(x[3].values()); G = len(ev); mu = sum(ev) / G
        se = math.sqrt(G / (G - 1) * sum((v - mu) ** 2 for v in ev)) if G > 1 else float("nan")
        flag = "SMALL-N" if len(x[2]) < minn or G < 15 else ""
        print("  %-26s %12.0f %11.0f %+9.4f %7d %6d %9.0f %s" % (k, x[0], x[1], x[1] / x[0] if x[0] else 0, len(x[2]), G, se, flag))
    if top and rest:
        print("  %-26s %12.0f %11.0f %+9.4f %7d" % ("(all other %d)" % len(rest), sum(g[k][0] for k in rest), sum(g[k][1] for k in rest),
              sum(g[k][1] for k in rest) / max(1e-9, sum(g[k][0] for k in rest)), sum(len(g[k][2]) for k in rest)))
print("\n=== 4. BREAKDOWNS (INCLUDED conditions; P&L net of taker fees; se = game-clustered standard error of P&L $; |P&L| < 2*se is not distinguishable from zero) ===")
table("(a) league (slug prefix), top 25 by cost", ((c.league, c, c.cost, c.pnl) for c in INC), top=25)
table("(b) market type", ((c.mt, c, c.cost, c.pnl) for c in INC))
table("(b2) gamma sportsMarketType inside 'other', top 12", ((c.rawtype, c, c.cost, c.pnl) for c in INC if c.mt.startswith("other")), top=12)
def when(c, r):
    d = r["timestamp"] - c.gst
    return "pre >12h" if d < -43200 else "pre 1-12h" if d < -3600 else "pre <1h" if d < 0 else "in-game 0-30m" if d < 1800 else "in-game 30-90m" if d < 5400 else "in-game 90-180m" if d < 10800 else "in-game >180m"
buys = [(c, r) for c in INC for r in c.rows]
table("(c) pre-game vs in-game (per buy, vs gamma gameStartTime)", (("PRE-GAME" if r["timestamp"] < c.gst else "IN-GAME", c, r["usdc_size"], r["pnl"]) for c, r in buys), order=["PRE-GAME", "IN-GAME"])
table("(c2) finer timing", ((when(c, r), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=["pre >12h", "pre 1-12h", "pre <1h", "in-game 0-30m", "in-game 30-90m", "in-game 90-180m", "in-game >180m"])
def band(p):
    return "<0.05" if p < .05 else "0.05-0.20" if p < .2 else "0.20-0.40" if p < .4 else "0.40-0.60" if p < .6 else "0.60-0.80" if p < .8 else "0.80-0.95" if p < .95 else ">=0.95"
PB = ["<0.05", "0.05-0.20", "0.20-0.40", "0.40-0.60", "0.60-0.80", "0.80-0.95", ">=0.95"]
table("(d) entry price band (per buy)", ((band(r["price"]), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=PB)
table("(d2) entry price band x timing", ((("PRE " if r["timestamp"] < c.gst else "IN  ") + band(r["price"]), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=[a + b for a in ("PRE ", "IN  ") for b in PB])
def sides(c): return "both-sided" if sum(1 for v in c.sh.values() if v > 0) >= 2 else "one-sided"
table("(e) one-sided vs both-sided conditions", ((sides(c), c, c.cost, c.pnl) for c in INC), order=["one-sided", "both-sided"])
def split(c):
    (i0, i1) = sorted(c.sh)[:2]; q = min(c.sh[i0], c.sh[i1]); pc = c.us[i0] / c.sh[i0] + c.us[i1] / c.sh[i1]
    return q, pc, q * (1 - pc), c.cost - q * pc
bs = [c for c in INC if sides(c) == "both-sided"]
k2 = []
for c in bs:
    q, pc, mp, rc = split(c); c.q, c.pc, c.mp, c.rc = q, pc, mp, rc
    k2.append(("matched pairs", c, q * pc, mp)); k2.append(("residual (unmatched)", c, rc, c.pnl - mp))
table("(e2) both-sided: matched-pair P&L = matched shares x (1 - pair cost)  vs  residual P&L (rest)", k2, order=["matched pairs", "residual (unmatched)"])
mq = sum(c.q for c in bs)
print("  matched shares %.0f at average pair cost %.4f; unmatched shares are %.1f%% of all shares in both-sided conditions" % (
    mq, sum(c.q * c.pc for c in bs) / mq, 100 * sum(abs(c.sh[sorted(c.sh)[0]] - c.sh[sorted(c.sh)[1]]) for c in bs) / sum(sum(c.sh.values()) for c in bs)))
def pcb(pc): return "pc <0.90" if pc < .9 else "pc 0.90-0.96" if pc < .96 else "pc 0.96-1.00" if pc < 1 else "pc 1.00-1.04" if pc < 1.04 else "pc >=1.04"
table("(e3) both-sided by pair cost: matched part", ((pcb(c.pc), c, c.q * c.pc, c.mp) for c in bs), order=["pc <0.90", "pc 0.90-0.96", "pc 0.96-1.00", "pc 1.00-1.04", "pc >=1.04"])
table("(e4) both-sided by pair cost: residual part", ((pcb(c.pc), c, c.rc, c.pnl - c.mp) for c in bs), order=["pc <0.90", "pc 0.90-0.96", "pc 0.96-1.00", "pc 1.00-1.04", "pc >=1.04"])
rw = [c for c in bs if c.rc > 1]
won = sum(1 for c in rw if c.pnl - c.mp > 0)
print("  residual side won in %d of %d both-sided conditions with residual cost > $1 (%.1f%%); dollar-weighted average residual entry price %.3f" % (
    won, len(rw), 100 * won / len(rw), sum(c.rc for c in rw) / sum(abs(c.sh[sorted(c.sh)[0]] - c.sh[sorted(c.sh)[1]]) for c in rw)))
nr = collections.Counter(); nru = collections.Counter()
for c in INC: k = (sides(c), bool(c.m.get("negRisk"))); nr[k] += 1; nru[k] += c.cost
print("  negRisk note (3-way events; a 'one-sided' condition can be hedged by another condition of the same event - NOT modelled):", {str(k): (nr[k], round(nru[k])) for k in nr})

# ---------- 5. inventory behaviour ----------
print("\n=== 5. INVENTORY BEHAVIOUR (INCLUDED conditions) ===")
cs = [c.cost for c in INC]; evc = collections.defaultdict(float)
for c in INC: evc[c.event] += c.cost
print("dollars per condition: p10 %.0f p25 %.0f p50 %.0f p75 %.0f p90 %.0f p99 %.0f mean %.0f max %.0f" % (*[pct(cs, p) for p in (.1, .25, .5, .75, .9, .99)], sum(cs) / len(cs), max(cs)))
print("dollars per game (all its conditions): p50 %.0f p90 %.0f mean %.0f max %.0f ; conditions per game p50 %d p90 %d" % (
    pct(list(evc.values()), .5), pct(list(evc.values()), .9), sum(evc.values()) / len(evc), max(evc.values()),
    pct(list(collections.Counter(c.event for c in INC).values()), .5), pct(list(collections.Counter(c.event for c in INC).values()), .9)))
print("buys per condition: p50 %d p90 %d p99 %d ; dollars per buy row: p10 %.1f p50 %.1f p90 %.0f p99 %.0f mean %.0f" % (
    pct([len(c.rows) for c in INC], .5), pct([len(c.rows) for c in INC], .9), pct([len(c.rows) for c in INC], .99),
    *[pct([r["usdc_size"] for _, r in buys], p) for p in (.1, .5, .9, .99)], inc_cost / len(buys)))
print("exposure build: cumulative share of final dollars bought by time relative to game start")
marks = [(-86400, "T-24h"), (-43200, "T-12h"), (-3600, "T-1h"), (0, "start"), (1800, "T+30m"), (3600, "T+60m"), (5400, "T+90m"), (7200, "T+120m"), (10800, "T+180m")]
cum = collections.Counter(); condcum = collections.defaultdict(list)
for c in INC:
    for off, name in marks:
        v = sum(r["usdc_size"] for r in c.rows if r["timestamp"] - c.gst <= off); cum[name] += v; condcum[name].append(v / c.cost)
print("  dollar-weighted: " + "  ".join("%s %.1f%%" % (n, 100 * cum[n] / inc_cost) for _, n in marks))
print("  median condition: " + "  ".join("%s %.0f%%" % (n, 100 * pct(condcum[n], .5)) for _, n in marks))
fb = collections.Counter()
for c in INC: fb["first buy pre-game" if c.rows[0]["timestamp"] < c.gst else "first buy in-game"] += 1
print("  ", dict(fb), "; conditions with ALL dollars pre-game %d, ALL in-game %d, mixed %d" % (
    sum(1 for c in INC if c.rows[-1]["timestamp"] < c.gst), sum(1 for c in INC if c.rows[0]["timestamp"] >= c.gst),
    sum(1 for c in INC if c.rows[0]["timestamp"] < c.gst <= c.rows[-1]["timestamp"])))
print("second side: added in %d of %d conditions (%.1f%%), holding %.1f%% of dollars" % (len(bs), len(INC), 100 * len(bs) / len(INC), 100 * sum(c.cost for c in bs) / inc_cost))
gaps, before_n, before_usd, share1, sw, when2 = [], [], [], [], [], collections.Counter()
for c in bs:
    first = c.rows[0]["outcome_index"]; t2 = next(r for r in c.rows if r["outcome_index"] != first)
    pre = [r for r in c.rows if r["timestamp"] < t2["timestamp"] or (r is not t2 and r["timestamp"] == t2["timestamp"] and r["outcome_index"] == first)]
    pre = [r for r in pre if r["outcome_index"] == first]
    gaps.append(t2["timestamp"] - c.rows[0]["timestamp"]); before_n.append(len(pre)); before_usd.append(sum(r["usdc_size"] for r in pre))
    sw.append(sum(1 for a, b in zip(c.rows, c.rows[1:]) if a["outcome_index"] != b["outcome_index"]))
    when2["second side first bought pre-game" if t2["timestamp"] < c.gst else "second side first bought in-game"] += 1
    c.first_px = c.rows[0]["price"]; c.second_px = t2["price"]; c.pre_avg = sum(r["usdc_size"] for r in pre) / max(1e-9, sum(r["size"] for r in pre))
print("  time from first buy to first buy of the other side: p10 %ds p25 %ds p50 %ds (%.0f min) p75 %ds p90 %ds; within 60s: %.1f%%; within 10 min: %.1f%%; over 1h: %.1f%%" % (
    pct(gaps, .1), pct(gaps, .25), pct(gaps, .5), pct(gaps, .5) / 60, pct(gaps, .75), pct(gaps, .9),
    100 * sum(1 for g in gaps if g <= 60) / len(gaps), 100 * sum(1 for g in gaps if g <= 600) / len(gaps), 100 * sum(1 for g in gaps if g > 3600) / len(gaps)))
print("  same-side buys before the other side appears: p50 %d p75 %d p90 %d ; dollars on first side before then: p50 $%.0f p90 $%.0f ; side switches per both-sided condition p50 %d p90 %d" % (
    pct(before_n, .5), pct(before_n, .75), pct(before_n, .9), pct(before_usd, .5), pct(before_usd, .9), pct(sw, .5), pct(sw, .9)))
print("  ", dict(when2))
s = [c.pre_avg + c.second_px for c in bs]
print("  (first side's average price so far) + (price of first opposite buy): p10 %.3f p25 %.3f p50 %.3f p75 %.3f p90 %.3f; below 0.96 in %.1f%%, above 1.00 in %.1f%%" % (
    *[pct(s, p) for p in (.1, .25, .5, .75, .9)], 100 * sum(1 for x in s if x < .96) / len(s), 100 * sum(1 for x in s if x > 1.0) / len(s)))
table("  first-buy timing of the condition", ((("first buy pre-game" if c.rows[0]["timestamp"] < c.gst else "first buy in-game") + " / " + sides(c), c, c.cost, c.pnl) for c in INC))

# ---------- 6. maker / taker ----------
print("\n=== 6. MAKER vs TAKER ===")
print("whole file: taker fills %d of %d (%.1f%%), taker usdc %.0f of %.0f (%.1f%%)" % (tk_n, len(trades), 100 * tk_n / len(trades), tk_usd, usd_all, 100 * tk_usd / usd_all))
tf = sum(r["usdc_size"] - r["size"] * r["price"] for r in trades if r["taker"]); mf = sum(r["usdc_size"] - r["size"] * r["price"] for r in trades if not r["taker"])
mod = 0.0
for r in trades:
    if r["taker"]:
        fs = (gam.get(r["condition_id"]) or {}).get("feeSchedule") or {}
        mod += (fs.get("rate") or 0) * r["size"] * r["price"] * (1 - r["price"])
print("fee embedded in usdc_size (usdc_size - size*price): taker rows $%.0f, maker rows $%.2f ; gamma feeSchedule rate*shares*p*(1-p) on taker rows $%.0f ; platform d(fees_paid) over the window $%.0f" % (
    tf, mf, mod, -(pts[-1]["fees_paid"] - P[W0]["fees_paid"])))
print("taker fee as %% of taker notional: %.2f%%; of all notional: %.3f%%" % (100 * tf / tk_usd, 100 * tf / usd_all))
table("maker vs taker (per buy, INCLUDED conditions; taker P&L is after its fee)", ((("TAKER" if r["taker"] else "MAKER"), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=["MAKER", "TAKER"])
table("maker/taker x timing", ((("TAKER" if r["taker"] else "MAKER") + (" pre-game" if r["timestamp"] < c.gst else " in-game"), c, r["usdc_size"], r["pnl"]) for c, r in buys),
      order=["MAKER pre-game", "MAKER in-game", "TAKER pre-game", "TAKER in-game"])
table("maker/taker x price band", ((("TAKER " if r["taker"] else "MAKER ") + band(r["price"]), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=[a + b for a in ("MAKER ", "TAKER ") for b in PB])
fl = collections.Counter()
for c in bs:
    first = c.rows[0]["outcome_index"]; t2 = next(r for r in c.rows if r["outcome_index"] != first)
    fl[("first buy " + ("taker" if c.rows[0]["taker"] else "maker"), "first opposite buy " + ("taker" if t2["taker"] else "maker"))] += 1
print("  both-sided conditions, how each side was opened:", {" / ".join(k): v for k, v in fl.items()})

# ---------- 7. size ----------
print("\n=== 7. SIZE DEPENDENCE (per buy row = one fill; a large order filled in pieces appears as several rows) ===")
def sb(u): return "<$20" if u < 20 else "$20-100" if u < 100 else "$100-500" if u < 500 else ">$500"
SB = ["<$20", "$20-100", "$100-500", ">$500"]
table("fill size band", ((sb(r["usdc_size"]), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=SB)
table("fill size band x maker/taker", ((("TAKER " if r["taker"] else "MAKER ") + sb(r["usdc_size"]), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=[a + b for a in ("MAKER ", "TAKER ") for b in SB])
table("fill size band x timing", ((("PRE " if r["timestamp"] < c.gst else "IN  ") + sb(r["usdc_size"]), c, r["usdc_size"], r["pnl"]) for c, r in buys), order=[a + b for a in ("PRE ", "IN  ") for b in SB])
def cb(u): return "cond <$100" if u < 100 else "cond $100-1k" if u < 1000 else "cond $1k-10k" if u < 10000 else "cond >$10k"
table("total dollars in the condition", ((cb(c.cost), c, c.cost, c.pnl) for c in INC), order=["cond <$100", "cond $100-1k", "cond $1k-10k", "cond >$10k"])
print("  equal-weight view (each condition counts once): mean P&L/$ of conditions <$100: %+.4f ; $100-1k: %+.4f ; $1k-10k: %+.4f ; >$10k: %+.4f" % tuple(
    (lambda xs: sum(xs) / len(xs))([c.pnl / c.cost for c in INC if lo <= c.cost < hi]) for lo, hi in ((0, 100), (100, 1000), (1000, 10000), (10000, 1e12))))
table("conditions under $100 in total, by entry price band (per buy)", ((band(r["price"]), c, r["usdc_size"], r["pnl"]) for c, r in buys if c.cost < 100), order=PB)
table("fills under $20, by entry price band", ((band(r["price"]), c, r["usdc_size"], r["pnl"]) for c, r in buys if r["usdc_size"] < 20), order=PB)
table("fills under $20 at price 0.20-0.95, maker/taker", ((("TAKER" if r["taker"] else "MAKER"), c, r["usdc_size"], r["pnl"]) for c, r in buys if r["usdc_size"] < 20 and .2 <= r["price"] < .95), order=["MAKER", "TAKER"])

print("\n=== 8. WINDOW ECONOMICS SUMMARY ===")
mk_usd = usd_all - tk_usd
print("INCLUDED settled trading P&L (net of taker fees): $%.0f on $%.0f bought = %.3f%% ; game-clustered se $%.0f (ESTIMATE) -> P&L/se = %.2f" % (
    inc_pnl, inc_cost, 100 * inc_pnl / inc_cost, math.sqrt(len(ev) / (len(ev) - 1) * sum((v - sum(ev.values()) / len(ev)) ** 2 for v in ev.values())),
    inc_pnl / math.sqrt(len(ev) / (len(ev) - 1) * sum((v - sum(ev.values()) / len(ev)) ** 2 for v in ev.values()))))
print("credited in the same file (whole window, not per condition): maker rebate $%.0f (%.3f%% of maker usdc), taker rebate $%.0f (%.1f%% of taker fees paid), rewards $%.0f ; together %.3f%% of all usdc bought" % (
    other["MAKER_REBATE"], 100 * other["MAKER_REBATE"] / mk_usd, other["TAKER_REBATE"], 100 * other["TAKER_REBATE"] / tf, other["REWARD"],
    100 * (other["MAKER_REBATE"] + other["TAKER_REBATE"] + other["REWARD"]) / usd_all))
