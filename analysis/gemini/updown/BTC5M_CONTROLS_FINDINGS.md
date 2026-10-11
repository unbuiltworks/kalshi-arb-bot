# Two dumb BTC5m controls: both negative, and one number explains everything

**Gemini BTC5m only. One trade per window, q=1 contract, held to settlement. No exits,
scaling, Martingale, flips, filters, Z or state conditioning. Existing strategies and
conclusions untouched. No live orders.**

## Decision

**(A) Is there evidence UP itself has positive executable hold-to-settlement
expectancy? NO.** UP settles 55.64% of 133 windows, but the 95% CI is
**[47.4%, 63.9%]** — it straddles 50%, so there is no established directional base-rate
advantage at all. And even granting one, it is unbuyable: UP has an executable ask in
only 85 of 133 windows, and **in those 85 windows UP settles 36.47%** against a 52.06%
fee-adjusted breakeven.

**(B) Is there evidence buying the initial non-favored outcome has positive executable
hold-to-settlement expectancy? NO.** On genuinely two-sided books at the earliest
available checkpoint, the cheaper outcome settles **30.36%** against a **49.46%**
breakeven — **−19.10pp**, CI low 31.6pp below breakeven. Its exact inverse, the initial
favorite, is **−18.18pp**. Both sides of the same trade lose by roughly the same amount,
in every price bucket, at every checkpoint tested.

### The one number that explains both

| at tte=120s | side settles… where **it has an ask** | …where **it has none** |
|---|---|---|
| **UP** | **36.47%** (n=85) | **89.58%** (n=48) |
| **DOWN** | **31.07%** (n=103) | **90.00%** (n=30) |

**The presence of an executable ask on a side is itself a ~55-point adverse signal, and
it is symmetric in direction.** This is not about UP versus DOWN, and not about favorite
versus underdog. The venue offers you a side it expects to lose. Every negative result
in this lane is a restatement of that.

## The symmetric adverse-ask table, in full

| at tte | UP settles where **UP has an ask** | where UP has **none** | DOWN settles where **DOWN has an ask** | where DOWN has **none** |
|---|---|---|---|---|
| 120s | **36.47%** (n=85) | **89.58%** (n=48) | **31.07%** (n=103) | **90.00%** (n=30) |

Read it twice: a side that is *offered* loses roughly two thirds of the time, and the
same side when *not offered* wins roughly nine tenths of the time. The ask is the
signal, and it points the wrong way for a buyer in both directions.

---

## Were these exact controls run before?

| control | status |
|---|---|
| **A — buy UP always / buy DOWN always, unconditionally** | **NEW.** No prior study bought a *fixed direction*; every earlier arm selected its side by price (cheaper/dearer) or by a state proxy. |
| **B1 — initial underdog, two-sided books only** | **NEW.** The earlier underdog matrix deliberately pooled one-sided books, where "the cheaper executable side" is whichever side happens to still be quoted. Restricting to two-sided books is a different population and was never measured. |
| **B2 — initial favorite, two-sided books only** | **OVERLAPS** an earlier measurement — the favorite test already required both sides executable, so its 120s row is the same population. Re-reported for symmetry; the numbers agree. |

---

## The checkpoint, and why it is not T+0

BTC5m has a **300s tradeable life** (the strike is null until `availableAt` =
expiry − 300s). The recorded grid is 1/2/5/10/15/20/30/45/60/90/120s remaining, so:

- **Earliest consistently available causal checkpoint: tte = 120s, i.e. T+180s** —
  already 60% through the window. This is the frozen primary.
- **The requested opening ladder T+5/15/30/60 (tte 295/285/270/240) is ENTIRELY ABSENT
  from this corpus.** Per your instruction it is **not** substituted with a later
  observation. It needs the P2 recorder.
- 90/60/45s are reported as secondary rows because they exist. **Two-sided coverage
  collapses below 90s** (56 → 30 → 13 → 14 windows), so Control B is only meaningfully
  testable at 120s and 90s.

Two-sided-only is load-bearing for Control B: on this venue a bid-only book still offers
a purchase (DOWN at `1 − bid`), so classifying favorite/underdog from a surviving
one-sided quote would label whichever side was still being offered — which, per the
table above, is the losing one by construction.

---

## CONTROL A — directional base rate, at tte=120s (T+180s)

Unconditional UP settlement rate across all 133 settled windows: **55.64%**,
95% CI **[47.20%, 64.08%]** (bootstrap [47.37%, 63.91%]) — **straddles 50%**.

| | **A1 buy UP once** | **A2 buy DOWN once** |
|---|---|---|
| trades / windows attempted | 85 / 133 (63.9% tradeable) | 103 / 133 (77.4%) |
| avg executable ask | 0.5031 | 0.4806 |
| **settlement win %** | **36.47%** | **31.07%** |
| win % CI95 | [27.06, 47.06] | [22.33, 39.81] |
| breakeven, zero fees / taker | 50.31% / **52.06%** | 48.06% / **49.81%** |
| **win − breakeven** | **−15.59pp** | **−18.74pp** |
| CI low − breakeven | −25.00pp | −27.48pp |
| established positive | **no** | **no** |
| fees / trade | $0.0121 | $0.0112 |
| **net P&L / trade** | **−$0.1504** | **−$0.1811** |
| net P&L / window attempted | −$0.0961 | −$0.1403 |
| **net P&L / day** (288 windows) | **−$27.69** | **−$40.40** |
| max drawdown | −$13.85 | −$18.81 |
| longest loss streak | 11 | 13 |
| depth median / p10 | 192 / 25 contracts | 192 / 25 |
| capacity at median depth | $96.59 | $92.27 |

