# August L5 forensic replay -- frozen S1 against the real tape

**DIAGNOSTIC HISTORICAL REPLAY ONLY.** Not validation. Not part
of the prospective 25-market Shadow S1 cohort. These artifacts
must not be used to modify Candidate S1 while that cohort is
running. Candidate fingerprint asserted unchanged:
`441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`.

* window: `btc-updown-15m-1787300100`, 900s, UTC 1787300100..1787301000
* L2 seconds resolvable: **900 / 900**  (unresolvable 0)
* arm: **B_sensitivity_with_chain_prints**

## 1. The side-by-side result

| | L5 (actual) | S1 (frozen, replayed) |
|---|---:|---:|
| fill legs | 398 | 10 |
| UP inventory | 4813.44 | 70.83 |
| DOWN inventory | 5041.89 | 165.27 |
| matched inventory | 4813.44 | 70.83 |
| residual | 228.45 | 94.44 |
| pair cost | 0.991077 | 0.935714 |
| orders placed | n/a | 11 |

S1's uncertain verdicts, recorded and **excluded from**
**economics**: `{"UNCERTAIN_PARTIAL": 1}`.

## 2. First material divergence

No second in this window has L5 holding a matched pair
while S1 holds none, so the designated marker never fires.

| marker | second | offset |
|---|---:|---:|
| `D1_first_l5_fill` | 1787300107 | 00:07 |
| `D2_l5_matched_while_s1_zero` | never | -- |
| `D3_first_s1_evidenced_fill` | 1787300101 | 00:01 |
| `D4_residual_gap_ge_one_leg` | 1787300101 | 00:01 |

## 3. What the trace says caused it

**M1 -- mint access.** 91 of 398 L5 fill legs (22.9%) priced at or
above the prevailing ask one second earlier AND were
mint-feasible -- 2711.18 shares. A resting bid cannot price above
the ask, and S1's action set is resting bids only, so this
inventory was unreachable to it by construction. A mint was
feasible on the touch itself in 34 of 900 resolvable seconds.

**M2 -- queue position.** S1 quotes at offset 0 ticks, i.e. it
joins the touch behind the size already resting: queue ahead
p50 **15.44**, p90 57.04, max 160.0 shares over 11 placements. The
frozen classifier returned `QUEUE_CLEARED_FRONT_OF_BOOK`
**0** times -- the queue drained, S1 would be at the front,
still resting. That is explicitly not a fill.

**M3 -- evidence reachability.** An S1 order was resting on
952 side-ticks; the touch moved strictly through its price, the
one reachable evidence standard in this arm, **5** times
(rate 0.005252 per resting side-tick).

**M4 -- suppression accounting.** 59 ticks fell inside the
TTE shutdown and 0 had no order resting on either side, so a
quoting gap is not being read as a fill failure.

## 4. What happens after the divergence

No designated divergence, so there is no trajectory to
trace.

## 5. Full trace

`TRACE_B_sensitivity_with_chain_prints.csv` / `.jsonl` carry one row per second with
timestamp, TTE, UP/DOWN BBO and depth, L5's fills and ledger,
S1's resting orders, evidenced fills and ledger.
