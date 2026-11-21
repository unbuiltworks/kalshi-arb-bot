# Campaign 10 — three-window maker-only autonomous validation

**For owner approval. Nothing is armed, no variable has been changed, and
nothing has been deployed.** The service is running `35109a3a` and is
disarmed.

## 1. Deployment SHA and the exact production-bound diff

| | |
|---|---|
| Proposed SHA | **`dc92e8672808fba5a64856196fac730c8259636e`** |
| Branch | `claude/compassionate-allen-wic64u` |
| Pushed and accessible | yes — verified on GitHub |
| Currently deployed | `35109a3a` / deployment `7fbf65d6` (SUCCESS, 2026-10-29T23:37Z) |

`35109a3a..dc92e867` is 16 files. **Five are production-bound** for
`l5-pmus-live` (its Dockerfile COPY list):

```
M src/l5_faithful_translation.py    the extraction + controller_translate
M src/l5_live_execution.py          provenance, helpers, handle(controller=)
M src/l5_pmus_live_runner.py        maybe_controller_open + tick dispatch
M src/l5_intent_gate.py             the 45th refusal site in the inventory
M src/l5_runtime_config_verify.py   the new flag, reported at boot
```

The other eleven are five documents, four test files and
`src/l5_session_integrity.py`, which this service does not import.

**Import closure checked, not assumed:** walking every `from src import …`
from the entrypoint `src.l5_pmus_live_runner` yields 51 modules, and all 51
are in the COPY list. The one new import this change adds
(`l5_execution_translations as X` in `l5_live_execution`) was already
copied. **No Dockerfile change is required and no new `src` module exists.**

## 2. Atomicity of the translation change and the freeze guards

**For `l5-pmus-live`: atomic by construction.** All five files ship in one
image built from one commit. There is no partial-deploy path. And this
service does not import `l5_session_integrity`, so the runtime
`FROZEN_RULE_CHANGED` halt is not in its image at all.

**Two other services do carry both the frozen file and that runtime check** —
`pmus_ws_recorder` and `l5_live_probe`. The re-pin is in the **same commit**
as the translation change, so any future deploy of them from `dc92e867` is
also atomic. Neither is being touched:

- `pmus-ws-evidence-recorder` last deployed 2026-10-21, status SKIPPED, from
  branch `claude/l5-shadow-mode-deployment-epkz1i`. It does not track this
  branch and will not pick this up.
- `l5-pmus-observer` runs `deploy/l5_window_collector/Dockerfile` from a
  different branch, with watch patterns confined to collector paths.

Three freeze guards were re-pinned to `dda950a57331cba8`, each carrying the
reason: `tests/test_faithful_rule_frozen.FROZEN` (the authority),
`src/l5_session_integrity.FROZEN` (the runtime halt), and
`tests/test_l5_live_runner_graft` (byte identity → identity to the one
recorded hash).

Two variables that look like code pins, `L5_PROVENANCE_CODE_SHA` and
`L5_LOSS_SCOPE_CODE_SHA`, are read by **nothing** in the repository —
documentation labels, so this change cannot invalidate them.

## 3. The service is disarmed, and the new path defaults off

Read from the running service, not carried forward:

| Evidence | Value |
|---|---|
| `L5_LIVE_PREFLIGHT` at 00:30:09Z | `PASSED` |
| `executor_starts_disarmed` | passed |
| **`armed_after_preflight`** | **`false`** |
| `owner_arm_token_present` | `false` (non-blocking: "arming is a separate owner act") |
| `no_unexpected_open_orders` | `0` |
| Account | funded `$343.21615`, key fingerprint `dd262ed4fb89` |
| `L5_CONTROLLER_OPEN` | **absent from the service's variables** |

**I must correct my previous report.** I said preflight was `ok: false` on
three counts including `absolute_deadline EXPIRED` and `campaign_approval
stale`. That was stale. The preflight is **PASSING**; the real blocker is a
single one, below.

`L5_EFFECTIVE_RUNTIME_CONFIG` reads `PASS: false`, `checked: 26`, with
**eight** failed rows — and every one fails for the same reason,
`required: "NO_APPROVAL_IN_FORCE"`:

```
submission_budget_open       eff 24   req NO_APPROVAL_IN_FORCE
submission_budget_complete   eff 32   req NO_APPROVAL_IN_FORCE
submission_budget_replace    eff 16   req NO_APPROVAL_IN_FORCE
submission_backstop          eff 72   req NO_APPROVAL_IN_FORCE
aggregate_exposure_usd       eff 20   req NO_APPROVAL_IN_FORCE
aggregate_exposure_in_force  eff 20   req NO_APPROVAL_IN_FORCE
daily_realized_loss_halt     eff  5   req NO_APPROVAL_IN_FORCE
max_windows_authorization    eff  8   req NO_APPROVAL_IN_FORCE
```

