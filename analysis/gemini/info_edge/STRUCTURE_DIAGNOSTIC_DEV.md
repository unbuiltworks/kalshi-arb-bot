# Is there predictive structure beyond sign(distance)? — DEV diagnostic, n=48

Frozen 48-window DEV cohort only. Prospective windows untouched. No rule
fitted or frozen. No live orders. Economics in **per-contract** terms; the $5
sizing normalizer is dropped for discovery as instructed.

# ANSWER

**Q1 — YES, there is structure beyond sign. Q2 — NO, it is not purchasable.**

Which lands exactly on the branch you named: *"#1 succeeds but #2 fails →
Gemini is incorporating the information efficiently or liquidity disappears."*
**Both are true here, and the mechanism is now measured.**

---

## Q1 — structure beyond sign: YES, but it is ONE factor, not a rich one

AUC for predicting settlement (0.5 = no information), n = 48 windows per cell:

| variable | T−120 | T−90 | T−60 | T−45 | T−30 | T−15 | T−5 |
|---|---|---|---|---|---|---|---|
| **dist** | **0.837** | **0.936** | 0.988 | 0.972 | 0.991 | **0.997** | 0.993 |
| **dist_norm** | **0.839** | 0.932 | 0.958 | 0.951 | 0.988 | 0.993 | 0.990 |
| **frac_above** | 0.776 | 0.812 | 0.841 | 0.867 | 0.879 | 0.901 | 0.917 |
| **max_exc_signed** | 0.788 | 0.840 | 0.845 | 0.861 | 0.910 | 0.915 | 0.927 |
| ret_60s | 0.541 | 0.753 | 0.757 | 0.733 | 0.686 | 0.655 | 0.602 |
| ret_30s | 0.623 | 0.690 | 0.629 | 0.596 | 0.599 | 0.560 | 0.491 |
| ret_15s | 0.601 | 0.766 | 0.654 | 0.576 | 0.580 | 0.507 | 0.523 |
| ret_5s | 0.490 | 0.542 | 0.698 | 0.431 | 0.591 | 0.455 | 0.556 |
| **accel** | 0.489 | 0.422 | 0.560 | 0.402 | 0.464 | 0.580 | 0.567 |

### Multiplicity calibrated, not ignored

99 cells on 48 windows will throw up strong-looking cells by chance, so the
whole grid was recomputed under **2,000 permutations of the settlement
labels**:

| | |
|---|---|
| best observed \|AUC−0.5\| | **0.497** |
| null max \|AUC−0.5\| | p50 0.197, **p95 0.269**, p99 0.300 |
| p-value of the best cell | **< 0.001** |

**The position variables clear the noise bar decisively.**

### Three honest qualifications

1. **It is essentially ONE factor.** `dist`, `dist_norm`, `frac_above` and
   `max_exc_signed` all measure the same thing — where BTC sits relative to
   the strike — in different clothes. They are not four independent signals.
2. **The richer path variables you hoped for do NOT clear the bar.** Momentum,
   velocity and acceleration are at or near noise: `ret_5s` ≈ 0.50, `accel`
   ≈ 0.40–0.58, and the best momentum cell (`ret_60s` 0.757 at T−60) gives
   \|AUC−0.5\| = 0.257, **below the 0.269 null threshold**. The
   "+$2 after falling $40" vs "+$35 after rising $25" distinction is **not
   supported by this cohort** — path adds nothing beyond position.
3. **`crossings_signed` and `reversal` are sign-contaminated by my own
   construction** (I signed them against `sign(dist)`, so they are partly
   proxies for −sign(dist)). Their apparent strength is an artifact; I am not
   claiming them as findings.

**Also note much of the AUC is mechanical.** 0.99 at T−15 mostly says "BTC far
from strike with 15 s left tends to settle that side" — physics, not insight.
The genuinely informative number is **T−120 AUC 0.837**, where real
uncertainty remains.

---

## Q2 — where the state is informative, is the side purchasable below that probability?

