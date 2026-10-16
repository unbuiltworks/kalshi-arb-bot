# L5 — CANONICAL INVENTORY, WORKFLOW, BLOCKERS, STATUS

One file. It replaces the scattered status/inventory/approval notes for
day-to-day operation. Where it disagrees with an older `*_STATUS.md` or
proposal note, this file and live deployment evidence win.

Captured 2026-10-16 from Railway (project `50fb7357`, env `production`
`f3642d27`) and from GitHub `unbuiltworks/kalshi-arb-bot`.

Behavioral source of truth stays `L5_OPERATING_MODEL.md`. Coordination
stays GitHub issue #167.

---

## 1. Authoritative branch and deployment

| | |
|---|---|
| Money-capable service | `l5-pmus-live` (`87f1834e-530e-4fc4-a310-91c8d3add196`) — **sole** engine permitted to move money |
| Deployment branch | `claude/gemini-btc-economic-viability-h3u7zl` |
| Research/dev branch | `claude/l5-shadow-mode-deployment-epkz1i` |
| Dockerfile | `deploy/l5_pmus_live/Dockerfile` (pinned by `RAILWAY_DOCKERFILE_PATH`; **not** the root `Dockerfile`) |
| Deploy trigger | `watchPatterns = ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]` — code pushes alone do **not** deploy |
| Independent watchdog | `l5-halt-watchdog` (`fb038b8c-c7c1-4599-b06a-54f72bad544f`) |
| Evidence feed | `pmus-ws-evidence-recorder` (`71b6ea99-ea6a-4b20-9602-fa873721ed35`) |

## 2. KEEP / CONSOLIDATE / RETIRE

49 services exist in the production environment. All read `state: live`
(`l5-pmus-live` reads `live-with-staged-changes`), so "provisioned" is not
distinguishable from "running" by state alone — deployment status is.
20 volumes total **1,157 GB (~1.16 TB)**. The KEEP set uses **101 GB**, so
**~91% of attached storage is releasable**.

### KEEP (3)
| Service | Why |
|---|---|
| `l5-pmus-live` | the execution engine; holds the ledger volume, the audit, the gates |
| `l5-halt-watchdog` | independent HALT authority — must stay separate from the engine it halts |
| `pmus-ws-evidence-recorder` | read-only book/trade tape; the only durable PMUS evidence source |

### CONSOLIDATE (duplicated function, no deletion proposed yet)
Shadow/observer services that duplicate what `l5-pmus-live` now computes
in-process (shadow decisions, depth comparison, action comparison):
`l5-exec-shadow`, `l5-book-shadow`, `polymarket-temporal-shadow`,
`l5-transfer-supervisor-shadow`, `l5-live-habitat`, `l5-window-collector`.
Their evidence volumes are the only non-duplicated part.

### RETIRE candidates (not actioned — dependencies not yet shown)
| Candidate | Evidence |
|---|---|
| `l5-exec-shadow` | **CRASHED**; carries the single largest volume, **256 GB** (`l5-research-state`) |
| `kalshi-incentive-shadow` | CRASHED; Kalshi-era, out of PMUS scope |
| `l5-live-habitat` | CRASHED |
| `l5-transfer-supervisor-shadow` | FAILED |
| `l5-book-shadow`, `polymarket-l5-replay-once`, `l5-ops-research`, `polymarket-temporal-shadow`, `l5-window-collector` | **never deployed** — provisioned only |
| 10 services on cron `0 0 1 1 *` | fire annually; effectively dormant |

**Nothing here is deleted, disabled or redeployed.** Per standing
instruction that requires shown dependencies plus owner approval. The
single highest-value cleanup, by cost, is releasing the 256 GB volume on
the crashed `l5-exec-shadow` after its evidence is accounted for.

### Staged / undeployed changes — PRESERVED
Railway patch `a8fb9c09` on `l5-pmus-live`, 5 variable changes
(`L5_ACC_MAX_ROUNDS`, `L5_MAX_WORST_CASE_DEFICIT_USD`, `L5_TS_DEPTH_TICKS`,
`L5_TS_EXPERIMENT_ID`, `L5_TS_MARKET`), `destructive: false`. All five
values are **already live** with the same values, so the patch appears
stale. **Left untouched.**

~330 remote branches. Only the two named in §1 are authoritative.

## 3. Canonical release and operating workflow

Deploying the money-capable service is exactly five steps. There is no
other sanctioned path.

1. Develop and test on `claude/l5-shadow-mode-deployment-epkz1i`.
2. Cherry-pick **only** the runtime files needed onto
   `claude/gemini-btc-economic-viability-h3u7zl`. Every new module must be
   named in `deploy/l5_pmus_live/Dockerfile`'s per-file COPY set (the
   image-closure and manifest-shipping tests enforce this).
