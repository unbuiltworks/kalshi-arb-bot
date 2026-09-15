# Does refreshing Gemini change the book? Yes — and here is what that means

**Trigger.** The owner observed that manually refreshing Gemini changes the
displayed order book. That is a claim about our *data path*, not about the
venue, and it had to be settled before any Gemini capacity conclusion could
stand.

**Status: INTERIM.** Numbers below are from the first minutes of a probe that
is still running across further windows. The two mechanism findings (H3, H4)
are settled; the capacity distribution is not, and no capacity conclusion is
drawn here.

---

## First, a correction to my own record

My previous commit attributed the original `depth 1.0 @ 0.66` reading to *"a
collector that literally requests `limit_bids=1`"*. **That attribution was
wrong.** Tracing the path:

* `gemini_mapping.resolve()` never returned a size at all — only `top_bid` /
  `top_ask` prices;
* the single-level evaluator took depth from `NormalizedBook.yes_ask_qty`,
  fed from a **full, untruncated** REST book.

So that reading was a **genuine 1-share touch**, not a truncation artifact.
The truncation defect is real and is documented below, but it lives in two
*other* services and was not the cause of that observation. Blaming the wrong
mechanism would have left the real one in place.

---

## Method

Both data paths, against the same contract, at the same instants. No artificial
delays, no UI automation — every REST call is a fresh unauthenticated GET
issued as fast as the loop reaches it.

| path | what it is |
|---|---|
| REST | `GET /v1/book/{symbol}`, timestamped on send **and** receive, with the venue's own per-level timestamps |
| WS | `wss://api.gemini.com/v1/marketdata/{symbol}`, maintained continuously from `initial` + deltas |
| limit=1 | `?limit_bids=1&limit_asks=1` — the path two existing collectors use |

Sampling is 2 Hz off the maintained book (costs no network), so the
distributions below are **time-weighted**, not weighted by how fast we polled.

---

## H3 — stale or cached snapshot: **RULED OUT**

At the HTTP layer the venue serves **no cache directive at all**:

```
Date, Content-Type, Content-Length, Connection, server: istio-envoy,
x-envoy-upstream-service-time
```

No `Cache-Control`, no `Age`, no `ETag`, no CDN header. Nothing between us and
Gemini is caching, and the agent proxy adds no header of its own.

At the data layer, measured:

| statistic | p05 | p50 | p95 | max |
|---|---|---|---|---|
| REST `snapshot_age_s` (response time − newest venue level stamp) | 0.001 | **0.52** | 0.93 | 1.59 |
| REST round-trip | 0.31 | 0.62 | 2.13 | 3.18 |
| WS maintained-book age | 0.001 | **0.042** | 17.88 | 26.91 |

REST agrees with the maintained book on the **best ask 99.15%** of the time and
on the **best ask size 100%** of the time. A cache would not do that.

*(The WS p95 of 17.9 s is a quiet book, not a stale one: no message means no
change. The minimum REST age is −0.031 s — the venue's clock is tens of
milliseconds ahead of ours, which is worth knowing and is not a book from the
future.)*

**REST is not stale. It is a sample of something that moves faster than any
polling rate.** The websocket shows the mechanism directly:

```
{"delta":"-1.0","price":"0.39","reason":"cancel","remaining":"0.0","side":"ask"}
{"delta":"1.0", "price":"0.39","reason":"place", "remaining":"1.0","side":"ask"}
```

placed and cancelled **25 milliseconds apart**, inside a stream running ~35
messages a second in which `place` and `cancel` counts track each other almost
exactly and `trade` stays at zero. That is a market maker's quote-refresh loop.
A REST snapshot landing between those two messages sees a 1-share touch; one
landing after sees nothing there. Neither reading is wrong, and neither is the
book.

## H4 — incomplete retrieval: **CONFIRMED, in two existing collectors**

