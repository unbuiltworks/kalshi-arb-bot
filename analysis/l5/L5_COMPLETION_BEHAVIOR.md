# What L5 does when completing a pair

Windows with a first leg: **2216**  
Windows that completed: **1610** (**72.7%**)

## How long L5's completion takes

| n | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| 1610 | 0.0 | 7.0 | 53.5 | 166.0 | 735.0 |

| completed within | windows | share of completions |
|---|---|---|
| 5s | 122 | 7.6% |
| 10s | 211 | 13.1% |
| 15s | 328 | 20.4% |
| 30s | 550 | 34.2% |
| 31s | 561 | 34.8% |
| 60s | 887 | 55.1% |
| 120s | 1279 | 79.4% |
| 300s | 1597 | 99.2% |

**Share of L5's completions that land inside our 31 s window: 34.8%**

## Does L5 reprice?

Among completions that got at least two fills — the only ones that CAN show repricing — **1266 of 1285 (98.5%) happened at more than one price.**  
(325 single-fill completions excluded: one fill has one price by arithmetic; it is evidence of neither repricing nor its absence.)

Are fast completions just one lucky resting order? No — they carry MORE prices, not fewer:

| completions | n | median distinct prices | median fills |
|---|---|---|---|
| within_31s | 561 | 7 | 8 |
| over_31s | 1049 | 3 | 3 |

## Ours, for comparison

- COMPLETE fill rate **13.7%**, no-fill **68.5%**, rejected **17.9%**
- median rest when filled **8.61s**, when not filled **30.85s**

## Observability limit

FILLS ONLY. L5's orders, cancels and resting prices are not observable, so seconds_to_complete is how fast his completion HAPPENED, not how fast he repriced. Distinct prices on the completing side ARE repricing evidence: one resting order fills at one price.
