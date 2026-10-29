# Campaign 9 preflight

Owner approved the unchanged Campaign 8 risk envelope on 2026-10-29. This
records every activation requirement verified before arming. **No
experimental trading logic. No limit expanded. No service created.**

## Authorization document

Minted 2026-10-29T11:04:42Z with the owner's approved parameters:

```
ASK_WINDOWS=8 ASK_EXPOSURE_USD=20 ASK_DAILY_LOSS_USD=5 ASK_DURATION_S=14400
ASK_OPEN=40 ASK_COMPLETE=40 ASK_REPLACE=64 ASK_TOTAL=72
```

| field | value |
|---|---|
| `document_sha256` | **`3bd752ba2e1c754c3f2f8789977594c74606f1db9d885d969b5141826759c766`** |
| `gate_says_approved` | **true** (verified against the real `ApprovalGate`, not the mint tool) |
| `gate_why` | `null` |
| `issued_utc` | `2026-10-29T11:04:42.510488+00:00` |
| `expires_utc` | **`2026-10-29T15:04:42.510488+00:00`** |
| `max_windows` | 8 |
| `max_total_exposure_usd` | 20 |
| `max_daily_loss_usd` | 5 |
| `max_live_orders` | 1 |
| `pair_cost_ceiling` | 0.96 |
| `series` | `cpc-btc-updown-15m-` |
| `strategy` | `TWO_SIDED` |
| submission budgets | open 40 / complete 40 / replace 64 / **total 72** |

`clamped` is **false on every field** (`expires_utc`,
`max_daily_loss_usd`, `max_total_exposure_usd`, `max_windows`), so the
effective scope equals the approved envelope — nothing was silently
reduced by the code ceilings in `src/l5_campaign_approval.py`.

The nonce (37 chars) was written to the file only and has not entered this
transcript, any log, or the repository. `git status --porcelain` is clean,
so no credential is staged.

## Activation ordering

`L5_LIVE_AUTH_NOT_AFTER` was set to `2026-10-29T15:04:42.510488+00:00`
**before** the document is installed. This is the rule recorded in
`L5_STATUS.md` after Campaign 7 deadlocked: `mandate_scope()` digests
`max_windows` and `not_after` from the deployed environment into the
immutable window claim, so changing either after the document is in force
raises `MANDATE_SCOPE_CHANGED`. **Do not touch these variables while the
campaign runs.**

The other four authorization variables are unchanged from Campaign 8
(`MAX_WINDOWS=8`, `MAX_SUBMISSIONS=72`, `MAX_OPEN=40`, `MAX_COMPLETE=40`,
`MAX_REPLACE=64`); Campaign 8's own `L5_LIVE_ARMED` scope record confirms
`max_windows: "8"` was read from the environment. Only `NOT_AFTER` needed a
new value, so exactly one variable was written and one redeploy triggered.

## Deployment

| | |
|---|---|
| commit | **`202456d325f050bc356b0a1cb1dba76864991ea7`** |
| branch | `claude/l5-narrow-campaign-prep` |
| previous deployment (ran Campaign 8) | `7bf2ad39-6cb6-4028-949b-1b3a1fa066de` |
| Campaign 9 deployment | `e0fc7bc1-4161-4ae8-9d90-1834e7d051ed` |

**Same commit.** The redeploy carries only the `NOT_AFTER` change, so the
trading code is byte-identical to the Campaign 8 baseline. That is what
makes Campaign 9 a clean comparison rather than a confounded one.

## Risk limits verified live at 11:00:04Z (`L5_LIVE_GATES`)

| gate | value | approved |
|---|---|---|
| `max_cash_deployed_usd` | 12.00 | ✓ |
| `max_unmatched_contracts` | 5 | ✓ |
| `max_l5_daily_loss_usd` | 5 | ✓ |
| `max_l5_cumulative_loss_usd` | 20 | ✓ |
| `max_worst_case_deficit_usd` | 3.00 | ✓ |
| `cash_policy` | NO_RECYCLING | ✓ |
| `halt_gate_configured` | **true** | ✓ |
| `journal_errors` | **0** | ✓ |
| `campaign_id` | `null` (Campaign 8 released) | ✓ |

## Reconciliation at entry

Campaign 8 closed fully settled: cash **343.21615**, unchanged from
03:24:02Z through 11:00Z (7.6 hours), no open positions, no unresolved
orders, `all_time_realized_usd` −9.59120000. That is the Campaign 9 cash
baseline.

## UTC-day boundary

Expiry **15:04:42Z** is inside UTC day 2026-10-29, so the $5 daily-loss
halt cannot reset mid-campaign. Eight 15-minute windows need 2h, so all
eight fit if the document is installed by **~13:00Z**; later installation
simply runs fewer windows and stops cleanly at expiry.

## One residual risk, with its signature

