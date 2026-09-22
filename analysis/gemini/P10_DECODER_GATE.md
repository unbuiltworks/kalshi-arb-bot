# The decoder gate — pre-registered 2026-09-22T22:55Z, before the data that decides it

**Written before the sample that will be measured against it.** Passing unlocks
exactly one thing: reconstructing BTC5m/BTC15m with the frozen decoder and
running ONE final pre-rebate evaluation. Failing stops the Gemini lane.

## The five thresholds

| # | gate | threshold | why this number |
|---|---|---|---|
| **G1** | decoded n, unique `(symbol, tid)`, post-capture, non-stale, BTC only | **≥ 150** | at 150 decoded with 0 H2 the exact 95% upper bound on the mirror rate is **2.0%**; the P10-D bound shows arm P&L swinging from −46 to +47 between a 0% and a 100% mirror reading, so a 2% mis-assignment is the most that can be waved through |
| **G2** | ambiguous rate, of observed | **≤ 25%** | above this the decoder is refusing more than it decides, and the reconstructed series is mostly holes |
| **G3** | agreement between the two independent decoders on rows both classify | **≥ 98%** | two rules that disagree more than 1 in 50 are not measuring one thing |
| **G4** | 95% one-sided upper bound on the true H2 rate | **≤ 2.0%** | the historical corpus is read entirely as H1; this is the size of the error that reading can carry |
| **G5** | agreement with the spec's structural prediction (decodable when \|p−0.5\| > w/2) | **≥ 80%** | the decoder's own model of when it should work has to hold, or its successes are luck |

All five must pass. G1 and G4 are one statement seen twice: n large enough, H2
rare enough.

## What passing does and does not license

**Does:** freeze the decoder by content hash; reconstruct BTC5m/BTC15m; run one
pre-rebate evaluation of the L5 sequential/inventory policy on causally
available information; recompute its accounting independently; add the observed
rebate afterwards on its own line.

**Does not:** license a tuning round, a new threshold search, a new strategy
branch, or a live order. If the evaluation is negative the answer is B.

## The stopping rule, so this cannot run forever

The BTC recorder yields roughly **20–30 decoded rows per hour** at its measured
cadence. G1 therefore needs about **5–7 hours** of capture. If the gate has not
passed by then, or the H2 rate has risen above 2%, the lane is declared
**GEMINI_STOP** and the effort moves to fresh peak-hours PMUS. Collecting past
that point buys precision on a lane whose economics have already failed three
independent pre-rebate tests.

## What is already known and does not depend on the gate

* **maker fee on our most recent 18 maker fills: $0.00**, in the venue's own
  execution ledger and in the fill events, per row, by symbol
* **rebate actually paid for those fills: $0.05**, venue row 21320, status PAID
* **BTC5m median book spread 0.280; BTC15m 0.060**
* the slate families are dormant — their newest prints predate our first book
  poll by 1.5 to 10.5 hours — so they cannot validate anything on this timescale
