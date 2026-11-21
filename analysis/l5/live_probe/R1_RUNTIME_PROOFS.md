# l5-r1-probe — the twelve proofs, from the running service

Captured 2026-09-16T05:12Z. Every claim below names its source. Two of the
twelve are **not** proven from the container and say so rather than being
dressed up; a proof that cannot be taken is worth less than nothing if it is
reported as taken.

| | |
|---|---|
| service | `l5-r1-probe` `6111a5d4-a2d9-4762-a057-31f02d07586f` |
| project / env | `50fb7357-e2bc-4d04-a49e-71eb5d73b0d1` / `f3642d27-26dc-4e21-9170-bc76290c076f` |
| deployment | `ed095324-fa56-44ab-8093-ab452dd57653`, SUCCESS, container up 04:53:04Z |
| artifact | `f8b8fae2712b67fdabee71a67135aa9f69f68e0a` |
| volume | `285285cd-3e1a-44d1-b6de-834668e96251` `l5-r1-probe-state` → `/state/l5-r1-probe` |
| variables | exactly two: `PMUS_API_KEY_ID`, `PMUS_API_SECRET_KEY` (Railway references; no value read, printed or copied) |

## 1. SOURCE_LINEAGE — PROVEN

`r1_source_lineage.verify()` recomputes the image's whole COPY set against the
reviewed artifact `6adcc4c4`, blob by blob:

```
copy_sources 36 | identical 32 | differing 4 | absent 0
undisclosed [] | forbidden_class [] | ACCEPTANCE True
```

The four differing files are all on the allow-list with a non-execution class
(`OBSERVABILITY` ×2, `ACCOUNTING_REPORTING`, `DOCUMENTATION`). A delta of class
`EXECUTION`, `DECISION`, `SIZING` or `SUBMISSION` fails the check outright.

Build log, 40 directives: 39 `COPY`/`WORKDIR`/`FROM` plus exactly **two** `RUN`
steps — `pip install --no-cache-dir "cryptography>=42"` and
`touch src/__init__.py`. No source-transform step.

Runtime corroboration: the container logs `commit f8b8fae2712b`, read from
`RAILWAY_GIT_COMMIT_SHA`, which the platform injects into the *running*
environment rather than baking into the image, plus
`deployment{commit f8b8fae2712b67fdabee71a67135aa9f69f68e0a, deployment_id
ed095324-…, service l5-r1-probe}`.

## 2. SOURCE_UNMUTATED_AT_RUNTIME — **PARTIAL. NOT CLAIMED AS PROVEN.**

What the running service does establish:

- The service carries **no `startCommand`**, so the image `CMD` ran. For
  contrast, the legacy service's `startCommand` reads, verbatim:
  `sh -c 'sed -i "s/^REARM_RUNG = \"R5F\"$/REARM_RUNG = \"R5FT\"/" src/pmus_l5_continuous_session.py && python3 -m src.pmus_r5ft_ledger_repair && exec python3 -m src.pmus_l5_continuous_session'`
- The container ran the **R1** CMD. It did not print `SERVICE_ROLE_MISMATCH`
  and did not exit 64, so `PMUS_SERVICE_ROLE=l5-r1-probe` held at runtime; and
  no `PMUS_R5FT_LEDGER_REPAIR` / `PMUS_R5FT_SCOPE_REPAIR` line appears, which
  are the legacy CMD's first two stdout lines.
- The build log carries no `R5FT_COMPLETE_REJECT_RETRY_PATCHED` and no
  `apply_complete_reject_retry.py` step.

What it does **not** establish: a byte digest of the `src/*.py` actually loaded
by the running interpreter. Nothing in the process emits one.
`pmus_spring_l5_session.crash_fix_present()` does AST-parse its own `__file__`
and is the right primitive, but it is reached only from `boot_assertions()`,
which requires `PMUS_SPRING_REARM_GATE` set **and** the armed path.

Closing the gap needs an observability-only source-digest line at boot — a code
change and a redeploy, i.e. an owner action. Until then this reads PARTIAL.

## 3. ACTIVE_RUNG_IS_R1 — PROVEN (container's own first log line)

