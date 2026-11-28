# Stage A four-window campaign — settlement reconciliation

Campaign nonce `stageA2-40fa42ffe6daa339`. Serving deployment `9b43b312`
on commit `0304fbe8`. All figures below are `VENUE_SETTLEMENT_PX` /
`MARKET_STATE_EXPIRED` CONFIRMED records, not provisional snapshots.

## The four windows the campaign actually used

The campaign budget's own `windows` list is authoritative:

```
["cpc-btc-updown-15m-2026-10-30-2115z",
 "cpc-btc-updown-15m-2026-10-30-2130z",
 "cpc-btc-updown-15m-2026-10-30-2145z",
 "cpc-btc-updown-15m-2026-10-30-2200z"]
```

| window | submitted | ack | filled orders | contracts filled | pairs | matched qty | residual | realized net |
|---|---|---|---|---|---|---|---|---|
| 2115z | 9 | 9 | 5 | 8.76 | **2** | 3.51 | DOWN 1.74 | **-0.275600** |
| 2130z | 7 | 7 | 1 | 2.05 | 0 | 0 | UP 2.05 | **-0.943000** |
| 2145z | 2 | 2 | 1 | 2.00 | 0 | 0 | DOWN 2.00 | **+1.100000** |
| 2200z | 2 | 2 | 1 | 1.85 | 0 | 0 | DOWN 1.85 | **-0.610500** |
| **total** | **20** | **20** | **8** | **14.66** | **2** | **3.51** | — | **-0.729100** |

## Ledger tie-out (exact)

| quantity | value |
|---|---|
| `all_time_realized_usd` before campaign | -10.85950000 |
| `all_time_realized_usd` after campaign | -11.58860000 |
| delta | **-0.72910000** |
| sum of four CONFIRMED windows | **-0.72910000** |
| unexplained residue | **0.00000000** |

`unattributed_usd` is `0`; `undated_settlements` is `[]`. The ledger moves
by exactly the campaign total, so no settlement is missing or double-counted.

Daily control: `utc_day_realized_usd = -1.99740000` against the `$5` halt,
`room_usd = 3.00260000`, `would_halt = false`. The daily settled-loss halt
was never approached. `$20` aggregate exposure and the `$1.50` per-market
worst-case deficit were never breached.

## Execution economics

- Maker orders 20, **taker orders 0**. Maker-only/post-only held for the
  whole campaign.
- `actual_fees_usd = 0` in every window (no maker-fee term on this venue).
- Rebates: estimated `$0.1059` across the four windows, **credited `$0`**,
  eligibility `UNVERIFIED_NO_VENUE_CREDIT_RECORD`. Excluded from every
  realized figure above and still not income.

## Pair economics vs. settlement

Window 2115z is the only window that completed pairs:

| pair | qty | pair cost | UP own | DOWN own | pair P&L |
|---|---|---|---|---|---|
| 1 | 1.72 | 0.9900 | 0.7800 | 0.2100 | +0.017200 |
| 2 | 1.79 | 0.9400 | 0.6700 | 0.2700 | +0.107400 |
| | | | | **pair edge** | **+0.124600** |

Settled, 2115z decomposes as matched `+0.136929` and residual
`-0.412529`, netting `-0.275600`. **Both pairs were profitable and the
window still lost money**: the 1.74-contract stranded DOWN leg cost more
than the matched book earned. Pair edge is not the P&L.

## Completed pairs per first-leg fill

Across the campaign there were 8 filled orders. Only 2115z converted, and
it converted because the complement filled 7.04 contracts against a 1.72
first leg. Windows 2130z/2145z/2200z produced one first-leg fill each and
**zero** complements: 3 first-leg fills, 3 stranded legs, 0 pairs.

Aggregate: **2 pairs from 4 windows**, and the three single-fill windows
converted 0 of 3.

## Why the three single-fill windows netted what they did

| window | fill | price | resolution | outcome |
|---|---|---|---|---|
| 2130z | UP 2.05 | 0.46 | DOWN | lost -0.943000 |
| 2145z | DOWN 2.00 | 0.45 | DOWN | won +1.100000 |
| 2200z | DOWN 1.85 | 0.33 | UP | lost -0.610500 |

The one profitable window was profitable because an unmatched directional
leg happened to resolve in its favour. That is **directional luck, not
replication**, and it must not be read as evidence the policy works.

## Schedule deviation (reported, not excused)

The authorization named windows at **21:30, 21:45, 22:00 and 22:15 UTC**.
The engine instead used **21:15, 21:30, 21:45 and 22:00**: on arming it
began working the market that was already live rather than waiting for the
21:30 window to open, and the four-window cap was therefore exhausted one
window early.

Consequences:

- **Window count**: 4 used, 4 authorized. The cap held.
- **2215z never traded.** Its 12 `submitted` stamps are pre-send records
  refused by the exhausted window cap; `acknowledged 0`, `terminal 0`,
  `filled 0`, `residual 0`. No order reached the venue, and the window has
  no CONFIRMED record because there was nothing to settle.
- **2230z never traded.** Its heartbeat `outstanding` legs are labelled
  `MODELLED hypothetical legs, never submitted`, and `armed` was already
  `false`.
- Risk envelope: every dollar limit held, with `$3.00` of daily room unused.

The deviation is in *which* windows were used, not in how many or at what
risk. It is still a deviation from the approved schedule and the arming
path should bind the campaign to the authorized window start times rather
than to whatever market is live at arm time.

## Status

Executor disarmed after the campaign: `PMUS_Q1_LIVE=NO`, `PMUS_Q1_ARM`
cleared, `L5_SHADOW_ONLY=1`. No outstanding venue orders and no unsettled
position in any of the four windows.
