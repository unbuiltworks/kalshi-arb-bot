## X1-ended-perfectly-matched — `btc-updown-5m-1783939200`

*Selected as:* ended with zero residual -- every contract paired

| property | value |
|---|---|
| asset | BTC |
| window length (min) | 5 |
| first fill (UTC) | 2026-07-13T10:40:07Z |
| fills in window | 10 |
| total spend (USDC) | 125.8 |
| matched pairs at end | 100 |
| residual side at end |  |
| residual size at end | 0 |
| effective pair cost at end | 1.258 |
| effective pair cost at first pairing | 1.14 |
| fills that lowered pair cost | 0 |
| fills that raised pair cost | 3 |
| leg switches | 3 |
| longest consecutive run on one leg | 4 |
| median seconds between fills | 1 |
| settled outcome | Down |
| settlement evidence | redeem_only |
| window P&L (USDC, excl. account-level rebates) | -25.8 |
| P&L per matched pair | -0.258 |

### Complete fill sequence with reconstructed inventory

| # | utc | t+s | t-s | dt | leg | px | qty | upQ | upVWAP | dnQ | dnVWAP | matched | pairCost | lockedP | resSide | resQ | resVWAP | spend |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-07-13T10:40:07Z | 7 | 293 |  | UP | 0.54 | 20 | 20 | 0.54 | 0 |  | 0 |  |  | Up | 20 | 0.54 | 10.8 |
| 2 | 2026-07-13T10:40:07Z | 7 | 293 | 0 | UP | 0.54 | 20 | 40 | 0.54 | 0 |  | 0 |  |  | Up | 40 | 0.54 | 21.6 |
| 3 | 2026-07-13T10:40:43Z | 43 | 257 | 36 | UP | 0.58 | 20 | 60 | 0.5533 | 0 |  | 0 |  |  | Up | 60 | 0.5533 | 33.2 |
| 4 | 2026-07-13T10:40:43Z | 43 | 257 | 0 | UP | 0.58 | 20 | 80 | 0.56 | 0 |  | 0 |  |  | Up | 80 | 0.56 | 44.8 |
| 5 | 2026-07-13T10:41:01Z | 61 | 239 | 18 | DOWN | 0.6 | 20 | 80 | 0.56 | 20 | 0.6 | 20 | 1.14 | -2.8 | Up | 60 | 0.5667 | 56.8 |
| 6 | 2026-07-13T10:41:02Z | 62 | 238 | 1 | DOWN | 0.6 | 20 | 80 | 0.56 | 40 | 0.6 | 40 | 1.14 | -5.6 | Up | 40 | 0.58 | 68.8 |
| 7 | 2026-07-13T10:41:02Z | 62 | 238 | 0 | DOWN | 0.6 | 20 | 80 | 0.56 | 60 | 0.6 | 60 | 1.1533 | -9.2 | Up | 20 | 0.58 | 80.8 |
| 8 | 2026-07-13T10:41:17Z | 77 | 223 | 15 | UP | 0.35 | 20 | 100 | 0.518 | 60 | 0.6 | 60 | 1.1533 | -9.2 | Up | 40 | 0.465 | 87.8 |
| 9 | 2026-07-13T10:41:38Z | 98 | 202 | 21 | DOWN | 0.95 | 20 | 100 | 0.518 | 80 | 0.6875 | 80 | 1.2475 | -19.8 | Up | 20 | 0.35 | 106.8 |
| 10 | 2026-07-13T10:41:38Z | 98 | 202 | 0 | DOWN | 0.95 | 20 | 100 | 0.518 | 100 | 0.74 | 100 | 1.258 | -25.8 |  | 0 |  | 125.8 |

