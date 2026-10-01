# `/v1/ws/private`: what is actually established, and the corrected verdict

## I was wrong, and the correction matters

My previous report said "there is no private-order event stream." What I had
actually established was only that **the calibration harness is not subscribed to
one**. `src/pmus_ws_lifecycle_observer.py` already implements `/v1/ws/private`.
Overstating a harness gap as a venue limitation is the kind of error that would
have sent us building around REST polling for no reason.

## What the existing code establishes, from its own recorded evidence

A capability probe on **2026-09-29** (`scripts/pmus_ws_private_probe.py`,
recorded in the observer's docstring and constants) established:

| fact | status |
|---|---|
| `wss://api.polymarket.us/v1/ws/private` is reachable and authenticates | **PROVEN** |
| it accepts `SUBSCRIPTION_TYPE_ORDER_SNAPSHOT` | **PROVEN** |
| it accepts `SUBSCRIPTION_TYPE_ORDER` | **PROVEN** |
| it accepts `SUBSCRIPTION_TYPE_POSITION` | **PROVEN** |
| the private socket has its OWN subscription enum, not the market one | **PROVEN** |
| the order snapshot returned `{"orders": [], "eof": true}` | **PROVEN** -- empty, because the account held no open orders |
| **any incremental order lifecycle frame** | **NEVER OBSERVED** |

## So the precondition still fails, but for a sharper reason

Not "the venue has no stream." The correct statement is:

> The channel exists and subscribes. **Its order-event payload schema has never
> been observed even once**, so whether it carries venue timestamps, a venue
> trade/fill id, fill quantity or a maker/taker role is UNKNOWN.

`LifecycleObserver.on_ws` already reaches for `cumQuantity`, `state`, `avgPx`
and `commission`, but those names are taken from the REST order shape, not from
an observed private frame. The module says so itself and deliberately refuses to
act on them: an order vanishing from a snapshot is recorded as
`VANISHED_FROM_SNAPSHOT` and is explicitly **not** a fill. That is the right
posture and I am not weakening it.

The observer records a local receipt time (`ws_t`) and correlates against REST
with a measured `ws_to_rest_latency_s`. Local receipt time alone is enough to
order an event against a ~0.101s market-data sequence ONLY if the socket is
genuinely low-latency; a venue timestamp or trade id would make the join exact
rather than proximate. Which of those we get is precisely what has never been seen.

## The cheapest way to observe the schema

One **post-only** order placed FAR from the market (e.g. a bid at 0.01 with the
market near 0.40) cannot fill, and cancelling it costs nothing. That single
lifecycle yields real `accepted` and `cancelled` frames and would reveal the
field names, timestamp precision and id availability for those states. It does
not reveal the FILL frame, which is the one that matters most.

This is still an order submission, so it is **not** covered by "no money" and I
am not doing it without explicit authorization. I am flagging it as the smallest
available step rather than acting on it.

## A read-only lead on the still-open question

`scripts/pmus_maker_persistence_probe.py` already exists and is read-only
(subscribe/unsubscribe, no private stream, no money path). It uses
`SUBSCRIPTION_TYPE_TRADE` on `/v1/ws/markets` -- `maker.side`, `taker.side`,
microsecond `tradeTime` -- to ask whether the resting side of the BTC-15m book
behaves like one persistent level, a replenished level, a repriced level, or
several simultaneous levels.

That is evidence about the resting/persistence question **at zero cost and zero
exposure**. Its honest limit, stated in its own docstring, is that it observes
the whole market and cannot attribute a trade to a specific participant, so it is
evidence about BTC-15m maker behaviour in general, not proof about L5 or about
our own order. It cannot settle crossed-resting semantics for OUR order, but it
can constrain the hypothesis space before we spend anything.

## Done in this pass

**Acceptance timestamps fixed** (`src/pmus_q1_canary.py`), independent of the
private-WS question: the `:accepted` record now persists `accepted_utc`,
`accepted_monotonic`, any venue-supplied timestamp verbatim
(`createdAt`/`created_at`/`transactTime`/`updatedAt`/`timestamp`) and
`venue_state`. Previously it stored only the stage and order id, so the moment our
quote joined the queue -- which sets causal `placed_t` -- was absent from disk.

## Status

Campaign NOT armed. Zero orders submitted. No strategy, cap, size or
campaign-limit change. Crossed-resting semantics unresolved and unassumed.
