"""Would a resting maker completion bid BELOW 0.25 actually have filled?

A displayed level is not a fill. The only evidence available without a print
endpoint is the book's own subsequent motion, so this requires the market to
come DOWN to our price before counting anything:

  TOUCHED  a later sample in the same window shows the complement's ask at or
           below our resting bid. Someone was willing to sell there. We may
           still be behind the queue at that price.
  SWEPT    a later sample shows the ask STRICTLY BELOW our bid. The seller
           went through our level, which is the conservative read: a sweep
           past our price is much better evidence that our own order traded.

Both are reported. SWEPT is the number any decision should use.

Source: `L5_SHADOW_DEFENSIVE_DECISION`, which carries, per 5s sample and per
shadow variant, `maker_complement_own` (the price our engine would REST at,
two ticks behind the touch) and `exit_price_own` (the taker price = the
complement's ask). Both are the engine's own figures off the authenticated
book.
"""
from __future__ import annotations
import collections, datetime as dt, glob, json
from decimal import Decimal as D, ROUND_CEILING

TOOLS = ("/root/.claude/projects/-home-user-kalshi-arb-bot/"
         "1ec9ca12-5dab-5253-8ef5-126eaccc8b25/tool-results/"
         "mcp-Railway-get-logs-*.txt")
TAKER_THETA = D("0.0695")
CHEAP = D("0.25")

def fee(q, p):
    if q <= 0 or p <= 0 or p >= 1: return D(0)
    return (TAKER_THETA*q*p*(D(1)-p)).quantize(D("0.01"),
                                               rounding=ROUND_CEILING)

def slug_start(s):
    try: d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz")
    except ValueError: return None
    return d.replace(tzinfo=dt.timezone.utc).timestamp()

seen = set(); byw = collections.defaultdict(list)
for p in sorted(glob.glob(TOOLS)):
    try: doc = json.load(open(p))
    except Exception: continue
    for e in doc.get("deploy") or ():
        m = e.get("message") or ""
        if "L5_SHADOW_DEFENSIVE_DECISION" not in m: continue
        i = m.find("{")
        try: d = json.loads(m[i:])
        except Exception: continue
        ts = e.get("timestamp")
        k = (ts, d.get("slug"), d.get("shadow_variant"))
        if k in seen: continue
        seen.add(k)
        slug = str(d.get("slug") or ""); ws = slug_start(slug)
        if ws is None: continue
        try:
            mk = D(str(d["maker_complement_own"]))
            tk = D(str(d["exit_price_own"]))
            q = D(str(d["unmatched_qty"]))
            b = D(str(d["first_leg_own_cost"]))
        except Exception: continue
        t = dt.datetime.fromisoformat(str(ts).replace("Z","+00:00")).timestamp()
        byw[(slug, str(d.get("shadow_variant")))].append(
            {"t": round(t-ws,1), "mk": mk, "tk": tk, "q": q, "b": b})

print("windows x variants with a defensive series: %d" % len(byw))
print("samples: %d\n" % sum(len(v) for v in byw.values()))

elig = touched = swept = 0
fill_lat = []; maker_pairs = []; taker_pairs = []; unfilled = []
for (slug, var), ss in byw.items():
    ss.sort(key=lambda s: s["t"])
    for i, s in enumerate(ss):
        if s["mk"] >= CHEAP:        # only the out-of-the-money states
            continue
        elig += 1
        B = s["mk"]
        later = ss[i+1:]
        hit = next((x for x in later if x["tk"] <= B), None)
        sw  = next((x for x in later if x["tk"] <  B), None)
        if hit: touched += 1
        if sw:
            swept += 1
            fill_lat.append(sw["t"] - s["t"])
            maker_pairs.append(s["b"] + B)
        else:
            unfilled.append(s)
        taker_pairs.append(s["b"] + s["tk"]
                           + fee(s["q"], s["tk"]) / s["q"] if s["q"] else D(0))

def q3(v, n, f="%.4f"):
    if not v: print("   %-34s (none)" % n); return
    v = sorted(v)
    print("   %-34s p10=%s median=%s p90=%s" % (
        n, f % v[len(v)//10], f % v[len(v)//2], f % v[len(v)*9//10]))

print("=== ELIGIBILITY AND FILL EVIDENCE ===")
print("   samples with a sub-0.25 maker completion price: %d" % elig)
if elig:
    print("   TOUCHED  (ask later <= our bid): %d  (%.1f%%)"
          % (touched, 100.0*touched/elig))
    print("   SWEPT    (ask later <  our bid): %d  (%.1f%%)  <- conservative"
          % (swept, 100.0*swept/elig))
    print("   never reached (unfilled):        %d  (%.1f%%)"
          % (len(unfilled), 100.0*len(unfilled)/elig))
    q3(fill_lat, "seconds until swept", "%.0f")
    print("\n=== PASSIVE vs TAKER COMPLETION, after real fees ===")
    q3(maker_pairs, "pair if the maker bid filled")
    q3(taker_pairs, "pair if we crossed, incl. taker fee")
    if maker_pairs and taker_pairs:
        mm = sorted(maker_pairs)[len(maker_pairs)//2]
        tt = sorted(taker_pairs)[len(taker_pairs)//2]
        print("   median advantage of waiting: %+.4f per contract" % (tt-mm))
    print("\n=== THE COST OF AN UNFILLED BID ===")
    print("   an unfilled completion leaves the residual in place. Its cost")
    print("   is the residual's own exposure, which is NOT avoided by having")
    print("   quoted: %d samples, median unmatched qty %s"
          % (len(unfilled),
             sorted(x["q"] for x in unfilled)[len(unfilled)//2]
             if unfilled else "n/a"))
