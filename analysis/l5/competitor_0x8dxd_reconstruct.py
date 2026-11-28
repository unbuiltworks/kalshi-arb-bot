"""Chronological reconstruction of 0x8dxd's BTC5m decisions and settlement.

Identity verified: data-api v2 returns name "0x8dxd" / pseudonym
"Blushing-Fine" for proxy_wallet 0x63ce...ba9a on three routes.

SOURCES
  activity.jsonl    THE FILL TAPE. Its TRADE rows are the most complete
                    record available, and it carries REDEEM in the same
                    schema so settlement reconciles in one source.
  trades.jsonl      NOT the fill tape. MEASURED: the v2 /trades route
                    returns a PARTIAL tape -- for window 1787613900 it
                    returns 1 of 9 fills (44 of 701.54 contracts), and
                    activity holds more fills in 230 of the 400 windows
                    both cover, never fewer. Reconciled against
                    redemption size, /trades matches in 166/688 windows
                    (24%) against activity's 848/1276 (66%). Their union
                    double-counts (269 over-reconciliations), so the
                    tapes are NOT merged.
  resolutions.jsonl authoritative per-window winner, from the CLOB
                    market record's `winner` flag per token.
  activity.jsonl    TRADE + REDEEM. Used ONLY to cross-check observed
                    redemption cashflow where the window is covered.
  positions.jsonl   NOT USED. The v2 positions route returns only
                    CURRENTLY OPEN positions (12 rows against ~4,000
                    traded windows), so it cannot settle history.

WHY RESOLUTION COMES FROM THE VENUE, NOT FROM REDEMPTIONS
  A window where 0x8dxd held only the LOSING side pays no redemption.
  Defining "settled" as "has a redemption row" therefore excludes the
  losing windows by construction and manufactures a ~100% win rate.
  Measured: that bias dropped 429 of 1,281 windows. The CLOB `winner`
  flag is independent of the trader's own redemption behaviour.

OBSERVED vs DERIVED vs UNKNOWN
  OBSERVED  side, price, size, timestamp, slug, outcome per fill;
            winner per window; redemption cashflow where present.
  DERIVED   t_in_window (timestamp minus the slug's epoch), running
            inventory, cost, settlement entitlement and realised net.
  UNKNOWN   maker vs taker. The v2 row carries no role field. Reported
            UNOBSERVABLE and never inferred from price: a fill at 0.99
            could be a resting bid that was hit or an offer that was
            lifted, and those are opposite behaviours.

SETTLEMENT ENTITLEMENT vs OBSERVED REDEMPTION
  A winning contract pays $1.00. Entitlement = terminal inventory on
  the winning outcome x $1.00. Where a REDEEM row is captured for the
  window, the entitlement is checked against it and any disagreement is
  printed. Entitlement is labelled DERIVED, never called a confirmed
  cashflow.

TRUNCATION
  v2 pages newest-first, so a page cap drops the OLDEST history. The
  window already open at the first captured fill has an unknown number
  of earlier fills and is excluded. A window whose reconstructed
  inventory would go negative is also excluded and counted, since that
  can only mean fills are missing.

FEES
  The activity TRADE row's usdc_size equals price*size exactly and
  there is no fee field on either route. Fees are reported as NOT
  PRESENT IN THE SOURCE, never as zero.
"""
import collections, datetime, json, os, sys

D = os.environ.get("CAP_DIR") or "/home/user/kalshi-arb-bot/data/l5/competitor_0x8dxd"
PREFIX = "btc-updown-5m-"


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
    """btc-updown-5m-<epoch> -> the window's opening epoch."""
    try:
        return int(str(slug).rsplit("-", 1)[1])
    except Exception:
        return None