The deployed values are already the authorized figures. They read `false`
only because there is no approval document to grade them against. **There is
exactly one authorization blocker: no campaign approval is in force.**

## 4. The controller uses fresh authenticated books, not L5 activity

Verified in production and in code:

- The preflight shows the authenticated socket serving a fresh two-sided
  book: `bid 0.4500 / ask 0.4600`, `stream_age_s 0.029`, `book_updates 94`,
  `source authenticated_ws`. The **REST book route 404s** for its whole
  retry budget — the websocket mirror is the only working book, and it is
  the one the trigger reads.
- `maybe_controller_open`'s code body, with docstring and comments stripped
  by AST, contains **no** `_last_action_row`, `l5_activity`,
  `new_l5_actions`, `action_key`, `l5_native`, `l5_to_pmus_action`,
  `transactionHash` or `OBS.` — asserted as a test.
- Its only market input is `self.book.snapshot(slug)`, read on the tick and
  not cached, which returns `None` unless the socket is **connected** and
  the book is **two-sided**, and which stamps its own `stream_age_s`.
- `controller_translate` then applies `STALE_MAX_S = 2.0` to that age. A
  book at `2.01s` refuses with `T_STALE` and no order is created — tested.
- The book the decision was gated against is recorded on the provenance
  chain as `CONTROLLER_BOOK`.

This is a tighter bound than the L5 path's, which is handed `{}` and rests
at L5's own price.

## 5. Submission, idempotency, inventory and reconciliation

All through the existing money path, all asserted against what reached the
transport (56 tests in `tests/test_l5_controller_open_trigger.py`):

- **Submission.** One tick produces one post-only MAKER order;
  `participateDontInitiate` is `true` in the wire body.
- **Authorization.** The leg is claimed by `claim_accumulation_leg`, the
  same per-leg grant the L5 path uses. Make the claim refuse and **nothing
  reaches the wire**. A second claim of the same leg raises `Refused`.
- **Idempotency.** `client_order_id` derives from
  `CONTROLLER|OPEN|<market>|r<n>|<LEG>`. Ten ticks against one order slot
  produce **one** order; a ledger that reports the id as already seen yields
  `ORDER.reason = DUPLICATE_SUPPRESSED` with `submitted: false`. Distinct
  legs and distinct rounds get distinct ids, so a complement is never
  mistaken for a duplicate of the opening leg.
- **No collision with L5.** An L5 `action_key` is
  `txhash|asset|side|price|size|timestamp` and cannot take the controller
  form. Both paths claim from the **same** accumulation state, so the L5
  path cannot re-place a leg the controller claimed, and vice versa.
- **Ambiguous acknowledgement.** The claim is taken before the wire; a
  transport that records the attempt then raises leaves the claim **spent**,
  and a fresh executor over the same state does not re-place that leg.
- **Partial fills.** Carried as inventory; the complement is sized to what
  filled, never to what was asked, and never exceeds it.
- **Restarts.** A fresh executor over the same ledger resumes the round
  rather than reopening the claimed leg, and prior inventory is not
  forgotten.
- **Reconciliation.** `reconcile_from_venue` lives on the **store** and is
  keyed on `market|side`. A venue row saying we hold 2.0 replaces our file's
  figure. There is no provenance column, so an autonomous fill reconciles
  identically — tested.
- **Fail-closed.** If the two-sided branch chose no leg, the key never
  resolves and the refusal is `CONTROLLER_INTENT:CONTROLLER_NO_PROBE_LEG`.
  The placeholder key is never submitted under.

## 6. Proposed authorization — three windows, maker-only

### The approval document

Graded through the real `CA.approval_gate()`: **`approved: True`, `why:
None`, and `clamped` is `false` on every field** — nothing asks for more
than the code ceilings allow.

```json
{
  "approved": true,
  "nonce": "camp10-<16 hex chars, owner-generated>",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<at install time, UTC>",
  "expires_utc": "<issued + 3h, UTC>",
  "max_windows": 3,
  "max_total_exposure_usd": "20",
  "max_daily_loss_usd": "5",
  "max_open_submissions": 12,
  "max_complete_submissions": 12,
  "max_replace_submissions": 6,
  "max_total_submissions": 27
}
```

