# Settlement-lock diagnostic — can the final-minute BRTI state manage the residual?

Diagnosis only. Nothing swept, nothing fitted, no controller built, no live order.
Entry and completion mechanics **imported unchanged** from
`pmus_path_economics.phase2(first_side="UP")`. All bands fixed in the module before
any result was read. Run `37245150350`, artifact `path-economics-37245150350`.

---

## Part 0 — the algebra and the settlement semantics, verified not assumed

`R_required = (60·K − S_fixed)/(60 − n)` reproduces from the state table's
`required_remaining_avg` with **max absolute error 0.0** — exact. `z` reproduces to
**3.7 × 10⁻⁴**, which is the stored rounding of `sigma_remaining_avg`, not an
algebra discrepancy. (Figures as inspected directly on the 28-window development
table; the same Part 0 ran on the OOS table and its output is in the artifact.)

The venue statistic was reconstructed independently from the per-second closes over
`[T−60, T−1]`, simple-averaged, rounded **half-even to the cent**, and tested
against the venue's own outcomes under the convention **UP iff stat > K**:
**28/28 windows agree (100%), with 0 exact ties.** Averaging interval, rounding mode
and tie convention are therefore confirmed, not presumed.

`n_published` differs from the pure clock formula on some rows. That is **missing
index buckets**, not an error: the state table counts buckets actually present,
`settlement_minute_published` is arithmetic on the clock.

**External claims, tested.** Uncertainty of the final statistic, in units of σ₁ₛ,
derived from the repository's own `sigma_remaining_avg`:

| remaining | sd (σ units) | ratio to t−60 |
|---|---|---|
| 60 s | 4.5280 | 1/1.00 |
| 45 s | 2.9531 | 1/1.53 |
| **30 s** | 1.6206 | **1/2.79** |
| **15 s** | 0.5869 | **1/7.72** |
| 5 s | 0.1236 | 1/36.6 |

The "1/3 at 30 s" and "1/7 at 15 s" claims **reproduce exactly** from our own
established formula. They are a restatement of algebra already in the repository,
not new information. τ³ and the 60× activity claim are used nowhere.

---

## Part A — does PMUS pricing incorporate the already-fixed observations?

Final minute only (n ≥ 1 observation fixed), 10,502 rows. "Disfavoured" = the side
the hurdle sign is against.

| \|z\| | rows | disfav px | disfav realized win | px − realized | fav px | fav realized win | takeable P&L (buy fav, hold) | % profitable |
|---|---|---|---|---|---|---|---|---|
| [0, 0.5) | 249 | 0.36 | 0.3374 | +0.0347 | 0.65 | 0.6627 | +0.0043 | 66.3 |
| [0.5, 1) | 290 | 0.22 | 0.1035 | **+0.1231** | 0.79 | 0.8966 | **+0.0998** | 89.7 |
| [1, 2) | 664 | 0.08 | 0.0723 | +0.0314 | 0.94 | 0.9277 | +0.0137 | 92.8 |
| [2, 3) | 523 | 0.03 | 0.0268 | +0.0243 | 0.98 | 0.9732 | +0.0131 | 97.3 |
| [3, 5) | 861 | 0.01 | 0.0070 | +0.0152 | 0.99 | 0.9930 | +0.0095 | 99.3 |
| [5, ∞) | 7,915 | 0.01 | **0.0000** | +0.0106 | 0.99 | **1.0000** | +0.0102 | 100.0 |

**Answer: the market substantially does incorporate them.** By |z| ≥ 3 the
disfavoured side is quoted at the **minimum tick ($0.01)** and wins 0.7% / 0.0% of
the time. The residual `px − realized` of +0.011 to +0.015 **is essentially one
tick** — the market cannot price it any closer, because $0.01 is the floor. That is
not a mispricing; it is the tick.

The one band where price and outcome genuinely diverge is **|z| ∈ [0.5, 1)**: the
favoured side quoted 0.79 but won 89.7%, a takeable +0.0998/unit. Three reasons
not to get excited:

1. **These are per-second rows, not independent observations.** 7,915 rows at
   |z| ≥ 5 come from at most 178 windows; within a window consecutive seconds are
   almost perfectly autocorrelated. The effective sample is **windows, ~10²**, not
   rows, ~10⁴. Every band's precision is overstated by roughly the number of
   seconds per window.
2. It is a **new entry mechanism**, not residual management — out of scope here.
3. At |z| ≥ 5 the "+0.0102" is buying at $0.99 to collect $1.00: a 1% return that
   ties up 99% of the capital. Inside the $200 ceiling that is ~$2/window gross
   before any adverse selection, and it is the most crowded state in the sample.

---

## Part B — residual P&L against the causally known hurdle

The residual book of the fixed study: **−54.2925** across **180 lots** (locked
+22.6806, net −31.6119). Split by whether the hurdle sign was already **against**
the lot:

| checkpoint | adverse lots | adverse total | adverse mean | adverse % losing | adverse lot win % | favourable lots | favourable total |
|---|---|---|---|---|---|---|---|
| n=15 (**45 s** left) | 84 | **−54.26** | −0.6460 | 97.6 | **2.4** | 96 | −0.03 |
| n=30 (**30 s** left) | 83 | **−54.33** | −0.6546 | 98.8 | **1.2** | 97 | +0.04 |
| n=45 (**15 s** left) | 84 | **−55.25** | −0.6577 | 98.8 | **1.2** | 96 | +0.96 |

