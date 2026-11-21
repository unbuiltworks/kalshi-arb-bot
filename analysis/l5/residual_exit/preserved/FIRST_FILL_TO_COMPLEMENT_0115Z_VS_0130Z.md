# 0115z and 0130z: first fill to complement, without hindsight

Both tapes preserved as NDJSON in this directory, pulled from the deploy
stream before retention: `btc15m_20261024_0115z.ndjson` (117 records,
01:15:05.806Z → 01:31:24.468Z) and `btc15m_20261024_0130z.ndjson` (116
records, 01:30:02.532Z → 01:46:23.344Z). The service's own `decisions.jsonl`
on `/data` remains authoritative; these are repo-durable copies. Every book,
depth and queue figure below is as-of-tick; only the settlement outcome comes
from window end.

## Method correction, applied here

Two of my earlier readings of 0130z were wrong and both came from inferring
from filtered log queries instead of authoritative records:

* I reported "no UP complement was ever submitted". It was submitted and
  filled. An empty filtered query is not evidence of absence.
* I reported the first leg as DOWN own 0.56. **The first leg was UP own 0.29**;
  DOWN own 0.56 was the complement. My query window began at 01:31:25 and
  missed the UP pre-submit at 01:31:24.968 by 32 milliseconds.

Both are corrected below from `L5_FILL_CALIB_PRESUBMIT` and
`L5_LIVE_LIFECYCLE` terminal records, cross-checked against `L5_LIVE_SETTLED`
leg costs. **Rule adopted: an order is declared absent only when the
authoritative venue records — the lifecycle stream and the settlement leg
costs — agree it is absent.**

0130z carried exactly two live orders, `l5f-…00f071f3` and
`l5f-…63a15b21`, both filled. The five other pre-submits in that tape carry
dotted shadow coids and `CALIB_TERM filled=None`; they are calibration
records, not live orders.

## The two windows side by side

| | 0115z | 0130z |
|---|---|---|
| first leg | **DOWN own 0.24** (wire 0.76), 0.96 | **UP own 0.29**, 1.27 |
| submitted / filled | 01:16:37.326 / 01:16:54.425 (17.1 s) | 01:31:24.968 / 01:31:43.746 (18.8 s) |
| cap needs complement ≤ | UP 0.72 own | DOWN 0.67 own |
| pair @ touch at the fill | 0.24 + 0.77 = **1.01** (out of reach) | 0.29 + 0.68 = **0.97** (out of reach by 1 tick) |
| complement submitted | 01:20:53, UP own 0.70 (pair 0.94) | 01:33:34.767, DOWN own 0.56 (pair 0.85) |
| complement outcome | **cancelled zero-fill** 01:21:14.417 | **FILLED** 01:33:49.220, comm 0.0000 |
| unmatched interval | 01:16:54 → settlement (**never closed**) | 01:31:43.746 → 01:33:49.220 (**125.5 s**) |
| settled | residual DOWN, **−$0.2304** | matched pair 0.85, **+$0.1905** |

**Both first legs were the cheap side, and BTC rose in both windows.** The
difference is which side was held. 0115z held DOWN, so the rise pushed the
complement further out of reach. 0130z held UP, so the same rise made its
DOWN complement *cheaper* — from 0.68 own at the fill to 0.58 at +111 s. The
discriminator is direction **relative to the held side**, which is not
knowable at entry.

## When would a controlled-exit rule have fired?

Exit means selling the held leg at its own-side bid, charged the PMUS taker
fee `θ·p·(1−p)`, θ=0.0695. **The exit arithmetic is only valid while the leg
is genuinely unmatched.** After 0130z's complement filled at 01:33:49 the
position was a matched pair, so "sell the UP" is no longer an alternative to
holding — it is a different, worse-risk trade that re-opens naked exposure.
Numbers past that point are excluded rather than quoted.

### 0115z — valid for the whole window, complement never completed

Every observed tick after the DOWN fill, from
`BTC15M_20261024_0115Z_EXIT_ANALYSIS.md`: exit net ranged **−$0.2215 to
+$0.0156** against **−$0.2304** held. A cross at *every* observed tick beat
holding. A 30-second dwell rule would have fired at ≈01:17:24 with the UP
touch at 0.79 (pair 1.03, unreachable) and realized about **−$0.049** —
avoiding roughly **$0.181** of the loss.

### 0130z — valid only for 125.5 seconds, and the tape is thin there

| +s after fill | UP bid | DOWN touch | pair @ touch | exit net | vs held |
|---|---|---|---|---|---|
| +111 | 0.41 | 0.58 | **0.87** (reachable) | **+$0.1310** | −$0.0595 |

**There is exactly one book observation inside 0130z's unmatched interval**,
at +111 s, and by then the complement was comfortably inside the cap. I do
not have the book at +30 s, so **I cannot say whether a 30-second dwell rule
would have fired in 0130z**, and I am not going to assume it either way.

What the tape does support: a rule with a dwell threshold **above ~111 s**
would not have fired in 0130z at all, because the pair completed at +125.5 s.
A rule firing at or before +111 s would have cost about **$0.0595** of the
+$0.1905.

## Does any single rule avoid 0115z's loss and keep 0130z's profit?

On these two windows, a 30-second dwell rule would save ≈$0.181 and cost
≈$0.060 or more — net positive here. **That is not evidence and must not be
used as such**: two windows, one of which has a single usable observation in
its decision interval, and the rule's threshold would be chosen knowing both
outcomes. Fitting to them is exactly the error to avoid.

What these two windows legitimately contribute is the **shape of the cost**:
an exit rule tuned to rescue 0115z-type windows will fire on 0130z-type
windows too, because at the moment of decision they are indistinguishable —
both held the cheap leg, both had the complement out of reach by 1–5 ticks,
both were inside a rising market. Any holdout evaluation must therefore charge
the forfeited pair profit on completed windows, not score exits only where the
leg stranded.

## What this changes about the proposal

Nothing is enabled and nothing is fitted. The pre-registration for the exit
rule must be written **before** the holdout run and must state:

* the dwell threshold and the unreachability test, fixed in advance;
* that eligibility includes windows whose complement *later completed*, so the
  opportunity cost is charged rather than excluded;
* that a cross is scored only against displayed opposite-side depth at that
  tick, with the taker fee charged and no maker rebate assumed;
* that touch is never scored as a maker fill — 0115z's complement rested at a
  price the book reached and still did not fill;
* acceptance on lower-tail loss and drawdown, with the mean reported
  separately and not blended.

Execution remains `MAKER_ONLY`. No exit, no taker hedging, no accumulation,
no change to live inventory exposure.
