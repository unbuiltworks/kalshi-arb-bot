"""Late-window settlement calibration, BTC15m, cluster-robust.

READ-ONLY. Joins recorded prediction-market books to AUTHORITATIVE
settlement outcomes and asks one question: does buying a side at the
CONTEMPORANEOUS EXECUTABLE price, late in the window, have positive
expected value after a conservative taker fee?

EXECUTABLE, NOT MID. To buy UP you pay the ASK. To buy DOWN you SELL YES at
the BID, so your own cost is 1 - bid. Midpoints and last trades are not
prices anyone can transact at and are not used.

CLUSTERED BY WINDOW. Ticks within one window are not independent -- they
share one settlement draw. So every statistic is computed per WINDOW first,
then aggregated across windows, and the standard error comes from the
between-window spread. Treating 300k ticks as 300k observations would
shrink every interval by ~100x and is the single easiest way to manufacture
a false edge here.
"""
import glob, json, math, statistics as st, datetime as dt
from collections import defaultdict

FEE_THETA = 0.0695          # PMUS published taker theta, verified in tests
NS = 1e9

def parse_expiry(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

books = defaultdict(list)   # market -> [(t, bid, ask)]
settle = {}                 # market -> "yes"/"no"
expiry = {}                 # market -> epoch seconds
strike = {}

for fp in sorted(glob.glob("data/ref_lag/*.jsonl")):
    with open(fp) as fh:
        for line in fh:
            try: d = json.loads(line)
            except Exception: continue
            k = d.get("kind"); m = d.get("market", "") or ""
            if "BTC15M" not in m: continue
            if k == "pbook":
                a, b = d.get("a"), d.get("b")
                if not a or not b: continue
                try: a, b = float(a), float(b)
                except Exception: continue
                if not (0 < b < a < 1): continue      # need a sane 2-sided book
                books[m].append((d["t_ns"] / NS, b, a))
            elif k == "settlement":
                rs = d.get("resolution_side")
                if rs in ("yes", "no"):
                    settle[m] = rs
                    if d.get("expiry"): expiry[m] = parse_expiry(d["expiry"])
            elif k == "window":
                if d.get("expiry"): expiry.setdefault(m, parse_expiry(d["expiry"]))
                if d.get("strike"):
                    try: strike[m] = float(d["strike"])
                    except Exception: pass

usable = sorted([m for m in books if m in settle and m in expiry],
                key=lambda m: expiry[m])
print("BTC15m windows with book+settlement+expiry : %d" % len(usable))
print("total 2-sided book ticks in those windows  : %d"
      % sum(len(books[m]) for m in usable))
cov_strike = sum(1 for m in usable if m in strike)
print("windows with a recorded strike             : %d/%d" % (cov_strike, len(usable)))
up_rate = sum(1 for m in usable if settle[m] == "yes") / max(1, len(usable))
print("base rate P(UP settles yes)                : %.4f" % up_rate)
print()

TTE_BANDS = [(0, 60), (60, 180), (180, 360), (360, 900)]
PX_BANDS  = [(0.00, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 1.00)]

def taker_fee(p):
    return FEE_THETA * p * (1.0 - p)

def collect(markets):
    """bucket -> {window: [ev_net,...]} for BUYING each side at its own
    executable cost. ev_gross = win - own_cost; ev_net subtracts the taker
    fee, because buying at the ask IS taking."""
    out = defaultdict(lambda: defaultdict(list))
    for m in markets:
        won_up = (settle[m] == "yes")
        xp = expiry[m]
        for (t, bid, ask) in books[m]:
            tte = xp - t
            if tte <= 0 or tte > 900: continue
            band = next((x for x in TTE_BANDS if x[0] <= tte < x[1]), None)
            if band is None: continue
            for side, cost, win in (("UP", ask, won_up),
                                    ("DOWN", 1.0 - bid, not won_up)):
                if not (0.0 < cost < 1.0): continue
                pb = next((x for x in PX_BANDS if x[0] <= cost < x[1]), None)
                if pb is None: continue
                ev = (1.0 if win else 0.0) - cost - taker_fee(cost)
                out[(band, pb, side)][m].append(ev)
    return out

def clustered(bucket):
    """mean and cluster-robust 95% CI, one cluster per WINDOW."""
    per_win = [sum(v) / len(v) for v in bucket.values() if v]
    w = len(per_win)
    if w == 0: return None
    mean = sum(per_win) / w
    if w < 2: return (mean, None, None, w, sum(len(v) for v in bucket.values()))
    sd = st.stdev(per_win)
    se = sd / math.sqrt(w)
    return (mean, mean - 1.96 * se, mean + 1.96 * se, w,
            sum(len(v) for v in bucket.values()))

# CHRONOLOGICAL, NON-OVERLAPPING SPLIT. No window appears in both.
cut = int(len(usable) * 0.62)
train, test = usable[:cut], usable[cut:]
print("chronological split: %d train / %d OOS windows (no overlap)"
      % (len(train), len(test)))
print("train expiry range: %s .. %s" % (
    dt.datetime.utcfromtimestamp(expiry[train[0]]).strftime("%m-%d %H:%M"),
    dt.datetime.utcfromtimestamp(expiry[train[-1]]).strftime("%m-%d %H:%M")))
print("OOS   expiry range: %s .. %s" % (
    dt.datetime.utcfromtimestamp(expiry[test[0]]).strftime("%m-%d %H:%M"),
    dt.datetime.utcfromtimestamp(expiry[test[-1]]).strftime("%m-%d %H:%M")))
print()

tr = collect(train)
print("=== TRAIN: net EV per contract, buying at the executable own-cost ===")
print("%-10s %-12s %-5s %9s %19s %5s %7s" %
      ("tte_s", "own_cost", "side", "net_EV", "95% CI (clustered)", "wins", "ticks"))
rows = []
for key in sorted(tr, key=lambda k: (k[0], k[1], k[2])):
    r = clustered(tr[key])
    if r is None: continue
    mean, lo, hi, w, n = r
    if w < 5: continue
    (tb, pb, side) = key
    ci = "[%+.4f, %+.4f]" % (lo, hi) if lo is not None else "n/a"
    print("%-10s %-12s %-5s %+9.4f %19s %5d %7d" %
          ("%d-%d" % tb, "%.2f-%.2f" % pb, side, mean, ci, w, n))
    rows.append((mean, lo, key, w))

pos = [r for r in rows if r[1] is not None and r[1] > 0]
print()
if pos:
    pos.sort(key=lambda r: -r[1])
    best = pos[0]
    print("TRAIN buckets whose 95%% CI excludes zero: %d" % len(pos))
    print("strongest on TRAIN (selected here, evaluated ONCE on OOS): %s" % (best[2],))
    te = collect(test)
    r = clustered(te[best[2]]) if best[2] in te else None
    print()
    print("=== OOS, the single TRAIN-selected rule, evaluated once ===")
    if r is None:
        print("rule produced NO OOS observations -> INSUFFICIENT DATA")
    else:
        mean, lo, hi, w, n = r
        print("net_EV %+.4f   95%% CI [%+.4f, %+.4f]   windows %d   ticks %d"
              % (mean, lo if lo is not None else float('nan'),
                 hi if hi is not None else float('nan'), w, n))
else:
    print("TRAIN: NO bucket has a 95% CI excluding zero.")
    print("Nothing is selected, so there is nothing to evaluate out of sample.")
