# Recovery readiness — release candidate `1e643a78` (+ scope-digest fix)

Status: **everything executable is done.** The only remaining blocker is
external: Railway has paused deployments for this workspace's plan tier.

## Deploy mechanism (do not rely on the webhook)

The GitHub push webhook is NOT the path. It did not fire for `1e643a78`
even though that commit modifies the watched file. Use:

```
connect-service-source(
    projectId = 50fb7357-e2bc-4d04-a49e-71eb5d73b0d1,
    serviceId = 87f1834e-530e-4fc4-a310-91c8d3add196,
    repo      = unbuiltworks/kalshi-arb-bot,
    branch    = claude/gemini-btc-economic-viability-h3u7zl)
```

It "deploys on push, builds immediately", so it produces a fresh build of
branch HEAD *and* re-registers the push trigger. Integration verified
intact: builder DOCKERFILE, `deploy/l5_pmus_live/Dockerfile`,
watchPatterns `["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`, nothing staged,
service state `live`.

Do NOT pass `commitSha` — that pins the service to one commit and stops the
branch deploying afterwards.

## Rollback

`rollback` (or `redeploy`) to deployment `92f194b8-34e0-4e05-ac25-1d9dbef4b78b`
(commit `0c1ff17f`), which is the currently running image. Railway restores
"the previous image, settings, and all variables with a new deployment; no
redeployment is required" within the image retention window — 24h on Free,
72h on Hobby, 120h on Pro. Both rollback and redeploy are DEPLOYMENTS, so
neither is available while the pause holds: there is no way to undo a
deployment during a pause, which is itself a reason not to take risky
actions on the service right now.

## Post-deploy verification order (stop at the first failure)

1. deployment metadata `commitHash` == `1e643a78...` — anything else and the
   deployment is NOT complete
2. `L5_BUDGET_SELFCHECK.all_passed == true`, `missing_checks == []`
3. `submission_gate.shadow_lock == true` — **verified from the heartbeat,
   never from a variable write acknowledgment**
4. `submission_gate.halt_latch == CLEAR`, `halt_latch_telemetry.kind == PRESENT`,
   `record_t` fresh (watchdog alive)
5. `unresolved_accounting.count == 0`, `account_exposure.ok == true`,
   `account_total_worst_case_usd == "0"`, `live_orders == 0`
6. `day_realized.would_halt == false`, `unattributed_usd == "0"`,
   `undated_settlements == []`
7. `campaign.approval_state` and the refusal reason: the document in place is
   exhausted and expired, so arming must refuse by the CAMPAIGN's own name
8. only then: issue a fresh document and present it for approval

## Why the next deployment cannot submit, in three independent layers

1. `L5_SHADOW_ONLY=1` is stored (written with `skipDeploys: true`). Its value
   cannot be read back over OAuth -- `list-variables` returns names only --
   so it is VERIFIED IN THE HEARTBEAT at step 3 above and nowhere else.
2. The campaign document in the environment is **exhausted** (4 of 4 windows,
   durable on the volume) and **expired** (20:28:57Z). Under the release
   candidate a present-but-invalid campaign refuses by its own name and the
   legacy handshake is NOT a fallback -- proven by
   `test_an_EXPIRED_document_does_not_fall_back_to_the_legacy_path` and
   `test_an_EXHAUSTED_document_does_not_fall_back_either`. So the document
   must be LEFT IN PLACE: clearing it would expose the legacy path, which is
   armed.
3. No new document will be installed without owner approval.

## The scope-digest defect found during this pass

`mandate_scope` recorded `session_id()` -- the LEGACY `PMUS_Q1_SESSION_ID` --
in the scope whose digest every window re-proves. So even with the claim key
campaign-derived, rotating that variable mid-campaign changed the digest and
refused every remaining window with `MANDATE_SCOPE_CHANGED`: the original
failure mode, surviving one layer down. The scope now records the identity
that KEYS the claim. The legacy path is byte-identical (`session=None`
falls back to `session_id()`).

Caught by `test_an_ARMED_legacy_handshake_does_not_change_the_claim_IDENTITY`,
which arms a window, rotates both legacy variables, and requires the next
window to arm.

## Halt latch: NOT reachable without a deployment

`src/l5_halt_server.py` defines no `do_POST`/`do_PUT`/`do_DELETE`, so the
server answers 501 to all of them: "there is no code here that could be
persuaded to write a halt, let alone clear one". `HttpHaltStore.put` raises
`PermissionError`. The only working writer is `FileStore.put` against a
local path on the watchdog's own volume, driven by the watchdog's own
detectors. There is no halt-set CLI and no authenticated remote write
anywhere in the tree. Asserting the latch requires running code on the
watchdog container, which requires a deployment.

Nothing was cleared or reset.
