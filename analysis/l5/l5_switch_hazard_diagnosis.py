"""P0: why does real L5 switch sides when frozen Model A does not?

Historical L5 corpus only. Nothing here is fitted on today's PMUS episodes and
nothing here changes Model A. Diagnosis only.

Unit of analysis: every fill that has a predecessor. The state is built from
fills 0..i-1 ONLY -- no value from fill i or later enters a feature, so the
label (did L5 acquire the OPPOSITE outcome next?) cannot leak backwards.

Chronological out-of-sample split on the window's FIRST timestamp: earliest 70%
of windows are IN-SAMPLE, latest 30% are OOS. No window spans the boundary.
"""
import gzip, csv, math, collections, json, random

CORPUS = "/home/user/kalshi-arb-bot/analysis/l5/l5_canonical_sequence.csv.gz"


def load():
    with gzip.open(CORPUS, "rt") as fh:
        rows = list(csv.DictReader(fh))
    w = collections.defaultdict(list)
    for r in rows:
        w[r["window_id"]].append(r)
    for k in w:
        w[k].sort(key=lambda r: (int(r["trade_seq"]), float(r["timestamp"])))
    return w


def f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def up_px(r):
    """Every price expressed in YES/Up terms so movement is comparable."""
    p = f(r["price"])
    return p if r["outcome"] == "Up" else (1.0 - p)


def states(fills):
    """One row per decision point. Features use fills[:i] only."""
    out = []
    run_len = 1
    run_start_px = up_px(fills[0])
    for i in range(1, len(fills)):
        prev, cur = fills[i - 1], fills[i]
        if i >= 2 and fills[i - 1]["outcome"] == fills[i - 2]["outcome"]:
            run_len += 1
        elif i >= 2:
            run_len = 1
            run_start_px = up_px(fills[i - 1])
        qu, qd = f(prev["cum_up_qty"], 0.0), f(prev["cum_down_qty"], 0.0)
        su, sd = f(prev["cum_up_spend"], 0.0), f(prev["cum_down_spend"], 0.0)
        resid = f(prev["residual_qty_signed"], 0.0)
        matched = f(prev["matched_qty"], 0.0)
        pu = up_px(prev)
        surplus_up = resid > 0
        avg_surplus = (su / qu if surplus_up and qu else
                       (sd / qd if (not surplus_up) and qd else None))
        p_surplus = pu if surplus_up else (1.0 - pu)
        complement = 1.0 - p_surplus
        pair_cost = (avg_surplus + complement) if avg_surplus is not None else None
        mv_prev = pu - up_px(fills[i - 2]) if i >= 2 else 0.0
        mv_run = pu - run_start_px
        dt_prev = (f(prev["timestamp"]) - f(fills[i - 2]["timestamp"])
                   if i >= 2 else 0.0)
        # complement cost at the start of this run, in surplus terms
        rs_surplus = run_start_px if surplus_up else (1.0 - run_start_px)
        comp_run_start = 1.0 - rs_surplus
        out.append({
            "window_id": prev["window_id"], "t": f(prev["timestamp"]),
            "run_len": run_len,
            "n_fills": i,
            "qty_up": qu, "qty_down": qd, "matched": matched,
            "residual_abs": abs(resid), "residual_signed": resid,
            "imbalance_abs": abs(qu - qd), "imbalance_signed": qu - qd,
            "avg_cost_up": (su / qu) if qu else None,
            "avg_cost_down": (sd / qd) if qd else None,
            "avg_cost_surplus": avg_surplus,
            "price_surplus": p_surplus,
            "complement_price": complement,
            "pair_cost_if_complete_now": pair_cost,
            "complement_cheaper_than_run_start": (
                1.0 if complement < comp_run_start - 1e-9 else 0.0),
            "complement_delta_vs_run_start": complement - comp_run_start,
            "move_since_prev_fill": mv_prev,
            "move_since_run_began": mv_run,
            "tte_s": f(prev["seconds_to_settlement"], 0.0),
            "dt_since_prev_fill": dt_prev,
            "cum_spend": su + sd,
            "maker": 1.0 if prev["maker_taker"] == "maker" else 0.0,
            # LABEL: did L5 acquire the OPPOSITE outcome next?
            "y": 1 if cur["outcome"] != prev["outcome"] else 0,
        })
    return out


def build():
    w = load()
    first = {k: min(f(r["timestamp"]) for r in v) for k, v in w.items()}
    order = sorted(w, key=lambda k: first[k])
    cut = int(0.70 * len(order))
    ins, oos = set(order[:cut]), set(order[cut:])
    rows = []
    for k, v in w.items():
        if len(v) < 2:
            continue
        for s in states(v):
            s["split"] = "IN" if k in ins else "OOS"
            rows.append(s)
    return rows, first, order, cut


FEATS = ["run_len","n_fills","matched","residual_abs","imbalance_abs",
         "avg_cost_surplus","price_surplus","complement_price",
         "pair_cost_if_complete_now","complement_cheaper_than_run_start",
         "complement_delta_vs_run_start","move_since_prev_fill",
         "move_since_run_began","tte_s","dt_since_prev_fill","cum_spend",
         "maker","qty_up","qty_down"]


def clean(rows, feats):
    out = []
    for r in rows:
        if any(r.get(k) is None for k in feats):
            continue
        out.append(r)
    return out


def zstats(rows, feats):
    st = {}
    for k in feats:
        v = [r[k] for r in rows]
        m = sum(v) / len(v)
        sd = (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5 or 1.0
        st[k] = (m, sd)
    return st


def vec(r, feats, st):
    return [1.0] + [(r[k] - st[k][0]) / st[k][1] for k in feats]


def fit_logistic(rows, feats, st, epochs=60, lr=0.35, l2=1e-4):
    import random
    w = [0.0] * (len(feats) + 1)
    idx = list(range(len(rows)))
    rnd = random.Random(17)
    for ep in range(epochs):
        rnd.shuffle(idx)
        for j in idx:
            r = rows[j]
            x = vec(r, feats, st)
            z = sum(a * b for a, b in zip(w, x))
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            g = p - r["y"]
            for k in range(len(w)):
                w[k] -= lr * (g * x[k] + (l2 * w[k] if k else 0.0))
        lr *= 0.94
    return w


def predict(w, r, feats, st):
    z = sum(a * b for a, b in zip(w, vec(r, feats, st)))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def logloss(ps, ys):
    e = 1e-12
    return -sum(y * math.log(max(e, p)) + (1 - y) * math.log(max(e, 1 - p))
                for p, y in zip(ps, ys)) / len(ys)


def auc(ps, ys):
    pairs = sorted(zip(ps, ys))
    n1 = sum(ys); n0 = len(ys) - n1
    if not n1 or not n0:
        return None
    rank = 0.0; i = 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg = (i + j - 1) / 2.0 + 1
        for k in range(i, j):
            if pairs[k][1] == 1:
                rank += avg
        i = j
    return (rank - n1 * (n1 + 1) / 2.0) / (n1 * n0)
