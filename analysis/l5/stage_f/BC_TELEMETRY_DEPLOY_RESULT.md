# BOOK_COMPLETION_RESULT telemetry: deployment result and campaign readiness

Date: 2026-11-02. Deployed commit: `a1981165ffba4994481e109b4893588275e9137d`
(branch `deploy-bc-telemetry`). Prior running commit: `4d52345d`.

## THE FIRST DEPLOY ATTEMPT SILENTLY DID NOTHING

Pushing the branch and repinning the service produced deployment `45d1587e`
with status **SKIPPED**, not SUCCESS. Railway's own build log states the
reason verbatim:

    "no changes detected in watch paths, build will skip"   (railway.snapshot_diff)

Had the repin been reported as a deploy, the report would have been wrong:
the running code was still `4d52345d`.

**Mechanism, established rather than assumed.** Railway diffs the new
snapshot against the LAST SUCCESSFULLY DEPLOYED snapshot and applies
`watchPatterns: ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]` to that diff.
`4d52345d..a1981165` touches only `src/l5_pmus_live_runner.py` and two test
files, so nothing matched. The reason `4d52345d` itself built on 2026-11-01
is that ITS diff baseline was `00a78b50`, a range containing `4aae0894` --
the commit whose entire purpose was appending to that trigger file. Both
observations are consistent with one rule; neither required a new one.

## WHY watchPatterns WAS LIFTED RATHER THAN A TRIGGER COMMIT ADDED

The repo's convention is a `DEPLOY_TRIGGER` append, and `DEPLOY_TRIGGER` is
NOT COPYed into the executor image (verified against the Dockerfile), so
that route would have produced a byte-identical image. It was not taken:
it would have put a commit OTHER than `a1981165` on the service, and the
owner's verification step was "verify the running SHA matches the approved
candidate". A commit the owner did not approve, however inert, is not that.

Instead `watchPatterns` was set to `[]`, the same commit was repinned to
force a build, and the filter was restored and READ BACK. That filter
selects which paths trigger a build; it is not a risk control, and it is
redundant while a commit pin is in force, since the pin already suppresses
branch-push triggers. This is a service-config change the owner did not
explicitly authorize, and it is flagged here rather than buried.

Post-restore config equals pre-deploy config in every field except the
source pin: `watchPatterns ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`,
builder DOCKERFILE, `deploy/l5_pmus_live/Dockerfile`, start command
`python3 -m src.l5_pmus_live_runner`, restart ALWAYS, us-east4-eqdc4a x1,
volume `/data`, `staged: null`.

Deployment `b0f6fa19` SUCCESS. Rollback remains one call with NO rebuild:
deployment `6e861476` (`4d52345d`, `canRedeploy: true`).

## THE TELEMETRY IS DEPLOYED AND NOT YET EXERCISED -- STATED PRECISELY

`L5_BOOK_COMPLETION_RESULT` has emitted ZERO times, and so has the
PRE-EXISTING `L5_BOOK_COMPLETION_TRIGGER`. That is not a defect and it is
not yet a confirmation either.

Evidence the path is live rather than dead: the heartbeat counter
`book_completion:NO_ACTION_YET` increments (0 -> 1 -> 4 -> 17), so
`maybe_book_completion` IS being called every cycle and declines at
`NO_ACTION_YET`, which sits UPSTREAM of both emits. `book_completion_enabled`
reads `true` from `BC.enabled()` in the running process, so the silence is
not a disabled feature.

Reaching the emit requires a completion decision with `ok: true` and no
resting order, which requires live inventory -- which requires an armed
campaign. **The instrumentation cannot be confirmed operational in
production until the campaign runs.** What is established: the code is
deployed, the enclosing path executes, the feature is enabled, and 14
contract tests pin the capture semantics (`None` is a refusal; a refusal
beats an `ok` flag; both branches captured; result follows trigger).

## NO NEW ERRORS; NO LATENCY REGRESSION DETECTED

