# Corrected Kalshi verdict — **NO LIVE PROBE** (unchanged conclusion, new reason)

2026-10-01. Supersedes the reasoning in `LIVE_PROBE_GATE_VERDICT.md`
(commit 98f4c955); see `LIP_SCORING_CORRECTION.md` for what was wrong and
`DESIGNATED_LP_PROGRAM.md` for C. No order submitted. PMUS untouched. No new
strategy started.

The previous verdict's arithmetic was invalid — it understated the true q1
ceiling by about 5x. Recomputing under Kalshi's actual published scoring does
not rescue a probe; it kills it for a cleaner reason.

## The decisive fact

Kalshi does not pay rewards below **$1.00** per program. Under the documented
scoring, **no q1 order anywhere on the venue currently reaches $1.00 on the
conservative reading** (best: $0.73), and only 6 of the top 150 programs reach
it even optimistically. So a q1 probe is credited **exactly $0.00 by
construction**.

The probe's authorized learning objective was *the cost of producing $1 of
venue compensation*. At q1 that quantity is not measurable, because the venue
floors q1 compensation at zero. The probe cannot teach what it was authorized
to teach, so it should not be run — independent of whether the economics are
attractive.

## A. Ordinary-user Kalshi BTC/ETH 15m

| | |
|---|---|
| Maximum defensible q1 reward / period | **$0.00** — exact, not a bound |
| Expected / conservative q1 reward / period | $0.00 |
| Reward per capital-hour | $0.00 |
| Qualifying probability | not applicable — no program to qualify for |
| Adverse selection to break even | **any loss at all is unrecoverable**; there is no revenue line to offset it |
| Plausible path to >= $3 net / opportunity | **None.** Not a capacity limit — a structural absence |

KXBTC15M: 9,781 programs in the dump, 0 active, 0 upcoming. KXETH15M: 8,299,
0 and 0. The live book is one tick wide with ~302k/~311k contracts resting
(see C). This is SOURCE-OBSERVED, and it is the whole answer for A.

## B. Best ordinary-user Kalshi incentivized market

Best candidate of the top 150 active programs by pool:
**`KXTRUMPAPPROVE-26OCT02-E38.5`, NO side**, Reference Price 0.93, side score
410.8, pool $600, period 24h.

| | |
|---|---|
| Maximum defensible q1 reward / period | **$1.4570 / 24h** (optimistic reading, 100% uptime, zero excluded snapshots) |
| Expected / conservative q1 reward / period | **$0.00** — the conservative figure is $0.7285, which is below the $1.00 minimum payout and is therefore **not paid** |
| Reward per capital-hour | $0.0653 optimistic on $0.93 capital; **$0.00 conservative** |
| Qualifying probability | **UNKNOWN.** Two-sided Target Size is met at the observed instant, but the non-excluded snapshot fraction needs per-second history we do not collect |
| Adverse selection to break even | optimistic: **unreachable** — max loss on the contract is $0.93 < $1.4570 credit. conservative: **$0.00 of loss is tolerable**, since the credit is zero |
| Plausible path to >= $3 net / opportunity | **Yes at q4–q7, no at q1.** 4 contracts (~$3.86 capital) clears $3 conservatively here; q1 cannot, at any market, under any reading |

Note the optimistic row is self-refuting: a credit exceeding the position's
entire capital at risk is a risk-free return, and the 6x cross-side score
asymmetry (NO 411 vs YES 2,467) that produces it would not survive a day of
maker competition. I treat it as evidence of a defective input, not an edge.
See `LIP_SCORING_CORRECTION.md`.

**The $3 capacity path is real enough to be worth stating and not worth acting
on.** It requires q4–q7, which exceeds the q1 constraint and in most cases the
$1.00 exposure cap, and every load-bearing input behind it is UNVERIFIED
(reward units, TimePeriodScore normalization) or ACCOUNT-OBSERVED-unobtainable
(eligibility, credited rewards). Resolving the units alone requires an account
credit we cannot observe. I am reporting the path, not recommending it.

## C. Designated-LP crypto 15m — informational, non-executable

Crypto 15min markets, termination 12/31/26, **2 designated providers**,
requiring an executed Market Maker Agreement and selection via reverse
auction. We do not assume eligibility, these payments are excluded from every
executable figure above, and no contact or status change was made or is
proposed.

Its explanatory value, by evidence class:

- absence of ordinary KXBTC15M incentives — **INFERRED-CONSISTENT**; a CFTC
  filing to wind down the Volume Incentive Program is an equally available
  explanation;
- existing depth and competition — **OBSERVED-CONSISTENT**; one-tick spread
  and ~302k/~311k resting contracts with no open subsidy is what a contracted
  obligation looks like;
- ordinary-user L5-style provision structurally disadvantaged there —
  **OBSERVED**. No subsidy to earn, no width to capture, and an unwinnable
  queue against participants paid separately to be in it.

## Gate

The existing gate (max $1.00 exposure, q1, maker-only, 1 market, 1 probe
sequence) is **not satisfied**, and the corrected economics do not
independently pass it. The q1 constraint and the $1.00 minimum payout are
jointly dispositive.

**NO LIVE PROBE.**
