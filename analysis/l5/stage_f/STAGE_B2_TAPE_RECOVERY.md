# Recovering the 1415z cap decision record — access blocker and minimal route

**Target.** The `RESIDUAL_CAP` and `INVENTORY_CLASS` notes for order
`l5f-fe3923afb7f1f9fc4f908ad3` (UP 2.08, filled 14:21:12.6Z), which took
unmatched UP inventory from 2.17 to 4.25 against a 3-contract ceiling.

**Location.** `/data/l5-live-20261012z/decisions.jsonl` on the
`l5-pmus-live` volume (`self.tape`, `l5_pmus_live_runner.py:1195`). Each line
is `{"t", "row", "record", "armed", "preflight", "decision_role"}` and
`record` is the full provenance chain, including both notes.

**Executor state throughout: DISARMED.** Nothing below was deployed, rearmed
or modified. The only change made anywhere was installing the Railway CLI in
my own session container.

## Access routes tried, and why each fails

| route | result |
|---|---|
| Railway MCP tools | **No exec, shell or file-read tool exists.** Every write-capable tool creates a service, a function, or a deployment. |
| `railway ssh` | **Exists and takes a non-interactive command** — the right mechanism. Refused: "No SSH keys found", and then `railway ssh keys list` refused with **"SSH key management is not supported with project tokens (`RAILWAY_TOKEN`). Use a workspace API token (`RAILWAY_API_TOKEN`) or run `railway login`."** My session holds only `RAILWAY_PROJECT_TOKEN`. |
| `l5_halt_server` (in-container, has volume) | Strict allowlist: `SERVED_KEYS = (HALT_KEY, FLOOR_KEY, REVOCATION_KEY)`; anything else is 404. No arbitrary path, by design. |
| `l5_order_registry_server` (in-container) | Single key: `path != REGISTRY_KEY` → 404. |
| `DC.from_tape` via `L5_DEPTH_COMPARISON` | **Reads the tape and emits to the logs** — the closest existing route — but extracts only `ORDER`/`intent`/`bbo`/`TWO_SIDED_PROBE` for a depth comparison, and emits an aggregate. It carries neither note. |

**Blocker, stated exactly:** container access requires an SSH key registered
on the Railway account, and the token available to this session cannot
register one. Registering a key would mint a new durable credential on the
owner's account, which is not an "existing authorized route" and is not
mine to create.

## Recovery option A — RECOMMENDED. No code, no deploy, no new service.

An owner-side `railway ssh` with a workspace-scoped token. One command, read
only, output already narrowed and redacted:

```bash
railway ssh \
  --project 50fb7357-e2bc-4d04-a49e-71eb5d73b0d1 \
  --environment f3642d27-26dc-4e21-9170-bc76290c076f \
  --service 87f1834e-530e-4fc4-a310-91c8d3add196 \
  "grep -F 'l5f-fe3923afb7f1f9fc4f908ad3' /data/l5-live-20261012z/decisions.jsonl \
   | python3 -c \"
import json,sys
KEEP=('RESIDUAL_CAP','INVENTORY_CLASS','FINAL_RESIDUAL_GUARD')
for ln in sys.stdin:
    try: d=json.loads(ln)
    except Exception: continue
    r=d.get('record') or {}
    o=r.get('ORDER') or {}
    out={'t':d.get('t'),'armed':d.get('armed'),
         'coid':o.get('client_order_id'),'submitted':o.get('submitted'),
         'order_reason':o.get('reason'),
         'refused_by':r.get('refused_by_layer'),
         'refused_reason':r.get('refused_reason')}
    for k in KEEP: out[k]=r.get(k)
    print(json.dumps(out,indent=2,default=str))
\""
```

**Why it is safe to run while disarmed:** `grep` and a read-only stdin
filter. It opens the tape for reading, writes nothing, and touches no venue.

**Why the output is already redacted:** it emits only the two cap notes, the
guard note, the order id and the submission outcome. It never prints `row`
(which carries the book), never prints credentials, and the engine does not
write API keys or account identifiers into these notes — the only account-
shaped values anywhere in the chain are the venue order id and our own coid,
and the coid is already public in this analysis.

Also worth capturing while there, for the neighbouring orders:

```bash
railway ssh --project 50fb7357-e2bc-4d04-a49e-71eb5d73b0d1 \
  --environment f3642d27-26dc-4e21-9170-bc76290c076f \
  --service 87f1834e-530e-4fc4-a310-91c8d3add196 \
  "grep -F '2026-10-31-1415z' /data/l5-live-20261012z/decisions.jsonl | wc -l"
```

## Recovery option B — only if A is refused. Costs one redeploy.

Extend the existing read-only diagnostic. `L5_DEPTH_COMPARISON` already reads
this exact tape and emits to the logs; a sibling gated variable could emit
the two notes for a named coid. **Second choice and deliberately so:** it
changes a module that ships in the engine image and needs the engine
redeployed, which is modifying the live trading engine to retrieve evidence.
It would be done disarmed, and it is still worse than option A.

## Recovery option C — owner-side, outside my reach

The Railway dashboard's own volume tooling, if it exposes file browsing or a
snapshot. The MCP surface does not.

## What the record will settle

The nine hypotheses already eliminated (predicate logic, store reads,
configuration, sign conventions, canonicalization, stale-across-ticks reads,
the cancel-mechanism path, early return from the two-sided branch, and
overshoot) leave exactly one question, and these fields answer it:

| field | the question it settles |
|---|---|
| `RESIDUAL_CAP.residual_realized` | did the gate see 2.17, or 0? |
| `.outstanding_residual` | was order 5 counted as live, and with which sign? |
| `.prospective_before` / `.abs_prospective_before` | was `exempt_open` reached because the book read FLAT? |
| `.increases_abs_prospective` | was the order classified as reducing? |
| `.clip_used` / `.cap_contracts` | was the cap 3, or something wider? |
| `.binds` | did the gate decide to refuse and get overridden downstream? |
| **absent `RESIDUAL_CAP` entirely** | the gate was never reached — a path bypass, not a state bug |
| `INVENTORY_CLASS.treatment_active` | was `suppress_imbalance_increase` off, leaving the cap as the only guard? |

The last row matters most: **if the note is absent from the record, the
cause is reachability; if present with `binds: false`, the cause is the
state it was handed.** Those are different fixes, and the tape distinguishes
them in one line.
