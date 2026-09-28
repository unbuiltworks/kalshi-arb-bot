# Execution-path blocker list for a bounded BTC15m capacity probe

Diagnosed read-only on 2026-09-28 at n=17. **Nothing was fixed and nothing was
deployed.** This is the list REPO_TESTS_001 requires to be resolved or proven
irrelevant before any order, with the reason each one matters to the probe.

## First, the thing that decides the classification

**There is no specified probe artifact yet.** EXP-010/EXP-011 established that
`pmus-q1-executor` is hard-capped at q=1/$1 and cannot express a ladder, that the
taker-completion primitive lives in `pmus_micro_executor`, and that live authority
is the immutable mandate plus `GuardedTransport` — but no module, image, commit and
mandate have been named as *the* probe.

Consequently **category 2 is empty.** "Proven irrelevant to the exact probe
artifact" is not a claim that can be made against an artifact that does not exist;
an argument that a failure *looks* unrelated is explicitly not sufficient. Category 2
can only be populated after the probe artifact is named and pinned.

## 1. Pre-existing and unrelated to the execution path

These are container/dependency defects, not code defects, and they do not touch
order safety.

| test | failure | why it is not a probe blocker |
|---|---|---|
| `test_fee_calc`, `test_gemini_collector_windows`, `test_gemini_position_semantics_probe`, `test_gemini_settlement_probe`, `test_spread_scanner` | `ModuleNotFoundError: dotenv` / `httpx` at import | collection-time only; no execution-path code involved |
| `test_gemini_terms_gate` (17), `test_gemini_settlement_observability` (2), `test_gemini_write_path_isolation` (1), `test_gemini_payload_schema` (1), `test_gemini_request_transport` (9 errors) | Gemini experiment path | a different venue and a different program; not in any PMUS order path |
| `test_orderbook` (5), `test_chain_ca_fallback` (1), `test_window_forensics_stream` (1) | unrelated subsystems | no live-order involvement |
| `test_deploy_fallthrough_guard` — the `dotenv` failure | `import src.main` fails for a missing dep | environment, not config |

**~37 of the 40 failures and 9 of the 23 errors sit here.**

## 2. Relevant, already demonstrated irrelevant to the exact probe artifact

**EMPTY.** See above. Nothing can enter this category until the probe artifact is
named, pinned, and shown not to execute the code under test.

## 3. GENUINE UNRESOLVED BLOCKERS — must be fixed or pinned before any order

### 3a. Two code paths can arm. The invariant says exactly one.
`test_l5_live_oneshot::test_only_the_oneshot_probe_arms_anything`

    assert all("l5_pmus_live_oneshot" in c for c in callers)
    AssertionError: ['src/l5_pmus_live_runner.py:591',
                     'scripts/l5_pmus_live_oneshot.py:179',
                     'scripts/l5_pmus_live_oneshot.py:535']

An AST sweep finds `t.arm()` called from `src/l5_pmus_live_runner.py:591` as well as
the one-shot probe. **Why it matters:** the whole point of a single arming site is
that one reviewed, bounded entry point decides when money can move. A second arming
call inside the continuous runner is the same *shape* of defect as the 2026-09-16
breach — a path that can start spending without the reviewed gate. This is a
first-order live-safety blocker regardless of which artifact the probe uses, because
both candidates live in that tree.

### 3b. The reviewed artifact and HEAD have drifted in execution-class files
`test_r1_source_lineage` (4) and `test_lane_a_probe_v0` (2)

    AssertionError: an undisclosed, execution-class or drifted delta is present
    ['analysis/l5/authorization/', 'src/pmus_micro_executor.py',
     'src/pmus_q1_executor.py']
    reviewed_artifact: 03f881ca  vs  head: HEAD
    'content diff ... reviewed blob' == 'no reviewed blob pinned'

**Why it matters most of all:** this is the mechanism that guarantees *the thing you
reviewed is the thing that will run*. It is currently red, and it names the two
executors and the authorization directory by path. Until it is green — or the probe
artifact is pinned to a reviewed blob and this check passes against that pin — there
is no basis for saying which code a live order would execute. Note this session's
own work moved HEAD further from `03f881ca` (including a correction inside
`analysis/l5/authorization/`), so some of the drift is mine and must be disclosed
rather than waived.

### 3c. The request-signing scheme is UNVERIFIED in this environment
`test_pmus_q1_executor::test_signed_headers_match_documented_scheme` and
`test_pmus_ws_evidence_recorder::test_auth_headers_match_documented_scheme`

    pyo3_runtime.PanicException: Python API call failed
    ModuleNotFoundError: No module named '_cffi_backend'
      (/usr/lib/python3/dist-packages/cryptography/exceptions.py)

`cryptography` cannot load its CFFI backend here, so both tests abort before
asserting anything. **This is an environment defect, not evidence of broken code** —
and that is exactly why it blocks. These are the only tests that check the
authenticated request signing against the documented scheme, so right now **the
signing path is untested, not verified.** A bounded probe sends authenticated
orders; sending the first one with its signing scheme unasserted is not acceptable.
The remedy is installing the dependency and running them, not editing code.

### 3d. Live-run status publishing is broken against its own module
`test_pmus_status_live_handoff` — **14 errors**

    AttributeError: module 'src.pmus_ws_evidence_recorder' has no attribute
    '_LIVE_STATUS'

`_LIVE_STATUS` existed in that module historically (touched by `c6686657`,
`3046d9b0`) and has since been removed while the tests still reference it. **Why it
matters:** this is the durable per-cycle status a live run writes as it happens, and
it is what makes an unattended money-moving session observable. The 2026-09-16 post
mortem put monitoring in the "detects and reports, never prevents" column — so this
is **not** a safety gate, and it should not be graded as one. It is a probe-quality
blocker: without it a probe can run and we cannot see what it did in flight. Fix or
consciously accept, but do not silently carry.

### 3e. One deployment-config finding, conditional on which service is used
`test_deploy_fallthrough_guard::…` — `l5_pmus_live: no railway.json (fall-through risk)`

**Why it matters:** a service directory with no `railway.json` can fall through to a
default start command, which is how a process ends up running something other than
what was intended. It blocks **only if `l5_pmus_live` is the probe service**; if the
probe is pinned elsewhere it becomes a category-2 candidate once that is
demonstrated. Listed here because the service is currently un-pinned.

## Summary

| # | blocker | class | blocks any artifact? |
|---|---|---|---|
| 3a | two arming call sites | **live-safety** | **yes** |
| 3b | reviewed-artifact lineage drift in execution files | **live-safety / identity** | **yes** |
| 3c | signing scheme unverified (missing `_cffi_backend`) | **verification gap** | **yes** |
| 3d | status publishing broken (14 errors) | probe quality, not safety | yes, for observability |
| 3e | `l5_pmus_live` has no `railway.json` | deployment config | only if that service is used |

**3a and 3b are the two that would stop me regardless of anything else**, because
each is the failure of a control designed to prevent exactly the 2026-09-16 class of
incident. 3c is not a code fault but is a hard gate: unverified signing plus a real
order is an untested first write.

Not fixed, not deployed, per instruction. The live-safety envelope itself remains
green and is *not* a substitute for these:
`test_live_mandate_{deployment,adversarial,ledger_invariant}` and
`test_pmus_capacity_rung_manifest` — 58 tests — bound what a spend may *do*; the
five above govern whether the executor behaves and whether we know what is running.
