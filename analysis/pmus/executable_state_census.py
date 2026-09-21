"""EXECUTABLE PMUS L5-STATE COUNT from the corrected live habitat.

READ-ONLY. Builds a real observation window off the gateway exactly as the
durable collector does (same discovery, same bbo fields), then runs the
EXISTING sequential L5 decision layer unchanged: analyze_market ->
_proposal/plan_action -> open_admission. No gate is relaxed and no fill is
invented; the output is a census of refusal reasons.
"""
import sys, time, json, collections, threading, queue
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import l5_live_habitat_service as S
from src import pmus_sports_l5_decision as DEC
from src import pmus_status_report as ST
from src import pmus_l5_persistence_v2 as PV2

SWEEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 12
CAD = 8.0

sel, livemap = S.pmus_discover()
live = [r for r in sel if livemap.get(r["event"], (False,))[0]]
print("slate: %d discovered, %d in LIVE events (the corrected coverage)"
      % (len(sel), len(live)))
if not live:
    print("no live markets right now"); raise SystemExit

by_slug = collections.defaultdict(list)
for sweep in range(SWEEPS):
    t0 = time.time()
    qq = queue.Queue()
    for r in live: qq.put(r)
    lock = threading.Lock()
    def w():
        while True:
            try: r = qq.get_nowait()
            except queue.Empty: return
            try:
                b = S.get(S.GATEWAY + "/v1/markets/%s/bbo" % r["slug"])["marketData"]
            except Exception:
                continue
            row = {"t": S.iso(t0), "slug": r["slug"],
                   "bid": S.qf(b.get("bestBid")), "ask": S.qf(b.get("bestAsk")),
                   "bid_touch_qty": S.qf(b.get("bidShares")),
                   "ask_touch_qty": S.qf(b.get("askShares")),
                   "ask_book_missing": S.qf(b.get("bestAsk")) is None,
                   "shares_traded_cum": S.qf(b.get("sharesTraded"))}
            with lock: by_slug[r["slug"]].append(row)
    ths = [threading.Thread(target=w) for _ in range(12)]
    [t.start() for t in ths]; [t.join() for t in ths]
    d = CAD - (time.time() - t0)
    if d > 0 and sweep < SWEEPS - 1: time.sleep(d)
    print("  sweep %2d/%d  slugs with rows: %d" % (sweep+1, SWEEPS, len(by_slug)), flush=True)

now = time.time()
model = PV2.load_v2()
reasons = collections.Counter()
eligible = []
for slug, obs in by_slug.items():
    fresh = ST.analyze_market(obs, now, 900.0)
    ages = [o for o in obs]
    last_age = now - max((DEC._ts(o) if hasattr(DEC, "_ts") else 0) or 0 for o in obs) \
        if hasattr(DEC, "_ts") else 0.0
    try:
        last_age = now - max(ST._ts(o) or 0 for o in obs)
    except Exception:
        last_age = 0.0
    inv = DEC.Inventory()
    bid = obs[-1].get("bid")
    if bid is None:
        reasons["one_sided_book(no bid)"] += 1; continue
    prop = DEC._proposal(model, float(bid), inv, "")
    if prop.get("off_distribution"):
        probe = {"decision": "no_action", "reason": "v2_off_distribution"}
    else:
        bk = {"bid": __import__("decimal").Decimal(str(obs[-1]["bid"])),
              "ask": __import__("decimal").Decimal(str(obs[-1]["ask"] or 1)),
              "t": now}
        probe = DEC.plan_action(prop["recommended_relation"], inv, bk)
    why = DEC.open_admission(fresh, last_age, probe)
    if why is None:
        eligible.append((slug, obs[-1]["bid"], obs[-1]["ask"],
                         fresh.get("q1_depth_support")))
        reasons["ELIGIBLE"] += 1
    else:
        reasons[why.split(":")[0]] += 1

print("\n=== EXECUTABLE PMUS L5-STATE CENSUS (%d live markets, %d sweeps) ==="
      % (len(by_slug), SWEEPS))
for k, v in reasons.most_common():
    print("  %-34s %4d" % (k, v))
print("\nEXECUTABLE (all four OPEN gates clear): %d" % len(eligible))
for s, b, a, q in eligible[:15]:
    print("   %-44s bid=%s ask=%s q1_depth=%s" % (s[:44], b, a, q))
