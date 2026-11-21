# L5 completion ceiling -- what L5 did when he chose COMPLETE

Owner direction: #167 comment 5574408755 item 2 (completion ceiling from actual L5 behaviour). WHAT L5 DID only. Pair costs are pre-rebate, pre-fee acquisition costs of a $1.00-paying pair, > 1.00 mass intact. What PMUS economics can support is a separate study; see economic_overlay_attachment_points.

Source: `analysis/l5/l5_canonical_sequence.csv.gz` (71984 rows, 2216 windows, 2026-03-19T10:09:37Z -> 2026-08-31T23:11:19Z, sha16 3b98847233684612).

Relations by the live rule (`action_model._relation`): {"open": 2864, "sell": 2, "extend": 35353, "complete": 33765}. COMPLETE fills: 33765 (44 pre-regime), fill $343486.96, of which $312856.78 completed pairs; 3431 completes overshoot the surplus ($29271.03 of overshoot).

## Bases

* **engine_avg** -- complete price + average cost (cum spend/cum qty) of the surplus side before the fill
* **marginal_lifo** -- complete price + qty-weighted price of the most recent surplus lot(s) consumed, LIFO, matched part only
* **window_realized** -- window's matched-pair unit cost at settlement (wavg_up + wavg_down, final canonical row), per complete

Dollar shares weight by completed dollars (complete price x matched qty (the part of the fill that formed pairs); dollar shares use this). Percentiles are fill-count; usd_p50/p90 are dollar-weighted. All pair costs are pre-rebate, pre-fee.

## Overall distribution of pair cost at COMPLETE

| basis | n | completed $ | mean | $-wmean | pair-wmean | p10 | p25 | p50 | p75 | p90 | p99 | max | $-p50 | $-p90 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| engine_avg | 33765 | 312856.8 | 0.9818 | 1.0995 | 1.0049 | 0.7668 | 0.8624 | 0.9697 | 1.1004 | 1.2204 | 1.4347 | 1.8324 | 1.1040 | 1.3092 |
| marginal_lifo | 33765 | 312856.8 | 0.9775 | 1.0174 | 0.9845 | 0.8600 | 0.9400 | 0.9823 | 1.0100 | 1.0800 | 1.3800 | 1.9200 | 0.9973 | 1.1506 |
| window_realized | 33765 | 312856.8 | 0.9688 | 0.9817 | 0.9708 | 0.9141 | 0.9418 | 0.9659 | 0.9885 | 1.0168 | 1.2524 | 1.7788 | 0.9687 | 1.0380 |

### Share above thresholds (fills / completed pairs / completed dollars)

| basis | >1.00 fills% | >1.00 pairs% | >1.00 $% | >1.01 fills% | >1.01 pairs% | >1.01 $% | >1.02 fills% | >1.02 pairs% | >1.02 $% | >1.03 fills% | >1.03 pairs% | >1.03 $% | >1.05 fills% | >1.05 pairs% | >1.05 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| engine_avg | 42.7 | 49.6 | 73.2 | 40.8 | 47.8 | 71.3 | 38.9 | 45.9 | 69.3 | 37.2 | 44.2 | 67.5 | 33.6 | 40.4 | 63.0 |
| marginal_lifo | 28.7 | 33.4 | 42.8 | 23.4 | 27.6 | 35.8 | 20.0 | 23.7 | 31.1 | 17.4 | 20.7 | 27.5 | 13.5 | 16.6 | 22.3 |
| window_realized | 17.4 | 19.7 | 24.0 | 13.6 | 15.7 | 19.4 | 9.1 | 11.1 | 14.3 | 7.7 | 9.5 | 12.5 | 4.7 | 6.2 | 8.2 |

Per-window realized pair cost (windows with any matched pair, weighted by matched-pair dollars): n=1610 p50=0.9704 p90=1.2442 max=1.7788; >1.00 by windows = 38.82%, by pairs = 19.74%, by $ = 21.7%.

