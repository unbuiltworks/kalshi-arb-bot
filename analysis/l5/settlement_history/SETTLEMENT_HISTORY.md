# PMUS BTC15m settlement history: how far it reaches, and what the outcomes do

**Dataset 1 of 3. Settlement outcomes only.** There is no book, no executable
price and no fee anywhere in this study. It is valid for base rates, streaks and
transitions. It **cannot** establish that any betting strategy is profitable, and
no figure in it is P&L.

The three datasets are kept apart and are never pooled:

| # | Dataset | What it is for | Status |
|---|---|---|---|
| 1 | **Settlement history**, 1,167 resolved windows | base rates, streaks, transitions | this document |
| 2 | **178-window generating sample** | executable economics; generated the reversal hypothesis and is permanently retired from validating it | unchanged |
| 3 | **43-window independent executable holdout** (2026-10-02 00:00z–10:45z) | frozen 885-second rule | unchanged |

The per-second **2026-10-01 executable tape remains swept and unrecoverable**.
Settlement history does not replace it and is not offered as a substitute: it
carries no 885-second price, so it cannot test the frozen rule at all.

## How far back the records actually reach

The app UI stops at October 1. **The API does not.** Enumerated backward in exact
15-minute increments from 2026-10-05 01:00Z:

```
walked windows                1,480
EXISTS                        1,168   (200 + the row's own slug matched)
ABSENT                          312   (venue answered 404)
ERROR                             0   (failed to ask -- none occurred)
oldest existing window        2026-09-22T15:15:00Z
stop reason                   288 consecutive ABSENT back to 2026-09-19T15:15Z
boundary confirmation         120 further daily probes, ALL absent
```

So the series begins at **2026-09-22 15:15Z** and runs to the present: about 13
days, roughly nine days earlier than the UI suggests, and there is no deeper
history to find. The boundary was **tested, not assumed** — after the walk
stopped, one window per day was probed for 120 further days and none existed.

`EXISTS`, `ABSENT` and `ERROR` are three different things here and were never
collapsed. A 404 is an answer and ends nothing on its own; only a run of 288
consecutive 404s (three days) stops the walk, because the gateway has previously
404ed a *live* window for sixteen minutes and because a genuine multi-day pause
in the series must not be mistaken for its beginning. A transport failure or
5xx is retried with backoff and then recorded as `ERROR`, which can never extend
an absent run. **Zero ERRORs occurred**, so no window's status is in doubt.

## What was preserved, and how each settlement can be audited

`data/pmus_settlement_history/settlements.jsonl.gz` — one row per window with
slug, market id, venue `windowStart`/`windowEnd`, `startDate`/`endDate`,
`priceToBeat`, final `settlementPrice`, `outcomePrices`, `status`, the derived
outcome and its source. `raw_responses.jsonl.gz` — **every response body
verbatim**, with route, status and fetch time, so every settlement traces back to
the bytes the venue sent.

One request per window suffices: `/v1/market/slug/{slug}` carries the settlement,
so `/bbo` was never asked. The slug form matters — `cpc-btc-updown-15m-...` is
served; the bare `btc-updown-15m-...` form in the app's URLs **404s** on this
route, because it is the *event* slug.

### The outcome is cross-checked, not merely read

The outcome is taken from the venue's own resolution (`outcomePrices`), and
independently recomputed from the rule the market's own description states — Up
iff the closing BRTI 60-second average is at least the opening one.

```
agree        1,163
disagree         0
incomplete       5   (4 resolved windows lack settlementPrice; 1 was still RESOLVING)
```

**Zero disagreements.** The arithmetic is a check and never fills in an outcome
the venue has not stated: the one window still in `MARKET_STATUS_RESOLVING`
(10-05-0100z, the in-flight one) is left unresolved rather than inferred from its
`priceToBeat`. Four resolved windows are not independently cross-checkable
because the venue did not publish their `settlementPrice`; their outcomes come
from the venue's resolution alone, and that is disclosed rather than hidden.

Two further integrity checks passed: the slug-derived window start matches the
venue's own `windowStart` on **all 1,168** rows, and no two slugs resolved to the
same market id. All rows are `15m` horizon on `BRTI`.

### Missing intervals, named rather than papered over

Three gaps, each exactly 8 windows (two hours), each 09:45Z → 12:00Z:

| After | Before | Missing |
|---|---|---|
| 2026-09-24 09:45Z | 2026-09-24 12:00Z | 8 |
| 2026-09-25 09:45Z | 2026-09-25 12:00Z | 8 |
| 2026-10-01 09:45Z | 2026-10-01 12:00Z | 8 |

That is 24 interior absences — a recurring maintenance window, present on three
of thirteen days. **A gap breaks a streak; it is never treated as an outcome.**
Statistics are therefore reported two ways: over the largest *continuous* run,
and over the whole resolved series with every streak broken at every gap.

## Base rates and streaks

| | Largest continuous block | Whole series, streaks broken at gaps |
|---|---|---|
| windows | 568 (09-25 12:00Z → 10-01 09:45Z) | 1,167 (09-22 15:15Z → 10-05 01:00Z) |
| UP | 296 (52.11%) | 591 (50.64%) |
| DOWN | 272 (47.89%) | 576 (49.36%) |
| longest UP streak | 11 | 11 |
| longest DOWN streak | 6 | **16** |

The base rate is close to a coin flip over the full series: 50.64% UP. The
largest block's 52.11% is the same UP drift already seen in the executable work,
not a new finding.

### Streaks reaching at least k (counting streaks, not windows)

Whole series, gap-broken:

| k | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|
| UP | 147 | 70 | 32 | 15 | 10 | 7 | 3 | 3 | **2** |
| DOWN | 150 | 65 | 30 | 13 | 6 | 2 | 1 | 1 | **1** |

### The direct answer

> **UP has occurred 10 or more consecutive times twice. DOWN has occurred 10 or
> more consecutive times once.**

The three occurrences, exactly:

| Outcome | Length | First window | Last window |
|---|---|---|---|
| UP | 10 | 2026-09-26 13:30Z | 2026-09-26 15:45Z |
| UP | 11 | 2026-09-28 09:45Z | 2026-09-28 12:15Z |
| DOWN | **16** | 2026-10-02 13:45Z | 2026-10-02 17:30Z |

None of the three crosses a gap: the gaps fall on 09-24, 09-25 and 10-01, and
these runs fall on 09-26, 09-28 and 10-02.

### Every streak of 6 or more (whole series, gap-broken)

| Outcome | Length | First window | Last window |
|---|---|---|---|
| UP | 6 | 2026-09-23 18:30Z | 2026-09-23 19:45Z |
| DOWN | 6 | 2026-09-23 23:00Z | 2026-09-24 00:15Z |
| DOWN | 6 | 2026-09-24 00:45Z | 2026-09-24 02:00Z |
| DOWN | 7 | 2026-09-24 08:00Z | 2026-09-24 09:30Z |
| UP | 7 | 2026-09-24 12:45Z | 2026-09-24 14:15Z |
| DOWN | 6 | 2026-09-24 16:30Z | 2026-09-24 17:45Z |
| UP | 10 | 2026-09-26 13:30Z | 2026-09-26 15:45Z |
| UP | 7 | 2026-09-27 06:45Z | 2026-09-27 08:15Z |
| UP | 7 | 2026-09-27 17:15Z | 2026-09-27 18:45Z |
| UP | 11 | 2026-09-28 09:45Z | 2026-09-28 12:15Z |
| UP | 7 | 2026-09-28 14:45Z | 2026-09-28 16:15Z |
| DOWN | 6 | 2026-09-30 05:45Z | 2026-09-30 07:00Z |
| UP | 9 | 2026-09-30 08:45Z | 2026-09-30 10:45Z |
| UP | 6 | 2026-10-01 08:15Z | 2026-10-01 09:30Z |
| DOWN | 16 | 2026-10-02 13:45Z | 2026-10-02 17:30Z |
| UP | 6 | 2026-10-03 16:30Z | 2026-10-03 17:45Z |

## Conditional continuation after a run of length k

Whole series, gap-broken. A frequency, not an edge.

| k | UP continued | freq | DOWN continued | freq |
|---|---|---|---|---|
| 1 | 147/300 | 0.490 | 150/300 | 0.500 |
| 2 | 70/146 | 0.480 | 65/149 | 0.436 |
| 3 | 32/70 | 0.457 | 30/65 | 0.462 |
| 4 | 15/32 | 0.469 | 13/30 | 0.433 |
| 5 | 10/15 | 0.667 | 6/13 | 0.462 |
| 6 | 7/10 | 0.700 | 2/6 | 0.333 |
| 7 | 3/7 | 0.429 | 1/2 | 0.500 |
| 8 | 3/3 | 1.000 | 1/1 | 1.000 |
| 9 | 2/3 | 0.667 | 1/1 | 1.000 |

The high continuation rates at k ≥ 5 rest on single-digit denominators — 3/3 at
k = 8 is three events. Nothing at k ≥ 5 is distinguishable from noise, and the
apparent rise with k is what a coin flip looks like once the sample thins. At
k = 1–4, where the denominators are 30–300, continuation sits slightly **below**
0.5 on both sides.

## The part of this series that is not contaminated by dataset 2

The 178-window generating sample and the 43-window holdout both lie on or after
2026-10-02, so their outcomes are a **subset** of this series. Streak statistics
computed over the whole 1,167 windows therefore cannot validate anything derived
from those windows.

The 875 resolved windows **strictly before 2026-10-02 00:00Z** are outside both
(the six 09-30 development windows aside, which is disclosed). On that region:

| | windows | UP | P(UP \| prev UP) | P(UP \| prev DOWN) | difference |
|---|---|---|---|---|---|
| fresh pre-10-02 region | 875 | 50.63% | 0.4966 (219/441) | 0.5163 (222/430) | **+1.97 pp**, z = +0.58, p = 0.56 |
| whole series | 1,167 | 50.64% | 0.4924 (290/589) | 0.5209 (299/574) | +2.85 pp, z = +0.97, p = 0.33 |

For comparison, and **for comparison only**: the 178-window generating sample
showed +10.3 pp (p = 0.171). On 875 windows that never generated the hypothesis,
the same contrast is **+1.97 pp and nowhere near significant**.

This is a directional base-rate observation, not a backtest. It carries no price,
no fee and no fill, so it cannot say what a reversal rule would have earned. What
it does say is that the 10.3 pp gap which motivated the reversal hypothesis
shrinks by about four fifths on fresh windows, which is the behaviour of a
sampling artifact rather than a persistent asymmetry. Dataset 3's frozen
executable test remains the only thing that can speak to the rule's economics,
and 43 windows can refute a large effect but cannot confirm a small one.

## Executable price information for these same markets

Open question, deliberately not answered here. Whether book, trade or
intra-window price history is recoverable for the 1,167 markets is the next
investigation, and **no 885-second price has been fabricated for any window that
lacks one**. Anything reconstructed from a midpoint, a last trade or a future
book state would be labelled non-executable and reported separately.
