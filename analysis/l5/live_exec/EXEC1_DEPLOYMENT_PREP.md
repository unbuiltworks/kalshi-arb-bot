# LIVE-L5-EXEC-1 — DEPLOYMENT PREPARATION, DISARMED

Status: **DEPLOYED AND DISARMED. NO ORDER. NO SIGNING. NO VENUE MUTATION.**
Attempt 2 was NOT run. matched-pair economics remain **N/A**.

Nothing in the frozen artifact changed: fingerprint
`b1dbe7f1e628e6f9fc8e6459a2a5b95098ada2f16ae872f10081d5dc9bb82e06`,
and strategy behaviour, risk envelope, quantity and every execution rule are
byte-identical. The only new code is a read-only boot preflight and its image.

## 1. The deployed artifact

| | |
|---|---|
| Service | `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`) |
| Project / env | `arb-ev-wallet-shadow` / `production` |
| Repo | `unbuiltworks/kalshi-arb-bot` |
| Branch | `claude/august-l5-forensic-replay-788cml` |
| **DEPLOYED SHA** | **`882aa5647d4d67a4f873a92b455c74f9bd15d807`** (pinned, not branch-following) |
| Dockerfile | `deploy/l5_exec1_preflight/Dockerfile` |
| Start command | `python3 -m scripts.l5_exec1_boot_preflight` |
| Deployment | `163cf6a9-419a-4de0-adb4-1997da0c209e`, SUCCESS, 2026-10-06T14:56Z |
| restartPolicyType | **NEVER** |

### Prior configuration, recorded so it can be restored

Branch `claude/l5-pmus-economic-results-7c116e`, commit
`6eedbefb457bc46e8718f2669046268288d315f2`, Dockerfile
`deploy/l5_pmus_lock5_probe/Dockerfile`, start command
`python3 -m scripts.pmus_q1_settlement_audit`, restart NEVER, volume
`l5-live-probe-state` at `/state/pmus-ws` (untouched, still mounted).
`RAILWAY_DOCKERFILE_PATH` was repointed to the new Dockerfile.

## 2. MUTATION-INCAPABLE, which is stronger than DISARMED

The image omits `l5_pmus_transport`, `pmus_q1_executor` and
`pmus_micro_executor` — the three modules that own the signed POST path — and
a **blocking gate fails if any of them is importable**. Its only HTTP object
is `pmus_auth.ReadOnlyTransport`, which raises `PermissionError` on any method
but GET; the preflight proves that at boot by attempting `POST /v1/orders`.

Consequence, stated plainly: **this deployment cannot submit an order even if
the four arming keys were set.** Attempt 2 needs a separate, separately
authorized image that adds the order path. It cannot be produced by setting
an environment variable on this one.

## 3. Boot proof from the host — 22/22 gates PASS, 0 blocking failures

```
exec1_fingerprint                  PASS  b1dbe7f1…2e06
sibling_fingerprint_s3             PASS  8b20a259…87df
sibling_fingerprint_reserved_micro PASS  0522b0da…9d1b31
mutation_incapable                 PASS  no order-capable module in this image
transport_refuses_post             PASS  PermissionError on POST /v1/orders
exec1_arm_keys_absent              PASS  all four PMUS_EXEC1_* = False
exec1_disarmed                     PASS  exec1_armed()=False
may_submit_refuses                 PASS  DISARMED: owner has not set the four keys
foreign_arming_cannot_arm_exec1    PASS  PMUS_Q1_/MICRO_/CANARY_/CASE1_/SCHEMA_PROBE_
                                         all set to armed values -> exec1_armed()=False
q1_arming_does_not_arm_exec1       PASS  pmus_auth.armed()=False, exec1_armed()=False
one_order_latch                    PASS  second request raised StateError
pmus_credentials_present           PASS  PRESENCE ONLY; values never read or printed
slug_is_btc15m_series              PASS  cpc-btc-updown-15m-2026-10-06-1445z
market_readable                    PASS  same slug, from the venue
minimum_trade_qty                  PASS  minimumTradeQty=0.01
tick_from_venue_field              PASS  orderPriceMinTickSize=0.01 (per market)
validate_size_fail_closed          PASS  quantity_to_use=0.01
authbook_connected                 PASS  8 consecutive two-sided snapshots
authbook_freshness_2.0s            PASS  8/8 within 2.0s
observed_spread_on_tick_grid       PASS  ['0.0100']
no_other_order_capable_process     PASS  none on this host
restart_policy (non-blocking)      PASS  NEVER, per the control plane
VERDICT: DISARMED / DEPLOY-READY / NO ORDER SUBMITTED
```

AuthBook ages, from the host: 0.010, 0.073, 0.040, 0.106, 0.073, 0.015,
0.020, 0.193 s — every one inside the reused
`l5_preview_gate.BOOK_MAX_AGE_S = 2.0` bound. BBO moved 0.98/0.99 →
0.97/0.98 → 0.98/0.99 across the eight, so these are live frames and not one
cached snapshot repeated.

## 4. Contamination state

* No order-capable process on this host (token-accurate scan: a needle must
  be a real invocation target, `-m <module>` or a `<name>.py` path, so a
  command line that merely mentions a module name no longer false-positives).
* `pmus-q1-executor` (a separate service) has **zero environment variables** —
  no `PMUS_API_KEY_ID`, no `PMUS_API_SECRET_KEY`. It cannot sign, so it cannot
  submit, whatever its process state. Its last deployment is 2026-09-06,
  restart NEVER.
* `PMUS_Q1_*` and `PMUS_CANARY_*`/`PMUS_CASE1_*` variables DO exist on
  `l5-live-probe-oneshot`. Their values were deliberately not read. They are
  irrelevant to EXEC-1 by construction, and the boot gate proves it by
  setting every foreign namespace to armed values in-process and showing
  `exec1_armed()` stays False.

## 5. Two limitations, not papered over

1. **The one-order latch is PROCESS-SCOPED.** `OneOrderOnly` lives in memory.
   Within one process a second order is refused, including a loss-repair
   order. Across a process restart it would not be, so the lifetime guarantee
   rests on `restartPolicyType = NEVER` plus `no_auto_repeat` — both verified,
   neither part of the latch itself. A durable latch on the mounted volume
   would close that gap. It is an execution-path change, so it is the owner's
   call, and it is **not** made here.
2. **Attempt 2 requires a different image** (§2). The order path is absent.

## 6. Exactly what the owner must set to arm — four keys, non-secret values

Claude did not and will not set these.

| Key | Required value |
|---|---|
| `PMUS_EXEC1_LIVE` | `YES` |
| `PMUS_EXEC1_SESSION_ID` | any session id you choose, e.g. `exec1-2026-10-06-a` |
| `PMUS_EXEC1_ARM` | **byte-identical to `PMUS_EXEC1_SESSION_ID`** |
| `PMUS_EXEC1_GATE_EVIDENCE` | the exact market slug, e.g. `cpc-btc-updown-15m-2026-10-06-1515z` |

Leave `PMUS_MICRO_*` unset and `PMUS_Q1_*` as they are: EXEC-1 reads neither,
and keeping the micro keys unset preserves clean attribution of a fill.
