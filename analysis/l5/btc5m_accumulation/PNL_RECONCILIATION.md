# The two P&L ledgers disagreed all day. Both were right.

2026-11-14. Read-only. The question: the 15:10Z BTC5m campaign's own
ledger reported +9.40 while the account-wide figure reported -5.44, and
the venue position read returned 0 rows on all 12 windows so neither
could check the other.

## The answer

**The account figure books a provisional loss at $1.00 a share and
corrects to the real cost later.** From the 15m service's own heartbeat:

```
venue_cost_attestation:
  attested 26   corrections_applied 26   corrections_delta_usd +142.35590000
  cpc-btc-updown-5m-2026-11-13-2335z  old_pnl -5.00 -> new_pnl -0.40  (+4.60)
```

A 5-share leg whose cost this ledger cannot yet source is charged at
`UNKNOWN_COST_UPPER_BOUND` = $1.00/share, so it books -5.00. The real cost
was 5 x 0.08 = 0.40. Venue attestation then restates it, about two minutes
after settlement.

The campaign ledger uses `cost_basis: AUTHENTICATED_FILLS` from the first
moment, so it was never wrong -- it was simply ahead.

**It is converging, measured over 80 minutes:**

| | 15:22Z | 16:39Z | move |
|---|---|---|---|
| utc_day_realized_usd | -5.44 | **-3.99** | +1.45 |
| all_time_realized_usd | -33.87 | **-32.42** | +1.45 |

Neither figure was wrong. One is provisional and one is sourced, and the
provisional one is walking toward the sourced one as corrections apply.

## The ground truth that existed all along

```
available_cash   $352.89765   source /v1/account/balances   age_s 0.0
```

It is in the 15m service's heartbeat, live, and it is the venue's own
number rather than anybody's ledger. It moved $353.31765 -> $352.89765 in
63 seconds while the sports bot placed orders, so it tracks CASH
including cash tied up in resting orders -- not realized P&L.

**Therefore the correct way to measure a campaign's realized result is to
sample `available_cash` WHEN FLAT -- no positions, no resting orders --
before and after.** The delta is then the venue's own answer and needs no
ledger at all. That was available for every campaign run today and was
never used; every P&L figure quoted in this session came from one of the
two disagreeing ledgers instead.

## What this changes

1. Today's campaign most likely did make close to +9.40. The account
   figure should keep converging as attestation applies.
2. Nothing should be quoted as realized from either ledger alone again.
   Quote the flat-to-flat `available_cash` delta, or say it is provisional.
3. The attestation is doing real work: +142.36 of corrections across 26
   markets. Without it the account-wide figures overstate losses by
   roughly $1.00 per share held, which is 12.5x at an 8c entry.

## What is still NOT established by any of this

The 5m straddle's edge. Pair rate 16/202 = 7.9% against an 8.70%
breakeven, net +3.49 c/w at t +1.61. Reconciling the accounting does not
make a thin edge real -- it only means we can now measure it honestly.
