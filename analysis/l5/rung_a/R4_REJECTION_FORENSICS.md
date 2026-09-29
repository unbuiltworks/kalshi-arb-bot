# Why `CSDKDZZVRYAQ` was rejected — forensics

Raw `/v1/order/{id}` records recovered verbatim, read-only, deployment
`aa1f1ba2` (01:59:10Z). **The venue exposes no rejection reason anywhere**, so
the cause is labelled **UNKNOWN**. What follows is what the artifacts do and do
not rule out.

## The two raw records, side by side

| field | UP `CSDKAWYZCYBQ` | DOWN `CSDKDZZVRYAQ` |
|---|---|---|
| `side` | `ORDER_SIDE_BUY` | **`ORDER_SIDE_SELL`** |
| `intent` | `ORDER_INTENT_BUY_LONG` | **`ORDER_INTENT_BUY_SHORT`** |
| `outcomeSide` | `OUTCOME_SIDE_YES` | `OUTCOME_SIDE_NO` |
| `marketMetadata.outcome` | `Yes` | `No` |
| `price` | `0.26` | `0.27` |
| `quantity` | 10 | 10 |
| `type` | `ORDER_TYPE_LIMIT` | `ORDER_TYPE_LIMIT` |
| `tif` | `TIME_IN_FORCE_GOOD_TILL_CANCEL` | **`TIME_IN_FORCE_GOOD_TILL_CANCEL`** |
| `action` | `ORDER_ACTION_BUY` | `ORDER_ACTION_BUY` |
| `createTime` | 00:46:35.897011 | 00:46:36.002028 |
| `insertTime` | 00:46:35.897962 | **00:46:36.003236** |
| `state` | `ORDER_STATE_FILLED` | **`ORDER_STATE_REJECTED`** |
| `cumQuantity` / `leavesQuantity` | 10 / 0 | **0 / 0** |
| `commissionsBasisPoints` | `0` | `0` |
| reject reason / code / text | — | **ABSENT** |

Probed and all 404: `/v1/order/{id}/status`, `/v1/order/{id}/fills`,
`/v1/order/{id}/events`. There is no per-order event or reason route.

**Rejected 1.2 ms after creation** (`createTime` → `insertTime`), against the
UP order's 10.7 s to fill. This was a synchronous validation or risk rejection
at insert, not a market-driven outcome.

## Hypotheses

| hypothesis | verdict | evidence |
|---|---|---|
| **wrong outcome mapping** | **DISPROVED** | the venue's own record says `outcome: "No"`, `outcomeSide: OUTCOME_SIDE_NO`, `intent: ORDER_INTENT_BUY_SHORT` — exactly what we intended |
| **NO=0.73 → wire 0.27 inversion wrong or applied twice** | **DISPROVED** | the venue represents our NO buy as `ORDER_SIDE_SELL` at `0.27` with `intent BUY_SHORT`. Buying NO at 0.73 own-terms *is* selling YES at 0.27, and the venue agrees. Applied once, correctly. `mandate_commit` independently recorded `price_wire 0.2700` with `cash_at_risk 7.30000` = 10 × 0.73, so the guard and the venue read the same order the same way. The serializer is not changed. |
| **IOC vs FOK semantics / insufficient immediately executable quantity** | **DISPROVED for this order** | `tif: TIME_IN_FORCE_GOOD_TILL_CANCEL`. It was a resting maker quote, not a marketable leg; there was no immediate-execution requirement to fail |
| **tick / minimum-size validation** | **DISPROVED** | market grid `orderPriceMinTickSize 0.01`, `minimumTradeQty 0.01`; price 0.27 is on the grid, quantity 10 exceeds the minimum |
| **market-state validation** | **DISPROVED** | `MARKET_STATUS_OPEN` at the 00:46:35.034 pin, and the sibling UP order on the same market filled 10.7 s later |
| **duplicate / order validation** | **NO EVIDENCE** | distinct id, distinct side, no duplicate marker in the record |
| **insufficient collateral / buying power** | **NOT EXCLUDED** | the second leg needed $7.30 on top of the first's $2.60 ($9.90 total). No balance snapshot was taken at 00:46:35.909, so this cannot be excluded from the artifacts — only the post-hoc balance is available |
| **post-only / participate-don't-initiate crossing rejection** | **LEADING, UNTESTED** | our REST bodies set `participateDontInitiate: true`; the raw record does **not** echo that field, so it cannot be confirmed from here. A PDI sell at 0.27 is rejected rather than rested if the YES **bid** was ≥ 0.27 at that instant. Both legs were quoted "at our touch", and a 1.2 ms synchronous rejection is the shape a PDI violation takes |

