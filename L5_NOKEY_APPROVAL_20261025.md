# L5 — FINAL NO-KEY LIVE APPROVAL

**Prepared 2026-10-24. Nothing has been written to Railway and nothing is armed.**
No signing key is involved anywhere in this document.

Evidence basis: the live `l5-pmus-live` service in Railway project
`50fb7357…`, environment `production`, deployment `224eace9…`, log lines
`L5_LIVE_START`, `L5_AUTHORIZATION_PREFLIGHT`, `L5_LIVE_GATES`,
`L5_LIVE_RECONCILE`, `L5_COST_BASIS_AUDIT`, `L5_LIVE_HB` and
`L5_EFFECTIVE_RUNTIME_CONFIG` at 2026-10-24T22:17–22:33Z.

---

## 0. The eight items, stated plainly

**0.1 — New experiment ID and campaign nonce**

| | Value |
|---|---|
| **New experiment ID** | `l5-ts-nokey-20261025T0000Z` |
| **Must NOT be reused** | `l5-1w-20261022T191823Z` — its OPEN budget reads `{max 16, spent 32, remaining 0}` |
| **Campaign nonce** | `l5-btc15m-nokey-20261025-7c4f9a21` |
| **Derived claim id** | `camp1.l5-btc15m-nokey-20261025-7c4f9a21` |

The two are independent keys and both must be new. The nonce resets the
*campaign* ledger; the experiment ID resets the *submission class budgets*.
Changing only one leaves the other exhausted.

**0.2 — Exact approval JSON and expiry** — full bytes and digest in §6.
Expiry is `min(expires_utc, issued_utc + 4h)`; the same 4-hour window is also
the staleness bound, so the document must be issued within 4 hours of arming.
`L5_LIVE_AUTH_NOT_AFTER` is a separate, independent outer deadline.

**0.3 — Maximum additional loss from the new campaign's starting point**

> **$8.3787 total, and $5 within any one UTC day.**

Four loss ceilings stack. The binding one is the account-wide lifetime gate:
`loss = max(0, −account_wide_realized) + max(0, unrealized)`, refused when
`loss >= $20`. With `account_wide_realized = −$11.6213`, the campaign is
refused once additional loss reaches **$8.3787**. Unrealized is included, so
an open book about to lose counts against it — not only settled losses.

| Ceiling | Limit | Currently used | Additional allowed |
|---|---|---|---|
| Account-wide lifetime | $20 | $11.6213 | **$8.3787 ← binds** |
| L5-scoped cumulative | $20 | $3.2265 | $16.7735 |
| L5-scoped daily | $5 | resets each UTC day | **$5/day ← binds** |
| Account-wide delta | not configured | — | inert |

**0.4 — Is $29.30 a legacy accounting requirement or does it permit more risk?**

> **It is a legacy accounting requirement. It permits no additional live risk.**

`L5_CAMPAIGN_BUDGET_USD` is a worst-case-*loss* ceiling, and $11.6213 of it is
already consumed by history before any new order exists. Nominal room is
$29.30 − $11.6213 = $17.6787, but that room is unreachable: the account-wide
gate refuses at $8.3787 of additional loss, which is less than half of it. So
$29.30 is sized to *clear history*, not to extend risk — and the number that
actually bounds the campaign is $8.3787, set by a limit you are not changing.

It cannot be lowered to $5, because the same historical $11.6213 is counted
against it. Proven against the real ledger:

```
L5_CAMPAIGN_BUDGET_USD=5.00    used=11.6213  first order 2@0.30 -> CAMPAIGN_WORST_CASE_BUDGET:12.2213>5.00
L5_CAMPAIGN_BUDGET_USD=11.70   used=11.6213  first order 2@0.30 -> CAMPAIGN_WORST_CASE_BUDGET:12.2213>11.70
L5_CAMPAIGN_BUDGET_USD=29.30   used=11.6213  first order 2@0.30 -> ALLOWED
```

