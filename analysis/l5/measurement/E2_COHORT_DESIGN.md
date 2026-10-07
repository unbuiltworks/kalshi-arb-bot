# COHORT E2 — CREDITED MAKER ECONOMICS: prospective cohort design

> # ⛔ NOT AUTHORIZED. NOT ARMED. NO ORDER MAY BE PLACED ON THE BASIS OF THIS DOCUMENT.
>
> # ⛔ **VOID UNLESS COHORT E1 RETURNS `PASS`.**
>
> **E2 DOES NOT EXIST AS AN AUTHORIZABLE ARTIFACT unless cohort E1 returns
> `PASS`** as that verdict is defined in
> `analysis/l5/measurement/E1_COHORT_DESIGN.md` §3. An `INCONCLUSIVE`
> truncation is **not** a PASS. An `ABORT` on a cumulative loss bound is **not**
> a PASS — it is a failure to measure. A `FAIL` is **not** a PASS. And **a PASS
> is still not an authorization**: the owner authorizes, and only after E1 is
> frozen and they approve it.
>
> **The gate is enforced in code, not only in this prose.** Every E2 entry
> point in `analysis/l5/measurement/cohort_design.py` —
> `e2_quantity`, `e2_gate_table`, `e2_decision_rules`,
> `e2_worst_case_table` — requires an explicit `e1_verdict="PASS"` argument and
> raises `E2NotAuthorized` otherwise. That refusal is asserted by test against
> every other verdict string, including `"pass"`, `"PASS "` and `""`.
>
> **This is a DESIGN.** No order was placed. No order is authorized. Nothing is
> armed. No owner value is set. No credential, signing path, Railway service,
> `live_exec*` module, A6, S3, the XR2W executor, or the running
> `l5-book-shadow` collector was touched or invoked. Everything under
> `analysis/l5/stage6/` was **read only and never written**; S6P
> (`32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8`) was
> **not modified and not tuned**.
>
> `LABEL: DESIGN ONLY — NOT HISTORICAL REPLAY, NOT SHADOW, NOT SUBMITTED, NOT
> FILLED, NOT SETTLED, NOT REALIZED.`

**Why E2 is separate from E1 and not folded into it.** E1's recommended
quantity (0.13 shares) and every size below the price-dependent rebate floor
produce a maker rebate of **exactly $0.00, at every price, by cent-floor
arithmetic**. Not "measured poorly" — there is no signal to extrapolate, so no
number of cheap observations aggregates into a rebate estimate. Measuring
credited maker economics therefore requires a **structurally larger** order,
which costs **12× to 300× more per placement**. CLAUDE.md requires that mature
incentive-era behaviour stays locked until equivalent economics are
source-confirmed for our account, and that rebates are *additive economics,
never permission to make an otherwise bad trade look profitable*. So E2 is
gated on E1, not merged with it.

Calculator and gate: `analysis/l5/measurement/cohort_design.py`.
Tests: `analysis/l5/measurement/test_cohort_design.py`, **127 passing**.

---

## 1. (a) THE MINIMUM REBATE-BEARING QUANTITY — computed dynamically, never assumed

### 1.1 The formula, and why the floor is STRICT

From `src/pmus_fee_schedule.py`, fingerprint
`ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`:

```
rebate = ROUND_HALF_EVEN( 0.0125 × C × p × (1 − p) , $0.01 )
```

**`ROUND_HALF_EVEN` sends exactly $0.005 to $0.00** — 0 is the even digit — so
a cent requires

```
0.0125 × C × p(1−p)  >  0.005          STRICTLY
i.e.      C  >  0.4 / (p(1−p))
```

The implementation is **closed form, not a search**: the smallest multiple of
the venue's 0.01 quantity grid **strictly greater** than
`0.4 / (p(1−p))`. Strictness is load-bearing and is not a detail. **The
threshold lands exactly on the grid at some prices** — `p = 0.50` gives exactly
**1.60** and `p = 0.20` gives exactly **2.50** — and at those counts the raw
rebate is **exactly $0.005**, which rounds to **$0.00**:

| exact-boundary case | raw rebate | rounded | true floor |
|---|---:|---:|---:|
| `p = 0.50`, `C = 1.60` | **$0.005000000** exactly | **$0.00** | **1.61** |
| `p = 0.20`, `C = 2.50` | **$0.005000000** exactly | **$0.00** | **2.51** |

**Getting that case wrong is the single easiest way to place an order that earns
exactly $0.00 while believing it is rebate-bearing.** Both cases are asserted
by test, as is the exhaustive per-tick property over the whole first-leg band
`p ≤ 0.60`: at every tick the floor earns a cent and **one grid step below it
earns nothing.**

