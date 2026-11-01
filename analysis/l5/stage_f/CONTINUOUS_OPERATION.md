# Continuous operation and safe in-place updates for `l5-pmus-live`

**Date:** 2026-11-01
**Scope:** the existing executor service only. No new service, repository,
branch, strategy or volume.

**Headline: ~90% of the nine requirements were already built, wired and
deployed.** The durable architecture — signed standing mandate, per-UTC-day
rate ledger, revocation list, independent watchdog, venue reconciliation,
startup recovery, persistent ledgers on `/data`, DEPLOY_TRIGGER-gated
deployment — exists. Four things did not, and this change adds them. One
blocker is owner-only and I have not touched it.

**Two readiness defects found, one of them live and losing data right now.**
See §6. Neither is caused by this change.

---

## 1. What I inspected, and what already satisfies each requirement

| # | requirement | status | mechanism already in place |
|---|---|---|---|
| 1 | durable owner mandate, scope/limits/expiry/revocation | **EXISTS** | `l5_mandate_signature` (689 lines): Ed25519 over canonical bytes, `kind: "standing"`, `MAX_MANDATE_S = 30 days`, `not_before`/`not_after`, required capital/loss/promotion fields, replay floor, revocation list |
| 2 | continuous operation without re-arming each campaign | **EXISTS** | `l5_daily_action_ledger`: per-UTC-day `max_windows_per_day` / `max_submissions_per_day`, O_EXCL claims, counters roll at midnight with no owner action |
| 3 | safe deployment via the established workflow | **EXISTS** | `deploy/l5_pmus_live/railway.json` — `watchPatterns` is the `DEPLOY_TRIGGER` file **alone**, so ordinary code pushes cannot restart a process holding a resting order |
| 4 | pre-deploy reconciliation, submission pause, restart recovery | **PARTLY** — reconcile and recovery exist (`reconcile_from_venue`, `reconcile_stranded`, startup enforcement at `l5_pmus_live_runner.py:8512/8525`); **no submission pause**. Added in §2. |
| 5 | persistent inventory and exact-order tracking across deploys | **EXISTS** | `/data` volume, `l5_order_registry_server`, `WindowLedger`, `pmus_probe_ledger`; exact-venue-ID cancellation with independent verification |
| 6 | independent watchdog, automatic halts, alerts | **EXISTS** | `l5-halt-watchdog` owns the volume; executor's only verb is GET; `HaltAsserter` has no clear by construction; emergency cancel-all on latch (`ebaef2d8`) |
| 7 | post-deploy health verification and rollback | **EXISTS** | `l5_runtime_config_verify.verify()` at boot; Railway `canRollback: true` with named rollback points |
| 8 | versioned strategy config for before/after comparison | **MISSING**. Added in §2. |
| 9 | no automatic promotion of unvalidated strategy changes | **MODULE EXISTED, NEVER WIRED** — `l5_promotion_gate.py` (217 lines) was dead code: nothing in the arming path called it. Added in §2. **This was the most important gap.** |

The design these implement is `analysis/l5/STANDING_AUTHORIZATION_DESIGN.md`.
Its planned `L5_REQUIRE_MANDATE` switch was **not** needed: the shipped gate is
better, because it self-activates on `MANDATE_PUBKEYS` being non-empty, so
turning enforcement **off** takes a repo commit and a deploy rather than an
environment edit.

## 2. The exact changes

Four files, +217 lines of source, +1 Dockerfile line, +1 test file.

### 2a. The promotion gate, wired — requirement 9

`src/l5_live_authorization.py`, new **step 9b** in `authorize_arming`, plus a
`promotion_evidence` keyword argument.

```python
if _mandate_promotion is not None or _mandate_enforcing:
    _pv = PG.evaluate(promotion=_mandate_promotion,
                      shadow=..., oos_evidence=..., live_validation=...)
    if _pv.get("blocks_live") is not False:
        raise ArmingRefusal("PROMOTION_GATE_BLOCKS_LIVE", {...})
```

- **Binds only with a signed mandate**, so the shipped no-trusted-key state is
  byte-for-byte unchanged.
- **Fails closed twice**: `evaluate` defaults to blocking, and only an
  *explicit* `False` clears it — a malformed verdict, a missing key or an
  exception all refuse (`PROMOTION_GATE_UNEVALUABLE`).
