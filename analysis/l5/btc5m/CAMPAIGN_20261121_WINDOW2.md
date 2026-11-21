# Campaign window 2 — cpc-btc-updown-5m-2026-11-21-1420z (arm B: heavy-underdog STOP_ADDING)

Label: LIVE PROBE, SETTLED. Lane commit 5424a3244 (deployment 388ec3f4), account service 62b54e14a.

## Result
- Submitted 26, filled 12, cancelled by the lane 8 (all CANCELED at venue), rejected by the venue 6, uncertain 0. Close clean.
- UP 0.37 contracts, cost $0.2594 (avg 0.7011). DOWN 0.39, cost $0.1514 (avg 0.3882). Total cost $0.4108.
- UP won. Redemption $0.37. Fees $0.0000 (26/26; `makerCommissionsBasisPoints` 0).
- Settled net: **-$0.0408** (lane, recorded 14:26:20Z while resident). Cash 321.4712 -> 321.4304 = -$0.0408.
- Matched 0.37 pairs at pair cost 1.0893: **-$0.0330**. Residual 0.02 DOWN lost: **-$0.0078**.
- Campaign to date: window 1 +0.0195, window 2 -0.0408 = **-$0.0213**. Campaign loss counted against the $0.60 halt: $0.0408.

## Fills (venue lastTransactTime, UTC)
| time | side | price | qty |
|---|---|---|---|
| 14:20:08 | DOWN | 0.60 | 0.08 |
| 14:20:14 | DOWN | 0.60 | 0.08 |
| 14:20:22 | UP | 0.38 | 0.08 |
| 14:20:35 | DOWN | 0.58 | 0.08 |
| 14:20:45 | UP | 0.45 | 0.09 |
| 14:21:25 | UP | 0.94 | 0.05 |
| 14:21:42 | UP | 0.93 | 0.05 |
| 14:21:49 | UP | 0.93 | 0.05 |
| 14:21:54 | DOWN | 0.06 | 0.05 |
| 14:22:05 | DOWN | 0.06 | 0.05 |
| 14:22:23 | UP | 0.97 | 0.05 |
| 14:23:00 | DOWN | 0.06 | 0.05 |

## What the switch did
- Fired on 8 steps from 14:21:02 (DOWN heavier, 0.24 vs 0.17, and now the underdog). No DOWN order was placed on any of them.
- It does not touch the other side: the lane kept buying UP at 0.93-0.97 to complete pairs against DOWN bought at
  0.58-0.60. Those four UP fills (0.20 contracts) are the matched-pair loss: ~1.52 per pair against a $1 payout.
- The first five fills (to 14:20:45) cost 0.2133 for UP 0.17 / DOWN 0.24 — pair cost ~1.01 before the price jump.

## Verification items
| Item | Result |
|---|---|
| Post-only as sent | True on 26/26 (new per-order record) |
| Venue evidence | LIMIT, GOOD_TILL_CANCEL, AUTOMATIC, maker commission 0 bps on 26/26. The venue row carries no post-only flag and no reject reason. 6 rejects have lastTransactTime == insertTime (rejected on arrival). |
| Portfolio-value refusal | 14:23:09-14:23:20, 3 blocks. Nothing resting, nothing cancelled, nothing placed inside. Next step 14:23:24 (HOLD, side cap reached); no order after it, so resumption-by-placing was not exercised. Coincides with pmus adopting the position 14:23:07.7 and clearing 14:23:24.6. |
| Watchdog | Lane registry OK on all 46 sweeps 14:19-14:34 incl. both redeploys; max 1 open order; no cancels, no read errors. |
| Resident after campaign | CAMPAIGN_ENDED resident:true; can_submit false, transport_armed false; settled the window at 14:26:20. |
| Fractional position | other_position_contracts 0 on every step. |
| Pre-settlement check (first production run) | 14:26:35 refused `FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION`; settlement unchanged. |
| Limits | UP side $0.2594 <= 0.30; DOWN $0.1514; total $0.4108 <= 0.60; window loss $0.0408 <= 0.20. |
| Account ledger | Booked -0.0200 at settlement (net 0.02 DOWN at the $1 bound). CORRECTED 14:36:48Z: -0.0200 -> -0.0408 (delta -0.0208). Lifetime realized -39.5158, UTC day -0.0213, uncertain markets empty, 1420z gone from unknown-basis keys. Lane, ledger and cash all -0.0408: difference 0.0000. The four pre-rebase 5m markets used their third and last sibling attempt (refused, unchanged). |
| Disarm variables | Applied ~14:33Z (worker restart); approvals had expired 14:25-14:26Z; lane already MONITOR. |
