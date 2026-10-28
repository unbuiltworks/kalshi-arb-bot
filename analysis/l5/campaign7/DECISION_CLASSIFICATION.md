# Campaign 7: why 36 orders, and what actually blocked the rest

## The boundary, corrected

The authorization covered EIGHT windows. They were spent under TWO campaign
documents, and the P&L must be split accordingly:

| | windows | realized |
|---|---|---|
| 1930z, campaign `camp.482c58de…` under the superseded `cd831438` | 1 | **-$0.4224** |
| Campaign 7, `camp.04de35f5…` under `b95bd9c8` | 7 (2000z-2130z) | **+$1.0953** |
| combined | 8 | +$0.6729 |

Reporting Campaign 7 as +$0.6729 was an attribution error. Campaign 7 proper
is **+$1.0953**; the 1930z loss belongs to the aborted campaign that
`MANDATE_SCOPE_CHANGED` deadlocked.

## 392 decisions are not 392 opportunities

The decision loop re-evaluates the same intent every ~2 s. Collapsing
consecutive runs of the same key within the same window gives **82 distinct
episodes**, of which **27 submitted** (36 orders).

| key | ticks | episodes | class |
|---|---|---|---|
| ORDER:SUBMITTED | 36 | 27 | submitted |
| SAFETY_OVERLAY:MAX_LIVE_ORDERS | 80 | **24** | order already resting |
| PAST_TERMINAL / COMPLETE_CUTOFF / ENTRY_CUTOFF / NOT_A_COMPLETION | 121 | 12 | time expired |
| SAFETY_OVERLAY:MAX_CONCURRENT_MARKETS / RESIDUAL_ABOVE_CAP / NOT_ARMED | 21 | 7 | safety or inventory limit |
| PMUS_TRANSLATION:NOT_CURRENT_WINDOW | 18 | 5 | duplicate/stale evaluation |
| PAIR_COST_CEILING:PAIR_COST_ABOVE_CEILING | 53 | **4** | unprofitable, correctly rejected |
| TWO_SIDED_PROBE:ACCUMULATION_EXHAUSTED | 37 | 1 | strategy gate |
| TWO_SIDED_PROBE:FIRST_LEG_BELOW_MIN_PRICE | 25 | 1 | strategy gate |
| COMPLEMENT_DEPTH_INSUFFICIENT / TOO_LATE_TO_WITHDRAW | 2 | 2 | liquidity / timing |

## The 0.96 ceiling rejected nothing profitable

All 53 ceiling refusals priced the complement from the live book. Every one
was **above $1.00**: min 1.02, median 1.11, max 1.30. None fell in the
0.96-1.00 band. Loosening the ceiling to $1.00 would have captured **zero**
additional pairs, and each refusal was a guaranteed matched loss. The gate is
not the bottleneck and must not be loosened.

(The completion ceiling reads 0.95 on all 53 records, not 0.96.)

## 2045z: an armed window that did nothing

`2045z` armed at 20:45:00.785 and spent a window claim. It then produced
**zero submits and zero decisions**. There are no decision records at all
between 20:43:02 and 20:59:04 -- a 16-minute silence covering the whole
window. Every other window produced 2-11 submits. The arming record for
2045z is present in the same capture, so this is not a capture gap.

One of seven authorized windows, 14% of the campaign, idle. Root cause not
yet established; it is a defect, not a strategy choice.

## The shadow-versus-live residual "discrepancy" is sampling noise

Earlier this was reported as a ~300x discrepancy. That was wrong. The
independent unit is the WINDOW -- units inside a window share one settlement.

    five settled residual windows, 9.70 units
    observed total                 -$0.0035
    per-window sd                   1.1141      SE(sum) 2.4912
    shadow rate -$0.135/unit predicts -$1.3095
    difference 0.52 standard errors -> NOT distinguishable

Separating the shadow rate from zero needs **~72 settled windows**. Campaign
7 had 7. A live campaign cannot answer this question, and no live result at
this scale may be used to confirm or refute a shadow-derived rate.

Consequently the earlier claim that Campaign 7 gave "settled evidence
against" defensive completion is withdrawn. What is true is narrower: on this
one realized path, defensive completion would have cost ~$0.44-0.63 and
sacrificed two winning residuals. That is one sample, not an expectancy.
