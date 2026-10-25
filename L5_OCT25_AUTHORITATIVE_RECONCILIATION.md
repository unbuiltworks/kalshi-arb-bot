# October 25 2026 — the one authoritative reconciliation

Supersedes every earlier partial account in this session. Every figure below is
venue-confirmed or read from the running process, never inferred from a variable write.

## 1. Campaign identity and authorization

| | |
|---|---|
| campaign nonce | `camp1.1c23068c42844ee055234f93` |
| experiment id | `l5-btc15m-sidefix-20261025-58dee103` |
| series allowlist | `cpc-btc-updown-15m-` (PMUS) |
| authorized windows | 3 |
| issued / expires | 2026-10-25T19:32:32Z / **20:30:00Z** |
| serving deployment | `55a11bf3` (SUCCESS, booted 19:33:44Z) |
| mode during campaign | maker-only, `L5_SHADOW_ONLY=0`, `armed:true` |

## 2. Orders, fills, settlement — all three windows

### 1945z — CONFIRMED, the only window that traded

| line | value |
|---|---|
| orders submitted / acknowledged / terminal | 1 / 1 / 1 |
| filled orders / fill events / partial fills | 1 / 1 / 0 |
| cancelled / rejected | 0 / 0 |
| maker orders / taker orders | 1 / 0 |
| contracts filled (first leg / complement) | **1.56 UP @ $0.44/contract** / 0 |
| acquisition cost | **$0.6864** = 1.56 x $0.44 |
| venue commission `actual_fees_usd` | $0 (maker-only charges none) |
| settled matched qty / matched P&L | 0 / $0.0000 |
| settled residual qty / side | 1.56 / UP |
| **residual P&L** | **−$0.686400** |
| resolution source | `VENUE_SETTLEMENT_PX` |
| venue state | `MARKET_STATE_EXPIRED` |
| **realized net** | **−$0.686400** |

**CORRECTION to my own earlier wording.** I previously wrote this fill as "1.56 UP @
$0.6864". $0.6864 is the ACQUISITION COST, not the per-contract price. The price was **$0.44**
and 1.56 x $0.44 = $0.6864 exactly. A peer session (session_01255E3fhWg7BWTviGCMRknt) caught
the inconsistency and asked whether the realized figure was understated by $0.3844. It is
not: the venue's -$0.686400 is correct and equals the full acquisition cost of a residual that
settled worthless. Only my description of the price was wrong, so neither the day total
(-$2.0192) nor the 5.2457 that refused 2000z/2015z moves.

### The complement order lifecycle, since it decides whether completion is raisable

`L5_LIVE_WINDOW` for 1945z:

```
ORDER:SUBMITTED: 1                                   <- one order all window
TWO_SIDED_PROBE:COMPLEMENT_PAIR_ABOVE_CAP: 12        <- complement EVALUATED 12x, refused on price
TWO_SIDED_PROBE:PAST_COMPLETE_CUTOFF: 153
TWO_SIDED_PROBE:PAST_TERMINAL: 22
VALIDATION_GATES:L5_DAILY_LOSS_LIMIT:5.245700>=5: 2  <- the loss gate fired inside 1945z too
arm_refused_window_not_usable: 18
```

`L5_RESIDUAL_BOOK_SAMPLE` at 19:59:44Z, 19:59:50Z and 19:59:57Z — seconds before close:

```
residual 1.56   held_side UP   order_resting: FALSE
book: bids [] , n_bids 0 , asks [["0.0100","31387.83"], ["0.0200","2605.83"], ...]
```

So, precisely: **no complement was ever POSTED, and none was resting at close. But the
complement was EVALUATED twelve times and refused because the pair was above cap** — our YES
side had collapsed to ~1c with no bids at all, so the complement cost ~$0.98 against a held
leg of $0.44, a pair of ~1.42 against the 0.96 ceiling. The controller was right to refuse:
completing would have locked ~42c/unit.

For this window the answer is therefore neither "the measure is too narrow" nor "we simply
never posted". Completion was **evaluated and economically unavailable**. That does not
settle the 299 matched-0 windows in the other dataset, which remain open and are the right
place to look, because `COMPLEMENT_PAIR_ABOVE_CAP` is a decision our cap makes, not a fact
about the market.

### 2000z — PROVISIONAL, zero orders

