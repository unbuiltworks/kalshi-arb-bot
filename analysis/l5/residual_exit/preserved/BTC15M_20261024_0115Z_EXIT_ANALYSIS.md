# 0115z, the −$0.2304 residual: what every alternative would have paid

**Preserved tape: `btc15m_20261024_0115z.ndjson`, 117 live records,
01:15:05.806Z → 01:31:24.468Z — arm through venue settlement.** Pulled from
the deploy stream before retention could age it out. The authoritative source
remains the service's own `decisions.jsonl` on the `/data` volume; this is a
second, repo-durable copy. Contains 79 book-bearing decisions, 6 pre-submits,
7 lifecycle records, the recovery, the cancel, the reconciles and the
settlement. Shadow/controller records excluded.

## The position

DOWN **0.96** contracts @ own **0.24** (wire 0.76), cost **$0.2304**, filled
**01:16:54.425**, commission 0.0000. Pairing inside the 0.96 cap required
UP own ≤ **0.72**.

## Every observed book state after the fill, and what a cross would have paid

`DNbid = 1 − UPask` is what selling our DOWN realizes. `cross_net` is
`0.96·DNbid − 0.2304 − taker_fee`, taker θ=0.0695 on the PMUS curve
`θ·p·(1−p)`. Depth is the displayed opposite side at that instant.

| time | UPbid | UPask | ask depth | DNbid | pair @ UP touch | cross net |
|---|---|---|---|---|---|---|
| 01:17:10 | 0.79 | 0.80 | 3213.51 | 0.20 | 1.03 | −0.0491 |
| 01:17:50 | 0.75 | 0.76 | 365.06 | 0.24 | 0.99 | −0.0122 |
| 01:18:14 | 0.84 | 0.85 | 5796.50 | 0.15 | 1.08 | −0.0949 |
| 01:18:46 | 0.83 | 0.84 | 181.01 | 0.16 | 1.07 | −0.0858 |
| 01:19:49 | 0.90 | 0.91 | 3597.75 | 0.09 | 1.14 | −0.1495 |
| 01:20:21 | 0.75 | 0.76 | 4903.60 | 0.24 | 0.99 | −0.0122 |
| **01:20:53** | **0.72** | **0.73** | **1227.73** | **0.27** | **0.96** | **+0.0156** |
| 01:21:59 | 0.73 | 0.74 | 1540.00 | 0.26 | 0.97 | +0.0064 |
| 01:23:02 | 0.91 | 0.92 | 464.17 | 0.08 | 1.15 | −0.1585 |
| 01:23:34 | 0.94 | 0.95 | 4299.43 | 0.05 | 1.18 | −0.1856 |
| 01:24:06 | 0.94 | 0.95 | 18.19 | 0.05 | 1.18 | −0.1856 |
| 01:24:37 | 0.94 | 0.95 | 4320.10 | 0.05 | 1.18 | −0.1856 |
| 01:25:09 | 0.90 | 0.91 | 4271.46 | 0.09 | 1.14 | −0.1495 |
| 01:25:49 | 0.82 | 0.83 | 6350.50 | 0.17 | 1.06 | −0.0766 |
| 01:26:13 | 0.75 | 0.76 | 2932.15 | 0.24 | 0.99 | −0.0122 |
| 01:27:23 | 0.81 | 0.82 | 1275.00 | 0.18 | 1.05 | −0.0674 |
| 01:27:53 | 0.95 | 0.96 | 6042.20 | 0.04 | 1.19 | −0.1946 |
| 01:28:24 | 0.96 | 0.97 | 424.22 | 0.03 | 1.20 | −0.2035 |
| 01:28:54 | 0.99 | 0.99 | 252.36 | 0.01 | 1.23 | −0.2215 |

## The four alternatives, answered

**1. Hold DOWN to settlement — actual, −$0.2304.** Venue-confirmed,
`residual_redeem 0.000000`, BTC settled UP.

**2. Sell DOWN at an executable bid.** Best at **01:20:53: +$0.0156**, a
**+$0.246** swing against what happened. Displayed ask depth there was
**1227.73** shares against the **0.96** needed, so this is a *guaranteed*
taker execution at the displayed price, not a hoped-for one.

**And the stronger result: a cross at EVERY observed tick in this window beat
holding.** The range is −$0.2215 to +$0.0156 against −$0.2304 held. There was
no moment at which crossing was worse than carrying, including the very last
tick with UP at 0.99.

**3. Partial UP hedge.** Dominated, per `INVENTORY_AWARE_VERDICT_20261024.md`:
per-unit net is linear in the hedge fraction, so the mean sits on a straight
line between hold and full exit and the tail improves monotonically with the
fraction. On this window, any fraction f pays
`f·(cross net) + (1−f)·(−0.2304)` — strictly worse than f=1 at every tick.

**4. Full UP hedge even if it locks a small loss — the same trade as (2).** On
a single-instrument venue, buying UP at the ask and selling DOWN at the bid are
one order: at 01:20:53, buying UP 0.96 @ 0.73 makes pair cost
0.2304 + 0.7008 = 0.9312 against 0.96 redeemed, i.e. +0.0288 gross less the
0.0132 taker fee = **+$0.0156**, identical to the cross. This is the venue
mechanic that makes L5's "never sells" irrelevant to us.

**5. Earlier adjustment on then-available information.** The engine *did* find
the one reachable moment: at **01:20:53**, with the UP touch at 0.72 making
pair-at-touch exactly **0.96**, it submitted the maker complement at own 0.70
(pair 0.94). It did not fill, and 16 seconds later the touch had moved to 0.77,
7 ticks away — which is what the stale-recovery gate then cancelled with zero
fill at 01:21:10, confirmed terminal 01:21:14 (4.13 s). So the maker route was
identified, attempted, and lost to the move. **No maker fill is claimed here:
the order rested at a price the book reached and still did not fill, which is
the direct evidence for why touch must never be scored as a fill.**

## What this window does and does not establish

**Establishes:** the loss was avoidable, the avoiding action was a *taker*
cross, it was executable against displayed depth, and it was available for at
least 66 seconds (01:20:53 → 01:21:59) and in weaker form at every other tick.
The maker-only mandate is what made the loss unavoidable, not the inventory
policy and not the recovery gate.

**Does not establish:** that a defensive taker exit is net-positive as a
policy. One window cannot carry that, and the cost side is real — crossing pays
the taker fee, forfeits the maker rebate, and on windows where the complement
*would* have completed it destroys the pair profit. The frozen-holdout
comparison is the next step, and it must charge those costs.

**Not changed:** nothing. Execution remains MAKER_ONLY, no taker hedging, no
accumulation, no new exit behaviour. This is read-only analysis of a settled
window.