**Answer: yes, completely, and earlier than expected.** The residual losses are not
merely *concentrated* in adverse-hurdle states — they are **entirely** there. At
every checkpoint the adverse subset carries **100%+ of the −54.29**, while the
favourable subset is flat to marginally positive. Adverse lots win **1.2–2.4%** of
the time: recovery does not merely weaken, it collapses.

And the split is already decisive **45 seconds before settlement** (84 lots,
−54.26), not only in the last few seconds.

---

## Part C — locked_frac versus z

| discriminator | separation range of mean lot P&L |
|---|---|
| `locked_frac` | **0.0** |
| `\|z\|` | **0.6935** |

`locked_frac = n_fixed/60` **by definition**, so at a fixed checkpoint it is a
constant — the whole 180-lot sample falls in one bucket, `[0.4, 0.6)`. It encodes
only *how much* of the minute is fixed and can never encode *what* was fixed or
therefore what the remaining observations must achieve.

**But the premise needs correcting, and this is the important part.** The
information was **not** discarded by the rejected model's feature set. `z` *is* the
normalized remaining-average hurdle, computed exactly as
`gap_to_required / sigma_remaining_avg`, and `pmus_state_table.FEATURES = ("z",
"locked_frac", "mom_60s")` — **z was one of its three frozen features.** The
rejected model had the settlement hurdle all along. What it did with it was use it
to *select entries*, which failed (rank correlation −0.084). It never used it to
*manage residual*.

One further nuance: the discriminating quantity is **sign(z) against the lot's
side**, not |z| magnitude. The |z| table is non-monotone (−0.947 at [0, 0.5),
−0.253 at [5, ∞)) because |z| alone does not say *which* side is doomed. The clean
separation — −0.655 adverse versus +0.0004 favourable — is driven by the **sign**.

---

## The question asked

> Does the final-minute settlement structure provide a defensible causal mechanism
> from which we could preregister ONE residual-management rule without selecting
> that rule for P&L on these 178 windows?

### No.

The mechanism is **causal, parameter-free and correct** — and it arrives **too
late to be worth acting on**. Parts A and B together close it:

- Part B: most adverse residual lots sit at **|z| ≥ 5** by the final minute
  (47 of 84 at the 45 s checkpoint, 71 of 84 at 15 s).
- Part A: at |z| ≥ 5 the disfavoured side is quoted at **$0.01**.

So by the time the hurdle is decisive, **the market has already marked the adverse
lot to approximately zero.** Exiting then does not avert the loss; the loss has
already happened. Combining Part B's adverse lot counts with Part A's
contemporaneous disfavoured-side prices, exiting **every** adverse lot at the 45 s
checkpoint recovers:

| \|z\| | adverse lots | disfav px | net of taker fee | recovered |
|---|---|---|---|---|
| [0, 0.5) | 3 | 0.36 | 0.34 | 1.02 |
| [0.5, 1) | 6 | 0.22 | 0.21 | 1.26 |
| [1, 2) | 13 | 0.08 | 0.07 | 0.91 |
| [2, 3) | 11 | 0.03 | 0.03 | 0.33 |
| [3, 5) | 4 | 0.01 | 0.01 | 0.04 |
| [5, ∞) | 47 | 0.01 | 0.01 | 0.47 |
| | | | **total** | **+4.03** |

**+$4.03 against a −$54.29 residual** — **7.4%** of the loss, and **6.3%** of the
**+$64.11** swing that $5/window inside the $200 ceiling requires. And that is a
*ceiling*: it assumes a perfect exit of every adverse lot at the median displayed
price with no slippage, no partial fills, and perfect hurdle-sign classification.

This is an order-of-magnitude bound built by joining two measured tables, not a
simulated result — but it is not close enough for the gap to matter. An exit rule
on this signal cannot work, and no choice of threshold changes that, because the
binding constraint is the **price at which the exit happens**, not when it triggers.

**So I am not going to manufacture another controller.** The residual loss is
created at **entry** — buying a first leg that later becomes adverse — not at exit.
The only lever left is avoiding or shrinking those first legs, which requires
predicting the eventual settlement side early in the window. That is precisely the
directional problem that was falsified on this tape with this exact `z` feature.

### What would change the answer

Not a parameter, but a different quantity. An exit is only worth making while the
adverse side still carries real value, i.e. while **|z| is small and the
disfavoured side is still quoted meaningfully above the tick** — the [0, 2) bands,
which hold just **22 of 84** adverse lots and ~$3.2 of the ~$4.0 recoverable. A rule
restricted there is both tiny and the least statistically reliable part of the
sample. For it to be worth preregistering you would need:

- **fresh windows**, not these 178 — enough that per-*window* counts in the |z| < 2
  adverse cell reach the low hundreds, which at ~20 such lots per 178 windows means
  roughly **1,500–2,000 additional windows** (~2–3 weeks of continuous recording);
- a **measured exit path**, which does not exist today: the executor's `action` is
  hardcoded `ORDER_ACTION_BUY` and the frozen controller has no SELL, so the price
  actually obtainable when selling a losing lot is **unmeasured**;
- the exit trigger **declared before** looking at the new data, and evaluated once.

Until a sell path exists and has been measured, any exit-rule result would be
priced on quotes we have never traded against.

## Limits

- Part A's row counts overstate precision: per-second rows inside a window are
  heavily autocorrelated, so effective n is windows (≤178), not rows.
- Residual lots inherit the path study's arbitrary entry ("first eligible second").
- Depth is touch-only; no ladder.
- The +4.03 bound joins two measured tables rather than simulating exits.
- One venue regime, 46 contiguous hours.
