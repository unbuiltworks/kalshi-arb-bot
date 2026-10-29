# Probe 04 — authoritative reconciliation, and the correction from stale local state

The local state is **not** hand-edited. This is the record that explains the
difference between what our books said at 13:13:30Z and what the venue says.

Every figure below is from a GET-only read by `pmus-readonly-recovery`
(service `247304c7-…`, deployment `7ed5a3bb`/`440722a8`, `post_capable:
false`) on 2026-09-25T13:40Z and 13:52Z.

## PROBE04 FINAL ACCOUNTING

| | our stale local state (13:13:30Z) | the venue (authoritative) |
|---|---|---|
| order `CQ5NPBADMYC3` | live, resting | `ORDER_STATE_FILLED` |
| L5 desired qty | — | — |
| executable qty (quantized) | 2.25 | `quantity` 2.25 |
| limit price | 0.32 | `price.value` "0.32" |
| filled qty | **0** | **`cumQuantity` 2.25** |
| remaining | 2.25 | **`leavesQuantity` 0** |
| average fill price | — | **`avgPx` 0.2900** |
| commission | — | **0.0300** |
| outstanding exposure | **$0.720000** | **$0** |
| committed exposure | $0 | 2.25 × 0.2900 = **$0.6525** |
| live orders | **1** | **0** (`/v1/orders/open` → `{"orders": []}`) |
| position | 0 | **0 now** — settled; was 2.25 YES |
| residual | 0 | 0 |

Fill timestamps: `createTime` 13:01:46.845851602Z, `insertTime` /
`lastTransactTime` **13:01:46.847706680Z**. The ACK said `filled_qty 0`; the
order was complete **two milliseconds later**.

### Settlement

```json
"/v1/markets/cpc-btc-updown-15m-2026-09-25-1300z/bbo" →
 {"marketSlug": "...-1300z", "settlementPx": {"value": "0.0000"},
  "state": "MARKET_STATE_EXPIRED", "currentPx": {"value": "0.0100"},
  "lastTradePx": {"value": "0.0100"}, "sharesTraded": "111721.6200",
  "bestAsk": null, "bestBid": null,
  "lastPriceSample": {"longPx": "0.0100", "shortPx": "0.99",
                      "ts": "2026-09-25T13:15:12.890991198Z"}}
```

`settlementPx` **0.0000**, market **EXPIRED**. We held the **long/YES** side
(`outcomeSide: OUTCOME_SIDE_YES`, `intent: ORDER_INTENT_BUY_LONG`), and the
contract's long side pays when the close is at or above the open. It settled
worthless.

### Cashflow

```json
"/v1/account/balances" → {"balances": [{"currentBalance": 62.36645,
  "buyingPower": 62.36645, "availableToWithdraw": 62.36645,
  "assetNotional": 0, "assetAvailable": 0, "openOrders": 0,
  "unsettledFunds": 0, "pendingWithdrawals": [], "currency": "USD"}]}
```

- balance before Probe 04 (Probe 03 preflight, 02:15Z; nothing traded in
  between): **63.04895**
- balance now: **62.36645**
- Δ = **−0.68250**
- fill 2.25 × 0.2900 = 0.652500, commission 0.030000 → **0.682500**. Exact.

Nothing unsettled, no position, no open orders.

### Labels

```
submitted = CONFIRMED            (venue order id CQ5NPBADMYC3)
filled    = CONFIRMED 2.25       (cumQuantity 2.25, leavesQuantity 0)
settled   = CONFIRMED            (settlementPx 0.0000, MARKET_STATE_EXPIRED)
realized  = CONFIRMED -$0.6825   (settlement complete AND attributable
                                  cashflow: 63.04895 -> 62.36645, and
                                  0.6525 + 0.0300 reproduces it exactly)
```

This is the first realized P&L this programme has ever recorded. It is a
**loss of $0.6825**, inside the $5 envelope, on a market that settled against
the side L5 was on.

## CANCEL ROOT CAUSE

The order became terminal at **13:01:46.847706680Z**. Close-out attempted
`POST /v1/order/CQ5NPBADMYC3/cancel` at **13:13:30Z** — **11 minutes 44
seconds after it had completely filled**. The venue rejected it, correctly.

**The exact HTTP status and response body were NOT recovered.** They are in
`ORDER.detail` in the journal on the probe's volume, which needs a running
container; four attempts failed (the observe-only container had already
exited by the time the reads were attempted, and the Railway agent twice
substituted a hallucinated deployment id). I am not inventing a status code.

The cause does not depend on it: cancelling an order the venue considers
terminal fails whatever the code. Accordingly **the cancel path's error
handling is unchanged** — fail-closed on a genuinely unresolved cancel is
preserved exactly. What changed is that the doomed cancel is no longer
attempted, because the order's state is now known before close-out acts.

## RECONCILIATION ROOT CAUSE

```json
"/v1/portfolio/positions" →
 {"positions": {}, "nextCursor": "", "eof": true, "availablePositions": []}
```

`positions` is an **object keyed by market slug**, not an array.
`PmusTransport.positions()` did `r.get("positions") or []`. Empty, `{}` is
falsy and the `or []` yielded a list, so it worked for every run that held
nothing. Probe 04 was the first close-out ever to run while the account
actually held a position: the mapping came back populated,
`reconcile_from_venue` did `for p in positions` over a dict and got **string
keys**, and `p["market"]` raised `TypeError: string indices must be
integers`.

The populated form is **not inferred**: `pmus_settlement_auditor._rows_of`
has read this resource against real fills and settlements on this venue and
already treats a mapping as keyed by slug. The fix matches that behaviour
rather than inventing a second reading of the same resource.

## Why the local state was wrong, and what is authoritative

Our books were not corrupt — they were **stale**, and correctly marked as
uncertain. The design's one real assumption was that a submit ACK is the last
word on fill state. It is a receipt. Nothing ever asked the venue again, so:

- 29 later L5 actions were refused `MAX_LIVE_ORDERS` against an order that
  had not existed since 13:01:46;
- $0.72 stayed reserved against shares we already owned;
- close-out tried to cancel a filled order;
- and the reconciliation that would have caught all of it crashed first.

The venue is the authority. This record is the correction; the journal and
the inventory file stay as they were written.
