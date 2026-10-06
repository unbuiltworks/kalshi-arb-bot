# Preregistration — OPENING15 study

**Written and committed BEFORE any new result is computed.** Nothing below may
be changed afterwards. The completed `PREREGISTRATION_NEXTACTION.md` protocol
and its results are **not modified** by this study; this is a separate,
narrower test on the one phase that survived there.

## 0. Status of the prior result

The BTC15m `OPENING 0–60 s` finding — **n = 136 transitions, lift 1.158,
0.530 bits** — is **discovery evidence, not validation.** It is the reason
this study exists and it is **not** counted as confirmation of anything here.

## 1. Subdivisions — the only two, fixed now

| phase | seconds after open |
|---|---|
| `OPEN_A` | **0 – 30** |
| `OPEN_B` | **30 – 60** |

**These are the only subdivisions that will be computed or reported.** No
other cut, merge, shift or alternative boundary will be examined, and the
60 s ceiling is inherited unchanged from the completed protocol.

## 2. Power — stated before the result, not after

The 0–60 s phase carries ~136 transitions across 12 windows. Splitting it
roughly halves that per subdivision. **This study is underpowered by
construction.** A floor is therefore fixed now:

* **n ≥ 50** decision points in a subdivision for any lift to be reported as
  a finding;
* below 50 the result is reported as **UNDERPOWERED** and cannot support any
  classification above `DESCRIPTIVE`, however large the lift.

## 3. Targets

Per subdivision, and for 0–60 combined (for comparability with discovery):

1. **next-side predictability** — the headline;
2. next price (exact tick, and tick error);
3. next action type `EXPOSE` / `MAINTAIN` / `WITHDRAW`;
4. timing to the next action.

Exact quantity remains **not a target** — observed partial fills do not
identify exposed size.

## 4. Features — the complete list, fixed now

All strictly causal: read at or before the decision instant, never after.

* **book/touch state** both outcomes: best bid, best ask, spread, depth at
  touch, total bid depth, bid-level count;
* **side persistence/alternation**: the previous clip's outcome;
* **price level**: the previous clip's price on each side, and the current
  touch on each side;
* **inferred ≤80-share clip state**: whether the current clip is ≈80,
  cumulative consumed at that price, and **remaining-to-80**;
* **partial consumption**: fills so far in the current clip and the fraction
  of 80 consumed;
* **inventory imbalance** (signed up − down);
* **matched inventory**;
* **residual**;
* **previous L5 action** (its action type);
* **elapsed time since the previous action**;
* **depth** and **spread** (as above);
* **transaction-level $1.00 pairs as a separate action class**: the count
  observed so far in the window, and whether the previous action was a pair.

**No feature will be added after a result is observed.**

## 5. Candidate rules — complete, fixed now

One rule chosen per target per training fold, by training-fold accuracy.

**Side (6):** `S-MAJ` · `S-PERSIST` · `S-ALT` · `S-LIGHT` (lower inventory) ·
`S-TOUCHFELL` (side whose touch fell more over 5 s) · `S-CLIPROOM` (the side
whose current clip has more remaining-to-80).

**Price (4):** `P-TOUCH` · `P-PREV` · `P-TOUCH-K` (k = training-fold median
offset) · `P-COMPLEMENT`.

**Action (3):** `A-EXPOSE` · `A-PERSIST` · `A-FROZEN`.

**Timing (2):** `T-MED` · `T-COND`.

The rule set is kept deliberately small because n is small: more candidates
would mean more selection noise under leave-one-window-out.

## 6. Protocol

Leave-one-window-out over the **12** BTC15m windows, run **separately within
each subdivision**. Every training constant is estimated on the 11 training
windows and applied unchanged to the held-out window. Abstention is permitted
and **coverage is reported, with accuracy over covered and over all
decisions.**

## 7. Window-by-window replay — mandatory

A per-window opening replay for **all 12 windows**, showing every genuine
archived book observation in 0–60 s, every L5 action (clips and $1.00 pairs),
and that window's own accuracy and contribution. **No aggregate statistic will
be reported without it**, so inconsistent behaviour across windows cannot hide
inside a mean.

## 8. Concentration verdict — criteria fixed now

The 0–60 lift is classified as exactly one of:

1. **concentrated in 0–30** — `OPEN_A` lift ≥ 1.10 with n ≥ 50 and `OPEN_B` < 1.10;
2. **concentrated in 30–60** — the reverse;
3. **consistent across both** — both ≥ 1.10 with n ≥ 50 each;
4. **driven by only a few windows** — **jackknife test, fixed now:** remove the
   two windows contributing the most correct predictions and recompute. If the
   0–60 lift falls below 1.10, the result is driven by those windows,
   regardless of 1–3.

Test 4 is evaluated **first** and overrides.

## 9. Economics — computed after, never used to choose

For the **correctly predicted** actions only, and using historical execution
evidence already in the archive: the price L5 actually obtained, the marginal
pair cost those fills formed, the model-implied fee
(`0.07·p·(1−p)`, from the operating model, **not** a credited fee), and the
settled outcome.

**Economics may not select, alter, weight or reject any rule.** They are
computed once, after the rules are fixed and the predictions are made, and are
reported separately.

## 10. Final classification — bar fixed now

| verdict | requirement |
|---|---|
| **REJECT** | no subdivision reaches n ≥ 50 with LOWO next-side lift ≥ 1.10 |
| **DESCRIPTIVE** | transition-level lift ≥ 1.10 but LOWO next-side prediction lift < 1.10, or the surviving subdivision is underpowered |
| **PREDICTIVE** | LOWO next-side lift ≥ 1.10, n ≥ 50, **and** surviving the §8.4 jackknife |
| **PREDICTIVE + ECONOMICALLY PROMISING** | the above **and** the correctly predicted actions show positive economics on historical execution evidence after the model-implied fee |

## 11. Scope

No S3. No change to S1 (`441c375e…b6fe`). No live orders. No change to the
completed `NEXTACTION` protocol, its code, or its results.
