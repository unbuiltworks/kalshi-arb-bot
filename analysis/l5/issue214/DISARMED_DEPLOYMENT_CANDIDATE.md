# Disarmed deployment candidate — the real Railway integration, the two halts, and three defects

**Status: NOT DEPLOYED. NOT MERGED. NOTHING ARMED. NO VARIABLE CHANGED.**

Issue #206's freeze is intact and was verified against the running service
during this work. The owner has approved *the concept* of a disarmed
validation deployment; this document is the approval request for a specific
SHA, scope and configuration, not a record of one performed.

---

## 1. What production is running RIGHT NOW

Read read-only through the new adapter against the live API at 14:12:19Z.

| | |
|---|---|
| deployment | `41defbb9-19e1-46b4-8ea8-b3b912e0987c` SUCCESS |
| commit | **`68553c7cb874`** |
| created | 2026-11-09T07:52:23Z, by the owner's account, reason `deploy` |
| branch tracked | `integration/l5-safe-release-candidate-20261109` |
| heartbeat market | `cpc-btc-updown-15m-2026-11-09-1400z` |

### The safety configuration, as it actually stands

| variable / field | value | what it means |
|---|---|---|
| `L5_LIVE_HALT` | **`1`** | operator halt ENGAGED |
| `L5_SHADOW_ONLY` | **`0`** | the structural submission lock is **OPEN** |
| `submission_gate.reason` | `NOT_ARMED` | |
| `submission_gate.operator_halt` | **ABSENT** | the deployed reporter has no such field |
| `halt_latch` | `CLEAR` | the independent watchdog latch is not asserted |
| `L5_CAMPAIGN_APPROVAL` | nonce `probe-depth2-20261109-0830z` | **expired** |
| `L5_LIVE_AUTH_NOT_AFTER` | `2026-11-09T09:00:00+00:00` | **expired ~5h** |
| `L5_TS_COMPLETION_CROSS` | `NO` | taker crossing off |
| `L5_PROVISIONAL_FORCED_SIDE` | unset | as instructed |

Arming therefore fails closed **three** ways: the operator halt, the expired
campaign approval, and the expired absolute deadline.

### Two things the owner should know about that table

**(a) Defence in depth is currently one deep.** `L5_SHADOW_ONLY` is `0`, so
the structural lock — normally the first and least defeasible gate — is not
holding. What is holding is `L5_LIVE_HALT=1` plus two expiries. Restoring
`L5_SHADOW_ONLY=1` would make the line independent of the halt. **This is a
Railway variable change and I have not made it**; it is an owner action
under the freeze.

**(b) The deployed gate reporter cannot see the halt it is subject to.**
Production's own heartbeat shows `submission_gate.reason: NOT_ARMED` while
`halt_local_env: true`. There is no `operator_halt` field in that verdict at
all. This is the defect fixed below, observed live.

### Whether a push deploys

The service tracks this branch. Three commits (`2c7c20b7f`, `0ba6fca23`,
`6507160b5`) have been on the branch tip since ~07:5xZ and production has
**not** moved to them, so a push alone has not deployed. Every deployment in
the retained list was created by the owner's account. **But the branch is
tracked**, so a redeploy or a Railway variable change may pick up the branch
*tip* rather than `68553c7cb`. After the push described below, a variable
change would therefore carry these commits into production. Flagged rather
than assumed either way.

---

## 2. The two halts, proven consistent and not automatically clearable

The owner asked for proof that `L5_LIVE_HALT` and the independent watchdog
halt are checked consistently, and that neither may be cleared
automatically. `tests/test_l5_two_halts_consistency.py`, 64 tests.

They are **not interchangeable** and both exist for a reason:

* **`L5_LIVE_HALT`** — the operator halt. An environment variable, local to
  the executor, settable only with deploy credentials. Read in exactly one
  expression, `l5_pmus_live_runner.operator_halted()`.
* **the durable latch** — the independent halt. An object a separate
  watchdog PUTs; it outlives our process, our crash and our redeploy. Read
  through `l5_halt_gate.HaltGate`, whose default answer is NO.

### Consistency

