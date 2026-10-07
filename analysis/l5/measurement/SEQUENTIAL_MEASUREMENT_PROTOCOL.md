# SEQUENTIAL MEASUREMENT PROTOCOL — resolving the 95.58% UNKNOWABLE by owning the orders

> # ⛔ NOT AUTHORIZED. NOT ARMED. NO ORDER MAY BE PLACED ON THE BASIS OF THIS DOCUMENT.
>
> **This is a DESIGN.** No order was placed. No order is authorized. Nothing is
> armed. No owner value is set. No credential, signing path, Railway service,
> `live_exec*` module, A6, S3, the XR2W executor, or the running
> `l5-book-shadow` collector was touched or invoked. Lane 4's files under
> `analysis/l5/stage6/` were **read only and never written**, and their
> `assert_frozen()` / `assert_bounds_coherent()` still pass unchanged.
>
> **The owner reviews this before anything goes live.** The four sizes below
> are presented as **OPTIONS FOR THE OWNER**, never as a recommendation, and
> **this document does not recommend a size.** The frozen Stage-6 candidate
> remains **1.00 share** and its verdict remains **INCONCLUSIVE /
> DO-NOT-AUTHORIZE**, exactly as
> `analysis/l5/stage6_validation/STAGE6_HISTORICAL_VALIDATION.md` §9 states it.
> Nothing here overrides, relaxes, re-fits or re-derives any frozen bound.
>
> `LABEL: DESIGN ONLY — NOT HISTORICAL REPLAY, NOT SHADOW, NOT SUBMITTED, NOT
> FILLED, NOT SETTLED, NOT REALIZED.`

**NO PARAMETER FITTING.** Nothing in this document was chosen to improve an
outcome. The two SPRT hypothesis points are **pre-existing numbers lifted from
Stage-6** (the observed proxy matched fraction `0.6772` and the derived
break-even `0.7873`); the precision targets are presented as a **grid of
options** priced for the owner, not as a single tuned choice. Where a number
had to be assumed in order to do arithmetic at all, it is named and the
arithmetic is given as a **function of** it rather than at one point. Lane 5's
failure was **not** used to re-fit anything: see §8.

Calculator: `analysis/l5/measurement/sample_size.py` (pure arithmetic, no
network, no credential, standard library only — enforced by test).
Tests: `analysis/l5/measurement/test_sample_size.py`, **28 passing**.

---

## 0. What is being bought, and what is not

Stage-6 historical validation established that under a passive maker first leg,
**95.58%** of evaluable historical states cannot say whether our order would
have filled. The cause is structural: the archive is a **book** archive —
aligned ladders written on change, **no trade prints, no sequence numbers** — so
an order joining behind resting size `Q` at its own price cannot be shown to
have traded. A vanished level is equally consistent with mass cancellation.
**Historical L2 replay is exhausted. More of it cannot answer this.**

Exactly one thing collapses the UNKNOWABLE bucket to **0%**: **owning the
order.** Our own fill is reported to us by the venue, per fill, with its own
timestamp, price and quantity. There is no inference step and no proxy. This is
why the instrument works, and it works **at any size** — the resolution of
UNKNOWABLE does not depend on how big our order is. What size changes is *which
estimands the instrument can reach*, which is §3 and §4.

**This is a measurement instrument, not a profit strategy.** Its stopping gates
are **information gates, not profit targets.** A measurement that stops because
the answer is clear has succeeded whether the answer is good or bad; a
measurement that stops because it is losing money has merely been truncated.
The two must never be conflated, and the protocol below stops on **posterior
width** and on **likelihood ratio**, never on cumulative P&L. A separate
cumulative loss bound (§2.5) exists as a **risk** control that *aborts* the
experiment; crossing it is a **failure to measure**, reported as such, and never
reported as a result.

### The five estimands

| | estimand | sample unit |
|---|---|---|
| **E1** | first-leg maker fill probability | one **placement** |
| **E2** | complement completion probability, **conditional on E1 having filled** | one **first-leg fill** |
| **E3** | time-to-completion, **as a distribution**, not a mean | one **completion** |
| **E4** | residual frequency and residual duration | one **fill** / one **residual** |
| **E5** | realized matched and residual economics | one **first-leg fill** |

**The cascade is the cost driver and must be stated before any number.** E2–E5
are denominated in *fills*, not placements, and fills are produced by E1 at the
rate E1 exists to measure. So the dollar cost of reaching E2–E5 is
**`required_fills / p_fill`** placements, and `p_fill` is **unknown** — that is
E1. Every total below is therefore given as a **function of `p_fill`** across a
grid, never as a single number. Anyone quoting a single total has assumed the
answer.

