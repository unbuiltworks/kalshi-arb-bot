# COMPLETE re-quote on a moved touch — replay against the tape

Bar `L5_COMPLETE_REQUOTE_OFFLINE_BAR_v1` sha `699cc8205fd23383` — **OFFLINE_EVIDENCE_STRONG**

| | |
|---|---|
| unfilled COMPLETEs replayed | 141 |
| a re-quote would have fired | 72 |
| ...and the book reached that price | 45 |
| priced counterfactual completions | 15 across 15 markets |

## Pair cost — the part a fill count would have hidden

| | |
|---|---|
| above par (locks a loss) | 10 (66.7%) |
| at or below par | 5 |
| median pair cost | 1.01 |
| worst pair cost | 1.19 |

## Counterfactual economics

| conversion | completions | units | locked pair $ | residual loss avoided $ | net $ |
|---|---|---|---|---|---|
| 100% | 15 | 54.09 | -1.7128 | 17.9281 | **16.2153** |
| 50% | 7 | 27.04 | -0.8564 | 8.9640 | **8.1076** |
| 25% | 3 | 13.52 | -0.4282 | 4.4820 | **4.0538** |

> residual is charged at -0.331449 per unit, the cohort mean. It carries the variance of a coin flip and the selection of which markets stranded, and it is the weakest link in the net.

## Costs charged

- extra orders on the #93 surface: **72**, expected additional rejections **13.4** at the observed 0.1866 rate
- a rejected complete at a non-crossing price revokes the complement proof; three consecutive end the session
- sessions exceeding the extended action budget: **0** of 39 with re-quotes

## Frozen bar

| check | result |
|---|---|
| net_positive_at_full_conversion | PASS |
| net_positive_at_0.5_conversion | PASS |
| additional_completions_min | PASS |
| distinct_markets_min | PASS |
| not_concentrated_in_one_market | PASS |
| no_session_loses_an_opening_to_budget | PASS |

Authorises designing a live A/B: **True**. Authorises shipping: **False**.

## Bounds

- **aggregate_not_sequence** — rest_book is an aggregate: min_opp_ask and max_touch are extrema over one window and cannot be sequenced. Every conversion figure is therefore a BOUND under an assumption, never an effect size, and the report must say so at every point where a number appears.
- **one_sided_trigger_detection** — max_touch only reveals the touch moving UP. A touch that moved down would also fire a re-quote and is invisible here, so the trigger count is a LOWER bound while the conversion is an UPPER bound. The two errors do not cancel and must not be described as offsetting.
- **no_live_read** — this bar is scored on the pre-change tape only. It may not be re-scored on data produced after any COMPLETE re-quote ships, and it may never read L5_COMPLETION_EXTENSION_GATE_v1's evidence window.

## Non-contamination

L5_COMPLETION_EXTENSION_GATE_v1 is frozen and running. This bar shares no threshold with it, reads no forward evidence, and its verdict has no effect on that gate's sample minimums or criteria. Any live trial designed from this bar must be separable in the tape by an explicit arm label, so the extension's attribution stays clean.

---

## CORRECTION 2026-10-29: this bar's net is invalidated by settled evidence

The `+16.2153` net at full conversion above is **not usable**. $17.9281 of it
is "residual loss avoided", charged at **−$0.331449 per unit** from a cohort
mean. Campaigns 6–8 have since measured residual settlement directly on 7
stranded legs totalling 12.82 contracts: **+$0.6005, i.e. +$0.0468 per unit
— the opposite sign.**

Recomputed at the measured rate, full conversion is **−$4.24**, not
`+16.2153`. The sign of the headline flips.

The bar's own counterfactual median pair cost was **1.01** with 66.7% above
par, which is the same above-par problem seen from the other side, and the
settled break-even completion pair cost is **0.9532** against an achieved
0.9530 — so converting strands into above-par pairs destroys value rather
than rescuing it.

This does not impeach the bar's mechanics or its stated bounds
(`aggregate_not_sequence`, `one_sided_trigger_detection`), which were
correct and explicit. It impeaches the residual price it was multiplied by.
`Authorises shipping: False` stands, and is now stronger: do not design a
live A/B on this evidence either.

See `analysis/l5/campaign9/THROUGHPUT_AND_INCENTIVES.md` and
`analysis/l5/campaign8/ACCOUNTING_AND_DWELL_VERDICT.md`.
