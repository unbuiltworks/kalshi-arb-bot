"""CANONICAL SAMPLING RULE FOR FLOW IMBALANCE.  Established 2026-09-20.

    RAW IMBALANCE CANNOT BE COMPARED ACROSS UNEQUAL PRINT COUNTS.
    I_excess, OR MATCHED-SAMPLE INFERENCE, IS CANONICAL.

This is a permanent methodological result, not a parameter. It is kept in its own
module, importing nothing from the frozen selector, so that the habitat selector
frozen at 2026-09-20T19:52:46Z (habitat_selector_frozen.json, sha256
f9dcd886549a2865) stays byte-identical while this rule is available to every
future venue and category the same framework is pointed at.

WHY IT EXISTS. A perfectly balanced market -- one whose flow is a fair coin
between the two sides a resting maker can be reached from -- still MEASURES a
non-zero imbalance from a finite number of prints:

    n =    10    18    30    50   100  1000  58312
  E[I] = 0.246 0.185 0.144 0.112 0.079 0.025  0.003

L5's maker-ladder habitat measured I = 0.083 across 58,312 fills, where the floor
is 0.003, so essentially all of that 0.083 is real imbalance. A PMUS forward
window carries a median of 18 prints, where the floor is 0.185. Reading 0.083 in
such a window is not evidence of an L5-quality habitat; it is what a fair coin
does roughly half the time. Every one of the 30 PMUS rows at or below 0.083 was
also at or below its own floor. That reading is what produced, and then killed,
the "balanced PMUS markets exist" hypothesis (graveyard #9).

TWO LEGITIMATE COMPARISONS, AND NO OTHERS:
  1. EXCESS   compare I - floor(n), so each observation is scored against what its
              own print count would produce under perfect balance.
  2. MATCHED  resample the richer series down to the sparser one's n and compare
              the resulting DISTRIBUTIONS quantile by quantile.
Comparing two raw I values at different n is not one of them, and `compare_raw`
below refuses to do it rather than returning a number that cannot carry a claim.
"""
import math
import random

_FLOOR_CACHE = {}

# Above this the exact sum costs more than it is worth and the normal
# approximation sqrt(2/(pi*n)) is accurate to better than 0.1% -- verified
# against the exact value at the boundary by a regression test.
EXACT_MAX_N = 20000


def noise_floor(n):
    """E[ |2y-n| / n ] for y ~ Binomial(n, 1/2): the imbalance a PERFECTLY
    BALANCED market shows at n prints. Exact, not simulated, so a reported floor
    is reproducible to the last digit."""
    if n <= 0:
        raise ValueError("noise_floor needs at least one print")
    if n in _FLOOR_CACHE:
        return _FLOOR_CACHE[n]
    if n > EXACT_MAX_N:
        v = math.sqrt(2.0 / (math.pi * n))
    else:
        # INTEGER 2 ** n, never 2.0 ** n: the float form overflows above n = 1023
        # and silently broke the exact branch for 1024 <= n <= 4000.
        v = sum(math.comb(n, y) * abs(2 * y - n) for y in range(n + 1)) / (n * 2 ** n)
    _FLOOR_CACHE[n] = v
    return v


def floor_quantile(n, q):
    """Quantile q of that same null distribution, for reporting a floor as a
    DISTRIBUTION rather than only its mean."""
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    acc = 0.0
    vals = sorted(((abs(2 * y - n) / n, math.comb(n, y) / 2.0 ** n)
                   for y in range(n + 1)), key=lambda t: t[0])
    for v, p in vals:
        acc += p
        if acc >= q:
            return v
    return vals[-1][0]


def imbalance(a, b):
    """|a-b|/(a+b), or None when there is no flow. None is never a zero."""
    t = a + b
    return abs(a - b) / t if t > 0 else None


