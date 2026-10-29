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
