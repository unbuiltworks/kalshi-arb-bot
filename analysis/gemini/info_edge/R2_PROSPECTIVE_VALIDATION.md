# R2 prospective validation — read once, rule untouched

Rule frozen at **`6bc5c1f730dd0d4639eb4c9459803dbf9d68dee5`**, committed
**before** any prospective data was read. Fingerprint
`64b178378806b5c257b58b29d0ba49da`. Tool `r2_frozen.py`, raw output
`r2_frozen_result.json`. Identical code path for both cohorts.

**R2 = post-strike · favoured side offered · `|z| >= 1.0` · de-duplicated to
one opportunity per (window, side, price level).** Nothing was changed after
the read.

## A correction to my own DEV headline, made before the read

While verifying, I found my first DEV pass counted **raw checkpoints**: an
offer resting at one price level across several checkpoints was counted
several times. One level in `BTC05M2610022240` appeared three times at TTE
15/10/5 and, being cheap and correct, dragged the mean price from 0.873 down
to 0.833.

**DEV EV/contract was therefore +0.0519, not the +0.0983 I reported.** The
inflated number was a defect in my extractor, not a property of the market.
De-duplication is now part of the frozen rule. The monotonicity survives and
is cleaner for it:

| | windows | EV net |
|---|---|---|
| z ≥ 0.50 | 37 | −0.0421 |
| z ≥ 0.75 | 24 | +0.0206 |
| **z ≥ 1.00** | **12** | **+0.0519** |
| z ≥ 1.25 | 8 | +0.0913 |
| z ≥ 1.50 | 6 | +0.1050 |

Also verified before freezing: side mapping against settlement (every UP
instance has `dist > 0`, every DOWN instance `dist <= 0`); executable price
construction (UP = best ask, DOWN = 1 − best bid); the fee formula; and the
`sigma` estimator against the tape — it implies a 1 sd 300 s move of **$40.8**
against an empirical p68 |move| of **$33.8**, so it is slightly
**conservative** and `z` does not overstate the cushion.

## DEV vs PROSPECTIVE, side by side

| | DEV | **PROSPECTIVE** |
|---|---|---|
| eligible settled windows with a strike | 48 | **134** |
| post-strike executable instances | 142 | 366 |
| R2 opportunities (de-duplicated) | 16 | **65** |
| **independent windows** | **12** | **42** |
| windows producing an opportunity | 25% | **31.3%** |
| executable price p10 / p50 / p90 | 0.64 / 0.93 / 0.98 | 0.49 / 0.88 / 0.98 |
| mean executable price | 0.873 | **0.810** |
| settlement correct | 15/16 = **93.8%** | 56/65 = **86.2%** |
| mean taker fee | 0.0125 | 0.0134 |
| EV/contract gross | +0.0644 | +0.0511 |
| **EV/contract NET** | **+0.0519** | **+0.0377** |
| total q=1 counterfactual NET | +$0.83 / 16 | **+$2.45 / 65** |
| bootstrap EV p05 / p50 / p95 | −0.0492 / +0.0553 / +0.1325 | **−0.0808** / +0.0348 / +0.1473 |
| share of bootstrap draws EV ≤ 0 | 18.1% | **31.6%** |
| size p10 / p50 / p90 | 25 / 1071 / 7500 | 25 / **681** / 7500 |
| losses | 1 | **9** |
| worst loss per contract | 0.990 | **1.000** |

Prospective span: 01:52Z → 11:47Z, **9.9 hours**, 134 eligible windows.

### Window-level risk, which is the real unit

42 independent windows, q=1 per opportunity:

```
mean net/window  +0.0583      median  +0.1300
min    -1.9000   max  +3.1400
windows profitable  34/42 = 81.0%
worst five windows  -1.90  -0.98  -0.98  -0.90  -0.90
window-level bootstrap (8,000 draws, resampling WINDOWS)
  p05 -0.1136   p50 +0.0536   p95 +0.2402
  one-sided P(mean net <= 0) = 0.304
```

### By TTE — descriptive only, no TTE selected