Setting it to $5 does not tighten anything. It brings the campaign to a halt
before its first order, while leaving every real limit exactly where it is.

**0.5 — How $5 instantaneous exposure and the $2 worst-case deficit interact**

They bound different axes and neither subsumes the other.

- **$5 `max_cash_deployed_usd` — gross cash.** `filled (unreleased) +
  still-fillable outstanding + the proposed order`, account-wide. It caps how
  much money is *out* at once, regardless of whether it is at risk.
- **$2 `max_worst_case_deficit_usd` — net loss on the worse outcome.**
  `C − min(up_qty, dn_qty)`, where `C` is confirmed acquisition cost plus this
  order plus the worst subset of still-fillable outstanding orders, with the
  taker fee charged on every leg.

The division of labour:

| Book shape | Which binds | Why |
|---|---|---|
| Matched two-sided pair | **$5 cash** | a completed pair pays on *both* outcomes, so its deficit is **negative** (−$0.03 at pair 0.96, −$0.11 at 0.92). $2 never obstructs completion. |
| Unmatched one-sided leg | **$2 deficit** | the whole cost is at risk on one outcome. `3 @ 0.80` from flat is refused `WORST_CASE_DEFICIT:2.45>2.00` while still well inside $5 of cash. |

So $2 is the real constraint on *directional* exposure and $5 on *matched
inventory*. Simultaneous fills need no separate treatment: every bound already
charges an outstanding order as though it fills in full, so two resting legs
cost exactly what two filled legs cost.

**0.6 — Do historical realized losses and unknown-basis legs remain in the account-wide gate?**

> **Yes. Both remain included, and nothing has been reset.**

The gate's input is `account_wide_realized = −$11.6213`, derived from an
authoritative cash anchor (`baseline_cash_usd $350.05255` at
2026-10-16T02:45:12Z). That single anchored figure is what produces the
$8.3787 headroom in §0.3. It is account-wide, not L5-attributed: an
unattributable loss is still counted as a loss.

**One thing I will not overstate.** The reported decomposition does not close:

```
identity        : account_wide = verified_l5 + manual + unknown_unattributed
identity_holds  : false
verified_l5_realized           : -3.2265
unknown_unattributed_realized  : -84.6200
  of which pre_baseline_total_l5_share_unknown : -79.0100
  legs_priced_at_one_dollar_upper_bound        : 49
manual_realized : null  (UNATTRIBUTED_RESIDUAL_NONZERO)
```

The $84.62 is larger than the $11.6213 the gate uses, because this is a
**shared account** and the venue does not say who placed a historical fill, so
the residual that would reconcile them is not computable. I therefore cannot
tell you the $84.62 is cleanly netted inside the $11.6213 — only that the gate
reads the *anchored* account-wide figure rather than the sum of these
sub-populations, and that the 49 legs are priced at a $1/share **upper bound**,
which the code labels "an estimate, not a verified realized loss."

If you want that closed before risking more money, it is a separate audit and
I would do it before arming rather than after. I am flagging it rather than
deciding it.

**0.7 — Effective production settings and required restarts** — §7, and the
restart requirement is real: `R.PROBE_LIMITS` is built **once at import** and
`SafetyLimits` is constructed from it, so a variable edit without a restart
leaves the *old* ceiling enforced. Railway redeploys on a variable change,
which satisfies it; confirm in `L5_EFFECTIVE_RUNTIME_CONFIG` rather than
assuming.

**0.8 — Which variables you must set, versus which I can prepare**

| Must be **yours** | Why |
|---|---|
| `L5_CAMPAIGN_APPROVAL` | This *is* the authorization. `EnvApprovalStore.put` is deliberately unimplemented: "the executor may never write its own approval." |
| `L5_LIVE_AUTH_NOT_AFTER` | The absolute deadline bounding the whole mandate. |