* Four readers share the one expression, pinned by test: `operator_halted()`,
  `Runner.halted()`, the authorization preflight's `local_operator_halt`
  row, and now `Runner.submission_gate()`. **Any** non-empty value halts,
  including `"0"` and `"false"` — so an operator who types `L5_LIVE_HALT=0`
  meaning "off" has engaged it, and a second reader parsing it as a boolean
  would disagree with the first and run.
* Either halt **alone** blocks, with every other gate open; both engaged
  still blocks; neither engaged is the only way through. Separate keys, so
  no reader can conflate them.
* Both are evaluated **before** `OWNER_ARM_TOKEN` and `TRANSPORT_ARMED` in
  `GATE_ORDER`, so a halted process reports the halt rather than `NOT_ARMED`.
* The durable gate fails closed nine ways, each parametrized: absent,
  unparseable, unreachable, explicitly halted, missing `halt` key, a *string*
  `"false"`, stale, unparseable time, naive time — plus future-dated. The
  only path to permitted is a fresh, positive, parseable "no halt".

### No automatic clear

* `HaltAsserter` has no delete and no clear; its interface (`ObjectStore`)
  offers only `get` and `put`, so it cannot express a delete.
* The watchdog cannot attest "no halt" over its own latch, nor over a state
  it could not establish — an unreadable record *may* be a latch, and
  writing over it would clear it. It may do so only over a **positive**
  absence.
* The launcher writes `L5_LIVE_HALT` only on an explicitly requested reset
  carrying a reason **and** all eight `HALT_RESET_PRECONDITIONS` verified
  from the runtime's own telemetry. Each precondition is individually
  load-bearing (parametrized); a *missing* one is not a satisfied one.
* `run_launch` may carry an owner's reset to `apply_plan`; it may not
  originate one. Pinned: the parameters default to `None`, the only
  assignment is the pass-through, and no precondition literal is synthesised.
* `L5_LIVE_HALT` is in `FORBIDDEN`, is not in `WRITABLE`, and the Railway
  client refuses it under the launcher's allowlist.

---

## 3. Three defects found and fixed

### 3.1 The effective-gate reporter could not see the operator halt

`Runner.submission_gate()` — the authoritative "can an order reach the venue
right now, and which gate says no" reader, consumed by the intent chain, the
heartbeat and the preflight — **did not consult `L5_LIVE_HALT` at all**. It
could report `blocked: false, reason: OPEN` with the halt engaged.

The halt does refuse a submission by other routes (`OPERATOR_HALT` in the
intent chain; the tick deadman cancels and disarms). The defect is that the
*reporter* disagreed with the refusal — and that is precisely how the
08:30Z window was lost: the halt was in the heartbeat the whole time and the
blockers I read off this gate did not name it. Production's current
heartbeat still shows the gap (§1b).

Fixed: `operator_halt` is now a first-class key, read through the shared
expression, and takes precedence over the structural lock in `reason`.

### 3.2 `apply_plan` trusted variables the approved digest did not cover

The digest the owner approves covers `prepared["document"]` and nothing
else. `apply_plan` then published `prepared["variables"]` — a *separate*
mapping. A plan whose `variables` were edited after the digest was taken
passed the digest check and published the edit. And `L5_LIVE_HALT` was
**unconditionally exempt** from both the forbidden and the writable check,
so an injected empty value would have cleared the halt under a valid
approval with none of the preconditions.

Fixed structurally rather than by another check: `variables_for(document)`
derives every published value — including the market slug, from `series` +
`window_starts[0]` — from the hashed bytes. `apply_plan` recomputes the
whole mapping and refuses `PLAN_VARIABLES_DISAGREE_WITH_DOCUMENT` if the
carried copy differs. The halt's exemption is now gated on an authorized
reset in the same call.

### 3.3 `shutdown_plan` read an absent order count as zero

`_get` returns `None` for absent, and the conditions read
`live not in (0, None)`. So a heartbeat that did not report `order_progress`
produced **no cancellation action** and `outstanding_clear: True`. The
orders a reporter cannot see are exactly the ones nobody would then cancel.

Fixed: absence is UNKNOWN, unknown is not clear, `counts_unreadable` names
which, and the cancellation sweep runs anyway — cancelling by exact owned id
is safe when there is nothing to cancel, and skipping it is not. Added
`reconciliation_clear` as its own key.

