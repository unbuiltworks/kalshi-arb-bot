# Execution-class review — candidate `8039c416` against REVIEWED `04cf5295`

Reviewed 2026-09-28. Verdict: **ADVANCE the reviewed baseline to `8039c416`.**
Predecessor `04cf5295` is retained in `REVIEWED_CHAIN`; `PREVIOUS_REVIEWED`
stays pinned at `03f881ca` deliberately, because the lineage mechanism's own
self-tests need a baseline that still differs from `PRIOR_DELTAS`.

This review is of the **actual reachable packaged artifact**, not of the
repository at HEAD. Reachability is defined by the AST import closure of the
packaged entrypoint `scripts/l5_pmus_live_oneshot.py`: **43 files**
(41 `src/`, 1 `scripts/`, plus `src/__init__.py`). The earlier figure of 42
excluded `src/__init__.py`, which `from src import X` also pulls in; it is in
the closure and in the image.

## 1. The diff, classified

`git diff 04cf5295..8039c416 -- src scripts` — 7 files, +627 / −2.

| file | in closure | class | disposition |
|---|---|---|---|
| `scripts/l5_pmus_live_oneshot.py` | YES | EXECUTION, SUBMISSION | admitted on evidence (§2, §3) |
| `src/pmus_lock5_translation.py` (new) | YES | DECISION (consumer only), SUBMISSION | admitted on evidence (§2) |
| `src/pmus_arm_retry.py` (new) | YES | EXECUTION (pre-arm gating) | admitted on evidence (§4) |
| `src/pmus_probe_observability.py` (new) | YES | non-forbidden (recording) | admitted |
| `src/pmus_ws_evidence_recorder.py` | YES | non-forbidden (read-only status) | admitted |
| `scripts/audit_economic_complete.py` | no | research | out of closure; cannot affect the probe |
| `scripts/pmus_window_ledger.py` | no | research | out of closure; cannot affect the probe |

Blob SHAs at `8039c416` for the five in-closure files:

```
scripts/l5_pmus_live_oneshot.py   531aec90938b9d602403dab7c84eba333e7153ab
src/pmus_arm_retry.py             a40d5a95d49d2d99b47cab4ded61221ac7fafd48
src/pmus_lock5_translation.py     75517fc192d358ee44a5139a4512d5ee5a0472df
src/pmus_probe_observability.py   6fea3b6a236f8c84c5a88065ba0755e03543dabd
src/pmus_ws_evidence_recorder.py  c3fa837af33497316b0eda3199e23dedf9328164
```

`FORBIDDEN_CLASSES` is **unchanged** and still contains EXECUTION, DECISION,
SIZING, SUBMISSION. Three files fall in forbidden classes and were admitted by
producing the evidence below — not by relabelling them and not by weakening the
gate. That distinction is the whole value of the gate.

## 2. The entrypoint composition fix (BLOCKER 1)

Candidate `5deaf006` defined `Lock5Session` and had 39 green unit tests, every
one of which constructed the session **directly**. The packaged entrypoint never
imported it. The tests proved the translation was correct and proved nothing at
all about the artifact that would have traded.

`8039c416` composes it on the entrypoint (`build_lock5`, `lock5_may_open_risk`,
`lock5_complete`, and `self.lock5` / `self.monitor` in `__init__`), and the
proof is structural rather than behavioural:

- `tests/test_lock5_entrypoint_composition.py` (20 tests) asserts the modules
  appear in the **recomputed closure of the entrypoint**, not in an import list
  someone wrote by hand.
- It carries **negative controls**: disconnecting each required component in
  turn makes the composition test fail. A composition test with no negative
  control cannot distinguish "wired" from "the assertion is vacuous", which is
  precisely how BLOCKER 1 survived the first review.

The Dockerfile COPY list was regenerated from that recomputed closure and is
**byte-identical to it** (verified by set comparison, 43/43, no manual
additions). A manual addition is how an unreachable component gets concealed.

## 3. RAMP_LADDER_001 absent from the capability graph (BLOCKER 2)

Regression rationale, preserved: on 2026-09-16 the R1 canary was authorized for
one market at $0.05 and committed **$19.3295 across eleven**, because
`pmus_l5_ramp_ladder.active()` auto-loaded three ten-day-old owner rungs and
silently replaced the live limits — per-market $0.05 → $7.50, daily stop
−$1.00 → −$15.00. A second, dynamic, rung-based sizing authority *is* that
incident.

`pmus_micro_executor` defines `per_market_first_leg_cap()`, which lazily imports
that ladder. The translation therefore does **not** import `pmus_micro_executor`
at all; it calls `l5_order_body.build` directly. That costs nothing in fidelity:
the micro module's `_body` is a one-line delegation to the same function, proven
byte-identical across 9 prices × 8 quantities × 2 sides × `post_only` by
`tests/test_r1_body_delegation_equivalence.py`. Completion economics
(`taker_completion_ok`, `taker_fee`) come from `pmus_q1_executor`, which has no
ladder dependency. `pmus_micro_executor` itself is **unmodified**, so every
other consumer is unaffected.

Verified at this commit: `pmus_l5_ramp_ladder` and `pmus_micro_executor` are
both **absent** from the 43-file closure (grep count 0). The structural negative
test fails if either, the ladder's `active()` state, or another dynamic/rung-based
cap mechanism becomes reachable. Omitting a file from a Dockerfile is not a
proof — reachability is a property of the import graph, not of a COPY list — so
the guard is on the graph.

The artifact therefore has **exactly one risk/sizing authority**: the reviewed
mandate / GuardedTransport boundary.

## 4. Arm retry

`src/pmus_arm_retry.py` contains **no** `.arm()` call (pinned by test); it only
gates. `TRANSIENT` is a whitelist of three conditions
(`orders_uncertain`, `unreconciled`, `book_unconfirmed`), deadline 45 s.
`src/l5_pmus_live_runner.py` had `try_arm()` and both call sites removed and
permanently cannot arm: `scripts/l5_pmus_live_oneshot.py` is the sole arming
owner. The single-arm-owner test was re-run after the retry work and still
passes, so retry did not recreate a second arming capability.

## 5. Test evidence

- 294 passed across the 16 focused probe-path suites (composition, translation,
  observability, arm-retry, lineage, body-delegation equivalence, q1 delta
  conservatism, mandate adversarial / deployment / ledger-invariant /
  concurrency-lifecycle, transport, shadow-net identity, ledger order
  invariance, distribution, tape drain).
- **REPO_TESTS_001 resolved**: all 249 test files that transitively reach a
  probe-closure module run **4590 passed, 8 skipped, 1 xfailed, 0 failed**.
- The 9 failing repository test files (`test_gemini_terms_gate`,
  `test_orderbook`, `test_gemini_settlement_observability`,
  `test_gemini_write_path_isolation`, `test_gemini_payload_schema`,
  `test_chain_ca_fallback`, `test_window_forensics_stream`,
  `test_deploy_fallthrough_guard`, `test_gemini_request_transport`) are each
  **transitively disjoint** from the probe closure — no module they import,
  directly or through any depth of import, is in the artifact. They were not
  fixed, and they are not blockers for this artifact. They remain repository
  debt.
