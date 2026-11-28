# owner_arm_token provenance, and the owner-side campaign procedure

Date: 2026-11-02. Running commit `a1981165`, deployment `b0f6fa19`.

## 1. WHY owner_arm_token CHANGED false -> true

Established entirely from boot diagnostics that by construction never emit
a configured value. No credential was read, and none is reproduced here.

`Q1.armed()` is a conjunction of THREE conditions:

    PMUS_Q1_LIVE == "YES"  AND  PMUS_Q1_SESSION_ID non-empty
                           AND  PMUS_Q1_ARM == PMUS_Q1_SESSION_ID

### The window, narrowed to eleven minutes

| deployment | started | owner_arm_token | auth_not_after |
|---|---|---|---|
| `06d2b680` | 2026-11-01 12:46:57Z | **false** | EXPIRED, −78537.7 s |
| `6e861476` | 2026-11-01 12:58:58Z | **true**  | OPEN, +8161.6 s |
| `b0f6fa19` | 2026-11-02 00:33:01Z | **true**  | EXPIRED, −33481.4 s |

TWO env values changed together between 12:46:57Z and 12:58:58Z on
2026-11-01. Reconstructing the deadline from each process's own remaining
seconds, WITHOUT reading the variable:

    06d2b680:  12:46:57Z − 78537.7 s  ->  2026-10-31T14:58:00Z   (old value)
    6e861476:  12:58:58Z +  8161.6 s  ->  2026-11-01T15:15:00Z   (new value)
    b0f6fa19:  00:33:01Z − 33481.4 s  ->  2026-11-01T15:15:00Z   (same)

Two independent deployments, eleven hours apart, agree to within 0.4 s.
`L5_LIVE_AUTH_NOT_AFTER` was advanced from **2026-10-31T14:58:00Z** to
**2026-11-01T15:15:00Z**.

### Source: an owner variable write, and why it is attributable

- A `connect-service-source` repin changes SOURCE only. It cannot alter an
  environment variable. Only a variable write changes these values.
- The new deadline is a deliberately chosen wall-clock instant, not a
  derived or default quantity.
- It is NOT the value this session proposed. The 2026-11-01 report proposed
  `15:45:00Z`; the value actually set was `15:15:00Z` — THIRTY MINUTES
  TIGHTER. An automated replay of the proposal would have matched it.
  Independent tightening is the signature of a human exercising judgment.
- The contemporaneous record for that deploy (`4d52345d`) states "no
  variable written", and this session has written no variable since.

LIMITATION, STATED PLAINLY: Railway attributes every deployment in this
project to the same account (`unbuiltapp@gmail.com`), which is also the
account this session's MCP token belongs to. So the creator field CANNOT
by itself distinguish owner action from session action. The attribution
above rests on the three structural arguments, not on that field.

### Validity, scope, expiration — the decisive finding

**The token is structurally valid and authorizes NOTHING.** Its
authorization window CLOSED at 2026-11-01T15:15:00Z — 9 h 18 m before the
current process even booted. `L5_AUTHORIZATION_PREFLIGHT` reports
`absolute_deadline` **FAIL** (state EXPIRED), and `authorize_arming`
refuses `AUTHORIZATION_EXPIRED` at step 26.

It is a leftover from the campaign proposed on 2026-11-01 that never
armed, because the approval was stale then too.

Scope bindings that DO pass, and still constrain any future arming:
`session_id` PASS, `role_binding` PASS (blocks
`AUTHORIZATION_SESSION_MISMATCH` at step 41), `market_series` PASS
(restricted to the BTC15m series), `window_budget_env` PASS (8).

An unexplained token is not authorization — and this one is now explained
AND expired. It is not being treated as authorization.

### THE RISK-POSTURE CHANGE THE OWNER SHOULD DECIDE DELIBERATELY

Because the arm triple is ALREADY true, arming now requires only:

    a valid L5_CAMPAIGN_APPROVAL  +  a future L5_LIVE_AUTH_NOT_AFTER

On 2026-11-01 it additionally required the arm write itself. One of the two
locks is already open. That is a real reduction in defence depth and it
happened silently, as a side effect of a campaign that never ran.

TIGHTER POSTURE, recommended: rotate `PMUS_Q1_SESSION_ID` to a fresh value
and re-set `PMUS_Q1_ARM` to match it, so this campaign binds to a NEW
session id and the stale token cannot ride along. This costs one extra
variable write and restores the two-lock property.

## 2. CONFIG AND SHA RE-CONFIRMED

- `watchPatterns: ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]` — still restored.
- `commitSha: a1981165ffba4994481e109b4893588275e9137d`, branch
  `deploy-bc-telemetry`. `staged: null`.
