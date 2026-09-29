# PMUS WebSocket capability map — measured, not assumed

Established by `scripts/pmus_ws_private_probe.py` against the live venue on
2026-09-29. Structurally read-only: subscribe/unsubscribe frames only. The
international Polymarket CLOB WS schema was NOT used as a reference; every
line below is the venue's own answer.

## VERDICT: PMUS **does** expose private order lifecycle over WebSocket.

## Two endpoints, two DIFFERENT enums

### `wss://api.polymarket.us/v1/ws/markets` — public market data

| int | name | payload |
|---|---|---|
| 1 | `SUBSCRIPTION_TYPE_MARKET_DATA` | **full depth, 24–29 levels/side**, `bids`/`offers` px+qty, `state`, `stats` |
| 2 | `SUBSCRIPTION_TYPE_MARKET_DATA_LITE` | `bestBid`/`bestAsk`, `askDepth`/`bidDepth`, `bidShares`/`askShares`, `longQuote`/`shortQuote` |
| 3 | `SUBSCRIPTION_TYPE_TRADE` | tape: px, qty, `tradeTime` (µs), **`maker.side`**, **`taker.side`+`intent`+`outcomeSide`**, trade `id` |
| 4 | event volume | `"event volume subscriptions use event_slugs, not market_slugs"` |
| 0, 5–12 | — | `invalid_message` |

### `wss://api.polymarket.us/v1/ws/private` — authenticated, account-scoped

| int | name | evidence |
|---|---|---|
| 1 | `SUBSCRIPTION_TYPE_ORDER` (inferred) | accepted, no immediate payload |
| **2** | **`SUBSCRIPTION_TYPE_ORDER_SNAPSHOT`** | returned `{"orders": [], "eof": true}` |
| 3 | `SUBSCRIPTION_TYPE_POSITION` (inferred) | accepted, no immediate payload |
| 4 | unidentified | accepted, no immediate payload |
| 5 | `SUBSCRIPTION_TYPE_RFQ` | `"retail Combo and RFQ access is not enabled"` |
| 0, 6–12 | — | `invalid_message` |

String names are honoured on this socket. Probed one fresh connection each:

| name | recognised |
|---|---|
| `SUBSCRIPTION_TYPE_ORDER` | **yes** |
| `SUBSCRIPTION_TYPE_ORDER_SNAPSHOT` | **yes** — returned the snapshot |
| `SUBSCRIPTION_TYPE_POSITION` | **yes** |
| `SUBSCRIPTION_TYPE_RFQ` | yes (permission denied) |
| ORDER_UPDATE, ORDER_EVENT, EXECUTION, TRADE, POSITION_SNAPSHOT, BALANCE, ACCOUNT, PORTFOLIO, SETTLEMENT, USER_TRADE | no — `invalid_message` |

All other paths 404: `/v1/ws/orders`, `/user`, `/users`, `/fills`,
`/trades`, `/account`, `/portfolio`, `/v1/ws`.

## Against the six requirements

| # | requirement | status |
|---|---|---|
| 1 | market BBO/book | **AVAILABLE** — full depth (type 1) or lite BBO (type 2) |
| 2 | order acks / state changes | **AVAILABLE** — private `ORDER` + `ORDER_SNAPSHOT` |
| 3 | partial fills | **channel available, payload UNOBSERVED** |
| 4 | fills | **channel available, payload UNOBSERVED** |
| 5 | cancellations | **channel available, payload UNOBSERVED** |
| 6 | expirations / rejections | **channel available, payload UNOBSERVED** |

Plus, not asked for but present: a private **POSITION** stream, which is a
second independent source for canonical inventory.

## THE EXACT REMAINING LIMITATION

The order snapshot came back **empty with `eof: true`**, which is correct —
the account holds no open orders after R5 closed out. So the subscription is
proven and **not one incremental event has been observed**. We do not yet
know:

* whether `SUBSCRIPTION_TYPE_ORDER` pushes updates after the snapshot;
* the payload shape of an ack, a partial fill, a fill, a cancel, a reject
  or an expiry;
* whether those carry `cumQuantity`/`avgPx`/`commission` in the same shape
  `pmus_order_lifecycle` already classifies from REST;
* event ordering and delivery guarantees across a reconnect.

**No amount of further read-only probing can close this.** The stream emits
account activity, and there is none without a live order. Characterising the
event schema requires observing the private socket **during a window in
which we hold orders**.

## Recommended architecture and sequencing

Target, which the evidence now shows is buildable:

```
market WS  type 1  -> latest book (FULL DEPTH)
private WS ORDER_SNAPSHOT -> startup reconciliation
private WS ORDER          -> lifecycle registry -> canonical inventory
private WS POSITION       -> independent inventory cross-check
REST -> periodic audit, disconnect recovery, UNKNOWN/ambiguity fallback
```

Sequencing, and the part that should not be skipped:

1. **Observe before trusting.** Run the private socket in SHADOW alongside
   the existing REST polling during the next armed window. Log both. Do not
   let WS book a single fill.
2. **Measure** WS↔REST agreement per order: state, cumQuantity, avgPx,
   commission, and event→REST latency.
3. **Only then** consider removing REST from the critical path, and only for
   event classes where agreement was exact.

Moving authoritative lifecycle onto an unobserved stream would put the
"never infer a fill from disappearance" invariant on a schema nobody here
has seen. The snapshot proves the door exists; it does not prove what comes
through it.

## Separately actionable, independent of the above

**Type 1 carries 24–29 real depth levels.** `l5_live_book_feed.touch_ladder`
synthesises a ONE-level ladder because the feed only tracked top-of-book,
and the frozen theta walk — designed to walk real depth — has never been
given any. The full ladder is already arriving on a socket we already run.
That is a strictly better input to the certified completion path with no
strategy change.

**Type 3 gives `maker.side`/`taker.side` at microsecond resolution.** That is
a far better instrument for the L5 multi-level-ladder question than the
1-second public fills, which could not separate a maker ladder from a taker
sweep.
