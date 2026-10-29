# What actually paid L5 — raw credits first, model never

OWNER priority #1: "Re-derive the raw L5 incentive credits before fitting any
reward model. We need to establish what the ~$35.2K MAKER_REBATE, ~$28.8K
REWARD, and ~$7.5K TAKER_REBATE rows actually represent, their dates/cadence,
and which historical programs could mathematically explain them."

Source: `analysis/l5/l5_ledger.json` -> `income_rows`, 200 rows carrying a
unix `ts`, a `type` and a `usdc` amount, harvested from L5's own public
activity. No model is fitted anywhere in this document.

## 1. The totals, corrected

| stream | rows | credited |
|---|---:|---:|
| MAKER_REBATE | 109 | **$33,929.41** |
| REWARD | 19 | **$26,956.26** |
| TAKER_REBATE | 72 | **$7,322.73** |
| **total** | **200** | **$68,208.40** |

The working figures in circulation were ~$35.2K / ~$28.8K / ~$7.5K. The ledger
says 33,929.41 / 26,956.26 / 7,322.73. I had been quoting the rounded set;
these are the rows.

## 2. Cadence: three daily programs with three different start dates

Every stream pays **once per day, at 00:00 UTC** (MAKER 94 of 109 credits at
hour 0; REWARD 18 of 19; TAKER 67 of 72). Day-gap histograms are dominated by
1, i.e. consecutive days. These are **daily pool distributions**, not per-fill
commissions.

| stream | first credit | last credit | distinct days | median credit |
|---|---|---|---:|---:|
| MAKER_REBATE | 2026-04-21 | 2026-08-30 | 109 | $93.63 |
| TAKER_REBATE | 2026-06-20 | 2026-08-30 | 56 | $76.41 |
| REWARD | **2026-08-08** | 2026-08-30 | 19 | **$1,622.24** |

The start dates are the era boundaries, and they were not chosen by me — they
are simply where each stream's first credit lands.

## 3. 82% of everything L5 was ever paid arrived in the last 21 days

| era | days | MAKER | TAKER | REWARD | total | **$/day** |
|---|---:|---:|---:|---:|---:|---:|
| A — MAKER only (Apr 21 – Jun 19) | 52 | 3,907 | 0 | 0 | 3,907 | **65** |
| B — +TAKER (Jun 20 – Aug 7) | 37 | 4,687 | 3,574 | 0 | 8,261 | **169** |
| C — +REWARD (Aug 8 – Aug 30) | 21 | 25,335 | 3,749 | 26,956 | **56,040** | **2,437** |

$56,040 of $68,208 lands in era C. And MAKER_REBATE itself steps 16x within
its own stream: $75/day in era A to $1,206/day in era C. So era C is not
merely "a new REWARD program appeared" — the maker program's payout scale
changed at the same time.

Typical era-C day (2026-08-26): MAKER 1,579.55 + TAKER 339.46 + REWARD
2,303.37 = **$4,222.39 in incentives alone**.

## 4. This dissolves the "$0.02314/contract, 6.6x the published maximum" flag

That figure came from $35.2K of MAKER_REBATE divided by 1,520,948 filled
shares, and it read as impossible against today's Polymarket maker program.

It was never a per-contract rate. These are **daily pooled distributions**
credited at 00:00 UTC, so dividing a pool share by lifetime contracts produces
a number with no program to compare against. Worse, the blended figure mixes
three programs operating at three different scales across three eras.

The flagged contradiction is therefore **resolved as a category error in our
own arithmetic**, not as evidence that the historical program paid 6.6x. It
also means our measured PMUS coefficient (0.0125 x qty x p x (1-p), validated
print-by-print against 25 credits) and L5's MAKER_REBATE are **not the same
kind of quantity** and must never be compared directly again: ours is a
commission credited on the fill, L5's is a share of a daily pool.

Which historical program explains them is still **UNRESOLVED**, but the shape
now constrains the answer: any candidate must be a daily, pool-share program,
live from 2026-04-21 (maker), 2026-06-20 (taker) and 2026-08-08 (reward).

## 5. Did L5's behaviour change with reward intensity? Yes — as BREADTH

Joining daily incentive to L5's own per-window ledger
(`l5_ledger_windows.csv.gz`, 16,501 windows):

| era | days | inc $/day | windows/day | fills/window | buy $/day | pair cost (med) | matched % | opposite gap (med) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 52 | 61 | 30.6 | 93.09 | 23,602 | 0.9660 | 99.3 | 30.0 s |
| B | 37 | 146 | 107.0 | 38.20 | 39,695 | 0.9852 | 72.8 | 51.0 s |
| C | 21 | 2,296 | **400.6** | 39.82 | **175,414** | 0.9759 | 73.5 | 54.0 s |

As incentives rose ~37x from era A to era C:

- **windows worked per day went 30.6 -> 400.6 (13x)**
- **capital deployed per day went $23.6K -> $175.4K (7.4x)**
- fills per window did **not** rise (93 -> 38 -> 40)
- pair cost moved ~2 cents (0.966 -> 0.985 -> 0.976)
- completion patience was flat B->C (51 s -> 54 s)

L5's answer to more incentive was **more markets, not bigger or more
aggressive sequences**. The per-window behaviour change happened between A and
B — fills/window 93->38, matched 99.3%->72.8%, gap 30s->51s — which tracks the
TAKER program, not the REWARD one.

That is the promotion order in `L5_OPERATING_MODEL.md`
(PATIENCE -> FREQUENCY -> SIZE -> CONCURRENCY) showing up in L5's own history:
frequency and breadth first, size essentially never.

### What this evidence does NOT support

- **Coverage.** The window ledger is **100% crypto up/down in all three
  eras**. This is L5's crypto lane, not all of L5's activity. Nothing here
  licenses a claim about his sports or other categories.
- **Dose-response inside an era is not established.** Same-day correlation of
  incentive against activity rises A +0.14, B +0.22, C +0.59 — but credits
  post at 00:00 UTC for the PRIOR day, so same-day correlation is
  activity -> payout, which is mechanical.
  Lagging incentive by one day (payout -> next-day activity) gives A -0.01,
  B +0.12, C **+0.45** — the right direction, but n=16 days. That is a
  hypothesis to carry, not a result.
- **Causation.** Era C is 21 days. A program launch coinciding with a
  behaviour change does not establish which drove which.

## 6. What this means for the $480/day target

The $480/day figure belongs to a trader who, in the era that produced it, was
being paid **$2,437/day in incentives alone** while deploying $175K/day across
400 markets/day. Reproducing L5's decision policy without that incentive
regime reproduces a materially different business, and the gap is not a
detail — in era C, incentives are roughly five times the daily profit target.

The honest framing for our own ramp: the L5 behavioural policy is worth
replicating on its execution merits, and the incentive economics have to be
earned separately from whatever programs are actually live for our account
today. They are not the same question and this document does not merge them.
