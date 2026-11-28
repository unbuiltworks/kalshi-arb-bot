# Stage B1 live campaign — 2026-10-31 0215z..0300z

Owner-authorized four-window PMUS BTC15m campaign. Approval nonce
`stageB1-9de9b4d8b53bf38f`, document sha256 `f4cfbbb4477d92a5e72e7a50059c18df3b9d5ba25c1f16688221575429a64d4b`,
scope digest `3c9cd171b255932cd6f947266a6b4a552124d9ccd92bc99e8b0a10c19014ce2d`,
serving deployment `f51840b8` on commit `ff00d287` (carries `69b19625`).

## Settled result, from `L5_LIVE_SETTLED` (the venue's own lines)

| window | up | dn | matched | matched P&L | residual | residual P&L | realized |
|---|---|---|---|---|---|---|---|
| 0215z | 5.61 | 5.61 | **5.61** | +0.001700 | **0.00** | 0 | **+0.001700** |
| 0230z | 8.52 | 8.52 | **8.52** | +0.219400 | **0.00** | 0 | **+0.219400** |
| 0245z | 2.01 | 3.98 | 2.01 | +0.029847 | 1.97 DOWN | +1.093053 | **+1.122900** |
| 0300z | 0 | 0 | 0 | 0 | 0 | 0 | **0** (armed, no fills) |
| **total** | | | **16.14** | **+0.250947** | | **+1.093053** | **+1.344000** |

**Ledger tie-out, exact:** `all_time_realized_usd` −11.58860000 →
**−10.24460000**, delta **+1.34400000**, equal to the sum above.
`utc_day_realized_usd` +1.344000 against the $5 halt, `room_usd` 6.344000,
`would_halt` false.

## Read this honestly

**+$1.3440 realized is NOT a skill result.** **81% of it (+$1.093053) is the
0245z residual winning on direction** — a 1.97-contract DOWN leg that
happened to settle in our favour. The same leg losing would have made the
campaign roughly −$0.84.

The component attributable to matched inventory — the part that is
outcome-independent, because a pair redeems $1.00 either way — is
**+$0.250947**. That is the honest figure, and it is small.

## What IS new and does not depend on luck

**Windows 0215z and 0230z settled with ZERO residual.** `up_qty == dn_qty`
exactly (5.61/5.61 and 8.52/8.52), `residual_qty 0.00`, both profitable.
Across every prior campaign on record every window carried a residual
(Stage A: 2.05, 2.00, 1.85, 1.74). Two consecutive fully-matched windows had
not happened before.

Matched volume also rose: **16.14 matched contracts** over three trading
windows, against Stage A's 3.51 over four.

**Attribution caveat I will not paper over:** I cannot prove the zero-residual
outcome came from the DOWN-completion fix. The window-1 counters show the
pairing was driven by `controller_open` (`ENTERED` 391, `NOT_REFUSED` 9),
while Route B's `book_completion` mostly did not place
(`ORDER_ALREADY_RESTING` 2, `ECONOMICS:COMPLEMENT_DEPTH_INSUFFICIENT` 1).
The fix removed a blocker and the outcome improved; those are two facts, not
one causal chain.

## Safety and control evidence

| control | evidence |
|---|---|
| exact authorized schedule | armed on 0215z, 0230z, 0245z, 0300z and no others |
| schedule could not drift earlier | `L5_LIVE_DISARMED ... why: WINDOW_BEFORE_AUTHORIZED_START` on 0145z, before the campaign |
| window budget | `windows_spent` 0→3 across arms, `window_budget` 4; no fifth window |
| automatic expiry | 03:15:10 `APPROVAL_NOT_VALID / approval_expired` refused 0315z |
| one live order | `controller_open:ORDER_ALREADY_RESTING` 134 in window 1 alone |
| duplicate orders | none; deterministic coids, `live_orders` 0 at every check |
| reconciliation failures | none |
| risk-limit violations | none. Scope enforced `max_usd_per_market 20`, `max_total_exposure_usd 20`, `daily_realized_loss_halt_usd 5`, `max_live_orders 1`, `max_cash_deployed_usd 12.00`, `execution_mode MAKER_ONLY` |
| crossing | never enabled; `L5_TS_COMPLETION_CROSS` untouched |
| watchdog | independent `l5-halt-watchdog` sweeping ~20s, `cancel_enabled true`, `open_orders 0`, `read_error null` |
| campaign baseline | captured fresh for `camp1.stageB1-9de9b4d8b53bf38f` at 02:15:16, cash $341.21875, inventory `{}` |
| end state | `armed false`, `owner_arm_token false`, `live_orders 0`, no residual inventory, `approval_state approval_stale` |

## Window 4

Armed 03:00:20 (`windows_spent 3`, `window_budget 4`), made **no fills**, and
disarmed 03:14:00 with `CAMPAIGN_WINDOW_NOT_USABLE /
PAST_ENTRY_WITH_NO_INVENTORY`, `residual 0`. No settlement record because
there was nothing to settle. It contributed $0 and stranded nothing.

## What this does not establish

- Four windows, one of them with no fills. Three settled windows is not a
  sample from which to claim an edge.
- The matched edge of +$0.250947 over 16.14 contracts is **+$0.0155 per
  matched contract**, close to the +0.0111 L5 average and the +0.0116 implied
  by PMUS spread capture — consistent, but three windows cannot distinguish
  that from noise.
- No rebate is counted; estimated maker rebates remain uncredited.
- $480/day remains a target. At this campaign's matched rate it would need
  roughly 31,000 matched contracts a day against the 16.14 traded here.

## Correction appended after diagnostic (see STAGE_B1_DIAGNOSTIC.md)

The note above that the matched edge is "consistent, but three windows cannot
distinguish that from noise" was directionally right and is now exact:

- treating each settled pair as one observation of `1.00 − pair_cost`:
  mean **+$0.0125/contract**, stdev 0.0282, n=8 → **t = 1.26, one-sided
  p ≈ 0.105**. Not significant.
- **80% of the matched edge comes from one pair of eight** (0230z #4 at
  0.9200). The other seven average **+$0.00286/contract**.
- only **4 of 8** pairs completed strictly below $1.00.

Also corrected: 0300z's zero fills were **not** caused by the book. The
campaign's `OPEN` submission class read **12 spent of 12, remaining 0** at
02:58:34, before window 4 armed, and the completion reserve independently
refused opening (`NO_COMPLETION_CAPACITY_RESERVED`). The 4-window
authorization was funded for roughly 3 windows of activity.
