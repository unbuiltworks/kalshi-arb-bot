# LIVE-L5-EXEC-1 — authorization request. **STATE: DISARMED**

A **single-leg execution-measurement experiment**. Built, tested, not armed.
No order, no preview, no cancel, no signature, no capital moved. **47 tests
pass.** S3 and both prior micro fingerprints are preserved unchanged and
verified by test.

## Frozen fingerprint
```
b1dbe7f1e628e6f9fc8e6459a2a5b95098ada2f16ae872f10081d5dc9bb82e06
```

## MAXIMUM POSSIBLE LOSS: **$0.0099**

Closed arithmetic, no estimate:

```
exactly ONE order  x  0.01 shares (venue minimumTradeQty)
                   x  0.99 (the venue's own 0.01-grid ceiling)
                   =  $0.0099
```

**A total loss is under one cent.** The bound needs no behavioural
assumption: it holds even if the order fills instantly and settles
worthless, because the experiment permits exactly one order and the
quantity is the venue minimum.

## What this experiment is, and is not

| | |
| --- | --- |
| **question** | does a genuine minimum-size near-touch maker order **rest/fill**, how long does it rest, what happens **ahead of it** in the book, is the execution **venue-confirmed passive**, and what are its **+1/+5/+10/+30/+60/settlement** markouts? |
| class | `EXECUTION_MEASUREMENT` |
| **not** | a profitable strategy, or evidence that a 0.99 pair cost is attractive |
| **not** | a test of L5's economics — it is **one leg**, so matched-pair economics are **N/A**, reported as N/A and never as zero |
| **not** | the economically reserved candidate — that is `0522b0da…`, untouched and still unarmed |

## Why it carries no pair-cost reservation

The 0.96 reservation is our **`PAIR_ECONOMICS` design choice, not an
observed L5 rule**, and our own work **REFUTED** a hard L5 pair-cost ceiling
(running pair-cost control = REFUTED; ~0.98 is ambient complement-sum
arithmetic, 7× noisier than the market's own). At the observed **0.01
spread** the mechanically available pair cost is **~0.99**, so a 0.96 gate
refuses to quote and the experiment could never run.

The two questions are kept strictly apart, and a test asserts the other
fingerprints are unchanged:

- **S3 / reserved candidate** → does the frozen 0.96 reservation find
  qualifying economic opportunities?
- **LIVE-L5-EXEC-1** → what happens to our actual minimum-size near-touch
  maker order?

## The only L5 behaviour used

**Near-touch passive exposure**, offset **0 ticks** — the most strongly
evidenced L5 behaviour (median offset 0 at every inferred level rank,
`rho(rank, offset) = 0.138`).

Explicitly excluded: **no complement chasing, no martingale, no averaging,
no second order, no automatic repeat.**

## Risk envelope

| | |
| --- | --- |
| markets | **1**, `btc-updown-15m` |
| orders in the whole experiment | **1** (a second is refused in code, *including* to repair a loss) |
| quantity | **0.01 shares**, the venue minimum, read live, fail-closed |
| maximum possible loss | **$0.0099** |
| maker | post-only `participateDontInitiate`; **taker not permitted** |
| partial fills | permitted; never promoted to a full fill |
| BBO | **`AuthBook` only**; gateway forbidden; **2.0 s** bound (validated 31/31) |
| tick | venue `orderPriceMinTickSize` as a **grid**; `tick_universal_constant: None` — 0.01 is *not* a new hardcoded constant, and a 0.005 market still validates |
| TTE shutdown | 120 s; cancel at window end |
| deadman | market-scoped cancel-all in `finally` on every exit path; POSTs never retried; no auto-restart, no auto-scaling |
| daily stop | −$1.00 inherited unchanged (unreachable at $0.0099 exposure) |

## Captured at placement

`side` · `price` · `bbo_bid` · `bbo_ask` · `spread` ·
`depth_ahead_at_our_price` · `quantity` · `tte_s` · `book_timestamp` ·
`book_age_s` · `order_timestamp`

Depth ahead is flagged **anonymous** — it bounds queue position, it does not
reveal it. A stale book is **flagged, not silently accepted**.

## Captured afterwards

Every venue lifecycle event with its timestamp, observable queue depletion
at our price, rest duration, **venue-confirmed liquidity role**, and
**actual commission**.

Lifecycle machinery is **reused** from the already-tested micro ledger, so
the seven states, the partial-never-promoted rule, the venue-role
requirement and the markout-horizon honesty all carry over:

```
SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
          → CANCELLED → SETTLED → CASH_CONFIRMED
```

`submitted_post_only` is recorded **separately** and never implies maker.
An unresolved role reports **`UNRESOLVED`**, never a guess. A modelled fee
can never be reported as actual.

## Arming — four keys, isolated, yours alone

`PMUS_EXEC1_LIVE=YES` · `PMUS_EXEC1_SESSION_ID=<id>` ·
`PMUS_EXEC1_ARM=<same id>` · `PMUS_EXEC1_GATE_EVIDENCE=<exact slug>`

Tested both directions: arming `PMUS_Q1_*`, `PMUS_MICRO_*`,
`PMUS_CANARY_*` or `PMUS_CASE1_*` **cannot** arm EXEC-1, and arming EXEC-1
arms **nothing else**. **I did not set any of them.**

Proposed host unchanged: `l5-live-probe-oneshot`, where credentials already
exist, `restartPolicyType: NEVER`, and the contamination check passed — the
only running credentialed service is the recorder, which is order-incapable
both structurally and by its own report.

## What a result will and will not mean

A fill would establish that **we can obtain real passive execution at the
venue minimum** — queue behaviour, rest time, venue-confirmed role, real
markouts. It would **not** establish that the L5 mechanism is profitable;
matched economics are N/A here by construction.

A **no-fill** is equally informative and is a real result: it would say a
minimum-size order at the touch does not get served inside a 15-minute
window, which bears directly on whether the mechanism is reachable for us
at all.

**Remaining DISARMED.** To proceed, authorize this exact fingerprint
(`b1dbe7f1…2e06`) and the $0.0099 bound, and set the four keys yourself.
