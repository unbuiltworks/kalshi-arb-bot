# Two-window live validation — result, 2026-11-06

Owner-authorized bounded test. One window of two executed. Everything below
is venue-confirmed unless explicitly labelled an estimate.

## Window 1 — 20:00:00Z — DID NOT ARM

```
L5_LIVE_DISARMED  market=...-2000z  why=AUTHORIZATION_EXPIRED
                  not_after=2026-11-06T03:45:00+00:00
                  now=2026-11-06T20:00:01.564690+00:00
                  boundary=l5_live_authorization.authorize_arming
```

`L5_LIVE_AUTH_NOT_AFTER` had lapsed ~16 h earlier. Refused at **step 26**,
which is after the Ed25519 mandate check and after
`CAMPAIGN_LIMITS_WIDER_THAN_APPROVED` — so the signed mandate was valid and
the $3 cap was correctly inside the approval. Zero capital at risk.

**The check was not missing; the verdict was not surfaced.**
`L5_AUTHORIZATION_PREFLIGHT` caught it at the 19:34 boot, 26 minutes early:
`ok: false`, `fail: ["absolute_deadline"]`, `expires_in_s: -56974.4`, every
other check PASS. Nobody read that line. Fixed in `b66370919` by putting the
compact verdict in the heartbeat under `HB_NEVER_REDUCE`, continuous rather
than boot-only. Window 1 was NOT replaced.

## Window 2 — 20:15:00Z — ARMED AND TRADED

```
L5_CAMPAIGN_BASELINE  captured=true  campaign_id=camp1.l5-i195-2w-1228f6db651961ac
                      account_wide_realized=-14.74950000  cash=341.70505
L5_LIVE_ARMED         armed=true  preflight=PASSED  execution_mode=MAKER_ONLY
                      claim bounded_by=VENUE_CLOSE at 20:29:00
                      scope.not_after=2026-11-06T20:40:00+00:00
```

### Venue-confirmed fills

| time | venue id | side | avg_px | qty | commission |
|---|---|---|---|---|---|
| 20:17:32.829 | DJDCEDVG13Z6 | UP | 0.3800 | 1 | 0.0000 |
| 20:17:50.478 | DJDCFKAG14F1 | DOWN | 0.4000 wire | 1 | 0.0000 |
| 20:17:54.607 | DJDCK99G14ET | DOWN | 0.4000 wire | 1 | 0.0000 |
| 20:18:08.941 | DJDCMP7G13ZD | UP | 0.3800 | 1 | 0.0000 |
| 20:18:39.526 | DJDCW3F013YB | UP | 0.3800 | 0.03 | 0.0000 |

All `venue_confirmed: true`. `DJDCMP7G13ZD` arrived via
`PENDING_TERMINAL_RESOLVED` — "final fill booked from the venue's own order
row; this is the reconstruction that resolves a lost cost basis, not an
assumption about it". Totals from settlement: **UP 4.03 @ $1.5414**,
**DOWN 4 @ $2.4200** (own terms).

### Settlement — VENUE_SETTLEMENT_PX, yes_value 0.0000 (DOWN won)

```
matched_qty      4
matched_cost     3.949925558312655086848635236
matched_redeem   4
matched_pnl     +0.050074441687344913151364764
residual_qty     0.03 UP
residual_cost    0.011474441687344913151364764
residual_redeem  0.000000
residual_pnl    -0.011474441687344913151364764
cost_total       3.961400
redemption_total 4.000000
settlement_pnl  +0.038600
realized_today_after  -14.71090000   (from -14.74950000)
```

**Confirmed settled realized net P&L: +$0.0386.** The lifetime figure moved
-14.74950000 -> -14.71090000, a delta of exactly +0.0386, so the window's
economics reconcile against the account.

### Second-leg completion — issue #195's actual defect, resolved

```
20:16:47.515  L5_TS_COMPLETION_TRIGGER  side=UP held_side=DOWN qty=1
              price=0.4300 resting_orders=0 route=CONTROLLER_REENTRY
              ranked=TAKER_COMPLETE
              ranking_basis=EXPECTED_WORSE_SETTLEMENT_OUTCOME_AT_ESTIMATED_FILL_PROBABILITY
20:16:47.784  L5_TS_COMPLETION_RESULT
              outcome={"captured":true,"submitted":true,"refused":null}
              window_outlay_usd=0.4500
```

