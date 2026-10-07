# PMUS rebate-adjusted L5-clone BREAKEVEN SURFACE

**Protocol** `P0-ECONOMICS-PMUS-BREAKEVEN-SURFACE`
**Code** `analysis/l5/economics/breakeven_surface.py`
**Raw numeric output** `analysis/l5/economics/PMUS_BREAKEVEN_SURFACE_RAW.txt`
(2,214 lines) and `PMUS_BREAKEVEN_SURFACE.json`
**Assumptions** `analysis/l5/economics/ASSUMPTIONS.md`
**Tests** `tests/test_pmus_breakeven_surface.py` — **37 passing**
**Schedule** `src/pmus_fee_schedule.py`, fingerprint
`ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`
(`assert_frozen()` called before any arithmetic; `maker_rebate()` /
`taker_fee()` called, cent rounding **never** reimplemented)

> **HISTORICAL / SHADOW ARITHMETIC ONLY.** No network, no order path, no
> venue write, no credentials. Nothing here is submitted, filled, settled or
> **realized**. Our one credited PMUS fill (A6) is **FILLED, settlement
> UNRESOLVED, NOT REALIZED**. The `$0.51` / `97.59 bp` maker-rebate credits
> are **GEMINI, not PMUS**, and are attributed to PMUS nowhere in this work.

---

## 0. THE ANSWER

> ### No.
>
> PMUS's actual compensation does **not** create an executable
> positive-expectancy region for the L5-derived matched-pair engine at any
> price, size, pair cost or completion rate we can evidence.

The maker rebate is real, **uncapped**, exactly computable and larger than
the brief assumed. It is also **not the binding constraint**. The binding
constraint is a residual that loses **3.7×** what a matched pair locks, at an
observed loss rate of **1.000**.

| | |
|---|---|
| breakeven matched fraction, **no** rebate | **78.73%** |
| breakeven matched fraction, **full** both-legs-maker rebate at size | **~77.2%** |
| breakeven matched fraction at Stage-6's authorized **1.00 share** | **78.73%** — *unchanged* |
| **observed** matched fraction | **67.72%**, 95% CI **[65.62%, 69.81%]** |
| gap to close | **11.01 points** |
| gap the rebate closes | **1.56 points — 14.2%, about one eighth** |

The rebate closes one eighth of the gap at size, and **none of it** at the
size actually authorized. Across the **630-cell** both-legs-maker surface and
the **630-cell** one-leg-taker surface, the number of cells reachable at the
observed completion rate is **zero**. The number reachable anywhere inside
the observed 95% CI is **zero**.

**Positive-expectancy region: EMPTY under evidenced bounds.**

---

## 1. THE CONDITION, components never pre-summed

```
matched_pair_pnl + residual_pnl + maker_rebates
    + credited_liquidity_rewards − fees   >   0
```

Every component is carried as its own field from end to end; no function in
the module returns a net number without the five lines beside it
(`test_components_are_never_pre_summed`).

`credited_liquidity_rewards_usd` is **$0.00 at every state** — not by
oversight but by evidence: the PMUS program census returns zero for all 239
of our slugs. The line is carried so its absence is *visible* rather than
silently dropped (`test_liquidity_rewards_are_zero_everywhere…`).

**Credited PMUS compensation, kept as separate lines and never mixed into
trading P&L:** maker rebates `$9.14` + liquidity rewards `$7.04` =
`$16.18`. None of it attaches to a matched-pair sequence we can replay.

---

## 2. THE DENOMINATOR RECONCILIATION — the main result

This was flagged as the step most likely to manufacture a false positive. It
does, twice, and both traps are avoidable only by going to source.

### 2.1 Three denominators, and where the 2× actually lives

| quantity | natural denominator |
|---|---|
| maker rebate | **MAKER NOTIONAL** = `C·p` — **one leg** |
| pair economics | **PAIR NOTIONAL** ≈ `$1.00` of settlement per pair |
| L5's `net_bp` | **TURNOVER** — *established from source, not assumed* |

