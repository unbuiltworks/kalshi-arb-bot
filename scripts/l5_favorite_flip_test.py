"""The behavioral test of FAVORITE -> EXTEND -> FLIP -> SWITCH -> REPEAT.

Scored exactly as `analysis/l5/favorite_flip/PREREGISTRATION.md` fixed it
before any number was computed: exogenous favorite, transaction unit, BTC15m
only, chronological 70/30 window split (plus the preregistered within-August
secondary split), persistence as the baseline to beat, and tau chosen on
TRAIN only.

The label is L5's NEXT side, so each row predicts from state i to side i+1.
Rows are therefore joined WITHIN a window and the last action of a window has
no label.
"""
from __future__ import annotations
import argparse, collections, gzip, hashlib, json, math, os, random, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.l5.next_side.model import Logit, auc                # noqa: E402

STATE = "analysis/l5/favorite_flip/state.jsonl.gz"


# --------------------------------------------------------------- data

def load(path: str):
    rows = []
    with gzip.open(path, "rt") as f:
        prov = json.loads(f.readline())
        for line in f:
            rows.append(json.loads(line))
    return prov, rows


def label_next_side(rows):
    """Attach the NEXT action's side/qty/price within the same window."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r["slug"]].append(r)
    out = []
    for slug, rs in by.items():
        rs.sort(key=lambda r: (r["t"], r["tx_i"]))
        for a, b in zip(rs, rs[1:]):
            a = dict(a)
            a["next_side"] = b["side"]
            a["next_qty"] = b["qty"]
            a["next_price"] = b["price"]
            a["next_dt"] = b["t"] - a["t"]
            a["switch"] = b["side"] != a["side"]
            out.append(a)
    out.sort(key=lambda r: (r["t"], r["tx_i"]))
    return out


def split(rows, mode: str):
    """Preregistered splits. Windows never span the boundary."""
    slugs = sorted({r["slug"] for r in rows}, key=lambda s: int(s.rsplit("-", 1)[1]))
    if mode == "chrono":
        cut = int(len(slugs) * 0.70)
        tr, te = set(slugs[:cut]), set(slugs[cut:])
    elif mode == "august":
        aug = [s for s in slugs if int(s.rsplit("-", 1)[1]) >= 1787000000]
        tr, te = set(), set()
        for s in aug:
            h = hashlib.sha256(s.encode()).hexdigest()
            (tr if int(h[:8], 16) % 10 < 7 else te).add(s)
    else:
        raise ValueError(mode)
    return ([r for r in rows if r["slug"] in tr],
            [r for r in rows if r["slug"] in te])


# --------------------------------------------------------------- helpers

def wilson(k: int, n: int):
    if not n:
        return (None, None, None)
    p = k / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(p, 4), round(c - hw, 4), round(c + hw, 4))


def quant(xs, qs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {}
    return {str(q): round(xs[min(len(xs) - 1, int(q * len(xs)))], 4) for q in qs}


def boot_margin(rows, pred_a, pred_b, n_boot=400, seed=11):
    """Window-level bootstrap of (acc_a - acc_b). Windows are the resampling
    unit because actions inside one window are not independent."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r["slug"]].append(r)
    slugs = list(by)
    rnd = random.Random(seed)
    out = []
    for _ in range(n_boot):
        pick = [slugs[rnd.randrange(len(slugs))] for _ in slugs]
        n = ca = cb = 0
        for s in pick:
            for r in by[s]:
                n += 1
                ca += pred_a(r) == r["next_side"]
                cb += pred_b(r) == r["next_side"]
        if n:
            out.append(ca / n - cb / n)
    out.sort()
    return {"mean": round(sum(out) / len(out), 4),
            "p2.5": round(out[int(0.025 * len(out))], 4),
            "p97.5": round(out[int(0.975 * len(out))], 4)}


# --------------------------------------------------------------- models

def m_persistence(r):
    return r["side"]


def m_rebalance(r):
    return "Down" if r["signed_imbalance"] > 0 else "Up"


def m_favorite(r):
    return r["favorite"] or r["side"]


def m_fav_mom(r):
    """Favorite, but momentum decides when the favorite is undefined or the
    price sits inside the dead zone. No threshold is fitted here."""
    f = r["favorite"]
    if f is None:
        m = r.get("mom_5s")
        return r["side"] if m is None else ("Up" if m > 0 else "Down")
    return f


ACC = lambda rows, fn: (sum(fn(r) == r["next_side"] for r in rows) / len(rows)
                        if rows else None)


FEATS = {
    "inventory": ["signed_imbalance", "abs_imbalance", "up_qty", "dn_qty",
                  "is_up"],
    "fav_mom": ["p_up", "dist50", "fav_is_up", "mom5", "mom15", "mom30",
                "flip", "consec"],
}
FEATS["fav_mom_inv"] = FEATS["fav_mom"] + FEATS["inventory"]


