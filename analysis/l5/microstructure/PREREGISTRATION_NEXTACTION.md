# Preregistration — causal next-action test

**Written and committed BEFORE the predictor is implemented and before any
result is seen.** Everything below is fixed at this commit: the feature set,
the complete list of candidate rules, the baselines, the evaluation protocol,
the metrics, the BTC15m phase boundaries, and the rule-labelling bar. Nothing
may be added afterwards to improve a number.

## 0. Frozen input

The BTC5m state machine is **frozen** as committed (`CLIPSTATE_5m.json`,
`clipstate.py`, lift 1.68×). **No feature is added to improve that result.**
The 80-share lattice, the clip definition, the par-pair definition and the
behavioural findings are inputs from `FROZEN.md`, not re-estimated.

## 1. The question

> Can the BTC5m latent-clip representation **predict** L5's next passive
> liquidity action from contemporaneously observable state, rather than merely
> describe fills after they occur?

## 2. Decision point

One decision point at the **end of each passive clip** in a window (the moment
its last fill is observed). At that instant we predict the **next passive
clip** in that window. The final clip of a window produces a decision point
whose true label is `WITHDRAW_WINDOW` (no further passive clip).

**Atomic $1.00 par pairs are a separate action class.** They are excluded from
the prediction target and never counted as a passive action. They may appear
in the *features* (as observed history) but a pair is never a thing to be
predicted, and no claim is made that they are profitable or necessary.

## 3. Prediction targets

| target | values |
|---|---|
| **outcome** | `up` / `down` |
| **price** | a price in ticks |
| **action type** | `EXPOSE` (new level) / `MAINTAIN` (same price as that side's previous clip) / `WITHDRAW` (no further clip on that side this window) |
| **timing** | seconds until the next clip's first fill |
| **clip ceiling** | whether the next clip totals ≈80 shares (secondary) |

**Exact quantity is deliberately NOT a target.** Observed partial fills do not
identify exposed size (`LIMITS.md` §2), so predicting it would be predicting
the counterparty's behaviour, not L5's.

## 4. Feature set — strictly contemporaneous

Only information observable at or before the decision instant:

* both outcomes' best bid, best ask, spread, depth at touch, total bid depth,
  bid-level count, from the latest archived observation **at or before** the
  decision second;
* touch movement over the preceding 5 / 10 / 30 s on both outcomes;
* seconds since open, TTE, TTE fraction remaining;
* L5 inventory reconstructed from his **already-observed** fills: up, down,
  matched, residual, signed imbalance;
* the previous clip on each side: outcome, price, total, duration, number of
  fills, whether it was ≈80;
* count of par pairs observed so far in the window.

No future book state, no future fill, no settled outcome.

## 5. Candidate rules — the complete list, fixed now

One rule is chosen **per target, per training fold**, by training-fold
accuracy. No rule outside this list may be introduced later.

**Outcome:** `S-MAJ` training-fold majority side · `S-PERSIST` same side as
the previous clip · `S-ALT` opposite side · `S-LIGHT` the side with lower
current inventory · `S-TOUCHFELL` the side whose touch last fell.

**Price:** `P-TOUCH` that side's current best bid · `P-PREV` that side's
previous clip price · `P-TOUCH-K` best bid minus k ticks, k = training-fold
median observed offset · `P-COMPLEMENT` 1 − the other side's best bid.

**Action type:** `A-EXPOSE` always · `A-PERSIST` repeat the previous action
type · `A-FROZEN` WITHDRAW when TTE < T_w and price < P_w (T_w, P_w =
training-fold medians at terminal clips), MAINTAIN when the predicted price
equals that side's previous clip price, else EXPOSE.

**Timing:** `T-MED` training-fold median inter-clip gap · `T-COND` median gap
conditional on the previous action type.

**Clip ceiling:** `C-BASE` training-fold base rate · `C-PREV` predict ≈80 iff
that side's previous clip was ≈80.

## 6. Baselines

Every metric is reported against: **majority class**, **persistence** (repeat
the previous observed value), and for price **the current touch**. Lift is
reported over the *best* baseline, not a convenient one.

## 7. Protocol — leave-one-window-out

23 folds over the 23 BTC5m windows. In each fold the rule selection and every
training-fold constant (k, T_w, P_w, medians, base rates) are estimated on the
other 22 windows and applied unchanged to the held-out window. **The held-out
window is never used for selection or estimation.** Reported metrics pool the
held-out predictions.

## 8. Abstention

The predictor abstains when the book is unresolvable at the decision second on
the predicted side, or when that side has no best bid. **Coverage is reported
and accuracy is reported both over covered decisions and over all decisions**,
so abstention cannot inflate accuracy.

## 9. BTC15m lifecycle phases — fixed now, never searched

Declared before computing anything, on round interpretable boundaries:

| phase | seconds after open |
|---|---|
| `OPENING` | 0 – 60 |
| `EARLY` | 60 – 300 |
| `MID` | 300 – 600 |
| `LATE` | 600 – 840 |
| `EXPIRY` | 840 – 900 |

**Boundaries will not be moved, merged or re-cut after seeing results**, and
no alternative slicing will be reported. Within each phase the same first-order
transition lift used on BTC5m is computed, with its n.

## 10. Rule labelling

* **cross-horizon** — selected in ≥50% of the 23 BTC5m LOWO folds **and**
  achieving lift ≥1.10 on BTC15m in at least one preregistered phase with
  n ≥ 50 decision points in that phase.
* **5m-only** — selected on BTC5m but failing that BTC15m bar.
* **15m-only** — the reverse.

**A 5m-only relationship is not promoted into the PMUS BTC15m
implementation**, and this document is the record of that commitment.

## 11. Executability screen (PMUS)

PMUS places **maker-or-cancel** orders at tick offsets with a rung count. A
rule is marked **not executable under PMUS mechanics** if it requires taker
aggression, sub-second timing, or knowledge of unfilled orders. The atomic
$1.00 pair class is taker-on-both-legs and is therefore expected to fail this
screen; that expectation is recorded here rather than discovered later.

## 12. Stopping rule

Results are reported as they come out. **No feature, rule, phase boundary or
threshold is added or changed after any result is seen.** If the test fails, it
is reported as a failure.