In-sample conditional probability (deliberately **generous** — the same 48
windows), executable price of the predicted side, per contract:

| TTE | \|dist\| | n | P(correct) | avail % | price p50 | **P − price** |
|---|---|---|---|---|---|---|
| 120 | 5–15 | 14 | 0.786 | 78.6% | 0.840 | −0.054 |
| 120 | 15–30 | 18 | 0.833 | 27.8% | 0.980 | −0.147 |
| 120 | 30–60 | 5 | 0.800 | **20.0%** | 0.640 | +0.160 |
| 90 | 15–30 | 15 | 0.933 | 26.7% | 0.900 | +0.033 |
| 90 | 30–60 | 12 | **1.000** | **0.0%** | — | — |
| 60 | 15–30 | 19 | **1.000** | **0.0%** | — | — |
| 45 | 15–30 | 18 | **1.000** | **5.6%** | 0.940 | +0.060 |
| 30 | all buckets | 48 | 0.67–1.00 | **0.0%** | — | — |
| 15 | 5–15 | 10 | 1.000 | 20.0% | 0.510 | +0.490 (size **2.0**) |

A few cells look "cheap". **They do not survive the decisive test below**, and
every one of them rests on 1–7 available observations with an in-sample
probability — several are literally 18/18, which will not be 100% forward.

### THE DECISIVE TEST — availability selects adversely *within* the state

| TTE | P(correct \| ALL) | P(correct \| AVAILABLE) | **delta** |
|---|---|---|---|
| 120 | 0.750 | 0.600 (n=25) | **−0.150** |
| 90 | 0.854 | 0.667 (n=15) | **−0.188** |
| 60 | 0.958 | 0.857 (n=7) | **−0.101** |
| 45 | 0.917 | 0.778 (n=9) | **−0.139** |
| 30, 20 | 0.938 | **none available** | — |
| 15, 10, 5 | 0.958 | 1.000 (n=1–2) | +0.042 *(noise)* |
| **POOLED** | **0.908** (n=336) | **0.705** (n=61) | **−0.203** |

**The BTC state really is informative — 90.8% unconditionally. Conditioning on
the side being purchasable destroys about 20 points of it, leaving 70.5%.**
And the mean executable price is ~0.80, so even 70.5% does not pay.

That reconciles the apparent contradiction with the earlier replay exactly:
the depth-filtered subset is *more* adversely selected still (55.6% at 79.7¢,
a −24.1 point deficit). The in-sample "cheap" cells above are an artifact of
computing P over all windows while only being able to trade the adverse subset.

---

## What this means

| | |
|---|---|
| Is there information in BTC state? | **Yes, strong — AUC 0.837 at T−120, 0.99 near expiry.** |
| Is it richer than position? | **No.** Momentum, velocity and acceleration do not clear a permutation null. |
| Is the informative side purchasable? | **Increasingly not** — 0% availability at T−30 and T−20. |
| When purchasable, is it cheap? | **No** — availability costs ~20 points of accuracy, and price exceeds what is left. |

**The market is pricing this information efficiently, and liquidity withdraws
precisely where the information is strongest.** That is a coherent, mechanistic
explanation rather than an execution detail to be engineered around.

## Limits

n = 48 windows; cells of 5–19; conditional probabilities estimated in-sample
and therefore optimistic; one afternoon/evening of one day; a single
mechanical predictor family. This is a **diagnostic**, not proof. It does not
establish that no BTC-state edge exists — it establishes that **the one we
collected for does not survive contact with availability on this cohort.**

## Implication for the plan — stated, not acted on

The failure is **structural (availability), not statistical (sample size)**.
Collecting 250 more windows would sharpen the probability estimates but would
not change the fact that the side is unbuyable at T−30 and adversely selected
earlier. **If the lane continues, the question worth powering is not "is BTC
state predictive" — it is answered — but "does any state exist where the
informative side is both purchasable and cheap", which this cohort suggests is
rare to absent.** That is a decision for you; collection continues untouched
meanwhile.