`submitted: true`, `refused: null`, and **zero `WOULD_SUBMIT` anywhere in the
tape**. That is the exact state issue #195 was opened about — a completion
decision that became neither an order nor a named refusal.

### Exact-order cancellation, venue-verified

```
L5_ACC_DEADLINE  reason=acc_order_deadline  ttl_s=120
  cancelled=[{client_order_id: l5f-2effb12f482e4e0bb1309ef0,
              venue_order_id: DJDCVKR0144P, side: DOWN,
              venue_state: ORDER_STATE_CANCELED, cum_qty: 0,
              verify_attempts: 3, verify_errors: []}]
  errors=[] unverified=[] still_open=[] filled=[]
```

### Acceptance

| # | target | result |
|---|---|---|
| B1 | simultaneous quoting | **PASS** — UP own 0.3600 @20:15:41.181 and DOWN own 0.6000 @20:15:41.854, 0.67 s apart, pair cost 0.96 |
| B2 | maker fill, attested basis | **PASS** — 5+ venue-confirmed fills with recorded avg_px |
| B3 | second-leg completion | **PASS** — `submitted: true`, zero WOULD_SUBMIT |
| B4 | repricing / exact-id cancel | **PASS** — exact coid+venue id, 3 verify attempts, no errors |
| B5 | rollover latency | **PASS** vs the 10 s budget (~6.9 s; <2 s still open) |
| B6 | limits held | **PASS** — see below |

### Limits, after

`armed false`, `submission_gate NOT_ARMED`, `open_orders 0`,
`orders_uncertain` none, `unresolved_accounting 0 / blocks false`,
`windows_spent 1 / remaining 1 / max 2`, `submissions_spent 18`,
`utc_day_realized -0.931400` (room 4.068600), zero tracebacks, zero CRITICAL.

**Executor is DISARMED.** 20:30z was refused `WINDOW_NOT_IN_AUTHORIZED_SET`;
20:29z was refused `CAMPAIGN_WINDOW_NOT_USABLE /
PAST_ENTRY_WITH_NO_INVENTORY` with `claim_taken: false`.

## What is NOT confirmed

* **Maker rebates: $0 confirmed.** Every fill shows `commission: 0.0000` and
  every pre-submit carries `venue_actual: null`,
  `venue_actual_status: PENDING`. The `0.0029-0.0031` per order is a
  `schedule_estimate`. Per the tape's own label, "a zero commission is NOT a
  rebate". Nothing is claimed as credited.
* **Whether the $2 lifetime completion budget was charged.** The heartbeat
  does not emit `defensive_spent_usd` under that key, so I listed a name in
  `HB_NEVER_REDUCE` that the runner never emits. The completion ran under
  `execution_mode MAKER_ONLY` at own 0.4300 against an ask of 0.4200, which
  would cross — but I have not proven from the tape whether the defensive
  charge was taken. Open item.
* **n = 1.** One window, +$0.0386, 4 matched contracts. This establishes
  mechanism, not expectancy.

## The bottleneck for profitable paired execution

Queue position, measured on every pre-submit: `queue_ahead` of
**1,096 / 1,152 / 1,665 / 1,946 / 2,109 / 2,166 / 2,279 / 2,428 / 2,975**
contracts against a 1-contract order resting 2 ticks behind the touch, with
`L5_MISSED_PAIR` firing **76 times** in the window. The fills that did land
came after ~2 minutes of repricing, and the pair completed only because both
sides eventually filled.

Fee asymmetry says the maker side is the only side with edge:
`maker_rebate_usd` 0.0029-0.0031 against `taker_fee_usd_if_we_crossed`
0.0160-0.0170 — about 5.5x. Crossing to force a fill destroys the pair
economics it is meant to secure.

So the next bottleneck is **fill probability at a non-crossing price**, not
authorization, not rollover latency, and not the completion route.
