# Sports lane economics, venue-confirmed — 2026-11-18T14:08Z

Read-only. Every figure is from the live `PMUS_TWO_SIDED` audit lines on
`pmus-spring-l5-probe` (deployed SHA `ac18a20bf`, deployment `bab3f650`,
2026-11-18T14:04Z), which read the venue's own activity rows.

## The headline: sports is losing money, and the gate that would stop it is off

Window: since 2026-11-14T15:30Z. 1,130 of our activity rows.

    orders submitted                        8,963
    venue-confirmed filled contracts     1,736.87
    completed pairs                        831.15
    gross pair margin                     -$1.7765
    fees charged                          +$4.3600 (cost)
    maker/taker rebates credited              $0
    residual settled                     -$36.9664
    unmatched cost OPEN                      $0.0000  (venue_reconciled)
    SETTLED REALIZED (venue rows)        -$29.3039  over 47 markets
    SETTLED REALIZED (own book)          -$34.0171  over 139 markets

`INVENTORY_RECONCILE` at 14:07:57Z: `ok: true`, `reconciled: true`,
`venue_positions_nonzero: 0`, `mismatch: 0`,
`sports_open_unmatched_cost_usd: 0.0000`.

**The $1.60 unmatched sports position is closed.** Open unmatched is
$0.0000, venue-reconciled, with zero position mismatches. The $36.2485
"lifetime unmatched" is a cumulative figure over settled markets and is
explicitly *not* exposure.

## Every sport measured has NEGATIVE expected value

`open_gate` reports the per-sport EV the opening gate would evaluate.
`on: false` — it is measuring and refusing nothing.

| sport | EV | n | | sport | EV | n |
|---|---|---|---|---|---|---|
| itfwo | **-0.3930** | 11 | | nba | -0.2317 | 7 |
| setkameua | **-0.3250** | 18 | | setkamecz | -0.2139 | 2 |
| engnl | -0.2862 | 1 | | wta | -0.2161 | 1 |
| wtadb | -0.2840 | 1 | | atp | -0.2033 | 13 |
| acb | -0.2814 | 1 | | nhl | -0.2008 | 4 |
| ligaf | -0.2788 | 1 | | cs2 | -0.1958 | 19 |
| testcr | -0.2777 | 1 | | setkamemd | -0.1634 | 7 |
| atpdb | -0.2770 | 3 | | dota2 | -0.1295 | 7 |
| itfme | -0.2761 | 5 | | utr | -0.1151 | 3 |
| cbb | -0.2690 | 1 | | | | |
| cfb | -0.2393 | 33 | | | | |

**20 of 20 negative.** Several are n=1 and carry no weight on their own,
but the five with real n all are too: cfb (n=33, -0.239), cs2 (n=19,
-0.196), setkameua (n=18, -0.325), atp (n=13, -0.203), itfwo (n=11,
-0.393). That is 94 of the 139 settled markets, and the sign is the same
everywhere. This is not a thin-sample artifact.

It is also consistent with the settled totals: setkameua -$7.61,
cfb -$6.56, itfwo -$6.43, cs2 -$6.09 are the four worst sports, and they
are four of the five largest-n cohorts.

Concentration: `aec-cs2-brute-eid-2026-11-15` alone lost **-$6.00**, and
since recovery was enabled the lane is -$17.2317 over 54 markets.

## Rewards do not rescue it, and the attribution is not established

`REWARD_CHECK` against the venue's `/v1/incentives/earnings`:

    rows                             158   (all liquidityProgram)
    PAID    (credited, = money)   $14.13   over 89 rows
    PENDING (not money yet)        $0.89   over 11 rows
    SKIPPED                        $3.47   over 58 rows
    rows on OUR markets               50   of 158

**$14.13 is credited and is real money.** But only 50 of the 158 rows are
on markets this bot traded, and the line does not split the PAID dollars
between our markets and the owner's manual sports activity. So the share
of $14.13 attributable to this lane is **not established** and must not be
booked against the lane's loss as though it were.

Even granting the whole $14.13 — the most generous reading available —
the lane is net roughly **-$15 to -$20** settled. Rewards offset about
half of the trading loss. They do not reverse its sign, and
`5_rebates_credited_usd` is **$0**: no maker or taker rebate has been
credited at all.

## What this means for "focus on 5-minute markets"

The steer was that sports is where the money is moving. It is moving
**out**: -$29.30 venue-settled over four days, with every measured sport
negative and the EV gate that would refuse those opens switched off.

This does not say the sports lane should stop — that is the owner's call,
and the lane is maker-only, capped at $1.50 per market and $60 total, with
a $15 daily stop. It does say that **no increase in sports frequency,
size, or concurrency is supported by the current evidence**, and that the
cheapest available improvement is not a new feature:

1. **`PMUS_TWO_SIDED_OPEN_EV_GATE` is the one change the evidence already
   justifies.** It exists, it is implemented, it is measuring, and it is
   off. Every sport it measures is negative. Turning it on refuses the
   opens the measurement says lose money. (Owner decision; previously
   left off deliberately.)
2. **Attribute the reward dollars.** Until the PAID rows are split
   between our markets and manual activity, the lane's net cannot be
   stated to the cent, and the one number that could make sports look
   profitable is the one that is unattributed.

Neither is a capital increase, a deployment, or an authorization request.
