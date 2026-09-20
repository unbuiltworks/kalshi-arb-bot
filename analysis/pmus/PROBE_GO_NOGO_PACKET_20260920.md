# Bounded live probe — GO/NO-GO evidence packet

**Status: NOT ARMED. Nothing in this packet authorises, prepares or configures an
order path.** Model A, the residual cap (3) and the COMPLETE rule are frozen and
unchanged. Cricket is research-only and excluded from every deployable figure.
Gemini A+B untouched as negative control. International reference-only.

Generated 2026-09-20T17:13Z. Prospective window opens at the sampling-frame
marker **15:24:09Z** and runs 109 minutes wall-clock, **with two collector gaps
inside it** (see Provenance).

---

## 1. Fidelity — what is now demonstrated against L5, and what is not

**Demonstrated.**

| question | evidence | verdict |
|---|---|---|
| Does Model A complete too rarely? | Model A recommended COMPLETE in 41/76 deployable episodes (53.9%); historical L5's opposite-acquisition hazard at matched run-length × imbalance states is ~32% | **No — Model A is *more* willing to complete than L5** |
| Is the switch rule faithful? | L5 switch hazard falls monotonically with run length (.361 → .130 OOS, n=69,768 decisions); no candidate variable improved OOS over run length alone (full model logloss 0.741 vs 0.604 baseline) | **Faithful; run length is the only OOS-informative variable** |
| Is long-lived residual a defect? | L5 carried **90.4%** of residual episodes to settlement (n=2425); PMUS carries 92.1% (n=76) | **Not a defect — it is L5 behaviour** |

**Remaining mismatches.**

1. **Exposure duration, the material one.** L5's residual was extinguished by a
   5–15 minute settlement clock. PMUS live-sports residual is extinguished by the
   end of the game: expected remaining duration at first fill p25 20 min,
   **p50 38 min**, p90 98 min. Same behaviour, 2–7× the exposure time.
2. **Flow provenance.** L5's corpus and International are real prints with known
   direction. PMUS flow is CAUSAL INFERRED from `sharesTraded` deltas and is
   never a print. Every PMUS fill below is modelled, not observed.
3. **Market class.** The entire L5 corpus is BTC/ETH 5m/15m crypto. No L5
   sports-market behaviour has ever been observed directly.

---

## 2. Execution — prospective, non-cricket, queue-aware

FIRST FILL → COMPLETE RECOMMENDED → COMPATIBLE FLOW → QUEUE CLEAR → MATCHED

| cohort | 1stFill | COMPL | FLOW | QCLR | MATCH | units | conv/100 | lat p50 | medEXT |
|---|---|---|---|---|---|---|---|---|---|
| **queue-aware deployable** | **76** | 41 | 9 | 8 | **8** | **12** | **10.5** | **371 s** | 2 |
| queue-aware full research | 79 | 42 | 10 | 9 | 9 | 15 | 11.4 | 317 s | 2 |
| optimistic deployable | 105 | 57 | 12 | 10 | 12 | 19 | 11.4 | 371 s | 2 |

By league (queue-aware deployable, matched first):

| league | eps | COMPL | matched | units | lat p50 | basis |
|---|---|---|---|---|---|---|
| cs2 | 7 | 4 | 2 | 4 | 5213 s | 0.9800 |
| setkameua | 11 | 8 | 2 | 3 | 376 s | 0.9767 |
| lal | 12 | 7 | 2 | 2 | 317 s | 0.9800 |
| rl | 1 | 1 | 1 | 2 | 224 s | 0.9800 |
| setkamecz | 5 | 3 | 1 | 1 | 279 s | 0.9800 |
| epl | 7 | 2 | 0 | 0 | — | — |
| bun | 6 | 5 | 0 | 0 | — | — |

**The chain breaks upstream of the queue.** Of the unmatched episodes the loss is
at COMPLETE-recommendation and at compatible-flow arrival; only 1 episode in the
whole sample died at the queue itself.

---

## 3. Economics — modelled, never realised

Known fees: **PMUS maker fee $0.0000 per pair.** The maker rebate is
**EXCLUDED, never estimated** — `/v1/transfers` carries no order id, so
fill-level attribution does not exist.

**Queue-aware deployable, every matched basis value:**

`0.9733, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800`

- median 0.9800, 8 of 8 below $1.00
- modelled gross per pair +0.0200 (best +0.0267)
- **TOTAL modelled gross across all 12 matched units: +$0.2467**
- **The distribution is one tick wide. There is no tail.**

Optimistic deployable (upper bound, never pooled with queue-aware) has 12 values
including two outliers at 0.6267 and 0.6667 from violent in-play moves, giving
+$1.6599 on 19 units. **Those two carry the entire optimistic advantage and do
not appear on the queue-aware branch at all.**

**Unresolved residual economics are separate and are never summed with matched
economics:** 70 episodes, **149 residual units**, notional at modelled cost
**$52.28**, economics **UNMEASURED** until settlement.

---

## 4. Risk

**Residual size** — bounded by construction: `{1: 28, 2: 5, 3: 37}`, cap 3, and
37 of 70 sit exactly at the cap.

**Residual duration** — Kaplan-Meier, event = return to flat:

| | 30 s | 60 s | 120 s | 300 s | 600 s | 900 s | 1200 s |
|---|---|---|---|---|---|---|---|
| **PMUS deployable** (n=76) | 1.4% | 1.4% | 1.4% | 2.9% | 6.1% | 8.4% | 8.4% |
| at risk | 72 | 71 | 67 | 63 | 50 | 40 | 31 |
| **Historical L5** (n=2425) | 4.6% | 6.5% | 8.3% | 9.9% | 10.8% | 10.8% | undefined |