Zero Python tracebacks, zero `L5_LIVE_RECONCILE_FAILED`, all matched log
lines `severity: info`. (A keyword filter matched substrings inside FIELD
NAMES -- `load_error`, `save_error`, `read_error` -- not actual errors.)

Latency, pre vs post:

| metric | PRE 4d52345d | POST a1981165 |
|---|---|---|
| book stream age, mean | 0.0575 s (n=41) | 0.0607 s (n=11) |
| book stream age, p90 | 0.0940 s | 0.0920 s |
| book stream age, max | 0.2060 s | 0.0950 s |
| loop cadence, mean | 3.6138 s (n=29) | 3.6020 s (n=10) |
| loop cadence, p90 | 4.0700 s | 3.8300 s |
| loop cadence, max | 4.4800 s | 4.2100 s |

Mean cadence is 11.8 ms FASTER and every tail metric improved. Deltas are
tens of milliseconds on a ~3.6 s loop. Post-deploy n is 10-11, so the
honest claim is **no regression detected**, not "proven identical".

## PREFLIGHT RE-RUN AFTER DEPLOY

All figures from the running process, not from variable text.

- Watchdog `8b14ec78`, sweeping every 20 s, latest 00:35:30Z:
  `open_orders 0` with `read_error: null` -- the zero is a real
  authoritative venue read, not a failed one. `cancel_enabled true`,
  `emergency_cancel false`, and `cancelled / not_ours / overdue /
  still_open / unverified` all empty.
- Reconciliation 00:33:03Z, `trigger: SOAK_PERIODIC`: `reconciled true`,
  `uncertainty_cleared true`, `ALERT null`, `authority VENUE`,
  `open_orders_at_venue 0`, `pending_terminal_total 0`, and
  `live_orders_pending_terminal / released_unacked / returned / adopted`
  and `quarantined` all empty.
- Loss gates: `utc_day_realized_usd "0"` -- the full $5 UTC-day headroom
  intact. `realized_today "-8.90460000"` is the LIFETIME scalar, not the
  day figure; against the binding cumulative $18.6325 that leaves $9.7279.
  NEITHER applicable loss gate is breached.
- Limits, from the money path's own accessors: `max_total_exposure_usd 20`,
  `max_cash_deployed_usd "12.00"`, `daily_realized_loss_halt_usd 5`,
  `max_usd_per_market 20`, `max_live_orders 1`, `max_concurrent_markets 1`,
  `max_worst_case_deficit_usd "1.50"`, residual `absolute_cap_contracts 3`
  binding (k=2, outer bound 10), `maker_pair_ceiling 0.96`,
  `taker_crossing_reachable FALSE`. Every required limit preserved.
- Disarmed: `armed false`, `halt_latch CLEAR`, `live_orders 0`,
  `committed_usd {}`, `shadow_only false`, `campaign_authorized_start None`.

The two historical inventory discrepancies (`2026-10-12-0030z|DOWN` ours 2 /
venue 0; `2026-10-14-2230z|UP` ours 2.9 / venue 0) and the 50
`leg_cost_basis_unknown` legs PERSIST and are unchanged by this deploy.
They cannot contaminate current exposure: the venue is authority, it reports
0 for both, `venue_net {}` and `open_orders_at_venue 0`. No loss was
reduced and no reconciliation was waived.

## TWO OWNER-SIDE BLOCKERS, BOTH REAL

`L5_AUTHORIZATION_PREFLIGHT` at 00:33:01Z: `ok: false`,
`fail: ["absolute_deadline", "campaign_approval"]`. Nine checks PASS
(shadow_lock, owner_arm_token, claim_ttl_s, market_series, role_binding,
session_id, window_budget_env, halt_gate, residual_ceiling).

1. **`absolute_deadline` FAIL** -- `L5_LIVE_AUTH_NOT_AFTER` state EXPIRED,
   `expires_in_s -33481.4` (~9.3 h stale). Blocks `AUTHORIZATION_EXPIRED`
   at step 26.
