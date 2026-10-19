# Owner HALT reset: standalone maintenance procedure

Status: **READY, NOT RUN.** Production is shadow-locked with the latch held.
Nothing here has been executed against production.

---

## 0. Why the previous attempt failed, in one paragraph

Obtaining the digest that gate 2 demands meant pointing the
`l5-halt-watchdog` **service** at the one-shot reset image, because the reset
built its store as `FileStore(L5_WATCHDOG_STATE_DIR)` and a Railway volume
attaches to exactly one service. That image exits by design under
`restartPolicyType: NEVER`, so the watchdog **stopped sweeping at 01:00:12Z on
2026-10-19 and stayed down 5m53s**. Worse, `redeploy` reuses the previous
**build**, so the first attempt restarted the *watchdog* while the service
config claimed the reset image; the INSPECT never ran and **no digest was ever
obtained**. Both causes are now removed.

---

## 1. The digest no longer requires touching anything

`HaltGate.telemetry()` reports the sha256 of the latch bytes the executor
**already reads on every tick**, under `submission_gate.halt_latch_telemetry`
in each `L5_LIVE_HB`. Never the raw contents, never `evidence`.

```
"halt_latch_telemetry": {
  "sha256": "<64 hex>", "kind": "PRESENT", "halt": true,
  "reason": "UNMANAGED_RESTING_ORDER", "record_t": "..."
}
```

Obtain the digest by reading one heartbeat. Zero watchdog impact, zero extra
venue or watchdog load. **It must be re-read immediately before the reset: any
re-latch moves it, and gate 2 then refuses.**

---

## 2. Maximum expected interruption

The clear runs as Railway's `preDeployCommand` in the **new** deployment's
container with the volume mounted, **while the current container keeps
sweeping**. The only interruption is the ordinary container handover every
watchdog deploy already costs.

Measured on this service today:

| Event | Evidence |
|---|---|
| handover at 00:59:32Z | `Stopping Container` / `Starting Container`, sweep at `00:59:32.774` — **sub-second** |
| handover at 01:08:12Z | `Stopping` / `Starting` 01:08:12.497, `L5_WATCHDOG_BOOT` 01:08:13.934 — **~1.4s** |
| the failed image swap | 01:00:12Z → 01:06:05Z — **5m53s** |

**Maximum expected interruption: ~2 seconds.** Budget 5s. If sweeps do not
resume within 60s, follow §6.

---

## 3. Submission is independently blocked throughout (item 4)

Three protections, none of which depends on the watchdog running:

1. **`L5_SHADOW_ONLY=1`** — `authorize_arming` step 1 refuses
   `SHADOW_ONLY_LOCK` before anything else. Structural: `submit()` raises.
2. **The outage is itself a block.** `HttpHaltStore.get` raises →
   `_classify` → `UNREADABLE` → `state()` → `halt_state_unavailable`. A
   watchdog that is down **halts the executor by construction**. Pinned by
   `test_a_watchdog_outage_is_itself_a_submission_block`.
3. **`env_halt`** (`L5_LIVE_HALT`) — an independent OR in `HaltGate`.

**And it is verified, not assumed.** The executor publishes
`submission_disabled` in its registry report, read from the same
`L5_SHADOW_ONLY` the authorization refuses on, so the published answer cannot
drift from the real one. The reset refuses `SUBMISSION_NOT_PROVEN_DISABLED`
unless it is exactly `True` — **and refuses if the field is absent**, because
an executor too old to publish it is one whose lock cannot be established.

---

## 4. Deployment and rollback mechanics (item 5)

**Never assume `redeploy` rebuilds an image.** Verified both directions today:

| Need | Mechanism | Evidence |
|---|---|---|
| Rebuild from source | Change a path in the service's `watchPatterns` (`deploy/l5_halt_watchdog/DEPLOY_TRIGGER`) and push | `2af41e0b` BUILDING from the restore commit |
| **Not** rebuild | `redeploy` reuses the previous build | `e597cc88` restarted the *watchdog* after `dockerfilePath` was pointed at the reset image |
| Roll back | `redeploy` with an explicit known-good `deploymentId` | `d83c5e28` reused `563dc7ec`'s build, `L5_WATCHDOG_BOOT` 01:08:13Z |
| Config-only change | Applies on the *next* deployment, not immediately | `update-service` returns `updatedFields`, no deployment |
| Push touching no watch pattern | **No deployment at all** | `fda033e6`, `e0e5bc98` both SKIPPED while sweeps continued |

