"""Chronological reconstruction of 0x8dxd's BTC5m decisions.

Identity verified: data-api v2 returns name "0x8dxd" / pseudonym
"Blushing-Fine" for proxy_wallet 0x63ce...ba9a on three routes.

WHAT IS OBSERVED vs INFERRED, kept apart throughout:
  OBSERVED  side, price, size, timestamp, slug, outcome -- venue rows
  DERIVED   t_in_window (fill timestamp minus the slug's epoch), running
            inventory, cost, and realised net. Arithmetic on observed
            values, labelled as derived.
  UNKNOWN   maker vs taker. The v2 trade row carries no role field. It is
            reported UNOBSERVABLE and never inferred from price: a fill
            at 0.99 could be a resting bid that was hit or an offer that
            was lifted, and those are opposite behaviours.
"""
import collections, json, os, sys

D = "/home/user/kalshi-arb-bot/data/l5/competitor_0x8dxd"


def rows(name):
    p = os.path.join(D, name)
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def epoch_of(slug):
    """btc-updown-5m-<epoch> -> the window's opening epoch."""
    try:
        return int(str(slug).rsplit("-", 1)[1])
    except Exception:
        return None


trades = [r for r in rows("trades.jsonl")
          if str(r.get("slug", "")).startswith("btc-updown-5m-")]
pos = {}
for r in rows("positions.jsonl"):
    s = r.get("slug")
    if str(s).startswith("btc-updown-5m-"):
        pos.setdefault(s, []).append(r)

print("BTC5m trade rows: %d   windows with positions: %d" % (len(trades), len(pos)))
if not trades:
    print("no BTC5m trades captured yet"); sys.exit(0)

bywin = collections.defaultdict(list)
for t in trades:
    bywin[t["slug"]].append(t)
for s in bywin:
    bywin[s].sort(key=lambda r: (r.get("timestamp") or 0))

#: a window is FULLY SETTLED when its position rows report a terminal
#: size of zero with a realised value -- never from the price alone
def settled(slug):
    ps = pos.get(slug) or []
    if not ps:
        return None
    tot = 0.0
    for p in ps:
        cs = p.get("current_size")
        if cs is None:
            return None
        try:
            tot += abs(float(cs))
        except Exception:
            return None
    return tot == 0.0


order = sorted(bywin, key=lambda s: epoch_of(s) or 0)
done = [s for s in order if settled(s)]
print("windows traded: %d   fully settled: %d" % (len(order), len(done)))
print()
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
for i, slug in enumerate(done[:N], 1):
    e = epoch_of(slug)
    fills = bywin[slug]
    print("=== %2d. %s   open=%s ===" % (i, slug, e))
    inv = collections.Counter()
    cost = 0.0
    print("   %-6s %-5s %-6s %-9s %-9s %-10s %s" % (
        "t_in_w", "side", "outc", "price", "size", "cost", "inventory after"))
    for f in fills:
        t = (f.get("timestamp") or 0) - (e or 0)
        px = float(f.get("price") or 0)
        sz = float(f.get("size") or 0)
        oc = str(f.get("outcome") or "")
        sd = str(f.get("side") or "")
        c = px * sz * (1 if sd == "BUY" else -1)
        cost += c
        inv[oc] += sz * (1 if sd == "BUY" else -1)
        print("   %-6s %-5s %-6s %-9.4f %-9.2f %-10.4f %s" % (
            t, sd, oc, px, sz, c, dict(inv)))
    ps = pos.get(slug) or []
    red = sum(float(p.get("current_value") or 0) for p in ps)
    paid = sum(float(p.get("entry_cost_usdc") or 0) for p in ps)
    fees = sum(float(p.get("entry_fees_usdc") or 0) for p in ps)
    print("   fills=%d  gross cost=%.4f  entry_cost=%.4f  fees=%.4f  redemption=%.4f"
          % (len(fills), cost, paid, fees, red))
    print("   maker/taker: UNOBSERVABLE (no role field on the v2 trade row)")
    print()