`release_stale` must take the budget over from Campaign 8, whose ledger
still reads `closed: false` with `peak_usd 22.2436` of the $29.30 budget.
It refuses unless the store shows nothing outstanding and nothing
unsettled, and a market holding unmatched inventory cannot retire.

- **Why it should pass:** Campaign 8's own first arm released Campaign 7's
  claim by the same path 9 hours ago, and the account is now fully settled
  and flat. `refuse_arming` also passes on its own terms (peak 22.2436 <
  29.30 budget).
- **If it fails:** the first `L5_LIVE_ARMED` record will carry
  `budget_release.reason` of `CAMPAIGN_UNSETTLED_EXPOSURE:N` or
  `CAMPAIGN_OUTSTANDING_ORDERS`, and orders will be refused
  `CAMPAIGN_BUDGET_LOCKED`. **This is fail-closed: arming refuses and no
  capital moves.** `budget_release.released: true` appears exactly once, on
  the arm that takes the budget over.

Campaign 8's arms 2–6 all reported `CANNOT_RELEASE_OWN_OPEN_CAMPAIGN`,
which is the normal reading once a campaign owns its own budget.

## Owner action required

The nonce may not pass through this session's context, so installation is
the owner's step:

> Set Railway variable **`L5_CAMPAIGN_APPROVAL`** on service
> `l5-pmus-live` (`87f1834e-530e-4fc4-a310-91c8d3add196`), environment
> `production` (`f3642d27-26dc-4e21-9170-bc76290c076f`), to the **entire
> contents** of
> `/tmp/claude-0/-home-user-kalshi-arb-bot/1fb75334-871b-5197-87bf-f412fbdb580d/scratchpad/campaign9_approval.json`
> (628 bytes, one line of JSON).

Writing the variable triggers the redeploy that puts it in force. After
that this session verifies the installed fingerprint matches
`3bd752ba…` and runs the campaign without per-window approval.

## Capture plan

`analysis/l5/capture_decision_tape.py` — append-only, idempotent, polled at
**≤30 minutes** while armed against the observed ~1.6h `L5_LIVE_DECISION`
retention, committing each poll. Labels captured: `L5_LIVE_DECISION`,
`L5_FILL_CALIB_PRESUBMIT`, `L5_FILL_CALIB_FILL`, `L5_FILL_CALIB_TERMINAL`,
`L5_FILL_CALIB_FEES_ACTUAL`, `L5_LIVE_CASH`, `L5_LIVE_GATES`,
`L5_LIVE_ARMED`, `L5_LIVE_HB`.

## Pre-registered primary metric

Every inter-order gap longer than 60s classified as exactly one of:

1. **no L5 intent** — no admitted action in the gap
2. **risk or strategy gate refusal** — intent existed and was refused; the
   gate is named
3. **existing resting order** — an order was already live
4. **no economically acceptable quote** — intent existed, no gate refused,
   and no pair at or below the ceiling was available at the book

Secondary, directly comparable to Campaign 8: orders/window, idle-gap
distribution split by whether the previous order filled or cancelled,
pairs/window, quantity-weighted pair cost, fill rate, and completion
attempts in every window that strands.

---

# Pre-arming verifications requested by the owner

## Experimental strategy flags: all OFF, verified at the accessor

From `L5_EFFECTIVE_RUNTIME_CONFIG` on deployment `e0fc7bc1` at 11:16:17Z.
Its own label: *"every row is the value the money path's own accessor
returns, not the text of a variable."* So these are effective values, not
variable strings.

| flag | source | effective |
|---|---|---|
| **`ts_two_order_pair_enabled`** | `TS.two_order_pair_enabled()` | **false** |
| **`defensive_execution_enabled`** | `DEF.enabled()` | **false** |
| `shadow_only` | `R.shadow_only()` | false (live, as intended) |
| `mandate_enforcing` | phase-1 constant | false |

Graded rows confirming nothing else drifted — all `ok: true`:

| check | effective | required |
|---|---|---|
| `window_strategy` | TWO_SIDED | TWO_SIDED |
| `maker_pair_ceiling` | 0.96 | 0.96 |
| `max_live_orders` | 1 | 1 |
| `series_allowlist` | `["cpc-btc-updown-15m-"]` | same |
| `maker_only_forbids_defensive_crossing` | true | true |
| `mandate_signing_key_absent_from_runtime` | true | true |
| `cancel_margin_s` | 300.0 | 300.0 |
| `defensive_terminal_s` | 600.0 | 600.0 |
| `max_windows_code_ceiling` | 8 | 8 |
| `campaign_duration_ceiling_s` | 14400.0 | 14400.0 |
| `approval_exposure_ceiling_usd` | 20 | 20 |
| `approval_daily_loss_ceiling_usd` | 5 | 5 |
| `defensive_loss_budget_usd` | 2.00 | 2 |
| `campaign_additional_loss_usd` | 5 | 5.00 |
| `opening_notional_budget_usd` | 3.00 | configured, 0..5 |

