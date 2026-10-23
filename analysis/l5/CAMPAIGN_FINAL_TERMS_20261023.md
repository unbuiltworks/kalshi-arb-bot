# Final campaign terms — awaiting owner approval (NOT armed)

Four windows, maker-only, post-only. Executor `l5-pmus-live`.

## 1. Is 1.11 contracts/leg executable? Yes — but it is not a fixed quantity

**Venue rules, read from the market by the live transport, not assumed:**
`quantity_increment = 0.01` (922 observations in the service's own logs; the
transport takes it from `orderMinQuantity`/`minimumTradeQty`/`minOrderSize`
on the market metadata and uses it as both the minimum and the increment).
There is **no minimum-notional rule** anywhere in the transport or the probe.

`1.11 = 111 x 0.01`, so it is on the grid and 111x the minimum: **executable**.

**But 1.11 is not a constant.** It is what the sizing formula yields at
`up_px = 0.48`. `build_orders` computes
`qty = floor_to_0.01(min(cap/dear, env/(1+up_px), headroom/(1+up_px)))`, so at
`env = $1.65` the executable quantity moves with the UP price:

| up_px | obligation/ct | raw | **executable qty** | obligation | acquisition |
|---|---|---|---|---|---|
| 0.30 | 1.30 | 1.2692 | **1.26** | $1.6380 | $1.2096 |
| 0.40 | 1.40 | 1.1786 | **1.17** | $1.6380 | $1.1232 |
| 0.48 | 1.48 | 1.1149 | **1.11** | $1.6428 | $1.0656 |
| 0.50 | 1.50 | 1.1000 | **1.10** | $1.6500 | $1.0560 |
| 0.60 | 1.60 | 1.0312 | **1.03** | $1.6480 | $0.9888 |
| 0.70 | 1.70 | 0.9706 | **0.97** | $1.6490 | $0.9312 |

**Executable range: 0.97 – 1.26 contracts/leg.** Every value is on the 0.01
grid and above the minimum.

**Nothing is rounded up.** The quantiser is `ROUND_FLOOR`, and after
quantising there is a further shrink loop that steps *down* one increment at a
time until the worst single-leg fill plus its fee fits the ceiling. The
refusal stands if nothing fits. Exposure is never enlarged to reach a tradable
size — a window that cannot be sized is skipped.

**Correcting my own figure from last turn:** I quoted "1.11 contracts/leg" and
"aggregate ≤ $4.26" as if the size were fixed. The 4-window worst case is
**$4.8384** of acquisition (4 x $1.2096), not $4.26.

## 2. Maker-only enforcement — the check works, and it was failing

The owner asked me to verify the fail-closed execution-mode check. I ran it
both ways:

| `L5_DEFENSIVE_COMPLETION` | `EXECUTION_MODE` | `DEF.enabled()` | row `maker_only_forbids_defensive_crossing` |
|---|---|---|---|
| `1` (as deployed) | MAKER_ONLY | True | **FAIL** |
| `0` (now set) | MAKER_ONLY | False | **PASS** |

The row is fail-closed by an explicit allowlist: only `TAKER_PERMITTED` or
`DEFENSIVE_PERMITTED` permit a crosser, and anything else — including an
absent, empty or unreadable mode — requires `DEF.enabled()` to be False.

**This matters more than I said last turn, and I had it wrong.** I wrote that
crossing "needs separate authorization and the `MAKER_ONLY` posture forbids an
armed crosser". `EXECUTION_MODE` is a *code constant* that this verifier
*grades*; it does not itself block anything. And `defensive_execute` —
docstring: *"Retire the resting complement, PROVE it is gone, then authorize
exactly one **crossing** order"* — is reached whenever `DEF.decide()` returns
`act: True`, with **no** `ts_completion_cross_authorized()` check on that path.
`L5_TS_COMPLETION_CROSS` (absent) gates only the separate `maybe_ts_completion`
path.

