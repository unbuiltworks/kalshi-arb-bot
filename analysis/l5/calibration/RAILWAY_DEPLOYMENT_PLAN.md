# Schema probe → `l5-live-probe-oneshot`: deployment plan. **STOPPED, NO MUTATION.**

All Railway reads below are read-only (`list-projects`, `list-services`,
`get-service-config`). **Nothing was changed. No order was submitted.**

## STOP REASON: item 3 cannot be specified — the probe has no entrypoint

`src/pmus_private_schema_probe.py` at `9c1ab8f5` is a **library**, not a runnable
program. It contains `armed`, `probe_price`, `stamp`, `FrameLedger`, `preflight`,
`build_body`, `classify_outcome` -- and **no `main()`, no `__main__` block, and
zero websocket, submission, cancellation or sweep code** (grep for
`websockets|/v1/orders|cancel_exact|subscribe` returns 0 matches).

So there is no module a `startCommand` could invoke. The instruction was to stop
before the Railway mutation if any deployment detail is uncertain; this one is not
uncertain, it is absent. My previous message said the probe was "built", which
overstated it: the guards and the price rule are built and tested, the orchestration
is not.

## Verified state of the service (independent of the above)

| # | item | finding |
|---|---|---|
| 1 | `9c1ab8f5` reachable by Railway | **YES** — it is pushed to `claude/l5-pmus-economic-results-7c116e`, and the service already builds from that exact repo and branch |
| 2 | branch / SHA Railway builds now | branch `claude/l5-pmus-economic-results-7c116e`, pinned `commitSha 768e5750e254293714389220503958bfa80a02d2` |
| 3 | exact start command | **BLOCKED** — current is `python3 -m scripts.pmus_q1_settlement_audit`; no probe entrypoint exists to replace it with |
| 4 | four arming variables | `PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` already exist on the service; **`PMUS_SCHEMA_PROBE_ARM` does not and would have to be added**. Values: `PMUS_Q1_LIVE=YES` and the three ids all set to one fresh session id — no secret is involved and none would be printed |
| 5 | PMUS credentials untouched | **YES** — `PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` are present as names on the service. Values were never read, are not needed, and no variable mutation would touch them |
| 6 | restart policy | already `restartPolicyType: NEVER` — **no change needed** |
| 7 | no unrelated service modified | only `l5-live-probe-oneshot` (`84aa9999-...`) would change. The recorder is a **separate service** (`pmus-ws-evidence-recorder`, `71b6ea99-...`) on a **different branch** (`feature/l5-transfer-backtest`), `restartPolicyType: ALWAYS`, and — checked specifically — a **different volume** (`18bd5702-...` vs the one-shot's `156032e0-...`), so there is no single-attach contention and the healthy recorder is not disturbed |
| 8 | rollback | restore `commitSha 768e5750e254293714389220503958bfa80a02d2` and `startCommand python3 -m scripts.pmus_q1_settlement_audit`; delete `PMUS_SCHEMA_PROBE_ARM`; clear the three arming ids. Builder, Dockerfile path, restart policy and volume mount would be unchanged, so rollback touches exactly the fields the deploy touched |

Project `arb-ev-wallet-shadow` (`50fb7357-...`), environment `production`
(`f3642d27-...`), builder `DOCKERFILE`, dockerfile
`deploy/l5_pmus_lock5_probe/Dockerfile`, volume `156032e0-...` at `/state/pmus-ws`.

## A second blocker found while checking the image

The image's COPY list is, by its own header, "the recomputed AST import closure of
the entrypoint". It already copies `src/l5_order_body.py`,
`src/pmus_q1_executor.py`, `src/pmus_q1_canary.py` and — usefully —
`src/pmus_ws_lifecycle_observer.py`. It does **not** copy
`src/pmus_private_schema_probe.py`, so the module would be absent from the image
even if an entrypoint existed.

Worth flagging honestly: `tests/test_l5_live_probe_image_closure.py` is one of the
**22 pre-existing baseline failures**, so the usual automated guard on this
Dockerfile's closure is currently not trustworthy and the COPY list would need
checking by hand.

## What has to exist before this plan can be completed

1. A real entrypoint (`__main__`) that: subscribes both sockets and proves each is
   receiving; runs `preflight`; submits exactly one order via `build_body`;
   persists every private frame verbatim; proves REST `OPEN`; cancels on evidence
   or at `OBSERVE_MAX_S`; proves terminal cancellation and zero open orders; and
   halts immediately on an unexpected fill.
2. That entrypoint added to the Dockerfile COPY closure, verified by hand given
   the broken closure test.
3. Then, and only then, the three-field service mutation in item 8's inverse.

## Unchanged constraints

q=1, exactly $0.01 post-only GTC, >= $0.20 below a fresh touch, both sockets live
before submission, maximum exposure $0.01, no repricing/retry/chasing, cancel on
evidence or 120s, prove terminal cancellation and zero open orders, unexpected fill
= probe failure and immediate stop. Schema observation only: no V3 trading, no
replay economics, no Defect-B change, no 1715z.
