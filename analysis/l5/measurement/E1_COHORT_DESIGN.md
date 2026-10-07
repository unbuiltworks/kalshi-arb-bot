# COHORT E1 — MECHANICS MEASUREMENT: prospective cohort design

> # ⛔ NOT AUTHORIZED. NOT ARMED. NO ORDER MAY BE PLACED ON THE BASIS OF THIS DOCUMENT.
>
> **This is a DESIGN.** No order was placed. No order is authorized. Nothing is
> armed. No owner value is set. No credential, signing path, Railway service,
> `live_exec*` module, A6, S3, the XR2W executor, or the running
> `l5-book-shadow` collector was touched or invoked. Everything under
> `analysis/l5/stage6/` was **read only and never written**; S6P
> (`32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8`) was
> **not modified and not tuned**, and `assert_frozen()` /
> `assert_bounds_coherent()` still pass unchanged.
>
> **The owner authorizes nothing until E1 is frozen and they approve it.**
> Everything below is presented as a **derivation with a recommendation**. The
> recommendation is a recommendation. **The owner chooses.**
>
> `LABEL: DESIGN ONLY — NOT HISTORICAL REPLAY, NOT SHADOW, NOT SUBMITTED, NOT
> FILLED, NOT SETTLED, NOT REALIZED.`

**PURPOSE IS MECHANICS, NOT PROFIT. NOTHING HERE IS OPTIMIZED FOR P&L.** Not
the quantity, not a gate, not a boundary, not a price. Every number is derived
from exactly one of four kinds of input: the venue's own 0.01 grid; a frozen
repository bound, cited; the published fee formula
(`src/pmus_fee_schedule.py`, fingerprint
`ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`); or a
standard statistical constant. Where a number could only be obtained by
assuming an unknown, it is given **as a function of** that unknown across a
grid. **One place in this design required a judgement that looks economic, and
it is flagged in §1.4 rather than buried.**

Calculator: `analysis/l5/measurement/cohort_design.py` — pure arithmetic,
standard library plus read-only imports of the frozen modules, enforced by
test. Tests: `analysis/l5/measurement/test_cohort_design.py`, **127 passing**.
Builds on `SEQUENTIAL_MEASUREMENT_PROTOCOL.md` and reuses its 28-test
`sample_size.py` machinery (Beta posterior width, Wald SPRT, order-statistic
quantile CIs, per-share normal CI) **without restating or re-deriving any of
it**.

---

## 0. Why this cohort exists

`analysis/l5/stage6_validation/STAGE6_HISTORICAL_VALIDATION.md`: **95.58%** of
evaluable historical states cannot say whether a passive maker order would have
fill. The cause is structural — the archive is **book-only, no trade prints, no
sequence numbers** — so an order joining behind resting size at its own price
cannot be shown to have traded, and a vanished level is equally consistent with
mass cancellation. **Historical L2 replay is exhausted.**

Exactly one thing collapses the UNKNOWABLE bucket to **0%**: **owning the
orders.** Our own fill is reported to us by the venue, per fill, with its own
timestamp, price and quantity. No inference, no proxy. **E1's whole job is to
make the unobservable observable** — and that is why its stopping rules are
information gates and never profit targets.

---

## 1. (a) THE MINIMUM USEFUL QUANTITY — derived, not inherited

### 1.1 The three structural facts the derivation rests on

1. **The venue minimum and the venue grid are both 0.01.** A fill is reported
   as a quantity on that grid, so the observable outcomes of one placement are
   `{0, 0.01, 0.02, …, qty}`.
2. **At 0.01 there is exactly ONE non-zero fill state**, so a partial fill is
   arithmetically indistinguishable from a full one. More observations never
   recover this; it is a property of the grid.
3. **Worst case per placement is `qty × $0.60`** — the frozen first-leg price
   ceiling, both legs BUYs, first leg filling in full and the complement never
   filling, residual settling to zero (`analysis/l5/stage6/bounds.py`, confirmed
   empirically at **−$0.600000** at 1.00 share).

So **observability rises in grid steps and loss rises linearly**, and the
minimum useful quantity is wherever the *last* estimand becomes observable at
all — not wherever anything is maximised.

### 1.2 What the frozen 1.00 share actually is