L5's denominator is settled by reading the code, not by inference:
`analysis/l5/regime_aug19/regime_shift.py` line 360 accumulates
`turnover += usdcSize` over **every fill of both legs**. So for a pair of `C`
contracts with legs `p` and `X − p`:

```
turnover = C·p + C·(X − p) = C·X  ≈  $1.00 · C
```

**Pair turnover ≈ pair cost.** A rebate expressed per pair over `C·X` is
therefore already in L5's bp-of-turnover units, with **no 2× correction**.

The 2× trap is real but points the other way, and this is the part that is
easy to get backwards: **a pair turns over 2× one leg's notional, but each
leg is only ~$0.50, so the pair total is still ~$1.00.** The factor applies
when converting *from* maker notional (one leg) *to* pair turnover. The raw
output carries both columns side by side, and the maker-notional column runs
~2× the pair-turnover column exactly as that conversion requires
(`test_maker_notional_column_is_about_twice_the_pair_turnover_column`).
**Quoting our rebate on maker notional and comparing it to L5's bp would
overstate our compensation ~2×.**

### 2.2 Is the −71.7 bp comparison legitimate? **NO** — on two independent grounds

**Ground 1 — it is a CHANGE, not a LEVEL.** `REGIME_SHIFT.md` states that
L5's trading net moved **+41.4 → −30.4 bp** of turnover. **−71.7 bp is that
move.** The level a clone must cover is **−30.4 bp**. Comparing a rebate
*rate* against the *delta* overstates the hurdle by **2.36×** — and would
have converted a mathematically positive region into an apparently impossible
one. (This repo's earlier `DENOMINATOR_RECONCILIATION.md` reached the same
conclusion independently; the two agree.)

**Ground 2 — even against −30.4 bp the comparison fails, on role mix.** L5's
R4 shows **fees of −62.1 bp of turnover**. L5 therefore **paid taker fees**
on a material share of that turnover. A maker rebate and a taker fee cannot
both be earned on the same fill, so the full both-legs-maker **62.5 bp cannot
be added to a −30.4 bp figure that was produced partly by taker legs** —
doing so double-counts the same notional. And an all-maker counterfactual
does not hold L5's completion rate fixed: **the taker completions are
precisely what produced it.**

> **Verdict: the comparison is NOT MADE here.** It could only be made on an
> all-maker sub-population with its own measured completion rate and its own
> turnover. **We do not hold that measurement.** The arithmetic
> `−30.4 + 62.5 = +32.1 bp` is available and tempting; it is **not
> legitimate**, and it is the single most likely source of a false positive
> in this whole exercise.

---

## 3. THE PAR-PAIR / REBATE-FINANCING HYPOTHESIS — tested properly

**Claim.** L5's median pair cost of `0.9668` with only `46.2%` completing
≤ 0.96 is explained by rebate financing.

**Arithmetic: CONFIRMED.** At `X = 1.00`, `p = 0.50`, both legs maker, the
pair earns `0.0125·[0.25 + 0.25] = $0.00625` against a **$0.00 lock** —
**62.5 bp of pair turnover, financed entirely by the rebate**
(`test_par_pair_is_rebate_financed_at_about_62_5_bp_with_zero_lock`). The
rebate **exceeds** the lock for every pair cost above ~`0.9938` and is a
majority of gross above ~`0.9875`.

**Causally: UNPROVEN**, for three reasons the raw output carries:

1. **L5's confirmed incentive yield is +163.4 bp of turnover — 2.6× the
   62.5 bp the maker-rebate formula can possibly produce.** So the maker
   rebate alone **does not account for L5's compensation**; a
   liquidity-reward program we have **no source-confirmed entitlement to**
   supplies the rest (`test_l5_confirmed_incentive_exceeds…`). This is the
   decisive objection: the hypothesis explains at most 38% of the thing it
   claims to explain.
2. **L5's R4 fees are −62.1 bp**, so L5 is not all-maker and cannot be
   earning the full both-legs-maker rate.