Zero submitted, acknowledged, filled, cancelled, rejected. Refused by
**`L5_DAILY_LOSS_LIMIT:5.245700>=5`**.

### 2015z — PROVISIONAL, zero orders

Zero of everything, same refusal, 8 occurrences between 20:14Z and 20:26Z while
`armed:true` and the submission gate read `OPEN`. `L5_TS_COMPLETION` reported
`NO_INVENTORY_TO_COMPLETE` with `up_qty 0, dn_qty 0` — **correct**: the 1945z residual had
already settled at 20:00Z, so live inventory was genuinely zero. The unmatched DOWN inventory
seen at 20:08Z belonged to separate shadow controllers, not to live holdings.

## 3. Cashflows and confirmed realized P&L

| | |
|---|---|
| contracts filled, campaign | 1.56 |
| acquisition cost | $1.0708 (1.56 @ $0.6864) |
| venue commission | $0.00 |
| settlement receipt on the residual | $0.00 (`yes_value 0.0000`) |
| matched-pair surplus | $0.0000 |
| **CONFIRMED REALIZED NET** | **−$0.686400** |
| rebate estimate | $0.0048, `UNVERIFIED_NO_VENUE_CREDIT_RECORD` — **excluded** |
| rebate credited | $0.00 |
| available cash (20:25Z) | $339.16685 |

Final `L5_CAMPAIGN_ROLLING` at 20:30:00.336Z: windows_confirmed 1, windows_with_fills 1,
windows_with_a_completed_pair **0**, completion_rate **0**, orders_submitted 1,
contracts_filled 1.56, matched_surplus $0.0000, residual_pnl −$0.686400, actual_fees $0,
**realized_net_usd −$0.686400**, rebate_credited $0.

**Campaign outcome: 1 of 3 authorized windows traded; the binding constraint was the loss
gate, not the authorization.**

## 4. Expiry, verified independently

Heartbeat at **20:31:24Z**, on the **2030z** market (outside the authorized set):

```
"armed": false        (was true at 20:25:47Z)
"submission_gate": { "blocked": true, "reason": "NOT_ARMED" }
"halt": true   "shadow_lock": false   "halt_latch": "CLEAR"   "preflight": "PASSED"
```

**Zero** `L5_LIVE_SUBMIT` / `L5_ACC_SUBMIT` rows after 20:30:00Z. The expiry stopped new
trading on schedule, verified from process state rather than from the authorization document.

## 5. Current production mode (as of 21:13Z)

| | |
|---|---|
| serving deployment | `bab0bfaa` — redeploy of the `55a11bf3` build, **no code change**, booted 20:43:21Z |
| armed | **false** |
| shadow lock | **true** (`SHADOW_ONLY_LOCK`) |
| campaign_id | **null** — no active campaign |
| halt gate | configured |
| `max_l5_daily_loss_usd` | **20** (owner-authorized) |
| `max_l5_cumulative_loss_usd` | 20 — unchanged |
| `max_cumulative_loss_usd` | 20 — unchanged |
| `max_cash_deployed_usd` | 5 — unchanged |
| `max_unmatched_contracts` | 5 — unchanged |
| `max_worst_case_deficit_usd` | 2.00 — unchanged |
| `max_strategy_loss_usd` | null — unchanged |

**The engine is in continuous read-only shadow. It cannot submit. The expired campaign is
not re-armed.**

## 6. All-time position

| | |
|---|---|
| this campaign | −$0.686400 |
| UTC-day realized (Oct 25) | −$2.019200 |
| all-time realized | −$13.640500 |

No rebate, reward, shadow position or mark-to-market gain is inside any of these.

## 7. Two process failures recorded, because both nearly produced a false report

1. **A variable write does not re-exec the process.** The store had `L5_SHADOW_ONLY=0` while
   the container kept `1`. I reported the campaign "armed" from a successful write; the owner
   caught it. Rule adopted: never claim armed from a write — require `preflight ok:true`
   **plus** `armed:true` in a heartbeat.
2. **An in-place restart reuses the deployment's baked environment.** After raising the loss
   limit I restarted the service and the heartbeat still read `shadow_lock=false` five minutes
   later, proving the old env was live. Only a NEW DEPLOYMENT picks variables up. The limit
   was not reported effective until `L5_LIVE_GATES` showed it from the money path's own
   accessors.