---

## 1. (A) SAMPLE-SIZE STOPPING GATES

### 1.1 E1 — first-leg maker fill probability

**Method: Bayesian Beta–Binomial posterior-width stopping.** Prior
`Beta(1, 1)`; after `k` fills in `n` placements the posterior is
`Beta(1+k, 1+n-k)`; **stop when the 95% equal-tailed credible interval is no
wider than `w`.**

**No hypothesis test is used here, deliberately.** There is no pre-existing
decision boundary for this quantity — the entire point of the lane is that no
one knows what it is. Inventing an `H0` for it would be exactly the fitting the
mandate forbids. So E1 is **pure estimation**, and its gate is a precision
target the owner picks.

Worst-case `n` (at the variance-maximising truth `p = 0.5`):

| 95% interval total width `w` | `n` placements | what it decides |
|---|---:|---|
| 0.20 | **97** | order-of-magnitude only: distinguishes "rare" from "common" |
| 0.10 | **385** | decision-useful: e.g. separates 0.10 from 0.25 cleanly |
| 0.05 | **1,537** | tight enough to price a cohort |

**Operating characteristics.** The gate is a width, so coverage is the nominal
95% by construction and there is **no multiplicity penalty**: stopping on
posterior width is not a repeated significance test, and the posterior is the
same object regardless of when you look at it. Expected sample size is *lower*
than the table when the truth is away from 0.5 — at `p = 0.10`, width 0.10
needs **139** placements, not 385, because `p(1-p)` is smaller. **Worst case is
the table.** Add a hard truncation at the table value so the instrument cannot
run forever.

**Early stopping in either direction is automatic and is the point.** If the
first 40 placements produce 0 fills, the `Beta(1, 41)` posterior already has a
95% upper bound near **0.086** — "a passive first leg essentially does not fill
at our price under our selector" is then established, and the protocol stops
having **answered** the question negatively. That is a success of the
instrument, not a failure.

### 1.2 E2 — completion probability conditional on first fill

**Method: truncated Wald SPRT**, because here — unlike E1 — a decision boundary
already exists and was **not** chosen by this lane.

* `H0: p_complete = 0.6772` — Stage-6's **observed** proxy matched fraction.
* `H1: p_complete = 0.7873` — Stage-6's **derived** break-even matched
  fraction, the fraction at which `+$0.075` matched gains exactly pay for
  `−$0.277` residual losses.
* `α = β = 0.05`.

Both numbers are quoted from `STAGE6_HISTORICAL_VALIDATION.md` §5. Neither was
selected to produce a convenient sample size, and the pairing is the natural
one: *does the live completion rate reach break-even, or does it sit at the
historical proxy's level?*

Log-likelihood-ratio walk, increment per observation:

```
success  +ln(0.7873/0.6772)              = +0.150643
failure  +ln(0.2127/0.3228)              = −0.417150
accept H1 at  +ln(0.95/0.05) = +2.944439
accept H0 at  −ln(0.95/0.05) = −2.944439
```

**Operating characteristics** (Wald, computed in `sample_size.py`, asserted by
test):

| | value |
|---|---:|
| error rates | `α = 0.05` (accept H1 when H0 true), `β = 0.05` (accept H0 when H1 true) |
| **expected n under H0** (`p = 0.6772`) | **81 fills** |
| **expected n under H1** (`p = 0.7873`) | **89 fills** |
| indifference point (zero drift) | `p = 0.7347` |
| **expected n at indifference — the worst case for the test** | **138 fills** |
| **hard truncation** | **385 fills** (the E1 width-0.10 figure, reused so the two gates share one ceiling rather than inventing a second number) |

Wald's `α`/`β` are the nominal bounds and the discrete walk makes actual error
rates **no worse** than nominal (overshoot of the boundary can only help). On
truncation without a decision, **report the posterior instead of forcing a
verdict** — a truncated SPRT that has not crossed is an honest "still
indistinguishable at 385 fills," which is itself decision-useful: it bounds how
far from break-even the truth can be.

**Early stopping, both directions.** If completion is genuinely poor, the
failure increment is **2.8× larger in magnitude** than the success increment, so
H0 is accepted fast: 8 consecutive non-completions alone cross the lower
boundary (`8 × −0.41715 = −3.337 < −2.944`). The instrument is **asymmetric by
construction in the direction of stopping early on bad news**, which is the
correct asymmetry for a risk-bearing experiment and is a consequence of the
hypothesis pair, not a tuning choice.

