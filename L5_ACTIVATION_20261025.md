# L5 — Campaign activation: the two variables that are yours

**Configuration is deployed and verified. Nothing is armed.** The owner-only
approval and deadline variables have **not** been written.

Deployment `7a3ecff7-0f85-444d-9aed-6df51376ab78`, commit `749d4f67`,
service `l5-pmus-live`, environment `production`, booted 2026-10-24T23:49:50Z.

---

## 1. The approval JSON

Exactly these bytes, one line, **no trailing newline**:

```json
{"approved":true,"nonce":"l5-btc15m-nokey-20261025-7c4f9a21","series":"cpc-btc-updown-15m-","issued_utc":"2026-10-25T00:00:00Z","expires_utc":"2026-10-25T04:00:00Z","max_windows":8,"max_total_exposure_usd":"5","max_daily_loss_usd":"5"}
```

| | |
|---|---|
| **sha256** | `74ab4250c14e8f8d6c7537e66c50792e976a01afe5654ad40a39b31a79fe79f0` |
| Length | 235 bytes |
| Nonce | `l5-btc15m-nokey-20261025-7c4f9a21` → claim id `camp1.l5-btc15m-nokey-20261025-7c4f9a21` |
| Gate verdict | `approved: True`, nothing clamped |
| **Valid for arming** | **2026-10-25 00:00:00Z → 04:00:00Z** |

Verified against the real `ApprovalGate` across the window: approved at +1 min,
+120 min and +239 min; `approval_stale` at +241 min. Before 00:00:00Z it reports
`approval_issued_in_the_future` and refuses — harmless and self-resolving, but
there is no point setting it earlier than that.

### Absolute deadline

```
L5_LIVE_AUTH_NOT_AFTER = 2026-10-25T04:00:00Z
```

Set to match the approval's own expiry so the two bounds close together. It is
independently enforced at `authorize_arming` step 8 and currently reads
`EXPIRED` (`expires_in_s: -23123.9`).

### SHA-256 verification

```bash
printf '%s' '<the one-line JSON>' | sha256sum
```

`printf '%s'` matters — a trailing newline changes the digest. After the
service restarts, confirm the digest the service itself reports matches:

- `L5_LIVE_HB` → `campaign.document_sha256`
- `L5_AUTHORIZATION_PREFLIGHT` → `campaign_approval.document_sha256`

The current **stale** document's digest is
`1bd9a3a61b1e0a021cfebf0ffce3a32b35a79dde046683b06a0c73e8cc4f525c`. Seeing that
after the paste means the variable did not change.

**If you act after 04:00Z**, regenerate — the digest changes with the
timestamps:

```bash
python3 - <<'EOF'
import json, hashlib, datetime as dt
issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, second=0)
doc = {"approved": True,
       "nonce": "l5-btc15m-nokey-%s" % issued.strftime("%Y%m%dT%H%MZ"),
       "series": "cpc-btc-updown-15m-",
       "issued_utc": issued.isoformat().replace("+00:00", "Z"),
       "expires_utc": (issued + dt.timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
       "max_windows": 8,
       "max_total_exposure_usd": "5",
       "max_daily_loss_usd": "5"}
raw = json.dumps(doc, separators=(",", ":"))
print(raw)
print("sha256:", hashlib.sha256(raw.encode()).hexdigest())
print("NOT_AFTER:", (issued + dt.timedelta(hours=4)).isoformat().replace("+00:00", "Z"))
EOF
```

A regenerated document carries a **new nonce**, which is what captures a fresh
campaign baseline — that is intended and costs nothing.

---

## 2. Minimal Railway actions — two variables, both yours

Service `l5-pmus-live` (`87f1834e…`), environment `production`:

| Variable | Value |
|---|---|
| `L5_CAMPAIGN_APPROVAL` | the one-line JSON above |
| `L5_LIVE_AUTH_NOT_AFTER` | `2026-10-25T04:00:00Z` |

Railway redeploys on the change, which satisfies the restart requirement.
**Nothing else needs touching.** Everything else is already deployed and
verified below.

### After the restart, before trusting it

- `L5_AUTHORIZATION_PREFLIGHT` → `ok: true`, `fail: []`
- `campaign.document_sha256` == `74ab4250…`
- `campaign.approval_state` == `approved`, `permits_this_window` true
- `L5_CAMPAIGN_BASELINE` appears once on the first arm with
  `"captured": true` and an `account_wide_realized` figure
