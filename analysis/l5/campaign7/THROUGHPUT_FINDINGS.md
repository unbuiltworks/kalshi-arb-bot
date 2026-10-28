# Where the throughput actually goes — Campaign 6, measured

Reproduce with:

```
python3 -I analysis/l5/campaign7/throughput.py
python3 -I analysis/l5/campaign7/ceiling_refusals.py \
        analysis/l5/campaign6/tape_submits.jsonl
```

## Headline: the one-resting-order limit is not the bottleneck

| | 1600z | 1615z | 1630z | campaign |
|---|---|---|---|---|
| orders transmitted | 8 | 5 | 6 | 19 |
| fills | 6 | 2 | 4 | 12 |
| slot occupied, entry period | 110 s | 63 s | 63 s | **236 s** |
| entry period | 600 s | 600 s | 600 s | 1800 s |
| **occupancy** | 18% | 10% | 10% | **13%** |
| longest idle gap | 157 s | 167 s | **297 s** | — |
| `MAX_LIVE_ORDERS` refusals | 9 | 11 | 3 | **23** |

**The slot was free 87% of the eligible entry time.** A second concurrent
order adds capacity to a system using an eighth of what it has. The 23
refusals are bursts of contention at specific instants, not saturation.

What the 23 refusals wanted:

| role | n | meaning |
|---|---|---|
| `FLAT_OPEN` | 14 | blocked the **second leg of a pair plan** while leg 1 rested |
| `NEW_OPEN` | 5 | blocked a **new pair** while the slot was held |
| `COMPLEMENT` | 4 | blocked a **completion of held inventory** |

Only the 4 completion refusals are economically dangerous, and none of them
stranded: every Campaign 6 window finished with zero residual.

## The idle gaps are the gates refusing losses, not waste

The longest gap — 297 s of the 600 s entry window in 1630z, T+187 to T+484
— looked like the engine sitting on its hands. It was not. It evaluated
**17 times** in that span and was refused **every time** by
`PAIR_COST_CEILING:PAIR_COST_ABOVE_CEILING`.

Holding 1.93 DOWN at own 0.40, the UP complement at depth 2:

| time | book | UP own | effective pair |
|---|---|---|---|
| 16:33:38 | 0.6400/0.6500 | 0.6200 | **1.0200** |
| 16:34:44 | 0.8000/0.8100 | 0.7800 | **1.1800** |
| 16:35:17 | 0.8100/0.8200 | 0.7900 | **1.1900** |
| 16:36:57 | 0.7100/0.7200 | 0.6900 | **1.0900** |
| 16:37:30 | 0.6300/0.6400 | 0.6100 | **1.0100** |

A matched pair redeems exactly $1.00. Every one of those completions was a
**guaranteed loss** of 1 to 19 cents a share. The gate refused all 17, the
engine waited, UP fell back, and at T+484 it completed at 0.40 + 0.45 =
**0.85 for +$0.2895**.

So filling that gap would have meant manufacturing defensive losses.
**Occupancy of 13% is the gates working, not a constraint to relieve.**

## Correction to an earlier statement of mine

I previously reported that the $0.96 ceiling "refused zero openings and
structurally cannot bind". That was right about **one** of two gates and I
should have separated them:

- **Plan stage** (`PAIR_COST_ABOVE_CAP`, `l5_two_sided_probe.py:1128`):
  zero refusals, and structurally incapable of firing, since the plan pair
  is `1 − spread − 2·depth·tick` = exactly 0.9500 at a 1-tick spread and
  depth 2.
- **Submission boundary** (`PAIR_COST_ABOVE_CEILING`,
  `l5_live_execution.py:5673`): fired **17 times in one gap alone**,
  because it tests the *effective* pair including already-held inventory at
  its realised basis, not a fresh plan.

Same 0.96 constant for openings, different question. The second is the one
that shapes completion timing, and it is doing so correctly.

## Serialisation economics: a variance trade, not a throughput gain

Pair cost realised against the 0.9500 the plan projected:

| window | legs (own) | realised | vs plan on qty | gap |
|---|---|---|---|---|
| 1600z | 0.36 + 0.63 | 0.9900 | −$0.0752 | 31 s |
| 1600z | 0.45 + 0.42 | 0.8700 | +$0.1600 | 62 s |
| 1600z | 0.41 + 0.64 | 1.0500 | −$0.1940 | 18 s |
| 1615z | 0.46 + 0.09 | 0.5500 | **+$0.8040** | 313 s |
| 1630z | 0.40 + 0.45 | 0.8500 | +$0.1930 | 364 s |
| 1630z | 0.39 + 0.51 | 0.9000 | +$0.1075 | 17 s |
| | | | **+$0.9953** | |

Four of six pairs beat the plan; the serialised path netted **+$0.9953
better** than resting both legs at 0.95 would have locked.

**This is directional, not structural.** Campaign 6's windows fell, so a
late complement was cheap. The identical serialisation in Campaign 5 cost
**−$2.9034** in stranded legs when the windows did not fall. Resting both
legs simultaneously would have locked 0.95, forfeiting this +$0.9953 and
avoiding that −$2.9034: it trades variance for a smaller, steadier edge.
That is a risk-profile decision, not a throughput one, and the upside case
is conditional on the second leg filling at the plan price, which these
tapes cannot show.

## What actually limits pairs per window

Not the slot (13% occupancy). Not the plan ceiling (cannot bind). Not
capital (peak cash headroom $10.14 of $12). Not L5 observation cadence (the
engine evaluated 17 times inside the quietest gap).

It is **market opportunity against queue depth**: how often a 1-tick book
offers a pair at or under 0.96 *and* a passive order of ours clears
1,173–7,629 contracts of queue ahead. Campaign 6 got 6 pairs from 3
windows — 2 per window — and every window that filled anything completed
its pairs.

## Recommendation: scale duration, change nothing else

The highest-impact lever for more profitable completed pairs is **more
eligible windows**, because:

1. **It cannot increase maximum loss.** The $5 daily realized-loss halt
   bounds the whole day regardless of window count. Twenty windows have the
   same worst case as four.
2. **Per-window risk is unchanged** — same $20 exposure, $12 envelope, one
   resting order, maker-only, same gates.
3. **It needs no code change.** `L5_LIVE_GATES` already reports
   `max_windows: 20`; Campaign 6 simply authorized 4.
4. Campaign 6 averaged **+$0.53 per trading window** over 3 windows. That
   is a thin sample and must not be extrapolated as a rate, but duration is
   the only lever that multiplies pairs without touching exposure,
   concurrency, capital or any gate.

**Concurrency is not recommended yet.** On throughput grounds the evidence
is against it: the slot is idle 87% of the time. On variance grounds there
is a real case for locking the plan pair, but it is a separate argument
needing its own validation, and it would have cost Campaign 6 about a
dollar of realised edge. Not requested here.