### 1.3 E3 — time-to-completion, as a distribution

**Method: Kaplan–Meier with distribution-free order-statistic quantile
intervals.** Durations are **right-censored** — a complement still resting at
the handover TTE is censored, not "infinite" and not imputed. Stage-6 measured
**32.28%** right-censoring on the proxy subset; censoring at that level makes
upper quantiles of the *uncensored* distribution unidentifiable, which is
reported rather than modelled through.

`n` so the 95% CI for the `q`-th quantile spans `± ε` in **coverage** terms
(`n ≥ q(1−q)(z/ε)²`):

| quantile | `ε = 0.10` | `ε = 0.05` |
|---|---:|---:|
| median | 97 | **385** |
| p90 | 35 | **139** |

The tail is **cheaper** than the median here, which is the opposite of
intuition and worth stating: `q(1−q)` is smaller at `q = 0.90`. **A "mean
time-to-completion" is not an estimand of this protocol** and must not be
reported — Stage-6's own proxy distribution has p50 22 s and max 699 s, a shape
whose mean is dominated by the tail and is not a useful summary.

### 1.4 E4 — residual frequency and residual duration

**Residual frequency is free.** It is `1 − E2`, read off the same observations;
it costs no additional placement and no additional dollar.

**Residual duration is the most expensive gate in the whole protocol**, and this
is the one non-obvious result of the sizing exercise. Residuals are produced at
rate `1 − p_complete`, which is the *smaller* branch. To observe 139 residuals
(p90, `ε = 0.05`), at Stage-6's observed completion rate of 0.6772:

```
139 / (1 − 0.6772) = 139 / 0.3228 = 430.6  ->  431 first-leg fills
```

**431 fills is therefore the binding fill-denominated gate** — more than the
SPRT's 385 truncation, more than E5's 261, more than E3's
`ceil(139/0.6772) = 206`. Asserted by test. The 0.6772 used for this conversion
is **Stage-6's biased proxy figure and is not an estimate of the live rate** —
it is used only to *price the experiment*, and if the live completion rate is
lower, residuals arrive **faster** and this gate gets **cheaper**, not dearer.

### 1.5 E5 — realized matched and residual economics

**Method: per-share mean with a normal CI** over the two-point
matched/residual mixture. Using Stage-6's own per-outcome figures
(`+$0.07496` matched, `−$0.27742` residual, mixed at 0.6772) the per-filled-leg
standard deviation is **$0.1648 per share**:

| CI half-width, USD **per share** | `n` fills |
|---|---:|
| ±$0.05 | **42** |
| ±$0.02 | **261** |

Two things about this gate. First, the estimand is **per share**, so `n` does
**not** change with trade size — a 0.01-share experiment needs the same 261
fills to pin the per-share expectancy to ±$0.02 as a 1.62-share one. Only the
*dollars at risk* scale. Second, **this gate measures the economics of whatever
cohort actually runs; it does not validate a profit.** Nothing here is realized
P&L. Per CLAUDE.md, nothing is realized without settlement and cash
confirmation, and E5's output is labelled `FILLED` → `SETTLED` only when
settlement audit confirms it.

### 1.6 The gate summary, and what "all gates" means

| estimand | method | unit | expected n | worst-case n |
|---|---|---|---:|---:|
| E1 fill probability | Beta(1,1) posterior width 0.10 | placement | 139 @ p=0.10 | **385** |
| E2 completion \| fill | truncated SPRT 0.6772 vs 0.7873 | fill | **81–89** | **385** (truncation) |
| E3 time-to-completion p90 | KM + order-statistic CI, ε=0.05 | completion | 139 | 139 (→ **206** fills) |
| E4 residual frequency | `1 − E2` | — | **0 extra** | **0 extra** |
| E4 residual duration p90 | KM + order-statistic CI, ε=0.05 | residual | 139 | 139 (→ **431** fills) |
| E5 per-share economics | normal CI, ±$0.02/share | fill | 261 | 261 |

**ALL GATES** = `385` placements **and** `431` fills, i.e.
`max(385, ceil(431 / p_fill))` placements. **Independent gates are not
multiplicity-corrected across estimands**, because they are five separate
questions with five separate reports, not one hypothesis tested five ways. No
estimand's gate may be moved after data collection begins; the grid above is
the menu, and the owner picks one row per estimand **before** the first
placement.

---

## 2. (B) WORST-CASE DOLLARS AT RISK — four sizes, arithmetic shown

