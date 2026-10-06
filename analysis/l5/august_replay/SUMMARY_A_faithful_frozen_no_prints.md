# August L5 forensic replay -- frozen S1 against the real tape

**DIAGNOSTIC HISTORICAL REPLAY ONLY.** Not validation. Not part
of the prospective 25-market Shadow S1 cohort. These artifacts
must not be used to modify Candidate S1 while that cohort is
running. Candidate fingerprint asserted unchanged:
`441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`.

* window: `btc-updown-15m-1787300100`, 900s, UTC 1787300100..1787301000
* L2 seconds resolvable: **900 / 900**  (unresolvable 0)
* arm: **A_faithful_frozen_no_prints**

## 1. The side-by-side result

| | L5 (actual) | S1 (frozen, replayed) |
|---|---:|---:|
| fill legs | 398 | 10 |
| UP inventory | 4813.44 | 70.83 |
| DOWN inventory | 5041.89 | 165.27 |
| matched inventory | 4813.44 | 70.83 |
| residual | 228.45 | 94.44 |
| pair cost | 0.991077 | 0.867143 |
| orders placed | n/a | 11 |

S1's uncertain verdicts, recorded and **excluded from**
**economics**: `{"UNCERTAIN_PARTIAL": 5}`.

## 2. First material divergence

**t = 1787300134 (00:34 into the window, TTE 866s).** D2: the first second at which L5 holds a matched pair and S1 holds none. Chosen as the marker because it is the first divergence in POSITION rather than in quote.

| marker | second | offset |
|---|---:|---:|
| `D1_first_l5_fill` | 1787300107 | 00:07 |
| `D2_l5_matched_while_s1_zero` | 1787300134 | 00:34 |
| `D3_first_s1_evidenced_fill` | 1787300102 | 00:02 |
| `D4_residual_gap_ge_one_leg` | 1787300102 | 00:02 |

## 3. What the trace says caused it

**M1 -- mint access.** 91 of 398 L5 fill legs (22.9%) priced at or
above the prevailing ask one second earlier AND were
mint-feasible -- 2711.18 shares. A resting bid cannot price above
the ask, and S1's action set is resting bids only, so this
inventory was unreachable to it by construction. A mint was
feasible on the touch itself in 34 of 900 resolvable seconds.

**M2 -- queue position.** S1 quotes at offset 0 ticks, i.e. it
joins the touch behind the size already resting: queue ahead
p50 **45.0**, p90 163.8, max 304.2 shares over 11 placements. The
frozen classifier returned `QUEUE_CLEARED_FRONT_OF_BOOK`
**0** times -- the queue drained, S1 would be at the front,
still resting. That is explicitly not a fill.

**M3 -- evidence reachability.** An S1 order was resting on
1099 side-ticks; the touch moved strictly through its price, the
one reachable evidence standard in this arm, **10** times
(rate 0.009099 per resting side-tick).

**M4 -- suppression accounting.** 59 ticks fell inside the
TTE shutdown and 0 had no order resting on either side, so a
quoting gap is not being read as a fill failure.

## 4. What happens after the divergence

| at | TTE | L5 matched | L5 resid | L5 pair | S1 matched | S1 resid | S1 pair | S1 fills |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 00:34 | 866 | 60.00 | 64.09 | 0.988111 | 0.00 | 70.83 | None | 3 |
| 01:34 | 806 | 500.97 | 141.25 | 0.97904 | 70.83 | 23.61 | 0.93 | 7 |
| 02:34 | 746 | 987.40 | 62.55 | 1.019889 | 70.83 | 47.22 | 0.918 | 8 |
| 03:34 | 686 | 1219.97 | 106.25 | 0.998808 | 70.83 | 94.44 | 0.867143 | 10 |
| 04:34 | 626 | 1234.44 | 253.95 | 0.975881 | 70.83 | 94.44 | 0.867143 | 10 |
| 05:34 | 566 | 1733.80 | 29.95 | 0.99783 | 70.83 | 94.44 | 0.867143 | 10 |
| 06:34 | 506 | 1820.02 | 94.36 | 1.003075 | 70.83 | 94.44 | 0.867143 | 10 |
| 07:34 | 446 | 1890.75 | 97.93 | 1.002513 | 70.83 | 94.44 | 0.867143 | 10 |
| 08:34 | 386 | 2133.38 | 106.45 | 0.984776 | 70.83 | 94.44 | 0.867143 | 10 |
| 09:34 | 326 | 2280.55 | 189.18 | 0.981487 | 70.83 | 94.44 | 0.867143 | 10 |
| 10:34 | 266 | 2547.97 | 183.24 | 0.981216 | 70.83 | 94.44 | 0.867143 | 10 |
| 11:34 | 206 | 3163.10 | 433.17 | 0.973308 | 70.83 | 94.44 | 0.867143 | 10 |
| 12:34 | 146 | 3721.27 | 306.93 | 0.985393 | 70.83 | 94.44 | 0.867143 | 10 |
| 13:34 | 86 | 4380.89 | 348.28 | 0.985188 | 70.83 | 94.44 | 0.867143 | 10 |
| 14:34 | 26 | 4813.44 | 228.45 | 0.991077 | 70.83 | 94.44 | 0.867143 | 10 |
| 14:59 | 1 | 4813.44 | 228.45 | 0.991077 | 70.83 | 94.44 | 0.867143 | 10 |

## 5. Full trace

`TRACE_A_faithful_frozen_no_prints.csv` / `.jsonl` carry one row per second with
timestamp, TTE, UP/DOWN BBO and depth, L5's fills and ledger,
S1's resting orders, evidenced fills and ledger.
