"""CHARACTERIZE the executable PMUS L5 states. READ-ONLY, no orders.

Extends executable_state_census with, per eligible market: market type, spread,
touch depth both sides, the model's action, the proposed side/price, and the
subsequent HYPOTHETICAL maker-fill opportunity. No gate is added or relaxed;
this only describes what the existing gates already admit.

"Hypothetical maker-fill opportunity" is SHADOW: whether the opposing touch
later reached the resting price during the observation window. It is not a
fill, not a submission, and never realized.
"""
import sys, time, json, collections, threading, queue
from decimal import Decimal as D
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import l5_live_habitat_service as S
from src import pmus_sports_l5_decision as DEC
from src import pmus_status_report as ST
from src import pmus_l5_persistence_v2 as PV2

SWEEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
CAD = 8.0

sel, livemap = S.pmus_discover()
live = [r for r in sel if livemap.get(r["event"], (False,))[0]]
print("slate: %d discovered, %d in LIVE events" % (len(sel), len(live)), flush=True)
meta = {r["slug"]: r for r in live}
by = collections.defaultdict(list)
for sw in range(SWEEPS):
    t0 = time.time(); qq = queue.Queue()
    for r in live: qq.put(r)
    lk = threading.Lock()
    def w():
        while True:
            try: r = qq.get_nowait()
            except queue.Empty: return
            try: b = S.get(S.GATEWAY + "/v1/markets/%s/bbo" % r["slug"])["marketData"]
            except Exception: continue
            with lk:
                by[r["slug"]].append({
                    "t": S.iso(t0), "slug": r["slug"],
                    "bid": S.qf(b.get("bestBid")), "ask": S.qf(b.get("bestAsk")),
                    "bid_touch_qty": S.qf(b.get("bidShares")),
                    "ask_touch_qty": S.qf(b.get("askShares")),
                    "ask_book_missing": S.qf(b.get("bestAsk")) is None,
                    "shares_traded_cum": S.qf(b.get("sharesTraded"))})
    ts = [threading.Thread(target=w) for _ in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    d = CAD - (time.time() - t0)
    if d > 0 and sw < SWEEPS - 1: time.sleep(d)
print("collected %d slugs x %d sweeps" % (len(by), SWEEPS), flush=True)

now = time.time(); model = PV2.load_v2()
rows, reasons = [], collections.Counter()
for slug, obs in by.items():
    fresh = ST.analyze_market(obs, now, 900.0)
    try: last_age = now - max(ST._ts(o) or 0 for o in obs)
    except Exception: last_age = 0.0
    inv = DEC.Inventory(); last = obs[-1]
    if last.get("bid") is None or last.get("ask") is None:
        reasons["one_sided_book"] += 1; continue
    prop = DEC._proposal(model, float(last["bid"]), inv, "")
    if prop.get("off_distribution"):
        probe = {"decision": "no_action", "reason": "v2_off_distribution"}
    else:
        bk = {"bid": D(str(last["bid"])), "ask": D(str(last["ask"])), "t": now}
        probe = DEC.plan_action(prop["recommended_relation"], inv, bk)
    why = DEC.open_admission(fresh, last_age, probe)
    if why is not None:
        reasons[why.split(":")[0]] += 1; continue
    reasons["ELIGIBLE"] += 1
    side_yes = probe.get("_side_yes")
    px = float(probe.get("_px")) if probe.get("_px") is not None else None
    # SHADOW: did the opposing touch later reach the proposed resting price?
    reached, first_s = False, None
    if px is not None:
        t0o = ST._ts(obs[0]) or 0
        for o in obs:
            if side_yes and o.get("ask") is not None and float(o["ask"]) <= px + 1e-9:
                reached = True; first_s = (ST._ts(o) or 0) - t0o; break
            if (not side_yes) and o.get("bid") is not None and (1 - float(o["bid"])) <= px + 1e-9:
                reached = True; first_s = (ST._ts(o) or 0) - t0o; break
    m = meta.get(slug, {})
    rows.append({
        "slug": slug, "league": m.get("league"),
        "type": m.get("sports_type") or m.get("type"),
        "bid": last["bid"], "ask": last["ask"],
        "spread_c": round((last["ask"] - last["bid"]) * 100, 1),
        "bid_shares": last.get("bid_touch_qty"), "ask_shares": last.get("ask_touch_qty"),
        "relation": probe.get("relation"), "side": "YES" if side_yes else "NO",
        "px": px, "shadow_touch_reached": reached, "shadow_reach_s": first_s})

print("\n=== CENSUS ===")
for k, v in reasons.most_common(): print("  %-30s %4d" % (k, v))
print("\n=== PROFILE OF THE %d EXECUTABLE STATES ===" % len(rows))
print("%-42s %-9s %-7s %5s %9s %9s %-8s %-4s %6s %8s"
      % ("slug", "type", "spread", "bid", "bid_sh", "ask_sh", "relation", "side", "px", "shadow"))
for r in sorted(rows, key=lambda x: (x["type"] or "", -(x["spread_c"] or 0))):
    print("%-42s %-9s %5.1fc %5s %9s %9s %-8s %-4s %6s %8s"
          % (r["slug"][:42], (r["type"] or "?")[:9], r["spread_c"], r["bid"],
             r["bid_shares"], r["ask_shares"], (r["relation"] or "?")[:8],
             r["side"], r["px"], ("reach %ss" % r["shadow_reach_s"]) if r["shadow_touch_reached"] else "-"))
print("\n=== AGGREGATES ===")
byt = collections.defaultdict(list)
for r in rows: byt[r["type"] or "?"].append(r)
print("%-26s %5s %9s %11s %12s" % ("market type", "n", "med_spr", "med_bid_sh", "shadow_reach"))
import statistics as st
for t, rs in sorted(byt.items(), key=lambda kv: -len(kv[1])):
    sp = st.median([x["spread_c"] for x in rs])
    bs = st.median([x["bid_shares"] or 0 for x in rs])
    rc = sum(1 for x in rs if x["shadow_touch_reached"])
    print("%-26s %5d %8.1fc %11.0f %6d/%d" % (t[:26], len(rs), sp, bs, rc, len(rs)))
print("\nrelations:", dict(collections.Counter(r["relation"] for r in rows)))
print("sides    :", dict(collections.Counter(r["side"] for r in rows)))
tot = sum(1 for r in rows if r["shadow_touch_reached"])
print("\nSHADOW/HYPOTHETICAL opposing-touch reach: %d/%d (%.0f%%) within %ds of observation."
      % (tot, len(rows), 100.0*tot/max(1,len(rows)), int(SWEEPS*CAD)))
print("NOT a fill, NOT submitted, NOT realized.")