## By imbalance bucket (live key, |residual shares| before the complete)

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| imb1-10 | 1707 | 3796.0 | 0.9642 | 1.2123 | 39.7 | 58.4 | 53.0 | 0.9800 | 1.1258 | 32.7 | 43.8 | 0.9624 | 24.9 |
| imb10-50 | 6833 | 48326.2 | 0.9633 | 1.1897 | 44.3 | 64.3 | 59.0 | 0.9800 | 1.1100 | 34.9 | 46.2 | 0.9646 | 24.9 |
| imb>50 | 25225 | 260734.6 | 0.9721 | 1.2265 | 50.7 | 75.0 | 71.5 | 0.9855 | 1.0700 | 33.1 | 42.1 | 0.9664 | 23.8 |

## By time-to-settlement bucket (live key)

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tte<30s | 382 | 4752.1 | 1.0156 | 1.4017 | 48.6 | 83.6 | 81.2 | 0.9700 | 1.1350 | 30.0 | 49.0 | 0.9659 | 33.0 |
| tte30-120s | 4784 | 56519.1 | 0.9665 | 1.3350 | 49.5 | 80.2 | 78.2 | 0.9800 | 1.1790 | 35.1 | 51.2 | 0.9659 | 33.6 |
| tte2-5m | 8191 | 91320.0 | 0.9992 | 1.2698 | 53.9 | 78.9 | 76.6 | 0.9876 | 1.0900 | 33.0 | 40.7 | 0.9680 | 24.4 |
| tte5-10m | 11456 | 95087.7 | 0.9516 | 1.1838 | 49.8 | 72.9 | 69.2 | 0.9800 | 1.0600 | 31.6 | 39.5 | 0.9657 | 20.9 |
| tte>10m | 8952 | 65177.9 | 0.9734 | 1.1205 | 44.0 | 58.6 | 50.9 | 0.9900 | 1.0700 | 35.1 | 42.5 | 0.9659 | 19.0 |

## By last-observed-price band (live key)

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p<10c | 3198 | 36671.2 | 0.8713 | 1.2906 | 50.0 | 90.8 | 89.8 | 0.9800 | 1.0003 | 14.2 | 22.0 | 0.9679 | 24.4 |
| p10-25c | 7074 | 46608.4 | 0.8847 | 1.1714 | 36.0 | 70.3 | 68.4 | 0.9700 | 1.0200 | 21.6 | 32.0 | 0.9633 | 21.0 |
| p25-45c | 7047 | 54300.2 | 0.9624 | 1.1333 | 42.7 | 54.7 | 48.0 | 0.9800 | 1.0643 | 34.6 | 39.8 | 0.9653 | 20.1 |
| p45-55c | 3465 | 33176.6 | 1.0125 | 1.1518 | 59.8 | 63.4 | 55.5 | 0.9913 | 1.1200 | 46.8 | 50.8 | 0.9659 | 25.3 |
| p55-75c | 6520 | 63517.8 | 1.0068 | 1.2271 | 53.9 | 66.1 | 61.2 | 0.9900 | 1.1900 | 43.1 | 51.4 | 0.9659 | 25.0 |
| p75-90c | 4304 | 45504.6 | 1.0144 | 1.2572 | 54.6 | 83.8 | 81.4 | 0.9900 | 1.0903 | 34.5 | 46.3 | 0.9679 | 25.1 |
| p>90c | 2157 | 33078.1 | 1.1203 | 1.3437 | 60.9 | 96.5 | 96.0 | 0.9900 | 1.1000 | 38.8 | 56.2 | 0.9732 | 29.7 |