| I can prepare on your word | What it is |
|---|---|
| `L5_EXPERIMENT_ID` | budget scope key — resets class budgets |
| `L5_TS_TWO_ORDER_PAIR` | enables the second maker slot |
| `L5_TS_ROUND_ALLOCATIONS` | rounds per window |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | clip ceiling |
| `L5_TS_MIN_FIRST_LEG_PRICE` | first-leg price floor |

**The honest caveat on that split.** I hold Railway write access in this
session (`set-variables`), so I *could* technically write the approval
variable too. Nothing in Railway stops me. What makes the control real is the
executor being unable to write its own approval, plus my not doing it — which
is why I have written nothing and am asking you to set those two yourself. If
you want the control to be structural rather than behavioural, the fix is to
remove my write access to this service, not to add a signing key.

---

## 1. Why the ledger reports $29.30 / $12.6790, and $8.3787

These are three different quantities. Only one of them is a capital limit.

| Figure | What it actually is | Source |
|---|---|---|
| **$5** | `max_cash_deployed_usd` — the **instantaneous** capital ceiling. This is the $5 you approved. | env `L5_MAX_CASH_DEPLOYED_USD`, `L5_MAX_TOTAL_EXPOSURE_USD` |
| **$29.30** | `L5_CAMPAIGN_BUDGET_USD` — a **worst-case-loss** ceiling that spans windows. Not an exposure limit. | env, read on every evaluation |
| **$12.6790** | $29.30 − `peak_usd` $16.6210, the Stage 1 campaign's stored high-water mark | ledger doc |
| **$17.6787** | $29.30 − $11.6213 all-time realized loss = the room the gate computes | `incremental_room_usd` |
| **$8.3787** | $20 `max_cumulative_loss_usd` − $11.6213 = **account-lifetime loss headroom** | `L5_MAX_CUMULATIVE_LOSS_USD` |

The gate computes
`campaign_used = max(0, −realized) + cash_committed(legs) + pending_obligation(outstanding)`
and `cumulative_realized` is passed as `store.realized_today()`, which despite
its name is the **all-time** total, currently **−$11.6213**.

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

**No loss is erased and no history is reset.** Realized losses live in the
executor's store and re-enter the gate on every evaluation. What the release
clears is the ownership claim and the high-water mark — neither of which is a
spending allowance.

---

## 2. Do the 49 unknown-cost-basis legs affect the new campaign?

**Available capital: no. Loss accounting: yes, as described in §0.6.**

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

### What this campaign can actually produce, stated before you authorize it

The operative budgets are `OPEN 16`, `COMPLETE 24`, `REPLACE 32`, backstop
`56`, across `max_windows 8` inside a 4-hour approval. So this campaign
authorizes **at most 16 first legs and 24 completions** — roughly 40 orders,
not hundreds.

Reaching hundreds of real fills is therefore *repetition* of this bounded
campaign — a fresh nonce and a fresh experiment ID each time — not a larger
one. I am not proposing to widen any budget to shortcut that, and the engine
should not force trades to reach a count.

---

## 4. Exact scope

| Field | Value | Enforced by |
|---|---|---|
| Experiment ID | `l5-ts-nokey-20261025T0000Z` | class claim locks |
| Nonce | `l5-btc15m-nokey-20261025-7c4f9a21` | approval doc; single-use claim |
| Series | `cpc-btc-updown-15m-` | code allowlist `ALLOWED_SERIES` |
| Approval expiry | `min(expires_utc, issued_utc + 4h)` | `MAX_CAMPAIGN_S`; also the staleness window |
| Absolute deadline | `L5_LIVE_AUTH_NOT_AFTER`, owner-set | `authorize_arming` step 8 |
| Max windows | **8** | `CA.MAX_WINDOWS = 8`; `L5_LIVE_AUTH_MAX_WINDOWS = 8` |
| Max submissions | **OPEN 16**, COMPLETE 24, REPLACE 32, backstop **56** | class claim locks |
| Instantaneous exposure | **$5** | `max_cash_deployed_usd`, `max_total_exposure_usd` |
| Worst-case deficit | **$2.00** | `L5_MAX_WORST_CASE_DEFICIT_USD` |
| Max additional loss | **$8.3787** total; **$5** per UTC day | $20 − $11.6213; L5 daily halt |
| Residual cap | 3 contracts (absolute binds) | unchanged, `PMUS_RESIDUAL_CAP_K=2` |
| Live orders | 1, raised to 2 only for one bounded pair | `max_live_orders` + pair slot |

