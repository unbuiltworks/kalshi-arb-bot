# Does the full state vector beat "repeat last side"? **No.**

61,493 chronological BTC15m fills. State built from fills 1..i, label from fill
i+1. Split **chronologically at a window boundary** — 358 train windows /
154 OOS windows, 34,530 / 26,963 rows. No window spans both.

## Baselines and models, OOS

| next side = Up | acc | bal acc | AUC | log loss | Brier |
|---|---|---|---|---|---|
| majority side | 0.5037 | 0.500 | 0.500 | 13.71 | 0.496 |
| **repeat last side** | **0.7039** | **0.7039** | **0.7039** | 8.18 | 0.296 |
| buy cheaper outcome | 0.5772 | 0.5767 | 0.5767 | 11.68 | 0.423 |
| repair imbalance | 0.4780 | 0.4775 | 0.4775 | 14.42 | 0.522 |
| logit, all 24 features | 0.7038 | 0.7038 | **0.7551** | **0.590** | **0.201** |
| tree, depth 4 | 0.7029 | 0.7029 | 0.7441 | 0.595 | 0.203 |

**Buying the cheaper outcome (0.577) and repairing imbalance (0.478) are both
far worse than persistence.** Repairing imbalance is worse than a coin flip:
whatever L5 is doing, it is not rebalancing.

## Ablation — the incremental information beyond last-side is ~zero

| blocks (nested) | acc | Δacc | AUC |
|---|---|---|---|
| last_side | 0.7039 | — | 0.7039 |
| + market_price | 0.7039 | 0.0000 | 0.7396 |
| + inventory | 0.7040 | +0.0001 | 0.7550 |
| + vwap_economics | 0.7041 | +0.0001 | 0.7551 |
| + tte_progress | 0.7042 | +0.0001 | 0.7551 |
| + recent_movement | 0.7038 | −0.0004 | 0.7551 |

**Total incremental accuracy over last-side-only: +0.0003.** Every block
contributes ≤ 0.0001. In every slice — run length, imbalance, TTE, price —
model accuracy equals persistence accuracy to three decimals. The classifier
has learned to be "repeat last side".

What *does* improve is **ranking and calibration**: AUC 0.7039 → 0.7551, Brier
0.296 → 0.201, and the deciles track closely (0.17/0.17, 0.27/0.23, 0.38/0.38,
0.57/0.57, 0.73/0.77, 0.81/0.80). The model knows *how confident* to be; it
does not know *something different*.

## Regime transfer fails

Train on spring (Mar+Apr, 5,051 rows) → test on August (56,442):

| | logit | trivial baseline |
|---|---|---|
| next side = Up | **0.6645** | 0.7045 |
| CONTINUE | 0.7025 | 0.7045 |

**Out of regime the fitted model is 4 points WORSE than repeat-last-side.**
Whatever it learned in spring does not hold in August.

## CONTINUE vs SWITCH

| | acc | bal acc | AUC |
|---|---|---|---|
| always CONTINUE | 0.7039 | 0.500 | 0.500 |
| logit, all features | 0.7039 | **0.5037** | 0.6221 |
| tree, depth 4 | 0.7039 | 0.500 | 0.6120 |

Balanced accuracy **0.5037** — the model essentially never predicts SWITCH.
Ablation is flat at 0.7034–0.7039 throughout.

**The one strong regularity is run length, and it is self-reinforcing:**

| current run | P(continue) | n |
|---|---|---|
| 1 | **0.602** | 8,090 |
| 2–3 | 0.700 | 8,166 |
| 4–7 | 0.766 | 6,495 |
| 8+ | **0.812** | 4,212 |

Runs do **not** mean-revert. The longer he has been buying one side, the more
likely the next fill is that side again. But conditional on run length the
model's AUC *falls* (0.586 at run 1 → 0.561 at run 8+): the extra features say
almost nothing about *when a run ends*.

Persistence is also strongly price-dependent — 0.772 below \$0.30, 0.601 above
\$0.70. He is most persistent when accumulating cheap outcomes.

## Verdict against the freeze criterion

The criterion was that a model must **materially beat repeat-last-side
chronologically OOS**. It does not: **+0.0003 accuracy**, and it is *worse*
than the baseline out of regime.

**So Arm I is NOT built on side prediction, and this is reported as the result
rather than engineered around.** Side selection in this data is persistence
plus noise; the state vector adds calibration, not direction.

Two things survive for whatever comes next, neither of which is a side model:
the run-length continuation curve (0.60 → 0.81, monotone, n = 26,963), and the
finding that cheapness and rebalancing are both *negatively* useful as side
rules.

## Not done

Size modelling and the counterfactual trajectory test are not started. Nothing
here widens 0.96, changes a depth, adds a signal, or touches live money.
R/S/D remains frozen.