### 1.2 The floor is PRICE-DEPENDENT. It is not 1.62.

`C_min(p) = 0.4 / (p(1−p))` rounded strictly up to the grid. Since `p(1−p)` is
maximal at 0.50 and falls toward either edge, **the floor scales as
`1/(p(1−p))` and blows up at the cheap end of the band.** Full table over the
first-leg range, computed, with the one-step-below check shown:

| `p` | `p(1−p)` | **min rebate-bearing contracts** | raw at floor | raw one step below | rounded below |
|---:|---:|---:|---:|---:|---:|
| 0.01 | 0.0099 | **40.41** | $0.0050007 | $0.0049995 | **$0.00** |
| 0.05 | 0.0475 | **8.43** | $0.0050053 | $0.0049994 | **$0.00** |
| 0.10 | 0.0900 | **4.45** | $0.0050063 | $0.0049950 | **$0.00** |
| 0.20 | 0.1600 | **2.51** | $0.0050200 | **$0.0050000** | **$0.00** ← exact boundary |
| 0.30 | 0.2100 | **1.91** | $0.0050138 | $0.0049875 | **$0.00** |
| 0.40 | 0.2400 | **1.67** | $0.0050100 | $0.0049800 | **$0.00** |
| 0.45 | 0.2475 | **1.62** | $0.0050119 | $0.0049809 | **$0.00** |
| **0.50** | 0.2500 | **1.61** *(global minimum)* | $0.0050313 | **$0.0050000** | **$0.00** ← exact boundary |
| 0.55 | 0.2475 | **1.62** | $0.0050119 | $0.0049809 | **$0.00** |
| 0.60 | 0.2400 | **1.67** | $0.0050100 | $0.0049800 | **$0.00** |

This **reproduces `bounds.py`'s stated 1.62 at p = 0.45 exactly** (asserted by
test) — and shows it is the floor at **p = 0.45 and p = 0.55 only**. At 1.62
contracts the rebate is non-zero for `p ∈ [0.45, 0.55]`: **11 ticks of the
99-tick domain.** The minimum is a plateau symmetric about 0.50 (`p ∈
[0.47, 0.53]` all give 1.61).

**Any single hard-coded figure is wrong almost everywhere on the band.** The
code takes the live price and returns the true minimum; it never assumes 1.62,
and `e2_quantity()` returns `assumed_1_62: False` explicitly.

### 1.3 (a, second half) Rebate-bearing ACROSS the band, not at one price

The quantity needed to be rebate-bearing at **every** price on the grid in a
band is the **max over that band**, which sits at whichever edge is furthest
from 0.50:

| band | **contracts needed** | worst case / placement |
|---|---:|---:|
| `p ∈ [0.45, 0.55]` | **1.62** | $0.9720 |
| `p ∈ [0.30, 0.60]` | **1.91** | $1.1460 |
| `p ∈ [0.20, 0.60]` | **2.51** | $1.5060 |
| `p ∈ [0.10, 0.60]` | **4.45** | $2.6700 |
| **`p ∈ [0.01, 0.60]` — the FULL placeable first-leg range** | **40.41** | **$24.2460** |

> **A CORRECTION TO THE PRIOR PROTOCOL, recorded rather than silently fixed.**
> `SEQUENTIAL_MEASUREMENT_PROTOCOL.md` §3 states that *"a protocol that wants
> rebate-bearing observations across the whole placeable range `[0.01, 0.60]`
> needs **1.91** shares."* **That is wrong.** 1.91 is the floor at `p = 0.30`;
> the full range reaches down to `p = 0.01`, where the floor is **40.41**
> contracts. `maker_rebate_usd(0.01, 1.91) = $0.00`, asserted by test. The
> prior figure holds only for a band that stops at `p = 0.30`. The prior
> document is **not edited** — its evidence is preserved — and the correction
> is carried here with its arithmetic.

**REPORTED ANSWER TO (a):** *"the minimum rebate-bearing quantity"* **is not a
number, it is a function of price**, and the quantity needed **across** a band
depends entirely on where the band stops. **The practical consequence is that
rebate-bearing observation across the full first-leg range is not feasible**:
40.41 contracts carries a worst case of **$24.25 per placement** and
**$104,500.26** for the all-gates cohort at `p_fill = 0.10`. **A feasible E2
must restrict its price band, and which band is an owner choice.** This document
prices six and recommends none of them as a band; see §4 and §5.

---

## 2. (b) WHAT E2 MEASURES: CREDITED maker economics, credited only

**Per CLAUDE.md a rebate is not income until the venue has credited it and it is
source-accounted.** E2 measures **credit**, never accrual, never an estimate.