3. Run the money-path suites. Push the branch — **this does not deploy.**
4. Append an entry to `deploy/l5_pmus_live/DEPLOY_TRIGGER` stating what
   changes, which locks stay on, and the rollback deployment id. Push.
   **That is the deploy.**
5. Read the boot log and confirm: HALT state, `L5_SHADOW_ONLY`, limits,
   and the hook output for whatever was promoted.

Operating invariants: read-only collectors self-recover and run 24/7;
money-moving sessions fail closed and never relaunch themselves; capability
is enabled by variables on the service, never by code default.

## 4. Prioritized blocker register

### B1 — Cost-basis correction PARTIALLY applied: 44 of 47
* Owner: me for the reconciliation (done, below); **the owner** for any
  further correction of the 3 refused legs.
* Evidence: deploy `119637c4`, `L5_COST_BASIS_APPLY` at
  2026-10-16T17:35:59.685Z — `mode: APPLIED`, `applied: 44`,
  `refused: 3`, `preserved_unknown: 2`. Ledger moved
  `-233.98540000 → -76.12160000`, i.e. **+157.86380000**, confirmed by
  three heartbeats (17:39/17:40/17:41) all reading
  `account_wide_realized = -76.12160000`.
* The 3 refusals are all `LEDGER_DOES_NOT_MATCH_EVIDENCE` — the premise
  that the leg carries a $1/share placeholder is FALSE for them, because
  `cost_total` on a retired row is the executor's own tracked cost:

  | leg | expect (qty) | ledger `cost_total` | measured |
  |---|---|---|---|
  | `cpc-btc-updown-15m-2026-10-14-2230z\|UP` | 2.90 | **0.590000** | 0.59 |
  | `cpc-btc-updown-15m-2026-10-15-1945z\|DOWN` | 1.0000 | **1.4600** | 0.51 |
  | `cpcxrp-xrp-updown-15m-2026-10-16-0330z\|UP` | 50.0000 | **37.975400** | 1.00 |

* Pass condition: the 2230z leg needs no correction (ledger already holds
  the measured 0.59). The other two need their divergence EXPLAINED before
  any write — not a retry.
* Next action: read-only diff of those two rows against the venue fills.
  **No ledger write.**

### B2 — Historical-loss gate still refuses every order
* Owner: **the owner** — this needs a decision, not code.
* Evidence: `CUMULATIVE_LOSS_LIMIT` fires on lifetime
  `realized_today()`: `loss = max(0, -realized) + max(0, unrealized)`,
  refusing at `loss >= L5_MAX_CUMULATIVE_LOSS_USD` (`20`). Today
  realized is now `-76.1216` → loss `76.1216`, still `>= 20`, so the
  partial correction does not clear this gate.
  **The +$6.4146 post-baseline delta figure is WITHDRAWN.** It assumed the
  full +$209.6638; only +$157.8638 applied, so the post-baseline delta must
  be recomputed from the persisted ledger before the delta policy is
  considered at all. Do not activate it on the old arithmetic.
* Pass condition: the gate returns no `*_LOSS_LIMIT` refusal under a
  policy the owner has explicitly approved.
* Next action: owner decision on §5.A.

### B3 — HALT latched and `L5_SHADOW_ONLY` set
* Owner: **the owner**.
* Evidence: watchdog HALT latched; `L5_SHADOW_ONLY` set on the service;
  `live_orders: 0`. `submit()` raises `NotArmed`.
* Pass condition: HALT cleared through the existing
  `deploy/l5_halt_reset` procedure and `L5_SHADOW_ONLY` unset, both under
  explicit written authorization naming the configuration.
* Next action: owner decision on §5.B and §5.C.

## 5. The single consolidated approval request

Three items. Nothing below has been done; all three are blocked on an
explicit "yes" naming the item.

**A. WITHDRAWN pending recomputation.** The delta policy's post-baseline
figure rested on the full $209.6638. Only $157.8638 applied, so this item
is not ready for a decision and must not be activated.

*(Original text, for the record: activate the account-wide delta-loss policy.)*
Set `L5_MAX_ACCOUNT_WIDE_DELTA_LOSS_USD` = **1.00** on `l5-pmus-live`.
What it does: refuses when post-baseline account-wide loss (realized +
unrealized + fees + adverse reconciliation, L5-attributed or not) reaches
$1.00. The baseline is immutable and auditable; lifetime loss reporting is
preserved and the lifetime `CUMULATIVE_LOSS_LIMIT` stays configured.
What it does **not** do: it cannot conceal the historical $233.99 — proven
at every bound $0–$999,999, realized `-233.9854` still returns
`CUMULATIVE_LOSS_LIMIT`. Trading therefore also requires:

**B. Rescope the lifetime cumulative-loss gate to the post-baseline delta**
for the bounded validation only, via the existing protected mechanism.
This is the actual relaxation. Without it the historical loss blocks every
order regardless of A.

**C. Clear HALT and release `SHADOW_ONLY` for one bounded validation.**
Scope, unchanged from the earlier authorization: **one** market
(PMUS BTC15m), **one** outstanding order, maker-first, `$1` maximum
deployed cash, `$1` maximum loss, no automatic repeat after completion or
crash, settlement-only realized labeling.
Sizing that actually fits the full completion path under NO_RECYCLING:
**q = 0.59** → pair cost $0.5841, completion $0.4071, worst-case loss
**$0.4012**, +$0.0059 if both legs fill. (At q = 1.01 the worst case is
$0.6868 but the completion crossing needs cash beyond $1, so the path
cannot finish.)

## 6. What remains between us and the first real PMUS trade

In order, nothing else:

1. **B1** — the apply lands (in flight, mechanical, no decision needed).
2. **§5.A + §5.B** — the loss-gate decision. Without B, A alone changes nothing.
3. **§5.C** — HALT reset and `SHADOW_ONLY` release for the bounded run.
4. Preflight: gates return no refusal, `q = 0.59` order constructed, arm
   token matches the session id.
5. Submit → fill → settle → audit, reported with
   submitted / filled / settled / realized kept separate.

Steps 2 and 3 are owner decisions. Everything else is already built,
tested and deployed.

---

## 7. Appendix — 44/47 partial apply, reconciliation record (2026-10-16)

Persisted account-wide realized: **-76.12160000** (`realized_attributed`
-76.12160000, `realized_unattributed` 0, `legacy_realized_today`
-76.12160000). Stable across three post-apply heartbeats.

**Arithmetic closes exactly.** Sum of the 44 applied legs'
(cost_before − cost_after) = 157.86380000 = the ledger's own movement.
The 3 refused legs' manifest deltas sum to 51.80000000, and
157.8638 + 51.80 = 209.6638. Nothing is unaccounted and no leg is
partially corrected.

**Safely partial by construction.** All ten guards run before the first
mutation, so a refused leg leaves the row untouched, consumes no trade id
and spends no correction id. `check_batch` gates the batch; the per-leg
guards still run, which is why a 47-leg batch can legitimately write 44.
`cost_superseded` is copied verbatim; both realized keys moved together;
`realized_unattributed` was not touched; both QUANTITY_MISMATCH UNKNOWN
legs preserved.

**Correction IDs**: `cb-20261016-47leg-209.6638|<market>|<SIDE>`, one per
applied leg, for the 47 manifest keys MINUS the three refused above.

**Retrying the three is NOT supported as a retry.**
* `2230z|UP` — ledger already holds the measured 0.590000. Delta would be
  0, and guard 8 (`COST_CORRECTION_MUST_REDUCE`) refuses `want >= was`.
  **No correction is due.** Its 2.31 was never in realized as a placeholder.
* `1945z|DOWN` (ledger 1.4600 vs measured 0.51) and
  `cpcxrp…0330z|UP` (ledger 37.975400 vs measured 1.00) carry a tracked
  cost from the executor's own settlement path, not a $1/share placeholder.
  Correcting them needs a NEW owner-authorized batch with `expect` set to
  the actual current values — and first an explanation of the divergence.
  The XRP leg is the material one: the heartbeat books its settlement at
  `pnl -37.97540000 / UNKNOWN_COST_UPPER_BOUND` while the audit measures
  50 shares acquired at 0.0200 for $1.00. Two incompatible accounts of the
  same 50 shares. Do not write either figure over the other.

**Assumptions now invalid.**
1. "All 47 legs carry a $1/share placeholder" — false for 3.
2. `overstatement_usd 209.66380000` is an upper bound, not a correction
   amount; the supportable part was 157.8638.
3. Any post-baseline delta computed from +209.6638, including the +6.4146
   figure, is withdrawn.

**Reporting defect (no ledger impact).** `leg_cost_basis_unknown` is not
cleared by a correction, so `unknown_basis_keys` and the heartbeat's
`legs_priced_at_one_dollar_upper_bound` still list all 49 keys, including
the 44 now measured. Double-apply protection therefore rests on the spent
`correction_id` alone (mechanism 2); the "the batch stops matching"
mechanism (1) does NOT fire. Mechanism 2 is persisted and sufficient, but
this is one defense fewer than documented.
