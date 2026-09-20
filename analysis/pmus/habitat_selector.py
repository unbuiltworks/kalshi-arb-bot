"""HABITAT SELECTOR -- can information available BEFORE trading identify markets
whose SUBSEQUENT flow imbalance resembles L5's maker-ladder habitat?

This is not a trading optimizer. It never sees P&L, settlement, our own
acquisitions or any future fill. Its target is a property of the MARKET.

THE TARGET. 81f77fea proved R/M = 2I/(1-I) exactly, so the habitat is fully
described by ONE number: the imbalance of the flow a resting two-sided maker can
actually capture. Direction is not carried by the PMUS tape, but it IS supportable
against the prevailing book, because a resting maker is only reachable from one
side at a time:

    print at px <= bid   a seller hit the bid   -> reaches a resting BUY YES
    print at px >= ask   a buyer lifted the ask -> reaches a resting BUY NO
    bid < px < ask       direction UNSUPPORTED  -> excluded, and its share is
                         reported as a feature so the exclusion is never silent

    I = |V_yes_reachable - V_no_reachable| / (V_yes_reachable + V_no_reachable)

Computed on a FORWARD window this is the target. Computed on a PAST window it is
a feature. The two never share an observation: the feature window ends strictly
before the target window begins, so no future information can enter a feature.

HORIZONS ARE FROZEN BEFORE ANY RESULT IS SEEN, and come from previously measured
episode statistics rather than a search: LOOKBACK 600s because that is the
evidence a market would have accumulated before we would consider it, HORIZON
1800s because the PMUS replay episodes had a median active span of 16 minutes,
so 30 minutes covers a typical episode end to end.

L5's maker-ladder habitat sits at I = 0.083. PMUS per-market I has p10 = 0.029,
so balanced PMUS markets EXIST. The only question here is whether they can be
told apart in advance.
"""
import bisect, collections, math, statistics as stt

LOOKBACK_S = 600.0
HORIZON_S = 1800.0
MIN_PRINTS_FEATURE = 5          # a feature window with less is not an observation
MIN_PRINTS_TARGET = 10          # a target window with less cannot define imbalance
L5_HABITAT_I = 0.083


def book_at(bt, se, t):
    """Last book observation at or before t. bt is the sorted list of book times."""
    j = bisect.bisect_right(bt, t) - 1
    return se[j] if j >= 0 else None


def classify(prints, se, bt, t0, t1):
    """Split flow in (t0, t1] into the side a resting maker could have been
    reached from. Returns (v_yes, v_no, n_yes, n_no, v_ambiguous, n_ambiguous)."""
    vy = vn = va = 0.0
    ny = nn = na = 0
    for p in prints:
        if p["t"] <= t0:
            continue
        if p["t"] > t1:
            break
        b = book_at(bt, se, p["t"])
        if b is None:
            continue
        if p["px"] <= b["bid"] + 1e-9:
            vy += p["size"]; ny += 1
        elif p["px"] >= b["ask"] - 1e-9:
            vn += p["size"]; nn += 1
        else:
            va += p["size"]; na += 1
    return vy, vn, ny, nn, va, na


def imbalance(a, b):
    t = a + b
    return abs(a - b) / t if t > 0 else None


def features(se, bt, prints, t):
    """Every input exists at time t. Nothing here reads past t."""
    t0 = t - LOOKBACK_S
    vy, vn, ny, nn, va, na = classify(prints, se, bt, t0, t)
    n = ny + nn
    if n < MIN_PRINTS_FEATURE:
        return None
    lo = bisect.bisect_right(bt, t0)
    hi = bisect.bisect_right(bt, t)
    obs = se[lo:hi]
    if len(obs) < 3:
        return None
    spreads = [o["ask"] - o["bid"] for o in obs]
    mids = [(o["ask"] + o["bid"]) / 2.0 for o in obs]
    dq = [(float(o["bid_qty"] or 0.0), float(o["ask_qty"] or 0.0)) for o in obs]
    two_sided_depth = [min(a, b) for a, b in dq]
    depth_ratio = [(min(a, b) / max(a, b)) for a, b in dq if max(a, b) > 0]
    stable = sum(1 for i in range(1, len(obs))
                 if obs[i]["bid"] == obs[i - 1]["bid"] and obs[i]["ask"] == obs[i - 1]["ask"])
    mins = LOOKBACK_S / 60.0
    return {
        "imb_vol_lb": imbalance(vy, vn),
        "imb_cnt_lb": imbalance(ny, nn),
        "print_rate": n / mins,
        "two_sided_rate": min(ny, nn) / mins,
        "min_side_share": (min(ny, nn) / n) if n else 0.0,
        "ambiguous_share": (na / (n + na)) if (n + na) else 0.0,
        "spread_med": stt.median(spreads),
        "depth_two_sided": stt.median(two_sided_depth),
        "depth_ratio": stt.median(depth_ratio) if depth_ratio else 0.0,
        "touch_stability": stable / max(1, len(obs) - 1),
        "drift_abs": abs(mids[-1] - mids[0]),
        "mid_dist_half": abs(mids[-1] - 0.5),
        "n_prints_lb": n,
    }


