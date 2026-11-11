# Why COMPLETE legs fail

Orders in sample: **1258**

| relation | orders | filled | no-fill | rejected |
|---|---|---|---|---|
| open | 409 | 55 (13.5%) | 267 (65.3%) | 87 (21.3%) |
| extend | 183 | 46 (25.1%) | 74 (40.4%) | 63 (34.4%) |
| complete | 666 | 91 (13.7%) | 456 (68.5%) | 119 (17.9%) |

## Completes that filled vs completes that did not

| bucket | n | median rest_s | median px |
|---|---|---|---|
| filled | 91 | 8.61 | 0.555 |
| no_fill_cancelled | 456 | 30.85 | 0.6 |

## Fill rate by distance from the opposite ask

**UNAVAILABLE** — no accepted order carries fresh_opp_ask yet; the control snapshot has not accumulated. Answering from rejection rows would be the selection bias this module refuses.

Computed over ALL orders of a relation. Reading the composition of rejections as the composition of completion failures said rejection was the problem; on this denominator 68.5% of completes die unfilled and 17.9% are rejected.
