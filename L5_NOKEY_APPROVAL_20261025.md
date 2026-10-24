# L5 — FINAL NO-KEY LIVE APPROVAL

**Prepared 2026-10-24. Nothing has been written to Railway and nothing is armed.**
No signing key is involved anywhere in this document.

Evidence basis: the live `l5-pmus-live` service in Railway project
`50fb7357…`, environment `production`, deployment `224eace9…`, log lines
`L5_LIVE_START`, `L5_AUTHORIZATION_PREFLIGHT`, `L5_LIVE_GATES`,
`L5_LIVE_RECONCILE`, `L5_COST_BASIS_AUDIT`, `L5_LIVE_HB` and
`L5_EFFECTIVE_RUNTIME_CONFIG` at 2026-10-24T22:17–22:33Z.

---

## 1. Why the ledger reports $29.30 / $12.6790, and $8.3787

These are three different quantities. Only one of them is a capital limit.

| Figure | What it actually is | Source |
|---|---|---|
| **$5** | `max_cash_deployed_usd` — the **instantaneous** capital ceiling. This is the $5 you approved. | env `L5_MAX_CASH_DEPLOYED_USD`, `L5_MAX_TOTAL_EXPOSURE_USD` |
| **$29.30** | `L5_CAMPAIGN_BUDGET_USD` — a **worst-case-loss** ceiling that spans windows. Not an exposure limit. | env, read on every evaluation |
| **$12.6790** | $29.30 − `peak_usd` $16.6210, the Stage 1 campaign's stored high-water mark | ledger doc |
| **$17.6787** | $29.30 − $11.6213 all-time realized loss = the room the gate actually computes | `incremental_room_usd` |
| **$8.3787** | $20 `max_cumulative_loss_usd` − $11.6213 all-time realized = **account-lifetime loss headroom** | `L5_MAX_CUMULATIVE_LOSS_USD` |

The gate computes
`campaign_used = max(0, −realized) + cash_committed(legs) + pending_obligation(outstanding)`
and `cumulative_realized` is passed as `store.realized_today()`, which despite
its name is the **all-time** total, currently **−$11.6213**
(`L5_LIVE_RECONCILE.realized_today`).

### Consequence, and it is the opposite of what it looks like

**`L5_CAMPAIGN_BUDGET_USD` must not be lowered to $5.** Because $11.6213 of
historical realized loss is counted against it before any new order exists, a
$5 campaign budget refuses the very first order. Run against the real ledger:

```
L5_CAMPAIGN_BUDGET_USD=5.00    used=11.6213  first order 2@0.30 -> CAMPAIGN_WORST_CASE_BUDGET:12.2213>5.00
L5_CAMPAIGN_BUDGET_USD=11.70   used=11.6213  first order 2@0.30 -> CAMPAIGN_WORST_CASE_BUDGET:12.2213>11.70
L5_CAMPAIGN_BUDGET_USD=29.30   used=11.6213  first order 2@0.30 -> ALLOWED
```

Leaving it at $29.30 does not widen capital at risk by one cent — $5
instantaneous exposure and the $2.00 worst-case deficit bound do that work.
The binding loss limits remain **$5/UTC-day** and **$8.3787 account-lifetime**.

### The ledger lock, and why it clears itself

A *new* nonce is refused while the Stage 1 ledger is open:

```
refuse_amount(new nonce) -> CAMPAIGN_BUDGET_LOCKED:camp1.l5-btc15m-stage1-20261024T1324Z-8a56b1d9
```

`authorize_arming` calls `release_stale()` immediately before claiming, and it
releases only on venue-reconciled evidence of nothing outstanding and nothing
unsettled. Production supplies exactly that evidence today:

- `open_orders_at_venue: 0`, `live_orders_pending_terminal: []` → `outstanding = []`
- `store.committed()` is `{}` (`L5_LIVE_HB.committed_usd == {}`) → `legs = []`
- the two stranded markets (`…10-12-0030z|DOWN`, `…10-14-2230z|UP`) are both in
  `retired_markets`, so they are settled, not unsettled exposure

Replayed against the real ledger code, the handover completes:

```
refuse_amount(new)  -> CAMPAIGN_BUDGET_LOCKED:…stage1…
release_stale(new)  -> {'ok': True, 'released': True, 'closed_campaign_id': '…stage1…'}
refuse_arming(new)  -> None
open_or_resume(new) -> budget_usd 29.30, peak_usd 0, resumed False
first order 2@0.30  -> ALLOWED
```

No loss is erased: realized losses live in the executor's store and re-enter
the gate on every evaluation. What the release clears is the ownership claim
and the high-water mark.

---