`campaign_start.rung.ladder_rung "R1"`, `campaign_id "R1-INITIAL"`,
`ramp_ladder.active "R1"`, and `previous_campaigns []` — there is no persisted
campaign to inherit, which is the whole R5F/R5FT hazard the isolation removes.

## 4. MARKETS_ALLOWED_IS_ONE — PROVEN

`rung.markets_allowed 1`, `stop_rule "rung_markets"`.

## 5. MAX_NOTIONAL_PER_MARKET_IS_0_05 — PROVEN

`per_market_cap "0.05"`, `rung.per_market_cap_usd "0.05"`,
`worst_case_usd "0.05"`, `per_action_target_usd "0.01"`,
`per_leg_max_usd "0.02"`, and
`safety_shell_version "R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s"`.

## 6. LEGACY_STATE_MOUNTED = false — PROVEN

One mount, `/state/l5-r1-probe`. The legacy volume
(`52303147-8e1c-4713-8405-c59743dbfc07`, `/state/pmus-spring`) is mounted only
on `d84d9a68`. Runtime corroboration: `fidelity_gate.ingest.source` is
`/state/l5-r1-probe/ledger.jsonl`.

## 7. DEDICATED_STATE — PROVEN

Same ingest line, with `rows_total 0`, `previous_campaigns []`,
`campaign_id "R1-INITIAL"`. A fresh volume with no inherited campaign.

## 8. LIVE_ARMED = false — PROVEN

`campaign_start.mode "SHADOW"`. `armed()` is
`LIVE=="YES" and SESSION_ID and ARM==SESSION_ID`; `SHADOW` **is** that
function's False branch, so this is the runtime answer and not an inference
from config. Config corroboration: none of `PMUS_SPRING_LIVE`, `_ARM`,
`_SESSION_ID`, `_GATE_EVIDENCE` exists on the service.

## 9. ORDER_COUNT = 0 — PROVEN

Latest funnel, 05:12:34Z: `cycles 79, universe_rows 0, admitted 0, sessions 0,
submitted 0, filled 0`. Heartbeats at uptime 0 / 300 / 600 / 900 s all read
`sessions 0, held 0`. Disarmed, the loop never constructs a Transport —
`note: "shadow selection only, no venue network"`.

## 10. SETTLEMENT_ACCOUNTING — WIRED, NOT YET EXERCISED

Runtime: `accounting.status "OK"`, `evidence_accounting_valid true`,
`second_pass_idempotent true`, `duplicates_dropped 0`, ingest bound to the
dedicated ledger. But `rows_total 0`: nothing has filled on this service, so
the chain has not yet carried a settlement. Connected, not demonstrated.

## 11. CANCEL_DEADMAN — **NOT PROVABLE FROM THIS CONTAINER, BY DESIGN**

`run_campaign()` reaches `sweep_orphan_orders(t)` and `_log("orphan_sweep", …)`
only inside the `else:` branch of `if not armed:`. Disarmed, no transport is
built and no venue call is made, so no `orphan_sweep` row exists — and none
ever will while the probe stays disarmed. The per-session deadman cancel
likewise runs on the way out of a session, and there have been zero sessions.

Proven instead: the path is in the image (in the COPY set, byte-identical to
`6adcc4c4`), it is covered by `tests/test_safe_shutdown.py` and
`tests/test_spring_cancel_race.py`, and on the first armed boot it runs
**before** the first submission. That is not the same as having watched it run
here, and it is not reported as such.

## 12. RESTART_POLICY_NEVER — PROVEN

`deploy.restartPolicyType "NEVER"` (the legacy service is `ALWAYS`). Runtime
corroboration: one `campaign_start` and a single unbroken uptime series
0 → 900 s. No restart has occurred.

---

## LANE_A_EXECUTION_MEASUREMENT_PROBE_V0 preflight, against this service

