"""Priority 1 + 3: full settlement reconciliation, and a REPRESENTATIVE
20-window reconstruction.

Priority 1. Every completed window on the tape, winners and losers alike,
with BUY cost, SELL proceeds, redemption and net. Resolution comes from
the venue's CLOB `winner` flag, never from whether the trader redeemed,
so losing windows are present rather than filtered out. Rebates are
reported SEPARATELY and never folded into a window's net: the venue
credits them as account-level rows that name no market, so attributing
them per window would be invention.

Priority 3. 20 windows chosen to be REPRESENTATIVE rather than first:
stratified across era x outcome x cost-basis integrity, taking the
window nearest each stratum's median net. The first-20 report is the
chronological companion to this one.

COST-BASIS INTEGRITY is the axis that matters most here. A window
reconciles when its redemption equals the tape's terminal inventory on
the winning outcome. Where it does not, inventory arrived outside the
trade tape -- a SPLIT mints both tokens for $1 and appears as no trade --
so the cost is understated and the net is not trustworthy. Reconciliation
status is NOT random: it is determined by whether SPLIT was used, which
is part of the strategy. So neither subset estimates the other, and this
report keeps them apart instead of averaging them.
"""
import collections, datetime, json, os, statistics, sys

D = os.environ.get("CAP_DIR") or "/home/user/kalshi-arb-bot/data/l5/competitor_0x8dxd_full"
PREFIX = "btc-updown-5m-"
AUGUST_FROM = 1780000000


def rows(name):
    p = os.path.join(D, name)
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            pass


def epoch_of(slug):
    try:
        return int(str(slug).rsplit("-", 1)[1])
    except Exception:
        return None


