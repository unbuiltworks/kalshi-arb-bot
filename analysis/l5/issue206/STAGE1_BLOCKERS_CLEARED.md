# Stage 1 blockers — disarmed-deployment readiness

**Disarmed deployment readiness: PASS.**
**Live trading readiness: FAIL** (one economic blocker, unchanged).

Nothing was deployed, merged, armed or configured. No Railway variable was
touched. **Confirmed settled realized net remains −$15.6109 all-time.**

## 1. The dependency stack, and the promotion plan

The chain is **linear** — each branch contains all of its predecessors — so
there is no subset to choose:

| PR | branch | commits from base | files |
|---|---|---|---|
| #197 **draft** | `fix/l5-issue195-rollover-and-real-completion` | 30 | 30 |
| #200 | `fix/l5-requote-pair-ceiling` | 33 | 42 |
| #202 | `fix/l5-pair-aware-stale-recovery` | 37 | 44 |
| #203 | `feat/l5-queue-depletion-estimator` | 38 | 51 |
| #204 | `feat/l5-unmatched-disposition` | 39 | 55 |
| #205 | `feat/l5-controller-authority` | 44 | 61 |
| **#208** | `fix/l5-reopening-decision` | **55** | **93** |

Base is `claude/compassionate-allen-wic64u`, not `main`.

**Audited exclusions.** **PR #199 is not an ancestor** of #208 —
`git merge-base --is-ancestor` confirms it — so the broad stale-order guard
the owner declined is excluded by construction, not by promise.

**What each layer contributes, and why none is omittable:**

| layer | required for |
|---|---|
| #197 | the 93s rollover fix and real second-leg submission — without it there is no completion path |
| #200 | the requote pair ceiling — without it the requote chases 1.0000 pairs (the 0100z defect) |
| #202 | pair-aware distance recovery — without it recovery cancels a profitable resting pair |
| #203 | the queue-depletion estimator — read-only, consumed by #204 |
| #204 | `l5_unmatched_disposition` — the controller itself |
| #205 | controller authority over the discretionary cancels |
| #208 | the reopening decision, capital accounting, the deficit preflight, PRESERVE/floor fixes |

**Promotion plan (minimal and auditable).** Production already runs a
snapshot branch (`deploy/l5-controller-stack` at `26dbfa119`), and
`26dbfa119` is **not** an ancestor of #208. So the auditable unit is one
deploy branch cut from `fix/l5-reopening-decision`, reviewed as a single
diff against the deployed SHA:

```
git diff 26dbfa119 <candidate> -- src/ deploy/
  8 files changed, 1383 insertions(+), 25 deletions(-)
    src/l5_capital_block.py          +114
    src/l5_capital_plan.py           +198   (new)
    src/l5_intent_gate.py              +6
    src/l5_live_execution.py         +305
    src/l5_pmus_live_runner.py        +45
    src/l5_two_sided_probe.py         +49
    src/l5_unmatched_disposition.py  +682
    deploy/l5_pmus_live/DEPLOY_TRIGGER  −9
```

That is the whole change to production. Merging the seven PRs is the
alternative and produces the same tree; it is more GitHub bookkeeping and no
more auditable, and it requires #197 to leave draft first.

## 2. The deficit bound now fails closed

**The defect.** The gate reads
`if self.max_worst_case_deficit_usd is not None:` — so an **unset** limit
does not widen the deficit check, it **removes** it. Production's
`effective_cash_gate` enumerated the gates in force and the deficit bound
was not among them, so a profile specified as "$1 maximum worst-case
deficit" would have armed with no deficit check at all and a
`max_usd_per_market` of 20.

**The fix, opt-in via the owner-signed campaign.** `authorize_arming`
step 14 already compares the approved scope against the running limits. It
now also reads `max_worst_case_deficit_usd` from the scope:

- **absent from the scope** → says nothing. Every existing configuration is
  untouched and **no new live limit is introduced.**
- **present, running limit unset / unreadable / non-positive** →
  **`CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT`**. Its own name, because an
  absent bound is not a wide bound.
- **present, running limit wider** → `CAMPAIGN_LIMITS_WIDER_THAN_APPROVED`.

The runner passes `gates=getattr(getattr(self, "executor", None),
"validation_gates", None)`; a runner with no executor passes `None` and is
refused for any campaign that names a deficit — the fail-closed direction.
`gates` defaults to `None`, so every existing caller is unchanged.