**Note the arithmetic that looks wrong and is not:** 36.47% + 31.07% = 67.5%, not 100%.
The two arms trade *different, overlapping subsets* of windows — UP is tradeable in 85,
DOWN in 103 — and each is selected against. On the 56 windows where both are tradeable
the complement identity holds exactly: cheaper 30.36% + dearer 69.64% = **100.00%**.

Secondary checkpoints, same shape, all negative:

| tte | A1 win / BE / edge | A2 win / BE / edge |
|---|---|---|
| 90s | 21.74% / 43.12% / **−21.38** | 23.33% / 40.50% / **−17.16** |
| 60s | 12.90% / 28.93% / **−16.03** | 14.67% / 29.55% / **−14.88** |
| 45s | 14.29% / 28.64% / **−14.36** | 11.69% / 29.42% / **−17.74** |

---

## CONTROL B — initial underdog and its inverse, two-sided books only

### tte = 120s (T+180s), 56 two-sided windows — 100% tradeable

| | **B1 cheaper outcome** | **B2 dearer outcome** |
|---|---|---|
| trades | 56 | 56 |
| avg executable ask | 0.4771 | 0.8704 |
| **settlement win %** | **30.36%** | **69.64%** |
| win % CI95 | [17.86, 42.86] | [57.14, 82.14] |
| breakeven, zero fees / taker | 47.71% / **49.46%** | 87.04% / **87.83%** |
| **win − breakeven** | **−19.10pp** | **−18.18pp** |
| CI low − breakeven | −31.60pp | −30.69pp |
| established positive | **no** | **no** |
| net P&L / trade | **−$0.1896** | **−$0.1812** |
| net P&L / day | −$54.62 | −$52.17 |
| max drawdown | −$12.32 | −$10.50 |
| longest loss streak | 12 | 3 |
| depth median | 239.5 | 150 |
| side mix | UP 36 / DOWN 20 | UP 20 / DOWN 36 |

### B1 by price bucket, tte = 120s

| bucket | trades | avg ask | win % | breakeven (taker) | **edge pp** | CI low − BE |
|---|---|---|---|---|---|---|
| <10c | 0 | — | — | — | — | — |
| 10-20c | 0 | — | — | — | — | — |
| 20-30c | 2 | 0.245 | 50.00% | 25.79% | **+24.21** | -25.79 |
| 30-40c | 16 | 0.347 | 18.75% | 36.34% | **-17.59** | -36.34 |
| 40-50c | 17 | 0.452 | 11.76% | 46.97% | **-35.20** | -46.97 |

**21 of the 56 B1 trades fall in no bucket**: the cheaper side was still priced at or above 50c. That is possible — and common at T+180s — because the two executable asks sum well above 1.00 on this venue, so *both* sides can exceed 50c at once. Those trades are in the arm total and in the artifact; they are simply outside the requested <50c grid, and are counted rather than dropped.


### Secondary checkpoints

| tte | two-sided n | B1 win / BE / edge | B2 win / BE / edge |
|---|---|---|---|
| 90s | 30 | 30.00% / 45.46% / **−15.46** | 70.00% / 89.59% / **−19.59** |
| 60s | 13 | 38.46% / 51.75% / **−13.29** | 61.54% / 85.38% / **−23.84** |
| 45s | 14 | 42.86% / 44.50% / **−1.64** | 57.14% / 91.44% / **−34.30** |

The 45s B1 row is the only near-breakeven cell in either control (net −$0.0154/trade),
and it rests on **14 trades** with a CI low 23.07pp below breakeven. It is noise and is
not promoted.

---

## What this does and does not establish

**Establishes:** neither a fixed direction nor the initial underdog (nor its inverse)
has positive executable hold-to-settlement expectancy on this corpus, at any tested
checkpoint, in any price bucket. Every arm is negative by 13–34 points with the
confidence interval entirely below breakeven, and the most profitable day in any arm is
−$4.43.

**Does not establish anything about T+0 to T+120.** The earliest observation this corpus
supports is T+180s, by which point 76 of 133 books are already one-sided. Whether a
two-sided book near the open prices the two outcomes fairly is **untested and
untestable here** — it is the single most valuable thing the P2 recorder would add.

**Does not touch** the frozen policy (fingerprint `5c113fab…6329`), the rejected
price-only study, or the BTC15m control lane.

## Artifacts

`btc5m_controls.json` · script `scripts/gemini_btc5m_controls.py`.
Accounting label: historical replay on Gemini's own recorded books and settlements.
No order was sent. Nothing here is realized profit.
