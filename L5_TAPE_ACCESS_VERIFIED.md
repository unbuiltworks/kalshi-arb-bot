# Workstream A — storage cadence verified, and my own null result overturned

**Read-only verification only. No log volume increased, no new capture started,
no service touched.** Measured 2026-10-25 15:40–16:00Z from the collector's own
counters.

## Storage cadence vs logging heartbeat — the measurement asked for

Authoritative source: the writer's own row counter in `PMUS_BOOK_TAPE`, which
needs no new instrumentation.

| | measured |
|---|---|
| cycles | 400 in 875.3 s → **one cycle / 2.19 s** (config says `--interval 2`) |
| **rows written to volume** | **800 in 875.3 s → one row / 1.09 s** |
| rows per cycle | 2.00 |
| file | `pmus_btcupdown15m_20260927T224630Z.jsonl`, **2,191,674 rows** since 09-27 |
| **logging heartbeat** | `HEARTBEAT_S = 30.0` → echoed median gap **30.10 s** |
| **storage ÷ log channel** | **27.5×** |
| per 15-min window | **~823 rows stored** vs **~30 echoed** (measured 22/21/27/24) |

## The echo is lossy — which overturns my previous null

`pmus_book_tape.py` suppresses an echo only when the body is **unchanged**, with
the comment *"an unchanged repeat loses nothing — the reader carries it
forward."* If that held, the echoed stream would be lossless and my earlier
lag-test null would be real. It does not hold:

| consecutive echoed snapshots | identical body | **different body** |
|---|---|---|
| gap ≥ 25 s | 1 | **80** |
| gap < 25 s | 0 | 4 |

**80 of 81 heartbeat-spaced echoes have a different body**, so the book changed
during those gaps and those transitions never reached stdout. The likely cause
is Railway log rate-limiting on ~1 row/s of ~4 KB payloads, not the dedup.

> **Consequence, stated plainly: the n=55 lag-test null in
> `L5_ISSUE192_EXECUTION.md` is NOT a clean null.** It was computed on a stream
> missing most state transitions, so it is evidence about the channel, not about
> the market. It is withdrawn, not reversed — I am not claiming an edge either.

That is the second time this cycle that a conclusion of mine turned on dataset
provenance rather than on the market.

## Existing data access is now exhausted, and exactly why

1. Storage is dense (1.09 s/row) and lives on a Railway volume.
2. A Railway volume mounts into **one** service. The `/state` path appears in 21
   deploy configs, but each is its own volume — only `pmus-btc15m-tape` can read
   this file.
3. This session runs outside the Railway project network, so
   `RAILWAY_PRIVATE_DOMAIN` is unreachable; no public domain exists for the
   service and creating one would be new exposure.
4. The log channel is the only route out, and it is measurably lossy.
5. This module's own header already documented the constraint: the dense tape
   lands *"onto a Railway volume this container cannot reach, and the export
   routes all cost either a redeploy or a credential."*

## Implemented: a bounded read-only export (ready, not deployed)

`src/pmus_book_tape.py` — `dump_existing()` plus
`--dump-existing / --dump-since / --dump-until / --dump-max / --dump-slug /
--dump-only`.

It reads rows **already on disk** and writes them to stdout. It opens no socket,
takes no credential, never writes to the tape, has no order path, and is bounded
**twice** — by the time range and by `DUMP_MAX_ROWS = 20000` — so a mistyped
range cannot flood the logs and a one-time dump cannot become a sustained
increase.

**8 tests, all passing** (`tests/test_pmus_tape_dump.py`): cap is a hard ceiling,
range filters inclusively, slug filter excludes other markets, **the tape is
byte-identical and its mtime unchanged after a dump**, a missing directory is
reported rather than raised, and a monkeypatched `socket` proves no socket is
opened.

### What it costs, and why I have not run it

Running it requires a **redeploy of `pmus-btc15m-tape`**, because only that
service can see the file. That is:

- a restart of a read-only collector — its own Dockerfile says *"a restart loses
  at most one interval rather than the window"*;
- a **one-time** log burst bounded by `--dump-max`, not an ongoing increase;
- no money path, no credentials, no new service, nothing in the trading path.

Suggested first slice — 4 hours over the windows I already hold tick spot for,
well inside the cap:

```
python3 -m src.pmus_book_tape --series btc-updown-15m --with-book \
  --interval 2 --minutes 0 --out-dir /state/pmus-btc15m \
  --dump-existing --dump-slug cpc-btc-updown-15m-2026-10-25 \
  --dump-since 2026-10-25T12:00:00 --dump-until 2026-10-25T16:00:00 \
  --dump-max 20000
```

≈ 823 rows/window × 16 windows ≈ **13,000 rows**, then collection continues in
the same process.

**I am not deploying this without your go**, because it restarts a currently
healthy collector that is unrelated to the live campaign, and because "do not
increase log volume" deserves an explicit decision even for a bounded one-off.

## Honest status of the causal analysis

The rerun you asked for **cannot be completed yet**, and the blocker is now
proven rather than inferred: the data exists at 1.09 s, the channel I can read
delivers ~30 s and drops 80 of 81 transitions, and the export costs one
redeploy. Everything downstream is already built and tested — tick spot
extractor, full-depth book extractor, the causal lag test with venue-timestamp
leakage controls, measured skew (spot 3.37 ms, book 1,541 ms), the replay engine,
fee and queue models. The one missing input is the file.