* **Source of truth:** `analysis/l5/stage6/settlement.py::credited_rebates_usd`, **unmodified**. On PMUS a credited maker rebate appears as a **NEGATIVE `commissionNotionalCollected` on our own execution**, per fill. A **positive** value is a *charge*, not a rebate, and is reported on a separate line. **An absent field is ABSENT — never read as zero credited** (`absent_field_read_as_zero_credited: False`), and the count of executions missing the field is reported.
* **Separate accounts, always.** Maker rebates, taker rebates and liquidity rewards are **three separate lines**, each separate from trading P&L. **Nothing is ever netted against a cost.** A rebate is not a negative fee.
* **Taker rebate tiers may not be assumed at any size.** `taker_rebate_available_to_this_account: False` in the frozen schedule. The 10/25/50% tiers above $250k/$1M/$10M prior-month notional are **not** available to this account and **E2 assumes none of them.**
* **Taker cost, for completeness.** `taker_theta = 0.0695` effective **2026-09-17T04:00Z** — **not** the stale flat 6% from `src/pmus_incentive_audit.py`, which is superseded. The taker coefficient has **no cent-floor exemption and no size threshold**; it is a cost charged on any crossing leg at any size. Fills in the **2026-09-17 → 2026-10-01** window are **DATE-AMBIGUOUS** per the schedule's recorded source conflict, and must be rated at the **higher** coefficient and **FLAGGED**, never quietly rated.
* **Everything stays `ESTIMATED` until the credit appears.** Any rebate figure E2 produces before the venue credit is labelled `ESTIMATED`, kept in a separate account, and is **never** called realized.

---

## 3. (c) END-TO-END REALIZED PAIR ECONOMICS — seven separate lines

```
1. matched_pair_pnl
2. directional_residual_pnl
3. spread_capture
4. maker_rebates_CREDITED_ONLY
5. liquidity_rewards_CREDITED_ONLY
6. fees
7. settlement_cashflow
```

**THE SEVEN LINES ARE NEVER SUMMED INTO ONE NUMBER WHILE ANY LINE IS
UNMEASURED.** A line with no observations is reported **`UNMEASURED`, not
zero**. This is enforced in the gate table's `summation_rule` and asserted by
test.

Each line carries its own interval. Lines 4 and 5 are **credited only** by
construction of their names. Line 7 goes through the frozen lifecycle
`SUBMITTED → RESTING → FILLED → SETTLED → REALIZED`, and
`settlement.py::realized_pnl` returns `NOT_REALIZED` **with a refusal** rather
than an estimate when settlement cash is unconfirmed. **Nothing is called
realized before settlement and cash confirmation** — not theoretical fills, not
stale BBO opportunities, not modelled pair profit, not shadow inventory, not
mark-to-market gains, not estimated rebates or rewards, not unconfirmed fills,
and not balance changes without sufficient source identity.

---

## 4. (d) E2's OWN STOPPING RULES AND WORST-CASE DOLLARS

### 4.1 The gates

| | estimand | method | unit | n |
|---|---|---|---|---:|
| **R1** | `P(the venue CREDITS a maker rebate \| our maker fill at or above the floor)` | Beta(1,1) posterior width 0.10 | qualifying maker fill | **385** |
| **R2** | mean **credited** maker rebate per share, and its agreement with the published formula | normal CI on the per-share mean, ±$0.0005 | qualifying maker fill | **597** |
| **R3** | the **seven lines**, each with its own interval | per-line normal CI; matched/residual on E1's two-point mixture machinery | settled market | **261** |
| **R4** | taker cost actually charged on any crossing leg at θ = 0.0695 | direct accounting against the frozen schedule | crossing fill | **0** (accounting, not estimation) |

### 4.2 PASS / FAIL / CONTINUE

**PASS** — *credited maker economics are measured, and the seven lines are each
measured.* **Evidence for a scaling decision, NOT a scaling authorization.* All
of: R1 reached 385 qualifying fills with its 95% **lower** bound above 0.90; R2's
credited amounts reproduce `round_half_even(0.0125·C·p(1−p), $0.01)`; **all
seven lines measured at 261 settled markets, none `UNMEASURED`**; R4 charged no
taker cost outside the frozen schedule.
**Error rates:** 95% intervals throughout; conservative union bound on any
single component misleading `4 × 0.05 = 0.20`.

**FAIL** — *stop; credited maker economics do not exist for us.* Any of:

