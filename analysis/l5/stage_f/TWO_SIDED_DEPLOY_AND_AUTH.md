# Authorized deployment action, verified preflight, live-authorization request

**NOT ARMED. NOTHING PUSHED. No order, no limit change, no variable written.**
I did not use a variable write or any other side effect to get around the
Production Deploy denial.

---

## 1. The authorized deployment path, and the permission needed

**The only authorized path is a push of
`deploy/l5_pmus_live/DEPLOY_TRIGGER` to origin.** That file is the service's
entire `watchPatterns`, so it is the deploy, and it is deterministic: the
build takes the commit carrying the entry. Nothing else promotes code to this
service without being the side effect I was told not to use.

**I have staged it and stopped.** The commit exists locally; **nothing is
pushed**, and a local commit deploys nothing — the build triggers only when
that file change reaches origin.

### What is blocked, precisely

`git push` of this branch was refused by the session's auto-mode classifier
with reason **`[Production Deploy]`**. There are **no settings files** in this
repo (`.claude/settings.json` and `.claude/settings.local.json` are both
absent), so no permission rule exists to carry it.

### Your three options

| | action | what it costs |
|---|---|---|
| **A — recommended** | **You run the push yourself.** | Nothing. Keeps the production-deploy decision with you, which is the right separation of duties for the money path. |
| B | You add a Bash permission rule allowing `git push origin claude/compassionate-allen-wic64u`, then I push. | That rule would let me deploy this service **for the rest of the session without asking**. I will not add it myself — self-granting deploy permission is exactly what the denial is for. |
| C | You re-run with a permission mode that admits the push. | Same standing-authority concern as B, more broadly. |

**Option A, in one command:**

```bash
git push origin claude/compassionate-allen-wic64u
```

**If you want the reports published WITHOUT deploying**, the trigger commit is
the branch tip and nothing else, so push everything below it:

```bash
git push origin HEAD~1:claude/compassionate-allen-wic64u
```

## 2. Executor stays disarmed — verified

| | |
|---|---|
| `armed` / `owner_arm_token` / gate `blocked` | **false / false / true** |
| `halt_latch` | `CLEAR` |
| `approval_state` | `approval_stale` — **no authorization in force** |
| `live_orders` | **0** |
| `all_time_realized_usd` | **−8.82910000** |

It stays this way until you complete §6. Promotion changes none of it: the
deploy is read-only reporting.

## 3. `taker_crossing_reachable` — PENDING, and a hard gate

**Not yet obtainable.** The deployed build has no such row; a restart alone
cannot print one. After promotion and a disarmed restart,
`L5_EFFECTIVE_RUNTIME_CONFIG` will carry:

```
taker_policy_enabled                  env L5_TAKER_COMPLETION_POLICY
taker_policy_owner_cross_authorized   env L5_TS_COMPLETION_CROSS
taker_crossing_reachable              the conjunction of both
```

**If it reads `true`, or does not appear, I stop and report.** No campaign is
proposed on an unverified maker-only claim. If `true`: set
`L5_TS_COMPLETION_CROSS` to anything but `YES`, restart, re-read.

The running process currently reports `taker_policy_enabled: true`, so the
second switch is the whole question.

## 4. Preflight reconfirmed (provisional — final reading is post-deploy)

**Watchdog, sweep at 12:29:55Z, deployment `5ef90355`:**

```
"emergency_cancel": false, "emergency_reason": null, "cancel_enabled": true,
"open_orders": 0, "overdue": [], "not_ours": [], "still_open": [],
"unverified": [], "read_error": null, "heartbeat": {"halt": false, ...}
```

**Two-sided quoting active**, from the executor's own accessors:
`ts_two_order_pair_enabled true`, `window_strategy TWO_SIDED` (ok),
`max_live_orders 1` (ok) with the pair slot raising it by exactly one.

**Risk limits**, every one unchanged:

| limit | value |
|---|---|
| aggregate exposure | **$20** |
| per-market worst-case deficit | **$1.50** |
| daily settled-loss halt | **$5** |
| residual cap | **3 contracts** — `residual_cap_decision` binds at 3.01 |
| max live orders | **2**, pair slot only |
| execution | maker-only, `maker_pair_ceiling 0.96` |

**Zero orders and obligations:** watchdog `open_orders 0`, all lists empty;
executor `live_orders 0`, `committed_usd {}`, `unresolved_accounting.count 0`.
The heartbeat's `stranded.inventory.yes:1` is inside a block labelled
*"MODELLED … NOT realized, NOT filled, NOT submitted"* — a shadow arm, not a
position.

**These must be re-read after the restart.** A restart re-reads every variable
and re-runs preflight, so today's values are provisional by construction.

## 5. The schedule, computed from actual preflight completion

**I am not naming a fixed start time, and the earlier 16:00Z proposal is
withdrawn** — it would be backdating a schedule to a preflight that has not
happened.

Let **T** = the instant the post-restart preflight completes and
`taker_crossing_reachable=false` is confirmed. Then:

