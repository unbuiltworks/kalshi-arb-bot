"""Adverse-selection estimation with uncertainty and market-level clustering.

THE QUESTION. Our replay estimates that, in windows ending with residual, the
residual sits on the LOSING side 81% of the time. L5's profitable Mar-Apr era
measured 57%. Both are counterfactual for us. This module turns live
`L5_FILL_OUTCOME` rows into an estimate with an interval, and tests it against
both reference rates.

WHY CLUSTERING MATTERS. Fills inside one window share a book, a quote and a
price path, so they are not independent observations. Treating 60 fills as
n=60 would understate the interval. Two estimators are reported:

  WINDOW-LEVEL (the headline). One observation per window -- did the residual
  land on the loser -- so independence holds across windows by construction.
  This is also the only estimator comparable to the 81% and 57% figures.

  FILL-LEVEL, CLUSTER-ROBUST. Uses every uncomplemented fill but computes a
  sandwich SE clustered on the window, plus the design effect, so the cost of
  the correlation is visible rather than hidden.

No scipy: the normal CDF comes from `math.erf`.
"""
import math
from typing import Dict, List, Optional, Sequence, Tuple

REPLAY_ESTIMATE = 0.81     # ours, counterfactual
L5_EARLY_RATE = 0.57       # L5's measured profitable era


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def wilson(k: int, n: int, z: float = 1.959963985) -> Tuple[float, float]:
    """Wilson score interval. Preferred over normal-approximation here
    because n will be small and p far from 0.5."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def cluster_ratio(clusters: Sequence[Tuple[int, int]]
                  ) -> Dict[str, Optional[float]]:
    """Ratio estimator p = sum(k)/sum(n) with a cluster-robust sandwich SE.

    `clusters` is one (successes, trials) pair PER WINDOW. The SE is the
    standard linearised ratio form, which reduces to the binomial SE when
    every cluster holds one trial -- so the two estimators below agree in the
    window-level case rather than being two unrelated calculations.
    """
    cl = [(int(k), int(n)) for k, n in clusters if int(n) > 0]
    m = len(cl)
    tot_n = sum(n for _, n in cl)
    tot_k = sum(k for k, _ in cl)
    if m == 0 or tot_n == 0:
        return {"p": None, "se": None, "m_clusters": m, "n": tot_n,
                "k": tot_k, "design_effect": None}
    p = tot_k / tot_n
    if m < 2:
        return {"p": p, "se": None, "m_clusters": m, "n": tot_n, "k": tot_k,
                "design_effect": None}
    ss = sum((k - p * n) ** 2 for k, n in cl)
    se = math.sqrt(ss * m / (m - 1.0)) / tot_n
    naive = math.sqrt(p * (1 - p) / tot_n) if 0 < p < 1 else 0.0
    return {"p": p, "se": se, "m_clusters": m, "n": tot_n, "k": tot_k,
            "design_effect": ((se / naive) ** 2 if naive > 0 else None)}


def z_test_against(p_hat: float, se: Optional[float], p0: float
                   ) -> Dict[str, Optional[float]]:
    """Two-sided z test of p_hat against a reference rate.

    NOT named `test_against`: pytest collects any module-level name starting
    with `test_` from an importing test file and then fails it as a test with
    a missing `p_hat` fixture.
    """
    if se is None or se <= 0:
        return {"p0": p0, "z": None, "p_value": None}
    z = (p_hat - p0) / se
    return {"p0": p0, "z": z, "p_value": 2.0 * (1.0 - _phi(abs(z)))}


def analyse(window_rows: Sequence[Dict]) -> Dict:
    """`window_rows` are L5_FILL_OUTCOME dicts, one per settled window."""
    # ---- headline: one observation per window, the comparable metric
    decided = [r for r in window_rows
               if r.get("residual_side_lost") is not None]
    k_w = sum(1 for r in decided if r["residual_side_lost"])
    n_w = len(decided)
    lo_w, hi_w = wilson(k_w, n_w)

    # ---- fill-level, clustered on the window
    cl = [(int(r.get("uncomplemented_lost") or 0),
           int(r.get("uncomplemented_fills") or 0)) for r in window_rows]
    cr = cluster_ratio(cl)

    out = {
        "window_level": {
            "windows_with_residual": n_w,
            "residual_lost": k_w,
            "p_hat": (k_w / n_w) if n_w else None,
            "wilson_95": [lo_w, hi_w],
            "vs_replay_81": (z_test_against(k_w / n_w,
                                          math.sqrt((k_w / n_w) *
                                                    (1 - k_w / n_w) / n_w)
                                          if 0 < k_w < n_w else None,
                                          REPLAY_ESTIMATE)
                             if n_w else None),
            "vs_l5_early_57": (z_test_against(k_w / n_w,
                                            math.sqrt((k_w / n_w) *
                                                      (1 - k_w / n_w) / n_w)
                                            if 0 < k_w < n_w else None,
                                            L5_EARLY_RATE)
                               if n_w else None),
            "labels": ("ONE observation per window. The only estimator "
                       "comparable to the 81% replay figure and L5's 57%."),
        },
        "fill_level_clustered": dict(
            cr,
            vs_replay_81=(z_test_against(cr["p"], cr["se"], REPLAY_ESTIMATE)
                          if cr["p"] is not None else None),
            vs_l5_early_57=(z_test_against(cr["p"], cr["se"], L5_EARLY_RATE)
                            if cr["p"] is not None else None),
            labels=("Every uncomplemented fill, with a sandwich SE clustered "
                    "on the window. design_effect > 1 is the price of the "
                    "within-window correlation. NOT comparable to 81%/57%."),
        ),
        "excluded_unresolved_windows": len(window_rows) - n_w,
        "labels": ("Measured from venue-confirmed fills joined to venue "
                   "settlement. A replay fill is never counted here."),
    }
    return out


def power_table(rates=(0.57, 0.81)) -> List[Dict]:
    """How many windows separate the two hypotheses? Reported before the run
    so the sample size is justified rather than asserted."""
    out = []
    for n in (20, 30, 40, 50, 60, 80):
        se57 = math.sqrt(rates[0] * (1 - rates[0]) / n)
        se81 = math.sqrt(rates[1] * (1 - rates[1]) / n)
        sep = abs(rates[1] - rates[0]) / math.sqrt(se57 ** 2 + se81 ** 2)
        out.append({"windows_with_residual": n,
                    "half_width_95_at_57pct": 1.959963985 * se57,
                    "half_width_95_at_81pct": 1.959963985 * se81,
                    "sigma_separating_57_from_81": sep})
    return out