`issued_utc` must be within `MAX_CAMPAIGN_S` (4h) of install, and the
effective expiry is clamped to `issued + 4h`. The nonce is still claimed
single-use before anything arms.

### Variable changes — six, every one a reduction or an activation switch

| Variable | Now | Proposed | Why |
|---|---|---|---|
| `L5_CAMPAIGN_APPROVAL` | expired | the document above | the one blocker |
| `L5_LIVE_AUTH_MAX_WINDOWS` | 8 | **3** | three windows |
| `L5_LIVE_AUTH_MAX_OPEN` | 24 | **12** | 3 windows x 4 rounds |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 32 | **12** | one complement per round |
| `L5_LIVE_AUTH_MAX_REPLACE` | 16 | **6** | scaled from 8 windows |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 72 | **27** | backstop below 12+12+6 |
| `L5_CONTROLLER_OPEN` | unset | **YES** | the activation switch |

Plus `L5_LIVE_AUTH_NOT_AFTER` (a fresh absolute deadline) and the owner's
arm token `PMUS_Q1_LIVE` / `PMUS_Q1_SESSION_ID` / `PMUS_Q1_ARM`, which are
the owner's act and not mine.

### Already deployed at the requested figures — no change needed

| Requirement | Variable | Deployed |
|---|---|---|
| $20 maximum exposure | `R.max_total_exposure_usd()` | `20` |
| $5 daily loss | `R.daily_realized_loss_halt()` | `5` |
| **$3 worst-case loss per window** | `L5_MAX_WORST_CASE_DEFICIT_USD` | **`3.00`** |
| one outstanding order | `R.max_live_orders()` | `1` |
| maker pair ceiling | `TS.max_pair_cost()` | `0.96` |
| first-leg price band | `TS.min/max_first_leg_own_price()` | `0.20` / `0.60` |
| opening leg size | `TS.opening_leg_max_contracts()` | `3` |
| opening notional | `TS.opening_notional_budget_usd()` | `3.00` |
| entry / completion cutoffs | `TS.entry/complete_cutoff_s()` | `600` / `720` |

### Verified: the proposal makes the verifier pass

Running `l5_runtime_config_verify.verify()` with this approval installed and
the six variables set, against production's own values for everything else:

```
PASS: True | checked: 26 | failed: []
  controller_open_enabled       True
  defensive_execution_enabled   False
  ts_two_order_pair_enabled     False
```

All eight `NO_APPROVAL_IN_FORCE` rows clear. **This is a measured result,
not a prediction.**

### One honest limitation of the approval mechanism

The approval document **cannot express "autonomous openings permitted"** in
a form the code enforces. `execution_scope` is not in
`CA.REQUIRED_FIELDS` and would be ignored. The approval authorizes windows,
exposure, loss and submission budgets — not which strategy opens. What
actually gates the autonomous path is `L5_CONTROLLER_OPEN` plus
`TS.active(market)`, and its orders claim through the identical
`claim_accumulation_leg`. So no new authorization primitive is needed, but
**approving this document is also approving autonomous openings**, and that
should be a conscious decision rather than an inference from the numbers.

## 7. Crossing stays disabled; every hard limit preserved

From the running service's own graded rows:

- `maker_only_forbids_defensive_crossing` — `effective: true`, `ok: true`
- `defensive_execution_enabled` — `false`
- `L5_DEFENSIVE_COMPLETION` — off; the 1.20 defensive ceiling is a code
  constant and inert while defensive is off
- `mandate_trusted_key_ids` — `(none)`; `mandate_enforcing` — `false`
- `mandate_signing_key_absent_from_runtime` — `true`, `ok: true`
- `ts_two_order_pair_enabled` — `false`: one order rests at a time

Every order the autonomous path can produce is post-only MAKER, asserted on
the wire body across both sides and multiple rounds. `EXECUTION_MODE` is
unchanged.

## Deploying it, when approved

`watchPatterns` is `["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`, so moving the
`commitSha` pin alone returns **SKIPPED** — this has cost an activation
window before. Deployment requires both: repin `commitSha` to `dc92e867`
**and** append a line to `deploy/l5_pmus_live/DEPLOY_TRIGGER`.

## What this validation can and cannot establish

It can establish that an autonomous maker opening reaches PMUS, is claimed,
fills or does not, pairs or strands, and settles — with provenance
separated from L5's throughout.

It **cannot** establish that the strategy is profitable. ARGMAX remains
**−$3.14** over 38 windows, and every opening-price band measured
net-negative. Three windows is an executability and safety test. Any
expectancy claim would need far more than three windows and is not what
this proposes.
