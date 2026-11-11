# H1 CHOICE / H2 SKEW / H3 FLOW — A · B · C · D

**Verdict: `NOT_DETERMINED`. H2 and H3 are refuted; H1 survives only in a
degenerate form that carries no control rule.**

---

## Two corrections that changed the numbers

**A transaction is one decision, not N.** Every multi-leg BTC15m transaction
has *all* legs on the same side — 6,396 of them, 19.7 % of fills. Counting legs
separately inflated same-side persistence. **71.50 % → 68.24 %** once
aggregated. Randomising order within a second moves it by ±0.06 pp, so the
1-second timestamp ambiguity is immaterial.

**The "flow" family was smuggling the side.** `price` equals `price_up_last`
exactly when the last fill was Up, so a flow-only model scored 0.694 accuracy
without ever seeing the side. Replaced with `p_up`, the Up contract's
probability however he traded. Flow-only then falls to **0.597 / AUC 0.637** —
the earlier 0.730 was leakage.

**No BTC spot series exists in this repo**, so "recent BTC return" is not a
separate block: the contract price is a monotone transform of BTC's distance to
strike, so `pm_mid_movement` already carries that signal. Adding it twice under
another name would report the same information twice.

---

## A · Next side, chronological OOS (26,173 rows, 154 windows)

| baseline | acc | AUC | log loss |
|---|---|---|---|
| majority side | 0.5028 | 0.500 | 13.74 |
| **repeat last side** | **0.6950** | 0.695 | 8.43 |
| buy cheaper outcome | 0.5795 | 0.579 | 11.62 |
| repair imbalance | 0.4798 | 0.480 | 14.37 |
| logit, all blocks | 0.6945 | 0.7493 | 0.596 |

| ablation (nested) | acc | AUC | ΔAUC |
|---|---|---|---|
| last_side | 0.6950 | 0.6950 | — |
| + inventory | 0.6947 | 0.7428 | **+0.0478** |
| + probability | 0.6940 | 0.7496 | +0.0068 |
| + tte | 0.6940 | 0.7496 | 0.0000 |
| + inter_fill_time | 0.6932 | 0.7494 | −0.0002 |
| + pm_mid_movement | 0.6936 | 0.7493 | −0.0001 |
| + vwap_economics | 0.6940 | 0.7492 | −0.0001 |
| + price_interactions | 0.6945 | 0.7493 | +0.0001 |

**Accuracy never beats persistence.** TTE, inter-fill time, mid movement and
VWAP economics contribute *nothing* — the last five blocks sum to −0.0003 AUC.

| family | acc | AUC | log loss |
|---|---|---|---|
| **control only** (his own state) | 0.6946 | **0.7494** | 0.5962 |
| **flow only** (incoming market) | 0.5970 | **0.6374** | 0.6626 |
| clock only | 0.4972 | 0.5062 | 0.6933 |
| everything | 0.6945 | 0.7493 | 0.5962 |

**Control alone equals everything. Flow adds zero on top of control.**

Regime transfer (spring → August) still *fails*: 0.6729 vs the baseline's
0.6943.

## B · CONTINUE vs SWITCH

Balanced accuracy **0.5085** — the model essentially never predicts SWITCH.

**The entire "inventory" gain is run length, not inventory:**

| added to last_side | AUC |
|---|---|
| nothing | 0.4980 |
| **run length only** | **0.6110** |
| imbalance only | 0.5026 |
| size/age only | 0.5169 |
| whole inventory block | 0.6046 |

**Imbalance contributes +0.0046 AUC. It is noise.**

SWITCH rate across own-side imbalance — neither a threshold nor a gradient:

| own-side imbalance | ≤−.20 | −.20…−.10 | −.10…−.03 | ±.03 | .03…10 | .10…20 | ≥.20 |
|---|---|---|---|---|---|---|---|
| P(SWITCH) | .3005 | .2920 | .3019 | .3000 | .3055 | .3087 | .3232 |

Flat. **No hysteresis, no reservation-price skew.**

Run length is the one real structure, and it is self-reinforcing:
P(continue) = .582 → .700 → .763 → **.808** across runs 1, 2–3, 4–7, 8+.
But the model's AUC *within* each run bucket is 0.56–0.59: the state vector
says little about *when* a run ends.

## C · Passive dual-sided null — mandatory, and it fails

A continuously resting two-sided bid at (best bid − k ticks). No side decision,
no pair gate, no inventory logic. Flow picks the fills.

| at the touch | null | **L5, same 12 windows** |
|---|---|---|
| events/window | 106 | **238** |
| **same-side persistence** | **0.530** | **0.750** |
| final pair cost | 0.9859 | ~0.96 |

At −1 and −2 ticks: persistence 0.552 / 0.527, fills 71 / 54.

**The null's side sequence is a coin flip. L5's is not.** The null also gets
under half his fills and worse pair economics — *and* the fill rule is biased
in the null's favour, because book-only data cannot separate a level that
traded from one that was cancelled.

## D · No inventory skew in fill offsets

| own-side imbalance | ≤−.20 | −.20…−.05 | ±.05 | .05…20 | ≥.20 |
|---|---|---|---|---|---|
| median offset (ticks) | 2.0 | 2.0 | 2.0 | 3.0 | 3.0 |

**Correlation 0.01**, n = 2,154. Stated strictly: *eventual fill price relative
to a prior BBO*. No anonymous size is attributed to anyone; mint fills excluded.
Subject to selection — we see quotes that were hit, not quotes posted — so this
is evidence against a *large* skew, not proof of none.

---

## Decision

| | verdict |
|---|---|
| `PASSIVE_DUAL_SIDED` (H3) | **refuted** — null persistence 0.53 vs L5 0.75; flow-only AUC 0.637 on side, 0.514 on switch |
| `INVENTORY_SKEWED_DUAL_SIDED` (H2) | **refuted** — imbalance +0.0046 AUC, flat SWITCH rate, offset correlation 0.01 |
| `ACTIVE_SIDE_CONTROLLER` (H1) | **not established** — persistence far exceeds flow-generated chance, so *something* chooses; but nothing observable predicts it beyond "what he just did" |
| **`NOT_DETERMINED`** | **← this** |

**No candidate is frozen.** Freezing `ACTIVE_SIDE_CONTROLLER` would mean
freezing "repeat the last side", which needs none of the state vector and was
never the hypothesis. Freezing either dual-sided variant would mean deploying a
mechanism this evidence refutes.

What would actually resolve it: L5's **order placements and cancellations**,
not his fills. Every study here observes only executions, and execution is the
convolution of his quoting with someone else's taking. The persistence gap
(0.75 vs a passive null's 0.53) is the size of the thing we cannot see.

R/S/D untouched. No live money. Nothing here widens 0.96 or changes a depth.