def vec(r, names):
    g = {
        "signed_imbalance": r["signed_imbalance"],
        "abs_imbalance": r["abs_imbalance"],
        "up_qty": r["up_qty"], "dn_qty": r["dn_qty"],
        "is_up": 1.0 if r["side"] == "Up" else 0.0,
        "p_up": r["p_up_ex"] if r["p_up_ex"] is not None else 0.5,
        "dist50": r["dist_from_50"] if r["dist_from_50"] is not None else 0.0,
        "fav_is_up": (1.0 if r["favorite"] == "Up"
                      else (-1.0 if r["favorite"] == "Down" else 0.0)),
        "mom5": r.get("mom_5s") or 0.0,
        "mom15": r.get("mom_15s") or 0.0,
        "mom30": r.get("mom_30s") or 0.0,
        "flip": 1.0 if r.get("flip_since_prev_action") else 0.0,
        "consec": float(r["consec_fav_buys"]),
    }
    return [float(g[n]) for n in names]


def fit_logit(train, test, names):
    X = [vec(r, names) for r in train]
    y = [1.0 if r["next_side"] == "Up" else 0.0 for r in train]
    mdl = Logit(l2=1.0, iters=300, lr=0.5).fit(X, y)
    p = mdl.predict([vec(r, names) for r in test])
    yt = [1.0 if r["next_side"] == "Up" else 0.0 for r in test]
    acc = sum((pi >= 0.5) == (yi > 0.5) for pi, yi in zip(p, yt)) / len(yt)
    return mdl, {"acc": round(acc, 4), "auc": round(auc(yt, p) or 0.0, 4),
                 "coef": mdl.coefficients(names)[:6]}, p


# --------------------------------------------------------------- questions

def q1_favorite_preference(rows):
    """P(buy favorite) against the base rate implied by L5's own side mix.

    The right base rate is NOT 0.5. If L5 bought Up in a fraction u of
    actions for reasons unrelated to the favorite, he would still agree with
    the favorite whenever the favorite happened to be Up. So the null is
    sum over rows of u if fav==Up else (1-u), which is what a side-blind
    trader with his mix would score against this favorite series.
    """
    d = [r for r in rows if r["bought_favorite"] is not None]
    k = sum(r["bought_favorite"] for r in d)
    u = sum(r["side"] == "Up" for r in d) / len(d)
    null = sum(u if r["favorite"] == "Up" else 1 - u for r in d) / len(d)
    per_w = []
    by = collections.defaultdict(list)
    for r in d:
        by[r["slug"]].append(r)
    for s, rs in by.items():
        per_w.append(sum(x["bought_favorite"] for x in rs) / len(rs))
    return {"n": len(d), "p_buy_favorite": wilson(k, len(d)),
            "side_mix_up": round(u, 4),
            "side_blind_base_rate": round(null, 4),
            "lift": round(k / len(d) - null, 4),
            "windows": len(per_w),
            "per_window_median": round(statistics.median(per_w), 4),
            "per_window_share_above_base": round(
                sum(1 for x in per_w if x > null) / len(per_w), 4),
            "by_dist50": {b: wilson(sum(x["bought_favorite"] for x in g),
                                    len(g))
                          for b, g in _bin(d, "dist_from_50",
                                           (0.05, 0.1, 0.2, 0.3)).items()}}


def _bin(rows, key, edges):
    out = collections.defaultdict(list)
    for r in rows:
        v = r.get(key)
        if v is None:
            out["null"].append(r); continue
        lab = "<%g" % edges[0]
        for i, e in enumerate(edges):
            if v >= e:
                lab = (">=%g" % e if i == len(edges) - 1
                       else "%g-%g" % (e, edges[i + 1]))
        out[lab].append(r)
    return out


def q2_flip_response(rows):
    f = [r for r in rows if r.get("flip_since_prev_action") is True]
    n = [r for r in rows if r.get("flip_since_prev_action") is False]
    lat = []
    for r in rows:
        if r["switch"] and r.get("flip_latency_s") is not None:
            lat.append(r["flip_latency_s"])
    return {"n_flip": len(f), "n_noflip": len(n),
            "p_switch_given_flip": wilson(sum(r["switch"] for r in f), len(f)),
            "p_switch_given_noflip": wilson(sum(r["switch"] for r in n),
                                            len(n)),
            "switch_latency_from_last_flip_s": quant(lat),
            "n_latency": len(lat),
            "p_switch_by_flip_latency": {
                b: wilson(sum(x["switch"] for x in g), len(g))
                for b, g in _bin(rows, "flip_latency_s",
                                 (5, 15, 60, 180)).items()}}


