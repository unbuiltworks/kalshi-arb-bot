#!/usr/bin/env python3
"""Markout of OUR bot's fills, 14-18 Nov 2026 (own-quote proxy) and 22 Nov 2026 (real quotes).

Read-only. Standard library only. Re-run:
    python3 -I l5_markout.py [DATA_DIR] [OUT_DIR]
DATA_DIR defaults to /home/user/kalshi-arb-bot/analysis/l5 ; OUT_DIR to this script's folder.

Outputs: l5_markout_fills.csv, l5_markout_summary.json (the .md is written by hand from these).

Definitions
  value of YES = mid ; value of NO = 1 - mid.
  markout(h)   = value of the filled side at t_fill + h  -  fill price     (cents per contract)
  settled      = settled value of the filled side (1 or 0) - fill price    (cents per contract)
  The two are never mixed.

Mid estimates for 14-18 Nov come from the bot's own ORDER rows (no book recording exists):
  tier A  "pair"   : a YES order at y and a NO order at n placed <= PAIR_GAP_S apart.
                     mid = (y + 1 - n) / 2 ; if both rest, the true mid is inside [y, 1-n].
  tier B  "single" : one order with no partner. A resting YES bid y is a lower bound of the mid,
                     a resting NO bid n makes 1-n an upper bound. Taker ("exit") orders bound the
                     other way. Two valuations are kept:
                       raw : mid = the bound itself
                       adj : bound moved inward by the market's usual pair half-width
  Variants reported:  A (pair only) / AB_adj (pair, else single adj) / AB_raw (pair, else single raw).
"""
import bisect, collections, csv, datetime as dt, gzip, json, math, os, random, statistics as st, sys

DATA = sys.argv[1] if len(sys.argv) > 1 else "/home/user/kalshi-arb-bot/analysis/l5"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
F_ORD = os.path.join(DATA, "rewards/sports_orders_fills_20261114_20261118.json.gz")
F_PNL = os.path.join(DATA, "rewards/sports_market_pnl_20261114_20261118.json")
F_C22 = os.path.join(DATA, "sports/campaign_20261122_serie_a_orders.json")
F_Q22 = [os.path.join(DATA, "sports/soccer_bol_udi_sas_gen_20261122_1407_1416.jsonl.gz"),
         os.path.join(DATA, "sports/soccer_bol_udi_sas_gen_20261122_1420.jsonl.gz")]
# 22 Nov settlement (YES settlement price), from CAMPAIGN_20261122_SERIE_A_LOG.md "Settlement" section
SETTLE_22 = {"atc-sea-sas-gen-2026-11-22-gen": 1.0, "atc-sea-bol-udi-2026-11-22-bol": 0.0}

HORIZONS = [5, 30, 120, 300, 900]
HN = {5: "5s", 30: "30s", 120: "2m", 300: "5m", 900: "15m"}
PAIR_GAP_S = 5.0
EXACT_TOL_S = 6.5          # 22 Nov: a quote sample at most this long after t+h (sampling is every 3 s)
NBOOT = 2000
SEED = 20261122
MIN_MARKETS_CI = 5      # no confidence interval is printed for fewer markets than this
VARIANTS = ["A", "AB_adj", "AB_raw"]
EPS = 1e-9


def tol(h):
    return max(h, 30.0)


def ts(x):
    return dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sport_of(slug):
    p = slug.split("-")
    return p[1] if p[0] in ("aec", "atc") and len(p) > 1 else p[0]


def px_bucket(p):
    if p < 0.25: return "<0.25"
    if p < 0.40: return "0.25-0.40"
    if p < 0.60: return "0.40-0.60"
    if p <= 0.75: return "0.60-0.75"
    return ">0.75"


def unp_bucket(u):
    if u < 1e-6: return "0"
    if u <= 5 + 1e-6: return "1-5"
    if u <= 10 + 1e-6: return "6-10"
    return ">10"


def rest_bucket(s):
    if s is None: return "unknown"
    if s < 10: return "<10s"
    if s < 60: return "10-60s"
    if s < 300: return "1-5min"
    return ">5min"


def role_of(same, opp):
    """Role from the position BEFORE the fill. same/opp = contracts held on the filled side / other side.
    OPEN: no unpaired contracts before (YES == NO, including 0/0; pairs are netted and pay $1).
    EXTEND: adds to the side that was already heavier. COMPLETE: adds to the lighter side."""
    if abs(same - opp) < 1e-6:
        return "OPEN", ("OPEN_FIRST" if same < 1e-6 else "OPEN_REOPEN")
    if same > opp:
        return "EXTEND", "EXTEND"
    return "COMPLETE", "COMPLETE"


# ---------------------------------------------------------------- mid proxy from own orders
def build_proxy(orders, filled_fast=None):
    """orders: list of dicts with T, side, px, stage, order_id (one market), any order.
    Returns sorted list of estimates:
      dict(T, tier 'A'|'B', mid (A) or bound, kind 'pair'|'L'|'U', hw (pair half width), stages)
    'L' = lower bound of mid, 'U' = upper bound of mid."""
    v = sorted(orders, key=lambda r: r["T"])
    est, i = [], 0
    while i < len(v):
        a = v[i]
        b = v[i + 1] if i + 1 < len(v) else None
        if (b is not None and a["side"] != b["side"] and b["T"] - a["T"] <= PAIR_GAP_S
                and "exit" not in a["stage"] and "exit" not in b["stage"]):
            y, n = (a, b) if a["side"] == "YES" else (b, a)
            lo, hi = y["px"], 1.0 - n["px"]
            est.append(dict(T=b["T"], tier="A", kind="pair", mid=(lo + hi) / 2.0, hw=(hi - lo) / 2.0,
                            lo=lo, hi=hi, stages=a["stage"] + "+" + b["stage"],
                            same_stage=a["stage"] == b["stage"], ids=(a["order_id"], b["order_id"])))
            i += 2
            continue
        taker = "exit" in a["stage"]
        if a["side"] == "YES":
            kind, bound = ("U", a["px"]) if taker else ("L", a["px"])
        else:
            kind, bound = ("L", 1.0 - a["px"]) if taker else ("U", 1.0 - a["px"])
        est.append(dict(T=a["T"], tier="B", kind=kind, bound=bound, stages=a["stage"], taker=taker))
        i += 1
    return est


def market_hw(est):
    h = [e["hw"] for e in est if e["tier"] == "A" and e["hw"] > -EPS]
    return st.median(h) if h else None


def value_at(est, Ts, t_target, h, hw_m):
    """First pair estimate in [t_target, t_target + tol]; else first single. Returns dict per variant."""
    lo = bisect.bisect_left(Ts, t_target - EPS)
    end = t_target + tol(h)
    first_pair = first_single = None
    j = lo
    while j < len(est) and est[j]["T"] <= end:
        e = est[j]
        if e["tier"] == "A" and first_pair is None:
            first_pair = e
            break
        if e["tier"] == "B" and first_single is None:
            first_single = e
        j += 1
    out = {}
    if first_pair is not None:
        e = first_pair
        for vname in VARIANTS:
            out[vname] = e["mid"]
        out.update(tier="A", lag=e["T"] - t_target, hw=e["hw"], src=e["stages"])
    elif first_single is not None:
        e = first_single
        sgn = 1.0 if e["kind"] == "L" else -1.0
        out["AB_raw"] = e["bound"]
        out["AB_adj"] = e["bound"] + sgn * hw_m
        out.update(tier="B", lag=e["T"] - t_target, hw=hw_m, src=e["stages"] + ":" + e["kind"])
    return out