`bounds.py` chose 1.00 share for **100 distinguishable partial-fill states**.
The forensic record is explicit: `BOUND_PROVENANCE["FIRST_LEG_QTY"]` says
*"Chosen for observability of partial fills, not for expectancy"* with
`fitted: False`. It is **not** from economics, **not** from the venue, **not**
from L5's own sizing, and it sits **below the rebate cent floor at every price**
(`REBATE_IS_ASSUMED_ZERO = True`, deliberately).

Restated in this design's own terms, and asserted by test: **1.00 share is
exactly the quantity at which filled-fraction resolution reaches `ε = 0.01`.**
It is the percentile-resolution choice. That is a defensible choice — it is just
*a choice of ε*, and E1 should state which ε each estimand needs rather than
inherit one.

### 1.3 How many distinguishable states does each estimand actually REQUIRE

| # | estimand | states required | **floor (shares)** | why that floor |
|---|---|---:|---:|---|
| 1 | first-leg maker fill probability | 1 | **0.01** | needs the ZERO state and one non-zero state; our own fill is reported to us, so it is resolved at the venue minimum and is **size-independent** |
| 2 | complement completion \| first-leg fill | 1 | **0.01** | conditional on a fill existing, completion is a property of the complement's price and the market's path, not of our size at these magnitudes |
| 3 | **partial-fill structure** | ≥2 | **0.02** | a *distribution* over filled fraction needs two non-zero states to separate partial from full **at all**. Finer features need `1/ε` states: **0.10** for decile, **0.20** for ε=0.05, **1.00** for ε=0.01. At 0.01 the estimand is **empty** |
| 4 | time-to-completion distribution | 1 | **0.01** | seconds are seconds |
| 5 | residual frequency and duration | 1 | **0.01** | frequency is `1 − #2` on the same observations; duration is seconds |
| 6 | **cancellation opportunity** | ≥2 | **0.02** | cancel-won / fill-won is binary, so one state suffices for *occurrence*. But the diagnostic signature of a **race** is a **partial** fill at cancel time, and at 0.01 a partially-raced cancel is indistinguishable from a fully-raced one |
| 7 | adverse selection | 1 | **0.01** | the direction the market moved after our fill is a per-share property of the market, read from the book feed already recorded |
| 8 | **settlement, end-to-end** | 1 | **0.13** | **the binding constraint — see §1.4** |

### 1.4 The binding constraint, and the one judgement in it

**Settlement cash is reported in CENTS with banker's rounding.** A per-share
cashflow smaller than half a cent per placement is reported as **$0.00** and
cannot be confirmed end-to-end however many times it occurs.

The locked gross on a pair completed **at** the frozen pair-cost ceiling is
`$1.00 − $0.96 = $0.04 per share`. So:

```
qty × $0.04  >  $0.005          (STRICT: ROUND_HALF_EVEN sends $0.005 -> $0.00)
qty          >  0.125
qty          =  0.13            (smallest point on the 0.01 grid above it)
```

Check, asserted by test: `0.13 × 0.04 = $0.0052 → $0.01`; and
`0.12 × 0.04 = $0.0048 → $0.00`. **At 0.12 shares or below, a completed pair's
gain vanishes into cent rounding and estimand 8 returns nothing.**

> **FLAGGED, per the mandate.** This floor is derived from `PAIR_COST_CEILING =
> 0.96`, which is a **CLAUDE.md spring-discipline value copied verbatim**, not
> an economic optimum. The derivation uses it as a **ceiling** — the
> worst-case-thinnest locked gain — which is the conservative direction: a pair
> completed more cheaply has a *larger* per-share gain and is cent-observable at
> a *smaller* quantity. **The quantity is not chosen because $0.04 is
> attractive; it is chosen because $0.04 is the thinnest gain the frozen
> discipline permits, and the instrument must be able to see even that.** If the
> owner regards end-to-end settlement confirmation of the *matched* leg as out
> of scope for E1, the binding floor drops to 0.02 and the recommendation
> changes accordingly. That is the owner's call, not this document's.

### 1.5 The derivation, and the recommendation

```
E1 minimum = max over the eight estimands of their observability floors
           = max(0.01, 0.01, 0.02, 0.01, 0.01, 0.02, 0.01, 0.13)
           = 0.13 shares        [binding estimand: 8, settlement end-to-end]
```