`default`, `limit=0` and `limit=100` return **identical level counts** — the
default REST response is *not* truncated, and Gemini's prediction-market books
genuinely carry only 3–6 price levels per side.

`limit_bids=1&limit_asks=1` returns exactly one level per side, hiding:

| hidden by limit=1 | p05 | p50 | p75 | p95 | max |
|---|---|---|---|---|---|
| ask levels | 0 | **3** | 3 | 4 | 4 |
| ask size (shares) | 0 | **182** | 542 | 720 | 1111 |

Full total ask p50 **659** vs limit=1 p50 **157**.

Two long-running services use that query:

* `src/gemini_l5_shadow_service.py:189`
* `src/gemini_l5_shadow_v2.py:1077`

**Their recorded tapes systematically understate Gemini depth by a median of
182 shares behind the touch.** They are deliberately not edited here: changing
their fetch mid-flight would change the meaning of a historical series that
already exists. This is recorded so that series is read correctly, and fixing
them is a separate deliberate act.

## H1 / H2 / H5 — how much is real, and for how long

Interim, first ~5 minutes, **two windows only**:

| | BTC15m | BTC5m |
|---|---|---|
| whole ask side ≤ 1 share | 0.0% | **8.7%** |
| no ask side at all | 0.0% | **7.3%** |
| best ask size p50 | 157 | 10 |
| total ask size p50 | 659 | 510 |
| time-weighted fillable q=1…10 | 100% | 92.7% |
| time-weighted fillable q=20 | 100% | 76.2% |
| touch changes / min | **1.5** | **11.4** |
| touch lifetime p50 | **26.6 s** | **8.8 s** |
| a touch-only read would understate q=20 | 0% | **49.2%** |

Two things follow, and they pull in opposite directions:

1. **BTC15m, in this window, was deep and stable** — 100 % fillable at every
   candidate size, a touch that survives a median 26.6 s.
2. **BTC5m is genuinely thin part of the time** — 8.7 % of the time the whole
   ask side holds one share or less, and 7.3 % of the time there is no ask side
   at all. That is H1 being *true some of the time*, on one cohort.

And on BTC5m a touch-only reading would have said "cannot supply 20" **half the
time that the full ladder could** — H2, quantified.

---

## What was fixed

`src/l5_shadow/venues.gemini_book()` now reads the **maintained websocket book
first**, falling back to a fresh REST snapshot only when the socket has not yet
delivered `initial` or has hit a sequence gap. Every row records which path
answered. Measured effect: book age p50 **0.52 s → 0.042 s**.

The socket also supplies `trade` events directly, so Gemini maker evidence no
longer depends on polling a capped `/v1/trades` list — and `prints_since()`
returns **None**, not an empty list, for any interval that began before the
socket's current continuous view, because *"we saw nothing"* and *"we were not
watching"* are different facts.

A sequence gap invalidates the book rather than being ignored: a book missing
updates is never served.

## What is NOT concluded

**No Gemini capacity conclusion is drawn from this document.** Two windows of
one cohort each is not a distribution, the two cohorts already disagree
sharply, and the earlier snapshot burst at 13:31–13:33 Z showed the BTC15m
touch flickering between 1 and 702 within a single second — behaviour this
later window did not reproduce. The probe continues across further windows;
the capacity statement waits for it.

---

# Part 2 — flow, and the metric I got wrong first

## Verdict per candidate explanation

Emitted by `report.py --classify` from the measurements, not written by hand.
Each carries the numbers and the threshold it was judged against.

