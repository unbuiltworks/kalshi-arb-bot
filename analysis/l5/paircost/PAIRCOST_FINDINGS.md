# Running pair-cost control — CONFIRMATION RESULT

Executed against frozen protocol **118571f3**, spec fingerprint
`e90895545a94999b9f9ac5ff472e92efb1042f0be00bf2d42d0eb738f5523b10`
(verified by `assert_frozen()` at the start of both runners). Every
threshold was **read from the frozen spec**, not restated in the analysis
code, so no value could drift between protocol and run. Nothing was tuned
after seeing results. S3 untouched. No live orders.

---

# VERDICT: **REFUTED**

The frozen rule: *REFUTED if the band is not maintained, **or** NULL_M
reproduces the trajectory as well as L5 does.* **Both limbs are satisfied.**

> **Is ~0.98 genuinely a maintained L5 state variable, or is its stability a
> mathematical consequence of acquiring both outcomes in these markets?**
>
> **It is a mathematical consequence — and the evidence is stronger than
> that.** Under the protocol's own fairness requirement, L5's running
> matched pair cost is **7× noisier than the cumulative marginal pair cost
> the market offers, and 19× noisier than NULL_M's cumulative pair cost.**
> 0.98 is not a controlled quantity; it is a *degraded* version of a number
> the books hand out for free.

---

## 1. The decisive tests

| test | frozen bar | result | |
| --- | --- | --- | --- |
| **SELECTION_EDGE, maker view** | ≥0.005 in ≥9/12 | **6/12** | **FAIL** |
| **SELECTION_EDGE, taker view** | ≥0.005 in ≥9/12 | **9/12** | pass |
| **SELECTION_EDGE overall** | spec requires **both views** | maker fails | **FAIL** |
| **CF0 / NULL_M** | reproduces both? | accumulation **1.012×**, cost L1 **0.0268** | **reproduces accumulation** |
| Budget / headroom | rho ≥0.40 in ≥9/12 | **7/12** (median rho 0.405) | **FAIL** |

The spec sets `maker_and_taker_views_both_required: True`. The maker view is
also the only apples-to-apples comparison for a trader who is **83 % maker
by fill count** (2,617 of 2,638 single-leg transactions). It fails at 6/12.

Both edge results are unchanged with atomic par pairs excluded (maker 6/12,
taker 9/12).

### SELECTION_EDGE, all 12 windows, both views

| window | maker edge | taker edge | tw bid-sum | tw ask-sum | L5 realised |
| --- | --- | --- | --- | --- | --- |
| …274000 | −0.0004 | +0.0162 | 0.9921 | 1.0087 | 0.9925 |
| …290200 | **+0.0307** | +0.0306 | 1.0011 | 1.0010 | 0.9704 |
| …296500 | −0.0106 | +0.0093 | 0.9901 | 1.0100 | 1.0007 |
| …300100 | −0.0007 | +0.0184 | 0.9904 | 1.0095 | 0.9911 |
| …305500 | **+0.0157** | +0.0292 | 0.9934 | 1.0069 | 0.9777 |
| …306400 | **+0.0308** | +0.0442 | 0.9934 | 1.0067 | 0.9626 |
| …307300 | −0.0084¹ | −0.0084 | 0.9924 | 0.9804¹ | 0.9888 |
| …311800 | **+0.0192** | −0.0363¹ | 0.9911 | 0.9355¹ | 0.9718 |
| …362200 | **+0.0519** | +0.0719 | 0.9901 | 1.0100 | 0.9381 |
| …363100 | −0.0077 | +0.0125 | 0.9899 | 1.0101 | 0.9976 |
| …365800 | **+0.0230** | +0.0454 | 0.9888 | 1.0112 | 0.9658 |
| …369400 | −0.0298 | −0.0092 | 0.9897 | 1.0103 | 1.0195 |

Four windows have a **negative** maker edge — L5 paid *more* than the
time-weighted resting-pair cost. ¹ flags windows affected by the archive
defect in §5.

## 2. Band maintenance — F1–F4, all 12 windows

Primary convention C3_VWAP_POOLED, band 0.96–1.00.

