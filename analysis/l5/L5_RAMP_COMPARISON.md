# Early ramp: L5 vs ours (OWNER 5570456323)

L5 from canonical prints (FIFO lots; maker/taker unknown); ours from the economic scoreboard and the recycling study; turnover per day over each cohort's own wall-clock span; read-only

| cohort | days/markets | turnover $/day | action $ (p50) | pair completion % | residual % of bought | net / turnover | hours per deployed $ (p50) | maker mix | concurrency | selection |
|---|---|---|---|---|---|---|---|---|---|---|
| L5 first 7 covered days | 7 d | 1112.75 | 3.25 | 82.0 | 18.0 | 0.01693 | see recycling | unknown from public prints | 1 per lane | {"BTC-5m": 2, "BTC-15m": 86} |
| L5 2026-03 | 7 d | 1112.75 | 3.25 | 82.0 | 18.0 | 0.01693 | 0.019 (all windows) | unknown from public prints | 1 per lane | {"BTC-5m": 2, "BTC-15m": 86} |
| L5 mature (2026-04-06 onward) | 13 d | 55350.99 | 4.383 | 88.5 | 11.3 | 0.01267 | 0.019 (all windows) | unknown from public prints | 1 per lane | {"BTC-15m": 428, "BTC-5m": 955, "ETH-5m": 703, "ETH-15m": 29} |
| R1 | 11 mkts | 1.52 | 0.01-0.02 shares (~$0.005-0.01) | 84.5 | 15.5 | -0.0261 | None | 100% maker (post-only) | 1 | {"match_winner/baseball": 1, "spread/football": 1, "match_winner/tennis": 5, "match_winner/esports": 2, "three_way/soccer": 1, "total/baseball": 1} |
| R2C | 6 mkts | 1.29 | 0.01-0.02 shares (~$0.005-0.01) | 79.2 | 20.8 | -0.10293 | None | 100% maker (post-only) | 1 | {"total/baseball": 1, "match_winner/baseball": 1, "total/football": 1, "spread/baseball": 1, "match_winner/tennis": 1, "three_way/soccer": 1} |
| R2C2 | 3 mkts | 2.13 | 0.01-0.02 shares (~$0.005-0.01) | 50.0 | 50.0 | -0.36709 | None | 100% maker (post-only) | 1 | {"three_way/soccer": 2, "match_winner/baseball": 1} |
| R3L | 3 mkts | 305.5 | ~2.50 (5 shares) | 77.3 | 22.7 | -0.04417 | 0.114 | 100% maker (post-only) | 1 | {"spread/football": 2, "total/football": 1} |
| R4 | 2 mkts | 273.93 | ~2.50 (5 shares) | 95.3 | 4.7 | 0.11515 | 0.206 | 100% maker (post-only) | 1 | {"match_winner/tennis": 2} |

Reproducible now with ~$109.0: action size: L5's early day-median action (see l5_early_action_usd_p50) is within reach of the R4 $2.50 action; the 10-13 share median is a later-era number; pair completion: our at-size completion share is in L5's range (see the table); the complement step is not the gap; maker-only, one market at a time: matches L5's observed concurrency

Not reproducible yet: turnover per day: L5's is set by 5/15-minute windows that settle in minutes and re-enter within a minute; our sports markets settle ~1.2 h after the first fill and the campaign waits for settlement before rotating (FLAT_WAIT) -- the ratio above is the gap; residual share: L5 runs a residual several actions deep (peak p50 3.5 units); R4 caps it at one action, which refuses ~98% of L5-style EXTENDs -- a translation choice, not a bankroll limit; market/window selection: L5's lanes are two assets x two durations; ours is a sports universe served by a recorder whose pool held only resolved markets for 2.5 h (opportunity_utilization)

Turnover: ours at size on 2026-09-07 was $53.26 in one calendar day against L5's first-week $1112.75 per day (ratio **0.0479**); as a rate over the R4 span the ratio is 0.246. what the ~$109 bankroll can reproduce is the per-market L5 process at ~1 action unit; what it cannot yet reproduce is L5's turnover, which is a market-duration and rotation property, not a capital property

Day-by-day L5 numbers are in `l5_ramp_comparison.json`.