```
start  S = the first :00/:15/:30/:45 boundary at least 10 minutes after T
end    E = S + 2h00m          (eight 15-minute windows)
ASK_STARTS_UTC        = S
L5_LIVE_AUTH_NOT_AFTER = E - 15m   (the LAST window's start)
ASK_DURATION_S        = ceil(E - issuance)   capped at 14400
```

The 10-minute margin exists because a window needs entry runway:
`ts_min_entry_runway_s = 300`, and arming inside that produces the
`WINDOW_BOUNDS_UNREADABLE` miss that cost Stage B2 its 1300z window.

**The authorization must be minted after T, never before**, because
`expires_utc = issued + ASK_DURATION_S`. An approval minted now would expire
mid-campaign, and `approval_state` already reads `approval_stale`. **Nothing
is backdated and no expired document is reused.**

Worked example — if T = 15:05Z, then S = **15:30Z**, windows
`1530z 1545z 1600z 1615z 1630z 1645z 1700z 1715z`, E = **17:30Z**,
`L5_LIVE_AUTH_NOT_AFTER = 17:15:00Z`, `ASK_DURATION_S = 9000`. I will compute
the real values from the real T and hand you the exact command.

## 6. `L5_TS_ROUND_ALLOCATIONS` 4 → 2: **I withdraw my recommendation. Keep 4.**

I called it a budget-headroom lever. **It is a position-sizing lever**, and I
was wrong. From `l5_two_sided_probe`:

```
env_per_round = cash_envelope / round_allocations
by_cash       = env_per_round / (1 + up_px)
qty           = min(by_loss, by_cash, by_opening_cap, by_notional, by_budget)
```

**Halving the divisor doubles the per-round cash envelope**, which doubles
`by_cash`.

**And `by_cash` is the constraint that actually binds today.** With the $12
envelope and 4 allocations, `env_per_round = $3.00`, so at an own price near
0.47 `by_cash = 3.00 / 1.47 = 2.04` contracts. **Stage B2's first legs were
2.02, 2.01 and 1.97.** The arithmetic reproduces the observed fills, so this
is measured, not assumed.

At 2 allocations `by_cash` rises to ~4.08 and stops binding; `by_loss =
1.50 / 0.57 = 2.63` takes over. Three consequences:

1. **Per-leg size grows ~2.0 → ~2.6 contracts, about 30%.**
2. **Worst-case single-leg strand grows from ~$1.09 to the full $1.50 cap**,
   leaving no margin under the deficit limit you asked me to preserve.
3. **It confounds the primary metric.** Matched contracts per filled first leg
   would be compared against a baseline measured at ~2.0-contract legs while
   running ~2.6-contract legs — a size change masquerading as a mechanism
   result.

**The concern that made me suggest it was real but misdiagnosed.** At 4
allocations the theoretical worst case is 4 rounds × 2 legs × 8 windows = 32
OPEN and 32 COMPLETE, leaving no COMPLETE headroom to repair a half-filled
pair. But that assumes every round fires in every window, and **Stage B2
placed 5–12 orders per window, never 32**. If the budget does bind it refuses
an **OPEN** — it declines new risk rather than trapping anything, and
cancellations are explicitly `UNBUDGETED: a bound that could refuse a cancel
is a bound that traps an order on the venue`.

**So: change nothing.** Keeping 4 preserves baseline-comparable leg size,
keeps ~$0.40 of margin under the $1.50 deficit cap, and fails safe if the
budget binds.

## 7. The final live-authorization request

**Step 0 — the only thing I need now:** your push, per §1.

Then I will, without arming: verify the deployed SHA, confirm the restart left
`owner_arm_token false` and the gate blocked, read
`taker_crossing_reachable`, re-read every §4 item, compute S and E from the
real T, and hand you the exact mint command and variable list.

**Then, your writes — in this order, and all yours** (the executor cannot
write its own authorization; `EnvApprovalStore.put` refuses by construction):

| # | variable | value |
|---|---|---|
| 1 | `L5_LIVE_AUTH_NOT_AFTER` | `E − 15m`, computed at §5 (currently `14:58:00Z`, stale from Stage B2) |
| 2 | `L5_CAMPAIGN_APPROVAL` | the document minted after T |
| 3 | `PMUS_Q1_SESSION_ID` | a fresh id |
| 4 | `PMUS_Q1_ARM` | the **same** id |
| 5 | `PMUS_Q1_LIVE` | `YES` — last |

**`L5_TS_ROUND_ALLOCATIONS` is NOT in that list.** Per §6, it stays at 4.

Authorization scope: **8 windows**, `cpc-btc-updown-15m-`, **$20** aggregate,
**$1.50** per-market deficit, **$5** daily settled-loss halt, **3-contract**
residual cap, **maker-only**, **2** live orders via the pair slot, budgets
**32 / 32 / 8 / 56** (already deployed — no budget variable changes).

**Not requested:** no capital increase, no new service, no new branch, no
crossing, no residual-cap change, no `MAX_WINDOWS` change, no sizing change.

**To stop fast, latch the HALT — do not clear a variable.** Clearing
`PMUS_Q1_ARM` takes effect only at restart; in Stage B2 window 8 armed 52
seconds after the write and traded for 5m53s.