## 2. Do the 49 unknown-cost-basis legs affect the new campaign?

**Available capital: no. Loss accounting: only through a figure already counted.**

From `L5_COST_BASIS_AUDIT` (`writes: "NONE"` — read-only, nothing repaired):

- `unknown_basis_legs: 49`, `establishable: 47`, `unestablished: 2`
- `placeholder_cost_total_usd: $294.09` vs `actual_cost_total_usd: $50.6148`,
  `overstatement_usd: $240.4352` on established legs only
- every sampled leg carries `qty_source: "retired_markets"`,
  `owners: ["UNKNOWN_OWNER"]`, `l5_owned_fills: 0`

They are settled, pre-campaign, and were not placed by this engine. Because
`store.committed()` is `{}` and `cash_committed` counts an unsettled leg in
full but skips settled ones, they contribute **$0** to `campaign_used` and
**$0** to the $5 instantaneous envelope. The $294.09 placeholder figure is a
diagnostic of what a naive repair *would* have assumed; it is not in the live
numbers.

Their only effect is inside the single aggregate −$11.6213, which is already
what sets both the $17.6787 campaign room and the $8.3787 account headroom
above. **Caveat worth stating plainly:** 2 of the 49 remain unestablished, with
no measured cost, and the audit explicitly refuses to treat unknown as zero —
so −$11.6213 carries residual measurement uncertainty on those two legs.

---

## 3. Does TWO_ORDER_PAIR with ROUND_ALLOCATIONS=2 satisfy the $2 deficit bound?

**Yes for every normal action, with one named consequence.** Run against the
real `ValidationGates.refuse()` at q=2, pair cap 0.96,
`max_worst_case_deficit_usd = $2.00`:

```
R1 leg A: 2 @ 0.30 from flat                           ALLOWED
R1 leg B: 2 @ 0.66, leg A 2@0.30 OUTSTANDING           ALLOWED
leg A FILLED (2 UP, cost 0.60); leg B 2 @ 0.36         ALLOWED
R2 leg A on top of a MATCHED 2x2 pair                  ALLOWED
dear side alone: 2 @ 0.80 from flat                    ALLOWED
dear side alone: 3 @ 0.80 from flat                    REFUSED  WORST_CASE_DEFICIT:2.45>2.00:none
R2 2@0.30 with a dear 2@0.80 PENDING-CANCEL            REFUSED  WORST_CASE_DEFICIT:2.30>2.00:up_only
```

Simultaneous fills are already priced: every bound charges an outstanding
order as though it fills in full, so two resting legs cost the same as two
filled legs. Completed pairs have a *negative* deficit (−$0.03 at pair 0.96,
−$0.11 at 0.92) because they pay on both outcomes, so the bound never
obstructs completion.

**The consequence:** round 2 cannot open a new leg while a dear leg is still
pending cancellation. That is a real throughput cost, and it is the correct
behaviour under a $2 bound.

### Live two-sided capability — verified in the executor, not the simulator

`max_live_orders` is **1**, but `SafetyLimits.refuse` computes
`ceiling = max_live_orders + (1 if pair_second_leg_ok else 0)`, and
`l5_two_sided_probe.second_leg_slot_refusal` grants that second slot only when
the flag is on, exactly one order is live, in this market, on the **opposite**
side, claimed in this round, and this side is not. So simultaneous UP/DOWN
resting orders are a real executor slot, conditional on
`L5_TS_TWO_ORDER_PAIR=1` (currently `false`).