- Everything else unchanged: builder DOCKERFILE,
  `deploy/l5_pmus_live/Dockerfile`, `python3 -m src.l5_pmus_live_runner`,
  restart ALWAYS, us-east4-eqdc4a x1, volume `/data`.
- Watchdog fresh at 00:46:34Z: `open_orders 0`, `read_error null`,
  `cancel_enabled true`, every list empty, `halt false`.

## 3-5. THE OWNER-SIDE PROCEDURE

STOPPED HERE BY DESIGN. `tools/install_campaign_approval.sh` requires
`RAILWAY_PROJECT_TOKEN`, an owner credential that must not reach this
session, and `EnvApprovalStore.put` refuses by construction so the executor
can never write its own authorization. Nothing was fabricated and no stale
authorization reused.

Schedule, chosen at 2026-11-02T00:46Z with ~44 minutes of preflight lead:

    four windows   : 0130z, 0145z, 0200z, 0215z
    first start S  : 2026-11-02T01:30:00Z
    last start     : 2026-11-02T02:15:00Z
    campaign ends  : 2026-11-02T02:30:00Z

RECOMPUTE IF LEAD IS SHORT. If less than ~15 minutes remains before `S`
when you run this, advance every value by 15-minute steps. `S` must be a
:00/:15/:30/:45 boundary, and the gate refuses any window starting before
`ASK_STARTS_UTC`.

### Step A — optional but recommended: rotate the session id

Restores the two-lock property described above. Set `PMUS_Q1_SESSION_ID`
to a fresh value and `PMUS_Q1_ARM` to the SAME value. Each write restarts
the service; with the commit pin in force a restart cannot bring new code.

### Step B — mint and install the approval

    export RAILWAY_PROJECT_TOKEN=...        # owner's Railway PROJECT token
    ASK_WINDOWS=4 \
    ASK_EXPOSURE_USD=20 \
    ASK_DAILY_LOSS_USD=5 \
    ASK_STARTS_UTC=2026-11-02T01:30:00Z \
    ASK_DURATION_S=7200 \
    ASK_OPEN=32 ASK_COMPLETE=32 ASK_REPLACE=8 ASK_TOTAL=56 \
      bash tools/install_campaign_approval.sh

The four budgets MUST be 32 / 32 / 8 / 56. The document's budgets become
`scope["submission_budgets"]` and the verifier grades the deployed
variables against them for EXACT EQUALITY; the script's defaults
(8/12/16/28) would report `PASS: false` on four rows purely from
disagreement and destroy the drift detection those rows exist for. All four
are inside `_CLASS_TICKET_CEILING` 4096.

Exposure 20 == `CA.MAX_TOTAL_EXPOSURE_USD`, daily loss 5 ==
`CA.MAX_DAILY_LOSS_USD`, windows 4 <= `CA.MAX_WINDOWS` 8, so each is
granted exactly with no clamp. The approvals INTERSECT with the code
constants and can never widen them. `ASK_DURATION_S=7200` covers mint-time
to 02:30:00Z from any mint inside the preceding hour and stays under
`MAX_CAMPAIGN_S` 14400. Runtime staleness is `MAX_CAMPAIGN_S` (4 h) via the
single `approval_gate()` factory, not the 1-hour default, so one document
covers the whole campaign.

The script mints, grades against the REAL `CA.ApprovalGate`, refuses to
install unless that gate approves, installs via `Project-Access-Token`
(never `Authorization`, which this session's egress proxy replaces),
and verifies by comparing sha256 digests rather than values. The nonce is
written to a mode-600 file and never to stdout.

### Step C — set the authorization deadline to the LAST permitted start

    L5_LIVE_AUTH_NOT_AFTER = 2026-11-02T02:15:00Z

The LAST window's START, deliberately: a 0230z window cannot then be
absorbed, because its own start lies beyond the deadline.

### Step D — then tell this session

Preflight will be re-run against the running process before anything arms:
authorization checks, watchdog, reconciliation, effective risk limits, and
the venue open-order read. If any gate fails, the executor stays disarmed
and the exact blocker is reported.

## 7. LIMITS TO BE PRESERVED, all currently verified in force

maker-only (`taker_crossing_reachable FALSE`), pair ceiling `0.96`,
exposure `$20`, cash deployed `$12.00`, daily loss `$5`, worst-case
deficit `$1.50`, residual absolute cap `3` contracts binding (k=2, outer
bound 10), `max_live_orders 1`, `max_concurrent_markets 1`,
`max_usd_per_market $20`, cumulative `$18.6325` binding with `$9.7279`
headroom. None is changed by this procedure.

## NOT DONE

Not armed. No order submitted. No variable written by this session. No
approval fabricated, no gate bypassed, no scope expanded.
