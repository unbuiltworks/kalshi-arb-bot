# GO / NO-GO — eight-window BTC15m TWO_SIDED live campaign

Date 2026-10-21. Target commit `61e4b9b1` (application code identical to
`ab098648`). Authorization: **eight windows within four hours**, $5 aggregate
exposure, $5 daily realized-loss halt, $0.96 maker pair ceiling, one
outstanding order, budgets 16/24/32/56.

**STATUS: NO-GO. Two blockers, one of which is not Railway.**

---

## BLOCKER 1 — a fresh `L5_EXPERIMENT_ID` is required, or the campaign
## starts with five submissions already spent

This is the answer to "whether the five submissions are historical, which
campaign owns them, and whether anything remains unresolved", and it is the
finding that matters most.

**Every claim namespace is keyed on the EXPERIMENT, not on the campaign
nonce.** Read from `src/l5_live_authorization.py`:

| claim | key |
|---|---|
| single submission | `submission_sid` → `sub1.<experiment>` |
| class budget ticket | `class_sid` → `cls1.<experiment>.<KLASS>.<n>` |
| two-sided probe leg | `probe_leg_sid` → `ts1.<experiment>.<leg>` |
| accumulation leg attempt | `accumulation_sid(experiment, market, round, leg, attempt)` |

The campaign nonce keys the **mandate scope digest** — it does *not* reset
these counters. So issuing a fresh approval document with a new nonce while
leaving `L5_EXPERIMENT_ID` unchanged would start the eight-window campaign
with the four-window campaign's five tickets already consumed: the effective
budgets would be 16/24/32/56 **minus whatever those five spent**, not the
approved figures.

**Required before activation:** rotate `L5_EXPERIMENT_ID` **and**
`L5_TS_EXPERIMENT_ID` to fresh values for this campaign. No code change.
This is established practice here, not a new idea — it is why the repo
carries a task literally titled "Prepare the next BTC15m cancellation test
(fresh experiment ID)", and why the 0330z test previously failed at
`SUBMISSION_CLAIM`.

**Verification after rotation:** `class_budget_report(path, experiment)`
must show 0 spent in all three classes. If it shows anything else, the
rotation did not take and activation must stop.

### The five submissions, reconciled

Historical. They belong to the **four-window campaign of 2026-10-20**
(2245z–2315z), which settled at **−$0.6300**:

| window | submissions | outcome |
|---|---|---|
| 2230z | 0 | no order — the `STRATEGY_EXCLUSIVE_TWO_SIDED` / `TS.active()` deadlock, since fixed |
| 2245z | 2 | pair completed |
| 2300z | 2 | `D7HHE1P010JB` UP 3.75 @ own 0.2400 + `D7HKMA6G10JQ` DOWN 3.75 @ own 0.7100 filled at T+392 → **+$0.1875** |
| 2315z | 1 | `D7HT1H9G10JH` UP 3.39 @ own 0.3700, no complement, settled worthless → **−$1.2543** |
| **total** | **5** | **−$0.6300 settled** |

2 + 2 + 1 = 5, which matches the ledger count exactly.

**What I cannot confirm from here:** whether any order, position or
settlement remains unresolved. That requires the production ledger on the
Railway volume, which this session cannot read. It must be confirmed on the
Railway side — see the checklist rows for open orders and unresolved
inventory. My reconciliation says everything reached a terminal state, but a
reconciliation done in conversation is not the ledger.

## BLOCKER 2 — the deployment has not happened

`61e4b9b1` is pushed and is the branch tip; Railway created no deployment
record. Diagnosis and remedy are in the previous report: the push webhook is
not firing (documented precedent in
`analysis/l5/RECOVERY_READINESS_20261020.md`), and the recorded remedy is
`connect-service-source` on the existing service and branch, **without
`commitSha`**. Not `redeploy`/`rollback`, which reuse the old image.

---

## What changed since `f6b67aa2`

**Deployed delta `f6b67aa2` → `962e7a5d`** (12 files, +909/−9): the defensive
module as **observation only**; `MAX_WINDOWS` 4 → 8; the LIVE_SEQUENTIAL
shadow arm; both Dockerfiles gain `l5_defensive_completion.py`. No live
defensive submission path. No financial limit moved.

**Pending delta `962e7a5d` → `61e4b9b1`** (15 files, +3353/−48, 13 commits):
the live defensive execution path behind a disabled-by-default flag; the
`[T+540, T+600)` guard derived from the binding cutoff plus a 15 s execution
floor; both unmatched directions; the venue-position cross-check; the
budget-exhaustion entry block; `l5_runtime_config_verify.py`; the
intent-gate refusal inventory. No financial limit moved.