### 2.1 Where the worst case comes from (frozen, not re-derived)

From `analysis/l5/stage6/bounds.py`, unchanged:

* **Both legs are BUYs.** The venue has a single instrument; "sell NO" is "buy
  YES". So cash out is bounded by what we pay.
* A pair completed at or under the ceiling **locks a non-negative gross**, since
  one side of a binary pays $1.00. **A completed pair is therefore not the
  loss-maximising outcome.**
* The worst case is **the first leg filling in full and the complement never
  filling**, leaving an unmatched directional residual that **settles to zero.**
* `MAX_FIRST_LEG_PRICE = 0.60`, a ceiling.

```
max loss per first leg = qty × $0.60
```

`MAX_FIRST_LEGS = 1` and `MAX_CONCURRENT_MARKETS = 1` per session, so
**max loss per session = max loss per first leg.** Stage-6 confirmed this
empirically: worst measured single residual loss **−$0.600000** at 1.00 share,
exactly the bound.

### 2.2 Per-leg and per-session, the four sizes

| qty (shares) | arithmetic | **max loss per first leg** | **max loss per session** |
|---|---|---:|---:|
| 0.01 | `0.01 × 0.60` | **$0.0060** | **$0.0060** |
| 0.10 | `0.10 × 0.60` | **$0.0600** | **$0.0600** |
| **1.00** *(frozen candidate)* | `1.00 × 0.60` | **$0.6000** | **$0.6000** |
| 1.62 | `1.62 × 0.60` | **$0.9720** | **$0.9720** |

At 1.62 shares there is a second, **tighter** figure worth recording: the rebate
is non-zero only for `p ∈ [0.45, 0.55]` (§3), so a placement constrained to be
rebate-bearing cannot price above $0.55, giving `1.62 × 0.55 = $0.8910`. The
**$0.9720** ceiling figure is the one carried below, because it is the larger.

### 2.3 Worst-case total to reach each gate

Worst case is `placements × qty × $0.60` — every placement filling at the
ceiling and every complement failing. **This is a bound, not a forecast**; it is
`1/p_complete` times more pessimistic than any plausible path, and that is what
a bound is for.

**E1 alone — 385 placements** (fill-probability gate; needs no fills to
complete, so it has **no `p_fill` cascade**):

| qty | arithmetic | worst case |
|---|---|---:|
| 0.01 | `385 × 0.0060` | **$2.31** |
| 0.10 | `385 × 0.0600` | **$23.10** |
| 1.00 | `385 × 0.6000` | **$231.00** |
| 1.62 | `385 × 0.9720` | **$374.22** |

**E2, E3, E4, E5 — fill-denominated, so cascaded through the unknown
`p_fill`.** Placements `= ceil(required_fills / p_fill)`:

| required fills | gate |
|---:|---|
| 385 | E2 SPRT truncation |
| 206 | E3 time-to-completion p90 (`139 / 0.6772`) |
| **431** | E4 residual duration p90 (`139 / 0.3228`) — **binding** |
| 261 | E5 per-share economics ±$0.02 |

### 2.4 Worst-case total to reach **ALL** gates

`placements = max(385, ceil(431 / p_fill))`, then `× qty × $0.60`:

| `p_fill` | placements | **0.01** | **0.10** | **1.00** | **1.62** |
|---|---:|---:|---:|---:|---:|
| 0.10 | 4,310 | **$25.86** | **$258.60** | **$2,586.00** | **$4,189.32** |
| 0.25 | 1,724 | **$10.34** | **$103.44** | **$1,034.40** | **$1,675.73** |
| 0.50 | 862 | **$5.17** | **$51.72** | **$517.20** | **$837.86** |
| 0.87 | 496 | **$2.98** | **$29.76** | **$297.60** | **$482.11** |

Arithmetic check, one cell by hand: `p_fill = 0.50`, qty 1.00 →
`ceil(431/0.5) = 862` placements, `862 × 1.00 × $0.60 = $517.20`. ✓ (asserted by
test). The `p_fill = 0.87` row uses Stage-6's **proxy** fill rate and is the
**cheapest and least credible** row: §3C of that document shows 0.87 is
adverse-selected by construction. The `p_fill = 0.10` row is not a pessimistic
guess either — a passive order joining behind existing size may well fill
rarely, which is precisely the hypothesis nobody can currently exclude.

