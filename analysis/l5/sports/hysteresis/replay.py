"""Replay recorded two-venue quotes through the RN1 opening decision with the
hysteresis rule off and on. Identical inputs for both; no venue, no orders.
Input rows (venue_sampler.py): t, slug, ub/ua = PMUS best bid/ask,
ib/ia = international best bid/ask, about every 3 s.

Model, the same for both arms:
- the bot is always flat and decides an opening bid for each side each step
  (RN1.quotes with the marginal test, fair value = international mid, judged
  against its low/high over the last 10 s and not used until it has been
  within 1c for 10 s -- the engine's rules);
- a bid rests until the decision drops or reprices it;
- FILL: the venue's best price on that side moves THROUGH the resting bid
  (the adverse case only), filled at the bid's price; marked at the PMUS mid
  30 s, 2 min and 5 min later; the position is then dropped so each fill is
  judged on its own;
- with the rule on, a failing bid is kept inside the tolerance for at most
  KEEP_MAX_S, exactly as the engine does.
Counts cancels, resting time, fills and marks. Marks are not profit.
usage (from a checkout of the sports code): replay.py <quotes.jsonl> ..."""
import bisect, collections, json, sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from src import pmus_rn1_replica as R
T, STABLE_S, RANGE, KEEP_MAX_S = D("0.01"), 10.0, D("0.01"), 60.0
HZ = (30, 120, 300)
def load(paths):
    by = collections.defaultdict(list)
    for p in paths:
        for l in open(p):
            r = json.loads(l)
            if None in (r.get("ub"), r.get("ua"), r.get("ib"), r.get("ia")): continue
            if not (0 < r["ub"] < r["ua"] < 1 and 0 < r["ib"] < r["ia"] < 1): continue
            by[r["slug"]].append(r)
    return {s: sorted(v, key=lambda r: r["t"]) for s, v in by.items()}
def run(rows, tol):
    ts = [r["t"] for r in rows]
    mid = [(D(str(r["ub"])) + D(str(r["ua"]))) / 2 for r in rows]
    st = {"orders": 0, "cancels": 0, "rest_s": 0.0, "fills": 0, "kept_steps": 0,
          "marks": {h: [] for h in HZ}, "steps": 0}
    rest, since, hist = {}, {}, []
    for i, r in enumerate(rows):
        now = r["t"]
        dt = (now - ts[i - 1]) if i else 0.0
        if dt > 30:                       # a gap in the record: nothing rests through it
            st["cancels"] += len(rest); rest, since, hist = {}, {}, []
            continue
        st["steps"] += 1
        yb, ya = D(str(r["ub"])), D(str(r["ua"]))
        # fills first: did the price move through a resting bid since last step?
        for s in list(rest):
            side_bid = yb if s == "YES" else D(1) - ya
            if side_bid < rest[s]:
                st["fills"] += 1
                for h in HZ:
                    j = bisect.bisect_left(ts, now + h)
                    if j < len(ts) and ts[j] - (now + h) <= max(h, 30):
                        val = mid[j] if s == "YES" else D(1) - mid[j]
                        st["marks"][h].append(float(val - rest[s]))
                del rest[s]; since.pop(s, None)
        st["rest_s"] += dt * len(rest)
        fair = (D(str(r["ib"])) + D(str(r["ia"]))) / 2
        hist.append((now, fair))
        while len(hist) > 1 and now - hist[1][0] >= STABLE_S: hist.pop(0)
        vals = [m for _t, m in hist]
        lo, hi = min(vals), max(vals)
        settled = (now - hist[0][0]) >= STABLE_S and (hi - lo) <= RANGE
        c = {"edge_gate": True, "fair_yes": fair if settled else None, "fair_yes_lo": lo, "fair_yes_hi": hi,
             "adverse_ticks": 1, "risk_step_ticks": 1, "incentive_ticks": 0}
        if tol > 0:
            c.update(keep_tol_usd=tol, keep_px=dict(rest))
        q = R.quotes(yes_bid=yb, yes_ask=ya, tick=T, pos={"YES": 0, "NO": 0}, cost={"YES": 0, "NO": 0},
                     last_fill_t={}, now=now, min_qty=D(1), c=c)
        for s in ("YES", "NO"):
            tg = q.get(s)
            if tg and tg.get("kept"):
                st["kept_steps"] += 1
                if now - since.setdefault(s, now) > KEEP_MAX_S: tg = None
            else:
                since.pop(s, None)
            if tg is None:
                if s in rest: st["cancels"] += 1; del rest[s]
            elif rest.get(s) != tg["px"]:
                if s in rest: st["cancels"] += 1
                rest[s] = tg["px"]; st["orders"] += 1
    hours = sum(min(b - a, 30) for a, b in zip(ts, ts[1:]) if b - a <= 30) / 3600 or 1e-9
    st["hours"] = hours
    return st
def show(tag, st):
    h = st["hours"]
    m = {k: (sum(v) / len(v) * 100 if v else None) for k, v in st["marks"].items()}
    f = lambda x: "  n/a" if x is None else "%+5.1f" % x
    print("  %-4s orders/h %6.1f  cancels/h %6.1f  resting %4.0f%% of side-time  avg life %5.0fs  fills/h %5.2f (n %3d)  marks c: 30s %s  2m %s  5m %s  kept steps %d" % (
        tag, st["orders"] / h, st["cancels"] / h, 100 * st["rest_s"] / (2 * h * 3600),
        st["rest_s"] / max(1, st["orders"]), st["fills"] / h, st["fills"], f(m[30]), f(m[120]), f(m[300]), st["kept_steps"]))
data = load(sys.argv[1:])
tot = {}
for slug, rows in sorted(data.items()):
    print(slug, "| %d samples" % len(rows))
    for tag, tol in (("off", D(0)), ("on", D("0.005"))):
        st = run(rows, tol); show(tag, st)
        a = tot.setdefault(tag, {"orders": 0, "cancels": 0, "rest_s": 0.0, "fills": 0, "kept_steps": 0, "hours": 0.0, "marks": {h: [] for h in HZ}})
        for k in ("orders", "cancels", "rest_s", "fills", "kept_steps", "hours"): a[k] += st[k]
        for h in HZ: a["marks"][h] += st["marks"][h]
print("ALL MARKETS")
for tag in ("off", "on"): show(tag, tot[tag])