# ---------------------------------------------------------------- load 14-18 Nov
raw = json.load(gzip.open(F_ORD))
pnl = json.load(open(F_PNL))
orders_all = [r for r in raw if r["kind"] == "ORDER"]
orders = [r for r in orders_all if "side" in r and "px" in r]
sideless = [r for r in orders_all if "side" not in r]
fills_raw = [r for r in raw if r["kind"] == "FILL"]
other_kinds = [r for r in raw if r["kind"] not in ("ORDER", "FILL")]
for r in orders:
    r["T"] = ts(r["t"]); r["px"] = float(r["px"]); r["q"] = float(r["qty"])
OBY = {r["order_id"]: r for r in orders}
ord_by_mkt = collections.defaultdict(list)
for r in orders:
    ord_by_mkt[r["slug"]].append(r)

GLOBAL_HW = None
proxy, proxy_T, hw_mkt = {}, {}, {}
all_hw = []
for slug, v in ord_by_mkt.items():
    e = build_proxy(v)
    proxy[slug] = e
    proxy_T[slug] = [x["T"] for x in e]
    hw_mkt[slug] = market_hw(e)
    all_hw += [x["hw"] for x in e if x["tier"] == "A"]
GLOBAL_HW = st.median(all_hw)
# quoted edge: if the filled order was one leg of a pair, its distance below the pair mid when it was placed
ORDER_PAIR_HW = {}
for _e in (x for v in proxy.values() for x in v if x["tier"] == "A"):
    for _id in _e["ids"]:
        ORDER_PAIR_HW[_id] = _e["hw"]

# winner: settle_px in the P&L file is the YES settlement price of markets left with unpaired contracts.
# Check: res_pnl must equal res_qty * (value of res_side) - res_cost.
settle_yes, settle_check_bad = {}, []
for slug, m in pnl.items():
    if m.get("settle_px") is None or m.get("res_side") is None:
        continue
    sp = float(m["settle_px"])
    val = sp if m["res_side"] == "YES" else 1.0 - sp
    if abs(m["res_qty"] * val - m["res_cost"] - m["res_pnl"]) > 1e-6 or sp not in (0.0, 1.0):
        settle_check_bad.append(slug)
        continue
    settle_yes[slug] = sp

fills = []
by_mkt_f = collections.defaultdict(list)
for i, r in enumerate(fills_raw):
    r["i"] = i; r["T"] = ts(r["t"])
    by_mkt_f[r["slug"]].append(r)
sanity = collections.OrderedDict()
pos_bad = 0
for slug, v in by_mkt_f.items():
    v.sort(key=lambda r: (r["T"], r["i"]))
    run = {"YES": 0.0, "NO": 0.0}
    for r in v:
        q, p, sd = float(r["qty"]), float(r["px"]), r["side"]
        after = {k: float(x) for k, x in r["pos"].items()}
        before = dict(after); before[sd] -= q
        if abs(before["YES"] - run["YES"]) > 1e-6 or abs(before["NO"] - run["NO"]) > 1e-6:
            pos_bad += 1
        run = after
        o = OBY.get(r["order_id"])
        other = "NO" if sd == "YES" else "YES"
        role, role_d = role_of(before[sd], before[other])
        f = dict(src="L5_1418_PROXY", fill_idx=r["i"], t=r["t"], T=r["T"], date=r["t"][:10], slug=slug,
                 sport=sport_of(slug), side=sd, px=p, qty=q, order_id=r["order_id"],
                 stage=o["stage"] if o else "", order_t=o["t"] if o else "",
                 order_px=o["px"] if o else None, order_qty=o["q"] if o else None,
                 rest_s=(r["T"] - o["T"]) if o else None,
                 pos_yes_before=before["YES"], pos_no_before=before["NO"],
                 unpaired_before=abs(before["YES"] - before["NO"]),
                 heavy_before=("YES" if before["YES"] > before["NO"] + 1e-6 else
                               "NO" if before["NO"] > before["YES"] + 1e-6 else ""),
                 role=role, role_detail=role_d)
        fills.append(f)


def enrich(f):
    f["px_bucket"] = px_bucket(f["px"])
    f["unpaired_bucket"] = unp_bucket(f["unpaired_before"])
    f["rest_bucket"] = rest_bucket(f["rest_s"])
    f["taker_stage"] = int("exit" in (f["stage"] or ""))


for f in fills:
    enrich(f)
    est, Ts = proxy.get(f["slug"], []), proxy_T.get(f["slug"], [])
    hwm = hw_mkt.get(f["slug"]) or GLOBAL_HW
    f["mkt_pair_hw_c"] = round(100 * hwm, 3)
    for h in HORIZONS:
        r = value_at(est, Ts, f["T"] + h, h, hwm)
        for vname in VARIANTS:
            m = r.get(vname)
            val = None if m is None else (m if f["side"] == "YES" else 1.0 - m)
            f["mo_%s_%s_c" % (vname, HN[h])] = None if val is None else round(100 * (val - f["px"]), 4)
        f["tier_%s" % HN[h]] = r.get("tier", "")
        f["lag_%s_s" % HN[h]] = round(r["lag"], 2) if "lag" in r else None
        f["esthw_%s_c" % HN[h]] = round(100 * r["hw"], 3) if "hw" in r else None
        f["estsrc_%s" % HN[h]] = r.get("src", "")
    qe = ORDER_PAIR_HW.get(f["order_id"])
    f["quoted_edge_c"] = None if qe is None else round(100 * qe, 3)
    for h in HORIZONS:
        m = f["mo_AB_adj_%s_c" % HN[h]]
        f["midmove_%s_c" % HN[h]] = None if (m is None or qe is None) else round(m - 100 * qe, 4)
    sy = settle_yes.get(f["slug"])
    f["settle_yes"] = sy
    f["settled_value"] = None if sy is None else (sy if f["side"] == "YES" else 1.0 - sy)
    f["settled_c"] = None if sy is None else round(100 * (f["settled_value"] - f["px"]), 4)

# ---------------------------------------------------------------- 22 Nov: real quotes
c22 = json.load(open(F_C22))
for r in c22:
    r["T"] = ts(r["t"]); r["px"] = float(r["px"]); r["q"] = float(r["qty"]); r["stage"] = r.get("stage") or ""
quotes = collections.defaultdict(list)
for path in F_Q22:
    for line in gzip.open(path, "rt"):
        q = json.loads(line)
        quotes[q["slug"]].append(q)
for s in quotes:
    quotes[s].sort(key=lambda q: q["t"])
QT = {s: [q["t"] for q in v] for s, v in quotes.items()}


def quote_at_or_after(slug, t, maxlag=EXACT_TOL_S):
    v = quotes.get(slug) or []
    j = bisect.bisect_left(QT.get(slug, []), t - EPS)
    if j < len(v) and v[j]["t"] - t <= maxlag:
        return v[j]
    return None


def quote_nearest(slug, t, maxd=3.1):
    v = quotes.get(slug) or []
    T = QT.get(slug, [])
    j = bisect.bisect_left(T, t)
    best = None
    for k in (j - 1, j):
        if 0 <= k < len(v) and abs(v[k]["t"] - t) <= maxd:
            if best is None or abs(v[k]["t"] - t) < abs(best["t"] - t):
                best = v[k]
    return best


