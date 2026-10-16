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

### B1 — Cost-basis correction not yet applied (HIGHEST)
* Owner: me (mechanical) — no owner decision outstanding; already authorized.
* Evidence: deploy `95e713d0`, two boots, `L5_COST_BASIS_APPLY` returned
  `ABORTED / BATCH_MANIFEST_MISSING`, `applied: 0`, `realized_before ==
  realized_after == -233.98540000`. Root cause: the manifest was absent
  from this service's image — `deploy/l5_pmus_live/Dockerfile` COPYs per
  file and named no `config/` path. Nothing was written.
* Pass condition: `L5_COST_BASIS_APPLY` logs `mode: APPLIED`, `applied:
  47`, `preserved_unknown: 2`, `realized_today` moving
  `-233.98540000 → -24.32160000`, 47 `cost_superseded` records intact.
* Next action: deploy `119637c4` (manifest now in the COPY set) — in
  flight.

### B2 — Historical-loss gate still refuses every order
* Owner: **the owner** — this needs a decision, not code.
* Evidence: `CUMULATIVE_LOSS_LIMIT` fires on lifetime
  `realized_today()`: `loss = max(0, -realized) + max(0, unrealized)`,
  refusing at `loss >= L5_MAX_CUMULATIVE_LOSS_USD` (`20`). Today
  `realized = -233.9854` → loss `233.9854`. **Even after B1** the
  corrected lifetime figure is `-24.3216` → loss `24.3216`, still `>= 20`,
  so the correction alone does not clear this gate. The implemented-but-inert account-wide
  delta-loss policy reads **+$6.4146** post-baseline after the correction
  (naive delta would read +$54.6884 — a $48.2738 overstatement from
  pre-baseline correction deltas, which the implementation subtracts out).
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

**A. Activate the account-wide delta-loss policy.**
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