## By the complete's own price band

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p<10c | 3145 | 3313.3 | 0.7992 | 0.8957 | 0.4 | 0.5 | 0.3 | 0.9600 | 0.9923 | 4.4 | 6.1 | 0.9631 | 12.3 |
| p10-25c | 7367 | 18825.5 | 0.8529 | 0.9460 | 2.9 | 3.3 | 1.9 | 0.9662 | 1.0050 | 12.0 | 12.5 | 0.9631 | 9.8 |
| p25-45c | 7703 | 44693.6 | 0.9383 | 1.0406 | 23.7 | 26.1 | 19.8 | 0.9800 | 1.0460 | 27.6 | 28.6 | 0.9653 | 15.3 |
| p45-55c | 3335 | 31475.3 | 1.0132 | 1.1301 | 60.3 | 60.5 | 52.0 | 1.0000 | 1.1166 | 48.7 | 48.9 | 0.9654 | 22.3 |
| p55-75c | 5468 | 70521.2 | 1.0851 | 1.2596 | 80.6 | 81.1 | 75.0 | 1.0000 | 1.2100 | 51.9 | 51.5 | 0.9663 | 23.7 |
| p75-90c | 4075 | 75115.5 | 1.1450 | 1.3419 | 95.9 | 96.1 | 94.1 | 0.9922 | 1.1400 | 45.6 | 45.6 | 0.9685 | 26.9 |
| p>90c | 2672 | 68912.3 | 1.2095 | 1.4012 | 98.9 | 98.9 | 98.4 | 1.0000 | 1.1271 | 47.1 | 47.1 | 0.9752 | 32.1 |

## By lane (asset-duration)

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC-15m | 30831 | 273202.8 | 0.9680 | 1.2041 | 50.1 | 72.7 | 68.9 | 0.9820 | 1.0600 | 31.9 | 39.4 | 0.9659 | 18.2 |
| BTC-5m | 2385 | 35532.4 | 0.9875 | 1.3547 | 45.6 | 76.3 | 72.8 | 0.9800 | 1.3040 | 41.1 | 65.5 | 0.9838 | 63.4 |
| ETH-15m | 31 | 178.1 | 1.1702 | 1.4310 | 67.4 | 90.9 | 90.9 | 1.1277 | 1.4703 | 60.8 | 85.8 | 1.1132 | 79.5 |
| ETH-5m | 518 | 3943.4 | 1.0488 | 1.4351 | 51.4 | 72.6 | 71.0 | 1.0300 | 1.4100 | 49.3 | 70.7 | 1.0364 | 70.1 |

## By month of the complete

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-03 | 819 | 3232.9 | 0.9720 | 1.1949 | 39.7 | 53.0 | 46.5 | 0.9700 | 1.1022 | 31.9 | 40.7 | 0.9649 | 36.6 |
| 2026-04 | 1714 | 10299.3 | 0.9700 | 1.2100 | 45.0 | 63.7 | 58.8 | 0.9800 | 1.1000 | 34.9 | 41.7 | 0.9525 | 26.6 |
| 2026-08 | 31232 | 299324.6 | 0.9694 | 1.2213 | 49.8 | 73.7 | 70.0 | 0.9834 | 1.0800 | 33.3 | 42.8 | 0.9664 | 23.8 |

## By unmatched dollars before the complete

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| inv<$5 | 1982 | 8529.8 | 0.9827 | 1.1944 | 47.3 | 58.9 | 51.4 | 0.9800 | 1.1100 | 31.9 | 37.5 | 0.9624 | 23.3 |
| inv$5-25 | 6878 | 65265.3 | 0.9850 | 1.1936 | 53.9 | 70.9 | 66.2 | 0.9800 | 1.1064 | 37.2 | 45.2 | 0.9631 | 24.4 |
| inv$25-100 | 14232 | 151887.5 | 0.9709 | 1.2118 | 50.5 | 74.2 | 70.4 | 0.9800 | 1.0900 | 33.8 | 44.0 | 0.9659 | 24.8 |
| inv$100-500 | 10673 | 87174.1 | 0.9518 | 1.2457 | 46.0 | 74.3 | 71.6 | 0.9884 | 1.0500 | 30.8 | 39.2 | 0.9680 | 22.4 |