**The table's spread is the honest headline: the worst-case cost of reaching all
gates ranges over a factor of ~8.7 purely because `p_fill` is unknown, and over
a further factor of 162 across the four sizes.** Which is why E1 is cheap, has
no cascade, and should be read as the gate that *prices the rest of the
experiment*: 385 placements at the smallest size costs **$2.31** worst case and
converts every row below it from a range into a number.

### 2.5 The cumulative bound that does not yet exist

`bounds.py` sets `MAX_DAILY_REALIZED_LOSS_USD = $0.60`, justified by "one probe
per authorization, no automatic repeat." Stage-6 §4 flagged that this is the
first assumption a cohort retires, and measured a **$13.22** maximum drawdown
over a 727-probe serial path at 1.00 share against that $0.60 per-session bound.

**A sequential measurement cohort is by definition many probes, so it cannot run
under the existing bounds as written.** This document does **not** set a
cumulative bound — setting one is an owner value, and setting owner values is
prohibited here. It records that **a cumulative bound is a precondition the
owner must supply**, that §2.4 sizes what it would have to cover, and that an
abort on that bound is a **failure to measure** (§0), reported as such.

---

## 3. (C) THE REBATE CLIFF, quantified

The PMUS maker rebate (`src/pmus_fee_schedule.py`, fingerprint
`ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`) is

```
rebate = ROUND_HALF_EVEN( 0.0125 × C × p × (1 − p) , $0.01 )
```

**The floor is HARD and STRICT.** `ROUND_HALF_EVEN` sends exactly $0.005 to
**$0.00** (0 is the even digit), so a cent requires

```
0.0125 × C × p × (1 − p)  >  0.005        i.e.    C × p(1−p) > 0.4
```

Recomputed here from the published formula rather than copied, and it reproduces
`bounds.py`'s stated floor exactly (asserted by test):

| `p` | `p(1−p)` | **min rebate-bearing contracts** |
|---|---:|---:|
| 0.30 | 0.2100 | **1.91** |
| 0.45 | 0.2475 | **1.62** ← the figure `bounds.py` states |
| 0.50 | 0.2500 | **1.61** (the global minimum) |
| 0.55 | 0.2475 | **1.62** |
| 0.60 | 0.2400 | **1.67** |

**The floor is price-dependent, and this is the part most easily missed.** At
1.62 shares the rebate is non-zero **only for `p ∈ [0.45, 0.55]` — 11 ticks of
the 99-tick domain** (asserted by test). So "1.62 shares is rebate-bearing" is
true only near 50¢. At `p = 0.40` it takes 1.67 shares; at `p = 0.30`, 1.91.
A protocol that wants rebate-bearing observations across the whole placeable
range `[0.01, 0.60]` needs **1.91 shares**, not 1.62 — and 1.91 shares carries a
worst case of `1.91 × 0.60 = $1.146`.

### What each size can and cannot measure

| | **0.01** | **0.10** | **1.00** (frozen) | **1.62** |
|---|---|---|---|---|
| resolves UNKNOWABLE (own fill is observed) | **yes** | **yes** | **yes** | **yes** |
| E1 fill probability (any fill) | yes | yes | yes | yes |
| E2 completion \| fill | yes | yes | yes | yes |
| E3 / E4 timing distributions | yes | yes | yes | yes |
| E5 per-share economics | yes | yes | yes | yes |
| **partial-fill structure** | **NO** — 0.01 is the venue qty grid, so there is exactly one fill state and a partial fill is indistinguishable from a full one | coarse: 10 states | 100 states | 162 states |
| **maker rebate economics** | **STRUCTURALLY BLIND — $0.00, always, at every price** | **STRUCTURALLY BLIND — $0.00, always, at every price** | **STRUCTURALLY BLIND — $0.00, always** (this is deliberate in `bounds.py` via `REBATE_IS_ASSUMED_ZERO`) | **yes, but only for `p ∈ [0.45, 0.55]`** |
| rebate economics across all of `p ≤ 0.60` | no | no | no | **no** — needs ≥ 1.91 shares |

**0.01 and 0.10 can never measure rebate economics. Not "measure them poorly" —
measure them at all.** $0.00 is $0.00, and no number of observations at those
sizes aggregates into a rebate estimate, because every single observation is
exactly zero by cent-floor arithmetic. If rebate economics is part of the
question, **only a size at or above the price-dependent floor can answer it**,
and the floor is 1.61–1.91 shares depending on where the price sits.

Conversely: **a size below the floor measures the protocol's economics *without*
rebate contamination**, which is itself a clean experiment and is exactly what
`REBATE_IS_ASSUMED_ZERO` was for. Stage-6's verdict already does not depend on
the rebate in either direction.

