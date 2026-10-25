# Bounded live-validation campaign — plan, pending fresh owner authorization

**Evidence standard adopted.** Simulated outcomes are diagnostic only. Nothing below is
offered as a profitability verdict, in either direction. The purpose is to find out whether
**real execution differs materially from the shadow assumptions**, measured only against
venue-confirmed records: submitted and acknowledged orders, maker/taker fills and prices,
cancellations and partial fills, credited rebates and actual fees, settlement cashflows, and
verified realized net P&L.

**Not armed. No authorization requested in this document. Nothing deployed.**

## 1. PRE-AUTHORIZATION VERIFICATION — the daily reset and the cumulative independence

Required before activation, and done as a test rather than an assertion:
`tests/test_l5_daily_reset_and_cumulative_independence.py`, **9 cases, all passing.**

Both settlement write sites stamp `dt.datetime.now(dt.timezone.utc).isoformat()`
(`src/l5_live_execution.py:2985` and `:3522`), so the day-matching in `l5_realized_today()`
operates on a real ISO prefix. **This was the one way the fix could have been silently
wrong** — a numeric epoch stamp would have made the daily figure permanently $0 and the daily
ceiling would never have bound. It is tested directly.

| property | result |
|---|---|
| today's attested losses count toward the DAY figure | **PASS** |
| **yesterday's losses do NOT count today — the reset** | **PASS** |
| yesterday's losses still count toward CUMULATIVE | **PASS** |
| a day with no settlement reads 0, not the cumulative figure | **PASS** |
| unattested rows excluded from both | **PASS** |
| pre-baseline rows excluded from both | **PASS** |
| no baseline returns None from both, never 0 | **PASS** |
| an unreadable P&L makes both unknown, never 0 | **PASS** |

## 2. REMAINING HEADROOM, read from the running engine at 22:51:33Z

| | |
|---|---|
| baseline epoch | 2026-10-16T02:45:12.565947+00:00 |
| baseline cash | $350.05255 |
| `l5_realized_since_baseline` | **−$5.245700** (43 attested settlements) |
| L5-scoped **cumulative** ceiling | $20 → **headroom $14.7543** |
| L5-scoped **daily** ceiling | $20 (owner-approved) |
| **account-wide `daily_realized_loss_halt_usd`** | **$5** |
| exposure capacity now | **$5 of $5** — `account_total_worst_case_usd` is 0, the book is FLAT |

### A material finding the owner needs before authorizing

**The approved $20 daily ceiling does not deliver $20/day of room.** The account-wide
`daily_realized_loss_halt_usd` is still **$5** and it runs **first** — the L5-scoped block is
only reached after the account-wide check returns clean. So:

> **EFFECTIVE DAILY LOSS BUDGET = min($5 account-wide, $20 L5-scoped) = $5.**

That is the independent limit behaving exactly as intended, and I am not proposing to change
it. But the campaign must be sized to $5, not $20, and a plan built on $20 would stop early
and look like a malfunction.

Also unverified and flagged: the account-wide **cumulative** gate is fed
`cumulative_realized=self.store.realized_today()` (per the guard in
`tests/test_l5_four_bucket_accounting.py`). Its scope should be confirmed from the boot
verifier at arm time rather than assumed.

## 3. THE CAMPAIGN — exact figures, every one an existing deployed value

Nothing here widens inventory exposure or the pair ceiling. The fill count is whatever these
limits produce.