| | at 0.13 shares |
|---|---|
| distinguishable non-zero fill states | **13** |
| filled-fraction resolution | **0.0769** (better than decile) |
| worst case per placement | `0.13 × $0.60` = **$0.0780** |
| worst case per session (`MAX_FIRST_LEGS = 1`) | **$0.0780** |
| rebate-bearing? | **NO**, at any price. Deliberate — that is cohort E2 |
| all eight estimands observable? | **yes** — this is the smallest grid quantity for which that is true |

**RECOMMENDATION: 0.13 shares.** It is the smallest quantity on the venue grid
at which **all eight preregistered estimands return information**, it carries a
worst case of **7.8 cents per placement** — one-eighth of the frozen candidate's
— and it buys **7.7× more observations per dollar at risk** than 1.00 share
while losing only filled-fraction resolution between ε=0.0769 and ε=0.01.

**It is recommended for observability coverage and for nothing else.** It was
not compared against any expectancy, and no step of the derivation consulted
one. **The owner chooses**, and the alternatives are priced in §4 so the choice
is informed rather than offered blind:

| if the owner wants | quantity | worst case / placement |
|---|---:|---:|
| the cheapest possible resolution of the 95.58% UNKNOWABLE, accepting that estimands 3, 6 and 8 return nothing | **0.01** | $0.0060 |
| partial/full separable, settlement gain still invisible | 0.02 | $0.0120 |
| decile filled-fraction resolution, settlement gain still invisible | 0.10 | $0.0600 |
| **all eight estimands observable — this design's recommendation** | **0.13** | **$0.0780** |
| ε=0.05 filled-fraction resolution, all eight observable | 0.20 | $0.1200 |
| the frozen S6P candidate: ε=0.01 resolution, all eight observable | 1.00 | $0.6000 |

---

## 2. (b) THE EIGHT PREREGISTERED ESTIMANDS

**Definitions are fixed BEFORE any data.** No estimand's definition, gate or
precision target may be moved after the first placement. Precision targets are
the prior protocol's own grid reused unchanged — `width = 0.10` for
proportions, `ε = 0.05` for quantiles, `±$0.02/share` for per-share means — so
**no new threshold is invented here.**

### 1 — first-leg maker fill probability
* **Definition:** `P(our post-only first leg receives ANY fill before the frozen handover TTE | placed)`. Reported together with `Q_ahead` at placement, which costs nothing and is the only bridge between sizes that exists.
* **Method:** Bayesian Beta(1,1) posterior-width stopping. No hypothesis test: **no pre-existing decision boundary exists for this quantity — that is the whole point of the cohort — and inventing an `H0` for it would be exactly the fitting the mandate forbids.**
* **Sample unit:** one **placement**. **n = 385** (worst case, at the variance-maximising `p = 0.5`).
* **Falsified by:** a 95% posterior **upper** bound below 0.10 — the fill-denominated estimands then cost more placements than the cheapest row this design has priced. See §3.
* **What it is NOT:** `P(FULL fill)`. At 0.13 shares `our_size` is small against realistic `Q_ahead`, so this measures `P(any fill)` and must be reported as such.

### 2 — complement completion probability CONDITIONAL on first-leg fill
* **Definition:** `P(the complement, sized to the first leg's ACTUAL filled quantity, fills before the TTE cutoff | the first leg filled)`. **Conditioning on the fill is part of the definition**, not a later slice.
* **Method:** truncated **Wald SPRT**, because here — unlike estimand 1 — a boundary already exists and **was not chosen by this cohort**: `H0 = 0.6772` (Stage-6's *observed* proxy matched fraction) vs `H1 = 0.7873` (Stage-6's *derived* break-even), `α = β = 0.05`.
* **Sample unit:** one **first-leg fill**. Expected **81** fills under H0, **89** under H1, **138** at the indifference point `p = 0.7347`; **truncated at 385**.
* **Falsified by:** the walk crossing `−2.944439`. **Triggered outright by 8 consecutive non-completions** (`8 × −0.417150 = −3.337`), because the failure increment is 2.8× the success increment — an asymmetry that is a consequence of the hypothesis pair, not a tuning choice.

### 3 — partial-fill structure
* **Definition:** the **distribution** of `filled_qty / placed_qty` over all placements receiving a non-zero fill, on the 13 states §1 provides. **A mean filled fraction is NOT an estimand of this cohort and must not be reported.**
* **Method:** empirical distribution with distribution-free order-statistic quantile CIs — the same machinery as estimands 4 and 5.
* **Sample unit:** one **first-leg fill**. **n = 385** (median, ε = 0.05).
* **Falsified by:** every fill landing in the top state, which would say partial fills do not occur at our size. **That is a positive finding, and it is the finding the venue minimum structurally cannot produce**, because at 0.01 the top state is the only state.