| TTE | n | windows | correct | price p50 |
|---|---|---|---|---|
| 240 | 10 | 10 | 70.0% | 0.86 |
| 180 | 23 | 23 | 82.6% | 0.90 |
| 120 | 13 | 13 | 92.3% | 0.89 |
| 90 | 8 | 8 | 87.5% | 0.98 |
| 60 | 2 | 2 | 100.0% | 0.95 |
| 45 | 2 | 2 | 100.0% | 0.49 |
| 30 | 2 | 2 | 100.0% | 0.98 |
| 20 | 3 | 3 | 100.0% | 0.84 |
| 5 | 2 | 2 | 100.0% | 0.83 |

**No TTE is selected, now or ever.** The apparent improvement at short TTE
rests on n = 2–3 per cell and is exactly the kind of cell a future reader
would be tempted to mine. It is recorded so that temptation is on the record
and refused.

Pooled DEV + prospective, for completeness only (DEV is in-sample):
n = 81, 54 windows, 87.7% correct, mean price 0.823, EV net **+0.0405**, total
q=1 net **+$3.28**.

## Verdict: R2 SURVIVED, but did not reach significance

**It survived.** Same sign, same order of magnitude, on **42 independent
windows** — a genuine effective sample, not 65 correlated checkpoints.
+0.0377 against a corrected DEV +0.0519 is **73% replication**. Correctness
fell (93.8% → 86.2%) and price fell with it (0.873 → 0.810), which is what a
real effect looks like when it meets fresh data: partially offsetting, not
vanishing. 81% of windows were profitable.

**It did not become tradeable.** The window-level bootstrap gives
**P(mean net ≤ 0) = 0.304**. Roughly one chance in three that the true edge is
zero or negative. That is not a bar I would put money behind, and the
prospective lower tail (−0.0808/contract) is *worse* than DEV's, not better.

So neither arm of the clean fork fires. This is a **partial survival**, and
the honest call is the third option:

> **Keep R2 frozen. Keep collecting. Do not trade it yet.**

## Recommendation

1. **No live probe now.** One-in-three odds of no edge does not justify money,
   and the capacity picture compounds it: size p10 is 25 contracts against a
   median of 681, so live sizing must survive the small end.
2. **ONE more read, preregistered now, at 100 independent R2 windows** (not
   instances — your correction is right and I was wrong to frame the target as
   70–80 instances). At the observed 31.3% window hit rate and ~12 eligible
   windows/hour, 100 R2 windows is roughly **26 more hours** of collection.
   That should take the window-level standard error from ~0.09 to ~0.06 and
   move `P(≤ 0)` decisively toward either ~0.05 or ~0.5.
3. **Exactly one read.** Re-reading accumulating prospective data every few
   hours is threshold-shopping in the time dimension — optional stopping gets
   you a significant result eventually whether or not the edge is real. The
   next look happens at 100 R2 windows and nowhere else.
4. **If it clears at 100 windows** (`P(≤ 0)` below ~0.10 with EV net still
   positive after fees) → bounded q=1 live probes to measure actual fill and
   slippage economics, under a fresh one-shot authorization.
5. **If it fails** → R2 is dead, the Gemini BTC5m directional lane is
   deprioritized, and resources return to direct L5 replication. **No
   threshold-shopping afterward**: z = 1.0 is the rule, and 0.75 / 1.25 / 1.5
   do not become "the real R2".

## An extrapolation, flagged as premature

+$2.45 over 9.9 h at q=1 is **≈ $5.9/day**. Reaching the $480/day historical
target would need q ≈ 81 contracts per opportunity. Median displayed size
(681) would allow it; the worst observed window (−1.90 at q=1) would become
**−$154**, and a 30% chance of zero edge makes that sizing reckless today.
The number is here only so nobody has to guess at it — it is not a plan.

## Out of scope, deliberately

**SEMANTICS-1's −$0.50 realized loss is excluded** from every figure above.
Its fill was pre-strike (venue metadata: `startTime` and `strike.availableAt`
both 12:40:00Z, fill 12:33:05.968Z), so it carries no directional information
in either direction. See `SEMANTICS_1_EVIDENCE_CHAIN.md`.
