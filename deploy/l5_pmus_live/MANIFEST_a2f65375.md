# Deployment manifest — disarmed candidate `a2f65375`

Reproducible. Every command below runs as written from the repository root
and every figure is derived by one of them.

**This manifest authorizes nothing.** It is the artifact an owner-approved
disarmed deployment would be executed from, and the deployment has not been
executed.

---

## 1. Source

| | |
|---|---|
| **source SHA** | **`a2f6537517bdbd04e991015a86fa4c727dfe3dff`** |
| short | `a2f65375` |
| branch | `fix/l5-reopening-decision` (PR #208) |
| `src/` tree hash | `73ae3c563e137495…` |
| base of the stack | `claude/compassionate-allen-wic64u` (**not** `main`) |

```bash
git rev-parse HEAD                       # a2f6537517bdbd04e991015a86fa4c727dfe3dff
git rev-parse HEAD:src                   # 73ae3c563e137495...
git merge-base --is-ancestor \
  origin/fix/l5-preserve-reducing-maker-20261107 HEAD; echo $?   # 1 = PR #199 ABSENT
```

The last check is a safety property, not bookkeeping: **PR #199's broad
stale-order guard must not ship**, and exit status 1 proves it is not an
ancestor.

## 2. Production diff

Deployed now: **`26dbfa119`**, deployment `9ee6b0cc`, branch
`deploy/l5-controller-stack`. `26dbfa119` is **not** an ancestor of the
candidate, so the whole change is one diff:

```bash
git diff --shortstat 26dbfa119 a2f65375 -- src/ deploy/
#  9 files changed, 1533 insertions(+), 27 deletions(-)
git diff --stat      26dbfa119 a2f65375 -- src/ deploy/
```

| file | sha256 (first 16) | change |
|---|---|---|
| `src/l5_unmatched_disposition.py` | `ee293ed19d46d2a5` | the controller: reopening decision, unified dispatcher, weighted-inventory pricing |
| `src/l5_live_execution.py` | `73f0ccbb7e8e6d55` | `probe_reopen_decision`, per-side reduction rule, claims fail-closed |
| `src/l5_live_authorization.py` | `8c49807378fe7201` | step-14 deficit requirement, preflight row |
| `src/l5_pmus_live_runner.py` | `e3dd8429b8f202b1` | unified-controller wiring, requote rest age, `effective_cash_gate` deficit reporting |
| `src/l5_two_sided_probe.py` | `4468bf52ad4968e7` | `REQUOTE_MIN_REST_S` patience floor |
| `src/l5_capital_block.py` | `d4474dc9def175e7` | `matched_cash_split` (read-only) |
| `src/l5_capital_plan.py` | `497dd443d01237d9` | six-part capital accounting (read-only, **new**) |
| `src/l5_intent_gate.py` | `52545bbb02db6351` | refusal inventory +1 entry |
| `deploy/l5_pmus_live/DEPLOY_TRIGGER` | `ebdb4b498c787e80` | trigger text only |

```bash
git diff --name-only 26dbfa119 a2f65375 -- src/ deploy/ \
  | while read f; do [ -f "$f" ] && sha256sum "$f"; done
```

### Image closure — proven, not assumed

No Docker daemon is available in this environment, so closure is established
by staging the Dockerfile's COPY set into an empty tree and importing the
entrypoint from **that tree alone**:

```bash
python3 -I - <<'EOF'
import os, re, shutil
root, dst = ".", "/tmp/imgstage"
shutil.rmtree(dst, ignore_errors=True); os.makedirs(dst)
copies = [m.groups() for m in
          (re.match(r'^COPY\s+(\S+)\s+(\S+)\s*$', l.strip())
           for l in open("deploy/l5_pmus_live/Dockerfile")) if m]
for s, d in copies:
    sp, dp = os.path.join(root, s), os.path.join(dst, d)
    os.makedirs(os.path.dirname(dp), exist_ok=True)
    (shutil.copytree if os.path.isdir(sp) else shutil.copy2)(sp, dp)
print("COPY directives:", len(copies))
EOF
cd /tmp/imgstage && python3 -I -c "import sys; sys.path.insert(0,'.'); \
import src.l5_pmus_live_runner as m; print('entrypoint OK', m.DEFICIT_ENV)"
```

Expected: `COPY directives: 75`, then `entrypoint OK
L5_MAX_WORST_CASE_DEFICIT_USD`. **Verified at this SHA.**

Only `l5_unmatched_disposition.py` among the new modules is reachable from
the runtime and it is in the COPY set. `l5_capital_plan.py`,
`l5_capital_block.py` and `l5_queue_depletion.py` are imported by no module
under `src/` that the entrypoint reaches — they are read-only analysis and
are deliberately **not** in the image:

```bash
grep -rn 'l5_capital_plan\|l5_capital_block\|l5_queue_depletion' \
  src/ --include='*.py'            # no runtime importer
```

## 3. Required configuration

**For the disarmed deployment: none. Zero variables are written.**

The three blocks that make it incapable of submitting are already set on the
service and **must not be touched**:

| variable | required state | why |
|---|---|---|
| `L5_SHADOW_ONLY` | **unchanged** (set) | parses as `!= "0"`; the submission gate reads `SHADOW_ONLY_LOCK` |
| `L5_LIVE_HALT` | **unchanged** (set) | halts on **any** non-empty value, `"0"` included |
| `L5_CAMPAIGN_APPROVAL` | **unchanged** (expired) | `authorization_preflight` fails `campaign_approval` |
| `L5_LIVE_AUTH_NOT_AFTER` | **unchanged** (expired) | fails `absolute_deadline` |

Variables that would be needed **later, for a live Stage 1 window only** —
**not part of this deployment**:

| variable | from | to |
|---|---|---|
| `L5_MAX_CASH_DEPLOYED_USD` | `12` | `10` |
| `L5_MAX_WORST_CASE_DEFICIT_USD` | **unset** | `1` |

The second is the Stage 1 blocker this candidate closes: with it unset the
deficit check **does not run**, and after this change a campaign whose scope
names a deficit refuses to arm without it
(`CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT`).

## 4. Rollback target

| | |
|---|---|
| **roll back to** | deployment **`9ee6b0cc-4f3e-4431-adb9-295b1b7df0d1`** |
| its commit | `26dbfa119f8beec5e865dc3428f58c99224e0849` |
| method | Railway redeploy of that deployment (`canRollback: true`, `canRedeploy: true`) |
| migration to undo | **none** — code-only; no variable write, no volume schema change, no ledger migration |
| ledger compatibility | the candidate adds no new persisted key; `retired_markets`, `committed`, `leg_cost`, `live_orders` are unchanged in shape |

Rollback is therefore a single redeploy with no cleanup. **A rollback does
not need to undo a variable**, because none is written.

## 5. Post-deployment verification

Run in order. **Stop and roll back on the first failure.**

### V1 — the new container is running this SHA

```bash
# Railway MCP: list-deployments for service 87f1834e-530e-4fc4-a310-91c8d3add196
#   expect status SUCCESS and meta.commitHash == a2f65375...
```

### V2 — it cannot submit (the acceptance condition)

```bash
# Railway MCP: get-logs, filter 'shadow_lock', limit 1
```
Required, all four, from the booted container's own heartbeat:

```
submission_gate.blocked   : true
submission_gate.shadow_lock: true
halt_local_env            : true
armed                     : false
```

**Anything else is a failed deployment — roll back immediately.**

### V3 — authorization is blocked on three independent checks

```bash
# Railway MCP: get-logs, filter 'L5_AUTHORIZATION_PREFLIGHT', limit 1
```
Expect `ok: false` with `fail` containing **`shadow_lock`**,
**`absolute_deadline`** and **`campaign_approval`**. Fewer than three is a
failed deployment.

### V4 — the deficit bound is now VISIBLE, and says it is absent

```bash
# Railway MCP: get-logs, filter 'effective_cash_gate', limit 1
```
Expect:

```
max_worst_case_deficit_usd : null
worst_case_deficit_check   : "NOT_CONFIGURED_NO_CHECK_RUNS"
max_cash_deployed_usd      : "12"
```

**`null` is the correct and expected result for a disarmed deploy.** It is
the whole point of the change: before it, the absence of the deficit check
was invisible. A value here would mean a variable was written, which this
deployment must not do.

### V5 — no live orders, nothing unresolved

```bash
# same heartbeat line
order_progress.live_orders : 0
unresolved_accounting      : 0 (or absent)
```

### V6 — the durable ledger and watchdog survived the restart

```bash
# Railway MCP: describe-service for fb038b8c-c7c1-4599-b06a-54f72bad544f
#   expect the halt watchdog still deployed and healthy
```
And in the runner's heartbeat, `halt` must be the submission gate's verdict
(`true`), not an absent key.

### V7 — settled P&L has not moved

```bash
# Railway MCP: get-logs, filter 'L5_LIVE_SETTLED', limit 1
#   realized_today_after must still read -15.20090000 and no new
#   settlement record may appear: this deployment trades nothing.
```

**Confirmed settled realized net must remain −$15.6109 all-time.** Any
change means something traded and the deployment has failed its premise.

### V8 — regression parity at the deployed SHA

```bash
git checkout a2f65375 && python3 -m pytest tests/ -q -p no:randomly | tail -2
# expect: 20 failed, 18080 passed, 27 skipped, 1 xfailed, 14 errors
```

Exactly the baseline: **20 failures / 14 errors**, the same 20 IDs, zero
introduced.

## 6. What this deployment does NOT do

- It does not arm, trade, or submit an order.
- It does not write a Railway variable.
- It does not change a capital limit, a loss budget, or a pair ceiling.
- It does not enable taker execution — both switches remain off.
- It does not merge any PR; the candidate is a deploy branch cut from #208.
- It does not advance Stage 1. **Live trading readiness remains FAIL** on
  the residual sign: negative at ~2 points of win rate on 1.24M of L5's
  contracts, 51% of windows negative, wins recovering 82.9% of losses — so
  at Stage 1 size ~10 pairs earn **+$0.35** against a **$1.00** permitted
  loss.

## 7. Authorization still required

1. **Lift the deployment freeze** for this one disarmed deploy.
2. Nothing else. No variable, no campaign, no arming.

A live Stage 1 window would need, separately and afterwards: the two
variable writes in §3, a fresh campaign naming one window, an unexpired
`L5_LIVE_AUTH_NOT_AFTER`, and lifting both locks for that window only.