| window | shares to entry | secs to entry | **frac. inventory inside** | frac. time inside | max excursion | excursions | returned |
| --- | --- | --- | --- | --- | --- | --- | --- |
| …274000 | 74 | 0 | **0.212** | 0.278 | 0.1145 | 4 | 4 |
| …290200 | 75 | 0 | 0.755 | 0.746 | 0.0460 | 10 | 10 |
| …296500 | — | — | **NEVER ENTERS** | — | **0.1686** | — | — |
| …300100 | 60 | 0 | 0.689 | 0.652 | 0.0538 | 11 | 11 |
| …305500 | 83 | 0 | 0.781 | 0.702 | 0.0439 | 7 | 7 |
| …306400 | 1,494 | 564 | 0.407 | 0.658 | 0.1084 | 3 | 3 |
| …307300 | 122 | 50 | 0.402 | 0.338 | 0.0976 | 7 | 7 |
| …311800 | 1,370 | 128 | 0.735 | 0.861 | 0.0300 | 4 | 4 |
| …362200 | 302 | 75 | 0.760 | 0.928 | 0.0469 | 9 | 8 |
| …363100 | 19 | 0 | 0.511 | 0.586 | 0.0912 | 7 | 7 |
| …365800 | 25 | 0 | 0.331 | 0.203 | 0.1001 | 9 | 9 |
| …369400 | 80 | 0 | 0.503 | 0.355 | 0.0587 | 7 | 6 |

**The band is not maintained.** Median residency is **51 %** of matched
inventory — barely better than a coin flip. One window **never enters the
band at all**. Excursions reach **0.17** from target, and windows
experience 3–11 separate excursions. A quantity that sits inside its
putative control band half the time is not being controlled.

F4 is the one mildly supportive result: excursions do almost always return
(93 of 95). But a series that wanders out and back seven times per window
is a random walk around a level, not a maintained band.

## 3. F6 / F7 — the fairness requirement is what kills it

The protocol pre-barred the tautology: a cumulative average is mechanically
smoother than its own increments, so **every** quantity goes through the
**same** running-mean transform, and NULL_M's cumulative cost is included.

| series (all cumulative, same transform) | median dispersion | |
| --- | --- | --- |
| **L5 running matched pair cost** | **0.0242** | the hypothesis |
| cumulative **marginal** pair cost (market-available) | **0.0035** | **7× more stable than L5** |
| cumulative **NULL_M** pair cost | **0.0013** | **19× more stable than L5** |
| *raw marginal pair cost (tautology guard)* | *0.0298* | *vs L5's 0.0242 — barely different* |
| cumulative matched fraction | 0.1013 | L5's cost is more stable |
| cumulative share allocation w_up | 0.1120 | L5's cost is more stable |
| cumulative price | 0.0392 | L5's cost is more stable |

Two things follow, and the first is fatal:

1. **F6 fails in the opposite direction to the hypothesis.** Compared
   fairly, L5's running pair cost is *markedly less* stable than the
   complement sum available in the market, and than the no-control null's
   own cumulative cost. The market supplies a tighter 0.98 than L5 achieves.
2. The tautology guard shows why this looked impressive before: raw
   marginal dispersion is 0.0298 against L5's cumulative 0.0242 — a ratio
   of 1.2, not the order of magnitude the earlier framing implied. **Almost
   all of the apparent "stability" was the averaging**, exactly as the
   protocol anticipated.

F7 passes directionally (pair cost is more stable than allocation
quantities), but that is uninformative: pair cost is pinned near 1 by the
complementarity of the two books, while allocation is free to range over
[0, 1]. Bounded quantities vary less. This is not evidence of control.

**F5 survives:** LOWO-fitted targets sit within **0.0088** of the frozen
0.98 (median 0.0056). The *level* 0.98 is real and reproducible. Its
**maintenance** is what fails.

## 4. Counterfactuals — and the most damaging single result

| controller | accumulation vs L5 | cost-trajectory L1 | final cost |
| --- | --- | --- | --- |
| **L5 (actual)** | 1.000 | 0.0000 | **0.9833** |
| **CF5 random permutation** (prices/sizes preserved) | 0.972 | **0.0140** | 0.9645 |
| **CF6 running-average controller** *(the hypothesis)* | 0.908 | 0.0190 | 0.9617 |
| CF1 always lighter side | 1.040 | 0.0253 | 0.9962 |
| **CF0 NULL_M** touch-taker, no control | **1.012** | 0.0268 | 1.0040 |
| CF3 fixed per-action reservation | 0.323 | 0.0538 | 1.0320 |
| CF2 fixed per-pair ceiling | 0.395 | 0.1806 | 0.6772 |