- **The bar is in the signed bytes** (`min_settled_windows`,
  `min_expectancy_usd`, `oos_evidence_id`,
  `requires_bounded_live_validation`), so no variable edit lowers it and
  nothing in the process tunes it.

**I first wired this too early — before owner authorization — and an existing
test caught it.** The function's contract is that a refusal names the
outermost reason; reporting "the strategy is unproven" to an operator whose
actual problem is a missing authorization is a worse diagnostic. It depends on
measured evidence, which makes it the *least* structural check in the
function, so it now runs last, immediately before the claim is written. A test
pins that ordering.

**It refuses today, and that refusal is the acceptance test for requirement 9,**
not a defect to tune away: measured realized expectancy is **−$0.0108 per
settled window** over our seven settled windows, and the best validated
ex-ante improvement is **+$0.0176/window** (`BTC15M_NET_SOURCE.md`). Against
any sane `min_expectancy_usd` both refuse.

### 2b. The deployment drain — requirement 4

`src/l5_live_authorization.py`, new **step 0**, outermost:

```python
SUBMIT_PAUSE_ENV = "L5_SUBMIT_PAUSE"
...
if _pause and _pause.lower() not in ("0", "false", "no", "off"):
    raise ArmingRefusal("SUBMISSION_PAUSED_FOR_DEPLOY", {...})
```

- **Unset means running.** An absent variable must never pause a live
  executor, or every container that had not heard of this switch would stop
  trading the moment this deployed.
- **An unrecognised value pauses.** The safe reading of a typo in a STOP
  switch is stop.
- **It gates arming only.** Cancellation, reconciliation and settlement stay
  live — a pause that blocked cancels could strand exactly the order it was
  protecting. Pinned structurally: the switch is read **once** in the module,
  inside `authorize_arming`, and no cancel path consults it.

### 2c. Versioned strategy configuration — requirement 8

`src/l5_two_sided_probe.py`: `STRATEGY_CONFIG_LABEL = "ts2s-v1-maker-cap3"`
and `strategy_config_fingerprint()`, recorded into the arming claim.

A label alone drifts from the constants it claims to describe — exactly how
eight rows of the runtime verifier once came to disagree with the
configuration the owner had approved. So the identity is **label plus a digest
over the effective knob values**, read with env overrides applied:

```
entry_cutoff_s=600.0  complete_cutoff_s=720.0  round_allocations=1
round_allocations_max=4  abs_residual_cap_contracts=3
opening_leg_max_contracts=3  min_first_leg_own_price=0.20
completion_ceilings_in_force=improves,within_budget
-> digest 6c557e1cb976c46b
```

Change a knob without changing the label and the digest changes anyway, so two
settled result sets can never silently merge. The residual cap is read from
`l5_live_execution`, the module that actually enforces it, not from a copy.
It is an **identifier**: nothing reads it to decide anything, and an
unreadable fingerprint is recorded as such rather than refusing a window every
risk gate has already permitted.

### 2d. The verifier stops lying — requirement 7

`src/l5_runtime_config_verify.py`: `mandate_enforcing` was hardcoded
`lambda: False` as a "phase 1 constant". The gate is **self-activating**, so a
hardcoded `False` would have reported "not enforcing" from the moment
enforcement actually began — the one moment the operator most needs the row to
be right. It now derives from `bool(MS.MANDATE_PUBKEYS)`, the same expression
the arming gate branches on, so the two cannot disagree. Four rows added:
`promotion_gate_binds`, `submission_paused_for_deploy`,
`strategy_config_label`, `strategy_config_digest`.

### 2e. The image closure — a production break this change would have caused

`deploy/l5_pmus_live/Dockerfile`: `COPY src/l5_promotion_gate.py`.

`test_the_image_copies_every_module_the_entrypoint_can_reach` failed on my
change. Local import or not, the module has to **exist** in the image: without
the COPY the gate would raise `ImportError` inside the container at the exact
moment it decides whether a strategy has earned live capital. The existing
test caught it; this is why that test exists.

## 3. Tests

`tests/test_l5_continuous_operation.py`, **35 tests**, all passing:

