"""The bar the imbalance-ratio augmentation must clear, FROZEN BEFORE the
backtest was run.

Written and committed before any augmented-vs-frozen number existed, so the
threshold cannot be moved to fit the answer. This file authorises nothing:
clearing the bar makes the augmentation eligible for a SHADOW-ONLY live
observation, never for a production model change.

WHY A BAR AT ALL. The augmented representation strictly refines the frozen
one -- every augmented state is a subset of a frozen state -- so it CANNOT
fit the training data worse. In-sample improvement is therefore guaranteed
and worthless. The only honest question is out-of-sample, and the only
honest split is temporal, because a random split leaks the same window's
later fills into training.

THE SPLIT, fixed here: canonical L5 sequence, systematic regime onward
(timestamp >= 1774569600), windows ordered by first timestamp, FIRST 70% of
windows train, LAST 30% test. No tuning on the test half.

THE PREDICTION RULE, fixed here: a state uses its own train distribution
when its train support is at least MIN_SUPPORT; otherwise it BACKS OFF --
augmented backs off to its parent frozen state, frozen backs off to the
global relation prior. Both representations get the same rule and the same
back-off ladder, so the comparison is of representations, not of smoothing.

THE METRICS, fixed here:
  primary   test log-loss over relations (open/extend/complete/sell)
  secondary relation mix L1 on the test half
            per-state relation L1, support-weighted
            log-loss restricted to HIGH-IMBALANCE rows (ratio >= 0.75)
            post-fill rows only (a residual already exists)
  guard     back-off rate, and the share of test rows whose augmented state
            was unseen in train

PASS CONDITIONS -- all four, or it does not pass:
  1. test log-loss improves by at least MIN_LOGLOSS_GAIN in relative terms
  2. the high-imbalance subset improves by at least MIN_HIGH_IMB_GAIN
  3. the augmented back-off-to-global rate does not exceed the frozen
     representation's by more than MAX_EXTRA_BACKOFF
  4. improvement is not concentrated in one bucket: at least MIN_IMPROVED_
     STRATA of the reported strata must improve

A representation that only wins in-sample, or only by back-off luck, FAILS.
"""
from __future__ import annotations

from decimal import Decimal as D

SEQUENCE = "analysis/l5/l5_canonical_sequence.csv.gz"
SINCE_EPOCH = 1774569600            # systematic regime, same as the frozen model
TRAIN_FRACTION = D("0.70")          # by window, chronological
MIN_SUPPORT = 30                    # train rows needed to use a state's own dist
RATIO_EDGES = ((D("0.25"), "r<25"), (D("0.50"), "r25-50"),
               (D("0.75"), "r50-75"), (D("0.95"), "r75-95"),
               (D("1.01"), "r95-100"))
HIGH_IMBALANCE_FROM = D("0.75")

MIN_LOGLOSS_GAIN = D("0.01")        # 1% relative, primary
MIN_HIGH_IMB_GAIN = D("0.01")       # 1% relative on ratio >= 0.75
MAX_EXTRA_BACKOFF = D("0.05")       # 5 percentage points
MIN_IMPROVED_STRATA = 3

BAR_FROZEN_UTC = "2026-09-08T22:35:00+00:00"
AUTHORISES_SHIPPING = ()
AUTHORISES_MODEL_CHANGE = ()
ON_PASS = ("eligible for shadow-only live observation beside the frozen "
           "model; production continues to compute and act from the frozen "
           "key unchanged")
ON_FAIL = ("report the negative result and stop; do not retune the buckets "
           "and re-run, which would be fitting the test half")