| parameter | value | source |
|---|---|---|
| series | `cpc-btc-updown-15m-` | `CA.ALLOWED_SERIES` |
| **windows** | **8 consecutive** | `CA.MAX_WINDOWS` code ceiling |
| **expiry** | issued + **2h30m**, hard | ≤ `CA.MAX_CAMPAIGN_S` 14400s |
| **order budget** | **56 submissions**: open 16 / complete 24 / replace 32 | the boot verifier's own 8-window reference values |
| **max capital exposure** | **$5.00** aggregate, `NO_RECYCLING` | `max_total_exposure_usd`, `max_cash_deployed_usd` |
| per-market worst case | $2.00 | `max_worst_case_deficit_usd` |
| per-market ceiling | $20 | `max_usd_per_market` |
| **max additional loss** | **$5.00** campaign-scoped | `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD` |
| **daily loss stop** | **$5.00 effective** (see §2) | account-wide halt binds |
| cumulative loss stop | $20, headroom $14.7543 | L5-scoped, unchanged |
| opening notional | $0.80 | `opening_notional_budget_usd` |
| opening clip ceiling | 2 contracts | `ts_opening_leg_max_contracts` |
| min first-leg price | $0.20 | `ts_min_first_leg_own_price` |
| **pair ceiling** | **0.96 — UNCHANGED** | `TS.max_pair_cost` |
| max unmatched | 5 contracts | unchanged |
| max residual | 10 contracts | `max_abs_residual` |
| concurrency | **1 market, 1 live order** | unchanged |
| execution | **maker-only; no crossing, no taker** | `maker_only_forbids_defensive_crossing` true |
| defensive completion | **disabled** | `DEF.enabled()` false |
| residual policy | `RESIDUAL_CAP_k2`, k=2, frozen rule `030d8bf` | unchanged |

### Cancellation behaviour

- order TTL **120s**, then cancel-and-reassess
- cancel margin **300s** before close; no resting order carried into the final 300s
- entry confined to the first ~300s of each 900s window (`ts_entry_cutoff_s` 600,
  `ts_min_entry_runway_s` 300)
- completion cutoff **720s**
- independent halt latch via `L5_HALT_URL`; `halt_gate_configured` is true and an **absent**
  latch refuses rather than permitting (`HALT_GATE_ABSENT`)
- **fail-closed on crash or completion — no automatic relaunch**

### Watchdog

`l5-halt-watchdog` and `l5-e1-safety-monitor`, already running, 20s sweeps reporting
`open_orders`, `overdue`, `unverified`, `still_open`, `not_ours`. No new service.

### Settlement reconciliation

Per window: `L5_WINDOW_ACCOUNTING` advances PROVISIONAL → **CONFIRMED** only on
`resolution_source: VENUE_SETTLEMENT_PX` with `venue_state: MARKET_STATE_EXPIRED`. Then
`L5_CAMPAIGN_ROLLING` for the campaign total. `realized_net_usd` counts **venue-confirmed
settlement lines over CONFIRMED windows only**; estimated, pending and credited rebates are
all reported separately and never inside it.

## 4. What the campaign can actually answer

With 8 windows at the observed ~0.5 fills/window this yields roughly **4 fills**. That is
enough to test the shadow assumptions it is aimed at, and not enough for a P&L verdict —
stated here so the result is not over-read in either direction:

| shadow assumption | campaign test | prior |
|---|---|---|
| maker fill rate 0.4325 per leg at 30s | fills / resting orders | modelled, never validated live |
| realized pair cost equals the book's `1 − spread` | filled price vs book at submit | identity, untested in execution |
| **a rebate credits at our clip** | credited rebate rows | $0.80/0.44 = 1.82 ct → raw $0.0056 → **$0.01, observable** |
| strand rate ~27.5% | windows ending unmatched | sim 27.5%, live 28–33% on 13 windows |
| cancellations complete before fill | cancel acks vs fills | never measured live |

**It cannot answer whether the strategy is profitable.** At 4 fills, and with a per-window
effect of a few cents against residual swings of ±$0.87, no 8-window campaign resolves
expectancy. Treating a positive or negative outcome here as a profitability verdict would
repeat exactly the error the new evidence standard forbids.

## 5. Authorization still required

Not armed and not requested here. To arm, the owner must supply a fresh approval document:
`approved`, `nonce`, `series`, `issued_utc`, `expires_utc`, `max_windows`,
`max_total_exposure_usd`, `max_daily_loss_usd`. The code intersects and never widens, and the
nonce is single-use.

**Current state: `armed=false`, `shadow_lock=true`, `campaign_id=null`, cap 0.96, every risk
limit at its deployed value. No deployment, no new service, no strategy change.**