| group | tests | covers |
|---|---|---|
| deployment drain | 18 | unset runs; 6 truthy values pause; 6 off-values run; unrecognised value pauses; drain beats the shadow lock, a halt and an unreconciled book; read exactly once and only in `authorize_arming`; no cancel path consults it; verifier row agrees with the gate |
| promotion gate, unit | 4 | no terms blocks; missing shadow stats blocks; **our measured −$0.0108 AND the validated +$0.0176 both block**; a tiny sample blocks even when profitable |
| promotion gate, wiring | 6 | inert with no trusted key; a signed mandate reaches it and is refused; **structural and authorization refusals still come first**; unevaluable refuses; only an explicit `False` clears it (5 malformed verdicts); clearing it is not by itself permission |
| strategy version | 6 | label and digest present; every knob readable; digest stable; a knob change moves the digest without the label; cap comes from the enforcing module; an unreadable fingerprint never refuses a window |
| verifier truthfulness | 2 | `mandate_enforcing` and `promotion_gate_binds` track the registry both ways; the new rows are reported |

**Parity, on the 93 existing suites that touch the three changed modules:**

| | passed | failed |
|---|---|---|
| baseline (stashed) | 2,942 | **0** |
| with this change | 2,977 | **0** |

Delta **+35**, exactly the new file.

**Full-suite parity, failing-id set compared directly:**

| | passed | failed | errors | failing-id sha256 |
|---|---|---|---|---|
| baseline (`HEAD~1` src + deploy) | 14,857 | 54 | 26 | `eb672d14b4ba` |
| with this change | 14,889 | 54 | 26 | **`eb672d14b4ba`** |

**Byte-identical: 80 ids both sides, zero new, zero disappeared.** An earlier
run of the changed tree reported 57 failures; those three did not reproduce
and are flakes, which the id-set comparison settles — the failure *set* is
exactly baseline. The 156 tests across the four existing
mandate suites pass unchanged. The runtime verifier is graded-row identical:
26 checked, the same 15 pre-existing local failures before and after (all are
unset-variable rows in this container), because every row added is
`observed_not_graded`.

## 4. Deployment procedure

**This change is not deployed and I have not touched `DEPLOY_TRIGGER`.** The
procedure below is what to run when the owner authorizes it.

```
 0. PAUSE          set L5_SUBMIT_PAUSE=deploy-<date> on l5-pmus-live
 1. VERIFY DRAIN   heartbeat shows submission_paused_for_deploy=PAUSED:...
 2. RECONCILE      confirm live_orders 0, committed_usd {}, and that
                   L5_LIVE_RECONCILE reports no un-cleared drift (see 6b)
 3. DEPLOY         append an entry to deploy/l5_pmus_live/DEPLOY_TRIGGER and
                   push -- that file IS the deploy; a code push alone does
                   nothing because watchPatterns is that file alone
 4. HEALTH         l5_runtime_config_verify at boot: mandate_enforcing,
                   promotion_gate_binds, strategy_config_digest,
                   submission_paused_for_deploy, taker_crossing_reachable
 5. RESUME         clear L5_SUBMIT_PAUSE
```

The trigger file is **append-only**; it is the deployment audit log. I
truncated it once this session with `>` instead of `>>` and had to restore it
from git.

**Rollback**, one call, no build:

| point | deployment | commit |
|---|---|---|
| current | `6e861476` SUCCESS | `4d52345d` |
| previous | `0072e385` | `00a78b50` |

Both report `canRollback: true`. A `commitSha` pin suppresses branch-push
triggers entirely, so a pinned service needs `connect-service-source` to
repoint — a variable write instead builds branch HEAD and ignores the pin,
which is how an earlier deploy in this workstream went to the wrong commit.

## 5. Readiness evidence, read from the running services

**`l5-pmus-live`** — deployment `6e861476` SUCCESS, commit `4d52345d`,
`restartPolicyType: ALWAYS`, one replica, `/data` volume, region
`us-east4-eqdc4a`. Heartbeat at **2026-11-01T21:26:31Z**:

| field | value |
|---|---|
| `armed` | **false** |
| `blocked` | **true** |
| `approval_state` | **`approval_stale`** |
| `reason` | `NOT_ARMED` |
| `halt_latch` | `CLEAR` |
| `halt_local_env` | false |
| `owner_arm_token` | **`true`** — see 6a |