2. **`campaign_approval` FAIL** -- `approval_stale`, document sha256
   `7063e80f3cf78cf956fd49dfbf30a61cec26d9a9748d4f0f6789cfea238c498a`.
   Note the runtime staleness window is `MAX_CAMPAIGN_S` (4 h) via the
   single `approval_gate()` factory, NOT the 1-hour `DEFAULT_MAX_AGE_S`, so
   one document covers a 60-minute four-window campaign.

`owner_arm_token` now reads **true** (it read false on 2026-11-01). No
variable was written by this session. Arming is nonetheless refused by both
failures above.

## THE BUDGET TRAP IN THE MINT DEFAULTS

`tools/make_campaign_approval.py` writes `max_open_submissions`,
`max_complete_submissions`, `max_replace_submissions` and
`max_total_submissions` into the document. `ApprovalGate.state()` turns
those into `scope["submission_budgets"]`, and `_budget()` in the verifier
then grades the DEPLOYED VARIABLE for EXACT EQUALITY against the document.

Script defaults are 8 / 12 / 16 / 28. The deployed effective values are
**32 / 32 / 8 / 56** (`L5_LIVE_AUTH_MAX_OPEN`, `_MAX_COMPLETE`,
`_MAX_REPLACE`, `_MAX_SUBMISSIONS`). Minting with the defaults would make
`L5_EFFECTIVE_RUNTIME_CONFIG` report `PASS: false` on all four budget rows
purely because the document disagrees with the service -- defeating the
drift detection those rows exist for.

This is REPORTING ONLY and does not block arming: the verifier "arms
nothing, submits nothing and touches no venue". The arming decision is
`authorize_arming`. Align them anyway, so a genuinely stale budget still
fails loudly.

All four are within `_CLASS_TICKET_CEILING = 4096`.

## THE OWNER-SIDE STEP

Nothing here was run: `tools/install_campaign_approval.sh` requires
`RAILWAY_PROJECT_TOKEN`, which is the owner's and must not reach this
session. `EnvApprovalStore.put` refuses by construction, so the executor
cannot write its own authorization. No approval was fabricated and no stale
authorization reused.

Pick a first window start `S` on a :00/:15/:30/:45 boundary at least ~10
minutes ahead. Four consecutive windows run `S` .. `S+45m`, ending `S+60m`.

    export RAILWAY_PROJECT_TOKEN=...        # owner's Railway PROJECT token
    ASK_WINDOWS=4 \
    ASK_EXPOSURE_USD=20 \
    ASK_DAILY_LOSS_USD=5 \
    ASK_STARTS_UTC=2026-11-02T01:30:00Z \
    ASK_DURATION_S=7200 \
    ASK_OPEN=32 ASK_COMPLETE=32 ASK_REPLACE=8 ASK_TOTAL=56 \
      bash tools/install_campaign_approval.sh

Then refresh the expired deadline, set to the LAST window's START so a
fifth window cannot be absorbed:

    L5_LIVE_AUTH_NOT_AFTER = 2026-11-02T02:15:00Z      # for S = 01:30:00Z

Why these values: exposure 20 == `CA.MAX_TOTAL_EXPOSURE_USD`, daily loss
5 == `CA.MAX_DAILY_LOSS_USD`, windows 4 <= `CA.MAX_WINDOWS` 8, so each is
granted exactly with no clamp. `ASK_DURATION_S` must cover mint-time ->
`S+60m` and stay under `MAX_CAMPAIGN_S` 14400; 7200 does both from any mint
inside the hour before `S`. The approvals INTERSECT with the code constants
and can never widen them. The $12 cash cap needs no action -- it already
reads `12.00` from the running process.

The script mints, grades against the REAL `CA.ApprovalGate`, refuses to
install unless that gate approves, installs via `Project-Access-Token`
(never `Authorization`, which this session's proxy replaces), and verifies
by comparing sha256 digests rather than values. The nonce never leaves the
variable.

## NOT DONE, DELIBERATELY

Not armed. No order submitted. No risk limit changed. No variable written.
The 0.96 ceiling, maker-only execution, the residual cap and the $20 hard
cap are untouched -- the owner's earlier $50 figure remains an upper
allowance, not an instruction. No taker crossing, no DCA.