## Offline test evidence

Every scenario named for this audit, selected by test name:

| scenario | result |
|---|---|
| campaign expiry | **82 passed** |
| stale approval | **130 passed** (+1 pre-existing baseline error, below) |
| restart / recovery | **292 passed** |
| duplicate prevention | **56 passed** |
| partial fills | included in the above selections |
| rejected complements | **251 passed** |
| exact-order cancellation | **466 passed** |
| reconciliation failures | **236 passed**, 1 skipped |
| watchdog | **166 passed** |
| HALT | **289 passed** |
| campaign authorization | **242 passed** |

The single error is
`test_pmus_status_live_handoff.py::test_publish_counter_advances_so_staleness_is_observable`
— one of the 14 pre-existing baseline errors (it monkeypatches `_LIVE_STATUS`,
which exists nowhere in `src/`). Present identically at both measured
baselines. Not a release blocker and not new.

Full regression at `ab098648`: 26 failed, 12129 passed, 14 errors — the same
40 pre-existing ids as both baselines, zero new, zero gone.

**No release-blocking defect was found in code.** Both blockers are
operational.

---

## THE CHECKLIST

Run top to bottom. Stop at the first NO-GO.

| # | gate | requirement | who |
|---|---|---|---|
| 1 | **Commit** | deployment `commitHash` = `61e4b9b1`, status SUCCESS. Not `ab098648` — the trigger is itself a commit; the image is provably identical across every Dockerfile COPY path | Railway |
| 2 | **Runtime config** | in-container `python3 -m src.l5_runtime_config_verify` → `PASS: true, checked: 22, failed: []` | Railway |
| 3 | **Cancel margin + guard** | rows `cancel_margin_s` 300, `defensive_terminal_s` 600, `defensive_guard_opens_s` 540, `defensive_last_actionable_s` 585 — all four, since the guard is derived | gate 2 covers it |
| 4 | **Experiment rotated** | fresh `L5_EXPERIMENT_ID` + `L5_TS_EXPERIMENT_ID`; `class_budget_report` shows 0 spent in OPEN, COMPLETE, REPLACE | **BLOCKER 1** |
| 5 | **Campaign approval** | fresh document, `max_windows: 8`, owner nonce, issued within the hour, `expires_utc` ≤ issued + 4 h; gate reads `approved` | owner |
| 6 | **Cash + exposure** | account cash readable; aggregate exposure $0 at start; `max_total_exposure_usd` 5 | Railway |
| 7 | **Open orders** | zero resting orders owned by this ledger; `orders_uncertain` is None | Railway |
| 8 | **Unresolved inventory** | residual 0 in every market, or every non-zero residual explained and inside the residual bound | Railway |
| 9 | **Watchdog** | heartbeat current; independent HALT authority intact; **cannot submit** | Railway |
| 10 | **HALT** | latch not set. A set latch is **never** auto-cleared by a new campaign | Railway |
| 11 | **Market data** | BTC15m book fresh — `stream_age_s` inside `MAX_BOOK_AGE_S`, no `gap_suspected`; the authenticated book reads | Railway |
| 12 | **Durable reconciliation** | ledger reconciles to the account to the cent; `realized_attributed` + `realized_unattributed` accounted | Railway |
| 13 | **Cancellation proven** | exact-order cancel-by-venue-id with proof-by-re-read demonstrated, not assumed. 466 offline tests pass; the live mechanism was proven on a real resting order earlier in this programme | both |
| 14 | **Submission gate blocked** | before activation: `shadow_lock` or `NOT_ARMED` must still refuse. **Read from the heartbeat, never from a variable-write acknowledgment** | Railway |
| 15 | **Expiry + shutdown** | campaign expires on its own document (≤ 4 h, ≤ 8 windows); no automatic relaunch after a completed or crashed money-moving session | code, verified offline |
| 16 | **Deployment did not arm** | `defensive_execution_enabled: false`, campaign `NOT_ARMED` — arming is a separate, explicit act | Railway |

## Nothing beyond existing authorization is required

- Windows: 8 — authorized, and `CA.MAX_WINDOWS` is already 8.
- Budgets 16/24/32/56 — authorized and already committed to production. **No
  increase needed**: 56 across eight windows is 7.0 submissions per window,
  the same per-window headroom the four-window campaign had at 28/4.
- Exposure $5, halt $5, pair ceiling 0.96, one outstanding order — unchanged.
- The defensive path stays **disabled** for this deployment. Enabling it is a
  separate decision; the campaign runs maker-only completion at ≤ 0.96
  without it.

I will not generate, read, print or transmit the nonce or the arm token.
