# Execution-speed analysis (existing tapes only; nothing deployed)

Script: `analysis/l5/execution_speed_eval.py`. Source: the preserved
Oct 24-26 tapes. No new infrastructure, no strategy change, no limit change.

**The 20-34s between paired fills is queue wait, not latency, and is treated
as such throughout.** It is the time a resting maker order waits for the
market to come to it.

## 1. Market-data latency — not a bottleneck

| measure | n | min | p50 | p90 | max |
|---|---:|---:|---:|---:|---:|
| book age at decision / presubmit (`stream_age_s`) | 826 | 0.000 | **0.041** | 0.097 | 0.905 |
| residual-sample book `row_age_s` | 135 | -0.283 | 0.084 | 0.340 | 1.861 |
| queue ladder `frame_age_s` at presubmit | 47 | 0.002 | 0.059 | 0.100 | 0.240 |

Feed is `authenticated_ws`, captured `mirror_snapshot_at_read`. Sub-100ms at
p50. (The negative `row_age_s` minimum is the same clock-skew artefact as the
negative `latency_s` rows already tracked; it does not affect these
conclusions.)

## 2 and 6. Decision cadence — the headline number, and it is NOT our delay

| measure | n | min | p50 | p90 | max |
|---|---:|---:|---:|---:|---:|
| gap between `L5_LIVE_DECISION` evaluations | 132 | 0.504 | **32.216** | 70.797 | 201.154 |
| gap between residual book samples (configured 5.0) | 132 | 5.305 | 6.557 | 6.882 | 7.692 |
| gap between order-state polls per order | 62 | 0.796 | 6.652 | 7.256 | 8.094 |

A 32s p50 against 0.041s data looks like a ~780x mismatch, and my first
reading was that this is an idle loop. **It is not.** The main loop is
`L5_LIVE_POLL_S`, default **3 seconds** (`l5_pmus_live_runner.py:6490`,
`time.sleep(poll_s)` at 6780). Every `L5_LIVE_DECISION` row carries
`l5_price`, `l5_size` and `latency_s` — the fields of an observed L5 action.

So the engine evaluates a pairing opportunity **when an L5 action arrives**,
and L5 acts about every 32s. The 32s is a third party's activity rate, not a
sleep we inserted. `ORDER_POLL_S = 6.0` and `SETTLEMENT_POLL_S = 10.0` are
ours and match the measured 6.65s order-poll cadence.

One genuinely avoidable overhead: the residual sampler is configured
`every_s = 5.0` but measures 6.56s at p50, ~30% over. It is reporting-only
and costs no P&L.

## 3. Submission latency

| measure | n | min | p50 | p90 | max |
|---|---:|---:|---:|---:|---:|
| presubmit -> ack OBSERVED | 16 | 3.506 | **4.289** | 8.163 | 9.646 |

**Upper bound, not latency.** The venue's own ack time is never given to us:
`L5_FILL_BOOK_EVIDENCE` carries `venue_event_t: null` and
`processing_lag_s: null` on every row. `ORDER_STATE_NEW` is the first time
OUR 6.65s poll saw the order, so each figure is inflated by up to one poll
interval. True submission latency is somewhere in 0-4.3s and **cannot be
measured with current instrumentation** — that gap is itself a finding.

## 4. Maker execution — queue wait, with the real constraint visible

| measure | n | min | p50 | p90 | max |
|---|---:|---:|---:|---:|---:|
| ack observed -> fill observed | 11 | 5.520 | 13.478 | 19.750 | 73.441 |
| presubmit -> fill observed | 13 | 8.926 | 17.129 | 23.588 | 83.088 |

Per-order queue depth at presubmit, against clips of 0.96-2.09 contracts:

    queue_ahead  760 - 7,778 contracts
    level_depth  346 - 3,553 contracts
    our_position_within_level  UNKNOWN on every single order

No partial fills occurred in any window (`partial_fills: 0` throughout), so
fills were all-or-nothing at these sizes.