- `L5_LIVE_GATES.campaign_budget.campaign_id` is the new nonce, and
  `released=true` appears exactly once on the arm that takes the budget over
  from the Stage 1 ledger

---

## 3. What is already deployed and verified

`L5_EFFECTIVE_RUNTIME_CONFIG` at 2026-10-24T23:49:51Z: **`PASS: true`,
`checked: 25`, `failed: []`.**

### The five you approved

| Variable | Set to | Verified in production as |
|---|---|---|
| `L5_EXPERIMENT_ID` | `l5-ts-nokey-20261025T0000Z` | `experiment_id: l5-ts-nokey-20261025T0000Z` |
| `L5_TS_TWO_ORDER_PAIR` | `1` | `ts_two_order_pair_enabled: true` |
| `L5_TS_ROUND_ALLOCATIONS` | `2` | `ts_round_allocations: 2` |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | `2` | `ts_opening_leg_max_contracts: 2` |
| `L5_TS_MIN_FIRST_LEG_PRICE` | `0.20` | `ts_min_first_leg_own_price: 0.20` |

### The sixth, which activates the bound you required

| Variable | Set to | Verified as |
|---|---|---|
| `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD` | `5.00` | `campaign_additional_loss_usd: effective 5.00, required 5.00, ok true` |

This is the variable that switches on the independent $5 bound. It is not a
capital increase and not a sixth behaviour change — without it the new gate is
inert, and your instruction was that the campaign have an independently
enforced maximum $5 additional loss. Flagging it because you approved five
items and I set six.

### Every limit you said to keep, unchanged

`aggregate_exposure_usd` **5** and `aggregate_exposure_in_force_usd` **5**
(they agree, so the service is correctly restarted) · `max_worst_case_deficit_usd`
**2.00** · `daily_realized_loss_halt_usd` **5** · `max_live_orders` **1** ·
`maker_pair_ceiling` **0.96** · `max_windows` **8/8** · residual cap **3
contracts** (`PMUS_RESIDUAL_CAP_K=2`, ABSOLUTE binds) · `L5_CAMPAIGN_BUDGET_USD`
**$29.30** (untouched) · `L5_MAX_CUMULATIVE_LOSS_USD` **20** ·
`L5_MAX_L5_DAILY_LOSS_USD` **5** · `L5_MAX_L5_CUMULATIVE_LOSS_USD` **20** ·
`maker_only_forbids_defensive_crossing` **true** ·
`mandate_signing_key_absent_from_runtime` **true**.

The independent watchdog `l5-halt-watchdog` was not touched: its deployment
`642a2fbb` (2026-10-24T22:09:33Z) is still SUCCESS on its own branch and its
own volume. `halt_gate` reads **PASS, permitted true, why null**.

### The two checks you asked for, confirmed

**16 OPEN submissions remaining.** `L5_LIVE_HB` at 2026-10-24T23:51:37Z on the
new deployment:

```
submissions_spent : 0        max_submissions : 56
class_budgets     : OPEN     {max 16, spent 0,  remaining 16}
                    COMPLETE {max 24, spent 0,  remaining 24}
                    REPLACE  {max 32, spent 0,  remaining 32}
```

Independently reproduced against the real `class_budget_report`: a directory
holding the old experiment's claim locks reads `OPEN (32, 0)`,
`COMPLETE (18, 6)`, `REPLACE (2, 30)` — production's exact figures, which
validates the reproduction — while the new experiment id reads
`OPEN (0, 16)`, `COMPLETE (0, 24)`, `REPLACE (0, 32)`.

**Both maker slots available under the existing safety conditions.** Run
against the real `second_leg_slot_refusal` with production's settings:

```
DOWN second leg, UP resting and claimed this round  -> SLOT GRANTED
UP again (same side = EXTEND)                       -> RESTING_ORDER_IS_THE_SAME_SIDE
DOWN with nothing resting                           -> NO_FIRST_LEG_RESTING
DOWN but DOWN already claimed this round            -> THIS_SIDE_ALREADY_CLAIMED
DOWN, resting order in another market               -> RESTING_ORDER_IS_ANOTHER_MARKET

max_live_orders = 1; ceiling with the pair slot = 2
```

So the slot is granted in exactly the state round 1 creates, and every safety
condition still refuses. Note the one real limitation: a same-side **EXTEND**
cannot use the second slot, so EXTEND and REDUCE remain available sequentially
through the single ordinary slot, not concurrently.