---

## 5. Current halt state — nothing for you to clear

```
halt_latch         : CLEAR    (sha256 40883bad…, kind PRESENT, halt false, reason null)
halt_gate preflight: PASS     (permitted true, why null)
halt_why           : null
```

The heartbeat's top-level `"halt": true` is **not** a latch. It is literally
`submission_gate().blocked`, whose reason is `NOT_ARMED` — the expected state
for an unarmed service. It clears by arming, not by resetting anything. No
halt is being cleared by this document.

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

**`issued_utc` must be within 4 hours of arming.** If you set this later than
2026-10-25T00:00Z, regenerate with the command below and use the digest it
prints instead.

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
   The current **stale** document's digest is
   `1bd9a3a61b1e0a021cfebf0ffce3a32b35a79dde046683b06a0c73e8cc4f525c`; seeing
   that value after the paste means the variable did not change.

The document's contents, nonce included, are never logged — only the digest.

---

## 7. Exact Railway actions

Service `l5-pmus-live` (`87f1834e…`), environment `production`.

### You set these two

| Variable | Value |
|---|---|
| `L5_CAMPAIGN_APPROVAL` | the JSON in §6 |
| `L5_LIVE_AUTH_NOT_AFTER` | a future UTC instant, e.g. `2026-10-25T04:00:00Z` (currently EXPIRED by ~5h) |

### I can set these five on your word

| Variable | Value | Currently |
|---|---|---|
| `L5_EXPERIMENT_ID` | `l5-ts-nokey-20261025T0000Z` | `l5-1w-20261022T191823Z` (OPEN exhausted) |
| `L5_TS_TWO_ORDER_PAIR` | `1` | `false` — no second maker slot without it |
| `L5_TS_ROUND_ALLOCATIONS` | `2` | `1` |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | `2` | `3` |
| `L5_TS_MIN_FIRST_LEG_PRICE` | `0.20` | `0` |

> **`L5_EXPERIMENT_ID` is not optional, and it is the one that would have
> wasted the campaign.** Class budgets are counted from durable claim-lock
> files keyed on the **experiment ID**, not the campaign nonce. The current
> experiment reports `OPEN: {max 16, spent 32, remaining 0}`. A new nonce does
> **not** reset that. Arming would succeed — the preflight does not check class
> budgets — and then every first leg would be refused `OPEN_BUDGET_EXHAUSTED`.

### Do not change these

`L5_CAMPAIGN_BUDGET_USD` (**keep $29.30** — §0.4 proves $5 refuses
everything), `L5_MAX_CASH_DEPLOYED_USD` 5, `L5_MAX_TOTAL_EXPOSURE_USD` 5,
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
- `observed_not_graded.experiment_id` is `l5-ts-nokey-20261025T0000Z`
- `L5_LIVE_GATES.campaign_budget.campaign_id` is the **new** nonce, and
  `released=true` appears once on the arm that takes the budget over
- `ts_two_order_pair_enabled: true`, `ts_round_allocations: 2`,
  `ts_opening_leg_max_contracts: 2`, `ts_min_first_leg_own_price: 0.20`

---

## 8. Effective production configuration, as the money path reports it

From `L5_EFFECTIVE_RUNTIME_CONFIG` at 2026-10-24T22:21:50Z (23 graded rows):