**Randomly permuting L5's own side choices reproduces his cumulative
pair-cost trajectory *better* (L1 0.0140) than the running-average
controller the hypothesis posits (0.0190).** If the *ordering* of his
actions carried cost-control information, destroying that ordering would
degrade the fit. It improves it. The trajectory is determined by his prices
and sizes, not by any sequencing decision.

**CF0 reproduces accumulation** (1.012×) while landing 0.021 higher on
cost. That gap is the maker-versus-taker execution difference, not control
— and the maker-view edge test, which isolates exactly that, fails at 6/12.

CF2 and CF3 fail the "must reproduce both" requirement badly (accumulation
0.40× and 0.32×): a hard ceiling or reservation would have prevented most
of L5's actual volume. He was **not** operating under either.

## 5. A data defect found during the run, disclosed

The archive contains **crossed books** — `best_ask < best_bid` on a token
within a single second (e.g. bid 0.760 / ask 0.560), consistent with the
event-driven capture dropping ask-side updates while the bid moved past.

| crossed token-seconds | windows |
| --- | --- |
| 0 % | 2 |
| 0.4–11.4 % | 8 |
| **28.9 % / 32.7 %** | 2 (…311800, …307300) |

**10 of 12 windows are affected.** This corrupts the **taker** view, which
depends on asks; the maker view uses bids only and is unaffected.

I did **not** add a crossed-book filter — that would be a post-freeze
amendment. Instead: the defect biases ask sums *downward*, which biases the
taker edge *downward*, so it **cannot** have manufactured the taker-view
pass. Its effect is to make the one passing view unreliable, not to create
it. The verdict rests on the maker view and on F6/CF5, none of which use
asks.

*Implementation correctness fix during the run (not a protocol change):* my
first pass averaged the two views over **different** second-sets, producing
ask-sum < bid-sum in two windows — arithmetically impossible. I enforced
common support (a second counts only when all four quotes exist). No bar,
target, convention, exclusion or counterfactual was altered; the decision
counts were identical before and after (maker 6/12, taker 9/12).

## 6. Robustness — the conclusion does not depend on the choices

**Attribution convention** (fraction of matched inventory inside band):

| convention | n | median | min |
| --- | --- | --- | --- |
| **C3_VWAP_POOLED (primary)** | 11 | **0.511** | 0.212 |
| C1_FIFO | 10 | 0.591 | 0.028 |
| C2_LIFO | 12 | 0.566 | 0.056 |

**The conclusion is convention-robust.** All three put residency in the
50–60 % range; none approaches a maintained band. C1_FIFO's minimum of
0.028 is *worse* than the primary.

**Atomic par pairs** (9.72 % of BTC15m volume): residency median
**0.511 included → 0.489 excluded**. The result is not produced by par
inventory mechanically dragging the average toward 1.00 — excluding par
pairs makes it marginally *worse*.

**Fees**, kept on their own line and never folded into pair cost: modelled
`0.07·p·(1−p)` gives a median **\$62.30 per window on \$3,070 gross
acquisition = 2.03 % of gross.** For scale, that is roughly the entire
maker-view edge in the windows where one exists — but per protocol it is
reported separately and no economic conclusion is drawn from it here.

## 7. What this means, stated plainly

The ~0.98 running matched pair cost is:

- **real** — F5 reproduces the level to within 0.009 under LOWO;
- **visually striking** — and I said in the preregistration that I expected
  it to be descriptive, which is why that expectation was recorded in
  advance;
- **not maintained** — 51 % median band residency, one window never
  entering, excursions to 0.17;
- **not controlled** — it is 7× noisier than the market's own cumulative
  complement sum and 19× noisier than the no-control null;
- **not sequenced** — shuffling L5's side choices fits his cost trajectory
  *better* than a running-average controller does.

The level comes from the arithmetic the protocol named as NULL_M: two
complementary books summing to ≈0.98 (the §5 geometry statistic already
measured mean UP + mean DOWN price per window at 0.977), bought on both
sides. L5's residual advantage over a touch-taker is maker execution, and
the test built to isolate that — the maker-view SELECTION_EDGE — **fails at
6/12 under the frozen bar.**

**Per the frozen protocol I am not identifying a mechanism**, because that
step was conditional on the selection edge passing against the actual
opportunity set and CF0 failing. Neither holds.

## 8. Standing constraints

S3 unchanged and verified frozen at `8b20a259…87df`. No implementation
decision is taken from this study. No threshold, target, window exclusion,
attribution convention or counterfactual was altered after results were
seen. No live orders.