| rule | numeric boundary | error rate |
|---|---|---|
| R1: no credit appears | **35 consecutive qualifying fills with no credit** puts the 95% posterior upper bound below 0.10 — **the rebate is not being credited to this account, and every rebate figure stays `ESTIMATED` forever** | 2.5% one-sided |
| R2: credited amounts disagree with the published formula | any systematic disagreement — **the frozen fee schedule no longer describes the venue and must be re-captured and re-fingerprinted** | 2.5% one-sided |
| R3: any of the seven lines is unattributable to a venue-sourced record | **n = 1** | none — an observation, not a test |

**CONTINUE** — **HARD TRUNCATION: 4,310 placements**, the same priced row as
E1's, so E2 inherits a ceiling rather than inventing a second one. **On
truncation: `INCONCLUSIVE`.** Rebates stay `ESTIMATED`, kept in a separate
account from trading P&L, and never netted against a cost.

**ABORT is not a verdict.** A cumulative-loss abort is a **failure to measure**,
reported as such with partial posteriors, never as FAIL and never as a result.
**The cumulative bound is an OWNER VALUE and is deliberately not set here.**

### 4.3 Worst-case dollars

Worst case = `placements × qty × $0.60` — every placement filling in full at the
frozen first-leg ceiling, every complement never filling, residual settling to
zero. **A BOUND, not a forecast.**

| qty (band it covers) | per placement | **R1 gate (385)** | **ALL, p=0.10** | **ALL, p=0.25** | **ALL, p=0.50** | **ALL, p=0.87** |
|---|---:|---:|---:|---:|---:|---:|
| **1.61** (`p = 0.50` only) | $0.9660 | **$371.91** | $4,163.46 | $1,665.38 | $832.69 | $479.14 |
| **1.62** (`p ∈ [0.45,0.55]`) | $0.9720 | **$374.22** | $4,189.32 | $1,675.73 | $837.86 | $482.11 |
| **1.91** (`p ∈ [0.30,0.60]`) | $1.1460 | **$441.21** | $4,939.26 | $1,975.70 | $987.85 | $568.42 |
| **2.51** (`p ∈ [0.20,0.60]`) | $1.5060 | **$579.81** | $6,490.86 | $2,596.34 | $1,298.17 | $746.98 |
| **4.45** (`p ∈ [0.10,0.60]`) | $2.6700 | **$1,027.95** | $11,507.70 | $4,603.08 | $2,301.54 | $1,324.32 |
| **40.41** (`p ∈ [0.01,0.60]`, full range) | $24.2460 | **$9,334.71** | **$104,500.26** | $41,800.10 | $20,900.05 | $12,026.02 |

**A tighter figure, recorded.** A placement *constrained* to be rebate-bearing
cannot price above 0.55 at 1.62 contracts, giving `1.62 × 0.55 = $0.8910` rather
than $0.9720. The **ceiling** figures above are the ones carried, because they
are larger.

**The honest headline:** E2 costs **12× to 311× more per placement than E1's
recommended 0.13 shares**, and the all-gates worst case at the full-band size
exceeds **$100,000**. **That ratio — not any expectancy — is why E2 is gated on
an E1 PASS**, and why E1 should be read as the cohort that prices E2.

---

## 5. WHAT E2 CANNOT ESTABLISH

1. **E2 cannot exist at all unless E1 PASSes.** Structural, enforced in code.
2. **It cannot establish profitability.** Measuring seven lines is not a profit claim, and **PASS is evidence for a scaling decision, not a scaling authorization.**
3. **It cannot establish rebate economics across the full first-leg price range at a feasible cost.** §1.3: that requires 40.41 contracts and a six-figure worst case. **Whatever band E2 runs on, its rebate findings are conditional on that band and do not transfer outside it.**
4. **It cannot assume a rebate it has not been credited.** If R1 FAILs, **no rebate figure ever becomes income**, and every rebate number in the repository stays `ESTIMATED` permanently.
5. **It cannot assume taker rebate tiers.** `taker_rebate_available_to_this_account: False`. Not at any size, not at any volume, unless source-confirmed for our account.
6. **It cannot resolve the 2026-09-17 → 2026-10-01 date ambiguity.** Fills in that window are rated at the higher taker coefficient and **FLAGGED**. E2 does not resolve the source conflict; it refuses to hide it.
7. **It cannot separate own-size market effect from rebate economics.** E2's sizes are 12× to 311× E1's, so they may change the displayed size other participants react to. **A difference between E1's and E2's measured mechanics is confounded between size-driven market impact and sample period, and E2 cannot attribute it.**
8. **It cannot carry E1's mechanics estimates forward unchanged.** `P(FULL fill)` is size-dependent even though `P(any fill)` is not: a sweep must consume `Q_ahead + our_size`, and at 1.61–40.41 contracts `our_size` is **not** negligible against realistic `Q_ahead`. **E1's fill probability does not transfer to E2's size as a measurement** — only as an extrapolation under a model (the `P(fill | Q_ahead)` curve), which must be labelled as such every time it is used.
9. **It cannot establish L5 behavioural fidelity**, and does not claim to. CLAUDE.md's validation standard is not met by either cohort.
10. **It cannot establish capacity, scaling or concurrency.** One market, one outstanding order.
11. **It cannot unlock mature incentive-era L5 behaviour.** Per CLAUDE.md, near-$1 pair tolerance and automatic late taker behaviour stay **LOCKED** unless equivalent economics are source-confirmed for our account. A credited rebate is **not** that confirmation and does not unlock them.
12. **It cannot run under the bounds as currently frozen**, for the same reason E1 cannot: a sequential cohort is many probes, and `MAX_FIRST_LEGS = 1` / `MAX_DAILY_REALIZED_LOSS_USD = $0.60` / "no automatic repeat" are written for one. Worse, **E2's per-session worst case at every size above 1.00 share EXCEEDS the frozen `MAX_POSSIBLE_LOSS_USD` of $0.60 outright** — $0.9660 at 1.61 contracts, $24.2460 at 40.41. **E2 therefore requires the owner to widen a frozen loss bound, which is the most consequential precondition in either design and is deliberately not made here.**