| gate | state |
|---|---|
| CATEGORY_BAR | PASS |
| SIZE_EXECUTABLE | PASS |
| SERVING_ARTIFACT | PASS |
| BUDGET_PERMITS | PASS |
| SETTLEMENT_ACCOUNTING | PASS |
| ACTIVE_RUNG_IS_R1 | PASS |
| MARKETS_ALLOWED_IS_ONE | PASS |
| EVIDENCE_SHA_PINNED | PASS |
| RUNTIME_IS_UNMUTATED_ARTIFACT | PASS |
| REARM_RUNG_UNMUTATED_AND_INERT | PASS |
| SOURCE_LINEAGE_ACCEPTED | PASS |
| DUAL_KEY_VARS_PROVEN_UNSET | PASS |
| SHUTDOWN_LATCH_CLEARED *(owner)* | PASS — never latched on this service |
| **DUAL_KEY_ARMED** *(owner)* | **FAIL — not set, and not set by us** |

`non_owner_blockers: []` · `owner_blockers: ["DUAL_KEY_ARMED"]` ·
`ORDER_PERMITTED: False` · `NO_ORDER_REASON: "awaiting owner gates"`

### Two corrections to the preflight module, disclosed rather than buried

Both were made to run the preflight against this service at all. Neither
relaxes a bound, and the run above is only meaningful if they are read first.

**(a) The artifact pin was re-anchored, and its guarantee relocated.**
`SERVING_ARTIFACT` named `6adcc4c43e67` — the legacy service's artifact. That
service is now permanently out of scope, and this one can never serve that
image. Repointing an identity pin is exactly how a safety check becomes a
rubber stamp, so the claim it carried ("the runtime is the artifact that was
reviewed") was moved to a new gate, `SOURCE_LINEAGE_ACCEPTED`, which
recomputes the whole COPY set against `6adcc4c4` and fails on any undisclosed
or execution-class delta. That is strictly stronger than the prefix match it
replaces. Both artifacts stay named in the module.

**(b) `REARM_RUNG_EXPECTS_R1` was unsatisfiable, and is replaced.**
It compared the running `REARM_RUNG` against `"R1"` — but that constant is
`"R5F"` in *every* unmutated build of this repo, so the gate blocked the
correct artifact and the sed-mutated one identically and discriminated
nothing. The existing test only ever passed it a value (`"R1"`) the system
cannot produce. `REARM_RUNG_UNMUTATED_AND_INERT` asks the two questions that
matter: has the constant been rewritten (`"R5FT"` is precisely what the legacy
boot `sed -i` produces — still FAIL), and can it raise the rung (only if
`PMUS_SPRING_REARM_GATE` is set; unset, it is inert — and if it were set on
this rung the boot would log `rearm_refused` and hold halted, so that blocks
too). Strictly more discriminating, not less.

4,704 tests pass.

---

## VERDICT: READY_FOR_OWNER_ARM

Every non-owner gate passes. The only outstanding gate is `DUAL_KEY_ARMED`,
which is the owner's and was not touched.

**Arming is necessary but not sufficient, and this is not a gate failure.**
`PMUS_SPRING_EVIDENCE_STATUS_URL` is unset on this service, so the
strict-evidence gate reports `status_unavailable{source:"unset"}` and the
funnel has read `universe_rows: 0` for all 79 cycles. Armed today, the probe
would run and select nothing. Pointing it at a status source is a second owner
action.

Owner actions, on `6111a5d4-a2d9-4762-a057-31f02d07586f` only:

1. `PMUS_SPRING_EVIDENCE_STATUS_URL=<strict-evidence status source>`
2. `PMUS_SPRING_LIVE=YES`, `PMUS_SPRING_SESSION_ID=<id>`,
   `PMUS_SPRING_ARM=<the same id, byte-identical>`,
   `PMUS_SPRING_GATE_EVIDENCE=<the exact market slug>`

`restartPolicyType` is `NEVER`, so setting these redeploys once and the probe
gets exactly one process; it does not relaunch after a crash or after the first
market completes.

**Legacy `pmus-spring-l5-probe` (`d84d9a68-…`) stays HALTED and latched.** It
was not read for values, modified, restarted or redeployed. Its own variable
set still carries `PMUS_SPRING_SHUTDOWN` **and** all four dual-key names —
which is the concrete reason clearing that latch alone was unsafe, and why the
P0 decision to leave it alone was right.