## The one test that would settle it, and why it is not run here

The journal holds **215 `native_tick` records** for this window, each carrying
the book state the decision was taken against. The tick at ~00:46:35.9 would
show the YES bid/ask at submission, and a bid ≥ 0.27 would confirm the PDI
crossing hypothesis while a bid < 0.27 would refute it. The current audit lane
dumps only five record kinds and not `native_tick`, so that check needs one
more read-only extraction; it does not need another live probe.

**Until then the cause is UNKNOWN.** It is specifically NOT being attributed to
liquidity: `leavesQuantity 0` with a GTC order means the venue refused to
create it, which is not the same as no counterparty.

## What this means for complement execution

Correct REJECTED handling fixes the accounting, and it is fixed. It does not
fix complement execution: if the cause is PDI crossing, the frozen policy's
two-sided quote at our own touch can have one side refused outright whenever
the touch is one tick wide — which is a systematic execution constraint on the
0.99 pair, not a one-off. That possibility is the reason the execution-aware
replay matters and the reason no rejection probability may be inferred from
n = 1.

---

# P0-B RESULT: the PDI hypothesis CANNOT be settled from existing data

Attempted 2026-09-29T02:22Z, read-only, deployment `aac88b7c`, extracting the
`native_tick` records around 00:46:35.909277.

## What was established

| item | value | source |
|---|---|---|
| submitted wire side / price | **YES SELL @ 0.27** | venue raw record: `side ORDER_SIDE_SELL`, `intent ORDER_INTENT_BUY_SHORT`, `price {value "0.27"}` |
| PDI / post-only setting | **TRUE — enabled** | the production serializer, reproduced byte-for-byte: `NMP.body_for(REST down @0.73)` yields `participateDontInitiate: true`, `price {value "0.27"}`, `outcomeSide OUTCOME_SIDE_NO`, `tif GTC`, `quantity 10` — matching every field the venue echoed |
| best YES bid / ask at 00:46:35.909 | **UNKNOWN — NOT PERSISTED** | see below |
| derived NO bid / ask | **UNKNOWN** | follows from the above |
| marketable at arrival? | **UNDETERMINED** | cannot be computed without the touch |

## Why the book is unrecoverable

Three independent sources, none of which carries it:

1. **`native_tick` does not record the book.** The wrapper wrote
   `{k: v for k, v in res.items() if k != "submitted"}`, i.e. only
   `acted`, `intents`, `cancelled`, `lock`, `fills_booked`,
   `replacement_blocked` — the verdict, not the input. The four rows around
   the submission confirm it.
2. **The authenticated WS frames were never journalled.** 8,594 frames arrived;
   none was persisted.
3. **The venue's REST book 404ed for this market throughout the window.** The
   probe recorded `PMUS_PREFLIGHT_DEGRADED book_readable HTTP 404` at
   00:46:35.4, and `pmus-btc15m-tape` independently recorded, at 00:46:43.98,
   `kind: error HTTPError:HTTP Error 404` and `kind: book_error HTTPError:HTTP
   Error 404` for `cpc-btc-updown-15m-2026-09-29-0045z`. Two services, same
   answer — so this was a venue-wide condition for that market, not a probe
   fault, and it also independently vindicates making that check non-blocking.

**Verdict: PDI-cross is neither confirmed nor eliminated.** It remains the
leading hypothesis on the strength of the 1.2 ms synchronous rejection and
`participateDontInitiate: true`, and the evidence needed to test it does not
exist. The cause of `CSDKDZZVRYAQ`'s rejection stays **UNKNOWN**.

## Bonus finding from the same four ticks

At **00:46:36.112** and **00:46:36.199** — 200 ms and 290 ms after submission —
the engine was ALREADY trying to re-quote both sides and was refused:

```
seq 17  cancelled [{side down, oid CSDKDZZVRYAQ, state CANCEL_FAILED}]
        replacement_blocked [{clear false, side down, reason CANCEL_FAILED}]
seq 18  cancelled [{side up,   oid CSDKAWYZCYBQ, state CANCEL_FAILED}]
        replacement_blocked [{clear false, side up,   reason CANCEL_FAILED}]
```

So the window was blocked from its **third tick**, not from the close-out
sweep. All 215 ticks after that were spent unable to quote. The
`CANCEL_FAILED`-on-a-terminal-order defect was therefore not a tidy-up
problem: it cost the entire window within 300 ms of the first order.

## The change that makes this answerable next time

`native_tick` now records the book it decided against — bid, ask, bid_qty,
ask_qty, status, state. Observability only; no policy, timing or sizing
behaviour is touched.