def aggregate_imbalance(per_market):
    """Aggregate flow imbalance across markets, the ONLY correct way.

    `per_market` is an iterable of (side_a_volume, side_b_volume), ONE PAIR PER
    MARKET. Returns sum|a_m - b_m| / sum(a_m + b_m).

    THIS EXISTS BECAUSE THE WRONG FORM IS ONE CHARACTER AWAY AND LOOKS RIGHT.
    Pooling first --

        I = |sum(a) - sum(b)| / sum(a + b)          # WRONG

    -- lets a yes-heavy market cancel a no-heavy one, and the answer collapses
    toward zero no matter how imbalanced every individual market is. It is the same
    defect as summing SIGNED residual across windows, which `l5_ladder_heldout.
    geometry` documents; that one was caught, and then this one was written anyway.

    Measured cost of the confusion, on real data:
        Kalshi KXCRYPTOLEAD15M   pooled 0.0032 vs correct 0.4144   (130x)
        Kalshi KXBTC15M/ETH15M   pooled 0.0011 vs correct 0.1054   (96x)
    Both pooled figures were reported, and both reversed a venue conclusion.

    A market is imbalanced on its OWN book. Imbalance does not net across markets
    because you cannot pair a YES in one market against a NO in another."""
    num = 0.0
    den = 0.0
    for a, b in per_market:
        num += abs(a - b)
        den += a + b
    return (num / den) if den > 0 else None


def rm_floor(I):
    """residual:matched floor implied by imbalance I. R/M = 2I/(1-I), exact."""
    if I is None:
        return None
    if not 0.0 <= I < 1.0:
        raise ValueError("imbalance must be in [0, 1): got %r" % (I,))
    return 2.0 * I / (1.0 - I)


def excess(observed_I, n):
    """The only cross-market-comparable form of an imbalance reading.

    Negative means the observation is MORE balanced than a fair coin typically
    looks at that n, which is ordinary sampling variation and NOT evidence of an
    unusually balanced market."""
    if observed_I is None:
        return None
    return observed_I - noise_floor(n)


def compare_raw(I_a, n_a, I_b, n_b):
    """Refuses an invalid comparison instead of returning a misleading number."""
    if n_a != n_b:
        raise ValueError(
            "raw imbalance is not comparable across unequal print counts "
            "(n_a=%d, n_b=%d): use excess() or a matched-sample comparison" % (n_a, n_b))
    return I_a - I_b


def matched_sample(side_labels, n, trials=4000, seed=20260920):
    """Resample a rich series down to n prints and return the sorted distribution
    of measured imbalance. `side_labels` is a sequence of 0/1 side indicators."""
    rng = random.Random(seed)
    out = []
    for _ in range(trials):
        y = sum(rng.choice(side_labels) for _ in range(n))
        out.append(abs(2 * y - n) / n)
    out.sort()
    return out


def cluster_bootstrap_ci(rows, stat, cluster_key="slug", trials=2000,
                         alpha=0.05, seed=20260920):
    """Percentile CI resampling CLUSTERS, not rows.

    Observations from one market share its book, its flow and its crowd, so they
    are not independent draws. Bootstrapping rows would understate the interval;
    resampling whole markets with replacement respects the dependence. `stat`
    takes a list of rows and returns a float, or None when the resample cannot
    support it (e.g. one arm came out empty)."""
    rng = random.Random(seed)
    groups = {}
    for r in rows:
        groups.setdefault(r[cluster_key], []).append(r)
    keys = list(groups)
    if len(keys) < 2:
        return None
    vals = []
    for _ in range(trials):
        draw = []
        for _ in range(len(keys)):
            draw.extend(groups[rng.choice(keys)])
        v = stat(draw)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            vals.append(v)
    if len(vals) < trials // 10:
        return None
    vals.sort()
    lo = vals[int((alpha / 2.0) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1.0 - alpha / 2.0) * len(vals)))]
    return {"lo": lo, "hi": hi, "n_clusters": len(keys), "n_resamples": len(vals)}
