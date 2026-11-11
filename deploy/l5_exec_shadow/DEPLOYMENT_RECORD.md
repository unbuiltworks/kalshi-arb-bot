# l5-exec-shadow — deployment record, 2026-09-15

**Authorized:** one dedicated research service, `restartPolicyType: ALWAYS`,
zero order-submission capability, zero trading credentials.

## What exists

| | |
|---|---|
| service | `l5-exec-shadow` · `5dced43a-b6b2-4ba9-8126-c9918072ac73` |
| project / env | `arb-ev-wallet-shadow` · production `f3642d27…` |
| source | `unbuiltworks/kalshi-arb-bot` @ `claude/l5-execution-instrumentation-io6nzi` |
| image | `deploy/l5_exec_shadow/Dockerfile` |
| start | `python3 -m src.l5_research_supervisor --state-root /state/l5-research` |
| restart policy | **ALWAYS**, max 1000 retries |
| volume | `l5-research-state` · **`/state/l5-research`** · 50 GB |
| deploys on | **`deploy/_promote/l5-exec-shadow` only** |
| replicas | 1 (`us-east4-eqdc4a`) |

Verified live with `get-service-config`, not from a file. See below for why
that distinction matters.

## The file that configures nothing

`deploy/l5_exec_shadow/railway.json` **is not read by Railway.** Config as Code
is deprecated and the API now refuses it outright:

```
Config as Code (railway.json / railway.toml) is deprecated.
Use Infrastructure as Code (.railway/railway.ts) instead.
```

It was silently inert when the service was created, which means
**`restartPolicyType: ALWAYS` and the promotion watchPattern were NOT in effect
on the first two deployments** — the second of which was triggered by an
ordinary source push, exactly the fan-out the watchPattern exists to prevent.
Both are now set through the API, which is the source of truth. The JSON is
kept as documentation, marked `_NOT_READ_BY_RAILWAY`, and a test asserts that
marker so the file cannot quietly start looking authoritative again.

## Zero order capability

* the image omits `src/l5_adapters/evaluate.py` — it imports `l5_venue_adapter`,
  which lazily reaches `pmus_q1_executor`, the order path;
* no `src/pmus_*`, `src/kalshi_*`, signer or wallet module is copied;
* no credential is read; every call is an unauthenticated GET or the public
  Gemini market-data websocket;
* asserted in a **fresh subprocess** (process-global `sys.modules` would
  otherwise let another test decide the result) and again over the Dockerfile's
  `COPY` lines.

`pmus-spring-l5-probe` is untouched: different service, different volume,
opposite restart policy, still on its 12:46 deployment.

## Restart proof — performed 15:26:12 Z

Service restarted deliberately via `restart-service`.

**All three streams recovered automatically:**

```
stream_start exec_shadow    pid 2  /state/l5-research/l5-shadow-state
stream_start gemini_depth   pid 3  /state/l5-research/gemdepth
stream_start chain_validate pid 4  /state/l5-research/chainval
   labels: "resumed against the existing store; nothing reset"
heartbeat  running: [chain_validate, exec_shadow, gemini_depth]  failures: all 0
```

**Durable histories intact, and the gap measured per stream:**

| stream | last row before restart | gap |
|---|---|---|
| `exec_shadow` | 1789485961.73 | **11.0 s** |
| `gemini_depth` | 1789485971.59 | **1.2 s** |
| `chain_validate` | 1789485932.16 | **40.6 s** |

Non-null `last_row_utc` on all three **is** the proof the volume survived — the
supervisor reads each store's newest row at boot, before any stream starts.
Contrast the first boot on an empty volume, which correctly reported
`last_row_utc: null, gap_s: null` — **UNKNOWN, never zero**.

**Work in flight survived too:** the shadow recovered **18 pending maker
quotes**, oldest placed 308.8 s earlier, each now carrying
`resolved_across_restart` so an undecided verdict across the gap can never be
read as a no-fill.

**Streams resumed producing immediately:** a chain validation pass 10 s after
boot — 148 chain fills, 145 agreeing, `feed_only: []`, no promotion blocker.

No `coverage_gap` marker fired, correctly: every gap was under two heartbeats.
A clean fast restart is not a coverage hole, and marking it as one would make
the marker meaningless when it matters.

## Standing instruction

Leave the service alone and collect evidence. It deploys only when someone
appends a line to `deploy/_promote/l5-exec-shadow`.
