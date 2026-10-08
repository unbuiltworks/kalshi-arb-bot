# Which PMUS feed has actually produced a fill — and the retraction

> **E1 IS DISARMED.** Cohort `E1-COHORT-20261007T1849Z`: 4/12 windows,
> 0 submitted, 0 filled, $0.00 exposure, **$0.00 realized**. The $5.00
> cohort ceiling and the invalidated cohort are untouched.

## 0. I have to retract my last verdict first

I reported **GENUINE DISAGREEMENT** across three PMUS market-data routes.
**That verdict is withdrawn.** It was a semantics error, and the
repository had already measured the thing I got wrong.

| reading | host | what it is |
|---|---|---|
| AuthBook | `wss://api.polymarket.us/v1/ws/markets` | **authenticated, live** |
| my REST `/book` | `https://gateway.polymarket.us` | **public gateway** |
| my REST `/bbo` | `https://gateway.polymarket.us` | **public gateway** |

`src/pmus_ws_evidence_recorder.py` already records that the gateway book
is a **~30-second cache**, and not as an opinion — as a measurement.
Inferring trade direction against it held **46.2% forward and 35.3%
inverted over 184 resolvable rows, with 18.5% of prints strictly inside
the quoted spread** where neither reading can apply. Its own conclusion:

> *"That is not a close call between two hypotheses, it is the wrong
> book."*

So I compared **one live authenticated feed against two reads of a stale
public cache** and called the gap a disagreement. A 12-tick move on a
BTC15m book over up to 30 seconds is ordinary.

My stability gates — `book_updates_during_rest == 0`, WS top unchanged,
skew 0.0392 s — were sound as far as they went, and they are the reason
I trusted the result. They prove the **local** websocket state did not
move during the 39 ms REST call. **They cannot detect staleness on the
gateway's side**, and nothing in that run could.

## 1. The source the proven path used: AuthBook

**XR2W Attempt 6** (`analysis/l5/live_exec5/XR2W_ATTEMPT_6.md`) —
described in its own record as *"the first evidenced fill from our own
order path"*:

```
AuthBook → decision → preflight → canonical preview → preview accepted →
TTE → durable latch → TTE → POST /v1/orders → venue response
```

> **AuthBook:** two-sided, age **0.084 s** against the **2.0 s** bound,
> first try. Source `l5_pmus_auth_book.AuthBook`.

Not REST `/book`. Not REST `/bbo`. **The authenticated websocket.**

The other lineage, `src/pmus_q1_executor.py`, prices from
`fresh_bbo()` → `GET /v1/markets/{slug}/bbo` with `base=GATEWAY` — the
cached public host. It has placed orders, but the **evidenced fill** is
A6's, and A6 used AuthBook.

## 2. The fill reconciles — executable, not displayed

| | |
|---|---|
| AuthBook YES bid / ask | **0.3200 / 0.3300** |
| semantic side | DOWN (frozen by digest before the window, not forecast) |
| own-side touch | **0.6700** = 1 − 0.3300 |
| offset from touch | **0 ticks** — passive, never crossing |
| wire `outcomeSide` | `OUTCOME_SIDE_NO` |
| wire `price.value` | **0.3300** = 1 − 0.6700 |
| wire `quantity` | 0.01, JSON number |
| preview | **ACCEPTED**, echo ok, zero mismatches |
| `POST /v1/orders` | `{"id": "CYKE4XSQ0YGW"}` |
| ack | `ORDER_STATE_NEW` 02:00:01Z, `leavesQuantity` 0.01 |
| **fill** | **`ORDER_STATE_FILLED` 02:00:15.69Z, `cumQuantity` 0.01** |
| submission → fill | ≈ **15.2 s** |

This is the distinction the owner asked for. AuthBook's price did not
merely *display*: it produced an order the venue **accepted**, **rested**
for 15 seconds, and **filled at the derived price**. The complement chain
— own-side price ↔ wire `price.value` = 1 − x — is confirmed end to end
by a venue that accepted and filled the body.

## 3. So is E1's feed unsuitable?

**No.** E1 already decides on AuthBook — that is what the Phase-2
switchover did. Its decision path reads the authenticated book only,
refuses REST as a timing authority, derives the complement as
1 − YES ask, and serializes NO through the proven
`l5_order_body.venue_price`. That is the A6 path.

**The comparison was mixing semantics. The feed was not the problem.**

## 4. The one real gap, and the smallest change that closes it

E1 carried an AuthBook staleness bound of **5.0 s**. A6 used **2.0 s**.
E1's was **looser than the bound that has actually filled**, on no
evidence for 5.0.

```
-MAX_BOOK_AGE_S = 5.0
+MAX_BOOK_AGE_S = 2.0
```

That is the entire executable change. No strategy, no risk parameter, no
AuthBook modification, no order-path change. Economics untouched: 0.13
shares, $0.60 first leg, $0.96 pair, 20 attempts/window, 12 windows /
240 placements, $1.56/window, $5.00 cohort ceiling, 13 tranches,
maker-only, no chase, no requote, no taker rescue.

Also recorded, documentation only: `pmus_identity.bbo()` and `book()` now
say in their own docstrings that they read the **~30-second cached public
gateway** and may never price an order. That is what I should have read
before running the comparison.

Fingerprint change, minted honestly:

```
superseded  9de41c1762342557668cdcdf6b57538122d5fb2e50d7546d57c22e0b37c759d5
current     b32e8c7bbc4d496bbf05c633f151892ad74a8bf5b34a5750fc70715520881cf1
invariants  159 -> 163
```

New invariants: `max_book_age_s = 2.0`,
`execution_feed_proven_by_a_fill = src.l5_pmus_auth_book.AuthBook`,
`rest_gateway_book_is_a_cache = True`,
`rest_gateway_book_may_price_an_order = False`.

## 5. GO / NO-GO

**GO on the feed question.** AuthBook is the proven PMUS-native execution
feed, evidenced by an accepted preview, a venue ack, a 15-second rest and
a real fill at the derived price. E1 is already on it. The three-route
"disagreement" was my measurement error, not a venue or parser fault.

**NO-GO on arming today**, and not for feed reasons:

1. **E1 has never produced a fill of its own.** A6 was XR2W at **0.01
   share**; E1 is frozen at **0.13** — 13× the size, through a different
   runner, with a complement leg A6 never placed. The feed is proven; the
   *runner* is not.
2. **E1's 4 consumed windows produced zero eligible states**, so nothing
   has exercised its submission path end to end against the venue.
3. The last two findings were both caught only in live runs, not by the
   suite — the missing `websockets` dependency and this bound. That rate
   argues for one bounded shadow pass before money moves.

**What I recommend, and it is your call:** one bounded E1 run with the
order path still refused at the final boundary — far enough to produce a
real preview and a real eligibility decision on AuthBook prices, with no
POST. That converts "the feed is proven" into "E1's own path is proven"
without risking the ceiling.

## 6. Accounting

**historical replay → shadow → live probe → submitted → filled →
settled → realized**

A6 is **FILLED / settlement unresolved / NOT REALIZED** and belongs to
XR2W, not to E1. Nothing in E1 has been submitted. **$0.00 realized.**
