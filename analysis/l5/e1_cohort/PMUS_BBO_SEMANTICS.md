# PMUS BBO semantics — settled from venue data

> **E1 IS DISARMED.** Cohort `E1-COHORT-20261007T1849Z`: 4/12 windows,
> 0 submitted, 0 filled, $0.00 exposure, $0.00 settled, **$0.00
> realized**. Untouched.

## 0. The question

E1's window-3 attempt recorded
`price_source: PMUS_BEST_BID, price_sent: 0.1300` while a
`"value": "0.4900"` also appeared in the same BBO/book payload. The first
leg's price **is** that parse, so no E1 decision could honestly depend on
it until the venue settled which field is the executable YES best bid.

Nothing below is inferred from a field name.

## 1. The capture

Read-only, credentials used for signing only, no POST of any kind, the
E1 latch's consume functions replaced with raises before any work.

| | |
|---|---|
| slug | `cpc-btc-updown-15m-2026-10-07-2215z` |
| captured | 2026-10-07T22:29:48Z, +842.5s into the window |
| deployment | `dd7583fb-6f5e-41fd-a51b-0b566c9c05fa` |
| routes | `GET /v1/markets/{slug}/bbo`, `GET /v1/markets/{slug}/book`, authenticated websocket (AuthBook) |
| fixture | `fixtures/pmus_bbo_2026-10-07T2229Z.json` — verbatim, nothing altered, nothing removed |

## 2. What the venue actually serves

### 2.1 The authenticated websocket carries NO best-bid field at all

Every book frame's keys were **exactly**:

```
["bids", "marketSlug", "offers", "state", "stats", "transactTime"]
bestBid: null      bestAsk: null
```

`bestBid` exists **only** on the REST `/bbo` route. So over the
websocket — which is E1's decision feed and its only timing authority —
the recorder's authoritative-field branch is **never taken**, and the
first-leg price **always** comes from the ladder:
`bids[0].px.value`.

This reframed the whole question. There is no "best bid field" for E1 to
read from its feed, so the thing that had to be verified was the
**ladder**, not a choice between two named fields.

### 2.2 The ladder is ordered best-first

The captured `/book` body carried **29 bid levels, strictly descending**:

```
0.9900  0.9800  0.9700  0.9600  0.9500  0.9400  0.9300  0.9200  0.9000
0.8900  0.8500  0.8300  0.7800  0.7500  0.6500  0.6000  0.5500  0.5000
0.4000  0.2500  0.0900  0.0800  0.0700  0.0600  0.0500  0.0400  0.0300
0.0200  0.0100
offers: []
```

**Three independent readings, one number:**

| reading | value |
|---|---|
| REST `/bbo` → `marketData.bestBid.value` | `0.9900` |
| REST `/book` → `marketData.bids[0].px.value` | `0.9900` |
| WS frame → `bid0.px.value` | `0.9900` |

So `levels[0]` **is** the best bid, confirmed rather than assumed. Had
the ladder been ascending, `levels[0]` would have been `0.0100` — the
worst bid — and E1 would have quoted it.

### 2.3 The window was one-sided, and E1 correctly refused it

`bestAsk: null`, `askDepth: 0`, `askShares: "0"`, `offers: []`.

AuthBook was **connected** with **103 book_updates** and still returned
**no snapshot**, because it only answers when connected *and* two-sided.
`health()` said `NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG`. That is the
fail-closed path, observed working on real data.

Near expiry a BTC15m window has resolved in all but name (`bestBid`
0.9900, `shortQuote` 0.01), so there is no two-sided book to compare —
which is why the probe now waits for a window **opening** instead.

## 3. The two transformations

The venue quotes and accepts **only the long (YES) side's price**.
`l5_order_body.venue_price` encodes this, learned the hard way: on
2026-09-05/06, **24 of 24** NO-side post-only orders priced in NO terms
were rejected, because the venue read them as NO at `1 − X`, which
crosses.

```
YES buy maker price = YES best bid
                      wire price.value = the same number

NO  buy maker price = 1 − YES best ask        (the NO best bid)
                      wire price.value = 1 − that = YES best ask
```

Worked example, `yes_bid = 0.40`, `yes_ask = 0.42`:

| | outcome terms | wire `price.value` | `outcomeSide` |
|---|---|---|---|
| YES maker buy | `0.40` | `0.40` | `OUTCOME_SIDE_YES` |
| NO maker buy | `0.58` | `0.42` | `OUTCOME_SIDE_NO` |

Both are asserted against the proven serializer, not restated.

### Complement arithmetic, against venue data

PMUS quotes **one** book per market; the NO side is its arithmetic
complement, which is the venue's own convention and not a shortcut:

```
NO bid = 1 − YES ask        NO ask = 1 − YES bid
```

