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

---

# ACTIVATION — 2026-10-29, Route B live

## The ledger question, answered empirically

The $7.0564 does **not** constrain the new campaign, and no ledger was reset
by hand. `l5_campaign_budget.release_stale` is the normal transition: it
refuses unless the open campaign is provably finished (nothing outstanding,
nothing unsettled, evidence supplied by the caller from the venue-reconciled
ledger), and it refuses to let a campaign release itself. Arming invoked it.

From `L5_LIVE_ARMED` 18:45:09Z, the budget passed to a new ledger
`camp1.camp-9OuJSAHK6JS-ECKO3C9DVgoA-wQb23Wd`. `budget_release` then reads
`CANNOT_RELEASE_OWN_OPEN_CAMPAIGN`, which is the normal reading once the new
campaign owns the budget.

What a release clears is the ownership claim and the `peak_usd` high-water
mark — neither is a spending allowance. No loss was erased: realized losses
live in the executor's store and re-enter every gate evaluation through
`worst_case_usd(realized_usd=...)`. The account-wide backstop
`max_cumulative_loss_usd` $18.6325 still sits beside the authorized $20 and
refuses first, and `realized_today` is **−$9.5912**.

## Authorized scope, as installed

8 windows · $20 exposure · $12 committed cash · $5 daily loss · 72
submissions (24 open / 32 complete / 16 replace). Minted at the code
ceilings, so `clamped: false` on every field; nothing was widened.

Document digest **`ac855ce359dd81108a14953c097a2e370fbafc010babf290c5e5dd9851441332`**,
issued 18:20:15Z, expires 22:20:15Z. `L5_LIVE_AUTH_NOT_AFTER` set to the
same instant.

The 3600 s staleness bound does not apply to the arming path: both the
preflight and `campaign_decision` build the gate through
`CA.approval_gate()`, which uses `max_age_s = MAX_CAMPAIGN_S` (4 h).

## Deployed variables tightened to the authorized figures

The document's submission budgets are GRADED against the deployed variables
by equality (`_budget` prefers the document). The deployed values were left
over from a previous campaign and would have failed arming:

| variable | was | now |
|---|---:|---:|
| `L5_LIVE_AUTH_MAX_OPEN` | 40 | **24** |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 40 | **32** |
| `L5_LIVE_AUTH_MAX_REPLACE` | 64 | **16** |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 72 | 72 (unchanged) |

All three are REDUCTIONS to the authorized figures. No limit was raised.

## Gates verified on the Route B container (`8149d173`, c39183a3, SUCCESS)

`L5_EFFECTIVE_RUNTIME_CONFIG`: **PASS true, checked 26, failed []**.

Including: `aggregate_exposure_usd` 20 · `aggregate_exposure_in_force_usd`
(frozen at boot) 20 · `daily_realized_loss_halt_usd` 5 · `max_live_orders`
**1** · `maker_pair_ceiling` 0.96 · `max_windows_authorization` 8 ·
`series_allowlist` `cpc-btc-updown-15m-` · `window_strategy` TWO_SIDED ·
`maker_only_forbids_defensive_crossing` **true** ·
`mandate_signing_key_absent_from_runtime` true.

Observed: `defensive_execution_enabled` **false** (maker-only),
`shadow_only` **false**, `ts_two_order_pair_enabled` **false**,
`ts_min_first_leg_own_price` **0.20**, `mandate_enforcing` false.

The two limits the previous session could not read are now verified from
both the variables and the armed scope digest: `max_total_exposure_usd` 20
and `max_live_orders` 1.

## Pre-order checklist — all ten pass

1. c39183a3, SUCCESS ✓  2. `preflight: PASSED` ✓  3. `halt_latch: CLEAR`,
`halt_why: null` ✓  4. `record_t` 18:43:31Z, 74 s old, inside 900 s ✓
5. `shadow_lock: false` ✓  6. reconciled — arming refuses an unconfirmed
book, and it armed ✓  7. `armed: true`, scope digest
`9beae19897191d3205ee8e5412829fd777e375e72debc03a7ec16acbfab8f98f` ✓
8. scope shows `max_total_exposure_usd: 20`, `max_live_orders: 1` ✓
9. `L5_DEFENSIVE_COMPLETION=0`, `L5_TS_TWO_ORDER_PAIR=NO`, both falsy;
`execution_mode MAKER_ONLY` ✓  10. `journal_errors: 0` ✓

## Window 1 of 8 was spent before Route B went live

The runner arms automatically per window. It armed `1845z` at 18:45:09
(`windows_spent: 1`) while `L5_BOOK_COMPLETION` was still unset, so that
window ran the baseline engine. Activating Route B requires a variable
write, which restarts the container, so the 1845z claim then read
`AUTHORIZATION_ALREADY_USED` and the window refused on
`INSUFFICIENT_ENTRY_RUNWAY:299.0<300.0`.

**Seven windows remain for Route B**, the first being `1900z`. No order was
submitted in 1845z: all activity was `CONTROLLER_GENERATED` shadow, labelled
"no order was ever submitted, and no fill was ever confirmed", with
`order_resting: false`.

## Accounting status

`historical replay -> shadow -> live probe -> submitted -> filled -> settled
-> realized`. Currently at **armed, nothing submitted**. No submitted order,
no fill, and no realized P&L exists yet, so none is reported.
