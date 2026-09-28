# Rung-A activation — state at the DISARMED stop point

Owner approval 2026-09-28 for artifact `e48960f7`, $1.00 envelope, with the
activation-semantics clarification (HALT not used as a claimed control; establish
known-disarmed arm state; quiesce the continuous runner; fresh session/auth ids).

**Stopped before the DISARMED -> ARMED transition, as instructed. No order, no
arm triple set, no mandate installed.**

## 1. HALT — superseded, and no code path added

Per the clarification, `L5_LIVE_HALT` is **not** treated as a control for this
artifact, because the reviewed 44-file capability graph does not read it. No HALT
code path was added to satisfy the earlier wording. The effective stop controls
are the ones the artifact actually implements:

| control | where it lives | state now |
|---|---|---|
| arm triple | `src/pmus_auth.py:47` — needs `PMUS_Q1_LIVE=YES` **and** non-empty `PMUS_Q1_SESSION_ID` **and** `PMUS_Q1_ARM == SESSION_ID` | all three **written empty** |
| fresh mandate | `PMUS_LIVE_MANDATE`, `src/pmus_live_mandate.py:180` `load_mandate` — no fallback to a rung table, a campaign record or a previous mandate | **written empty** (`no_mandate`) |
| GuardedTransport / `admit()` | enforced at `POST /v1/orders` under exclusive `flock` | in the graph, unreachable without a mandate |
| one-shot execution | `main()` runs one market and returns | `restartPolicyType NEVER` |
| monitoring abort | `ProbeMonitor.require_healthy()` raises loud | in the graph |
| single market / $1 | mandate caps | mandate absent, so nothing is permitted |

## 2. Known-disarmed state WRITTEN, not inferred

The Railway MCP authenticates as an OAuth app, so variable **values are never
returned** — only names. Reading the existing arm state was therefore impossible,
which was abort condition 3 of `RUNG_A_ACTIVATION_ABORT_001.md`. Resolved as
instructed: the state was **written**, so it is known by construction rather than
by inference.

Written to `l5-live-probe-oneshot` (`84aa9999-…`) with `skipDeploys: true`, so
the write itself deployed nothing:

```
PMUS_Q1_ARM              = ""      disarmed
PMUS_Q1_LIVE             = ""      disarmed
PMUS_Q1_SESSION_ID       = ""      disarmed
PMUS_LIVE_MANDATE        = ""      no mandate -> admit() cannot pass
L5_RECOVER_ONLY          = ""      OFF
L5_RECOVER_MARKET        = ""      cleared
L5_RECOVER_JOURNAL       = ""      cleared
L5_RECOVER_STATE_SOURCE  = ""      cleared
L5_OBSERVE_ONLY          = "1"     ON, for this verification run only
L5_LIVE_PROBE_MARKET     = ""      unbound; bound at arming to a FUTURE window
L5_MAKER_ONLY            = "1"     maker-first
L5_MAKER_TREATMENT       = ""      OFF
L5_IMBALANCE_SUPPRESS    = ""      OFF
L5_PROBE_STATE           = /state/pmus-ws/rung_a_inventory.json
L5_SESSION_JOURNAL       = /state/pmus-ws/rung_a_journal.jsonl
L5_LIVE_AUTH_LEDGER      = /state/pmus-ws/rung_a_auth_ledger.jsonl
```

`L5_RECOVER_ONLY` mattered most. The build being replaced was the Probe-08
RECOVER-ONLY image, and a stale `L5_RECOVER_ONLY` has already hijacked two runs
that were meant to observe (1615z and 1800z), each time with no symptom but a
report that never arrived. Every mode flag is opt-in truthy
(`"1"/"YES"/"TRUE"`), so `""` is a definite OFF.

The three state paths are **fresh**, so no prior session's inventory,
journal or one-use authorization can reach this probe. `L5_LIVE_AUTH_LEDGER`
being fresh is what makes "no historical authorization reused" true rather than
asserted.

## 3. Artifact pinned immutably

```
repo        unbuiltworks/kalshi-arb-bot
branch      claude/l5-pmus-economic-results-7c116e
commitSha   e48960f709e3aaef9a6193b584e8d5b5b75fe345
dockerfile  deploy/l5_pmus_lock5_probe/Dockerfile
start       python3 -m scripts.l5_pmus_live_oneshot
restart     NEVER
watch       deploy/l5_pmus_lock5_probe/ENABLE_RUN_BY_OWNER_ONLY/**   (path does
            not exist -> no push can auto-deploy this service)
```

