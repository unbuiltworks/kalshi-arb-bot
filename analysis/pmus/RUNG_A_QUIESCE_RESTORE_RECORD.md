# `l5-pmus-live` pre-probe configuration — RESTORE FROM THIS, AND ONLY AFTER RUNG A

Captured from Railway 2026-09-28, **before** any change, so quiescence is
reversible from a record rather than from memory.

Restore **only** after Rung A has terminated AND the probe arm triple has been
cleared AND a subsequent probe preflight reports NOT_ARMED.

* project `arb-ev-wallet-shadow` `50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`
* environment `production` `f3642d27-26dc-4e21-9170-bc76290c076f`
* service `l5-pmus-live` `87f1834e-530e-4fc4-a310-91c8d3add196`

## Configuration to restore

| property | value |
|---|---|
| source repo | `unbuiltworks/kalshi-arb-bot` |
| source branch | `claude/gemini-btc-economic-viability-h3u7zl` |
| checkSuites | false |
| builder | `DOCKERFILE` |
| dockerfilePath | `deploy/l5_pmus_live/Dockerfile` |
| buildEnvironment | `V3` |
| startCommand | `python3 -m src.l5_pmus_live_runner` |
| **restartPolicyType** | **`ALWAYS`** |
| runtime | `V2` |
| useLegacyStacker | false |
| ipv6EgressEnabled | false |
| **multiRegionConfig** | **`us-east4-eqdc4a: numReplicas 1`** |
| volume mount | `3c08c916-dc42-4eb4-9592-3b25dc07958f` at `/state/l5-live` |

watchPatterns (13, verbatim):

```
deploy/l5_pmus_live/**
src/l5_pmus_live_runner.py
src/l5_pmus_auth_book.py
src/l5_pmus_transport.py
src/l5_live_execution.py
src/l5_faithful_translation.py
src/l5_execution_translations.py
src/l5_order_body.py
src/l5_preview_gate.py
src/pmus_auth.py
src/pmus_q1_executor.py
src/pmus_fees.py
src/pmus_ws_evidence_recorder.py
src/l5_pmus_realtime_observer.py
```

Variables present (values never read, never written by the quiescence):
`L5_LIVE_ROOT`, `L5_LIVE_SOAK_RECONCILE_S`, `PMUS_API_KEY_ID`,
`PMUS_API_SECRET_KEY`, `PMUS_Q1_ARM`, `PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`,
`PMUS_RESIDUAL_CAP_K`, `PMUS_SERVICE_ROLE`, `PYTHONUNBUFFERED`,
`RAILWAY_DOCKERFILE_PATH`.

## The quiescence act, and why this one

**`numReplicas: 1 -> 0` in `us-east4-eqdc4a`. Nothing else.**

Chosen because it is the smallest act that guarantees the property required —
no running process — while touching **no** strategy, no variable, no branch, no
Dockerfile, no start command, no volume and no restart policy. `restartPolicyType`
stays `ALWAYS`, which is why replicas and not the policy are what changes: at
zero replicas there is nothing for `ALWAYS` to restart.

Restoring is the same edit inverted: `numReplicas` back to `1`. Its credentials,
arm variables and state volume are untouched throughout, so the runner resumes
exactly the configuration above.

## Pre-quiescence evidence of what it was doing

Running at 2026-09-28T03:13:40Z on `cpc-btc-updown-15m-2026-09-28-0300z`,
authenticated WS book, minting client order IDs continuously. Every decision
refused: `"reason":"NOT_ARMED"`, `"submitted":false`, `"armed":false`. It was
**not** trading; it was disarmed and one variable away from being able to.