**Taker side, for completeness.** `taker_theta = 0.0695` effective
2026-09-17T04:00Z (**not** the stale flat 6%; `src/pmus_incentive_audit.py`'s
old scalar is superseded). The taker coefficient has **no cent floor exemption
and no size threshold** — it is a cost, charged on any crossing leg at any size,
and it enters only if a complement crosses. Taker **rebate** tiers (10/25/50%
above $250k/$1M/$10M prior-month notional) are
`taker_rebate_available_to_this_account: False` and **may not be assumed at any
size.** Fills in the 2026-09-17 → 2026-10-01 window are **date-ambiguous** per
the schedule's recorded source conflict and must be rated at the higher
coefficient and **flagged**, never quietly rated.

**Per CLAUDE.md: no rebate or reward is income until the venue has credited it**
(on PMUS, a negative `commissionNotionalCollected` on our own execution, per
fill). Every rebate figure this protocol produces is `ESTIMATED` until that
credit appears, kept in a separate account from trading P&L, and never netted
against a cost.

---

## 4. (D) THE INFORMATION-PER-DOLLAR TRADE-OFF — stated honestly

Observations per dollar at risk scale as `1 / (qty × 0.60)`:

| qty | worst-case $ per placement | **placements per $1 of worst-case risk** | relative to 1.00 share |
|---|---:|---:|---:|
| 0.01 | $0.0060 | **166.7** | **100×** |
| 0.10 | $0.0600 | **16.7** | 10× |
| 1.00 | $0.6000 | **1.67** | 1× |
| 1.62 | $0.9720 | **1.03** | 0.62× |

So the smallest size buys **100× more observations per dollar** than the frozen
candidate. That is the case for it. Here is the case against, in full.

### 4.1 Where tiny-order measurements ARE transferable

* **Resolution of UNKNOWABLE itself.** Our own fill is reported to us. This is
  size-independent and is the entire point of the instrument. A 0.01-share
  experiment collapses the 95.58% to 0% exactly as well as a 1.62-share one.
* **Queue priority is price-then-time, not size.** Two orders placed at the same
  instant at the same price have the **same queue position** regardless of size.
  So **`P(our order receives any fill)`** — the probability that the touch
  reaches our price and consumes the queue ahead of us — is a property of the
  *market*, not of our size, and **transfers**.
* **E2, completion probability conditional on fill.** Conditional on the first
  leg having filled, whether the complement fills is a property of the
  complement's price and the market's subsequent path, not of our size at these
  magnitudes.
* **E3 / E4 timing distributions.** Same argument; seconds are seconds.
* **E5 as a per-share quantity.** `+$0.075` and `−$0.277` are per-share figures.
  The *mixture* and its SD are scale-free; only the dollar total scales. The
  **sign** of expectancy transfers, which is the decision-relevant part.
* **The direction of adverse selection.** Whether our maker fills are
  systematically the wrong side is a per-share property and transfers.

### 4.2 Where they are NOT transferable — and this is not papered over

1. **Rebate economics: not transferable at all, in principle.** §3. Below the
   cent floor every observation is exactly $0.00. There is no extrapolation,
   because there is no signal to extrapolate. **A 0.01-share experiment can
   never say anything about rebates, however long it runs.**
2. **`P(FULL fill)` is size-dependent even though `P(any fill)` is not.** A
   sweep must consume `Q_ahead + our_size`. At 0.01 shares, `our_size` is
   negligible against any realistic `Q_ahead`, so `P(full) ≈ P(any)`. At 1.62
   shares it is not, and the gap grows with size and shrinks with level depth.
   **So a tiny order measures `P(any fill)` and silently reports it as
   `P(full fill)`.** For a protocol whose complement is sized to the first leg's
   *actual* fill, that difference is not cosmetic: it changes what inventory the
   second leg has to match.
3. **Partial-fill structure is unobservable at 0.01 by grid arithmetic.** One
   quantity state exists. A 0.01-share experiment cannot discover that partial
   fills are common, which is one of the two reasons `bounds.py` rejected 0.01
   in the first place.
4. **Own-size market effect is unmeasurable at 0.01 and possibly non-zero at
   1.62.** Adding 1.62 shares to a thin level changes the displayed size other
   participants react to; 0.01 does not. **Neither size can measure this
   about the other**, and the protocol should not claim it can.
5. **Minimum-notional and rounding behaviour at the venue edge.** A 0.01-share
   order may be handled differently by venue-side minimums, fee rounding, or
   rejection logic than a 1.62-share one. `analysis/l5/MIN_SIZE_CONSTRAINT.md`
   is the relevant prior; this protocol does not re-litigate it, and any
   finding at one size is **not** evidence about the other.