A variable write on this service produced **no deployment**, so it cannot be
relied on to apply config.

**Rollback target for this procedure: `563dc7ec-36ad-4ee8-8366-0aa977dc59f4`**
(watchdog image, commit `3d7b4677`, with the cancellation repair).

---

## 5. The procedure

Preconditions, all verified before anything is changed:

- [ ] `L5_LIVE_HB.submission_gate.shadow_lock` is `true`
- [ ] `L5_LIVE_DISARMED why: SHADOW_ONLY_LOCK` present
- [ ] Registry reports `submission_disabled: true`
- [ ] Latest `L5_WATCHDOG_SWEEP` has `open_orders: 0`, `not_ours: []`,
      `still_open: []`, `unverified: []`, `read_error: null`
- [ ] Sweeps arriving on the 20s cadence
- [ ] `halt_latch_telemetry.sha256` captured **within the last minute**
- [ ] `halt_latch_telemetry.reason` captured for the retyped-reason gate
- [ ] No BTC15m window within 10 minutes (so no live test is pending)

Steps:

1. Set the six gate variables on `l5-halt-watchdog`, with
   `skipDeploys: true` so nothing deploys yet:
   `L5_HALT_RESET_CONFIRM` = `RESET THE LATCHED HALT`,
   `L5_HALT_RESET_WHO`, `L5_HALT_RESET_ID` (**fresh**),
   `L5_HALT_RESET_EXPECT` = the digest from §1,
   `L5_HALT_RESET_CONFIRM_REASON` = the reason from §1,
   `L5_HALT_RESET_ATTEST`, plus `L5_HALT_RESET_PRE_DEPLOY=YES` and
   `L5_HALT_RESET_INSPECT=NO`.
2. Set `preDeployCommand` = `python3 -m scripts.l5_halt_reset`.
3. Trigger a rebuild via the `DEPLOY_TRIGGER` watch pattern (§4).
4. Read the pre-deploy output: `L5_HALT_RESET` with `ok: true`, or
   `L5_HALT_RESET_REFUSED` naming the gate. **A refusal exits 0 by design** —
   the deployment proceeds and the latch stays.
5. Confirm `L5_WATCHDOG_BOOT` and sweeps resumed, and that
   `heartbeat_refused` is **gone** from the sweep rows (the latch is clear).
6. **Remove `preDeployCommand`** and clear the six variables.
7. Confirm the executor's `submission_gate.halt_latch` reads `CLEAR` while
   `shadow_lock` stays `true` — HALT clear, submission still disabled.

The audit trail is preserved on the volume at `l5/halt_resets.jsonl`, fsynced
**before** the latch write.

---

## 6. Recovery

| Symptom | Action |
|---|---|
| Sweeps absent >60s after handover | `redeploy` `deploymentId: 563dc7ec…`; confirm `L5_WATCHDOG_BOOT` |
| Deployment fails | Latch is untouched (audit-before-clear). Remove `preDeployCommand`, roll back as above |
| `L5_HALT_RESET_REFUSED` | Read the named gate. Re-read the digest if it moved; mint a **new** id if the old one was spent |
| Latch cleared but sweeps absent | Roll back the image. Submission is still blocked by `L5_SHADOW_ONLY=1` |
| Uncertain state | Do nothing live. `L5_SHADOW_ONLY=1` holds submission closed independently |

A spent reset id is **inert** on every later deployment, so a `preDeployCommand`
left in place by mistake clears nothing — gate 3 plus the digest pin. Step 6 is
still mandatory.

---

## 7. Tested in non-production only

44 tests in `tests/test_l5_halt_reset_independent.py`; every write goes to
`tmp_path` or an in-memory fake. Covers the end-to-end rehearsal (digest from
the executor's own read → clear a temp latch → audit entry asserted),
pre-deploy exit-0-on-refusal, the bricking-deploy failure mode, wrong-image
refusal, spent-id replay with a *new* hazard's latch surviving,
`SUBMISSION_NOT_PROVEN_DISABLED` for both `false` and absent, telemetry
carrying no raw contents or `evidence`, and an unreachable watchdog reporting
no digest while staying blocked.

Regressions: **1420 passed, 0 failed** across halt, watchdog, cancel, shadow,
registry, one-shot, ported-safety, live-execution and wiring.
