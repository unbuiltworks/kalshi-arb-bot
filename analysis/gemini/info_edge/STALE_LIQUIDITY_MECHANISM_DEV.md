# Stale-liquidity mechanism check — frozen 48-window DEV cohort

Scope: DEV cohort only (`DEV_COHORT_FROZEN.json`, boundary `2026-10-03T01:50:00Z`,
24 yes / 24 no). Prospective/sealed data untouched. No thresholds optimized —
horizons are fixed at 1/2/5/10 s and reported in full. Tool:
`analysis/gemini/info_edge/ie_staleness.py`, raw output `ie_staleness.json`.

Question asked: **does exploitable stale liquidity ever occur on the
BTC-state-favored side, and is it observable before the fill?**

## Method

For every instance in the DEV cohort where the BTC-state-favored side became
executable (an offer existed at or inside the price implied by the favored
side), reconstruct the preceding 1/2/5/10 s event-by-event from the `@depth`
diff stream and the Coinbase trade tape:

- per-level **first-seen time** (`level_age`) maintained from the diff stream,
  reset whenever a price level disappears and reappears;
- **persist time** — how long the level survived after the instance;
- Coinbase signed move over each horizon;
- displayed size and price at the instance.

"PRE-MOVE" at horizon h means `level_age > h`: the resting offer already
existed before the underlying move of the last h seconds, i.e. it is a
candidate for being stale rather than freshly repriced.

## Results

**61 executable instances across 28 independent windows. The favored side
settled correct in 43/61 = 70.5%.**

### Level age

| | n | p10 | p50 | p90 | persist p50 | still_there |
|---|---|---|---|---|---|---|
| winners | 43 | 1.0 s | 6.0 s | 30.0 s | 4.0 s | 0 |
| losers | 18 | 0.0 s | 4.0 s | 147.2 s | 7.0 s | 0 |

Winners are *not* older on the tail — the long-lived tail (p90 147 s) belongs to
the losers. `still_there` is 0 in both groups: every one of these levels was
gone by the end of the observation, so none of them is liquidity that simply sat
there to be taken at leisure.

### PRE-MOVE split (all four fixed horizons reported)

| h | pre-move n | pre-move correct | post-move n | post-move correct | gap |
|---|---|---|---|---|---|
| 1 s | 49 | 71.4% | 12 | 66.7% | +4.8 |
| 2 s | 46 | 71.7% | 15 | 66.7% | +5.1 |
| 5 s | 34 | 79.4% | 27 | 59.3% | **+20.2** |
| 10 s | 19 | 78.9% | 42 | 66.7% | +12.3 |

### Coinbase move preceding the instance

Median signed move is **+0.00 at every horizon for both winners and losers** —
most instances follow no meaningful underlying move at all. Only the tails
differ: |move over 10 s| p90 = 16.46 (winners) vs 10.45 (losers).

### Full stale signature

Requiring all three conditions jointly — favored side correct, `level_age > 10 s`,
and a favourable 10 s underlying move — leaves **7 of 61 instances, spread over
only 5 independent windows**. Price p50 0.78, size p50 25.0, age p50 30.0 s,
persist 1–12 s. Three of the seven are the *same* ticker
`BTC05M2610022240` at T−15 / T−10 / T−5 with displayed size **2.0 contracts**.

## Answer

**Yes in principle, no in practice.**

1. *Does it occur?* A pre-move-aged subset exists and separates: at h=5,
   79.4% correct vs 59.3%. So some executable favored-side offers do look like
   liquidity that had not repriced.
2. *Is it observable before the fill?* Yes — `level_age` is computable from the
   diff stream in realtime, before any order is sent. It needs no settlement
   knowledge.
3. *Is it exploitable?* Not on this evidence. The joint signature fires 7 times
   in 48 windows across 5 windows, one of which contributes 3 instances at 2
   contracts. That is roughly one economically-sized opportunity per ten
   windows at sizes that cannot carry the fee/spread load established earlier
   in this lane.

## Caveats (carried deliberately)

- **Clustering.** 61 observations over 28 windows; within-window correlation is
  heavy, so the effective n is far below 61 and no p-value is quoted here.
- **Multiplicity.** Four horizons were examined. The +20.2 at h=5 is the best
  of four and is not corrected; it should not be read as a calibrated effect.
- **No median move.** Since the median preceding Coinbase move is +0.00 in both
  groups, the h=5 separation is *not* mostly a fast-underlying-move story. The
  age variable is doing the work, not the move — which weakens the specific
  "stale after a fast move" mechanism this check was designed to test.
- **Size.** The signature set's sizes are not screened for economic viability,
  and inspection shows they often are not viable.

## Status

Mechanism diagnostic only. **Nothing is frozen as a rule, no strategy is
promoted, and no live order is implied.** This does not reopen the killed
`sign(distance)` rule. Prospective collection continues untouched.