The durable blocker is the stale campaign approval: correct fail-closed
behaviour after the eight-window campaign closed.

**`l5-halt-watchdog`** — independent service, separate image, owns the latch
volume; the executor can only GET.

## 6. Two readiness defects

### 6a. `owner_arm_token` now reads `true`

The standing instruction was to preserve `owner_arm_token=false`. The running
heartbeat reports **`true`**. The service is not armed — `blocked: true` and
`approval_state: approval_stale` stop it — but one of the three arming
variables is set, so only the remaining gates stand between this service and
live submission. **I have not changed it**, because clearing a Railway
variable on the money-capable executor is an owner action and the service is
safely blocked meanwhile. Recommend clearing it before any deploy.

### 6b. The observer's critical notification: its volume is FULL, and it is losing every window

This is the item flagged for investigation, and it is **live right now**:

```
L5_COLLECTOR_ERROR {"error": "OSError", "detail":
  "[Errno 28] No space left on device:
   '/state/l5-pmus-obs/windows/cpc-btc-updown-15m-2026-11-01-2115z'"}
```

Retrying every ~5 seconds continuously through 21:26Z. `WindowRecord.__init__`
cannot `mkdir` the window directory, so **no window is being collected at
all.**

Three things make this worse than it looks:

1. **It is not a crash, so `restartPolicyType: ALWAYS` cannot help.** The
   process is healthy and looping. Nothing restarts, nothing alerts, and the
   service's last SUCCESS deployment is **2026-09-26** — every deployment
   since reads SKIPPED, because its `watchPatterns` never matched the pushes
   that touched other services' triggers.
2. **It breaks requirement 2 at the root.** The promotion gate is satisfied by
   measured shadow expectancy over a minimum settled sample. A collector
   writing nothing produces no new evidence, so a continuously-operating
   executor gated on that evidence could **never** be promoted — it would sit
   refusing forever while looking healthy.
3. **My earlier report in this session that the observer was "SUCCESS, no
   critical notification found" was wrong.** The notification is real; I had
   looked at deployment status, which is SUCCESS/SKIPPED, rather than at the
   logs.

**I have not fixed it.** Freeing the volume means deleting collected window
data — destructive, and on a volume holding the research tape. The options,
for the owner to choose:

- **expand the volume** (no data loss, costs money);
- **prune the oldest collected windows — but NOT yet, and this is the
  correction that matters.** I checked whether the volume's contents are
  already in the repository tape, and they are not:
  `data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl` is **363 lines / 176 KB**,
  last written 2026-10-29, and `data/l5_window_forensics/` holds a single
  incident file. The collector has been writing to `/state` since its last
  successful deploy on **2026-09-26**. So the volume holds substantially more
  than the repository does, and **deleting any of it would destroy data that
  exists nowhere else.** Export must come first; expanding the volume is the
  only immediately non-destructive option;
- **add a retention policy** to the collector so it bounds its own footprint,
  which is the durable fix and is a code change needing its own review.

A fourth item worth noting: `L5_LIVE_RECONCILE` at 21:26:07Z reports drift
against the venue —
`2026-10-14-2230z|UP: ours 2.9000, venue 0` and
`2026-10-12-0030z|DOWN: ours 2, venue 0`, both `stranded_without_stamp`.
These are mid-October ledger rows the venue settled long ago. `reconciled`
is `true` and they are not blocking, but they are **persistent inventory state
carried across deployments that does not match the venue** (requirement 5), and
step 2 of the deployment procedure should clear them rather than carry them
into continuous operation.

## 7. The one remaining blocker, which is owner-only

`MANDATE_PUBKEYS` is **empty**:

```python
MANDATE_PUBKEYS: Dict[str, str] = {
    # "owner-2026-10": "<64 hex chars>",
}
```

Every mechanism for requirements 1, 2 and 9 is built, wired, tested and
self-activating on this dict being non-empty. Until the owner's Ed25519
**public** key is committed, the mandate gate and the promotion gate are both
inert and the service keeps using the per-campaign path — i.e. the manual
ceremony continues.

**I did not generate a keypair.** The private half must be created by the
owner, offline, and must never enter this repository, Railway, a log or a chat.
The exact offline procedure already exists at `docs/OWNER_MANDATE_SIGNING.md`:
it generates the key at `~/.l5/mandate_ed25519.key` with mode `0600` and
prints only the public half. Give me that public hex string and I commit it as
a reviewed one-line change; enforcement begins on the next deploy.