## By what preceded the complete (open / extend streak / complete)

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| after_open | 890 | 9518.7 | 0.9900 | 1.2850 | 46.4 | 64.7 | 58.8 | 0.9900 | 1.2600 | 42.3 | 59.4 | 0.9704 | 53.5 |
| after_extend_x1 | 4416 | 46105.1 | 0.9767 | 1.2350 | 48.0 | 73.0 | 69.5 | 0.9900 | 1.0700 | 29.6 | 39.1 | 0.9664 | 28.2 |
| after_extend_x2-3 | 3661 | 41434.1 | 0.9911 | 1.2441 | 50.5 | 74.9 | 70.9 | 0.9900 | 1.0500 | 29.7 | 37.2 | 0.9675 | 25.4 |
| after_extend_x4+ | 2724 | 39013.7 | 1.0570 | 1.2691 | 66.7 | 84.8 | 81.8 | 0.9900 | 1.0400 | 28.9 | 30.8 | 0.9659 | 19.3 |
| after_complete | 22074 | 176785.2 | 0.9535 | 1.1992 | 46.9 | 70.7 | 66.8 | 0.9800 | 1.0900 | 35.5 | 46.7 | 0.9659 | 22.1 |

## By full live state (price band | tte | imbalance), top 25 by count

| group | n | completed $ | eng p50 | eng p90 | eng >1.00 pairs% | eng >1.00 $% | eng >1.02 $% | marg p50 | marg p90 | marg >1.00 pairs% | marg >1.00 $% | realized p50 | realized >1.00 $% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p10-25c\|tte5-10m\|imb>50 | 2496 | 15185.6 | 0.8849 | 1.1500 | 34.5 | 69.8 | 68.3 | 0.9700 | 1.0100 | 20.1 | 28.1 | 0.9633 | 15.6 |
| p25-45c\|tte>10m\|imb>50 | 1919 | 14175.7 | 0.9688 | 1.0936 | 41.5 | 52.5 | 43.4 | 0.9842 | 1.0500 | 32.8 | 36.7 | 0.9672 | 15.2 |
| p25-45c\|tte5-10m\|imb>50 | 1753 | 14235.8 | 0.9705 | 1.1527 | 47.3 | 59.3 | 52.0 | 0.9863 | 1.0600 | 35.3 | 38.6 | 0.9663 | 17.8 |
| p55-75c\|tte>10m\|imb>50 | 1572 | 14209.5 | 0.9901 | 1.1622 | 47.0 | 58.5 | 51.8 | 0.9900 | 1.1200 | 40.0 | 47.1 | 0.9675 | 20.9 |
| p55-75c\|tte5-10m\|imb>50 | 1453 | 16168.2 | 1.0312 | 1.2204 | 60.2 | 69.9 | 65.4 | 0.9900 | 1.1500 | 40.3 | 44.7 | 0.9633 | 17.5 |
| p10-25c\|tte2-5m\|imb>50 | 1293 | 11491.6 | 0.8747 | 1.2292 | 40.2 | 74.3 | 72.1 | 0.9793 | 1.0216 | 24.2 | 31.6 | 0.9664 | 24.7 |
| p75-90c\|tte5-10m\|imb>50 | 1247 | 14142.3 | 1.0249 | 1.2205 | 61.2 | 86.2 | 84.0 | 0.9900 | 1.0500 | 32.8 | 42.1 | 0.9631 | 22.4 |
| p10-25c\|tte>10m\|imb>50 | 1123 | 5944.6 | 0.8990 | 1.0846 | 29.2 | 62.3 | 60.9 | 0.9700 | 1.0100 | 16.9 | 25.2 | 0.9659 | 17.2 |
| p75-90c\|tte2-5m\|imb>50 | 1119 | 13593.5 | 1.0587 | 1.2768 | 57.0 | 85.6 | 83.6 | 0.9828 | 1.0554 | 30.9 | 38.9 | 0.9679 | 20.0 |
| p55-75c\|tte2-5m\|imb>50 | 1081 | 13180.9 | 1.0408 | 1.2492 | 54.8 | 66.8 | 64.4 | 0.9900 | 1.1700 | 43.4 | 51.9 | 0.9663 | 30.1 |
| p45-55c\|tte>10m\|imb>50 | 976 | 9611.4 | 1.0141 | 1.1180 | 60.8 | 62.3 | 49.8 | 0.9951 | 1.0993 | 47.2 | 49.2 | 0.9608 | 23.0 |
| p<10c\|tte2-5m\|imb>50 | 875 | 13048.3 | 1.1034 | 1.3242 | 63.4 | 94.7 | 93.7 | 0.9800 | 1.0001 | 12.9 | 16.4 | 0.9677 | 19.4 |
| p25-45c\|tte2-5m\|imb>50 | 847 | 9638.4 | 0.9537 | 1.1987 | 45.1 | 58.2 | 54.4 | 0.9900 | 1.1100 | 40.7 | 46.4 | 0.9665 | 25.0 |
| p<10c\|tte5-10m\|imb>50 | 809 | 7156.5 | 0.8657 | 1.1966 | 44.3 | 92.1 | 91.8 | 0.9800 | 1.0000 | 12.3 | 18.7 | 0.9687 | 29.8 |
| p<10c\|tte30-120s\|imb>50 | 752 | 11406.1 | 0.9085 | 1.3614 | 50.5 | 90.0 | 89.5 | 0.9760 | 1.0100 | 17.9 | 29.6 | 0.9663 | 26.6 |
| p45-55c\|tte5-10m\|imb>50 | 733 | 8325.5 | 1.0402 | 1.1760 | 69.5 | 71.1 | 64.7 | 1.0000 | 1.1400 | 51.1 | 53.1 | 0.9685 | 22.9 |
| p25-45c\|tte>10m\|imb10-50 | 703 | 4283.2 | 0.9601 | 1.0867 | 33.2 | 44.4 | 35.4 | 0.9700 | 1.0300 | 23.7 | 28.3 | 0.9633 | 15.7 |
| p>90c\|tte2-5m\|imb>50 | 682 | 13085.1 | 1.1329 | 1.3367 | 67.9 | 97.3 | 96.9 | 0.9900 | 1.0500 | 35.8 | 46.7 | 0.9690 | 19.5 |
| p10-25c\|tte30-120s\|imb>50 | 594 | 6301.6 | 0.8503 | 1.2800 | 42.6 | 75.7 | 74.2 | 0.9500 | 1.0500 | 28.9 | 45.6 | 0.9592 | 31.4 |
| p75-90c\|tte>10m\|imb>50 | 592 | 4817.2 | 0.9226 | 1.1542 | 39.0 | 72.7 | 68.9 | 0.9900 | 1.0500 | 35.1 | 52.3 | 0.9752 | 25.6 |
| p55-75c\|tte>10m\|imb10-50 | 585 | 4510.5 | 1.0100 | 1.1645 | 57.2 | 67.2 | 57.4 | 1.0000 | 1.1703 | 51.3 | 59.6 | 0.9654 | 17.4 |
| p>90c\|tte30-120s\|imb>50 | 551 | 8969.3 | 1.1203 | 1.3964 | 53.2 | 95.7 | 94.8 | 0.9900 | 1.1200 | 38.6 | 62.8 | 0.9710 | 33.6 |
| p10-25c\|tte5-10m\|imb10-50 | 512 | 2206.8 | 0.8741 | 1.1168 | 28.3 | 61.5 | 57.4 | 0.9500 | 1.0112 | 13.6 | 24.7 | 0.9608 | 16.1 |
| p25-45c\|tte5-10m\|imb10-50 | 507 | 2833.8 | 0.9417 | 1.1115 | 34.0 | 45.0 | 40.2 | 0.9703 | 1.0500 | 28.4 | 33.6 | 0.9592 | 15.5 |
| p55-75c\|tte30-120s\|imb>50 | 502 | 6749.1 | 1.0188 | 1.3367 | 53.7 | 73.7 | 69.0 | 1.0000 | 1.3109 | 46.9 | 63.4 | 0.9618 | 40.5 |

