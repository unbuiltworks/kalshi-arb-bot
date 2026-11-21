# Campaign budget handover, 20261023 -> 20261024a

Recorded because the ledger file does not keep one. `CampaignBudgetLedger._write`
is an atomic `os.replace` of a single JSON document with no journal, so
`open_or_resume` overwrote the closed campaign's document in place. The figures
below are recovered from the `L5_LIVE_GATES` heartbeat, which carries the whole
document every cycle; they are quoted, not reconstructed.

## What happened, and when

The handover was performed by already-deployed code (`4dea4007`), not by hand.
`authorize_arming` calls `release_stale` before claiming, and the claim for the
0030z window is what triggered it.

| time (UTC) | `campaign_id` | `closed` | `peak_usd` | `remaining_usd` | `owns_budget` |
|---|---|---|---|---|---|
| 2026-10-24T00:31:23Z | `camp1.l5-btc15m-8win-20261023` | false | 10.35520000 | 18.94480000 | false |
| 2026-10-24T00:33:38Z | `camp1.l5-btc15m-8win-20261024a` | false | 10.21000000 | 19.09000000 | true  |

`budget_usd` is 29.30 on both sides. The release happened inside the
00:31:27Z arm of `cpc-btc-updown-15m-2026-10-24-0030z`.

## The old campaign's final state

- `campaign_id`: `camp1.l5-btc15m-8win-20261023`
- final `peak_usd`: **10.35520000** of 29.30, i.e. 18.94480000 unspent
- eight windows exhausted; `refuse_arming` was returning
  `CAMPAIGN_WINDOWS_EXHAUSTED`
- settled net across its eight windows: **+$0.1336** gross
- nothing outstanding, nothing unsettled at the moment of release, which is
  the evidence `release_stale` required

## Why the new ledger does not start at zero

`peak_usd` on the fresh ledger reads 10.21, not 0. That is correct and it is
not carried-over campaign usage. `worst_case_usd` is

    realized losses + unsettled cost + outstanding notional

evaluated ACCOUNT-WIDE on every call. At 00:33:38Z the account's cumulative
realized figure was -$8.56 and one $1.65 obligation was outstanding:
8.56 + 1.65 = 10.21. The campaign-specific usage did start at zero
(`open_or_resume` writes `peak_usd: "0"`); the high-water mark then
immediately re-absorbed the account-wide realized loss, because account-wide
limits are cumulative by design and a new campaign nonce must not wipe them.

The consequence worth stating plainly: the new campaign's *incremental*
allowance is 29.30 - 8.56 = **20.74**, which is what the planner reported as
`budget_headroom_usd` on its first plan. A fresh nonce buys a fresh window
budget, never a fresh loss allowance.

## What is NOT reset by a handover

`peak_usd` and the ownership claim are the only things the ledger holds.
Realized P&L, the $5 daily halt, the $20 cumulative halt and account-wide
accounting all live in the executor's store and re-enter the gate on every
evaluation. `realized_today` read `-8.56000000` on both sides of the
handover, which is the direct evidence that none of them moved.

## The gap this document stands in for

The ledger should journal a lifecycle transition rather than depend on a log
stream with a retention window. `_write` replaces the document, so the
20261023 entry survives only here and in the heartbeat. Appending the previous
document to a history file on any change of `campaign_id` or `closed` is the
fix; it is deliberately NOT being deployed mid-campaign, because no further
handover can occur inside this campaign and a redeploy costs a live window.
