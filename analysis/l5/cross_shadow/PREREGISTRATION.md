# PRE-REGISTRATION — bounded complement-cross shadow

**Frozen 2026-09-13, BEFORE any analysis code for this study existed.** The
run that follows is scored against this document and nothing in it may be
changed after outcomes are seen. If something here turns out to be
unmeasurable, the answer is UNKNOWN and a stated limitation — never a
relaxed rule.

**Production is not changed by this study.** `f7a3e224`, maker-only, cap 6,
current band, 160 recorder seats, watch limit 160, existing financial
envelope. No live taker order is placed. OPEN, EXTEND, sizing, concurrency,
band and cap are untouched. **The $1.00 pair ceiling does not move.** What
is being tested is execution CERTAINTY inside the existing economic
envelope, not a wider envelope.

---

## 1. Hypothesis

At pair prices our existing economic gate already accepts, our maker-only
COMPLETE converts to a fill ~6.8% of the time. Crossing for the complement,
when a contemporaneous authenticated ask is available and every existing
gate still passes, converts more of those into matched inventory. The
question is whether that certainty survives the confirmed fee and adverse
selection.

## 2. Unit of observation

Every REAL production COMPLETE decision in the recorded corpus. No
synthetic decisions are created.

## 3. Qualification — all eight must hold

A shadow cross is PROPOSED only if:

1. a genuine held residual exists at that decision;
2. the frozen model selected COMPLETE;
3. the authenticated opposite-ask age is **≤ 5 seconds**;
4. **observed ask depth ≥ the proposed quantity**;
5. the resulting pair cost ≤ the arm's threshold;
6. the existing residual / spend / daily-loss gates pass on the ARM's own
   inventory;
7. the settlement-horizon gate passes;
8. no required evidence field is missing or malformed.

Failing 3, 4 or 8 is not a refusal — it is **UNKNOWN_EXECUTABILITY** and is
reported separately from refusals, because "we could not tell" and "we
would not have" are different facts.

## 4. Arms — reported in full, never selected after the fact

**Thresholds** (pair cost ceiling for the cross): `.97`, `.98`, `.99`,
`1.00`. Tested separately. Not optimised. `1.00` is the existing hard
ceiling and is the maximum; **no arm above 1.00 exists.**

**Timing policies**: `T0` cross immediately on the first qualifying
COMPLETE; `T10` allow the maker attempt 10 s first, then cross if still
qualifying; `T30`; `T60`. Tested separately.

**The entire 4 × 4 matrix is reported.** No arm is promoted, highlighted or
carried forward on the basis of being the best after the fact.

## 5. Strict executability (P2)

A shadow taker fill counts **only** when an authenticated contemporaneous
ask exists **and** observed depth ≥ requested quantity. **If depth is
unavailable the row is UNKNOWN_EXECUTABILITY and is NOT counted as a fill.**
No fill is ever assumed. Partial depth fills the available size only, and
the shortfall stays residual.

## 6. Fees (P1) — CONFIRMED before the run

Source: **docs.polymarket.us/fees, fetched 2026-09-13**, effective 12 AM ET
Wednesday July 1 2026, exchange-wide:

* **Taker fee** `Θ × C × p × (1 − p)` with **Θ = 0.06** (max $1.50 at
  p = 0.50).
* **Maker rebate** same structure with **Θ = −0.0125** (max −$0.31).
* Reduced taker tiers begin at **$250,000** prior-calendar-month taker
  volume (10% / 25% / 50% rebate at $250K / $1M / $10M). **We are orders of
  magnitude below the first tier, so the base 0.06 applies** and no tier is
  assumed.
* **"All fees and rebates are rounded to the nearest $0.01 using banker's
  rounding (round half to even)."**
* Category differentiation: **NOT STATED** on the page.

**The rounding is applied at our actual clip size and is not treated as a
rounding detail.** At C ≈ 1.7 and p ≈ 0.5 the formula gives $0.0258 and the
charge is $0.03 — the quantisation is larger than the price effect being
measured, and the study must show it.

Every arm reports **gross** pair economics and **net** economics separately,
each labelled `fee_status = CONFIRMED`. Any figure that cannot be computed
from the confirmed rule is reported UNKNOWN and excluded from net.

**Maker rebate forgone** by crossing is counted as a cost of the cross.

## 7. Economic decomposition (P3)

Per supported cross: maker price forgone, taker premium paid, confirmed fee
(banker's-rounded), maker rebate forgone, gross locked pair P&L, residual
dollars eliminated, the actual eventual residual settlement P&L that was
avoided, incremental gross and net vs production, capital required, and how
much earlier the residual was removed.

Per arm: opportunities, supported fills, UNKNOWN_EXECUTABILITY count,
markets, capital, taker premium, fees, matched-pair P&L, directional
residual loss avoided, incremental gross P&L, incremental NET P&L,
net per deployed dollar, capital-hours.

**Incentives are counted only if attributable and confirmed.** PMUS credits
rewards at account level with an empty `conditionId`, so reward attribution
stays UNKNOWN; the maker rebate IS per-fill attributable and is counted.

## 8. Adverse selection (P5) — the principal risk

For every supported cross, the complement price at **+10 s, +30 s, +60 s and
at settlement**, against the counterfactual of continuing to wait as a
maker. The question is whether paying the ask buys useful certainty or
systematically crosses immediately before an adverse move. A result showing
the complement got cheaper right after we crossed is a **negative** result
and is reported as one.

## 9. L5 fidelity (P6)

**No claim is made that L5 crosses. That is unobservable and will not be
asserted.** Comparison is trajectory only: shadow side-switch timing,
shadow switch pair economics, shadow matched-fraction trajectory, against
L5's empirical distributions (median switch interval 9 s, median
reconstructed switch pair 0.99, 47.2% above 1.00, median window matched
fraction 0.625). The question is whether bounded crossing makes our
INVENTORY TRAJECTORY more L5-like — not our maker/taker mix.

## 10. Promotion gate — all eight, or maker-only stays

1. PMUS fee economics source-confirmed. **(Met at freeze time; see §6.)**
2. Contemporaneous depth proves executability.
3. Multiple independent markets represented.
4. Incremental NET economics positive after confirmed fees.
5. Residual losses fall.
6. Tail risk does not materially worsen.
7. Inventory trajectory at least as L5-faithful.
8. A fresh realtime shadow confirms the historical result.

**Failing any one of these means maker-only stays.** This document does not
propose a live change and cannot be read as proposing one.

## 11. Pre-declared limitations

* The corpus is one day of Railway-retained decisions across a small number
  of markets; it cannot support arm selection and is not intended to.
* The opposite-ask series is sparse — it exists only where an order was
  resting — so reachability is understated and timing arms are measured
  against the observations that exist, not against a continuous book.
* Depth may be absent from the probe's recorded book. If it is, **condition
  2 of the promotion gate fails by definition** and the study's honest
  output is the UNKNOWN_EXECUTABILITY count, not a fill count.
* `T10/T30/T60` require knowing the book 10–60 s after the decision. Where
  no observation exists in that window the row is UNKNOWN, not a cross.

---

Frozen by: this file, committed before `analysis/l5/cross_shadow/run.py` and
`src/l5_cross_shadow.py` existed.