So with the flag on, a taker order could have been submitted. **Turning
`L5_DEFENSIVE_COMPLETION` to 0 is the control**, not a hygiene step, and it is
now set. The verifier row is the only thing that was flagging the conflict.

## 3. Applied, and what each change does

| variable | from | to | effect |
|---|---|---|---|
| `L5_CAMPAIGN_BUDGET_USD` | 11.10 | **29.30** | owner-authorized; headroom $19.9964 |
| `L5_MAX_CASH_DEPLOYED_USD` | 4.65 | **1.65** | owner-authorized; holds qty near 1.1 |
| `L5_DEFENSIVE_COMPLETION` | on | **0** | removes the taker crosser; verifier row passes |

**Preserved and untouched:** $20 account-wide cumulative, $5 daily halt,
residual cap 3, `L5_MAX_UNMATCHED_CONTRACTS`, watchdog, exact-order
cancellation, `L5_DEFENSIVE_DWELL` absent (dwell gate stays off), shadow lock.
No realized loss reset: `all_time_realized_usd` stays **−$9.30360000**.

## 4. Exposure and loss bounds

| bound | value |
|---|---|
| obligation per window | **≤ $1.65** by construction |
| acquisition per window | $0.9312 – $1.2096 |
| **4-window acquisition, worst case** | **$4.8384** |
| 4-window obligation, worst case | $6.60 |
| campaign budget headroom | $19.9964 |
| account-wide cumulative room | $10.6964 |
| **daily realized-loss room today** | **$4.5952** ← binding stop |

**The 4-window worst-case acquisition ($4.8384) slightly exceeds today's daily
room ($4.5952).** If every window lost its full acquisition, the $5 daily halt
would fire during the fourth window and stop new commitment. That is the
control working, not a breach — but it means the campaign is bounded by the
halt, not by its own sizing. If you would rather the whole 4-window aggregate
sit inside the daily room, set `L5_MAX_CASH_DEPLOYED_USD` to **1.55** instead
(4-window worst case $4.5696). Say the word and I will.

Expected outcome on the 282-window production shadow (−$0.2065/window):
**about −$0.83**. Unchanged from last turn, and still the reason I am not
calling this positive-expectancy.

## 5. The approval document — for the owner to set

Set `L5_CAMPAIGN_APPROVAL` on `l5-pmus-live` to exactly this, with the two
timestamps filled in at the moment you set it:

```json
{
  "approved": true,
  "nonce": "camp-20261023-4w-a1",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<now, tz-aware, e.g. 2026-10-23T15:30:00+00:00>",
  "expires_utc": "<now + 90 min, e.g. 2026-10-23T17:00:00+00:00>",
  "max_windows": 4,
  "max_total_exposure_usd": "2.00",
  "max_daily_loss_usd": "5"
}
```

- **expiry 90 minutes** — four 15-minute windows is 1h; the code ceiling is 4h.
- **`max_total_exposure_usd` 2.00** narrows the $20 code constant to just above
  the $1.65 obligation. The document can only narrow, never widen.
- **nonce is single-use** — spent on claim, no renewal.
- Naive or future timestamps, a stale document, a spent nonce, or a series
  outside the allowlist all read as **refusal**.

Then two more, which actually lift the lock:

- `L5_SHADOW_ONLY` = `0`
- `PMUS_Q1_LIVE` = `YES`

**I am not setting any of these three.** The approval is the second key, and
`EnvApprovalStore.put` exists only to raise *"the executor may not write its
own approval"*. As flagged last turn, my Railway token could write them; I am
not going to, because a campaign I both authorize and execute has no second
key.

## 6. On confirmation

Once the approval is in place and the lock is lifted I will run the four
windows unattended and report **submitted / venue-confirmed filled / settled /
realized** separately, with no shadow or modelled figure presented as realized.
Stop conditions: expiry, four windows spent, the $5 daily halt, any
reconciliation uncertainty, watchdog latch, or any safety failure. Shadow lock
restored and verified afterwards.
