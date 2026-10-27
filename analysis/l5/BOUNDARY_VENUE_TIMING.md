# The window boundary is ours, not the venue's

Read-only probes of the public PMUS gateway across two real boundaries on
2026-10-27, plus the nine campaign-2 preflight records. No credentials, no
order verb, no production change.

## The measurement that settles it

**1930z boundary, the venue's own tradable flag** — exactly the fields
preflight's `market_open_tradable` gate reads:

```
t= -19.3s  state=MARKET_STATUS_CLOSED  active=True closed=True   -> TRADABLE=False
t=  +0.6s  state=MARKET_STATUS_OPEN    active=True closed=False  -> TRADABLE=True
```

**1915z boundary, route availability** (first usable answer, seconds after
the boundary):

| resource | first usable | used by |
|---|---:|---|
| `/v1/market/slug/{slug}` | **−5.9s** | `resolve_market` |
| `/v1/events/slug/{event}` | **−5.9s** | `resolve_market`, and now `verify_alignment` |
| `/v1/markets/{slug}/bbo` | +40.7s | settlement sweep (the OLD slug) |
| `/v1/markets/{slug}/book` | **+83.2s** | preflight's `book_readable` |

**Our preflight passes at +80.1s to +107.8s** across all nine campaign-2
windows (`L5_LIVE_PREFLIGHT.utc`), and `L5_LIVE_ARMED` follows it by only
2.0–8.2s.

So the venue is tradable at **+0.6s** and we are armed at **+82 to +116s**.
**The ~80–107 seconds is ours.**

## Two of my own earlier claims, corrected

1. **"The delay may be venue-side."** Wrong. I proposed a venue listing
   floor as the leading explanation after seeing `/book` first answer at
   +83.2s. The tradable flag flips at +0.6s, so there is no floor on
   tradability. Removing our own redundant work CAN advance the first
   order.
2. **"`resolve_market` makes two knowingly-404ing requests per window."**
   Wrong. Its docstring says `/v1/market/slug/{slug}` 404s for BTC15m, and
   I repeated that as waste. Probed live it returns **200 with the matching
   slug**. The venue changed; the docstring is stale. There is no wasted
   probe there, and the docstring should be corrected when that file is
   next touched for a real reason.

## The contradiction that now gates everything

`/book` not serving until +83.2s would explain preflight's pass time
exactly — except that the engine already has a substitute for precisely
this, and it demonstrably works:

| window | book_read_attempts | preflight passed |
|---|---|---:|
| 1330z | `attempt1:ok` | +80.1s |
| 1345z | `attempt1..3:HTTPError`, **`ws_fallback:accepted`** | +84.2s |
| 1400z | `attempt1..3:HTTPError`, **`ws_fallback:accepted`** | +84.2s |
| 1415z | `attempt1:ok` | +105.8s |
| 1430z | `attempt1:ok` | +107.4s |
| 1445z | `attempt1..3:HTTPError`, **`ws_fallback:accepted`** | +106.0s |
| 1500z | `attempt1..3:HTTPError`, **`ws_fallback:accepted`** | +107.8s |
| 1515z | `attempt1:ok` | +83.0s |
| 1530z | `attempt1:ok` | +82.5s |

The five `attempt1:ok` windows got a first-try book **because by then it
was already +80s** and the route was live. The four fallback windows had a
connected, fresh, two-sided socket — `subscribed_before_open: true`,
`book_updates: 816`, `stream_age_s: 0.061` — and still only passed at +84
to +108s.

`BOOK_READ_ATTEMPTS = 3` with `BOOK_READ_BACKOFF_S = 1.0` costs 3s, so a
preflight attempt starting at +3s should have reached the WS substitute by
+6s and passed. It did not. Either:

* **(a)** preflight's retry ladder (`PREFLIGHT_BACKOFF_S = 3, 6, 12, 24`,
  cumulative +3/+9/+21/+45/+69/+93) is being starved because
  `maybe_preflight` sits at the top of a SEQUENTIAL tick whose remaining
  housekeeping reads take ~80s to pace out — so the second attempt cannot
  happen until the first tick finishes; or
* **(b)** the authenticated socket is not yet subscribed to the NEW slug
  on the first attempt, and `subscribed_before_open` describes the state at
  the attempt that eventually passed rather than the first one.

**Neither is established.** Both are testable with one cheap change: record
every FAILING preflight attempt with its timestamp and blocking gate.
Today a failed attempt whose blockers are unchanged emits nothing, which is
why 80 seconds of this engine's most expensive interval is invisible.

## Why this matters economically, bounded honestly

A round takes ~99s (1500z: first order +142.4s, round 4 by +538s). Eighty
seconds is about one round, worth ~1.84 matched contracts ≈ **$0.14 of
matched surplus** at the measured $0.07692/contract — and more importantly
it is the only credible route from today's **30% envelope utilisation**
toward the **$0.615/window** that the $12 envelope allows at all.

That figure is contingent on cause (a) or (b) being fixable. It is not
claimed, and no change has been proposed on it.

## Parity

Both landed commits are clean against baseline `a79fb8a3`
(20 failed / 13849 passed / 14 errors, 34 named):

| commit | result | named set |
|---|---|---|
| `d769b963` limiter purpose accounting | 20 failed, **13876** passed, 14 errors | **identical, 34** |
| `2115c989` alignment by slug lookup | 20 failed, **13888** passed, 14 errors | **identical, 34** |

+27 and +39 passed are exactly the new test files.