3. **The pair-cost distribution SPREAD rather than shifted** — median *fell*
   `0.9764 → 0.9609` while p90 *rose* `1.1705 → 1.2175`. A pure
   rebate-financing story does not predict a widening upper tail.

So the mechanism is **arithmetically available to L5 and to us**, and is a
genuine partial explanation — but it is not sufficient, and it does not
license copying near-$1.00 pair tolerance. Per the operating model, that
behaviour **stays locked**: it belongs to incentive-era L5 and the equivalent
economics are **not** source-confirmed for our account.

---

## 4. DELIVERABLES 1–3 — the rebate itself

Full grid for prices `0.10–0.90` × quantities
`0.01, 0.10, 1, 1.62, 2, 5, 10, 25, 50, 100` in the raw output.

### 4.1 There is no $0.31 cap — REFUTED

The captured venue page carries **`lot = 100`**: the published table is the
formula evaluated **per 100 contracts** and cent-rounded. `$0.31` appears for
every price from **0.43 to 0.57** because that is where the rounded formula
sits. **All 99 published rows reproduce exactly** from the two coefficients
(`deviations_from_formula == []`), and the word "cap" appears nowhere in the
capture.

**The rebate is UNCAPPED.** At `p = 0.50` the per-contract credit is
**$0.003125**, not $0.0031. At 10,000 contracts it pays **$31.25**, not
$31.00 (`test_no_cap_is_applied_anywhere_so_large_size_is_uncapped`).

The brief is **right** that the formula gives `0.003125` at p = 0.50 and
`0.003000` at p = 0.40/0.60. It is **wrong** that a cap binds, because there
is nothing to bind. This correction is *favourable* and is applied anyway.

### 4.2 Effective bp, and the lumpiness that is not an edge

The asymptotic rate on maker notional is `125·(1−p)` bp — 112.5 / 62.5 /
12.5 bp at p = 0.10 / 0.50 / 0.90 — confirmed exactly.

Cent rounding makes realised bp **lumpy, and at small size HIGHER than the
asymptotic rate**: at `p = 0.10, C = 5` the $0.01 cent on $0.50 of notional
reads **200.00 bp** against a 112.5 bp asymptote. **This is a rounding
artifact, not an edge.** It is a single cent granted on a fill whose own
unrounded entitlement is `$0.005625`; it cannot be harvested, because
capturing it requires the fill, and the fill carries the full residual risk
that the cent does not come close to paying for. Every such row is flagged
`ARTIFACT` in the raw output.

### 4.3 Minimum quantity for $0.01 — reconciles with the brief exactly

| p | 0.10 | 0.20 | 0.30 | 0.40 | **0.50** | 0.60 | 0.70 | 0.80 | 0.90 |
|---|---|---|---|---|---|---|---|---|---|
| **min contracts** | 4.45 | 2.51 | 1.91 | 1.67 | **1.61** | 1.67 | 1.91 | 2.51 | 4.45 |

**All nine agree with the brief to the tick**, and one tick less pays `$0.00`
in every case. The exact-half cases (p = 0.20, 0.50, 0.80) genuinely need the
extra tick, because `ROUND_HALF_EVEN` sends an exact `$0.005` to `$0.00`
(0 is even) — which is why p = 0.50 is **1.61 and not 1.60**.

### 4.4 Minimum economically-meaningful quantity per price

"Nonzero" and "meaningful" are different thresholds. A cent is *nonzero* at
1.61 shares, but the first quantity at which the realised rebate tracks the
formula rather than the rounding grid — within 10% of the asymptotic rate —
is materially larger:

| p | nonzero cent | **economically meaningful** | reads vs asymptote |
|---|---|---|---|
| 0.10 | 4.45 | **25** | 120.00 vs 112.50 bp |
| 0.20 | 2.51 | **5** | 100.00 vs 100.00 bp (exact) |
| 0.30 | 1.91 | **25** | 93.33 vs 87.50 bp |
| 0.40 | 1.67 | **10** | 75.00 vs 75.00 bp (exact) |
| **0.50** | **1.61** | **10** | **60.00 vs 62.50 bp** |
| 0.60 | 1.67 | **10** | 50.00 vs 50.00 bp (exact) |
| 0.70 | 1.91 | **25** | 40.00 vs 37.50 bp |
| 0.80 | 2.51 | **5** | 25.00 vs 25.00 bp (exact) |
| 0.90 | 4.45 | **25** | 13.33 vs 12.50 bp |

