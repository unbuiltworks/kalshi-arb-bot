# Does refreshing Gemini change the book? Yes — and here is what that means

**Trigger.** The owner observed that manually refreshing Gemini changes the
displayed order book. That is a claim about our *data path*, not about the
venue, and it had to be settled before any Gemini capacity conclusion could
stand.

**Status: FINAL for capacity** (see Part 6, 15 windows / 8,186 samples / 48 min).
Parts 1–4 are kept as written, including the two interim capacity tables that
Part 6 supersedes — the revisions are the record.

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

---

# Part 3 — the finding that limits incremental accumulation, and it is not Gemini

Incremental acquisition takes time, and an intent is a statement about **one
moment**. So: would a *later* L5 intent still have authorized a purchase that
was still being assembled?

For each intent, the next intent in the **same market** at least *h* seconds
later must agree on two things — same desired side, and a ceiling no lower than
the original. Markets with no later intent are UNKNOWN and excluded from the
rate rather than counted either way.

| source | h | still authorized | side flipped | ceiling fell | rate |
|---|---|---|---|---|---|
| **REPLAY** (L5's own fills) | 30 s | 12 | **36** | 22 | **0.171** |
| REPLAY | 60 s | 15 | 39 | 14 | 0.221 |
| REPLAY | 120 s | 15 | 32 | 15 | 0.242 |
| REPLAY | 300 s | 11 | 23 | 9 | 0.256 |
| MODEL (our prediction) | 30 s | 11 | 35 | 22 | 0.162 |

REPLAY and MODEL are split deliberately: a side flip in REPLAY is **L5 changing
his mind**, while a flip in MODEL is only our model alternating. Pooling them
would let model churn masquerade as trader behaviour. They come out almost
identical — 0.171 against 0.162 at 30 s — so the alternation is L5's, and the
model is tracking it.

**About 83 % of L5's intents are no longer authorized 30 seconds later**, mostly
because he has flipped side. Even at 300 s only a quarter survive.

## What that means

1. **Incremental accumulation over tens of seconds is not a safe substitute for
   atomic execution in this replication.** Inventory assembled slowly is
   inventory L5 had already stopped wanting at that price — it would be our
   position, not a reproduction of his.
2. It independently justifies keeping the router atomic. The incremental
   numbers stay a measurement, not a policy.
3. **The binding constraint is time, not Gemini.** A ~15 s observation lag
   against an intent that decays within 30 s leaves very little room, and no
   amount of venue depth widens it.

## On the Gemini UI (item 5), stated as inference

Not directly determinable — there is no UI automation here and none was added.
But it does not need to be: REST agrees with the continuously maintained book
on the best price 99.15 % of the time and on the size resting there 100 % of
the time, so any UI reading either source is showing the same book we are. The
owner's observation is **fully explained by the measured churn** — a cancel and
re-place 25 ms apart, 0.992–0.994 of placement volume being refresh — without
needing a UI-specific mechanism. Labelled inference, not measurement.

---

# Part 4 — capacity, across 7 windows (supersedes the 2-window interim)

> **These figures move with sample size, and they have moved DOWN.** At 10
> windows / 4,281 samples / 27 min the same statistics read: BTC15m no-ask
> **6.4 %** (was 1.7 %), q=20 fillable **91.6 %** (was 97.9 %), touch ≤ 1 share
> **13.8 %**; BTC5m no-ask **27.4 %** (was 21.2 %), q=20 fillable **67.4 %**
> (was 73.3 %). The qualitative conclusion is unchanged and the direction is
> worth stating plainly: the early windows were the flattering ones. Treat
> `report.py` on the live store as the source of truth, not any table frozen
> here.


3,131 time-weighted samples over 1,213 s, 7 windows, **0 socket gaps, 0
reconnects**. The Part 1 table was two windows and it was not representative —
these numbers supersede it.

| | BTC15m (n=1479) | BTC5m (n=1491) |
|---|---|---|
| no ask side at all | **1.7 %** | **21.2 %** |
| whole ask side ≤ 1 share | 1.7 % | 21.2 % |
| **touch size ≤ 1 share** | **12.8 %** | 0.0 % |
| fillable q=1, 2 | **98.3 %** | 78.8 % |
| fillable q=5, 10 | **97.9 %** | 78.8 % |
| fillable q=20 | **97.9 %** | **73.3 %** |
| touch changes / min | 21.6 | 18.0 |
| touch lifetime p50 | **3.6 s** | 4.4 s |
| touch-only read understates q=20 | 12.4 % | **28.9 %** |

## The conclusion, and it is two conclusions

**The distribution is bimodal by cohort. Averaging them would hide the finding.**

**BTC15m — Gemini is not capacity-limited.** q=20 is executable **97.9 %** of
the time, and the rate is flat from q=1 to q=20: when it can trade at all it can
trade 20 as easily as 1. The binding constraint is **price**, exactly as the
ceiling table in Part 2 shows — at a 0.11 ceiling the book is one contract, at
0.14 it is 846.

**BTC5m — Gemini is genuinely unavailable a fifth of the time.** 21.2 % of
samples have **no ask side at all**: not thin, absent. q=20 is executable only
73.3 % of the time. This cohort cannot be relied on for a timely Up
acquisition, and that is a venue fact, not an artifact.

**The original observation was real.** On BTC15m the touch holds ≤ 1 share
**12.8 %** of the time — so `depth 1.0 @ 0.66` was a true reading of a
recurring state. It was only wrong as a statement about the venue's capacity,
because 12.4 % of the time a touch-only read understates what q=20 could have
taken. Both halves of that matter: the reading was honest, the inference was not.

**And the touch is fast.** Lifetime p50 **3.6 s** on BTC15m, changing 21.6
times a minute. Against a ~15 s observation lag, the book we act on is several
touch-generations newer than the action we are reacting to.

## Where the blocker actually is

Not Gemini's depth. In order:

1. **Intent decay** — 83 % of L5's intents are no longer authorized 30 s later
   (Part 3).
2. **Observation lag** — p50 ~15 s on BTC15m, ~133 s on BTC5m.
3. **Price** — capacity at a 0.11 ceiling is one contract; this is a *pricing*
   constraint that looks like a depth constraint.
4. **BTC5m availability** — 21.2 % no-ask.

Gemini's ability to supply size on BTC15m, at 97.9 % for q=20, is not on that
list.

---

# Part 5 — what changed in the engine as a result

The investigation was asked for because Gemini capacity looked like the
blocker. It is not, so the engine changes are where the blocker actually is.

## 1. The data path (done, Part 1)

`gemini_book()` reads the maintained websocket book first, REST as fallback,
`source` recorded on every row. Book age p50 **0.52 s → 0.042 s**. Live
confirmation: the shadow's last 40 Gemini evaluations all read `websocket`.

## 2. The execution model (done, Part 1)

The ladder evaluator replaced the touch-depth read. Live proof from the running
stream: **72 of 342** evaluations have `executable_qty` above touch depth, and
on Gemini specifically **touch 50 → 678 executable at the ceiling**, touch 500
→ 776. That is the execution-model error, closed and observable.

## 3. Intent validity (new)

`DesiredInventoryAction` now carries `valid_for_s`, default **30 s**, and the
router declines an expired intent **before any venue is ranked**:

```
route: NEITHER
reason: intent expired: 44.0s old against a validity of 30.0s. It is a
        statement about a moment that has passed, not a cheaper venue problem.
```

The default is set from the measurement, not chosen: only 17.1 % of L5's own
intents survive 30 s. It is a *ceiling* on validity and an uncomfortable one —
at 30 s the intent is already mostly gone.

Age is measured from `t_utc`, the moment the engine formed the intent, which
for a fill-anchored intent is **L5's own event time**. Measuring from when we
observed it would hide the feed lag inside the validity window and make every
intent look fresh.

`valid_for_s` is part of the hashed identity: an intent valid for 30 s and one
valid for 300 s are different instructions, not the same instruction annotated.

## 4. The age budget is now visible

Every routing decision records `intent_age_at_decision_s`. First live rows show
**9.6 s of a 30 s budget already spent** before we could price anything, on a
fast BTC15m observation. On BTC5m, where observation lag runs ~133 s, intents
will expire before they are priceable — and the router will say so rather than
trading them.

Two new promotion gates (G6.7, G6.8) pin both.


---

# Part 6 — FINAL capacity statement: it is time-to-expiry, not cohort

15 windows, **8,186 time-weighted samples**, 2,881 s, **0 socket gaps, 0
reconnects**. This supersedes the 2-window and 7-window tables above.

I said earlier the distribution was "bimodal by cohort". **That was the wrong
cut.** Split by time to expiry instead and one mechanism explains most of it:

| cohort | band | n | share | q=20 fillable | no ask side |
|---|---|---|---|---|---|
| BTC15m | **excl. last 60 s** | 3448 | 90.2 % | **99.25 %** | **0.00 %** |
| BTC15m | last 60 s | 374 | 9.8 % | 37.17 % | **46.26 %** |
| BTC5m | **excl. last 60 s** | 2759 | 70.6 % | **88.66 %** | 7.43 % |
| BTC5m | last 60 s | 1151 | 29.4 % | 45.18 % | **51.69 %** |

**Gemini withdraws in the final minute, on both cohorts.** Roughly half of all
samples inside the last 60 s have *no ask side at all* — 46.3 % on BTC15m and
51.7 % on BTC5m. That is one venue behaviour, not two cohort personalities.

**Most of the apparent cohort gap is composition.** A 5-minute window spends
~20 % of its life inside the last minute against a 15-minute window's ~6.7 %,
and the observed sample shares (29.4 % vs 9.8 %) track that. Outside the final
minute a real but much smaller cohort difference remains: **99.25 % vs
88.66 %** at q=20.

### Full-window figures, for completeness

| | BTC15m (n=3822) | BTC5m (n=3910) |
|---|---|---|
| no ask side at all | 4.5 % | 20.5 % |
| touch ≤ 1 share | **15.2 %** | 0.0 % |
| fillable q=1 / q=5 / q=20 | 95.5 / 93.2 / **93.2 %** | 79.5 / 79.5 / **75.9 %** |
| best ask size p05 / p50 / p95 | 1 / **500** / 720 | 10 / 50 / 1081 |
| total ask size p50 | 722 | 620 |
| touch lifetime p50 | **2.1 s** | 4.3 s |
| touch changes / min | 29.2 | 17.8 |
| touch-only read understates q=20 | 12.9 % | **27.3 %** |

### The conclusion

**Gemini is not capacity-limited for L5-sized orders outside the final minute.**
At q=20 it is executable **99.25 %** of the time on BTC15m and **88.66 %** on
BTC5m, and on BTC15m the completion rate is flat from q=1 to q=20 — when it can
trade at all it can trade 20 as easily as 1.

**Inside the final minute it is unreliable on both cohorts** and roughly half
the time cannot be bought at all. A late COMPLETE is the action that would need
that window, so this is a real constraint on one specific L5 behaviour rather
than on the venue generally.

**The original observation stands and was never the problem.** The BTC15m touch
holds ≤ 1 share **15.2 %** of the time — `depth 1.0 @ 0.66` was a true reading
of a recurring state — and a touch-only read would understate q=20 capacity
12.9 % of the time on BTC15m and 27.3 % on BTC5m. Honest reading, wrong
inference; both are now fixed and measured.

## Reconciling with the executable shadow

The shadow reads Gemini over the websocket **144 of 155 evaluations (92.9 %)**
since cutover — the remainder are REST fallbacks while a new window's socket
delivers its `initial`, which is the fallback working as designed. Maintained
book age **p50 0.069 s** against REST's 0.52 s.

Its route distribution is **not** contradicted by 99 % Gemini fillability,
because fillability is not eligibility. Gemini loses on **price**, not size:
it is at or under L5's own achieved price only **23.3 %** of the time against
Polymarket's 45.8 %, and every declined Gemini route in the sample was a price
decline, never a capacity one.

## Maker evidence at the 300 s horizon

Raising the horizon from 120 s did what it was meant to — it converted
"undecided for want of any print" into decisions:

| | evidenced fill | evidenced no-fill | undecided |
|---|---|---|---|
| at 300 s (n=221) | **25** | 24 | 172 |
| Gemini | 11 | 16 | 146 |
| Polymarket | 14 | 8 | 26 |

Among **decided** placements the evidenced-fill rate is **0.64 on Polymarket
and 0.41 on Gemini**, both above the G4.4 threshold of 0.20 — but on 22 and 27
decided placements against a gate requiring **200 per venue**. Early, and
labelled early. 64 of these are now decided from the socket's own `trade`
events rather than a polled list.
