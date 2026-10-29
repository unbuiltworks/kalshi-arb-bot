# Campaign 9 pre-arm verification — 2026-10-29

Read-only. Nothing was armed, minted, installed or submitted.

## Build identity — CONFIRMED

| | |
|---|---|
| service | `l5-pmus-live` (`87f1834e`) |
| deployment | `c208b3de-c79a-41a4-81c8-264aeceaec52`, SUCCESS |
| commit | `c39183a3` |
| heartbeat | `L5_LIVE_GATES` emitting on 15-min cadence through 18:00:04Z |

Railway token authenticates as `unbuiltworks`, workspace reads succeed.

## Risk gates — readable set all PASS or at authorized value

From `L5_LIVE_GATES` 17:36:55Z and `L5_AUTHORIZATION_PREFLIGHT` 17:36:54Z:

| gate | value |
|---|---|
| `max_cash_deployed_usd` | $12.00 |
| `max_unmatched_contracts` | 5 |
| `max_worst_case_deficit_usd` | $3.00 |
| `max_l5_daily_loss_usd` | $5 |
| `max_l5_cumulative_loss_usd` | $20 |
| `max_cumulative_loss_usd` (account-wide) | $18.6325 — stricter, refuses first |
| `cash_policy` | `NO_RECYCLING` |
| `halt_gate_configured` / `halt_gate` | true / PASS, `permitted: true` |
| `journal_errors` | 0 (32 entries) |
| `shadow_lock` | PASS, not engaged |
| `owner_arm_token`, `role_binding`, `session_id` | PASS |
| `residual_ceiling` | PASS, effective cap 3 contracts, ABSOLUTE binds |
| `window_budget_env` | 8 |

Route B present and inert: `L5_BOOK_COMPLETION` unset.

## Two blockers — both are owner variable writes

`preflight.ok: false`, `fail: ["absolute_deadline", "campaign_approval"]`

1. **`L5_LIVE_AUTH_NOT_AFTER` — EXPIRED**, `expires_in_s: -9131.9`
   (lapsed ~02:30 before the 17:36 read). Blocks `AUTHORIZATION_EXPIRED`.
2. **`L5_CAMPAIGN_APPROVAL` — `approval_stale`.** A document IS installed,
   digest `d60871dc274f7e7eec9e00f8409716738ab2e3428dc7c5900e7f68270b32d9f1`,
   but it is past the 3600 s staleness bound. Blocks arming.

## Two limits I cannot verify, and do not claim

`max_total_exposure_usd` and `max_live_orders` are `SafetyLimits` set through
variables this Railway role cannot read. They appear only in the
`L5_LIVE_ARMED` scope digest, so arming is the first point they are
checkable. Arming halts unless they are exactly $20 and 1.

## Ledger headroom contradicts a $20 document

Open ledger `camp1.camp-3cOtW8HnrFbY5u2NAkX1QfvhqUhxsChq`, `closed: false`:

- `budget_usd` 29.30, `peak_usd` 22.2436, **`remaining_usd` 7.0564**, `owns_budget` false

A fresh nonce resets `peak_usd` but never the account-wide figure. So the
campaign-wide worst case is bounded by the account-wide gate against
**$7.0564** of remaining room, not by a document's $20. The $20 instantaneous
ceiling would not be reachable.

## Scope conflict, unresolved

| source | exposure | submissions |
|---|---|---|
| `L5_LIVE_CAMPAIGN_MANDATE_20261024` / `MANDATE_PAYLOAD_5USD.json` (owner-facing) | **$5** | 56 |
| `analysis/l5/campaign9/CAMPAIGN_ARMING_CHECKLIST.md` mint command | **$20** | 72 |

Both are inside the code ceilings. They are different campaigns. Minting the
$20 form would be a 4x expansion over the last owner-facing mandate.

## Not done, deliberately

- Did not mint or install `L5_CAMPAIGN_APPROVAL`. `L5_AUTHORIZATION_ROUTE.md`
  §6 commits to not writing it; `ROUTE_B_PRODUCTION_READINESS.md` requires
  that *the owner* sign and install it; `L5_LIVE_CAMPAIGN_MANDATE_20261024`
  §6 states the consuming mechanism cannot distinguish the owner's approval
  from Claude's. The document is the only input encoding owner intent as to
  scope, size and expiry; everything else is a ceiling.
- Did not set `L5_LIVE_AUTH_NOT_AFTER` — it sets the campaign's end time.
- Did not set `L5_BOOK_COMPLETION=1` — downstream of arming, which is blocked.