def q3_momentum_extension(rows):
    """Conditional on L5 already on the favorite, does the favorite
    strengthening raise P(same side again) or the size of it?"""
    on = [r for r in rows if r["favorite"] and r["side"] == r["favorite"]]
    out = {}
    for h in (5, 15, 30):
        g = []
        for r in on:
            m = r.get("mom_%ds" % h)
            if m is None:
                continue
            toward = m if r["favorite"] == "Up" else -m
            g.append((toward, r))
        g.sort(key=lambda kv: kv[0])
        if len(g) < 200:
            out["mom_%ds" % h] = {"n": len(g), "verdict": "INSUFFICIENT"}
            continue
        terts = [g[:len(g) // 3], g[len(g) // 3:2 * len(g) // 3],
                 g[2 * len(g) // 3:]]
        out["mom_%ds" % h] = {
            "n": len(g),
            "tertiles_toward_favorite": [
                {"mom_range": [round(t[0][0], 4), round(t[-1][0], 4)],
                 "p_same_side_next": wilson(
                     sum(1 for _, r in t if r["next_side"] == r["side"]),
                     len(t)),
                 "median_next_qty": round(statistics.median(
                     [r["next_qty"] for _, r in t]), 3)}
                for t in terts]}
    return out


def q4_inventory_interaction(rows, train, test):
    """Does the favorite still predict the next side after conditioning on
    aggregate imbalance? Stratified, then as nested logits."""
    strat = {}
    for b, g in _bin(rows, "abs_imbalance", (10, 50, 200)).items():
        d = [r for r in g if r["favorite"]]
        if len(d) < 100:
            strat[b] = {"n": len(d), "verdict": "INSUFFICIENT"}
            continue
        strat[b] = {
            "n": len(d),
            "p_next_is_favorite": wilson(
                sum(r["next_side"] == r["favorite"] for r in d), len(d)),
            "p_next_is_prev_side": wilson(
                sum(r["next_side"] == r["side"] for r in d), len(d)),
            "p_next_repairs_imbalance": wilson(
                sum(r["next_side"] == ("Down" if r["signed_imbalance"] > 0
                                       else "Up") for r in d), len(d))}
    # the discriminating cell: favorite and rebalance DISAGREE
    dis = [r for r in rows if r["favorite"] and r["signed_imbalance"] != 0
           and r["favorite"] != ("Down" if r["signed_imbalance"] > 0 else "Up")]
    agree = [r for r in rows if r["favorite"] and r["signed_imbalance"] != 0
             and r["favorite"] == ("Down" if r["signed_imbalance"] > 0
                                   else "Up")]
    nested = {}
    for name in ("inventory", "fav_mom", "fav_mom_inv"):
        _, sc, _ = fit_logit(train, test, FEATS[name])
        nested[name] = sc
    return {"stratified_by_abs_imbalance": strat,
            "disagreement_cell": {
                "n": len(dis),
                "p_next_is_favorite": wilson(
                    sum(r["next_side"] == r["favorite"] for r in dis),
                    len(dis)),
                "p_next_is_rebalance": wilson(
                    sum(r["next_side"] != r["favorite"] for r in dis),
                    len(dis))},
            "agreement_cell_n": len(agree),
            "nested_logits_heldout": nested}


def q5_fifty_cents(train):
    """TRAIN ONLY. Is the response to price discontinuous at 0.50?"""
    bins = collections.defaultdict(list)
    for r in train:
        if r["p_up_ex"] is None:
            continue
        b = min(19, int(r["p_up_ex"] * 20))
        bins[b].append(1.0 if r["next_side"] == "Up" else 0.0)
    curve = [{"p_up_bin": [round(b / 20, 2), round((b + 1) / 20, 2)],
              "n": len(v), "p_next_up": round(sum(v) / len(v), 4)}
             for b, v in sorted(bins.items()) if len(v) >= 30]
    d = [r for r in train if r["p_up_ex"] is not None]
    y = [1.0 if r["next_side"] == "Up" else 0.0 for r in d]

    def ll(p):
        return -sum(yi * math.log(max(pi, 1e-9))
                    + (1 - yi) * math.log(max(1 - pi, 1e-9))
                    for yi, pi in zip(y, p)) / len(y)

    cont = Logit(l2=1.0, iters=400).fit([[r["p_up_ex"]] for r in d], y)
    p_cont = cont.predict([[r["p_up_ex"]] for r in d])
    step = Logit(l2=1.0, iters=400).fit(
        [[1.0 if r["p_up_ex"] > 0.5 else 0.0] for r in d], y)
    p_step = step.predict([[1.0 if r["p_up_ex"] > 0.5 else 0.0] for r in d])
    both = Logit(l2=1.0, iters=400).fit(
        [[r["p_up_ex"], 1.0 if r["p_up_ex"] > 0.5 else 0.0] for r in d], y)
    p_both = both.predict(
        [[r["p_up_ex"], 1.0 if r["p_up_ex"] > 0.5 else 0.0] for r in d])
    taus = {}
    for tau in (0.50, 0.52, 0.55, 0.60, 0.65, 0.70):
        pred = ["Up" if r["p_up_ex"] > tau else
                ("Down" if r["p_up_ex"] < 1 - tau else r["side"]) for r in d]
        taus[str(tau)] = round(
            sum(p == r["next_side"] for p, r in zip(pred, d)) / len(d), 4)
    return {"response_curve_train": curve,
            "train_logloss": {"continuous_p_up": round(ll(p_cont), 4),
                              "step_at_0.50": round(ll(p_step), 4),
                              "both": round(ll(p_both), 4)},
            "step_coef_when_both": both.coefficients(["p_up", "above_50"]),
            "tau_grid_train_accuracy": taus}


def q6_sequential(train, test):
    rules = {"persistence": m_persistence, "inventory_rebalance": m_rebalance,
             "current_favorite": m_favorite, "favorite_plus_momentum": m_fav_mom}
    out = {"rules_heldout_accuracy": {k: round(ACC(test, f), 4)
                                      for k, f in rules.items()},
           "rules_train_accuracy": {k: round(ACC(train, f), 4)
                                    for k, f in rules.items()}}
    fits = {}
    for name in ("inventory", "fav_mom", "fav_mom_inv"):
        _, sc, _ = fit_logit(train, test, FEATS[name])
        fits["logit_" + name] = sc
    out["fitted_heldout"] = fits
    out["margin_vs_persistence_bootstrap"] = {
        k: boot_margin(test, f, m_persistence)
        for k, f in rules.items() if k != "persistence"}
    # size / timing, only where the state supports it
    on_fav = [r for r in test if r["favorite"] and r["side"] == r["favorite"]]
    off = [r for r in test if r["favorite"] and r["side"] != r["favorite"]]
    out["size_timing"] = {
        "median_next_qty_on_favorite": (round(statistics.median(
            [r["next_qty"] for r in on_fav]), 3) if on_fav else None),
        "median_next_qty_off_favorite": (round(statistics.median(
            [r["next_qty"] for r in off]), 3) if off else None),
        "median_next_dt_on_favorite": (statistics.median(
            [r["next_dt"] for r in on_fav]) if on_fav else None),
        "median_next_dt_off_favorite": (statistics.median(
            [r["next_dt"] for r in off]) if off else None),
        "n_on": len(on_fav), "n_off": len(off)}
    return out


ERA_CUT = 1780000000          # Mar-Apr ramp vs the August regime


def era(r):
    return "spring" if int(r["slug"].rsplit("-", 1)[1]) < ERA_CUT else "august"


def by_era(rows, fn):
    """Same question inside each era. Prior art already showed a fitted side
    model transferring 4 points WORSE than persistence across this boundary,
    so a pooled number here would hide the thing most likely to matter."""
    out = {}
    for e in ("spring", "august"):
        g = [r for r in rows if era(r) == e]
        out[e] = {"n": len(g)} if len(g) < 200 else fn(g)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--split", default="chrono", choices=("chrono", "august"))
    ap.add_argument("--out", default="analysis/l5/favorite_flip/result.json")
    a = ap.parse_args()
    prov, raw = load(a.state)
    rows = label_next_side(raw)
    train, test = split(rows, a.split)
    res = {"provenance": prov, "split": a.split,
           "n_rows_labelled": len(rows),
           "n_train": len(train), "n_test": len(test),
           "n_windows_train": len({r["slug"] for r in train}),
           "n_windows_test": len({r["slug"] for r in test}),
           "momentum_coverage": {
               "mom_%ds" % h: round(sum(1 for r in rows
                                        if r.get("mom_%ds" % h) is not None)
                                    / len(rows), 4) for h in (1, 5, 15, 30)},
           "q1_favorite_preference": q1_favorite_preference(rows),
           "q2_flip_response": q2_flip_response(rows),
           "q1_by_era": by_era(rows, q1_favorite_preference),
           "q2_by_era": by_era(rows, q2_flip_response),
           "persistence_accuracy_by_era": {
               e: {"n": len(g),
                   "p_next_same_side": round(ACC(g, m_persistence), 4),
                   "p_next_is_favorite": round(ACC(g, m_favorite), 4)}
               for e, g in (("spring", [r for r in rows
                                        if era(r) == "spring"]),
                            ("august", [r for r in rows
                                        if era(r) == "august"]))},
           "q3_momentum_extension": q3_momentum_extension(rows),
           "q4_inventory_interaction": q4_inventory_interaction(rows, train,
                                                                test),
           "q5_fifty_cents_TRAIN_ONLY": q5_fifty_cents(train),
           "q6_sequential": q6_sequential(train, test)}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items()
                      if k.startswith("n_") or k == "momentum_coverage"},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
