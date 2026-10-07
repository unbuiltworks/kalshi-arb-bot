# BOOK_ONLY_ACTIVITY_UNAVAILABLE — exact window

**Status:** CLOSED. Both edges are source-backed: the start from the live
log, the end from the collector's own `activity_availability` CLOSE row on
the tape. Neither is asserted by this file.

## The window — TWO bounds, and the difference matters

| field | value |
|---|---|
| label | `BOOK_ONLY_ACTIVITY_UNAVAILABLE` |
| **true start (UTC)** | **2026-10-07T03:33:10Z** — first observed `activity poll failed`, deployment `2aa5cb96` |
| machine-readable start on the tape | `2026-10-07T04:03:01.867925Z` — deployment `b08498b1` |
| **end (UTC)** | **2026-10-07T04:17:51.286224Z** — `activity_availability` CLOSE edge, deployment `fb8a9444`, `closed: true` |
| true duration | **44 min 41 s** |
| duration the tape's own interval covers | 14 min 49 s |
| **UNRECORDED LEADING GAP** | **03:33:10Z → 04:03:01.868Z, 29 min 52 s** |

### Read this before using the derived file

`derived/activity_windows.json` **understates this outage by 29 min 52 s.**
The OPEN edge could not be written before the code that writes it existed:
the observability change shipped at 04:03, so the first ~30 minutes of
failure were recorded only as individual log lines, with no
`activity_error` rows and no OPEN edge on the tape.

This is the exact failure mode the label exists to prevent, occurring in
the instrument rather than the data. Stating it here is the correction;
the tape is append-only and will not be rewritten to manufacture rows
that were never observed.

**Therefore: exclude `2026-10-07T03:33:10Z → 2026-10-07T04:17:51.286Z`,
the TRUE bound above — not the narrower interval the derived file
reports.**

| | |
|---|---|
| books during the whole window | **HEALTHY AND UNAFFECTED** — two-token rows continued throughout; `single_token_markets: []`, `http_429: 0` |
| activity during the whole window | **ZERO rows, and ZERO is UNMEASURED** |

## The rule this file exists to enforce

> Missing L5 activity inside this interval is **UNMEASURED, not a zero-fill
> observation**, and must not be analysed as one.

Any study that counts L5 fills, computes a fill rate, measures an
EXTEND→COMPLETE turn, or treats "no activity row" as "L5 did not trade"
**must exclude this interval** or report it separately. The collector
enforces this on the tape itself: every failed poll wrote an
`activity_error` row carrying `ANALYSIS_WARNING =
BOOK_ONLY_ACTIVITY_UNAVAILABLE`, and the interval is bracketed by
`activity_availability` rows at both edges, so a downstream consumer that
reads the raw tape cannot miss it.

## Root cause (evidence, not inference)

Exposed by the observability-only change in `f221b43b4`, then read off the
live log of deployment `b08498b1` at `2026-10-07T04:03:03Z`:

```
status      400 Bad Request
body        {"error":"required query param 'user' not provided"}
endpoint    https://data-api.polymarket.com/activity?limit=<v>&type=<v>&user=<v>
class       HTTPError
x-request-id 592a4ffbf2ea0cbe670904e74e4e368f
```

The `user` parameter **was present**. `data-api` validates it as an address
and reports a non-address as *absent*, so the collector's profile-handle
default (`l5zn1bwom8etsk`) produced an error indistinguishable from a
missing parameter. `src/l5_5m_coverage/run.py:28` already recorded the
convention — *"data-api keys on the WALLET, not the handle"* — and the new
collector regressed it.

Why this was not reproducible locally: this container's egress proxy
returns `Tunnel connection failed: 403 Forbidden` for
`data-api.polymarket.com`, for every parameter variant. Railway reported
`HTTPError`, meaning the host *replied*. That asymmetry is why exposing the
status and body from the deployed host was the required first step rather
than a local guess.

## Repair (`fd76be5a2`)

GET-only, smallest sufficient:

1. `L5_ADDRESS_DEFAULT` → `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`, the
   proxy wallet this repository already documents for the same subject
   (`analysis/l5/POLYMARKET_FEE_SOURCES.md`,
   `analysis/l5/LANE5_NEWEST_ACTIVITY_AUDIT.md`). `L5_HANDLE` keeps the
   handle for labelling and is never sent as `user`.
2. `is_wallet_address()` plus a **blocking** boot-gate row,
   `activity_identity_is_wallet_address`. The venue's error for a wrong
   identity is byte-identical to its error for a missing one, so a wrong
   identity is silent at the HTTP layer and has to be refused at boot.

Unchanged: market selection, pacing (`BOOK_SHADOW_MAX_RPM = 240`), book
sampling, schema semantics, research logic, the mutation-incapable boot
gate, and zero credentials. No tape part was rewritten, truncated or
discarded; `TapeWriter.MODE` remains `"a"`.