(85 states with n >= 20 in the JSON; smaller cells pooled.)

## Completed at all vs held residual to settlement (per window)

| lane | windows | any complete % | both sides % | held >=1sh % | flat % | med completes/win | bought $ | matched-pair $ % of bought | resid $ p50 | resid $ p90 | resid $ total |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ALL | 2216 | 72.6 | 72.7 | 97.9 | 2.1 | 2 | 728502.1 | 86.4 | 25.3 | 118.6 | 99385.4 |
| BTC-15m | 527 | 96.0 | 96.0 | 98.9 | 1.1 | 52 | 573704.7 | 93.7 | 43.6 | 189.0 | 36243.1 |
| BTC-5m | 957 | 83.1 | 83.1 | 97.0 | 3.0 | 2 | 129037.4 | 63.6 | 33.0 | 121.1 | 46899.7 |
| ETH-15m | 29 | 41.4 | 44.8 | 93.1 | 6.9 | 0 | 1041.8 | 43.1 | 21.8 | 39.0 | 591.8 |
| ETH-5m | 703 | 42.1 | 42.1 | 98.7 | 1.3 | 0 | 24718.1 | 36.7 | 14.6 | 54.6 | 15650.8 |

## Dollar-weighted average pair cost of ALL matched pairs, per month

| month | windows | matched pairs | $-wavg pair (window basis) | $-wavg pair (lot LIFO basis) | completes | $-wavg engine pair at complete |
|---|---|---|---|---|---|---|
| 2026-03 | 88 | 6517.9 | 0.9705 | 0.9686 | 819 | 1.0451 |
| 2026-04 | 128 | 20915.4 | 0.9601 | 0.9694 | 1714 | 1.0595 |
| 2026-08 | 2000 | 620588.3 | 0.9712 | 0.9852 | 31232 | 1.1014 |
| ALL | 2216 | 648021.6 | 0.9708 | 0.9845 | 33765 | - |