| Row | Effective |
|---|---|
| `submission_budget_open` / `complete` / `replace` | 16 / 24 / 32 |
| `submission_backstop` | 56 |
| `aggregate_exposure_usd` | **5** |
| `daily_realized_loss_halt_usd` | 5 |
| `max_live_orders` | 1 |
| `maker_pair_ceiling` | 0.96 |
| `max_worst_case_deficit_usd` | **2.00** |
| `max_windows_authorization` / code ceiling | 8 / 8 |
| `campaign_duration_ceiling_s` | 14400 |
| `window_strategy` | TWO_SIDED |
| `series_allowlist` | `cpc-btc-updown-15m-` |
| `maker_only_forbids_defensive_crossing` | true |
| `mandate_signing_key_absent_from_runtime` | true |

Reported, not graded: `shadow_only false`, `defensive_execution_enabled false`,
`mandate_enforcing false`, `mandate_trusted_key_ids (none)`,
`ts_two_order_pair_enabled false`, `ts_round_allocations 1`,
`ts_opening_leg_max_contracts 3`, `ts_min_first_leg_own_price 0`,
`experiment_id l5-1w-20261022T191823Z`.

No signing key is configured and none is required: `MANDATE_PUBKEYS` is empty,
so the Ed25519 gate is inert. The no-key `L5_CAMPAIGN_APPROVAL` path is the
authorization mechanism.

---

## 9. What I changed in this pass, and what I did not

**Changed** (committed to `claude/l5-defensive-8window-prod` as `2a75167b`,
not deployed):

- `src/l5_runtime_config_verify.py` — `aggregate_exposure_usd` required $20
  while production runs the approved $5, so `L5_EFFECTIVE_RUNTIME_CONFIG`
  reported `PASS: false` permanently, hiding any real regression behind a
  standing failure. Now $5. The old comment justifying $20 was wrong on the
  mechanism: it claimed the row had to equal `CA.MAX_TOTAL_EXPOSURE_USD`
  because `_looser()` refuses arming on disagreement, but `_looser` flags a
  field only when the deployed value is **greater** than the approved one.
- Split into `aggregate_exposure_usd` (the variable, via the accessor) and
  `aggregate_exposure_in_force_usd` (the dict frozen at boot), both graded
  against $5. A disagreement is exactly the "variable changed, service not
  restarted" state, which now fails a row instead of being discovered by an
  order admitted under an unapproved ceiling.
- Added `L5_MAX_TOTAL_EXPOSURE_USD` to `SNAPSHOT_ALLOWED`; it was missing, so
  `verify_snapshot` silently dropped the name and reported the running
  process's boot value as though predicted from the snapshot.
- Tests: the frozen dict is pinned in an autouse fixture, fixing a latent
  order-dependence (these tests passed alone and failed in the full suite on
  import order), plus two new tests for the restart-pending state and for a
  tighter-than-constant ceiling passing.

**Regression comparison**, full suite, this tree vs its base: **58 failed / 23
errors in both, with identical sets**; HEAD passes 13198 vs 13196, the +2 being
these new tests. **No new failures are attributable to this patch.** The 58 are
pre-existing and sit in the Gemini, legacy-arb and lineage areas
(`test_gemini_terms_gate` 17, `test_spread_scanner` 9, `test_orderbook` 5,
`test_r1_source_lineage` 4, `test_pmus_auth` 4, others ≤3) — none in the L5
live, authorization or budget path. The suite is not green and I am not
claiming it is.

**Not done, deliberately:** `L5_CAMPAIGN_APPROVAL` has not been written, no
Railway variable has been changed, nothing is armed, no realized-loss history
has been reset, no account-wide loss limit has been raised, and no halt has
been cleared.

**Not claimed:** no realized P&L is asserted anywhere in this document. The
$480/day figure remains a target to measure against. Maker rebates stay
separate from trading P&L and count only when credited — the audit shows 831
credited rebates and `commission_sum_usd −$5.8800` historically, which is
evidence the account *can* earn them, not an assumption that this campaign
will. Realized profit will be labelled only after settlement.
