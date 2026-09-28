# Activation manifest — successor build `de72ab80`

Status: **DISARMED. Not authorized to arm.**

## Build

| | |
|---|---|
| Service | `l5-live-probe-oneshot` (84aa9999-6d7a-4980-9a87-96694d8178dd), existing |
| Project / env | arb-ev-wallet-shadow / production (f3642d27) |
| Deployment | `2045ae4e-70a3-43c7-bbc7-f256129f5a02`, status SUCCESS |
| Commit built | `de72ab8045fbce8e4d44b381ff08e2b210e845cf` (pinned via commitSha) |
| Dockerfile | `deploy/l5_pmus_lock5_probe/Dockerfile`, 54 COPY steps |
| Image digest | `sha256:ac396be1fb3e96d96821b8679f6f190880c27548c8bd4c741973ee943050401f` |
| restartPolicyType | NEVER |
| Start command | `python3 -m scripts.l5_pmus_live_oneshot` |
| Builds performed | 1 |
| New services | 0 |

## Disarmed boot, 2026-09-28T21:38:25Z

```
L5_MODE {"mode": "LIVE_PROBE", "observe_only": "", "recover_only": "",
         "market_pin": "", "session": "", "armed": "", "live": ""}
L5_LIVE_PROBE {"LABEL": "LIVE_BOUNDED_PROBE",
               "market": "cpc-btc-updown-15m-2026-09-28-2130z",
               "refused": "OWNER_AUTHORIZATION_ABSENT",
               "detail": {"needs": ["PMUS_Q1_LIVE","PMUS_Q1_SESSION_ID",
                                    "PMUS_Q1_ARM"],
                          "note": "no order path is built without all three"},
               "armed": false, "submitted": 0,
               "note": "no transport capable of POST was constructed"}
```

Process exited; no restart (NEVER). No mandate installed at any point.

## The eight proofs

| # | claim | status | evidence |
|---|---|---|---|
| 1 | serving SHA is `de72ab80` | PROVEN | deployment `2045ae4e` commitHash, source pinned to that commit, one build |
| 2 | disarmed boot cannot POST | PROVEN ON THE ARTIFACT | boot log above |
| 3 | authenticated account binding succeeds | **NOT PROVEN** | see below |
| 4 | market metadata resolves, execution grid known | **NOT PROVEN** | see below |
| 5 | REST book failure non-blocking; order-time WS freshness mandatory | PROVEN BY TEST | `test_rest_book_failure_does_not_kill_the_window`, `test_rest_book_is_called_by_nothing_in_the_packaged_artifact` (AST over the Dockerfile's own COPY list) |
| 6 | positions GET not performed with no consumer | PROVEN BY TEST | `test_positions_is_not_read_when_there_is_nothing_to_reconcile`; `test_preflight_caches_nothing_...` asserts `/v1/portfolio/positions == 0`, `/v1/orders/open == 3` |
| 7 | open-orders reconciliation still blocks | PROVEN BY TEST | `test_unexplained_open_order_blocks`, `test_open_orders_read_failure_blocks`, `test_position_drift_blocks`, `test_positions_read_failure_blocks_when_reconciliation_is_asked_for` |
| 8 | stdout exposes the exact failing check | PROVEN BY TEST, NOT YET ON THE ARTIFACT | `test_every_blocking_failure_prints_its_cause` (parametrized over 3 gates), `test_no_credential_value_appears_in_the_printed_cause`, `test_rate_limiting_is_distinguishable_from_rejection` |

19/19 in `tests/test_preflight_gate_isolation.py`; full suite shows no new
failures against the pre-change baseline.

## Why 3 and 4 cannot be proven from a disarmed boot

`run()` refuses at step 1 on the owner arm triple, before the mandate check
and before `run_preflight()`. `observe()` holds `EX.NullTransport` and runs
no preflight. `recover()` runs no preflight and short-circuits on clean
state. `scripts/pmus_auth_preflight_readonly.py`, which would answer both
directly, is not in this Dockerfile's COPY list.

So the artifact has exactly one path to `preflight()`, and it requires the
authorization triple. That is itself the safety property asked for in 2 --
an unauthorized boot cannot even reach an authenticated read -- but it
means 3 and 4 are first established at the preflight of an authorized run.
Both are now *visible* when they fail, which is the whole point of this
build: a failure of either prints `PMUS_PREFLIGHT_FAIL` naming the check,
endpoint/host, exception class and HTTP status, and `L5_LIVE_PROBE` carries
`preflight_failing_check`.

Closing that gap costs one of: a code change plus a second build (packaging
the read-only preflight script), or accepting 3 and 4 as first-minute
evidence of the authorized run. Neither is taken here.

## Not requested, not done

No arm. No mandate. No market pin. No policy change: theta 0.96, LOCK5 $5,
variant B, hold 60 s, residual_cap_k 2, clip default 50 (`L5_NATIVE_CLIP`
currently 10), completion economics and the frozen n=25 evidence are
untouched; parity remains 25/25 at 6.1167. No Dockerfile or unrelated test
cleanup (the stale `deploy/l5_live_probe` closure test still fails, as it
did on `dc9fd488`).
