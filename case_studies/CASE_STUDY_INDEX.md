# L5 window case studies — complete chronological sequences

29 windows, each rendered in full: every fill in timestamp order with the reconstructed inventory after it.

These files are descriptive only. They record what happened in each window. They contain no hypothesis about why.

| # | stratum | window | selected as | fills | asset | min | spend | pair cost | residual | settled | P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `R1-early-regime` | `btc-updown-15m-1774541700` | earliest-regime window with a readable sequence | 40 | BTC | 15 | 94.29 | 1.0019 | 27.95 Up | Down | -9.29 |
| 2 | `R2-late-regime` | `btc-updown-15m-1788217200` | latest-regime window | 166 | BTC | 15 | 1243.39 | 0.8933 | 10.38 Down | Down | 147.64 |
| 3 | `P1-most-profitable` | `btc-updown-15m-1787166900` | highest window P&L | 344 | BTC | 15 | 3636.82 | 0.8863 | 742.42 Down | Down | 981.32 |
| 4 | `P1b-most-profitable-early` | `btc-updown-15m-1777453200` | highest window P&L, early regime | 68 | BTC | 15 | 528.28 | 1.045 | 947.97 Up | Up | 797.35 |
| 5 | `P2-most-unprofitable` | `btc-updown-15m-1777957200` | lowest window P&L | 166 | BTC | 15 | 1803.39 | 0.9555 | 825.48 Up | Down | -505.67 |
| 6 | `P2b-most-unprofitable-early` | `btc-updown-15m-1777966200` | lowest window P&L, early regime | 168 | BTC | 15 | 2338.89 | 1.0079 | 475.95 Up | Down | -258.04 |
| 7 | `P3-near-zero-pnl` | `btc-updown-15m-1785367800` | window P&L closest to zero | 98 | BTC | 15 | 825.47 | 0.9813 | 128.64 Up | Down | 0.02 |
| 8 | `D1-one-sided-accumulation` | `btc-updown-15m-1786597200` | largest share of final inventory left unpaired | 54 | BTC | 15 | 1017.41 |  | 1317.19 Up | Up | 299.78 |
| 9 | `D1b-one-sided-accumulation-early` | `btc-updown-15m-1777614300` | largest unpaired share, early regime | 47 | BTC | 15 | 323.41 | 1.3258 | 564.11 Down | Up | -144.71 |
| 10 | `D2-one-sided-accumulation-that-lost` | `btc-updown-15m-1781270100` | large unpaired residual whose side LOST | 54 | BTC | 15 | 226.05 | 1.0934 | 589.01 Up | Down | -104.63 |
| 11 | `C1-deepest-compression` | `btc-updown-15m-1787153400` | most fills that lowered the pair cost | 621 | BTC | 15 | 8085.75 | 0.9798 | 886.88 Down | Up | 141.88 |
| 12 | `C1b-deepest-compression-early` | `btc-updown-15m-1777815900` | most pair-cost-lowering fills, early regime | 237 | BTC | 15 | 2253.52 | 0.995 | 19.75 Up | Up | 11.93 |
| 13 | `C2-pair-cost-worsened` | `btc-updown-15m-1787121900` | pair cost moved against L5 on the most fills | 756 | BTC | 15 | 9191.46 | 0.989 | 269.14 Up | Up | 106.55 |
| 14 | `C3-highest-final-pair-cost` | `btc-updown-15m-1778234400` | worst effective pair cost reached at window end | 48 | BTC | 15 | 453.92 | 1.3519 | 106.94 Up | Down | -123.89 |
| 15 | `C4-lowest-final-pair-cost` | `btc-updown-15m-1781198100` | best effective pair cost reached at window end | 56 | BTC | 15 | 558.17 | 0.6141 | 356.9 Up | Up | 252.12 |
| 16 | `S1-highest-switch-rate` | `btc-updown-15m-1776710700` | highest leg-switch rate per fill | 53 | BTC | 15 | 478.49 | 0.9995 | 50.01 Down | Up | -0.4 |
| 17 | `S1b-highest-switch-rate-early` | `btc-updown-15m-1774986300` | highest leg-switch rate, early regime | 42 | BTC | 15 | 191.75 | 0.9266 | 4.2 Down | Up | 14.05 |
| 18 | `S2-longest-single-leg-run` | `btc-updown-15m-1786179600` | longest consecutive run on one leg before switching | 146 | BTC | 15 | 913.64 | 0.9831 | 973.74 Down | Up | -123.17 |
| 19 | `S3-zero-switch` | `btc-updown-5m-1783063800` | many fills, never switched leg | 22 | BTC | 5 | 183.51 |  | 323.76 Up | Up | 140.25 |
| 20 | `V1-largest-notional` | `btc-updown-15m-1787615100` | largest total spend | 664 | BTC | 15 | 8515.65 | 1.0008 | 844.04 Down | Up | -112.08 |
| 21 | `V2-most-fills` | `btc-updown-15m-1787875200` | most fills in a single window | 749 | BTC | 15 | 7199.94 | 1.0047 | 83.41 Up | Down | -36.11 |
| 22 | `T1-fastest-cadence` | `btc-updown-15m-1776843900` | lowest median seconds between fills | 144 | BTC | 15 | 952.13 | 0.9252 | 107.98 Up | Down | 72.32 |
| 23 | `T2-slowest-cadence` | `btc-updown-15m-1774909800` | highest median seconds between fills | 44 | BTC | 15 | 223.59 | 1.111 | 39.24 Down | Up | -27.34 |
| 24 | `A1-eth` | `eth-updown-15m-1787564700` | ETH window | 37 | ETH | 15 | 281.97 | 1.1788 | 73.59 Up | Down | -60.17 |
| 25 | `A2-5-minute` | `btc-updown-5m-1782806100` | 5-minute window | 69 | BTC | 5 | 887.52 | 0.9668 | 52.3 Up | Down | 8.82 |
| 26 | `A3-15-minute` | `btc-updown-15m-1787042700` | 15-minute window | 720 | BTC | 15 | 4807.52 | 1.0314 | 898.71 Down | Up | -277.11 |
| 27 | `X1-ended-perfectly-matched` | `btc-updown-5m-1783939200` | ended with zero residual -- every contract paired | 10 | BTC | 5 | 125.8 | 1.258 | 0  | Down | -25.8 |
| 28 | `X2-residual-side-won` | `btc-updown-15m-1787030100` | residual side settled in the money | 364 | BTC | 15 | 3146.51 | 0.9663 | 1046.87 Up | Up | 885.63 |
| 29 | `X3-residual-side-lost` | `btc-updown-15m-1787706000` | residual side settled worthless | 317 | BTC | 15 | 2735.82 | 1.0347 | 1764.17 Down | Up | -170.46 |