**One honest limitation:** a same-side **EXTEND** is explicitly *not* allowed
to use that slot ("a same-side order is an EXTEND, which is a separate
decision with its own economics"). EXTEND and REDUCE both remain available,
but sequentially, through the single ordinary slot — not concurrently with
another resting order.

---

## 4. Exact scope

| Field | Value | Enforced by |
|---|---|---|
| Nonce | `l5-btc15m-nokey-20261025-7c4f9a21` | approval doc; single-use claim |
| Series | `cpc-btc-updown-15m-` | code allowlist `ALLOWED_SERIES` |
| Approval expiry | `issued_utc + 4h`, or the document's `expires_utc`, whichever is sooner | `MAX_CAMPAIGN_S`; also the staleness window |
| Absolute deadline | `L5_LIVE_AUTH_NOT_AFTER` — independent outer bound, owner-set | `authorize_arming` step 8 |
| Max windows | **8** | `CA.MAX_WINDOWS = 8`; `L5_LIVE_AUTH_MAX_WINDOWS = 8` |
| Max submissions | **OPEN 16**, COMPLETE 24, REPLACE 32, backstop **56** | class claim locks |
| Instantaneous exposure | **$5** | `max_cash_deployed_usd`, `max_total_exposure_usd` |
| Worst-case deficit | **$2.00** | `L5_MAX_WORST_CASE_DEFICIT_USD` |
| Max campaign loss | **$5 per UTC day**, and **$8.3787** before the account-lifetime halt | daily halt; `$20 − $11.6213` |
| Residual cap | 3 contracts (absolute binds) | unchanged, `PMUS_RESIDUAL_CAP_K=2` |
| Live orders | 1, raised to 2 only for one bounded pair | `max_live_orders` + pair slot |

---

## 5. Current halt state — nothing for you to clear

```
halt_latch        : CLEAR      (sha256 40883bad…, kind PRESENT, halt false, reason null)
halt_gate preflight: PASS      (permitted true, why null)
halt_why          : null
```

The heartbeat's top-level `"halt": true` is **not** a latch. It is literally
`submission_gate().blocked`, whose reason is `NOT_ARMED` — the expected state
for an unarmed service. It clears by arming, not by resetting anything.

`L5_AUTHORIZATION_PREFLIGHT` names **exactly two** failures blocking arming,
both owner variables:

```
fail: ["absolute_deadline", "campaign_approval"]
  absolute_deadline : EXPIRED, expires_in_s -17843.2   (L5_LIVE_AUTH_NOT_AFTER)
  campaign_approval : approval_stale                   (L5_CAMPAIGN_APPROVAL)
```

Everything else passes: shadow lock not engaged, owner arm token present,
claim TTL 900s, market series, role binding, session id, window budget 8,
halt gate, residual ceiling.

---

## 6. The approval JSON

Exactly these bytes, one line, **no trailing newline**:

```json
{"approved":true,"nonce":"l5-btc15m-nokey-20261025-7c4f9a21","series":"cpc-btc-updown-15m-","issued_utc":"2026-10-25T00:00:00Z","expires_utc":"2026-10-25T04:00:00Z","max_windows":8,"max_total_exposure_usd":"5","max_daily_loss_usd":"5"}
```

- 235 bytes
- `sha256 = 74ab4250c14e8f8d6c7537e66c50792e976a01afe5654ad40a39b31a79fe79f0`
- validated through the real `ApprovalGate`: `approved: True`, nothing clamped,
  `nonce_sid = camp1.l5-btc15m-nokey-20261025-7c4f9a21`

**`issued_utc` must be within 4 hours of arming**, so if you set this later
than 2026-10-25T00:00Z, regenerate it with the command below and use the
digest it prints instead.

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
EOF
```

### SHA-256 verification procedure

1. Compute the digest of what you are about to paste:
   ```bash
   printf '%s' '<the one-line JSON>' | sha256sum
   ```
   `printf '%s'` matters — a trailing newline changes the digest.
2. Paste the JSON into `L5_CAMPAIGN_APPROVAL` in Railway.
3. After the service restarts, read the digest back from the service's own
   telemetry and confirm it matches byte-for-byte:
   - `L5_LIVE_HB` → `campaign.document_sha256`
   - `L5_AUTHORIZATION_PREFLIGHT` → `campaign_approval.document_sha256`

   The gate hashes the raw variable bytes, so a match proves Railway stored
   exactly the document you hashed. A mismatch means the paste was altered
   (whitespace, smart quotes, a trailing newline) — fix it before arming.
   For reference, the current stale document's digest is
   `1bd9a3a61b1e0a021cfebf0ffce3a32b35a79dde046683b06a0c73e8cc4f525c`; seeing
   that value after the paste means the variable did not change.

The document's contents, nonce included, are never logged — only the digest.

---

## 7. Exact Railway actions

Service `l5-pmus-live` (`87f1834e…`), environment `production`.

### Set these seven

| Variable | Value | Why |
|---|---|---|
| `L5_CAMPAIGN_APPROVAL` | the JSON above | currently `approval_stale` |
| `L5_EXPERIMENT_ID` | a **new** id, e.g. `l5-ts-20261025T0000Z` | **see the warning below** |
| `L5_LIVE_AUTH_NOT_AFTER` | a future UTC instant, e.g. `2026-10-25T04:00:00Z` | currently EXPIRED by ~5h |
| `L5_TS_TWO_ORDER_PAIR` | `1` | currently `false`; without it there is no second maker slot and no simultaneous UP/DOWN |
| `L5_TS_ROUND_ALLOCATIONS` | `2` | currently `1` |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | `2` | currently `3` |
| `L5_TS_MIN_FIRST_LEG_PRICE` | `0.20` | currently `0` |

> **`L5_EXPERIMENT_ID` is not optional, and it is the one that would have
> wasted the campaign.** Class budgets are counted from durable claim-lock
> files keyed on the **experiment id**, not the campaign nonce. The current
> experiment `l5-1w-20261022T191823Z` reports
> `OPEN: {max 16, spent 32, remaining 0}`. A new nonce does **not** reset
> that. Arming would succeed — the preflight does not check class budgets —
> and then every first leg would be refused `OPEN_BUDGET_EXHAUSTED`. A fresh
> experiment id restores OPEN 16 / COMPLETE 24 / REPLACE 32.

### Do not change these

`L5_CAMPAIGN_BUDGET_USD` (**keep $29.30** — §1 proves $5 refuses everything),
`L5_MAX_CASH_DEPLOYED_USD` 5, `L5_MAX_TOTAL_EXPOSURE_USD` 5,
`L5_MAX_WORST_CASE_DEFICIT_USD` 2.00, `L5_MAX_CUMULATIVE_LOSS_USD` 20,
`L5_MAX_L5_DAILY_LOSS_USD` 5, `L5_MAX_L5_CUMULATIVE_LOSS_USD` 20,
`L5_MAX_LIVE_ORDERS` 1, `PMUS_RESIDUAL_CAP_K` 2,
`L5_LIVE_AUTH_MAX_OPEN` 16 / `MAX_COMPLETE` 24 / `MAX_REPLACE` 32 /
`MAX_SUBMISSIONS` 56 / `MAX_WINDOWS` 8, `L5_WINDOW_STRATEGY` TWO_SIDED,
and the halt watchdog's own service and variables.

### A restart is required, not just a variable edit

`R.PROBE_LIMITS` is built **once at import** and `SafetyLimits` is constructed
from it, so the ceiling in force is the one the process booted with. Editing a
variable without a restart leaves the old limit enforced. Railway redeploys on
a variable change, which satisfies this — but confirm it in
`L5_EFFECTIVE_RUNTIME_CONFIG` rather than assuming.

### After the restart, confirm before arming

- `L5_EFFECTIVE_RUNTIME_CONFIG` → `PASS: true`, `failed: []`
- `L5_AUTHORIZATION_PREFLIGHT` → `ok: true`, `fail: []`
- `campaign.document_sha256` matches your computed digest
- `campaign.class_budgets.OPEN.remaining` is **16**, not 0
- `L5_LIVE_GATES.campaign_budget.campaign_id` is the **new** nonce and
  `released=true` appears once on the arm that takes the budget over
- `observed_not_graded` shows `ts_two_order_pair_enabled: true`,
  `ts_round_allocations: 2`, `ts_opening_leg_max_contracts: 2`,
  `ts_min_first_leg_own_price: 0.20`

---

## 8. What I changed in this pass, and what I did not

**Changed** (committed to the branch, not deployed):

- `src/l5_runtime_config_verify.py` — `aggregate_exposure_usd` required $20,
  so production reported `PASS: false` permanently against the $5 you
  approved, hiding any real regression behind a standing failure. Now $5. The
  old comment claimed the row had to equal `CA.MAX_TOTAL_EXPOSURE_USD` because
  `_looser()` refuses arming on disagreement; that is wrong — `_looser` flags a
  field only when the deployed value is **greater** than the approved one, so a
  tighter deployed ceiling arms normally.
- Split the row in two: `aggregate_exposure_usd` (the variable, via the
  accessor) and `aggregate_exposure_in_force_usd` (the dict frozen at boot).
  Both graded against $5. They agree on a correctly restarted service, and a
  disagreement is exactly the "variable changed, service not restarted" state —
  which now fails a row instead of being discovered by an order admitted under
  an unapproved ceiling.
- Added `L5_MAX_TOTAL_EXPOSURE_USD` to `SNAPSHOT_ALLOWED`. It was missing, so
  `verify_snapshot` silently dropped it and reported the running process's boot
  value as though predicted from the snapshot.
- Tests: the fixture now pins the frozen dict (these tests passed alone and
  failed in the full suite purely on import order — a latent order-dependence,
  not a new one), plus two new tests for the restart-pending state and for a
  tighter-than-constant ceiling passing.

**Not done, deliberately:** `L5_CAMPAIGN_APPROVAL` has not been written, no
variable has been changed in Railway, and nothing is armed. Per your
instruction, that waits on your approval of this document.

**Still unproven and not claimed:** no realized P&L is asserted anywhere here.
The $480/day figure remains a target to measure against. Maker rebates stay
separate from trading P&L and are counted only when credited — the audit shows
831 credited rebates and `commission_sum_usd −$5.8800` historically, which is
evidence the account *can* earn them, not an assumption that this campaign will.