### 4 — time-to-completion, as a distribution
* **Definition:** seconds from first-leg fill to complement fill, **right-censored** at the frozen handover TTE. A complement still resting at handover is **censored, not infinite and not imputed**.
* **Method:** **Kaplan–Meier** with distribution-free order-statistic quantile intervals. **No mean is reported** — Stage-6's own proxy distribution has p50 22 s and max 699 s, a shape whose mean is dominated by the tail.
* **Sample unit:** one **completion**. **n = 139** (p90, ε = 0.05) → **206 fills** at Stage-6's observed completion rate.
* **Falsified by:** censoring heavy enough that the p90 sits above the censoring point, in which case it is **NOT IDENTIFIED** and is reported as such rather than modelled through. Stage-6 measured **32.28%** right-censoring on the proxy subset.

### 5 — residual frequency and residual duration
* **Definition:** frequency is `1 − estimand 2`. Duration is seconds from the complement's non-fill at handover to the earlier of settlement or flat, right-censored identically.
* **Method:** frequency is read off the same observations and **costs no extra placement and no extra dollar**; duration uses the same Kaplan–Meier quantile CI.
* **Sample unit:** one fill (frequency) / one **residual** (duration). **n = 139 residuals** → **431 fills** at `1 − 0.6772`. **This is the BINDING fill-denominated gate** — more than the SPRT's 385, more than estimand 8's 261, more than estimand 4's 206.
* **Falsified by:** a residual frequency incompatible with `1 − estimand 2`, which would mean the inventory ledger and the fill ledger disagree — **an instrument fault, not a result.**
* **Note on pricing:** the `0.6772` used for the fills conversion is **Stage-6's biased proxy figure and is not an estimate of the live rate.** It prices the experiment only. If the live completion rate is lower, residuals arrive **faster** and this gate gets **cheaper**.

### 6 — CANCELLATION OPPORTUNITY
* **Definition:** two quantities. (i) `P(a cancel we intend lands before any fill | cancel attempted)`. (ii) the **race**: the signed interval between our cancel acknowledgement and any fill the venue reports, as a **distribution**.
* **Method:** Beta(1,1) posterior on (i); quantile CIs on (ii). **Read off S6P's existing event pair** `CANCEL_FIRST_LEG → POST_CANCEL_SWEEP_FIRST_LEG`, which **already** polls once more after the cancel and counts anything that landed before it took effect. Nothing in S6P is modified; this estimand consumes what the frozen machine already emits.
* **Sample unit:** one **cancel attempt**. The frozen machine issues **exactly one cancel attempt per placement**, so this estimand is **FREE — zero extra placements, zero extra dollars.** **n = 385 placements.**
* **Falsified by:** any post-cancel sweep returning a fill with a venue timestamp **AFTER** the cancel acknowledgement. That would mean cancellation does **not** bound exposure, which **invalidates the worst-case arithmetic every bound in `bounds.py` rests on.** It is a HARD stop that invalidates the design, not merely the run.

### 7 — ADVERSE SELECTION
* **Definition:** conditional on our passive leg filling, which way did the market then move? Primary: `P(the midpoint of the token we bought is LOWER at t+h than at our fill | our passive leg filled)`, plus the **signed** move as a distribution. Horizons `h` are **preregistered as a fixed set supplied by the frozen window geometry** (the handover TTE and the window length) — **not searched**, because searching horizons is fitting.
* **Method:** Beta(1,1) posterior on the direction; quantile CIs on the signed move.
* **Sample unit:** one **first-leg fill**. **n = 385.**
* **Falsified by:** a 95% posterior interval straddling 0.50 with the signed move's interval straddling zero — i.e. our passive fills are **not** systematically on the wrong side.
* **THE HISTORICAL FIGURE IS NOT INHERITED.** The historical lane's provable-residual loss rate of **1.000** is a *demonstration that the proxy's fill rule and its outcome rule are biased in the SAME direction* — the leg "fills" because the price crossed down through us, so the token we bought is the one that fell. **It is not a forecast, and it is NOT used here as a prior, a hypothesis point, or a boundary. E1 measures adverse selection directly, from scratch.**

