# Minimal reviewable patch plan

Two independent change sets. Neither is proposed for deployment in this
report, and neither changes arming, restart policy, or another service.

## Change set A — the executor pricing boundary (isolated, undeployed)

Branch: `claude/pmus-q1-executor-pricing-boundary`, cut from
`claude/l5-pmus-economic-results-7c116e` @ `3dc9f135c`.
**Not on the deployed branch and not on the deployed execution path.**

### Files

| File | Change |
|---|---|
| `src/pmus_q1_executor.py` | `+from src import pmus_ws_live_state as WS`; new `StateBackedFeed`; `run_campaign` gains `state` / `feed` / `max_book_age_s` and attaches the feed before `preflight`; `WS_STALE_REFUSED` status; `main()` owns feed construction and closing |
| `src/pmus_q1_canary.py` | `_StateBackedFeed` is now an alias for `X.StateBackedFeed` — the duplicate definition is deleted |
| `tests/test_q1_executor_pricing_boundary.py` | new, 10 tests |

### What it fixes

`attach_ws_feed` existed since the module was written and was called from
nowhere in the repository. `fresh_bbo` therefore always saw `WS_FEED is
None`, so an armed `run_campaign` raised `WS_PRICING_REQUIRED` before
placing anything. That refusal was *correct* — the keyless public gateway
is not an acceptable pricing source for a money-moving run — but it meant
the authenticated path was **unreachable**, not merely unused.

### What it deliberately does not change

- **No new early return.** With no feed supplied, `WS_FEED` is left unset
  and the existing contract decides, unchanged. This matters: the first
  draft of this patch *did* return early on a missing feed, which masked
  three of `preflight`'s own refusals and produced **5 regressions**. The
  early return was removed. Preflight's findings keep their order and
  their names.
- **`STALE_PRICE_REFUSED` keeps its meaning** (the post-only race guard).
  A stale or absent authenticated book is the separate, new
  `WS_STALE_REFUSED`, matching the canary's existing name.
- **Staleness has one implementation.** `StateBackedFeed` delegates to
  `WS.LiveState.snapshot`, which already raises on an absent or stale
  book. No second threshold is introduced.
- **No implicit socket.** `run_campaign` never constructs a `WsFeed`;
  `main()` does, and only when armed, so a disarmed invocation still makes
  zero network calls — which is what `DISARMED`'s own note claims. Pinned
  by `test_run_campaign_never_opens_a_socket_itself`.
- **No outcome inversion added.** The adapter returns long-side terms;
  `l5_order_body.venue_price` stays the only place inversion happens.

### Risks

1. `main()` now opens a websocket on an armed run before `run_campaign`
   checks `has_credentials` / `gate_asserted`. An armed-but-ungated
   invocation therefore connects and then returns `GATE_NOT_CLEARED`.
   No order can be placed, but it is a network call that did not happen
   before. Mitigation if unwanted: move the guard checks into `main()`
   ahead of the feed, or accept it — it is a read-only subscription.
2. `WS_STALE_REFUSED` is a new status string. `main()`'s exit-code set
   was updated to treat it as a clean refusal (exit 0). Any external
   consumer keying on the status set needs to know about it.
3. The canary alias changes `_StateBackedFeed`'s identity. Anything that
   subclassed or monkeypatched the canary's copy by module path now
   patches the shared one. Pinned by
   `test_the_canary_shares_the_executors_boundary_class`.

### Rollback

`git revert` the single commit, or
`git checkout 3dc9f135c -- src/pmus_q1_executor.py src/pmus_q1_canary.py &&
git rm tests/test_q1_executor_pricing_boundary.py`. Nothing is deployed, so
rollback is local only — no service restart, no image rebuild.

## Change set B — the deployed branch's handover gap (specified, NOT written)

Target branch: `claude/august-l5-forensic-replay-788cml` @ `b88ec5eec`
(the branch the live service is pinned to).

Not implemented in this report. The mapping in
`PMUS_DEPLOYED_PATH_MAP.md` establishes that the deployed E1 path already
has durable pre-POST intent, fail-closed startup reconciliation, registry
restoration, and an ownership-scoped cancellation mechanism. The only
missing links are reachability and out-of-process recovery:

| # | File | Change | Lines of risk |
|---|---|---|---|
| B1 | `scripts/l5_e1_cohort_run.py` | install SIGTERM/SIGINT handlers that raise a dedicated exception, so the unwind path reaches the handover | small; needs B2 to be useful |
| B2 | `analysis/l5/e1_cohort/runner.py` | make the handover deadman reachable on **every** exit from the iteration loop, not only the `past_cutoff` branch. The cancel mechanism and its ownership scoping are unchanged — only reachability | the frozen-spec fingerprint changes, so the spec pin must be updated in lockstep |
| B3 | — | an out-of-process recovery actor. `restartPolicyType: NEVER` + one container means nothing cancels after a hard crash. The cancel tooling is already in the image; what is missing is something that runs it, which needs a restart-policy change or a second service. **Not authorized**, so recorded as an open requirement |

Explicitly not claimed: B1+B2 do **not** protect against SIGKILL, OOM,
power loss, or a container hard stop. Only B3 addresses those, and B3 is
not authorized.

Deployment closure for B1/B2 is already satisfied — `deploy/l5_e1_cohort/
Dockerfile` copies `analysis/l5/e1_cohort/` and `analysis/l5/live_exec2/`
as whole directories and `scripts/l5_e1_cohort_run.py` by name.

## Item 5 — full-suite result, stable tree vs patched tree

Both runs captured **whole**, not piped through `tail`, and the pristine run
executed in an isolated `git worktree` at `3dc9f135c` so nothing in the
working tree could contaminate it. (An earlier attempt used `git stash`,
which leaves untracked files in place — the new test file would have been
collected against the pristine source. That run was abandoned before it
produced a number.)

| | pristine `3dc9f135c` (worktree) | patched `c5b03c2ac` |
|---|---|---|
| failed | **50** | **50** |
| passed | 10,753 | 10,763 |
| skipped / xfailed | 28 / 1 | 28 / 1 |
| wall clock | 9m 12s | 8m 30s |

- **Regressions: 0.** `comm -13` over the sorted, de-parametrised FAILED
  sets is empty.
- **Cleared: 0.** This patch is not expected to clear anything; the 12
  `test_pmus_q1_executor` failures are armed-run tests that price from the
  keyless gateway, which an armed run must never do. They are outdated
  relative to the WS requirement, not broken by it, and fixing them means
  rewriting their expectations — out of scope here and deliberately left
  visible.
- **+10 passed** is exactly `tests/test_q1_executor_pricing_boundary.py`.

### The regressions that did happen, and were fixed before this number

The first patched run was **55 failed**, i.e. **5 real regressions**, every
one an image-closure test:

```
tests/test_probe_image_completeness.py::test_every_boot_scope_import_is_in_the_image
tests/test_probe_image_completeness.py::test_G_the_production_command_passes_only_with_the_full_closure
tests/test_probe_image_completeness.py::test_this_check_would_have_failed_at_565b4cde
tests/test_l5_live_runner_graft.py::test_the_image_copies_every_module_the_entrypoint_can_reach
tests/test_pmus_spring_evidence_transfer.py::test_spring_image_copies_every_module_it_imports_at_load_time
```

They were correct. A module-level `import pmus_ws_live_state` put that
module and the evidence recorder into the boot-scope closure of every image
copying `pmus_q1_executor.py`; those images would have failed to import and
never started. The import was made lazy rather than satisfied by widening
five Dockerfiles and a `railway.json` watch list. No safety assertion was
weakened and no test expectation was edited to clear them.

### The 50 remaining failures, by module

```
 12 test_pmus_q1_executor.py          9 test_spread_scanner.py
  5 test_orderbook.py                 4 test_case1_entrypoint.py
  3 test_gemini_q1_runner_wiring.py   2 test_r1_source_lineage.py
  2 test_lane_a_probe_v0.py           2 test_l5_live_probe_image_closure.py
  1 each: test_ws_durability_entry, test_window_forensics_stream,
          test_preflight_gate_isolation, test_l5_chain_validation,
          test_gemini_write_path_isolation, test_gemini_decision_observer,
          test_gemini_bundle_and_prelatch, test_deploy_fallthrough_guard,
          test_continuous_variants, test_chain_ca_fallback,
          test_case1_authorization
```

These are the pre-existing baseline, unchanged by this patch and kept
visible. The nine safety-relevant ones identified in the earlier triage
remain open and are **not** addressed here.