**We sit behind 760-7,778 contracts with a 1-2 contract clip and no
published queue position.** That, not latency, is what decides whether a
maker order fills.

## 5. Missed opportunities — could speed have completed the strands?

For each stranded leg, the shortfall is how many cents too expensive the
complement was, against how far the book actually moved between
observations:

| strand | need <= | n | shortfall min / p50 / max (cents) | per-sample move p50 / p90 / max (cents) |
|--------|--------:|--:|----------------------------------:|----------------------------------------:|
| 1024/0115z | 0.72 | 79 | **0** / 12 / 27 | 0.0 / 5.0 / 18.0 |
| 1024/0145z | 0.61 | 40 | 1 / 19 / 35 | 0.0 / 5.0 / 11.0 |
| 1024/0215z | 0.65 | 134 | **-64** / 6 / 32 | 0.0 / 3.0 / 55.0 |
| 1026/2100z | 0.70 | 21 | **24** / 28 / 28 | 0.0 / 1.0 / 2.0 |
| 1026/2115z | 0.79 | 66 | 1 / 13 / 17 | 0.0 / 2.0 / 5.0 |
| 1026/2130z | 0.44 | 65 | 1 / 24 / 34 | 0.0 / 3.0 / 5.0 |

**Answer: no, for the strands that lost money.** 2100z needed the complement
24-28 cents cheaper while the book moved 0 cents at p50 and never more than
2 cents between samples. No reaction time closes that. 2115z and 2130z were
within 1 cent at their best instant but 13 and 24 cents at p50, and a 1-cent
touch does not imply a fill from the back of a 4,143-contract queue.

0115z reached shortfall **0** — momentarily joinable at exactly the cap — and
0215z reached **-64**, deeply completable. 0215z is the window that settled
**+$0.8625** by being held. Acting faster there would have locked roughly
+$0.05 and destroyed the best outcome in the record. Faster execution would
have helped where it did not matter and hurt where it did.

Resolution caveat, stated plainly: the finest book series available is the
~6.6s residual sampler, so an affordable excursion shorter than that is
invisible to this analysis. For 2100z (24-cent gap, 2-cent moves) such an
excursion is implausible. For 2115z and 2130z (1-cent best gap) it cannot be
ruled out.

## Largest bottleneck and its estimated impact

**Ranked by avoidable delay:** (1) nothing in the data path — 0.041s;
(2) submission, 0-4.3s, unmeasurable; (3) order polling, 6.65s, ours;
(4) the 32s evaluation gap, which is L5's activity rate and not ours.

**Estimated P&L impact of removing every latency above: approximately
$0.00, and plausibly negative.** The six strands needed price moves of
1-28 cents that did not occur, not seconds. Of 13 observed fills, every one
came from a resting order filling over 8.9-83.1s of queue wait; arriving
~4s earlier changes a 17s median wait by under a quarter and changes no
fill/no-fill outcome in the record.

Plausibly negative because evaluating more often means opening more first
legs, and post-pair openings are 0-for-3 and cost -$0.7458 across the two
Oct 26 windows. **Under the current policy, more speed buys more adverse
selection.** Latency is not the constraint on matched quantity; queue
position behind 760-7,778 contracts is.

## Low-cost fixes worth doing, in priority order

1. **Record the venue's ack and fill timestamps.** `venue_event_t` and
   `processing_lag_s` are null on every fill row, so true submission and
   fill latency are unmeasurable today. Reporting-only, no gate, and it
   closes the one blind spot that prevents this analysis being conclusive.
2. **Correct the residual sampler's 5.0s -> 6.56s drift.** Reporting-only,
   ~30% overhead, no P&L effect.
3. **Do NOT shorten the evaluation path or chase the 32s gap.** It is L5's
   activity rate, and under the current policy acting more often increases
   strand creation. Revisit only after the first-leg discipline change is
   validated.
4. **Do NOT pursue latency as a route to more matched quantity.** The
   evidence points at queue position and clip size, and `our_position_
   within_level` is UNKNOWN on every order — which is the measurement to add
   before any speed work could be justified.
