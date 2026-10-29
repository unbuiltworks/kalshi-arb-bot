# The ~82 s late subscription: causation, then the fix

Read-only collector work. The `FULL >= 290 s` definition is **not** changed, and
no missing early observation is fabricated — the fix makes us *subscribe*
earlier, it does not invent anything before the first real frame.

## The symptom

Six consecutive window digests on serving build `d3302fcd`:

| close  | market  | tte_first_any | tte_last_any | span_any_s |
|--------|---------|---------------|--------------|------------|
| 17:21  | BTC05M  | 247.88        | 0.52         | 247.36     |
| 17:21  | ETH05M  | 247.88        | 0.82         | 247.06     |
| 17:31  | BTC05M  | 247.65        | 0.61         | 247.04     |
| 17:31  | ETH05M  | 247.65        | 0.60         | 247.05     |
| 17:31  | BTC15M  | 247.65        | 0.60         | 247.05     |
| 17:31  | ETH15M  | 247.65        | 0.61         | 247.05     |

Two facts the earlier diagnosis did not use:

1. **The 15-minute windows show the identical 247.65.** A 15m window has been
   open for ten minutes by the time a 5m window in the same batch even
   exists. If the cause were anything about the *window* — when it opens,
   when it is listed, how it prices — the 5m and 15m numbers could not agree
   to the centisecond. They agree because all four were subscribed by the
   **same roll**, so the cause is in our subscribe path.
2. **The loss is constant, not variable.** 330 − 247.88 = 82.12 and
   330 − 247.65 = 82.35, on batches 600 s apart. A 45 s discovery tick with
   arbitrary phase would produce a loss uniform on [0, 45); it cannot produce
   the same 82 s twice.

And one window is missing outright: the batches are exactly 600 s apart, and
there is **no digest at all** for the window expiring 17:25:00 (`expiry_t`
1789406700). 600 s is the ws session period.

## Ruling out the venue

The previous commit's mechanism — "DISCOVER_SECONDS is 45, which bounds the
expected loss near 45 s" — does not survive fact (2), so before changing
anything I measured what the venue actually offers. A read-only poll of
`/v1/prediction-markets/events` every 3 s for 11 minutes, recording every
`(symbol, marketState)` pairing rather than only the ones discovery admits
(220 polls, 0 errors, 30 window/state pairings):

```
BTC05M...1850-UP  open  first listed 4116.9 s before expiry
BTC05M...1845-UP  open  first listed 4080.9 s
BTC05M...1840-UP  open  first listed 3861.9 s
...
BTC05M...1745-UP  open  first listed  561.9 s
BTC05M...1740-UP  open  first listed  261.9 s   <- already live at probe start
```

* Every pairing was in state `open`. There is no pre-open state we are
  filtering out, so the `marketState in {open, active}` filter is exonerated.
* **28 of 30 were first listed with more than 330 s of lead**, at a median of
  ~4,100 s (~69 minutes). The two short ones were mid-flight when the probe
  started and are an artefact of the probe, not the venue.

So the venue publishes each window more than an hour ahead. Every one of the
82 s is ours.

## Causation

Four defects compound, and the fourth is why the loss is reproducible rather
than noisy.

1. **Each session started blind for 45 s.** `_gemini_ws_session` seeded
   `last_discover = time.monotonic()`, so the first discovery ran a full
   `DISCOVER_SECONDS` after the session opened — with an empty catalogue.
   `kalshi_task` has always seeded so that its first pass is immediate.
2. **The subscription roll was inside the discovery branch.** Moving a window
   `known -> subscribed` is pure local computation over a ~40-entry dict, but
   it only ran when the 45 s REST tick fired. A window that came due at
   `expiry - 330` therefore waited for the next tick — up to another 45 s.
   45 + 45 = 90, which brackets the observed 82.
3. **Session-local buffers.** `known`, `closed`, `prints_by`, `one_sided_by`
   and `any_tte` were local to `_gemini_ws_session`, so a 600 s recycle erased
   the catalogue *and* silently discarded the digest of any window still open.
   That is the missing 17:25:00 window.
4. **600 is an exact multiple of 300.** The session period is twice the 5m
   window period, so the blind period and the tick grid — both anchored to
   session start — hold the same phase against every window. The loss does not
   average out across windows; it lands in the same place every time, which is
   exactly why 82 s recurs and why one 5m window in two is lost rather than a
   random few.

## The fix

Collector-only, read-only, no order path, no change to `FULL`:

* Seed `last_discover = time.monotonic() - DISCOVER_SECONDS` so the first
  discovery is immediate. It must be relative to `monotonic()`, **not** the
  literal `0.0` that `kalshi_task` uses: `kalshi_task` compares against a
  wall-clock epoch, while `monotonic()` is time since boot and can legitimately
  be *smaller* than `DISCOVER_SECONDS` in a fresh container — where `0.0` would
  leave the blind period exactly where it was.
* Run the subscription roll **every loop iteration** instead of on the REST
  tick. Only the HTTP call needs the 45 s cadence. Due -> `SUBSCRIBE` now
  happens within one recv timeout (<= 1 s), which retains the intended 330 s
  lead rather than replacing it.
* Carry the window catalogue and per-window digest buffers across sessions in
  a `state` dict owned by `gemini_task`, and prune the side-tables with
  `known`. `subscribed` is deliberately **not** carried: a new socket has no
  subscriptions, and carrying it would make the roll believe everything was
  already subscribed and send nothing — silent total data loss.

## Instrumentation kept

The digest now carries the six subscribe-path stamps — `window_discovered_at`,
`window_due_at`, `subscription_requested_at`, `subscription_ack_at`,
`first_frame_at`, `expiry` — and the four latencies between them
(`DISCOVERY -> DUE`, `DUE -> SUBSCRIBE`, `SUBSCRIBE -> ACK`,
`ACK -> FIRST_FRAME`), plus `discovery_tick_index`,
`session_age_at_discovery_s` and `session_index` so a session-recycle artefact
is never averaged together with steady state. An absent stamp reports `None`
(UNKNOWN), never `0`, which would read as "instant" and exonerate the stage
that is actually late.

**The sign convention is not uniform, and assuming it is will invert three of
the four readings.** Only `lat_discovery_to_due_s` is a *lead* — positive means
the window was known before it was due. The other three are *elapsed
durations*: `lat_due_to_subscribe_s` is positive when we subscribed **late**
(0 is the target), and the two venue-side latencies are simply smaller-is-
better. A cold-start window makes the difference concrete:
`lat_discovery_to_due_s -96.74` (no lead — the container did not yet exist)
with `lat_due_to_subscribe_s +96.74` (subscribed 96.74 s late) describe the
same event from the two different sides.

**`tte_first_any` cannot verify this fix.** It is clamped to the window
duration by construction (`0 <= tte <= _win_dur_s`), so for a 5m window it can
never exceed 300 no matter how early we attach. The unclamped measure is
`expiry - first_frame_at`, which the new fields make computable.

The ack attribution is honest about its limit: the venue acks a SUBSCRIBE
*batch*, not a symbol, so `subscription_ack_at` is exact for a single
outstanding batch and an upper bound on ack latency when batches overlap.