### 4.3 The one partial recovery, labelled as a model and not a measurement

Logging **`Q_ahead`, the displayed quantity resting at our price at the moment
of placement**, costs nothing — it is already in the book feed the collector
reads — and lets `P(fill)` be estimated **conditional on `Q_ahead`** rather than
marginally. Since a larger order is, for fill purposes, equivalent to a smaller
order with more quantity ahead of it, a conditional `P(fill | Q_ahead)` curve
measured at 0.01 shares can be **evaluated** at the `Q_ahead + Δ` a larger order
would face.

**This is an extrapolation under a model, not a measurement, and it must be
labelled that way every time it is used.** It assumes fills consume a level
contiguously and that our presence does not alter the arriving flow — assumption
(4) above, which is precisely what cannot be checked at small size. It is worth
logging `Q_ahead` regardless, because it costs nothing and it is the only bridge
between the sizes that exists at all.

### 4.4 The verdict on information-per-dollar

**The estimands split cleanly into two groups, and the trade-off is therefore
not a single trade-off at all:**

* **E1 (as `P(any fill)`), E2, E3, E4, and E5-per-share are measured
  essentially as well at 0.01 shares as at 1.62, at 1/100th of the dollars at
  risk.** For these, small size is not a compromise; it is strictly more
  information per dollar with no identified loss of validity.
* **Rebate economics, partial-fill structure, `P(FULL fill)`, and own-size
  effects are measured at 0.01 shares either not at all or wrongly.** For
  these, no amount of cheap data substitutes; they require a size at or above
  the relevant structural threshold (1.61–1.91 shares for rebates, ≥ ~0.10
  shares for any partial-fill resolution at all).

**So the honest framing is not "which size" but "which questions, in which
order."** The questions that defeated Stage-6 — *does a passive first leg fill,
and if it fills does the complement complete* — are in the first group. The
questions about rebate-era economics are in the second, are **not** what Lane 5
failed on, and are gated by CLAUDE.md's rule that mature incentive-era behaviour
stays locked until equivalent economics are source-confirmed for our account.

**This document does not recommend a size.** It states which size can answer
which question at what worst-case cost, and the choice — like the cumulative
loss bound, like the authorization itself — is the owner's.

---

## 5. (E) WHAT THIS PROTOCOL CANNOT ESTABLISH

1. **It cannot establish that the Stage-6 protocol is profitable, and it is not
   designed to.** It is an instrument for five estimands. Even all five gates
   cleared in the favourable direction is **evidence for an authorization
   decision, not an authorization**, and §1.5's output is `FILLED`/`SETTLED`,
   never `REALIZED` without settlement and cash confirmation.
2. **It does not supersede Stage-6's INCONCLUSIVE / DO-NOT-AUTHORIZE verdict,
   and nothing in it may be cited as doing so.** That verdict stands exactly as
   written, bound to its three module sha256s, and still requires re-binding
   before it may be cited for authorization at all.
3. **It cannot retroactively fix the archive.** The 95.58% of *historical*
   states remain unknowable forever. Owning orders resolves the question
   **going forward only**, in whatever market regime the clock happens to be
   in, and says nothing about `REGIME_PRE_2026-04-25`, `REGIME_2026-04-25` or
   `REGIME_2026-06-23`, which have **zero** evaluable states and cannot be
   validated with any data we hold or could collect now.
4. **It cannot measure rebates below the cent floor** (§3) — not poorly, at all.
   And at 1.62 shares it measures them only for `p ∈ [0.45, 0.55]`.
5. **It cannot establish a counterfactual at the same instant.** With
   `MAX_OUTSTANDING_ORDERS = 1` there is exactly one order at one price at one
   time. "Would a different price, a different size, or one tick more
   aggression have filled?" is **not** answerable by this instrument. Answering
   it requires simultaneous orders, which the frozen bounds forbid.
6. **It cannot distinguish cancellation from trade for anyone else's order.**
   It resolves **our own** fills with certainty and resolves nothing about the
   rest of the book. The LEVEL_CONSUMED ambiguity persists for every order that
   is not ours.
7. **`p_fill` is conditional on the discovery selector, not on markets at
   large.** Markets are chosen by the PMUS selector, not randomised, so the
   measured fill probability is `P(fill | this selector, this period)`. Changing
   the selector invalidates the estimate, and the protocol cannot detect that
   from its own output.