## Where the PMUS economic overlay attaches (named only, not computed)

NAMED ONLY, not computed here. The overlay converts an observed pair cost into supported/unsupported at PMUS.

* maker rebate per filled dollar on PMUS, per leg (complete leg and the surplus leg it matches), from the venue schedule in force at the fill time
* taker fee / maker fee per filled dollar (if any) per leg
* tick rounding of the complement price (floor to tick, as complement_target does) and the resulting fixed-point pair cost
* settlement payout per pair (1.00) and any settlement/redemption cost
* minimum-edge parameter currently in hard_pair_bound (1.00 - MIN_EDGE) and PAIR_OBSERVATION_CAP
* capital lock time (complete timestamp to settlement) if a carry cost is to be charged

Attach to: each COMPLETE record's engine_avg (the basis the engine prices through); the comparison is supported_ceiling = 1.00 + rebates - fees - min_edge (per pair dollar) against this study's observed distribution by state. Code point: `src/pmus_sports_l5_decision.py: hard_pair_bound / complement_target`.

## Limitations

* fills only: a COMPLETE here is a filled deficit-side buy, not the quote L5 posted; the ceiling is the distribution of filled completes, resting completes that never filled are invisible
* state price band is the previous fill's price (the live model's market-price proxy), not the book at the fill; it can be the opposite side's price
* engine_avg uses the canonical average-cost basis (cum spend / cum qty), which includes every earlier lot on the surplus side, matched or not; marginal_lifo is the alternative reading
* window_realized is an average-cost allocation at settlement, not a per-fill fact; it is the number the ledger locks
* maker/taker is known only inside takerOnly coverage (Aug 28-31); pair costs are not conditioned on it
* 3431 completes exceed the surplus (overshoot); pair costs there cover the matched part and dollar shares use completed_usd
* two lifetime SELL rows are applied to the lot ledger LIFO and otherwise ignored
* pair costs exclude rebates and fees by construction (owner: separate the two questions)
* the canonical file has no fills between 2026-04 and 2026-08 (Mar 1931 / Apr 3338 / Aug 66715 rows); 'by month' is three eras and the August cohort dominates every pooled number

Generated by `python3 -m src.l5_completion_ceiling_study`; JSON: `analysis/l5/l5_completion_ceiling.json`.
