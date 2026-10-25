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
| contracts filled (first leg / complement) | **1.56 UP** / 0 |
| venue commission `actual_fees_usd` | $0 (maker-only charges none) |
| settled matched qty / matched P&L | 0 / $0.0000 |
| settled residual qty / side | 1.56 / UP |
| **residual P&L** | **−$0.686400** |
| resolution source | `VENUE_SETTLEMENT_PX` |
| venue state | `MARKET_STATE_EXPIRED` |
| **realized net** | **−$0.686400** |

The complement was never acquired. The controller refused it, correctly: the fee-safe pair
had moved to ~1.38 against a 0.96 ceiling, so completing would have locked a ~38c/unit loss.

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