8. **It cannot establish L5 behavioural fidelity.** CLAUDE.md's validation
   standard — *at this exact market state, what did L5 do next, and what would
   our clone have done?* — is **not** met by this lane and is not claimed.
   This measures *our* orders, not agreement with L5's.
9. **It cannot establish capacity or scaling.** Everything is at one size, one
   market at a time, one outstanding order. Nothing here bounds what happens at
   10× size or with concurrency, and the one bridge between sizes (§4.3) is an
   explicit model, not a measurement.
10. **It cannot establish cross-venue transfer.** Stage-6's archive is
    Polymarket **international**; the fee schedule, cent floor and tick grid are
    Polymarket **US**. This protocol measures PMUS only, so it cannot confirm or
    refute Stage-6's cross-venue numbers — it replaces them with PMUS ones.
11. **It cannot establish the upper tail of residual duration under heavy
    censoring.** At Stage-6's 32.28% right-censoring, quantiles above the
    censoring point are **not identified**. Censored observations are reported,
    never imputed, and no distribution is fitted through them.
12. **It cannot distinguish "the measurement failed" from "the answer is bad" if
    it aborts on a cumulative loss bound.** §0 and §2.5. An abort is a failure
    to measure and must be reported as one, with the partial posterior, and
    never as a result.
13. **It cannot run under the bounds as currently frozen.** A sequential cohort
    is many probes; `MAX_FIRST_LEGS = 1`,
    `MAX_DAILY_REALIZED_LOSS_USD = $0.60` and "no automatic repeat" are written
    for a single probe. **Reconciling that is an owner decision and a
    precondition, and this document deliberately does not make it.**

---

## 6. Preconditions the owner must supply before anything is armed

Listed so the gap is explicit, **not** as a request and **not** as a checklist
this lane may complete on its own:

1. authorization, in the owner's own words;
2. a size, chosen from §2's options with §3 and §4 in view;
3. precision targets — one row per estimand from §1's grids, fixed **before**
   the first placement;
4. a **cumulative** loss bound (§2.5), which does not currently exist;
5. reconciliation of `MAX_FIRST_LEGS = 1` and "no automatic repeat" with a
   multi-probe cohort (§5.13);
6. re-binding of Stage-6's three module sha256s, which that document already
   requires independently of this one.

Until all six exist, **this remains a design and nothing may be armed.**

---

## 7. Provenance

| artifact | path |
|---|---|
| this design | `analysis/l5/measurement/SEQUENTIAL_MEASUREMENT_PROTOCOL.md` |
| calculator (offline, stdlib only) | `analysis/l5/measurement/sample_size.py` |
| tests, 28 passing | `analysis/l5/measurement/test_sample_size.py` |
| the problem being solved | `analysis/l5/stage6_validation/STAGE6_HISTORICAL_VALIDATION.md` |
| frozen bounds, read-only | `analysis/l5/stage6/bounds.py` |
| frozen spec, S6P `32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8` | `analysis/l5/stage6/spec.py` |
| fee schedule, `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629` | `src/pmus_fee_schedule.py` |
| LEVEL_CONSUMED bias honored | `analysis/l5/august_replay/S2_FINDINGS.md` §2 |
| the zero-capital path already running | `src/polymarket_l5/owned_order_shadow.py`, `l5-book-shadow` |

## 8. Anti-drift and no-fitting declaration

* **No parameter was chosen to improve any outcome.** The SPRT's `H0 = 0.6772`
  and `H1 = 0.7873` are Stage-6's own observed and derived numbers. The
  precision targets are a grid of options priced for the owner, not a selection.
  Where `p_fill` or `p_complete` had to be assumed to do arithmetic, the result
  is given **as a function of** the assumption across a grid.
* **Lane 5's failure was not used to re-fit anything.** No bound was loosened,
  no threshold moved, no gate re-derived. `qty` remains **1.00** in the frozen
  spec; the four sizes here are presented as options and explicitly do not
  override it. The INCONCLUSIVE / DO-NOT-AUTHORIZE verdict is preserved
  verbatim.
* **How this serves the L5 program** (CLAUDE.md anti-drift rule): the gating
  constraint on the whole pipeline is **evidence about queue position**, and
  Stage-6 §9 identifies owning the orders as the only route to it. This
  document prices that route. It does not redefine the strategy as arbitrage,
  market making or rebate farming — §3 and §4.2 say plainly that rebate
  economics is the one thing the cheap sizes cannot measure, and §4.4 keeps it
  out of the first group of questions for exactly that reason.