Median time to flat **not reached** in either. L5's row is undefined past 900 s
because no L5 market lives that long.

**Time to expected settlement at first fill:** p25 20 min, p50 38 min, p90 98 min.

**The governing ratio:** +$0.2467 of modelled gross against **$52.28** of
directional residual notional held for a median 38 minutes — **0.47%**, and
12.4 residual units per matched unit.

**Concurrency implication:** at 10.5 matched per 100 first fills, a probe that
completes one matched pair must be prepared to have opened roughly ten positions,
most of which will still be carrying residual when the pair completes. A
one-market-at-a-time constraint therefore caps throughput to roughly one matched
pair per several event-hours. That is a consequence of *our chosen safety rule*,
not of L5 behaviour — L5 never required returning flat between opportunities.

### Proposed smallest possible envelope (external to Model A, not a strategy change)

Sized to the observed distribution, deliberately not sized for throughput.

| bound | value | basis |
|---|---|---|
| max dollars per market | **$2.00** | ≈ 4 units at the observed median unit cost 0.33 |
| max residual units | **3** | the existing frozen cap, unchanged |
| max residual dollars | **$1.00** | 3 units at observed median cost |
| max concurrent residual markets | **1** | one market at a time |
| max total directional exposure | **$1.00** | equals the single-market residual bound |
| max time-to-expected-settlement for a new first fill | **45 min** | just above the observed p50 of 38 min; bounds toward the L5-like end |
| daily realised-loss stop | **$2.00** | one market's maximum |
| stale/unreadable state | **fail closed**, no new quote, no flatten | matches the read-only posture |

---

## 5. NFL PRE→LIVE — reported separately, nothing tuned from it

**Cadence transition CONFIRMED.** Same collector, post-kickoff:

- LIVE NFL per-slug cadence **median 8.0 s** (p10 8.0, p90 11.6, n=1805)
- still-PRE NFL cadence **median 34.2 s** (n=1144) — the 20:05Z/20:25Z games

**Exact-slug continuity: 45 of 67 live slugs (67.2%)** were observed PRE by the
same collector. The 22 live-only slugs are mostly new spread lines the venue
creates at kickoff, plus moneylines whose PRE observation fell inside a collector
gap.

**NFL funnel: zero first-fill episodes since kickoff.** This is NOT a finding
about NFL liquidity — local coverage since kickoff is ~6 minutes and the
collector was down 16:55:30Z–17:04:32Z, across the kickoff itself.

---

## 6. Provenance

- Prospective boundary: `SAMPLING_FRAME_CHANGE` marker **15:24:09Z**, stamped
  before any prospective outcome existed.
- Queue-aware and optimistic are separate sequential replays, reported separately,
  **never pooled**.
- PMUS flow: CAUSAL INFERRED from `sharesTraded` deltas, never a print.
  International: real prints, reference only.
- Causality invariants, 7 tests passing: no fill may be credited before the
  previous fill in a sequence; residual duration cannot be negative.
- **Two collector gaps inside the prospective window**: 16:12:33Z–16:50:45Z
  (38 min) and 16:55:30Z–17:04:32Z (9 min, spanning kickoff). Both marked
  `COLLECTOR_GAP` in every stream. The durable Railway volume has continuous
  coverage; the local series used for this funnel does not.
- Sample sizes are small in every cell and are printed with every figure.

---

## 7. The case for and against proceeding

**For.**

1. Fidelity questions that blocked a probe are now answered with evidence, not
   assertion: the completion rule, the switch rule and the residual-carrying
   behaviour all match L5.
2. Every matched pair modelled so far is below $1.00 — 8 of 8 queue-aware,
   12 of 12 optimistic.
3. Known fees are genuinely zero on the maker side, and the rebate is upside that
   is deliberately not counted.
4. The chain does complete on PMUS, in five different leagues, at the venue we can
   actually execute on.

**Against.**

1. **Scale.** +$0.2467 total modelled gross against $52.28 of directional notional
   is 0.47%. A probe cannot be sized down far enough to make that ratio safe —
   shrinking the position shrinks the gross identically.
2. **No tail.** The queue-aware basis distribution is one tick wide (0.9733–0.9800).
   The only large values in the whole sample are on the optimistic branch, which
   is an upper bound, not an execution model.
3. **Exposure duration is the unresolved mismatch.** Same carrying behaviour as
   L5, 2–7× the clock, and the residual KM median is not reached.
4. **Sample.** 76 deployable episodes, 8 matched, over 109 minutes with two gaps.
   No cell supports a stable estimate.
5. **The fills are modelled.** Not one PMUS fill in this packet was venue-confirmed.

**Recommendation: NO-GO at this time**, on scale and sample — not on fidelity.

### What would change this to a GO

- Queue-aware matched units **≥50** on a continuous multi-hour window with no
  collector gap, at a basis distribution that still has mass below 0.97.
- Modelled gross as a fraction of residual notional carried **≥5%**, an order of
  magnitude above the current 0.47%.
- Residual KM median time-to-flat **reached** inside the event clock, or a
  demonstrated settlement-value distribution for carried residual that shows it
  is not a systematic loss.
- At least one venue-confirmed maker fill on PMUS establishing that the queue
  model is not optimistic.

### What would falsify a GO even if the above were met

- Matched basis distribution shifting to or above 1.00 on a larger sample.
- Residual carried to settlement showing negative expected value large enough to
  exceed accumulated pair gross — which is currently **unmeasured** and is the
  single largest unknown in this packet.
- Any evidence the queue-aware fill model is optimistic against real fills.