---

## 4. The real Railway integration

`src/l5_railway.py` — the adapter the launcher was missing. It prepares,
applies, reads back, validates the deployment, verifies arming and shuts
down, rather than returning a plan describing those actions.

* **Verified against live production, read-only**: scope, deployment,
  heartbeat, runtime config, taker reachability, 92 variables.
* `Project-Access-Token`, not `Authorization: Bearer`, which returns
  "Project Token not found". The token is scoped to one project and one
  environment, and `verify_scope()` checks rather than assumes it.
* `curl` is the default transport with the reason documented: `urllib` gets
  403 through the agent proxy, `curl` returns 200. The transport is injected
  for tests.
* Terminal deployment statuses are **not** retried; `expect_sha` is checked
  on SUCCESS; `not_before` refuses a *stale* SUCCESS, so a write that never
  triggered a rebuild cannot look like a completed one.
* `newest_labelled` requires the label at the **start** of the line — a line
  merely *mentioning* it comes back from a substring filter.
* The write surface is closed twice: read-only by construction, and an
  allowlist checked again at the transport. One forbidden name refuses the
  **whole** batch.

---

## 5. Tests

| file | tests | what it covers |
|---|--:|---|
| `test_l5_two_halts_consistency.py` | 64 | §2 and §3.1–3.2 |
| `test_l5_launch_orchestrator.py` | 73 | the launcher's phases |
| `test_l5_launch_end_to_end.py` | 29 | the sequence against a FakeRailway that *changes* when written to |
| `test_l5_railway_adapter.py` | 35 | request construction and error handling against a stubbed transport |
| `test_l5_launch_venue_end_to_end.py` | 23 | §6 — the launcher's report derived from a mocked venue |
| `test_l5_release_candidate_lifecycle.py` | 29 | the order lifecycle through the real execution path |

---

## 6. End-to-end against a mocked venue

`test_l5_launch_venue_end_to_end.py` closes the join the other two files
leave open. The launcher's end-to-end file hands `execution_report` rows
written by hand, which proves the arithmetic and nothing about where the
rows come from. The lifecycle file drives the real execution path and never
looks at the launcher.

Here **every** order row is derived from the scripted venue's own record of
what was sent, acknowledged, filled and cancelled, and from the durable
store. A report that counts a fill the venue never gave is a failing test
rather than a plausible number — pinned directly: deleting the
acknowledgement from the venue's record must move the launcher's count.

Covered: two resting quotes at the deployed cap of 2; a refused third leg
counted as a decision and not an order; one leg filled and the complement
resting (unmatched residual); both filled (matched pair, no residual); a
partial fill reported as partial; cancellations counted from the venue's own
record at the real TTL sweep; an unanswered POST that is neither an
acknowledged order nor nothing; restart mid-window with no double count and
no duplicate client order id; and that a fill is **never** reported as
settled money.

`win2(armed=False)` is deliberately **not** used: the harness carries
`Venue.armed` but its `submit` does not consult it, a mistake already made
and corrected once in the lifecycle file. The real gate is upstream.

---

## 7. What is being asked for

1. **Approval of the SHA** named in the pull request for a **disarmed**
   validation deployment.
2. A decision on **`L5_SHADOW_ONLY`**: it is `0` today and I recommend `1`.
   Mine to recommend, yours to set.
3. Acknowledgement that, the branch being tracked, a Railway variable change
   after the push may carry these commits into production.

## 8. What is explicitly NOT being asked for and has NOT been done

* No merge. PR #213's base conflict is reported in the PR, unresolved.
* No deployment, no redeploy, no variable change.
* `L5_LIVE_HALT` not cleared, not reset, not written. Its origin is still
  unestablished — nothing in `src/` or `scripts/` writes it, and the exact
  moment needs Railway's variable history, which the MCP surface does not
  expose.
* The shadow lock not cleared by me. The executor not armed. No live order.
* No new campaign approval. `L5_CAMPAIGN_APPROVAL` remains owner-only by
  code invariant: `EnvApprovalStore.put` raises "the executor may not write
  its own approval".
* No limit raised, no budget reset, no taker switch touched.
