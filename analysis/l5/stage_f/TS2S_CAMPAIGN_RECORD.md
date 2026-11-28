# Two-sided simultaneous-quote campaign — live record

**Owner-approved 2026-11-01. Eight consecutive BTC15m windows, 1330z–1515z.**

## Authorization in force

| | |
|---|---|
| document sha256 | `7063e80f3cf78cf956fd49dfbf30a61cec26d9a9748d4f0f6789cfea238c498a` |
| issued / expires | 12:56:40Z / 15:36:40Z |
| `starts_utc` | **13:30:00Z** |
| `L5_LIVE_AUTH_NOT_AFTER` | **15:15:00Z** (last window's start) |
| session id | `ts2s-20261101-1330z-ab9b07c00a28` |
| clamped | **nothing** |
| windows / exposure / daily loss | 8 / $20 / $5 |
| budgets | OPEN 32 / COMPLETE 32 / REPLACE 8 / backstop 56 |

Fresh mint, never reused, not backdated. The previous `approval_stale`
document is superseded.

## Schedule

| # | window | start | end |
|---|---|---|---|
| 1 | `1330z` | 13:30 | 13:45 |
| 2 | `1345z` | 13:45 | 14:00 |
| 3 | `1400z` | 14:00 | 14:15 |
| 4 | `1415z` | 14:15 | 14:30 |
| 5 | `1430z` | 14:30 | 14:45 |
| 6 | `1445z` | 14:45 | 15:00 |
| 7 | `1500z` | 15:00 | 15:15 |
| 8 | `1515z` | 15:15 | **15:30** |

## Pre-campaign preflight — ALL PASS

**Deployment `6e861476`, commit `4d52345d`.** That commit differs from the
verified `4aae0894` by **one markdown file**; `src/` and `deploy/` are
byte-identical, so the executor code is exactly what was verified.

**A correction to my own note:** I said the commit pin would make arming
writes rebuild the pinned commit. It did not — the variable write built
**branch HEAD**, bypassing the pin, as this project established earlier. The
code is identical here so nothing turned on it, but the note was wrong.

`L5_EFFECTIVE_RUNTIME_CONFIG` at 12:58:58Z: **`PASS: true`, `failed: []`.**

| graded row | effective | required |
|---|---|---|
| submission budgets | 32 / 32 / 8 / 56 | match |
| aggregate exposure | $20 | $20 |
| daily realized loss halt | $5 | $5 |
| max windows | 8 | 8 |
| max live orders | 1 | 1 |
| maker pair ceiling | 0.96 | 0.96 |
| window strategy | TWO_SIDED | TWO_SIDED |
| series allowlist | `cpc-btc-updown-15m-` | match |
| maker-only forbids defensive crossing | true | true |

| observed | value |
|---|---|
| **`taker_crossing_reachable`** | **false** |
| `taker_policy_owner_cross_authorized` | false |
| **`ts_two_order_pair_enabled`** | **true** |
| **`ts_round_allocations`** | **4** — unchanged |
| `campaign_authorized_start` | **2026-11-01T13:30:00+00:00** |
| `max_worst_case_deficit_usd` | $1.50 |
| `ts_opening_leg_max_contracts` | 3 |

Heartbeat 12:59:02Z: `owner_arm_token true`, `approval_state approved`,
`preflight PASSED`, `halt_latch CLEAR`, `live_orders 0`, `committed_usd {}`.
`armed false` with the gate `blocked` — **correct**: the live window was 1245z,
which precedes the authorized 13:30Z start, so `starts_utc` refuses it as
`WINDOW_BEFORE_AUTHORIZED_START` and **no window budget is consumed**.

Watchdog `5ef90355`, sweep 12:56:03Z: `emergency_cancel` present,
`cancel_enabled true`, `open_orders 0`, `overdue`/`not_ours`/`still_open`/
`unverified` all empty, `read_error null`.

## Accounting labels

Submitted is **not** a fill. An unsettled position is **not** realized
profit. Only `L5_LIVE_SETTLED` / `realized_net_usd` is realized. Estimated
rebates are excluded. Baseline for comparison: matched **+$0.3895**, residual
**+$1.0260**, total **+$1.4155**, zero-pair window rate **42.9%**, residual
frequency **71.4%**, matched contracts per filled first leg **≥1.68**.

Results follow after settlement.
