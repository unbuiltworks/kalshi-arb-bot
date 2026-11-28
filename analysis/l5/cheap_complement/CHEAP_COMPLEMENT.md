# P0 — Why does L5 reach cheap-complement states so often?

**Read-only. Sequential. No lookahead. The production model is not modified.**

Run: `python3 analysis/l5/cheap_complement/run.py`
Reconstruction: `src/l5_cheap_complement.py` · regressions: `tests/test_l5_cheap_complement.py`

Corpus: the canonical L5 fill sequence, **21,699 side switches over 2,201 windows**,
systematic-regime epoch onward. Train/holdout split at the window start
(`HOLDOUT_EPOCH` 2026-08-31): 17,343 / 4,356. Base rate `pair <= 0.97` =
**45.55%**, reproducing the earlier P4 figure from an independent walk.

## The answer is an identity, not a strategy

At a side switch the canonical pair cost and the observed cross-side sum are

    pair      = avg_cost_held      + first_opposite_price
    last_pair = last_same_side_px  + first_opposite_price

Subtract:

    pair = last_pair - (last_same_side_px - avg_cost_held)
         = last_pair - gain_per_unit

**Max |residual| over all 21,699 switches: 2.22e-16.** This is arithmetic, not a
fitted relationship. `gain_per_unit` — the held side's unrealised gain against
its own running average cost — is known BEFORE the switch fill.

So `pair <= 0.97` **is** "the held side is up by at least `last_pair - 0.97`".
The cheap complement and being up on the position are the same number viewed
from opposite ends.

| | |
|---|---|
| freeze `last_pair` at its median, vary gain only | **40.41%** ≤0.97 |
| freeze gain at its median, vary `last_pair` only | 26.99% ≤0.97 |
| actual | **45.55%** |

## Q1 — Selection at entry, or movement after it? **Movement after it.**

Single-feature AUC for `pair <= 0.97`, all features strictly pre-action:

| feature | AUC |
|---|---|
| `gain_per_unit` (post-entry accumulation) | **0.9513** |
| `last_same_side_price` | 0.8915 |
| `move_since_entry` | 0.8860 |
| `held_side_drift_during_run` | 0.6138 |
| `switches_so_far` | 0.5274 |
| **`entry_price` (ENTRY-TIME ONLY)** | **0.4973** |
| `tte_s` | 0.4513 |
| `run_length` | 0.4292 |

Entry-time information is a coin flip. **There is no market- or side-selection
rule at entry to copy.**

## Q2 — Same pre-action state: what separates ≤0.97 from >1.00?

Within identical `(price_band | tte)` buckets, the separator is the same
variable every time — median `gain_per_unit`:

| state | n | ≤.97 | gain given ≤.97 | gain given >1.00 |
|---|---|---|---|---|
| p75-90c \| tte5-10m | 1202 | 94.0% | +0.1483 | −0.0188 |
| p55-75c \| tte5-10m | 1334 | 72.0% | +0.1048 | −0.0459 |
| p55-75c \| tte>10m | 1799 | 61.6% | +0.0682 | −0.0126 |
| p25-45c \| tte>10m | 1625 | 16.5% | +0.0045 | −0.0791 |
| p10-25c \| tte5-10m | 1256 | 3.6% | −0.0494 | −0.1470 |

## Q3 — How much is mechanically implied? **Essentially all of it.**

The identity is exact, and the gain term alone reproduces 40.41 of the 45.55
points. The residual is the cross-side sum (p50 0.9900, variance 0.00807 against
0.02830 for gain). **No prior decision rule is required to explain the rate.**

## Q4 — TTE regimes, normalised by market type

| asset / window min | n | ≤.97 | gain p50 |
|---|---|---|---|
| BTC / 15 | 18,892 | 45.1% | −0.0001 |
| BTC / 5 | 2,297 | 49.3% | +0.0000 |
| ETH / 5 | 479 | 45.5% | +0.0000 |

| tte bucket | n | ≤.97 | last_pair p50 |
|---|---|---|---|
| tte<30s | 181 | 50.8% | 0.9800 |
| tte30-120s | 2,997 | 52.0% | 0.9900 |
| tte2-5m | 6,021 | 46.8% | 0.9900 |
| tte5-10m | 6,604 | 46.7% | 0.9900 |
| tte>10m | 5,896 | 39.6% | 0.9900 |

The TTE effect is real but modest (39.6% → 52.0%) and far smaller than the gain
effect. **Every number above is L5's short-dated crypto tape. No transfer to
PMUS sports is claimed, and none is implied.**

## Q5 — Reconciling "drift ≈ 0, switch in ~9s" with a 45% cheap rate

Both earlier findings stand; they measure different quantities.

- held-side drift **during the last run**: p50 +0.0000, `|drift| < 1c` in **57.8%**
- gain against the **accumulated average cost**: p50 +0.0000, `gain >= 0.015` in **41.5%**
- switches on a FLAT run (`|drift| < 1c`, n = 12,545): `pair <= 0.97` in **47.5%**

Cheap complements are *more* common on flat runs than overall. **L5 does not wait
for a favourable move.** A position built while its side rose is already up
against its own average without any recent movement, so drift near zero and a
45% cheap rate are consistent rather than contradictory.

## Q6 — Highest-information pre-action features and OOS lift

| feature | AUC train | AUC holdout |
|---|---|---|
| `gain_per_unit` | 0.9510 | **0.9524** |
| `last_same_side_price` | 0.8906 | 0.8953 |
| `move_since_entry` | 0.8858 | 0.8874 |
| `avg_cost_held` | 0.7278 | 0.7421 |
| `held_side_drift_during_run` | 0.6145 | 0.6111 |
| `switches_so_far` | 0.5274 | 0.5207 |
| `tte_s` | 0.4523 | 0.4473 |

Train-median `gain_per_unit` threshold applied out of sample (holdout base
45.11%): above → **85.0%** ≤0.97 (n=2,012); below → **10.9%** (n=2,344). Lift
**1.88×**. Holdout AUC exceeds train AUC, so this is not overfit.

**The production model is not modified by any of this.**

## What this means for us — and what it does not

L5's 45.55% is not evidence for crossing and not a rule to copy. It says the
cheap complement is a **position-state** phenomenon: he is up on the held side
when he switches, roughly as often as a near-symmetric gain distribution allows
(gain p50 = 0.0000), and he samples that distribution ~10 times per window
across 2,201 windows.

Our engine took **239 COMPLETE decisions across 8 markets** in the comparable
period, at a **6.7% maker fill rate**, and in the one post-cutover market
observed so far the held side was *down*, putting every priced observation at
**1.14–1.26**.

The open question this promotes is **opportunity frequency and held-side
direction**, not taker execution. Nothing here is a strategy change, and none
is proposed.