def usmid(q):
    return None if q is None or q.get("ub") is None or q.get("ua") is None else (q["ub"] + q["ua"]) / 2.0


def intlmid(q):
    return None if q is None or q.get("ib") is None or q.get("ia") is None else (q["ib"] + q["ia"]) / 2.0


def why_missing(slug, t):
    v = QT.get(slug, [])
    if not v or t < v[0]: return "before recording start (14:07:34Z)"
    seg_end1 = ts("2026-11-22T14:16:02Z"); seg_start2 = ts("2026-11-22T14:20:31Z")
    if seg_end1 <= t <= seg_start2: return "recording gap 14:16-14:20Z"
    if t > v[-1]: return "after recording end"
    q = quote_at_or_after(slug, t)
    if q is not None and usmid(q) is None: return "book one-sided/closed in sample"
    return "no sample within tolerance"


o22 = [r for r in c22 if r["kind"] == "ORDER"]
f22raw = sorted([r for r in c22 if r["kind"] == "FILL"], key=lambda r: r["T"])
o22_by = collections.defaultdict(list)
for k, r in enumerate(o22):
    r["order_id"] = "c22-%d" % k
    o22_by[r["slug"]].append(r)
proxy22 = {s: build_proxy(v) for s, v in o22_by.items()}
proxy22_T = {s: [e["T"] for e in v] for s, v in proxy22.items()}
hw22 = {s: market_hw(v) for s, v in proxy22.items()}

# --- validation of the proxy against sampled quotes (22 Nov)
val_rows = []
for slug, est in proxy22.items():
    hwm = hw22[slug] or GLOBAL_HW
    for e in est:
        q = quote_nearest(slug, e["T"])
        m = usmid(q)
        if m is None:
            continue
        row = dict(slug=slug, t=iso(e["T"]), tier=e["tier"], kind=e["kind"], stages=e["stages"],
                   ub=q["ub"], ua=q["ua"], true_mid=m, spread_c=round(100 * (q["ua"] - q["ub"]), 2),
                   dt_s=round(q["t"] - e["T"], 2))
        if e["tier"] == "A":
            row.update(proxy_mid=e["mid"], err_c=round(100 * (e["mid"] - m), 3), hw_c=round(100 * e["hw"], 3),
                       inside=int(e["lo"] - 1e-9 <= m <= e["hi"] + 1e-9))
        else:
            sgn = 1.0 if e["kind"] == "L" else -1.0
            row.update(bound=e["bound"], raw_err_c=round(100 * (e["bound"] - m), 3),
                       adj_err_c=round(100 * (e["bound"] + sgn * hwm - m), 3),
                       bound_holds=int((e["bound"] <= m + 1e-9) if e["kind"] == "L" else (e["bound"] >= m - 1e-9)),
                       touch_err_c=round(100 * (e["bound"] - (q["ub"] if e["kind"] == "L" else q["ua"])), 3))
        val_rows.append(row)


def dist(xs):
    xs = sorted(xs)
    if not xs:
        return dict(n=0)
    q = lambda p: xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]
    return dict(n=len(xs), mean=round(st.mean(xs), 3), median=round(st.median(xs), 3),
                mean_abs=round(st.mean(abs(x) for x in xs), 3), p10=q(.10), p90=q(.90),
                min=xs[0], max=xs[-1], within_1c=round(sum(abs(x) <= 1.0 + 1e-9 for x in xs) / len(xs), 3),
                within_2c=round(sum(abs(x) <= 2.0 + 1e-9 for x in xs) / len(xs), 3))


pairs_v = [r for r in val_rows if r["tier"] == "A"]
singles_v = [r for r in val_rows if r["tier"] == "B"]
n_pairs22 = sum(1 for v in proxy22.values() for e in v if e["tier"] == "A")
n_singles22 = sum(1 for v in proxy22.values() for e in v if e["tier"] == "B")
validation = collections.OrderedDict(
    note="22 Nov controller (rn1: post-only, one tick behind the touch, pair cap 0.97) differs from the "
         "14-18 Nov controllers; this validates the METHOD on the only day with a book recording.",
    orders=len(o22), pairs_total=n_pairs22, singles_total=n_singles22,
    pairs_with_quote=len(pairs_v), singles_with_quote=len(singles_v),
    pair_mid_error_c=dist([r["err_c"] for r in pairs_v]),
    pair_mid_error_same_stage_c=dist([r["err_c"] for r in pairs_v if "+" in r["stages"]
                                      and r["stages"].split("+")[0] == r["stages"].split("+")[1]]),
    pair_true_mid_inside_own_quotes=round(sum(r["inside"] for r in pairs_v) / len(pairs_v), 3) if pairs_v else None,
    pair_half_width_c=dist([r["hw_c"] for r in pairs_v]),
    true_spread_c=dist([r["spread_c"] for r in val_rows]),
    single_raw_error_c=dist([r["raw_err_c"] for r in singles_v]),
    single_adj_error_c=dist([r["adj_err_c"] for r in singles_v]),
    single_bound_holds=round(sum(r["bound_holds"] for r in singles_v) / len(singles_v), 3) if singles_v else None,
    single_distance_from_own_touch_c=dist([r["touch_err_c"] for r in singles_v]),
    single_by_stage={k: dict(raw=dist([r["raw_err_c"] for r in singles_v if r["stages"] == k]),
                             adj=dist([r["adj_err_c"] for r in singles_v if r["stages"] == k]))
                     for k in sorted(set(r["stages"] for r in singles_v))},
)

# --- 22 Nov fills
fills22 = []
run22 = collections.defaultdict(lambda: {"YES": 0.0, "NO": 0.0})
for k, r in enumerate(f22raw):
    sd, other = r["side"], ("NO" if r["side"] == "YES" else "YES")
    before = dict(run22[r["slug"]])
    role, role_d = role_of(before[sd], before[other])
    run22[r["slug"]][sd] += r["q"]
    # resting order: last own order on that market/side/price before the fill
    cand = [o for o in o22_by[r["slug"]] if o["side"] == sd and abs(o["px"] - r["px"]) < 1e-9 and o["T"] <= r["T"]]
    o = cand[-1] if cand else None
    f = dict(src="NOV22_EXACT", fill_idx=10000 + k, t=r["t"], T=r["T"], date=r["t"][:10], slug=r["slug"],
             sport=sport_of(r["slug"]), side=sd, px=r["px"], qty=r["q"], order_id="",
             stage=(o["stage"] if o and o["stage"] else ("(unlabelled)" if o else "")),
             order_t=o["t"] if o else "", order_px=o["px"] if o else None, order_qty=o["q"] if o else None,
             rest_s=(r["T"] - o["T"]) if o else None,
             pos_yes_before=before["YES"], pos_no_before=before["NO"],
             unpaired_before=abs(before["YES"] - before["NO"]),
             heavy_before=("YES" if before["YES"] > before["NO"] else "NO" if before["NO"] > before["YES"] else ""),
             role=role, role_detail=role_d)
    enrich(f)
    est, Ts = proxy22[r["slug"]], proxy22_T[r["slug"]]
    hwm = hw22[r["slug"]] or GLOBAL_HW
    f["mkt_pair_hw_c"] = round(100 * hwm, 3)
    q0 = quote_nearest(r["slug"], r["T"])
    m0 = usmid(q0)
    f["exact_mid_at_fill"] = m0
    f["mo_exact_0s_c"] = None if m0 is None else round(100 * ((m0 if sd == "YES" else 1 - m0) - r["px"]), 3)
    for h in HORIZONS:
        q = quote_at_or_after(r["slug"], r["T"] + h)
        m = usmid(q); im = intlmid(q)
        f["mo_exact_%s_c" % HN[h]] = None if m is None else round(100 * ((m if sd == "YES" else 1 - m) - r["px"]), 3)
        f["mo_exact_intl_%s_c" % HN[h]] = None if im is None else round(100 * ((im if sd == "YES" else 1 - im) - r["px"]), 3)
        f["exact_missing_%s" % HN[h]] = "" if m is not None else why_missing(r["slug"], r["T"] + h)
        pr = value_at(est, Ts, r["T"] + h, h, hwm)
        for vname in VARIANTS:
            mm = pr.get(vname)
            val = None if mm is None else (mm if sd == "YES" else 1.0 - mm)
            f["mo_%s_%s_c" % (vname, HN[h])] = None if val is None else round(100 * (val - r["px"]), 4)
        f["tier_%s" % HN[h]] = pr.get("tier", "")
        f["lag_%s_s" % HN[h]] = round(pr["lag"], 2) if "lag" in pr else None
        f["esthw_%s_c" % HN[h]] = round(100 * pr["hw"], 3) if "hw" in pr else None
        f["estsrc_%s" % HN[h]] = pr.get("src", "")
    sy = SETTLE_22.get(r["slug"])
    f["settle_yes"] = sy
    f["settled_value"] = sy if sd == "YES" else 1.0 - sy
    f["settled_c"] = round(100 * (f["settled_value"] - r["px"]), 4)
    fills22.append(f)