def U(e):
    return datetime.datetime.fromtimestamp(
        e, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


WINNER = {}
for r in rows("resolutions.jsonl"):
    if r.get("closed") and r.get("winner"):
        WINNER[str(r.get("slug"))] = r["winner"]

fills = collections.defaultdict(list)
redeem = collections.defaultdict(float)
redeem_sz = collections.defaultdict(float)
redeem_n = collections.Counter()
rebate = collections.Counter()
first_ts = None
seen = set()
for r in rows("activity.jsonl"):
    ts = r.get("timestamp")
    if ts is not None:
        ts = int(ts)
        first_ts = ts if first_ts is None or ts < first_ts else first_ts
    ty = r.get("type")
    #: account-level incentive rows. They name no market, so they are
    #: tallied on their own and never attributed to a window.
    if ty in ("MAKER_REBATE", "TAKER_REBATE", "REWARD", "REFERRAL_REWARD", "YIELD"):
        rebate[ty] += float(r.get("usdc_size") or r.get("size") or 0)
        continue
    slug = str(r.get("slug") or "")
    if not slug.startswith(PREFIX):
        continue
    if ty == "REDEEM":
        redeem[slug] += float(r.get("usdc_size") or 0)
        redeem_sz[slug] += float(r.get("size") or 0)
        redeem_n[slug] += 1
    elif ty == "TRADE":
        k = (r.get("transaction_hash"), r.get("token_id"), r.get("side"),
             round(float(r.get("size") or 0), 4))
        if k in seen:
            continue
        seen.add(k)
        fills[slug].append(r)
for s in fills:
    fills[s].sort(key=lambda r: int(r.get("timestamp") or 0))


def window(slug):
    e = epoch_of(slug)
    fs = fills.get(slug) or []
    win = WINNER.get(slug)
    if e is None or not fs or not win:
        return None
    if first_ts is not None and e <= first_ts:
        return None
    inv = collections.Counter()
    buy = sell = 0.0
    nb = ns = 0
    steps = []
    for f in fs:
        sz = float(f.get("size") or 0)
        u = f.get("usdc_size")
        c = float(u) if u is not None else float(f.get("price") or 0) * sz
        oc = str(f.get("outcome") or "")
        sd = str(f.get("side") or "")
        if sd == "BUY":
            inv[oc] += sz; buy += c; nb += 1
        elif sd == "SELL":
            inv[oc] -= sz; sell += c; ns += 1
        else:
            return None
        steps.append({"t": int(f.get("timestamp") or 0) - e, "side": sd,
                      "outcome": oc, "price": float(f.get("price") or 0),
                      "size": sz, "cash": c, "inv": dict(inv)})
    if any(v < -0.01 for v in inv.values()):
        return None
    held = inv.get(win, 0.0)
    ent = held * 1.00
    obs = redeem.get(slug)
    rec = None
    if obs is not None:
        rec = abs(obs - ent) <= max(0.02, 0.001 * max(obs, ent))
    u_, d_ = inv.get("Up", 0.0), inv.get("Down", 0.0)
    tot = abs(u_) + abs(d_)
    return {"slug": slug, "epoch": e, "winner": win, "steps": steps,
            "n_fills": len(fs), "n_buy": nb, "n_sell": ns,
            "buy_cost": buy, "sell_proceeds": sell,
            "entitlement": ent, "observed_redemption": obs,
            "n_redeems": redeem_n.get(slug, 0), "reconciled": rec,
            "terminal_inv": dict(inv), "held_winner": held,
            "matched": min(max(u_, 0.0), max(d_, 0.0)),
            "imbalance": (abs(u_ - d_) / tot) if tot else 0.0,
            "era": "MARCH" if e < AUGUST_FROM else "AUGUST",
            "net": ent + sell - buy}


recon = [w for w in (window(s) for s in sorted(fills, key=lambda s: epoch_of(s) or 0)) if w]

print("CAPTURE %s" % D)
print("  tape begins %s   ends %s" % (U(first_ts), U(max(w["epoch"] for w in recon))))
print("  BTC5m windows with fills %d   completed & accounted %d"
      % (len(fills), len(recon)))
print()
print("=" * 78)
print("PRIORITY 1 -- SETTLEMENT RECONCILIATION, ALL COMPLETED WINDOWS")
print("Losers are included: resolution is the venue's winner flag, not redemption.")
print()
hdr = ("era", "cost basis", "n", "BUY cost", "SELL proc", "redemption", "net", "win%")
print("%-7s %-13s %5s %11s %10s %12s %11s %6s" % hdr)
grp = collections.defaultdict(lambda: dict(n=0, b=0.0, s=0.0, r=0.0, net=0.0, w=0, l=0))
for w in recon:
    k = (w["era"], "reconciled" if w["reconciled"] is True else
         "UNRECONCILED" if w["reconciled"] is False else "no-redeem")
    g = grp[k]
    g["n"] += 1; g["b"] += w["buy_cost"]; g["s"] += w["sell_proceeds"]
    g["r"] += w["entitlement"]; g["net"] += w["net"]
    if w["net"] > 0.005: g["w"] += 1
    elif w["net"] < -0.005: g["l"] += 1
for k in sorted(grp):
    g = grp[k]
    print("%-7s %-13s %5d %11.0f %10.0f %12.0f %11.2f %5.1f%%" % (
        k[0], k[1], g["n"], g["b"], g["s"], g["r"], g["net"],
        100.0 * g["w"] / max(1, g["w"] + g["l"])))
print()
tr = collections.Counter()
for k in grp:
    tr["TRUSTWORTHY" if k[1] in ("reconciled", "no-redeem") else "NOT TRUSTWORTHY"] += grp[k]["net"]
print("  trading net, VERIFIED cost basis : $%+.2f" % tr["TRUSTWORTHY"])
print("  trading net, cost basis MISSING  : $%+.2f  <- not a measurement" % tr["NOT TRUSTWORTHY"])
print()
print("OBSERVABLE FEES AND REBATES (account-level rows; no market named, so")
print("never attributed to a window and never folded into a net above):")
for k, v in sorted(rebate.items(), key=lambda kv: -kv[1]):
    print("    %-17s $%10.2f" % (k, v))
print("    %-17s $%10.2f" % ("TOTAL", sum(rebate.values())))
print("  NOTE: no per-trade fee field exists on either v2 route; the TRADE row's")
print("  usdc_size equals price*size exactly. Trading fees are NOT OBSERVABLE,")
print("  which is not the same as zero.")
print()
print("=" * 78)
print("PRIORITY 3 -- 20 REPRESENTATIVE COMPLETED WINDOWS")
print()
strata = collections.defaultdict(list)
for w in recon:
    cb = ("verified" if w["reconciled"] is True or w["reconciled"] is None
          else "missing")
    strata[(w["era"], cb, "PROFIT" if w["net"] > 0 else "LOSS")].append(w)
order = sorted(strata, key=lambda k: (k[0], k[1], k[2]))
#: allocate 20 slots proportionally to how many windows each stratum holds,
#: with at least one each so no behaviour present in the tape is invisible
tot = sum(len(strata[k]) for k in order)
quota = {k: max(1, round(20.0 * len(strata[k]) / tot)) for k in order}
while sum(quota.values()) > 20:
    quota[max(quota, key=lambda k: quota[k])] -= 1
while sum(quota.values()) < 20:
    quota[max(quota, key=lambda k: len(strata[k]) / quota[k])] += 1
sel = []
for k in order:
    g = sorted(strata[k], key=lambda w: w["net"])
    q = quota[k]
    #: spread the picks across the stratum's net distribution rather than
    #: clustering on the median, so losers and winners both show their range
    idx = [int(round((i + 0.5) * len(g) / q)) for i in range(q)]
    for i in idx:
        sel.append(g[min(i, len(g) - 1)])
print("stratified allocation (%d windows):" % len(sel))
for k in order:
    print("   %-7s cost-basis=%-8s %-6s pool %5d -> %d picked"
          % (k[0], k[1], k[2], len(strata[k]), quota[k]))
print()
sel.sort(key=lambda w: w["epoch"])
print("%-26s %-8s %-4s %6s %5s %5s %9s %9s %9s %9s %7s %6s" % (
    "window", "era", "win", "fills", "buys", "sells", "BUYcost",
    "SELLproc", "redeem", "NET", "imbal%", "cost?"))
for w in sel:
    print("%-26s %-8s %-4s %6d %5d %5d %9.2f %9.2f %9.2f %+9.2f %6.1f%% %6s" % (
        w["slug"], w["era"], w["winner"][:4], w["n_fills"], w["n_buy"],
        w["n_sell"], w["buy_cost"], w["sell_proceeds"], w["entitlement"],
        w["net"], 100.0 * w["imbalance"],
        "OK" if w["reconciled"] is True else
        ("none" if w["reconciled"] is None else "MISS")))
print()
print("  cost? OK = redemption reconciles to the tape; MISS = inventory arrived")
print("  outside the tape (SPLIT), so that row's NET is understated-cost and not")
print("  trustworthy; none = no redemption row (held only the losing side).")
if len(sys.argv) > 1 and sys.argv[1] == "full":
    print()
    for i, w in enumerate(sel, 1):
        print("=== %2d. %s  %s  winner=%s  NET $%+.2f  cost basis %s ===" % (
            i, w["slug"], U(w["epoch"]), w["winner"], w["net"],
            "OK" if w["reconciled"] is True else
            ("none" if w["reconciled"] is None else "MISSING")))
        for st in w["steps"]:
            print("   t+%-5d %-5s %-5s %7.4f %10.2f %11.4f  %s" % (
                st["t"], st["side"], st["outcome"], st["price"], st["size"],
                st["cash"], st["inv"]))
        print("   fills=%d (buy %d / sell %d)  BUY $%.2f  SELL $%.2f  redeem $%.2f"
              % (w["n_fills"], w["n_buy"], w["n_sell"], w["buy_cost"],
                 w["sell_proceeds"], w["entitlement"]))
        print("   terminal inv %s  matched pair %.2f  imbalance %.1f%%"
              % (w["terminal_inv"], w["matched"], 100.0 * w["imbalance"]))
        print("   maker/taker: UNOBSERVABLE (no role field on the v2 row)")
        print()