---

## 4. The new $5 campaign bound — what it does

`src/l5_campaign_baseline.py`. At the first arm of a campaign it persists the
starting **cash** (from `/v1/account/balances`, currently `$341.18605`,
`read_ok: true`), **inventory**, **open orders** and **realized loss**, then
bounds

```
max(0, baseline_realized - current_realized) + max(0, open book worst case)
```

at a **$5 code constant**. The open-book term is the worst-case deficit over
this order *plus the worst subset of still-fillable resting orders*, so
outstanding obligations are counted as you required.

**It does not rely on the $20 lifetime gate**, which was your explicit
instruction. It computes its own worst case rather than reusing the $2 deficit
gate's result, so it binds even if that gate were unconfigured.

**It uses the deficit, not gross cost**, which is what lets it refuse new risk
without blocking the completions that reduce it. Measured:

| Action | Open worst case | Additional loss | Verdict |
|---|---|---|---|
| First leg 2 @ 0.30 from flat | +0.64 | 0.64 | allowed |
| Fresh 2 @ 0.80 when $4 already lost | +1.64 | **5.64** | **refused** |
| Completion 2 @ 0.36 against 2 UP @ 0.60, $3.38 down | **−0.63** | 3.3787 | allowed |

**Nothing is reset.** The baseline records what realized *was*; the lifetime
ceiling still reads the full figure on every evaluation, so the campaign is held
to both and the $5 binds first. A campaign may not re-measure its own start —
`capture` is idempotent, so no restart, re-arm or later window moves the zero
point. A different nonce captures a fresh baseline, which costs an owner action
because the nonce comes from `L5_CAMPAIGN_APPROVAL`, which the executor cannot
write.

Fail-closed on every unknown: no baseline, a foreign campaign's baseline, no
campaign id, an unreadable stored figure, a missing current realized figure, a
missing inventory, an uncomputable book, or a store that raises.

31 new tests in `tests/test_l5_campaign_baseline.py`.

---

## 5. Honest notes

**I introduced 26 regressions and the full-suite comparison caught them.** My
first version of the baseline work made `capture_campaign_baseline` a method on
the runner, and the arm path is exercised with partial runner doubles — so it
raised `AttributeError` and took arming down across 24 tests. That was not a
test artefact: any runner shape missing an attribute would have done the same in
production, and a baseline capture must never break arming. It is now a
module-level function with every read guarded. The second cluster was the new
module missing its Dockerfile `COPY` line — the same shape as
`l5_mandate_signature` earlier on this branch. Both fixed in `749d4f67`.

**Final regression state:** 58 failed / 23 errors, the **identical set** to the
base tree, with 13229 passed against the base's 13196 — the +33 being my 31 new
baseline tests and the 2 earlier verifier tests. No new failures. The 58 are
pre-existing, in the Gemini, legacy-arb and lineage areas; none in the L5 live,
authorization or budget path. The suite is not green and I am not claiming it is.

**Two open accounting items** are recorded in
`L5_OUTSTANDING_ACCOUNTING_ISSUES.md`, neither reset nor reclassified:

- **A1** — the historical attribution identity does not close
  (`identity_holds: false`; `unknown_unattributed −$84.62` against an anchored
  `account_wide −$11.6213`; `manual_realized null`; 49 legs at a $1/share upper
  bound, 2 of them unestablished). The gates read the anchored figure. The new
  campaign bound does not depend on this being resolved.
- **A2** — `require_cash_evidence` is implemented, tested, and **called from
  nowhere**, so the arm-time cash precondition its docstring claims is not in
  force. The figure it would check is present and healthy
  (`available_cash_usd 341.18605`), and `INSUFFICIENT_AVAILABLE_CASH` inside
  `refuse()` *is* live. Not wired, deliberately: it would add a new arming
  blocker you have not approved, immediately before a bounded campaign.

**What this campaign can produce:** 16 first legs and 24 completions across 8
windows inside 4 hours — roughly 40 orders. Hundreds of fills is repetition of
this bounded campaign with a fresh nonce and experiment id each time, not a
larger one. No budget has been widened to shortcut that, and nothing forces a
trade to reach a count.

**No realized profit is claimed.** The $480/day figure remains a target to
measure against. Results will be labelled realized only after settlement.