### 8 — SETTLEMENT, end-to-end
* **Definition:** every placement followed through the frozen lifecycle `SUBMITTED → RESTING → FILLED → SETTLED → REALIZED`, with per-share matched and residual economics.
* **Method:** reconciliation through `analysis/l5/stage6/settlement.py`, unmodified. Per-share mean with a normal CI over the two-point matched/residual mixture (SD **$0.1648/share** from Stage-6's own per-outcome figures).
* **Sample unit:** one **settled market**. **n = 261 fills** (±$0.02/share). **The estimand is per share, so n does NOT change with quantity — only the dollars at risk scale.**
* **Falsified by:** a settlement record whose confirmed cash disagrees with the fill ledger, or **any cashflow that cannot be attributed to an identified source** — either of which is an **instrument fault**, reported as such and **not** as economics.
* **NOTHING IS CALLED REALIZED BEFORE SETTLEMENT AND CASH CONFIRMATION.** Per CLAUDE.md, theoretical fills, modelled pair profit, shadow inventory, mark-to-market gains, estimated rebates and unconfirmed fills are **never** realized profit, and `settlement.py::realized_pnl` returns `NOT_REALIZED` with a refusal rather than an estimate.

### The cascade, stated before any total
Estimands 2, 3, 4, 5, 7 and 8 are denominated in **fills**; 1 and 6 in
**placements**. Fills are produced by estimand 1 at the rate estimand 1 exists
to measure, so the cost of everything else is `required_fills / p_fill`
placements and **`p_fill` is unknown**. Every total in §4 is therefore a
**function of `p_fill`** across a grid. **Anyone quoting a single total has
assumed the answer.**

| | gate | denominated in | n |
|---|---|---|---:|
| 1 | Beta width 0.10 | placements | **385** |
| 6 | Beta width 0.10 | placements | **385** (free) |
| 2 | SPRT truncation | fills | 385 |
| 3 | median, ε=0.05 | fills | 385 |
| 7 | Beta width 0.10 | fills | 385 |
| 8 | ±$0.02/share | fills | 261 |
| 4 | p90, ε=0.05 → `139/0.6772` | fills | 206 |
| 5 | p90, ε=0.05 → `139/0.3228` | fills | **431 ← BINDING** |

**ALL GATES** = `max(385, ceil(431 / p_fill))` placements. **No multiplicity
correction is applied across the eight estimands**, because they are eight
separate questions with eight separate reports, not one hypothesis tested eight
ways.

---

## 3. (c) PASS / FAIL / CONTINUE — all three, numerically

Implemented and tested in `cohort_design.py::e1_decision_rules()`.

### PASS — "E2 IS JUSTIFIED"
**It does not mean profitable, and it is not an authorization.** All of:

1. every one of the eight estimands reached its preregistered precision: **385 placements AND 431 fills**;
2. estimand 1's 95% posterior **lower** bound is **above 0.10**;
3. estimand 6 recorded **ZERO** post-cancel-acknowledgement fills, i.e. cancellation **provably** bounds exposure;
4. estimand 8 reconciled every settled market against the fill ledger with **no unattributed cashflow**;
5. estimand 8's per-share expectancy 95% **upper** bound is **above −$0.006231/share** (§3.1).

**Error rates.** Each component is a 95% interval or an `α = 0.05` test. A false
PASS requires every component to mislead **jointly**, so its probability is
below the smallest component's **0.05**; the conservative union bound on **any
single** component misleading is `5 × 0.05 = 0.25` and is **reported rather
than hidden.**

### FAIL — "stop, and here is what we learned"
Any of:

| rule | numeric boundary | error rate | what we learned |
|---|---|---|---|
| estimand 1's 95% posterior **upper** bound on `p_fill` falls below **0.10** | **triggered outright by 35 consecutive no-fill placements** (`Beta(1,36)` 97.5% upper = 0.0947) | 2.5% one-sided | **a passive first leg does not fill at our price under this selector.** That answers the question Stage-6 could not — negatively, and that is a success of the instrument |
| the estimand-2 SPRT crosses H0 **AND** estimand 8's per-share upper bound is below **−$0.006231** | log-LR ≤ **−2.944439**; triggered outright by **8** consecutive non-completions | `β = 0.05` + 2.5% one-sided | completion sits at or below the historical proxy level **and** the per-share loss exceeds the arithmetic ceiling on *any* attainable maker rebate, so **the rebate-bearing cohort cannot close the gap and need not be run** |
| estimand 6 records **any** fill timestamped after the cancel acknowledgement | **n = 1** | none — an observation, not a test | **cancellation does not bound exposure**, so the worst-case arithmetic every frozen bound rests on is invalid. HARD stop; invalidates the design, not just the run |
| estimand 8 reports any cashflow without sufficient source identity | **n = 1** | none — an observation | the settlement ledger cannot be trusted; nothing may be labelled REALIZED. Instrument fault |

### 3.1 Where −$0.006231 comes from (derived, not an owner value)
The maker rebate is `round_half_even(0.0125 × C × p × (1−p), $0.01)`. Per
share, the raw rebate is `0.0125 × p(1−p) ≤ 0.0125/4 = $0.003125` (at p=0.50).
Cent rounding can add at most half a cent to the credited total, worth most per
share at the **smallest** rebate-bearing size, so the **generous** per-share
ceiling over the whole band `p ≤ 0.60` is

```
max over p of  (0.0125*C_min(p)*p*(1-p) + 0.005) / C_min(p)
  attained at p = 0.50, C_min = 1.61  ->  $0.006231 per share
```

So a per-share expectancy whose 95% upper bound is **below −$0.006231** cannot
be rescued by **any** attainable maker rebate. **This is a derived arithmetic
ceiling, not a profit threshold and not an owner value**, and it is asserted by
test.

### CONTINUE
Neither PASS nor FAIL yet. **HARD TRUNCATION: 4,310 placements.** That is not a
new number — it is the placement count of the **cheapest `p_fill` row this
design prices** (`p_fill = 0.10`), so the instrument cannot run beyond
arithmetic the owner has already been shown. **It cannot run forever.**

**On truncation: verdict INCONCLUSIVE, which is NEITHER PASS NOR FAIL.** Report
every posterior and every censored observation as they stand. **E2 remains
unauthorizable.** An INCONCLUSIVE truncation still bounds how far the truth can
be from each hypothesis and is decision-useful.

### ABORT is not a verdict
A cumulative-loss abort is a **FAILURE TO MEASURE**, reported as such with the
partial posteriors, and **never reported as FAIL or as a result**. The
cumulative bound itself is an **OWNER VALUE and is deliberately not set here**
— see §5.

---

## 4. (d) WORST-CASE DOLLARS FOR THE WHOLE E1 COHORT

Worst case = `placements × qty × $0.60`: every placement filling in full at the
frozen ceiling and every complement never filling, the residual settling to
zero. **This is a BOUND, not a forecast** — it is `1/p_complete` times more
pessimistic than any plausible path, and that is what a bound is for.

### 4.1 At the derived minimum, 0.13 shares

| | placements | **worst case** |
|---|---:|---:|
| estimand 1 + estimand 6 alone (no `p_fill` cascade) | **385** | **$30.03** |
| ALL gates, `p_fill = 0.10` | 4,310 | **$336.18** |
| ALL gates, `p_fill = 0.25` | 1,724 | **$134.47** |
| ALL gates, `p_fill = 0.50` | 862 | **$67.24** |
| ALL gates, `p_fill = 0.87` *(Stage-6 proxy — cheapest and least credible row)* | 496 | **$38.69** |

### 4.2 The four reference sizes

The fourth reference size is **NOT 1.62**. The rebate floor is
**price-dependent** and the cheapest-price end of the first-leg band dominates
it: being rebate-bearing at **every** price in `[0.01, 0.60]` requires **40.41
contracts** (§P2 (a) in `E2_COHORT_DESIGN.md`, and the full family of sub-bands
is tabulated there). That size is carried here **because it is the honest
answer**, and what it shows is that **rebate-bearing across the full placeable
band is not a feasible E1 size at all.**

| qty | fill states | per placement | **E1+E6 only (385)** | **ALL, p=0.10** | **ALL, p=0.25** | **ALL, p=0.50** | **ALL, p=0.87** |
|---|---:|---:|---:|---:|---:|---:|---:|
| **0.01** (venue min) | 1 | $0.0060 | **$2.31** | $25.86 | $10.34 | $5.17 | $2.98 |
| **0.10** | 10 | $0.0600 | **$23.10** | $258.60 | $103.44 | $51.72 | $29.76 |
| **0.13** *(recommended)* | 13 | $0.0780 | **$30.03** | $336.18 | $134.47 | $67.24 | $38.69 |
| **1.00** (frozen S6P) | 100 | $0.6000 | **$231.00** | $2,586.00 | $1,034.40 | $517.20 | $297.60 |
| **40.41** (rebate-bearing across `p ≤ 0.60`) | 4,041 | $24.2460 | **$9,334.71** | $104,500.26 | $41,800.10 | $20,900.05 | $12,026.02 |

Arithmetic check by hand, asserted by test: `p_fill = 0.50`, qty 0.13 →
`ceil(431/0.5) = 862` placements, `862 × 0.13 × $0.60 = $67.236 → $67.24`. ✓

**The spread is the headline.** The worst-case cost of reaching all gates ranges
over a factor of **~8.7 purely because `p_fill` is unknown**, and over a factor
of **4,041 across the sizes**. That is exactly why **estimand 1 is cheap and has
no cascade**, and why it should be read as the gate that **prices the rest of
the experiment**: 385 placements at 0.13 shares costs **$30.03** worst case and
converts every row below it from a range into a number.

---

## 5. WHAT E1 CANNOT ESTABLISH

1. **It cannot establish that the S6P protocol is profitable, and it is not designed to.** PURPOSE IS MECHANICS. All eight gates cleared favourably is **evidence for an authorization decision, not an authorization.**
2. **It does not supersede Stage-6's INCONCLUSIVE / DO-NOT-AUTHORIZE verdict**, which stands exactly as written, bound to its three module sha256s, and still requires re-binding before it may be cited for authorization at all.
3. **It cannot measure maker rebate economics. At all.** At 0.13 shares — and at every size below the price-dependent floor of 1.61–40.41 contracts — every rebate observation is **exactly $0.00** by cent-floor arithmetic. Not "measured poorly": there is no signal to extrapolate. **That is cohort E2's entire reason to exist.**
4. **It measures `P(any fill)`, not `P(FULL fill)`.** A sweep must consume `Q_ahead + our_size`; at 0.13 shares `our_size` is small against realistic `Q_ahead`, so the two coincide and E1 **silently reports the former as the latter unless the distinction is carried in the report.** `Q_ahead` is logged so a `P(fill | Q_ahead)` curve can be *evaluated* at a larger order's `Q_ahead + Δ` — **that is an extrapolation under a model, never a measurement**, and it assumes fills consume a level contiguously and that our presence does not alter arriving flow.
5. **It cannot measure own-size market effect.** 0.13 shares does not move the displayed size other participants react to; a rebate-bearing size might. **Neither size can measure this about the other.**
6. **It cannot retroactively fix the archive.** The 95.58% of *historical* states remain unknowable forever. Owning orders resolves the question **going forward only**, and says nothing about `REGIME_PRE_2026-04-25`, `REGIME_2026-04-25` or `REGIME_2026-06-23`, which have **zero** evaluable states.
7. **It cannot establish a counterfactual at the same instant.** With `MAX_OUTSTANDING_ORDERS = 1` there is one order at one price at one time. *"Would a different price, size, or one tick more aggression have filled?"* is **not answerable**, and answering it requires simultaneous orders the frozen bounds forbid.
8. **It cannot distinguish cancellation from trade for anyone else's order.** It resolves **our own** fills with certainty and resolves nothing about the rest of the book.
9. **`p_fill` is conditional on the discovery selector, not on markets at large.** Markets are chosen by the PMUS selector, not randomised, so the estimate is `P(fill | this selector, this period)`, and the cohort **cannot detect a selector change from its own output.**
10. **It cannot establish L5 behavioural fidelity.** CLAUDE.md's validation standard — *at this exact market state, what did L5 do next, and what would our clone have done?* — is **not met and not claimed.** E1 measures *our* orders, not agreement with L5's.
11. **It cannot establish capacity, scaling or concurrency.** One size, one market, one outstanding order.
12. **It cannot establish the upper tail of residual duration under heavy censoring.** At Stage-6's 32.28% right-censoring, quantiles above the censoring point are **not identified**; censored observations are reported, never imputed.
13. **It cannot distinguish "the measurement failed" from "the answer is bad" if it aborts on a cumulative loss bound.**
14. **It cannot run under the bounds as currently frozen.** `MAX_FIRST_LEGS = 1`, `MAX_DAILY_REALIZED_LOSS_USD = $0.60` and "no automatic repeat" are written for a **single** probe; a sequential cohort is **many** probes, and Stage-6 measured a **$13.22** maximum drawdown over a 727-probe serial path against that $0.60 per-session bound. **Reconciling that is an owner decision and a precondition, and this document deliberately does not make it.**

---

## 6. Preconditions the owner must supply before anything is armed

Listed so the gap is explicit, **not** as a request and **not** as a checklist
this lane may complete on its own:

1. **authorization, in the owner's own words**;
2. a **quantity** — §1.5 recommends 0.13 shares and prices five alternatives; the owner chooses;
3. **precision targets** — one row per estimand, fixed **before** the first placement;
4. a **cumulative loss bound**, which does not currently exist (§3, §5.13);
5. reconciliation of `MAX_FIRST_LEGS = 1` and "no automatic repeat" with a multi-probe cohort (§5.14);
6. re-binding of Stage-6's three module sha256s, which that document already requires independently of this one;
7. a ruling on §1.4 — whether end-to-end settlement confirmation of the *matched* leg is in E1's scope, since that is what makes 0.13 rather than 0.02 binding.

Until all seven exist, **this remains a design and nothing may be armed.**

---

## 7. Provenance

| artifact | path |
|---|---|
| this design | `analysis/l5/measurement/E1_COHORT_DESIGN.md` |
| E2, void unless E1 PASSes | `analysis/l5/measurement/E2_COHORT_DESIGN.md` |
| calculator (offline, stdlib only) | `analysis/l5/measurement/cohort_design.py` |
| tests, 127 passing | `analysis/l5/measurement/test_cohort_design.py` |
| the stopping-gate work this builds on | `analysis/l5/measurement/SEQUENTIAL_MEASUREMENT_PROTOCOL.md`, `sample_size.py`, 28 tests |
| the problem being solved | `analysis/l5/stage6_validation/STAGE6_HISTORICAL_VALIDATION.md` |
| frozen bounds, read-only, unmodified | `analysis/l5/stage6/bounds.py` |
| frozen S6P `32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8`, read-only, **not tuned** | `analysis/l5/stage6/spec.py` |
| cancel + post-cancel sweep this cohort consumes, unmodified | `analysis/l5/stage6/machine.py` |
| settlement lifecycle, unmodified | `analysis/l5/stage6/settlement.py` |
| fee schedule `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`, taker θ 0.0695 eff. 2026-09-17 | `src/pmus_fee_schedule.py` |
| behavioural source of truth | `L5_OPERATING_MODEL.md`, GitHub issue #167 |

## 8. Anti-drift and no-fitting declaration

* **No parameter was chosen to improve any outcome, and the one judgement that looks economic is flagged in §1.4 rather than buried.** The quantity comes from cent rounding against a frozen ceiling; the SPRT points are Stage-6's own observed and derived numbers; the precision targets are the prior protocol's grid reused unchanged; the FAIL boundary 0.10 is the cheapest `p_fill` row already priced; the −$0.006231 foreclosure boundary is an arithmetic ceiling on the published rebate formula; the truncation 4,310 is that same priced row's placement count.
* **Nothing frozen was modified, relaxed, re-fitted or re-derived.** `analysis/l5/stage6/` was read only. S6P was **not tuned**. `FIRST_LEG_QTY` remains **1.00** in the frozen spec and §1.5's recommendation explicitly does not override it. The XR2W executor, its spec and fingerprint, A6 (FILLED / settlement unresolved / **NOT REALIZED**), S3 and the running `l5-book-shadow` collector were not touched. No evidence was deleted or overwritten.
* **The historical 1.000 residual-loss rate was NOT inherited** as a prior, a hypothesis point or a boundary. Estimand 7 measures adverse selection directly. That is the single most important methodological difference between this cohort and the historical lane.
* **How this serves the L5 program** (CLAUDE.md anti-drift rule): the gating constraint on the whole pipeline is **evidence about queue position and fill mechanics**, and Stage-6 §9 identifies owning the orders as the only route to it. E1 is that route, minimally sized for observability. It does **not** redefine the strategy as arbitrage, market making or rebate farming — §5.3 says plainly that rebate economics is precisely what E1 **cannot** measure, and CLAUDE.md's rule that mature incentive-era behaviour stays locked until equivalent economics are source-confirmed for our account is why E2 is a **separate, gated** cohort rather than part of this one.