# proxy markout vs exact markout on the same 22 Nov fills
pv = collections.OrderedDict()
for vname in VARIANTS:
    d = []
    for f in fills22:
        for h in HORIZONS:
            a, b = f.get("mo_%s_%s_c" % (vname, HN[h])), f.get("mo_exact_%s_c" % HN[h])
            if a is not None and b is not None:
                d.append(round(a - b, 3))
    pv[vname] = dist(d)
validation["proxy_markout_minus_exact_markout_c_on_22Nov_fills"] = pv


# ---------------------------------------------------------------- statistics
def wmean(rows, key):
    w = sum(r["qty"] for r in rows)
    return sum(r["qty"] * r[key] for r in rows) / w if w > 0 else None


def boot_ci(rows, key, weighted=True, nboot=NBOOT, seed=SEED):
    """Cluster bootstrap: resample whole markets with replacement. 95% percentile interval."""
    by = collections.defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        w = r["qty"] if weighted else 1.0
        by[r["slug"]][0] += w * r[key]; by[r["slug"]][1] += w
    cl = list(by.values())
    if len(cl) < MIN_MARKETS_CI:
        return None
    rng = random.Random(seed)
    k, out = len(cl), []
    for _ in range(nboot):
        s = w = 0.0
        for _ in range(k):
            c = cl[rng.randrange(k)]
            s += c[0]; w += c[1]
        if w > 0:
            out.append(s / w)
    out.sort()
    return [round(out[int(0.025 * (len(out) - 1))], 3), round(out[int(0.975 * (len(out) - 1))], 3)]


def stat(rows_all, key):
    rows = [r for r in rows_all if r.get(key) is not None]
    n_all = len(rows_all)
    res = collections.OrderedDict(n_fills=n_all, n_valued=len(rows),
                                  coverage=round(len(rows) / n_all, 3) if n_all else None)
    if not rows:
        return res
    res.update(contracts=round(sum(r["qty"] for r in rows), 2), markets=len(set(r["slug"] for r in rows)),
               mean_c=round(wmean(rows, key), 3), ci95=boot_ci(rows, key, True),
               mean_per_fill_c=round(st.mean(r[key] for r in rows), 3), ci95_per_fill=boot_ci(rows, key, False),
               median_c=round(st.median(r[key] for r in rows), 3),
               share_negative=round(sum(r[key] < -1e-9 for r in rows) / len(rows), 3))
    return res


GROUPS = collections.OrderedDict([
    ("overall", lambda f: "all"),
    ("sport", lambda f: f["sport"]),
    ("role", lambda f: f["role"]),
    ("role_detail", lambda f: f["role_detail"]),
    ("unpaired_before", lambda f: f["unpaired_bucket"]),
    ("price_bucket", lambda f: f["px_bucket"]),
    ("stage", lambda f: f["stage"]),
    ("rest_time", lambda f: f["rest_bucket"]),
    ("date", lambda f: f["date"]),
    ("role_x_unpaired", lambda f: f["role"] + "|" + f["unpaired_bucket"]),
    ("role_x_date", lambda f: f["role"] + "|" + f["date"]),
    ("role_x_era", lambda f: f["role"] + "|" + ("14-16 Nov" if f["date"] <= "2026-11-16" else "17-18 Nov")),
    ("role_x_maker_taker", lambda f: f["role"] + "|" + ("taker_exit" if f["taker_stage"] else "resting")),
])
ORDER = {"unpaired_before": ["0", "1-5", "6-10", ">10"],
         "price_bucket": ["<0.25", "0.25-0.40", "0.40-0.60", "0.60-0.75", ">0.75"],
         "rest_time": ["<10s", "10-60s", "1-5min", ">5min"],
         "role": ["OPEN", "EXTEND", "COMPLETE"]}


def grouped(rows, fn):
    g = collections.defaultdict(list)
    for r in rows:
        g[fn(r)].append(r)
    return g


def table(rows, keys):
    out = collections.OrderedDict()
    for gname, fn in GROUPS.items():
        g = grouped(rows, fn)
        names = [k for k in ORDER.get(gname, []) if k in g] + sorted(
            [k for k in g if k not in ORDER.get(gname, [])], key=lambda k: -len(g[k]))
        out[gname] = collections.OrderedDict((k, collections.OrderedDict((kk, stat(g[k], key)) for kk, key in keys))
                                             for k in names)
    return out