def target(se, bt, prints, t):
    """Realized flow imbalance over the FORWARD window. Never a feature."""
    vy, vn, ny, nn, va, na = classify(prints, se, bt, t, t + HORIZON_S)
    if ny + nn < MIN_PRINTS_TARGET:
        return None
    return {"I_fwd": imbalance(vy, vn),
            "two_sided_fwd": min(ny, nn) / (HORIZON_S / 60.0),
            "n_fwd": ny + nn}


def observations(books, flows, venue="PMUS", stride_s=300.0):
    """One row per (market, decision time) on a fixed grid. The feature window
    ends at t and the target window starts at t, so they never overlap."""
    out = []
    for (v, slug), se in books.items():
        if v != venue or len(se) < 10:
            continue
        pr = flows.get((v, slug), [])
        if len(pr) < MIN_PRINTS_FEATURE + MIN_PRINTS_TARGET:
            continue
        bt = [o["t"] for o in se]
        t_lo, t_hi = se[0]["t"] + LOOKBACK_S, se[-1]["t"] - HORIZON_S
        t = t_lo
        while t <= t_hi:
            f = features(se, bt, pr, t)
            if f is not None:
                g = target(se, bt, pr, t)
                if g is not None:
                    row = {"slug": slug, "t": t,
                           "league": se[0].get("league"),
                           "sports_type": se[0].get("sports_type")}
                    row.update(f); row.update(g)
                    out.append(row)
            t += stride_s
    out.sort(key=lambda r: r["t"])
    return out


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else 0.0


# ---------------------------------------------------------------------------
# THE FROZEN SELECTOR
#
# Fitted on TRAIN rows only (chronologically earliest 70%, with every market that
# straddled the cut dropped entirely so no market contributes to both sides), then
# never refitted. It is deliberately three features and an average of ranks: a
# causal selector that can be read and argued with, not a fitted scorer.
#
# The three were chosen because their TRAIN sign is MECHANISTICALLY EXPLICABLE and
# they agree with each other, not because they were the largest coefficients:
#
#   mid_dist_half   +0.226  a market priced near 0.50 has genuine two-way opinion;
#                           one at 0.90 has a crowd on one side and nothing on the other
#   imb_cnt_lb      +0.207  imbalance is persistent -- a market that has been
#                           one-sided for ten minutes tends to stay one-sided
#   min_side_share  -0.207  the same thing from the other end: flow that has already
#                           arrived on BOTH sides is the direct evidence we want
#
# drift_abs scored -0.213 on TRAIN and is EXCLUDED, because its sign says larger
# short-horizon price movement predicts BETTER balance and no mechanism was found
# that explains that. Including a feature whose direction cannot be argued is how a
# selector gets fitted to noise. It is reported, not used.
#
# NO ECONOMIC QUANTITY IS AN INPUT. No P&L, no settlement, no fill of ours, and no
# observation from after the decision instant enters any of the three.
FROZEN_FEATURES = (("mid_dist_half", +1), ("imb_cnt_lb", +1), ("min_side_share", -1))
FROZEN_CUTOFF_Q = 1.0 / 3.0       # preregistered: the best TRAIN tercile, chosen
                                  # before any economics were computed


def fit_reference(train_rows):
    """The TRAIN distribution each feature is ranked against. This IS the frozen
    model: three sorted lists and a cutoff."""
    ref = {f: sorted(r[f] for r in train_rows) for f, _ in FROZEN_FEATURES}
    scores = sorted(score(r, ref) for r in train_rows)
    cut = scores[int(FROZEN_CUTOFF_Q * len(scores))]
    return {"ref": ref, "cutoff": cut}


def _pct(sorted_vals, x):
    return bisect.bisect_left(sorted_vals, x) / max(1, len(sorted_vals))


def score(row, ref):
    """Mean percentile rank across the three, signed so LOWER is better habitat."""
    acc = 0.0
    for f, sign in FROZEN_FEATURES:
        p = _pct(ref[f], row[f])
        acc += p if sign > 0 else (1.0 - p)
    return acc / len(FROZEN_FEATURES)


def eligible(row, model):
    return score(row, model["ref"]) <= model["cutoff"]