The clean prices are **0.20, 0.40, 0.60, 0.80**, where `p(1−p)` lands on the
cent grid and the realised rate is exact from 5–10 contracts. The tails
(0.10, 0.90) and the odd-tick prices (0.30, 0.70) need **25 contracts** before
the grid stops dominating.

Below these, size is being chosen to catch a rounding boundary rather than to
earn the rate — which is the thing the brief rightly said to stop and report
rather than tune.

---

## 5. DELIVERABLE 4 — the breakeven completion surface

`m*` = the matched-completion rate at which the five components sum to zero.
Full 630-cell surfaces for both role cases in the raw output.

**Minimum `m*` per price, at the most favourable pair cost (X = 0.94):**

| p | 0.10 | 0.20 | 0.30 | 0.40 | **0.50** | 0.60 | 0.70 | 0.80 | 0.90 |
|---|---|---|---|---|---|---|---|---|---|
| **both maker** | 0.8114 | 0.8105 | 0.7956 | 0.7956 | **0.7956** | 0.7956 | 0.8074 | 0.8114 | 0.8163 |
| **one leg taker** | 0.8222 | 0.8222 | 0.8222 | 0.8222 | **0.8222** | 0.8222 | 0.8222 | 0.8222 | 0.8211 |

* **Best cell on the entire both-maker surface: `m* = 0.7956`** at
  `p ∈ [0.30, 0.60]`, `C = 2`, `X = 0.94`.
* **Best cell on the one-leg-taker surface: `m* = 0.8211`.**
* Observed is **0.6772**, CI to **0.6981**. The best cell anywhere misses the
  CI upper bound by **9.75 points**.
* `m*` rises monotonically with pair cost; at `X = 1.00` it reaches **1.0000**
  — breakeven requires *every* pair to complete, which 107 one-leg-taker
  cells cannot reach at any completion rate at all.

### One taker leg ends the discussion

At `p = 0.50` a taker completion costs **$0.0174 per contract = 173.8 bp of
pair turnover**, against a both-legs-maker credit of **62.5 bp**. **A single
taker leg is worth ~2.8× the entire maker rebate, with the opposite sign**
(`test_one_taker_leg_outweighs_the_entire_maker_rebate`). No rebate at any
price or size on the venue grid offsets one taker fill.

---

## 6. DELIVERABLE 5 — residual tolerance

At `p = 0.50`, `C = 5`, both legs maker, holding completion at the observed
67.72%:

| pair cost | max tolerable residual **$/contract** | max tolerable residual **frequency** |
|---|---|---|
| 0.94 | **−$0.1467** | **19.92%** |
| 0.96 | **−$0.1047** | **14.93%** |
| 1.00 | **−$0.0208** | **2.84%** |

Against **observed**: residual loss **−$0.27742/contract**, residual
frequency **32.28%**, residual **loss rate 1.000**.

> The observed residual is **2.7× too expensive** at the $0.96 ceiling, and
> occurs **2.2× too often**. Both constraints are violated simultaneously,
> and by multiples rather than margins.

Residual duration p50 **22 s** / p90 **160 s** confirms these are not stale
positions that patience clears — they are fills that were adversely selected
at the moment of execution.

---

## 7. DELIVERABLE 6 — does any positive region survive at OBSERVED values?

**No.** Expected value per probe at the observed completion rate, `p = 0.50`,
both legs maker:

| C | rebate (matched) | `m*` | Δ vs 0.7873 | **EV @ 67.72%** | positive? |
|---|---|---|---|---|---|
| 0.01 | $0.00 | 0.7873 | 0.0000 | −$0.000388 | **no** |
| 0.10 | $0.00 | 0.7873 | 0.0000 | −$0.003879 | **no** |
| **1.00** | **$0.00** | **0.7873** | **0.0000** | **−$0.038788** | **no** |
| 1.62 | $0.02 | 0.7565 | −0.0308 | −$0.046065 | **no** |
| 2 | $0.02 | 0.7623 | −0.0250 | −$0.060805 | **no** |
| 5 | $0.04 | 0.7672 | −0.0201 | −$0.160397 | **no** |
| 10 | $0.06 | 0.7722 | −0.0151 | −$0.337567 | **no** |
| 25 | $0.16 | 0.7712 | −0.0161 | −$0.835531 | **no** |
| 50 | $0.32 | 0.7712 | −0.0161 | −$1.671061 | **no** |
| 100 | $0.62 | 0.7717 | −0.0156 | −$3.358894 | **no** |

**EV is negative at every size and grows more negative with size** — the
rebate scales linearly while the residual scales linearly and is 3.7× larger.
Increasing size makes the loss bigger, never the sign better. On L5's own
coins the observed matched fraction falls to **57.90%**, which is worse still.

Note the non-monotonicity at C = 1.62 (`m*` = 0.7565, the single best cell in
this table): that is the cent-rounding grid, not an edge — 1.62 shares
catches a full cent on an unrounded entitlement of $0.0051. Choosing C = 1.62
*because* it minimises `m*` would be exactly the parameter-tuning the brief
prohibited. It is reported and **not** adopted.

---

## 8. STAGE-6 REPLAY UNDER ACTUAL PMUS COMPENSATION

`analysis/l5/stage6/` read **read-only**; `bounds.py`, `machine.py`,
`pair_economics.py`, `settlement.py` inspected and **not modified**. S6P, the
XR2W executor and its fingerprint, and the running `l5-book-shadow` collector
are all untouched.

### 8.1 Components, separately

| component | value | basis |
|---|---|---|
| matched-pair economics | **+$0.07496**/contract | observed, n=16,549 |
| directional residual P&L | **−$0.27742**/contract | observed, n=7,148, loss rate **1.000** |
| maker rebates | **$0.00** at the authorized size | structurally zero — below cent floor |
| credited liquidity rewards | **$0.00** | PMUS census returns zero for all our slugs |
| fees | **$0.0000** charged across every audit (maker-only by owner rule); S6P's gate nonetheless **charges the taker coefficient on both legs** as a bound | |
| settlement | **A6 unresolved → nothing realized** | |

### 8.2 What changes when the rebate is credited where formula and account history support it

1. **S6P's conservatism is quantified, and it is large.** At `p₁ = 0.60` and
   a complement of `0.36`, the taker-on-both-legs charge is
   `$0.0167 + $0.0160 = ` **$0.0327** per contract, which pushes that pair to
   `$0.9927` and fails the gate outright. The highest complement that clears
   is **`0.32`** — so the `$0.96` ceiling becomes a **raw-price ceiling of
   `$0.92`**, four cents tighter. Priced as what the legs actually are — two
   post-only maker fills — the same pair would instead *receive* a rebate.
   The swing between the two pricings is the schedule's full maker-to-taker
   spread, and `headroom_report()` was right to make it visible.
2. **The conservatism is not what kills it.** Removing the taker pricing
   *entirely* and crediting the full both-legs-maker rebate at a size above
   the cent floor still leaves `m*` at **~77.2%** against an observed
   **67.72%**. S6P's conservatism costs real headroom but is **not the
   binding constraint**.

### 8.3 What does not change

1. **The verdict.** UNECONOMIC before and after.
2. **The rebate at the authorized size: $0.00, at every price.**
   `bounds.REBATE_CENT_FLOOR_SHARES = 1.62` while `FIRST_LEG_QTY = 1.00` —
   **the protocol sits below its own documented cent floor.** S6P's
   zero-rebate treatment is therefore not merely conservative here, it is
   **exactly right** (`test_stage6_is_read_only_and_its_qty_is_below_its_own_cent_floor`).
3. **The residual.** 3.7× the matched lock, loss rate 1.000. No rebate on the
   venue grid is of that order of magnitude.