## 7b. A limitation in my own change, stated so it is not discovered later

**The promotion gate is wired as an unconditional refusal, not yet as a
conditional gate.** `authorize_arming` now accepts `promotion_evidence`, but
`l5_pmus_live_runner.py:2517` — the only caller — does not pass it, and the
runner does not currently hold the two inputs (`windows_settled`,
`net_per_settled_window` live in the shadow controller's heartbeat, not in the
runner). So once a trusted key is installed, the gate refuses every window
with `SHADOW_STATS_MISSING`.

For today that is the correct state and the safe direction: the instruction is
not to arm, and a gate that refuses for lack of evidence is behaving exactly
as designed. But it means **installing the public key would stop the
per-campaign path working without yet enabling the standing path** — the
executor would refuse at step 9b instead of arming. Two consequences the owner
should price in:

1. The key ceremony (§7) and the evidence wiring must land **together**, or
   the service goes from "arms per campaign" to "refuses everything".
2. Wiring the evidence feed from the shadow controller into the runner's
   `authorize_arming` call is a **separate change on the money path** and
   needs its own review. I have not made it, because it is the one change
   here that could turn a refusal into an arming, and the instruction was not
   to deploy a risk-increasing change.

Sequencing I would recommend: deploy this change first (all refusals, no key),
confirm the four new verifier rows in the running process, then land the
evidence wiring and the public key in one reviewed change.

## 8. Proposed ongoing authorization scope

Not a request to arm. This is the scope I would ask the owner to sign **after**
the promotion gate's bar is actually met, stated now so it can be reviewed
cold.

| term | proposed | basis |
|---|---|---|
| `kind` | `standing` | — |
| validity | **14 days**, not the 30-day `MAX_MANDATE_S` | shorter first mandate |
| venue / strategy | `PMUS` / `l5-two-sided-btc15m` | unchanged |
| `execution_mode` | `MAKER_ONLY` | re-asserted in signed bytes |
| series | `cpc-btc-updown-15m-` | code allowlist, document may only narrow |
| `max_total_exposure_usd` | **$20** | unchanged |
| `max_cash_deployed_usd` | **$12** | unchanged |
| `daily_realized_loss_halt_usd` | **$5** | unchanged |
| `residual_cap_contracts` | **3** | unchanged; and the cap validated OOS at +111.9 bp/$ |
| `max_windows_per_day` | **8** | same rate as the approved campaign, now per day instead of per nonce |
| `max_submissions_per_day` | **56** | unchanged |
| `promotion.min_settled_windows` | **956** | the power recheck: 956 windows detects +$0.10/window at 80% power |
| `promotion.min_expectancy_usd` | **0.05** | above our −$0.0108 realized and above the +$0.0176 validated improvement, so it refuses today |
| `promotion.oos_evidence_id` | `btc15m-oos-20261101` | `BTC15M_NET_SOURCE.md` |
| `promotion.requires_bounded_live_validation` | **true** | shadow can only ever withhold; going live additionally needs a bounded live probe recorded against that id |

Revocation stays three independent paths, all existing media: the halt latch
(fastest, no redeploy, executor cannot clear), the watchdog's revocation list
by mandate nonce, and replacing the document.

**Note the consequence plainly:** signing this would **not** start trading. The
promotion gate would refuse, the system would stay in shadow, and it would
begin trading on its own only once measured expectancy clears $0.05/window —
bounded, and with no further Railway change. That is the honest answer to
"remove the ceremony" that does not also mean "trade a measured loser
continuously". On current evidence it refuses, and it should.

## 9. What this change does not do

- does not arm anything, submit an order, or change any limit;
- does not deploy — `DEPLOY_TRIGGER` is untouched;
- does not create a service, repository, branch, strategy or volume;
- does not install a key, so the mandate and promotion gates remain inert;
- does not alter sizing, the 0.96 completion ceiling, `ENTRY_CUTOFF_S`, the
  residual cap, the watchdog, or exact-order cancellation;
- adds no graded verifier row, so no deployment can newly fail on it;
- every added code path is a **refusal**, an identifier or a report. Nothing
  added can permit an action that was previously refused.
