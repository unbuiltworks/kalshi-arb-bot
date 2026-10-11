# PREREGISTRATION — adequately powered cohort

Frozen before the powered cohort exists. **Nothing here is concluded from the
n=15/18 preview, and no parameter is taken from it.** Collection continues
unchanged. Sealed OOS unread. No live orders.

---

## 1. THE PRIMARY EXECUTABLE VARIABLE IS SIDE-SYMMETRIC (implemented)

`has_ask` is **retired as a primary variable**. It silently asked "can I buy
UP?", which is not the question. The predicted side is determined **first**,
then priced:

| predicted side | executable price | executable size |
|---|---|---|
| **UP** | best UP **ask** | UP ask size |
| **DOWN** | **1 − best UP bid** | UP **bid** size |

Emitted per observation (`_predicted_side_fields` in `ie_tte_ladder.py`):
`predicted_side` · `predicted_side_available` · `predicted_side_price` ·
`predicted_side_size` · `predicted_side_settled_correct` ·
`predicted_side_edge`.

**The question this makes answerable:**

> When BTC state strongly favours one outcome, can we acquire **that** outcome
> at a price **below** its empirical conditional settlement probability?

### A circularity guard that must not be dropped

`predicted_side_edge = P(correct | state) − predicted_side_price`.

**`P(correct | state)` must be estimated on DEV and applied to
VALIDATION/OOS.** Estimating it from the same observations being scored makes
the edge mechanically non-negative and is worthless. The field is therefore
emitted as `None` by the descriptive extractor and is filled **only** at the
frozen-model stage. Any edge figure computed in-sample is void by
construction.

### Placeholder predictor, labelled as such

For the n=100 **descriptive** read the predictor is `sign(coinbase − strike)`
— mechanical, not fitted, and deliberately crude. It is **not** the model. The
real signal is specified and frozen on DEV/VALIDATION before any prospective
evaluation.

---

## 2. THREE ARCHITECTURES, COMPARED ON IDENTICAL SIGNALS AND WINDOWS

All three consume the **same** signal stream and the **same** windows, so the
comparison isolates architecture rather than signal.

### A — ONE ENTRY / HOLD
Choose one side from BTC state · enter once at an executable price · size to
≈ **+$5** settlement profit if correct · **no extend, no flip** · hold to
settlement.

### B — ONE ENTRY / CONDITIONAL FLIP
Same initial entry. Do nothing while the thesis holds. **Only if the BTC-state
thesis genuinely reverses**, evaluate `HOLD / EXIT original / BUY opposite /
WAIT`.

**A flip is admissible only if the new action is independently positive EV
given the current executable price and the total existing inventory.**
**Never flip merely because the first trade is losing** — asserted in tests,
not left to judgement.

### C — EXTEND / FLIP
Same initial signal, plus additional **same-side** fills while **each
incremental purchase is independently +EV on its own terms**. On reversal,
the same conditional-flip economics as B.

**Rebates are additive accounting only. An EXTEND whose justification requires
the rebate is inadmissible** — i.e. every extension must be +EV *before*
rebates.

### Reported separately for each of A/B/C — never netted into one figure

initial-entry P&L · extension P&L · flip P&L · matched P&L · residual P&L ·
fees · credited/defensible rebates · **total NET**

### The ordered comparison, decided in this order

1. **Does B beat A?**
2. If yes, **does C beat B?**

**If not, the simpler architecture wins.** Continuous inventory is **not**
assumed desirable because L5 uses repeated inventory actions; on this venue
that assumption has already failed twice (gate A: median window has zero
favorite flips; maker/maker matched inventory in 1.4–6.7% of windows). It must
earn its place here on its own numbers.

---

## 3. THE LIQUIDITY / INFORMATION FRONTIER

Per window, the earliest TTE at which **all four** hold simultaneously:

1. the predicted side is **sufficiently informative**,
2. the predicted side is **executable**,
3. the price still offers **positive estimated edge** (DEV-estimated
   probability, per §1),
4. **displayed depth suffices for ≈$5 winning profit** —
   `size ≥ 5 / (1 − price)`.

**The hypothesis being tested:** early = plenty of liquidity, weak information;
late = strong information, no liquidity. **Is there a middle region where both
coexist?**

Reported continuously, with preregistered checkpoints at
**T−120 / T−90 / T−60 / T−45 / T−30 / T−20 / T−15 / T−10 / T−5**.

**No timestamp is selected from the preview.** The frontier is reported as a
curve across all checkpoints; if a best region exists it must be identified on
DEV and confirmed on VALIDATION before any prospective use.

*(T−120 and T−90 are added to the existing ladder, which currently starts at
T−60. The collector already captures them — subscribe lead is 420 s — so this
is an extraction change, not a collection change.)*

---

## 4. WHAT THE PREVIEW DOES AND DOES NOT LICENCE

The n=18 side-symmetric read showed `predicted_side_available` at **0–28%**,
and **0% at T−30, T−20, T−5, T−2 and T−1**; where available at T−60 the price
was p50 **0.89** against a mechanical-predictor correct rate of ~94%.

**None of this is carried forward as a finding, a parameter, or a prior.** It
is recorded only to show the measurement works. In particular:

- the ~94% correct rate comes from a **mechanical** predictor and is largely
  tautological near expiry;
- `0.89 vs 94%` rests on **5 available observations**;
- the availability collapse may reverse at larger n or differ by regime.

---

## 5. STAGE GATES

| stage | what happens |
|---|---|
| **n = 100 usable** | **DESCRIPTIVE ONLY** — availability, price, size, spread, depth and correct-rate distributions across all checkpoints. No strategy run, no model fitted, no edge computed, sealed OOS untouched. |
| **powered cohort** | specify and **FREEZE** the signal and the A/B/C rules on **DEV**, confirm once on **VALIDATION**, record the SHA. |
| **prospective** | only then evaluate the frozen rule on genuinely prospective windows, **read once**. |

**No live orders at any stage of this document.**

## 6. STOPPING RULES

- If `predicted_side_available` stays near zero at the informative TTEs, the
  hypothesis fails on **availability**, and that is reported as the result
  rather than worked around by relaxing executability.
- If B does not beat A, or C does not beat B, the simpler architecture is
  adopted and the richer one is reported as **not justified**.
- If the DEV-estimated conditional probabilities do not replicate on
  VALIDATION, no prospective evaluation occurs.
- Theoretical rebates are never counted as realized cash.
- OOS, once read, wins any contradiction with DEV/VALIDATION.
