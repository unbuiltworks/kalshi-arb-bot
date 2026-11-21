# Four-window campaign, window 1 — cpc-btc-updown-5m-2026-11-21-0620z (arm A, existing controller)

Label: LIVE PROBE, SETTLED. Source: l5-btc5m-live deployment 27500aa2 (commit ac3c7f9e7), l5-pmus-live, l5-halt-watchdog logs.

## Result
- Submitted 37, filled 17, cancelled 20, uncertain 0. Close clean, nothing left open.
- UP 0.49 contracts, cost $0.2948 (8 fills). DOWN 0.61 contracts, cost $0.2957 (9 fills). Total cost $0.5905.
- DOWN won (venue settlement px 0.0000). Redemption $0.61. Venue fees $0.0000 on all 37 orders.
- Settled net: **+$0.0195** (lane, recorded at 12:27:36Z boot recovery). Account cash moved 321.4517 -> 321.4712 = +$0.0195.
- 0.49 matched pairs at average pair cost ~$1.086 (0.6016 + 0.4848): the matched part LOST ~$0.042;
  the +$0.0195 comes from the 0.12 unmatched DOWN contracts that happened to win.

## Fills (time = step line that reported the fill, UTC)
| # | time | side | qty | price |
|---|---|---|---|---|
| 1 | 06:20:21 | DOWN | 0.08 | 0.40 |
| 2 | 06:20:48 | UP | 0.07 | 0.66 |
| 3 | 06:20:48 | DOWN | 0.07 | 0.33 |
| 4 | 06:21:08 | DOWN | 0.06 | 0.20 |
| 5 | 06:21:08 | UP | 0.06 | 0.79 |
| 6 | 06:21:15 | UP | 0.06 | 0.81 |
| 7 | 06:21:22 | UP | 0.06 | 0.79 |
| 8 | 06:21:22 | DOWN | 0.06 | 0.20 |
| 9 | 06:21:27 | UP | 0.06 | 0.82 |
| 10 | 06:21:52 | DOWN | 0.08 | 0.39 |
| 11 | 06:21:52 | UP | 0.08 | 0.60 |
| 12 | 06:22:08 | DOWN | 0.08 | 0.61 |
| 13 | 06:22:35 | DOWN | 0.06 | 0.72 |
| 14 | 06:23:06 | DOWN | 0.07 | 0.67 |
| 15 | 06:23:29 | UP | 0.05 | 0.10 |
| 16 | 06:23:34 | DOWN | 0.05 | 0.93 |
| 17 | 06:23:34 | UP | 0.05 | 0.06 |

## Deviations and open items
1. Account ledger (l5-pmus-live) booked this market as DOWN 0.10 at the $1 bound, origin UNKNOWN, P&L $0.00;
   venue fills give +$0.0195. Difference $0.0195 > the $0.005 stop tolerance. Attestation final: VENUE_QTY_NE_LEDGER_QTY.
   The market sits in the ledger's uncertain / unknown-basis lists.
2. 06:22:40-06:22:50Z: three ACCOUNT_RISK:ACCOUNT_DRAWDOWN:PORTFOLIO_VALUE_UNAVAILABLE blocks, one resting order cancelled.
   Coincides with pmus adopting the lane's position as unknown (06:22:38.5) and clearing it (06:22:53.8). Link inferred, not logged.
3. 13 of the 20 cancels have no per-order log line (aggregate only; all 37 per-order fees read 0.0000, watchdog open orders 0).
4. Disarm variables were applied at ~12:26Z, not 06:25Z (session worker restart). The lane had exited by itself at
   06:25:30Z (CAMPAIGN_ENDED, state MONITOR); no order on any other market. Its registry was unreachable to the
   watchdog from 06:25:33Z until the 12:27Z redeploy.
5. Post-only flag and venue fill timestamps are not in the lane logs; price 5/15/30 s after each fill not yet computed.
6. other_position_contracts 0.05 shown on two steps (06:23:17, 06:23:22), back to 0 at 06:23:29; unexplained.