summary = collections.OrderedDict()
summary["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
summary["definitions"] = dict(
    markout="value of the filled side at t+h minus fill price, cents per contract; YES value = mid, NO value = 1-mid",
    settled="settled value of the filled side (1 or 0) minus fill price, cents per contract",
    mean_c="contract-weighted mean; ci95 = percentile bootstrap resampling whole markets (%d draws)" % NBOOT,
    role="OPEN: YES==NO before the fill (no unpaired contracts); EXTEND: adds to the heavier side; "
         "COMPLETE: adds to the lighter side",
    horizon_rule="first estimate at or after t+h, at most max(h,30s) later; pair estimate preferred over single",
    variants=dict(A="pair estimates only", AB_adj="pair, else single-sided bound moved inward by the market's "
                  "median pair half-width (central)", AB_raw="pair, else single-sided bound as is"))

# sanity
tot_q = sum(f["qty"] for f in fills); tot_c = sum(f["qty"] * f["px"] for f in fills)
dup_exact = sum(v - 1 for v in collections.Counter((r["order_id"], r["t"], r["qty"], r["px"]) for r in fills_raw).values() if v > 1)
per_order = collections.Counter(r["order_id"] for r in fills_raw)
fq = collections.defaultdict(float)
for r in fills_raw: fq[r["order_id"]] += float(r["qty"])
exit_rest = sorted(f["rest_s"] for f in fills if f["taker_stage"])
all_T = [ts(r["t"]) for r in raw]
sanity.update(
    rows=len(raw), order_rows=len(orders_all), order_rows_with_side=len(orders), fill_rows=len(fills_raw),
    rows_other_kind=len(other_kinds),
    order_rows_without_side=len(sideless),
    sideless_detail=dict(
        note="kind is ORDER, no side and no px, second-resolution timestamps, order_id not seen elsewhere; "
             "these are orders with fields missing from the recovered log line, NOT cancels. "
             "The file contains no cancel rows at all.",
        by_stage_qty={"%s|qty=%s" % k: v for k, v in collections.Counter((r["stage"], r["qty"]) for r in sideless).items()},
        by_hour_sport={"%s|%s" % k: v for k, v in collections.Counter((r["t"][:13], sport_of(r["slug"])) for r in sideless).items()}),
    stage_counts_orders=dict(collections.Counter(r["stage"] for r in orders).most_common()),
    stage_counts_fills=dict(collections.Counter(f["stage"] for f in fills).most_common()),
    total_contracts=round(tot_q, 4), total_cost=round(tot_c, 4),
    expected_contracts=1900.97, expected_cost=953.91,
    markets_with_orders=len(ord_by_mkt), markets_with_fills=len(by_mkt_f), markets_in_pnl_file=len(pnl),
    exact_duplicate_fill_rows=dup_exact,
    fills_without_matching_order=sum(1 for f in fills if not f["stage"]),
    orders_with_more_than_one_fill_row={str(k): v for k, v in collections.Counter(per_order.values()).items()},
    orders_filled_beyond_order_qty=sum(1 for k, v in fq.items() if k in OBY and v > OBY[k]["q"] + 1e-6),
    partial_fill_rows=sum(1 for f in fills if f["order_qty"] is not None and f["qty"] < f["order_qty"] - 1e-6),
    fill_px_differs_from_order_px=[dict(slug=f["slug"], side=f["side"], fill_px=f["px"], order_px=f["order_px"], stage=f["stage"])
                                   for f in fills if f["order_px"] is not None and abs(f["px"] - f["order_px"]) > 1e-9],
    fill_before_its_order=sum(1 for f in fills if f["rest_s"] is not None and f["rest_s"] < 0),
    position_field_inconsistent_with_running_sum=pos_bad,
    file_time_sorted=all_T == sorted(all_T),
    fill_qty_histogram=dict(collections.Counter("%g" % f["qty"] for f in fills).most_common(8)),
    fills_below_1_contract=sum(1 for f in fills if f["qty"] < 1 - 1e-9),
    taker_exit_fills=len(exit_rest),
    taker_exit_fill_minus_order_time_s=dict(min=round(exit_rest[0], 2), median=round(st.median(exit_rest), 2),
                                            p90=round(exit_rest[int(.9 * (len(exit_rest) - 1))], 2), max=round(exit_rest[-1], 2)),
    fill_timestamp_note="Taker (exit) fills are logged 5.3 s or more after their order, never sooner, so the FILL time is "
                        "when the bot LOGGED the fill, not when the venue matched it. On 22 Nov two fills were logged "
                        "about 30 s after the venue had them. Horizons are counted from the logged time.",
    pnl_file_totals=dict(contracts=round(sum(m["y"] + m["n"] for m in pnl.values()), 2),
                         cost=round(sum(m["cost"] for m in pnl.values()), 4),
                         pair_pnl=round(sum(m["pair_pnl"] for m in pnl.values()), 4),
                         unpaired_pnl=round(sum(m["res_pnl"] or 0 for m in pnl.values()), 4),
                         taker_fee_est=round(sum(m["taker_fee_est"] for m in pnl.values()), 4)),
    settlement=dict(markets_resolved=len(settle_yes), markets_with_fills=len(by_mkt_f),
                    resolved_check_failed=settle_check_bad,
                    fills_in_resolved_markets=sum(1 for f in fills if f["settle_yes"] is not None),
                    contracts_in_resolved_markets=round(sum(f["qty"] for f in fills if f["settle_yes"] is not None), 2),
                    how="settle_px is the YES settlement price, present only for the 47 markets left with unpaired "
                        "contracts; verified with res_pnl = res_qty*value(res_side) - res_cost. In all 47 the unpaired "
                        "side settled at 0. The 126 fully paired markets have no settlement price in any local file; "
                        "their winner cannot be derived from P&L because a pair pays $1 whoever wins."),
)
summary["sanity"] = sanity

# proxy description
pair_ests = [e for v in proxy.values() for e in v if e["tier"] == "A"]
single_ests = [e for v in proxy.values() for e in v if e["tier"] == "B"]
summary["proxy_14_18"] = collections.OrderedDict(
    pair_estimates=len(pair_ests), single_estimates=len(single_ests),
    pair_stage_combos=dict(collections.Counter(e["stages"] for e in pair_ests).most_common()),
    single_stage=dict(collections.Counter(e["stages"] for e in single_ests).most_common()),
    pair_half_width_c=dist([round(100 * e["hw"], 3) for e in pair_ests]),
    pair_sum_histogram={("%.2f" % k): v for k, v in sorted(collections.Counter(
        round(1 - 2 * e["hw"], 2) for e in pair_ests).items())},
    crossed_pairs=sum(1 for e in pair_ests if e["hw"] < -1e-9),
    hard_bound_note="If both orders of a pair were resting, the true mid lies between them, so the error of a pair "
                    "estimate is at most its half-width (unless the orders were rejected or the book moved in the "
                    "seconds between them).",
    global_median_half_width_c=round(100 * GLOBAL_HW, 3))
summary["proxy_validation_22Nov"] = validation
summary["proxy_validation_rows"] = val_rows

mo_keys = lambda v: [(HN[h], "mo_%s_%s_c" % (v, HN[h])) for h in HORIZONS]
summary["MARKOUT_14_18_proxy"] = collections.OrderedDict((v, table(fills, mo_keys(v))) for v in VARIANTS)
_mm = [("quoted_edge", "quoted_edge_c")] + [(HN[h], "midmove_%s_c" % HN[h]) for h in HORIZONS]
summary["MID_MOVE_since_order_14_18_proxy"] = collections.OrderedDict(
    note="Only fills whose order was one leg of a two-sided quote. quoted_edge = how far below the proxy mid the "
         "order was placed (the pair half-width). mid move = markout (AB_adj) - quoted_edge = how far the value of the "
         "filled side moved between order placement and t+h. Negative = the price moved against the fill.",
    tables={k: v for k, v in table([f for f in fills if f["quoted_edge_c"] is not None], _mm).items()
            if k in ("overall", "role", "role_x_era", "sport", "stage", "date")})
summary["tier_used_14_18"] = {HN[h]: dict(collections.Counter(f["tier_%s" % HN[h]] or "none" for f in fills))
                              for h in HORIZONS}
summary["tier_used_by_role_14_18"] = {
    role: {HN[h]: dict(collections.Counter(f["tier_%s" % HN[h]] or "none" for f in fills if f["role"] == role))
           for h in HORIZONS} for role in ("OPEN", "EXTEND", "COMPLETE")}

# settlement tables (resolved markets only)
res_f = [f for f in fills if f["settled_c"] is not None]
summary["SETTLED_14_18_resolved_markets_only"] = table(res_f, [("settled", "settled_c")])
# exact all-market settled result that needs no winner: pair P&L + unpaired P&L per market, from the P&L file
allm = []
for slug, m in pnl.items():
    q = m["y"] + m["n"]
    if q > 0:
        allm.append(dict(slug=slug, sport=sport_of(slug), qty=q, cost=m["cost"],
                         pnl=m["pair_pnl"] + (m["res_pnl"] or 0.0), resolved=slug in settle_yes))
def mk(rows):
    q = sum(r["qty"] for r in rows); p = sum(r["pnl"] for r in rows)
    rr = [dict(slug=r["slug"], qty=r["qty"], v=100 * r["pnl"] / r["qty"]) for r in rows]
    return collections.OrderedDict(markets=len(rows), contracts=round(q, 2), pnl_usd=round(p, 2),
                                   settled_c_per_contract=round(100 * p / q, 3) if q else None,
                                   ci95=boot_ci(rr, "v", True))
sm = collections.OrderedDict(all_markets=mk(allm),
                             markets_left_unpaired=mk([r for r in allm if r["resolved"]]),
                             markets_fully_paired=mk([r for r in allm if not r["resolved"]]))
sm["by_sport"] = collections.OrderedDict(
    (s, mk([r for r in allm if r["sport"] == s]))
    for s, _ in collections.Counter(r["sport"] for r in allm).most_common())
sm["note"] = "Before fees. Whole-market result (pairs + unpaired) divided by all contracts bought; needs no winner."
summary["SETTLED_14_18_all_markets_market_level"] = sm

# settled result by 5-minute markout tercile (resolved markets, fills with a 5-min value)
def terciles(rows, mkey, skey="settled_c"):
    rows = sorted([r for r in rows if r.get(mkey) is not None and r.get(skey) is not None], key=lambda r: r[mkey])
    n = len(rows)
    if n < 6:
        return dict(n=n)
    cuts = [rows[n // 3][mkey], rows[2 * n // 3][mkey]]
    parts = [[r for r in rows if r[mkey] < cuts[0]], [r for r in rows if cuts[0] <= r[mkey] < cuts[1]],
             [r for r in rows if r[mkey] >= cuts[1]]]
    out = collections.OrderedDict(n=n, cut_points_c=cuts)
    for name, p in zip(("low_markout", "mid_markout", "high_markout"), parts):
        if not p:
            out[name] = dict(n=0); continue
        out[name] = collections.OrderedDict(
            n=len(p), markets=len(set(r["slug"] for r in p)),
            markout_mean_c=round(wmean(p, mkey), 3), settled_mean_c=round(wmean(p, skey), 3),
            settled_ci95=boot_ci(p, skey, True), share_settled_winner=round(sum(r["settled_value"] > 0.5 for r in p) / len(p), 3))
    return out
summary["SETTLED_by_5min_MARKOUT_tercile"] = collections.OrderedDict(
    (v, collections.OrderedDict(all=terciles(fills, "mo_%s_5m_c" % v),
                                OPEN=terciles([f for f in fills if f["role"] == "OPEN"], "mo_%s_5m_c" % v)))
    for v in VARIANTS)
summary["SETTLED_by_2min_MARKOUT_tercile"] = collections.OrderedDict(
    (v, terciles(fills, "mo_%s_2m_c" % v)) for v in VARIANTS)

# item 5: headline adverse selection numbers
def key5(rows):
    return collections.OrderedDict((v, collections.OrderedDict((HN[h], stat(rows, "mo_%s_%s_c" % (v, HN[h])))
                                                               for h in HORIZONS)) for v in VARIANTS)
opens = [f for f in fills if f["role"] == "OPEN"]
exts = [f for f in fills if f["role"] == "EXTEND"]
five = collections.OrderedDict()
five["a_OPEN_all"] = key5(opens)
five["a_OPEN_maker_stages_only"] = key5([f for f in opens if not f["taker_stage"]])
five["a_OPEN_by_sport_n30"] = collections.OrderedDict(
    (s, key5([f for f in opens if f["sport"] == s]))
    for s, n in collections.Counter(f["sport"] for f in opens).most_common() if n >= 30)
five["b_EXTEND_all"] = key5(exts)
five["b_EXTEND_by_unpaired"] = collections.OrderedDict(
    (b, key5([f for f in exts if f["unpaired_bucket"] == b])) for b in ORDER["unpaired_before"]
    if any(f["unpaired_bucket"] == b for f in exts))
five["b_EXTEND_by_sport_n30"] = collections.OrderedDict(
    (s, key5([f for f in exts if f["sport"] == s]))
    for s, n in collections.Counter(f["sport"] for f in exts).most_common() if n >= 30)
five["b_EXTEND_by_sport_x_unpaired_n30"] = collections.OrderedDict(
    ("%s|%s" % k, key5([f for f in exts if (f["sport"], f["unpaired_bucket"]) == k]))
    for k, n in collections.Counter((f["sport"], f["unpaired_bucket"]) for f in exts).most_common() if n >= 30)
five["EXTEND_stage_mix"] = dict(collections.Counter(f["stage"] for f in exts))
five["OPEN_stage_mix"] = dict(collections.Counter(f["stage"] for f in opens))
five["COMPLETE_stage_mix"] = dict(collections.Counter(f["stage"] for f in fills if f["role"] == "COMPLETE"))
summary["ITEM5_adverse_selection"] = five

# how soon the unpaired leg created by an OPEN fill was fully paired again (context for coverage bias)
def time_to_flat(fl):
    out = {}
    by = collections.defaultdict(list)
    for f in fl: by[f["slug"]].append(f)
    for slug, v in by.items():
        v.sort(key=lambda f: (f["T"], f["fill_idx"]))
        for i, f in enumerate(v):
            if f["role"] != "OPEN": continue
            y, n = f["pos_yes_before"], f["pos_no_before"]
            y, n = (y + f["qty"], n) if f["side"] == "YES" else (y, n + f["qty"])
            tt = None
            for g in v[i + 1:]:
                if g["side"] == "YES": y += g["qty"]
                else: n += g["qty"]
                if abs(y - n) < 1e-6:
                    tt = g["T"] - f["T"]; break
            out[f["fill_idx"]] = tt
    return out
ttf = time_to_flat(fills)
for f in fills:
    f["open_time_to_flat_s"] = round(ttf[f["fill_idx"]], 2) if ttf.get(f["fill_idx"]) is not None else None
    f["open_never_flat"] = int(f["role"] == "OPEN" and ttf.get(f["fill_idx"]) is None) if f["role"] == "OPEN" else None
tv = sorted(v for v in ttf.values() if v is not None)
summary["OPEN_time_to_flat"] = dict(
    open_fills=len(ttf), flat_again=len(tv), never_flat_again=len(ttf) - len(tv),
    median_s=round(st.median(tv), 1), p75_s=round(tv[int(.75 * (len(tv) - 1))], 1), p90_s=round(tv[int(.9 * (len(tv) - 1))], 1),
    coverage_bias=collections.OrderedDict(
        (HN[h], collections.OrderedDict(
            (lab, dict(n=len(rows), valued_A=sum(1 for f in rows if f["mo_A_%s_c" % HN[h]] is not None),
                       valued_AB=sum(1 for f in rows if f["mo_AB_adj_%s_c" % HN[h]] is not None)))
            for lab, rows in (("flat_again_before_horizon", [f for f in opens if ttf[f["fill_idx"]] is not None and ttf[f["fill_idx"]] <= h]),
                              ("still_unpaired_at_horizon", [f for f in opens if ttf[f["fill_idx"]] is None or ttf[f["fill_idx"]] > h]))))
        for h in HORIZONS))

# in-sample check of the pair proxy, 14-18 Nov: a taker "exit" order paid the far touch (a known book price).
# Compare that touch with the first pair estimate placed within 30 s after the exit order.
# d = (touch paid - pair mid), signed so that d > 0 means the touch is on the expected side of the mid;
# a correct mid gives d = half the real spread (a fraction of a cent to about 1c).
fill_by_order = collections.defaultdict(list)
for f in fills: fill_by_order[f["order_id"]].append(f)
exv = []
for slug, v in ord_by_mkt.items():
    est = proxy[slug]; Ts = proxy_T[slug]
    for o in v:
        if "exit" not in o["stage"] or o["order_id"] not in fill_by_order: continue
        fp = fill_by_order[o["order_id"]][0]["px"]
        touch = fp if o["side"] == "YES" else 1.0 - fp
        j = bisect.bisect_right(Ts, o["T"])
        while j < len(est) and est[j]["T"] - o["T"] <= 30.0:
            if est[j]["tier"] == "A":
                m = est[j]["mid"]
                dd = (touch - m) if o["side"] == "YES" else (m - touch)
                exv.append(dict(d=round(100 * dd, 3), hw=round(100 * est[j]["hw"], 3), lag=est[j]["T"] - o["T"],
                                sport=sport_of(slug), date=o["t"][:10]))
                break
            j += 1
summary["proxy_check_vs_taker_exit_touch_14_18"] = collections.OrderedDict(
    what="touch price paid by a taker exit order minus the pair-proxy mid placed within 30 s after it (cents); "
         "expected value = half the real spread, so a small positive number means the proxy mid is right",
    n=len(exv), d_c=dist([r["d"] for r in exv]),
    share_between_0_and_pair_half_width=round(sum(-1e-9 <= r["d"] <= r["hw"] + 1e-9 for r in exv) / len(exv), 3) if exv else None,
    share_abs_le_half_cent=round(sum(abs(r["d"]) <= 0.5 + 1e-9 for r in exv) / len(exv), 3) if exv else None,
    median_pair_half_width_c=st.median(r["hw"] for r in exv) if exv else None,
    median_lag_s=round(st.median(r["lag"] for r in exv), 1) if exv else None,
    by_sport=dict(collections.Counter(r["sport"] for r in exv).most_common()),
    by_date=dict(collections.Counter(r["date"] for r in exv).most_common()),
    limit="Only the older controller of 14-15 Nov used taker exits (mostly college football, tight quotes). "
          "The 17-18 Nov quotes (wider, 5 contracts) have no such check.")

# OPEN fills split by whether the bot was flat again before the horizon
ost = collections.OrderedDict()
for v in VARIANTS:
    ost[v] = collections.OrderedDict()
    for h in HORIZONS:
        k = "mo_%s_%s_c" % (v, HN[h])
        ost[v][HN[h]] = collections.OrderedDict(
            flat_again_before_horizon=stat([f for f in opens if ttf[f["fill_idx"]] is not None and ttf[f["fill_idx"]] <= h], k),
            still_unpaired_at_horizon=stat([f for f in opens if ttf[f["fill_idx"]] is None or ttf[f["fill_idx"]] > h], k))
summary["OPEN_markout_by_state_at_horizon"] = ost

# cycles: from an OPEN fill until the market is flat again. Exact arithmetic on fills; a pair pays $1 whoever wins.
cycles = []
fb = collections.defaultdict(list)
for f in fills: fb[f["slug"]].append(f)
for slug, v in fb.items():
    v.sort(key=lambda f: (f["T"], f["fill_idx"]))
    cur = None
    for f in v:
        if f["role"] == "OPEN":
            cur = dict(slug=slug, sport=f["sport"], date=f["date"], t0=f["T"], open_side=f["side"], open_px=f["px"],
                       open_stage=f["stage"], y=0.0, n=0.0, cost=0.0, fills=0, t_flat=None, max_unpaired=0.0,
                       extend_fills=0, taker_fills=0)
            cycles.append(cur)
        if f["side"] == "YES": cur["y"] += f["qty"]
        else: cur["n"] += f["qty"]
        cur["cost"] += f["qty"] * f["px"]; cur["fills"] += 1
        cur["extend_fills"] += f["role"] == "EXTEND"; cur["taker_fills"] += f["taker_stage"]
        cur["max_unpaired"] = max(cur["max_unpaired"], abs(cur["y"] - cur["n"]))
        f["cycle_id"] = len(cycles) - 1
        if abs(cur["y"] - cur["n"]) < 1e-6:
            cur["t_flat"] = f["T"]
for c in cycles:
    pairs = min(c["y"], c["n"]); resid = abs(c["y"] - c["n"])
    c["qty"] = c["y"] + c["n"]; c["pairs"] = pairs; c["resid"] = resid
    c["ttf"] = None if c["t_flat"] is None else c["t_flat"] - c["t0"]
    if resid < 1e-6:
        c["pnl"] = pairs - c["cost"]
    else:
        sy = settle_yes.get(c["slug"])
        if sy is None:
            c["pnl"] = None
        else:
            rs = "YES" if c["y"] > c["n"] else "NO"
            c["pnl"] = pairs + resid * (sy if rs == "YES" else 1.0 - sy) - c["cost"]
    c["v"] = None if c["pnl"] is None else 100.0 * c["pnl"] / c["qty"]
    c["pair_cost"] = None if resid > 1e-6 or pairs <= 0 else c["cost"] / pairs
def ttf_bucket(c):
    if c["ttf"] is None: return "never paired (held to settlement)"
    t = c["ttf"]
    return "<30s" if t < 30 else "30s-2min" if t < 120 else "2-5min" if t < 300 else "5-15min" if t < 900 else ">15min"
def cyc_stat(rows):
    rows = [c for c in rows if c["pnl"] is not None]
    q = sum(c["qty"] for c in rows); p = sum(c["pnl"] for c in rows)
    pc = [c["pair_cost"] for c in rows if c["pair_cost"] is not None]
    return collections.OrderedDict(
        cycles=len(rows), markets=len(set(c["slug"] for c in rows)), contracts=round(q, 2), pnl_usd=round(p, 2),
        settled_c_per_contract=round(100 * p / q, 3) if q else None, ci95=boot_ci(rows, "v", True),
        mean_pair_cost=round(sum(c["cost"] for c in rows if c["pair_cost"] is not None) /
                             sum(c["pairs"] for c in rows if c["pair_cost"] is not None), 4) if pc else None,
        share_pair_cost_above_1=round(sum(x > 1 + 1e-9 for x in pc) / len(pc), 3) if pc else None,
        with_taker_fill=sum(1 for c in rows if c["taker_fills"]))
TB = ["<30s", "30s-2min", "2-5min", "5-15min", ">15min", "never paired (held to settlement)"]
cy = collections.OrderedDict(
    note="A cycle starts at an OPEN fill and ends when YES and NO are equal again. Result = pairs x $1 + unpaired "
         "contracts x settlement value - cost, before fees. Exact: needs no mid estimate. For paired cycles the "
         "winner does not matter; every never-paired cycle is in a market whose settlement is known.",
    all=cyc_stat(cycles), unresolved_cycles=sum(1 for c in cycles if c["pnl"] is None),
    by_time_to_pair=collections.OrderedDict((b, cyc_stat([c for c in cycles if ttf_bucket(c) == b])) for b in TB),
    by_date=collections.OrderedDict((d_, cyc_stat([c for c in cycles if c["date"] == d_])) for d_ in sorted(set(c["date"] for c in cycles))),
    by_open_stage=collections.OrderedDict((g, cyc_stat([c for c in cycles if c["open_stage"] == g]))
                                          for g, _ in collections.Counter(c["open_stage"] for c in cycles).most_common()),
    by_sport=collections.OrderedDict((g, cyc_stat([c for c in cycles if c["sport"] == g]))
                                     for g, _ in collections.Counter(c["sport"] for c in cycles).most_common()),
    by_open_price=collections.OrderedDict((g, cyc_stat([c for c in cycles if px_bucket(c["open_px"]) == g]))
                                          for g in ORDER["price_bucket"]),
    paid_up_vs_not=collections.OrderedDict(
        paired_with_taker_exit=cyc_stat([c for c in cycles if c["ttf"] is not None and c["taker_fills"]]),
        paired_maker_only=cyc_stat([c for c in cycles if c["ttf"] is not None and not c["taker_fills"]])))
summary["SETTLED_cycles_exact"] = cy
for f in fills:
    c = cycles[f["cycle_id"]]
    f["cycle_settled_c_per_contract"] = None if c["v"] is None else round(c["v"], 4)
    f["cycle_time_to_pair_s"] = None if c["ttf"] is None else round(c["ttf"], 2)

# where the first completion bid after an OPEN fill sits relative to the pair target of the filled quote.
# A bid parked exactly AT the target says nothing about an adverse move (it cannot follow the market up).
capc = collections.defaultdict(collections.Counter)
for f in opens:
    if f["quoted_edge_c"] is None: continue
    target = 1.0 - 2.0 * f["quoted_edge_c"] / 100.0
    nxt = [o for o in ord_by_mkt[f["slug"]] if f["T"] < o["T"] <= f["T"] + 30 and o["side"] != f["side"] and "complete" in o["stage"]]
    if not nxt:
        capc[f["stage"]]["no completion bid within 30s"] += 1; continue
    ssum = f["px"] + min(nxt, key=lambda o: o["T"])["px"]
    capc[f["stage"]]["at pair target" if abs(ssum - target) < 0.0011 else
                     "cheaper than target" if ssum < target else "above target (pays up)"] += 1
summary["first_completion_bid_vs_pair_target"] = {k: dict(v) for k, v in capc.items()}

# 22 Nov exact
ex = collections.OrderedDict()
ex["fills"] = [collections.OrderedDict(
    [("t", f["t"]), ("market", f["slug"]), ("side", f["side"]), ("px", f["px"]), ("qty", f["qty"]),
     ("role", f["role"]), ("unpaired_before", f["unpaired_before"]), ("stage", f["stage"]),
     ("rest_s", None if f["rest_s"] is None else round(f["rest_s"], 1)),
     ("mo_exact_0s_c", f["mo_exact_0s_c"])] +
    [("mo_exact_%s_c" % HN[h], f["mo_exact_%s_c" % HN[h]]) for h in HORIZONS] +
    [("missing_%s" % HN[h], f["exact_missing_%s" % HN[h]]) for h in HORIZONS if f["exact_missing_%s" % HN[h]]] +
    [("settled_c", f["settled_c"])]) for f in fills22]
ex["overall"] = collections.OrderedDict((HN[h], stat(fills22, "mo_exact_%s_c" % HN[h])) for h in HORIZONS)
ex["overall_vs_international_mid"] = collections.OrderedDict((HN[h], stat(fills22, "mo_exact_intl_%s_c" % HN[h])) for h in HORIZONS)
ex["by_role"] = collections.OrderedDict(
    (role, collections.OrderedDict((HN[h], stat([f for f in fills22 if f["role"] == role], "mo_exact_%s_c" % HN[h]))
                                   for h in HORIZONS)) for role in ("OPEN", "EXTEND", "COMPLETE"))
ex["settled"] = collections.OrderedDict(
    [("overall", stat(fills22, "settled_c"))] +
    [(role, stat([f for f in fills22 if f["role"] == role], "settled_c")) for role in ("OPEN", "EXTEND", "COMPLETE")])
ex["note"] = ("8 fills in 2 markets: no confidence interval is meaningful. Recording covers 14:07:34-14:16:01Z and "
              "from 14:20:32Z. Fill times are the bot's log times; the log says the venue had the two 14:28 fills "
              "about 30 s earlier, and the 14:40:52 / 14:44:08 times have one-second resolution.")
# the two late-logged fills re-measured from the venue time given in the campaign log (~14:28:07Z)
alt = []
for f in fills22:
    if f["t"].startswith("2026-11-22T14:28"):
        T0 = ts("2026-11-22T14:28:07Z")
        row = collections.OrderedDict(market=f["slug"], side=f["side"], px=f["px"], logged=f["t"], venue_time_approx="14:28:07Z")
        for h in [0] + HORIZONS:
            q = quote_at_or_after(f["slug"], T0 + h); m = usmid(q)
            row["mo_%s_c" % ("0s" if h == 0 else HN[h])] = None if m is None else round(100 * ((m if f["side"] == "YES" else 1 - m) - f["px"]), 3)
        alt.append(row)
ex["late_logged_fills_from_venue_time"] = alt
summary["EXACT_22Nov"] = ex

# ---------------------------------------------------------------- write
cols = ["src", "fill_idx", "t", "date", "slug", "sport", "side", "px", "qty", "order_id", "stage", "order_t", "order_px",
        "order_qty", "rest_s", "rest_bucket", "taker_stage", "pos_yes_before", "pos_no_before", "unpaired_before",
        "unpaired_bucket", "heavy_before", "role", "role_detail", "px_bucket", "mkt_pair_hw_c",
        "quoted_edge_c", "midmove_5s_c", "midmove_30s_c", "midmove_2m_c", "midmove_5m_c", "midmove_15m_c",
        "open_time_to_flat_s", "open_never_flat", "cycle_id", "cycle_time_to_pair_s", "cycle_settled_c_per_contract"]
for h in HORIZONS:
    cols += ["mo_%s_%s_c" % (v, HN[h]) for v in VARIANTS] + ["tier_%s" % HN[h], "lag_%s_s" % HN[h],
                                                            "esthw_%s_c" % HN[h], "estsrc_%s" % HN[h]]
cols += ["exact_mid_at_fill", "mo_exact_0s_c"]
for h in HORIZONS:
    cols += ["mo_exact_%s_c" % HN[h], "mo_exact_intl_%s_c" % HN[h], "exact_missing_%s" % HN[h]]
cols += ["settle_yes", "settled_value", "settled_c"]
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "l5_markout_fills.csv"), "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(cols)
    for f in sorted(fills, key=lambda f: (f["T"], f["fill_idx"])) + fills22:
        row = []
        for c in cols:
            x = f.get(c)
            if isinstance(x, float): x = round(x, 6)
            row.append("" if x is None else x)
        w.writerow(row)
with open(os.path.join(OUT, "l5_markout_summary.json"), "w") as fh:
    json.dump(summary, fh, indent=1)
print("fills 14-18:", len(fills), "contracts %.2f cost %.4f" % (tot_q, tot_c), "| 22 Nov fills:", len(fills22))
print("wrote", os.path.join(OUT, "l5_markout_fills.csv"), "and l5_markout_summary.json")