4. **The evidence base.** **95.58%** of first legs are UNKNOWABLE under the
   strict standard. Rebate arithmetic does not touch the question the
   authorization actually turns on.
5. **The cumulative-loss gap.** A 727-probe serial cohort draws down
   **$13.22** against a per-session bound of `$0.60`. Unaffected by anything
   here.

---

## 9. CLASSIFICATION — exact boundaries

### OBSERVED-POSITIVE — **EMPTY SET**

**No region qualifies.** We hold **one** credited PMUS fill (A6), at `0.01`
shares, whose rebate is structurally `$0.00` and whose **settlement is
UNRESOLVED**. The `$16.18` of credited PMUS compensation is not attached to a
matched-pair sequence we can replay. The `$0.51` / `97.59 bp` credits are
**GEMINI, not PMUS**. **One fill cannot evidence a region**, and this class is
not inflated to pretend otherwise (`test_observed_positive_is_empty`).

### PLAUSIBLE-BUT-UNPROVEN

Mathematically positive, requiring values we have **not** demonstrated:

1. **Both legs maker, `C ≥` cent floor (1.61–4.45 by price), `X ≤ ~0.96`,
   matched completion `≥ 79.6%`.** Needs a completion rate **9.75 points
   above the observed CI upper bound of 69.81%**, demonstrated on all-maker
   fills — against a 95.58% UNKNOWABLE fill question.
2. **Both legs maker at `p ≈ 0.50`, `X ≈ 1.00`, rebate-financed with zero
   lock.** Needs 62.5 bp of pair turnover to exceed the residual drag, i.e. a
   residual loss rate far below the observed **1.000**. *Also gated shut by
   the operating model*: near-$1 pair tolerance is incentive-era L5
   behaviour and stays locked absent source-confirmed equivalent economics.

**Boundary:** the surface `m*(p, C, X)` in the raw output. Positive requires
`m > m*`, and **`m* ≥ 0.7956` everywhere** the observed residual applies.

### UNECONOMIC

| region | boundary / why |
|---|---|
| **any state at Stage-6's authorized 1.00 share** | below the cent floor at every price; rebate `$0.00`; `m*` stays **0.7873** |
| **any state with one taker leg** | **173.8 bp** of pair turnover at p = 0.50 vs a **62.5 bp** credit — 2.8×, opposite sign; `m* ≥ 0.8211`; 107 cells unreachable at **any** completion rate |
| **any state at the observed residual and completion** | `m* ≈ 0.772` at size vs observed **67.72%**; CI excludes breakeven by **>7 points**; on L5's own coins 57.90% |
| **p ≤ 0.20 or p ≥ 0.80 at small size** | cent floor rises to 2.51–4.45 shares; asymptotic rate collapses to **12.5 bp** at p = 0.90 |
| **any pair cost `X ≥ 1.00`** | `m* = 1.0000` — every pair must complete |

**Boundary of the whole exercise:** the positive region begins only **above**
the observed completion CI and **below** the observed residual loss rate.
**The residual, not the rebate, sets the sign.**

---

## 10. What would change this, and what would not

**Would not** (all tested, none produces a positive cell): loosening the
`$0.96` ceiling; removing S6P's taker-on-both-legs conservatism; crediting
the full both-legs-maker rebate; trading larger size.

**Would:**

1. a demonstrated matched completion rate **above ~79.6%** on all-maker
   fills — a **measurement**, not a parameter; or
2. a residual loss materially below **−$0.27742** at a loss rate materially
   below **1.000**; or
3. a **source-confirmed** liquidity-reward entitlement of the order of L5's
   **+163.4 bp**.

(1) and (2) are measurements we do not hold. (3) is an entitlement we have no
source for — and per the operating model, **a rebate or reward is not income
until it is credited**, and **never permission to make an otherwise bad trade
look profitable**.

**The bottleneck is not the fee schedule. It is the residual, and the 95.58%
UNKNOWABLE fill question underneath it.** No amount of further rebate
arithmetic will move either.