| class | verdict | basis |
|---|---|---|
| `CACHE_REFRESH_BEHAVIOR` | **RULED_OUT** | no cache directive on any response |
| `STALE_SNAPSHOT` | **RULED_OUT** | REST age p50 0.52 s; agrees with the maintained book on best price 99.15 % and on size at the touch 100 % |
| `INCOMPLETE_BOOK` | **PRESENT_IN_LEGACY_COLLECTORS_ONLY** | default == limit=0 == limit=100; `limit=1` hides a median 182 shares, and two services use it |
| `BEST_LEVEL_ONLY_PARSING` | **WAS_PRESENT_NOW_FIXED** | the ladder evaluator replaced the touch-depth read; a touch-only read would have understated capacity in up to 49.2 % of sampled instants |
| `GENUINE_BOOK_MOVEMENT` | **PRESENT_AND_DOMINATED_BY_QUOTE_CHURN** | cancel/place volume ratio 0.992–0.994 |
| `UNKNOWN` | **NOT_NEEDED** | every class has evidence |

## The metric I got wrong, and the correction

My first flow metric summed `place` deltas and called it *unique executable
liquidity*. On a live BTC5m window it reported **37,805 contracts offered at
≤ 0.50 in 70 seconds** — against **50 actually traded** and a cancel/place ratio
of **0.994**.

That is one quote refreshed 159 times. **Gemini's v1 market-data feed carries no
order id**, so a cancel followed by a place at the same price and size is
indistinguishable from an order that never moved. Summing placements is not a
capacity measure and cannot be made into one on this feed.

The corrected decomposition reports what each number can actually support:

| field | meaning |
|---|---|
| `standing_eligible_size` | size actually **resting** at an eligible price, replayed from events |
| `net_new_eligible` | placed − cancelled: what accumulated **on net** |
| `traded_at_or_under_ceiling` | size that actually **changed hands** |
| `placed_gross_NOT_A_CAPACITY_MEASURE` | retained only to make the churn visible |
| `churn_ratio` | cancelled / placed; near 1.0 means refresh, not supply |
| `accumulation_trade_limited` | resting max + traded — the defensible bound |

## Measured flow, ~152 s per window, at the L5 ceilings actually observed

Ceilings come from the executable shadow's own frozen intents for the same
cohort, so this answers *capacity through the intent's maximum acceptable
price* rather than through an invented grid.

**BTC15m** — and this is the whole point of the worked example:

| ceiling | standing p50 | net new | traded | realistic capture | q=20 reachable |
|---|---|---|---|---|---|
| 0.11 | **1** | 0 | 0 | **1** | **no** (q=1 only) |
| 0.14 | 174 | 174 | 0 | 846 | yes |
| 0.17 | 673 | 674 | 0 | 859 | yes |
| 0.20 | 675 | 889.5 | 215.5 | 1087.5 | yes |
| 0.50 | 676 | 889.5 | 215.5 | 1122.5 | yes |

**BTC5m**:

| ceiling | standing p50 | net new | traded | realistic capture |
|---|---|---|---|---|
| 0.10 | 10 | 10 | 10 | 60 |
| 0.50 | 637 | 742 | 60 | 1109 |
| 0.62 | 637 | 742 | 60 | 1109 |

**Capacity is a function of the ceiling, not a property of the venue.** At a
0.11 ceiling the book really is one contract; three cents higher it is 846.
Quoting "Gemini depth" without naming a ceiling is meaningless, and the
original `depth 1.0 @ 0.66` reading was a true statement about one price, read
as a statement about a venue.

**Turnover is low.** 215.5 contracts traded on BTC15m and 60 on BTC5m over
~152 s, against 600–900 resting. Standing size is plentiful; *flow* is thin.
That is consistent with the executable shadow's maker result — plenty of size
to join behind, little volume coming through to clear the queue.

## Atomic versus incremental

`accumulation_upper_bound` reports time and VWAP to reach each size by
acquiring it over several fills. It is **labelled INCREMENTAL and is never
substituted for atomic capacity**, and the router is untouched: it still
requires the whole `incremental_qty` to be available at one instant. A quantity
assembled from several fills over time is a different economic object, and only
an intent whose semantics permit incremental acquisition may use it.

The simulation is also an explicit **upper bound on speed** — no queue, no
competing taker — which is why `accumulation_trade_limited` sits beside it.
