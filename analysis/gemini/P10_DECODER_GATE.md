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

## Derived consequence, recorded 2026-09-22T23:15Z — G1's 150 assumed zero H2

G1 and G4 are the same statement, and G1's threshold of **150** is the n at
which **zero** H2 rows clear G4. One H2 row has since been observed, so the n
that clears G4 is larger. The thresholds are unchanged; this is their
arithmetic, computed by `p10h_gate.binom_upper`:

| H2 observed | n needed for the 95% upper bound to reach 2.0% |
|---|---|
| 0 | 149 |
| **1** | **236** |
| 2 | 313 |
| 3 | 386 |
| 4 | 456 |

**The binding target is therefore n ≥ 236 while the H2 count stays at 1.** Each
further H2 row moves the target by roughly 75–80, which is about an hour of
capture — so a rising H2 count fails the gate on the stopping rule rather than
on the bound, exactly as intended.

## Scoring is one command

    python3 analysis/gemini/p10h_gate.py BTC      # exit 0 = passed

It reads every capture in `p10_capture/`, `.partial` files included, counts
rows unique by `(symbol, tid)`, and where two recorders saw the same print it
keeps the better-observed classification. It does not get to argue with the
thresholds above.

## Measured yield, 2026-09-22T23:15Z

76 decoded BTC rows from ~2h of capture. By contract family, decoded / rows:

| family | rows | decoded | median book width |
|---|---|---|---|
| BTC05M | 104 | 48 (46%) | 0.250 |
| BTC15M | 64 | 24 (38%) | 0.090 |
| BTC hourly ladder | 23 | 5 (22%) | 0.070 |
| ETH / SOL / XRP | 15 | 2 | — |
| slate (HORMUZ, DEMNOM, WXHIGH) | 267 | **0** | no book overlap |

The slate families contribute 267 rows and zero decoded, confirming they are
dormant on this timescale. Three BTC recorders now run on disjoint duration
families; the constraint is how many BTC contracts are watched, not the poll
rate per contract.

## Relaunching a recorder — the exact pattern, because the obvious one dies

A recorder started with a plain `nohup … &` from a tool shell is killed when
that shell exits. Three recorders were lost this way at about 23:40Z and the
loss was invisible until the next gate score. Use `setsid` and `disown`:

    cd /home/user/kalshi-arb-bot
    D=analysis/gemini/p10_capture
    S=$(date -u +%Y%m%dT%H%M%SZ)
    setsid nohup env P10_RATE=4.5 timeout 10800 python3 analysis/gemini/p10a_capture.py \
      $D/btc15_$S.jsonl 178 4 Crypto BTC15M < /dev/null > /tmp/p10_btc15.log 2>&1 & disown

Three processes, one per duration family, `BTC15M` / `BTC2609` / `BTC05M`, so
they cover disjoint contracts and each owns its own rate bucket. Verify with
`pgrep -af p10a_capture.py` — an empty result is a dead gate, not a quiet one.