`defensive_pair_ceiling 1.20` is a code constant and is inert while
`DEF.enabled()` is false.

### `PASS: false` is the correct pre-installation reading

Eight rows fail, and **every one of them reads
`required: "NO_APPROVAL_IN_FORCE"`**: `submission_budget_open`,
`submission_budget_complete`, `submission_budget_replace`,
`submission_backstop`, `aggregate_exposure_usd`,
`aggregate_exposure_in_force_usd`, `daily_realized_loss_halt_usd`,
`max_windows_authorization`.

The checker is reporting that no approval is installed yet, which is true.
Their *effective* values are already the approved ones (40 / 40 / 64 / 72,
exposure 20, daily loss 5, windows 8).

**This gives a precise acceptance test for the installation:** after the
document is in force these eight rows must flip to `ok: true` and
`PASS` must become `true`. If `PASS` stays false with any row still reading
`NO_APPROVAL_IN_FORCE`, the document did not take effect and the campaign
must not arm.

## Campaign 8's durable budget lock: releases through the normal mechanism

**No manual clearing, and none is possible from here.** The ledger lives on
the Railway volume; this session has no write path to it. The only release
is the one the arming code performs.

The mechanism, wired at `src/l5_live_authorization.py:2368`, runs inside
`authorize_arming` before the budget is claimed:

```python
_rel = _budget.release_stale(_cid, legs=_legs, outstanding=_out, now_iso=...)
```

`release_stale` (`src/l5_campaign_budget.py:360`) closes a previous
campaign's ledger only on the evidence `close` itself demands:

- refuses `CAMPAIGN_OUTSTANDING_ORDERS` if any live order has
  `remaining > 0`
- refuses `CAMPAIGN_UNSETTLED_EXPOSURE:N` if any market in
  `store.committed()` is absent from `retired_markets`
- refuses `CANNOT_RELEASE_OWN_OPEN_CAMPAIGN` if the claimant is the owner
- an unreadable store yields a SENTINEL outstanding entry, so it **fails
  closed** rather than freeing a budget it cannot vouch for
- erases no loss: realized figures live in the executor's store and
  re-enter every gate evaluation, so a release clears the ownership claim
  and high-water mark only, neither of which is a spending allowance

`tests/test_l5_campaign_budget_release.py` — **18 cases pass**, including
that a campaign may not release itself.

### Evidence that it will pass, and what I cannot prove

Supporting:

- **It already did this on this exact deployment lineage.** Campaign 8's
  first arm released Campaign 7's claim by this same path ~9 hours ago,
  when the store held strictly *more* unretired markets than it does now.
- **The account is flat and fully settled.** Cash has been 343.21615 since
  03:24:02Z — 7.6 hours unchanged — so there is no open position and no
  pending settlement credit.
- **No outstanding orders.** Campaign 8's last order terminated at
  03:23:58Z. The new boot shows `L5_LIVE_ORDER_REGISTRY` serving normally
  with no `L5_LIVE_ORPHAN_CANCEL`, no `L5_LIVE_ORDERS_STALLED` and no
  `L5_LIVE_LIFECYCLE` warning.
- `refuse_arming` passes independently: peak 22.2436 < 29.30 budget.

What I cannot prove in advance: the `retired_markets` set is on the volume
and not exposed in telemetry, so I cannot confirm from here that every
committed market has retired. **That is acceptable because the path is
fail-closed** — if the evidence is short, arming refuses, no order is
submitted and no capital moves.

**Verification point:** the first `L5_LIVE_ARMED` record of Campaign 9 must
carry `budget_release.released: true` with
`closed_campaign_id: camp1.camp-3cOtW8HnrFbY5u2NAkX1QfvhqUhxsChq`. That is
the first thing checked after installation. `released: true` appears exactly
once per campaign; arms 2–8 will correctly read
`CANNOT_RELEASE_OWN_OPEN_CAMPAIGN`, as Campaign 8's arms 2–6 did.

## A correction to my own earlier explanation

I previously said the post-expiry `submissions_spent` of 142 arose because
`campaign_id` goes null and the claim glob degenerates. This config shows
`experiment_id` is a *separate* identifier
(`l5-btc15m-prog24-c1-3bf1daa5`) and is what namespaces the claim files, so
my stated mechanism may be wrong in detail.

What is unchanged is the measurement and the rule: the counter read **22**
during Campaign 8 at 02:50Z, exactly matching the 22 orders submitted by
then (9+5+7+1), and **142** after expiry. Something in the namespace
resolution changes at expiry. **Do not read `submissions_spent` once the
campaign is over**; during the campaign it is exact.