Mirrored read-only in `authorization_preflight` so an operator sees it
**before** spending a window. The row appears **only** when an approval asks
for the bound: `not_evaluated` means "cannot be answered here", and
"no approval asks, so none is required" *can* be answered — two existing
suites rightly enumerate that list, and this does not belong in it.

## 3. Watchdog intervention, exercised

Driven against a real `FaithfulExecutor` with the halt gate's actual
contract:

| condition | result |
|---|---|
| halt asserted | **0 submissions**, `HALT:halt_asserted:residual_above_cap` — the guard's own reason, not just the classification |
| watchdog raises | **0 submissions**, `HALT_GATE_UNREADABLE:RuntimeError` |
| halt record stale | **0 submissions**, `halt_record_stale` |
| watchdog permits | orders flow — so the halt is the cause, not the harness |
| **restart** | a fresh executor on the same volume is still halted |
| **trip after arming** | submissions stop immediately; the halt is evaluated per submission, not once at arming |

The halt is read **before** translation and before the book
(`i_halt < i_book` asserted on the source), so it cannot depend on the rest
of `handle` working. An absent halt gate is refused earlier still, at
arming, by `halt_gate_present`.

## 4. Configuration propagation, end to end

| stage | $10 cash | $1 deficit |
|---|---|---|
| environment variable | `L5_MAX_CASH_DEPLOYED_USD` | `L5_MAX_WORST_CASE_DEFICIT_USD` |
| parsed | ✅ | ✅ (`AUTH.DEFICIT_LIMIT_ENV == R.DEFICIT_ENV`, pinned) |
| effective gates | `max_cash_deployed_usd` | `max_worst_case_deficit_usd` |
| **heartbeat** | ✅ already | ✅ **newly reported** |
| final submission | `CASH_COMMITMENT:…>1.00` | `NO_LEGAL_QUANTITY` at a 0.01 bound |

**The heartbeat gap is closed, and it was the reason the blocker was
invisible.** `effective_cash_gate` now always reports the deficit bound and
states `worst_case_deficit_check: NOT_CONFIGURED_NO_CHECK_RUNS` when it is
absent — `null` meaning *no check runs at all*, which is a different and
more serious statement than a large number. Log-only; no behaviour change.

## 5. The disarmed candidate

| | |
|---|---|
| **candidate SHA** | **`<this commit>`** on `fix/l5-reopening-decision` |
| deployed SHA (unchanged) | `26dbfa119`, deployment `9ee6b0cc` |
| **regression parity** | **20 failed / 18,080 passed / 14 errors — exactly the baseline**, same 20 IDs, zero introduced |
| required variables for a **disarmed** deploy | **none.** `L5_SHADOW_ONLY` and `L5_LIVE_HALT` are already set and must not be touched |
| variables that would be needed **later, for live** | `L5_MAX_CASH_DEPLOYED_USD=10` (from 12) and `L5_MAX_WORST_CASE_DEFICIT_USD=1` (from unset) |
| rollback | redeploy `9ee6b0cc` — `canRollback: true`, `canRedeploy: true`; it is a code-only change with no variable or volume migration |

**Evidence it cannot submit.** Three independent blocks, all currently
asserted in production and none altered by this change:
`submission_gate.blocked: true`, `shadow_lock: true`, `halt_local_env: true`,
with `authorization_preflight.ok: false` failing `shadow_lock`,
`absolute_deadline` **and** `campaign_approval`. `L5_SHADOW_ONLY` parses as
`!= "0"` and `L5_LIVE_HALT` halts on **any** non-empty value — so lifting
either requires an explicit write, and lifting one still leaves the other
two plus an expired deadline and an absent campaign.

## Unresolved

1. **Live trading: the residual sign.** Measured negative at ~2 points of
   win rate on 1.24M of L5's contracts, 51% of windows negative, wins
   recovering 82.9% of losses. At Stage 1 size ~10 pairs earn **+$0.35**
   while the deficit bound permits **$1.00**. Economic, not code, and only
   our own fills settle it.
2. **No rebate has ever been credited** — every figure is gross of it.
3. **PR #197 is still a draft** and roots the stack. Only matters if the
   promotion goes by merge rather than by deploy branch.
4. **Watchdog *health* in production** is observable; this suite covers its
   *action*, which previously had no offline coverage.