---

## 6. Preconditions the owner must supply before E2 may be armed

1. **a `PASS` verdict from a frozen, completed cohort E1**, approved by the owner;
2. **authorization, in the owner's own words**;
3. a **price band** — §1.3 prices six; a band is a choice, not a derivation;
4. a **quantity**, which follows from the band but is still the owner's to set;
5. a **cumulative loss bound**, which does not exist;
6. **an explicit widening of `MAX_POSSIBLE_LOSS_USD`**, since every rebate-bearing size exceeds the frozen $0.60 per-session bound (§5.12) — and `bounds.py::assert_bounds_coherent()` additionally requires that `REBATE_IS_ASSUMED_ZERO` be **re-justified** once the first-leg quantity reaches the rebate cent floor, which it does by construction here;
7. re-binding of Stage-6's three module sha256s.

Until all seven exist, **this remains a design, it is VOID without an E1 PASS,
and nothing may be armed.**

---

## 7. Provenance

| artifact | path |
|---|---|
| this design | `analysis/l5/measurement/E2_COHORT_DESIGN.md` |
| **the gate: E2 is void without this cohort's PASS** | `analysis/l5/measurement/E1_COHORT_DESIGN.md` |
| calculator and the enforced gate (`E2NotAuthorized`) | `analysis/l5/measurement/cohort_design.py` |
| tests, 127 passing | `analysis/l5/measurement/test_cohort_design.py` |
| stopping-gate machinery reused | `analysis/l5/measurement/SEQUENTIAL_MEASUREMENT_PROTOCOL.md`, `sample_size.py` |
| fee schedule `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`, taker θ 0.0695 eff. 2026-09-17 | `src/pmus_fee_schedule.py` |
| credited-rebate reader, unmodified | `analysis/l5/stage6/settlement.py` |
| frozen bounds, read-only, unmodified | `analysis/l5/stage6/bounds.py` |
| frozen S6P, read-only, **not tuned** | `analysis/l5/stage6/spec.py` |
| behavioural source of truth | `L5_OPERATING_MODEL.md`, GitHub issue #167 |

## 8. Anti-drift and no-fitting declaration

* **No parameter was chosen to improve any outcome.** Every quantity is computed from the published rebate formula and real cent rounding at a given price; the gates reuse the prior protocol's precision grid; the truncation is inherited from E1's priced row; the FAIL boundaries are arithmetic consequences of a 95% posterior.
* **Nothing frozen was modified.** `analysis/l5/stage6/` read only. S6P **not tuned**. The XR2W executor, its spec and fingerprint, A6 (FILLED / settlement unresolved / **NOT REALIZED**), S3 and the running `l5-book-shadow` collector untouched. No evidence deleted or overwritten. The prior protocol's §3 error is **corrected here with arithmetic, not edited out of that document.**
* **E2 is not a rebate-farming workstream.** Per CLAUDE.md, rebates are *additive economics, never permission to make an otherwise bad trade look profitable*, and E2's FAIL rules are written so that a rebate credit cannot rescue a sequence whose mechanics E1 already showed to be worse than the arithmetic rebate ceiling (`−$0.006231/share`, E1 §3.1). **E2 measures whether a known economic component exists for our account. It does not redefine the strategy as rebate farming, market making or arbitrage.**