Confirmed on the real body's own long/short pair:
`lastPriceSample.longPx` 0.9900 + `shortPx` 0.01 = 1.0000, and
`shortQuote` 0.01 = 1 − 0.9900.

With `bestAsk: null` no complement **bid** can be formed — and
`complement_of(bid, None)["bid"] is None`, so the absence propagates
instead of becoming a number.

## 4. The defect found, and fixed

`_walk_market_events` propagated the slug **downward**, so any nested
dict carrying a `bids`/`offers`/`bestBid` key became a **second book
event** for the enclosing market — and `apply_events` folds in order, so
the **last one won**. Reproduced against the venue's real frame shape:

```
bids[0]        = 0.9900     <- the authoritative ladder top
stats.bids[0]  = 0.4900
folded result  : bid = 0.4900
```

`stats` is a **real** key in PMUS book frames, carrying `currentPx`,
`highPx`, `lastPriceSample` (including a NO-denominated `shortPx`),
`lastTradePx`, `lastTradeQty`.

**The fix is the narrowest that closes it:** a nested object is not a
second book for the same market. `_walk_market_events` now carries the
set of slugs an ancestor already described and refuses a descendant book
event for those slugs. **Trades are unaffected** — still collected at any
depth, because a trade legitimately appears inside a market object.

Every real shape was checked individually and still parses:
`{"marketData": {slug, bids, offers}}`,
`{slug, "marketData": {bestBid, bestAsk}}`,
`{slug, bids, offers, trades: [...]}`, and a descending ladder.

**Five tests fail without the fix and pass with it**, checked by stashing
the change rather than by reasoning about it.

## 5. So what was 0.4900?

Honestly: **not a competing best bid.** There is no second best-bid field
in a PMUS payload to disagree with. The real ladder carries `0.5000`,
`0.4000` and `0.2500` among its 29 levels, and `stats` carries
`currentPx` / `lastTradePx` / a NO-denominated `shortPx`. `0.4900` has
the shape of a **mid-ladder or last-trade value**.

The reading I can defend is that `0.1300` was the ladder top at that
instant and `0.4900` was a different field in the same body. What was
missing was a tape that recorded **which path** each number came from —
which is why the probe now enumerates every numeric value with its JSON
path, and why the recorded rejection event stores the serialized body
verbatim.

I am **not** claiming the window-3 price was definitely correct. I am
claiming the parse rule is now established from venue data, the one
mechanism that could have produced a wrong number is fixed and pinned by
test, and the fail-closed paths are confirmed on real data.

## 6. The second defect this work uncovered

**`websockets` was missing from the E1 image.** `l5_pmus_auth_book`
imports it *inside* `AuthBook._run`, so the Dockerfile's build-time
import assertion passed with it absent. Deployment `cc131f76`,
2026-10-07T22:19:53Z:

```
Exception in thread auth-book:
  File "/app/src/l5_pmus_auth_book.py", line 346, in _run
    import websockets
ModuleNotFoundError: No module named 'websockets'
```

**E1's decision feed could never have connected.** It fails closed — no
connected book, so `health()` refuses and no window opens — but it also
could never have traded, and it would have spent the cohort's remaining
windows as feed-not-ready terminals.

**A disarmed validation structurally could not catch it**, because a
disarmed boot never starts the feed. The earlier "zero DNS lookups"
result was a clean reading on a build that could not have streamed a
book. That claim bounded what E1 does *not* do; it said nothing about
the feed working.

Fixed with exact pins (`cryptography==49.0.0 websockets==17.2`), a build
step that imports both **by name**, and
`test_every_lazily_imported_third_party_module_is_installed`, which walks
the whole closure's function bodies and requires the Dockerfile to
install every non-stdlib module it finds. It finds exactly those two —
the two that have shipped missing — and nothing else.

## 7. Still outstanding

A **two-sided** reading, so the agreement can be stated with both a bid
and an ask from the same instant. The capture above is one-sided by
nature of its timing (+842s, near expiry).

Two venue facts make this harder than it sounds, both measured:

* **near expiry the book collapses** — `offers: []`, `bestAsk: null` —
  so a late window has no ask to compare;
* **at window open `/v1/markets/{slug}/bbo` returns 404** while
  `/v1/events/slug/{event}` already resolves the market. The market
  resource is not served the instant the window opens. The probe now
  retries and records a refusal instead of crashing, and never invents a
  payload.

Until that reading exists, the **ask** side of the comparison rests on
the ladder-ordering proof and the complement arithmetic above, not on a
direct two-route match. **The bid side is settled** by three independent
readings.

The instrument is in the image and ready; it needs one run that lands on
a genuinely two-sided window.

## 8. Accounting

**historical replay → shadow → live probe → submitted → filled →
settled → realized**

Nothing above is realized profit or loss. Nothing has been submitted.