Pinning `commitSha` means pushes to the branch no longer deploy this service at
all, so the serving artifact cannot drift from the reviewed one while we wait.
The watch-pattern guard is belt-and-braces, carried over from the previous
configuration's idiom.

The Dockerfile at `e48960f7` was verified to `COPY` **exactly** the 44 files of
the reviewed AST import closure — set-equal, no additions, no omissions — and to
`CMD` the one-shot entrypoint. `pmus_l5_ramp_ladder` and `pmus_micro_executor`
appear nowhere in it.

## 4. Fresh identifiers, never reused

```
session id        l5probe-20260928-btc15m-rungA-237d2c62
authorization id  OWNER-2026-09-28-LOCK5-CAPACITY-RUNG-A
```

Both are new. The authorization id is the one in the approved mandate and has
never been used; with a fresh `L5_LIVE_AUTH_LEDGER` its consumption count starts
at zero, which is checkable rather than asserted.

## 5. Why the mandate is NOT installed yet

The approved `expiry` is **authorized_at + 4h**. Installing the mandate now would
start that clock against an unbounded wait for the go-ahead, and the probe would
very likely arm against an expired authorization — or worse, be armed inside a
window the mandate no longer covers. So `PMUS_LIVE_MANDATE` is written empty, and
the mandate is installed **together with** the arm triple at activation, with
`authorized_at` set to that moment. No parameter changes: the JSON is exactly as
approved apart from `authorized_at` and the `expiry` derived from it.

The same reasoning applies to `L5_LIVE_PROBE_MARKET`. Probe 08's first attempt
failed closed on a market pin six hours stale; a window bound now would be stale
before arming. It is bound at activation, to a window that has not yet opened.

## 6. Still OUTSTANDING before arming

* **`l5-pmus-live` is not yet quiesced.** See `RUNG_A_QUIESCE_RESTORE_RECORD.md`
  for its exact pre-probe configuration. The act required is
  `numReplicas: 1 -> 0`, and **this MCP cannot do it**: `update-service` states
  that scaling is not handled, there is no stop/scale tool, and no Railway CLI or
  token exists in this container. Options, none taken:
  1. owner scales it to 0 in the dashboard (one edit, exactly reversible);
  2. authorize `cronSchedule "0 0 1 1 *"` — this project's own idiom for parking a
     service — which needs a redeploy of a credentialed money-capable service to
     take effect, and I will not redeploy that lane uninstructed;
  3. accept it running while demonstrably disarmed, which does **not** satisfy
     "sole credentialed money-capable process."
* **Mandate install + verification**, at activation.
* **Window binding**, at activation, to a future BTC15m window.
* **Clean preflight** on the newly built image.

## 7. Build and known-disarmed verification — EVIDENCE

Build: deployment `a64ecf5a-fe8d-4438-9ce0-e497da884d9c`, **SUCCESS**,
2026-09-28T04:12:58Z, commit `e48960f709e3aaef9a6193b584e8d5b5b75fe345`, branch
`claude/l5-pmus-economic-results-7c116e`, region `us-east4-eqdc4a`.

A first attempt (`65b1a618`) came back **SKIPPED**: the watch-pattern guard I had
just set blocked its own build. Recorded because the fix matters — the guard was
removed rather than worked around, on the reasoning that a `commitSha` pin already
prevents branch pushes from deploying this service and is therefore the stronger
control. Two documentation pushes have landed on the branch since; the serving
artifact is unchanged, which is the pin doing its job.

**The artifact's own first line, from the running container** — not inferred from
variables I cannot read:

```json
L5_MODE {"mode": "OBSERVE_ONLY", "observe_only": "1", "recover_only": "",
         "market_pin": "", "recover_market": "", "maker_treatment": false,
         "maker_only": true, "imbalance_suppress": false, "treatment": "NONE",
         "session": "", "armed": "", "live": ""}
```

`session`, `armed` and `live` all empty: `pmus_auth.armed()` requires all three
and a token match, so this is **DISARMED, demonstrated by the artifact itself**.
`recover_only` empty confirms the stale-recovery hazard is cleared, and
`mode OBSERVE_ONLY` means this run holds a NullTransport and cannot submit at all.

The OBSERVE_ONLY report (`L5_OBSERVE_ONLY …`) had **not yet been emitted** when
this was written — the run observes until the window closes. It is a confirmation
of preflight health on the new image, and it is outstanding, not passed.