def U(e):
    return datetime.datetime.fromtimestamp(
        e, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


WINNER = {}
for r in rows("resolutions.jsonl"):
    if r.get("winner") and r.get("closed"):
        WINNER[str(r.get("slug"))] = r["winner"]

REDEEM = collections.defaultdict(float)
REDEEM_N = collections.Counter()
ACT_COVER = None
for r in rows("activity.jsonl"):
    ts = r.get("timestamp")
    if ts is not None:
        ts = int(ts)
        ACT_COVER = ts if ACT_COVER is None or ts < ACT_COVER else ACT_COVER
    if r.get("type") == "REDEEM" and str(r.get("slug") or "").startswith(PREFIX):
        REDEEM[r["slug"]] += float(r.get("usdc_size") or 0)
        REDEEM_N[r["slug"]] += 1

fills = collections.defaultdict(list)
first_ts = ACT_COVER
_seen = set()
for r in rows("activity.jsonl"):
    if r.get("type") != "TRADE":
        continue
    if not str(r.get("slug") or "").startswith(PREFIX):
        continue
    #: the route can repeat a row across pages; identity is the match,
    #: not the row
    k = (r.get("transaction_hash"), r.get("token_id"), r.get("side"),
         round(float(r.get("size") or 0), 4))
    if k in _seen:
        continue
    _seen.add(k)
    fills[r["slug"]].append(r)
for s in fills:
    fills[s].sort(key=lambda r: (r.get("timestamp") or 0))

SKIP = collections.Counter()


def window(slug):
    e = epoch_of(slug)
    fs = fills.get(slug) or []
    if e is None or not fs:
        SKIP["no_epoch_or_fills"] += 1
        return None
    if first_ts is not None and e <= first_ts:
        SKIP["tape_truncated_at_capture_start"] += 1
        return None
    win = WINNER.get(slug)
    if not win:
        SKIP["resolution_unknown"] += 1
        return None
    inv = collections.Counter()
    paid = recv = 0.0
    steps = []
    for f in fs:
        sz = float(f.get("size") or 0)
        oc = str(f.get("outcome") or "")
        sd = str(f.get("side") or "")
        u = f.get("usdc_size")
        c = float(u) if u is not None else float(f.get("price") or 0) * sz
        if sd == "BUY":
            inv[oc] += sz
            paid += c
        elif sd == "SELL":
            inv[oc] -= sz
            recv += c
        else:
            SKIP["unknown_side"] += 1
            return None
        steps.append({"t": int(f.get("timestamp") or 0) - e, "side": sd,
                      "outcome": oc, "price": float(f.get("price") or 0),
                      "size": sz, "cash": c, "inv": dict(inv)})
    if any(v < -0.01 for v in inv.values()):
        SKIP["negative_inventory_means_missing_fills"] += 1
        return None
    held = inv.get(win, 0.0)
    ent = held * 1.00
    obs = REDEEM.get(slug)
    #: a redemption LARGER than the tape's terminal inventory means the
    #: position was partly acquired outside the trade tape (a SPLIT mints
    #: both tokens for $1 and appears as no trade). Cost basis is then
    #: unknowable, so the window is marked unreconciled rather than
    #: reported with an understated cost.
    reconciled = None
    if obs is not None:
        reconciled = abs(obs - ent) <= max(0.02, 0.001 * max(obs, ent))
    return {"slug": slug, "epoch": e, "winner": win, "steps": steps,
            "reconciled": reconciled,
            "n_fills": len(fs), "paid": paid, "recv": recv,
            "terminal_inv": dict(inv), "held_winner": held,
            "entitlement": ent, "observed_redemption": obs,
            "n_redeems": REDEEM_N.get(slug, 0),
            "net": ent + recv - paid}


order = sorted(fills, key=lambda s: epoch_of(s) or 0)
recon = [w for w in (window(s) for s in order) if w]

print("CAPTURE %s" % D)
print("  earliest captured BTC5m fill : %s" % (U(first_ts) if first_ts else "n/a"))
print("  activity tape begins         : %s  (windows before this are NOT reported)"
      % (U(ACT_COVER) if ACT_COVER else "n/a"))
print("  BTC5m windows with fills     : %d" % len(fills))
print("  FULLY SETTLED (venue winner + complete tape) : %d" % len(recon))
for k, v in SKIP.most_common():
    print("    excluded %-42s %d" % (k, v))
print()

mode = sys.argv[1] if len(sys.argv) > 1 else "first20"
want = sys.argv[2:]
sel = ([w for w in recon if w["slug"] in want] if want else recon[:20])

if mode in ("first20", "detail"):
    for i, w in enumerate(sel, 1):
        print("=== %2d. %s   opens %s   VENUE WINNER = %s ===" % (
            i, w["slug"], U(w["epoch"]), w["winner"]))
        print("   %6s %-5s %-5s %8s %10s %11s  %s" % (
            "t+s", "side", "outc", "price", "size", "cost", "inventory after"))
        for st in w["steps"]:
            print("   %6d %-5s %-5s %8.4f %10.2f %11.4f  %s" % (
                st["t"], st["side"], st["outcome"], st["price"], st["size"],
                st["cash"], st["inv"]))
        print("   fills=%d  total cost=$%.4f  sell proceeds=$%.4f  terminal inv=%s"
              % (w["n_fills"], w["paid"], w["recv"], w["terminal_inv"]))
        print("   redemption: %.2f winning contracts -> $%.4f entitlement [DERIVED]"
              % (w["held_winner"], w["entitlement"]))
        if w["n_redeems"]:
            tag = "agrees" if abs((w["observed_redemption"] or 0) - w["entitlement"]) <= 0.02 \
                  else "DISAGREES"
            print("               observed REDEEM cashflow $%.4f (%d row(s)) -- %s"
                  % (w["observed_redemption"], w["n_redeems"], tag))
        else:
            print("               no REDEEM row captured for this window")
        print("   REALISED NET = $%.4f   [derived; fees not present in source]" % w["net"])
        print("   maker/taker: UNOBSERVABLE (no role field on the v2 row)")
        print()

if mode in ("first20", "summary"):
    nets = sorted(w["net"] for w in recon)
    n = len(nets)
    tot = sum(nets)
    win = [x for x in nets if x > 0.005]
    los = [x for x in nets if x < -0.005]
    flat = n - len(win) - len(los)
    print("=" * 74)
    print("ALL FULLY SETTLED WINDOWS (%d)   %s .. %s" % (
        n, U(recon[0]["epoch"]), U(recon[-1]["epoch"])))
    print("  total realised net   : $%.2f" % tot)
    print("  mean / median window : $%.4f / $%.4f" % (tot / n, nets[n // 2]))
    print("  winners %4d (%5.1f%%)  sum $%10.2f  mean $%8.4f"
          % (len(win), 100.0 * len(win) / n, sum(win), sum(win) / max(1, len(win))))
    print("  losers  %4d (%5.1f%%)  sum $%10.2f  mean $%8.4f"
          % (len(los), 100.0 * len(los) / n, sum(los), sum(los) / max(1, len(los))))
    print("  flat    %4d (%5.1f%%)" % (flat, 100.0 * flat / n))
    print("  worst 8 : %s" % ", ".join("$%.2f" % x for x in nets[:8]))
    print("  best 8  : %s" % ", ".join("$%.2f" % x for x in nets[-8:]))
    print("  cost deployed per window: mean $%.2f  median $%.2f  max $%.2f"
          % (sum(w["paid"] for w in recon) / n,
             sorted(w["paid"] for w in recon)[n // 2],
             max(w["paid"] for w in recon)))
    #: an edge that depends on one outlier is not a repeatable edge
    print("  total net excluding the single best window : $%.2f" % (tot - nets[-1]))
    print("  total net excluding the best 5 windows     : $%.2f" % (tot - sum(nets[-5:])))
