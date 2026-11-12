# BTC5m minimal live execution test — package (prepared 2026-11-12 00:20Z, NOT executed)

Nothing here has been deployed, armed or submitted. Every step marked
OWNER needs the owner's explicit approval or action; I do not take them.

**UPDATE 2026-11-12 01:00Z.** `l5-pmus-live` now runs `8a17b720c` (PR #224),
DISARMED, and publishes the account-wide loss state. The BTC5m session arms
only through `authorize_arming` (candidate `9660592ca`, PR #225). The
manual 19:15Z cost is verified from authenticated venue records:
`manual_1915z_cost_basis_venue.json` ($2.10 actual vs $5.00 booked). The
01:30Z window below has expired; the current proposal is in
`L5_STATUS.md` and in the PR #225 description.

## What the test must show, and where each answer comes from

| question | evidence the session emits |
|---|---|
| does the venue accept our 5-minute order? | `L5_BTC5M_STEP.placed` with the venue's own order id (`venue_order_id`); `submit_uncertain` stays 0 |
| did it fill? | `L5_BTC5M_STEP.fills`, booked only from the venue's `/v1/order/{id}` `cumQuantity` |
| is the cancel exact? | `L5_BTC5M_CLOSE.cancelled`: venue ids from this ledger only, each re-read to a terminal state; `close.clean` |
| does the position reconcile? | `reconcile`: our UP/DOWN inventory beside the venue's own position row (`agrees`) |
| how did it settle? | `settlement`: the venue's own `settlementPx`, recorded once |
| fees and net | `venue_fees`: the venue's `commissionNotionalTotalCollected` per order; `settled_net_usd` = settlement on confirmed fills minus those fees, labelled realized once the venue credits the redemption |

`would_submit_not_sent` counts decisions a disarmed transport did not send.
They are reported as that and never as trades.

## The proposed test

| item | value |
|---|---|
| market | `cpc-btc-updown-5m-2026-11-12-0130z` (exists on the venue; tick 0.01, minimum 0.01, fee coefficient 0.095) |
| window | 01:30:00–01:35:00 UTC, 2026-11-12. Orders only from 01:30:00 to 01:34:30; every own order cancelled from 01:34:30 |
| strategy | the BTC5m favorite controller with the venue-index probability, maker-only (post-only buys), taker never |
| exposure cap | $1.00 (approval document `max_total_exposure_usd`; also `L5_ACCOUNT_MAX_EXPOSURE_USD=1.00` on the service) |
| per-order size | about $0.10 of notional (controller default) |
| strategy daily loss | $1.00 (approval document `max_daily_loss_usd`) |
| account daily halt | $5.00, unchanged (today: $0.00 realized) |
| account lifetime ceiling | $18.6325, unchanged — **currently exceeded, see below** |
| submissions | total 20; OPEN 6, COMPLETE 8, REPLACE 6 |
| windows | exactly one; the service idles after it and refuses the market on restart |
| expected result | small and probably negative: about -1c per window in the 42-window replay (hypothetical fills); worst case -$1.00 |

01:30Z is the earliest window that leaves about an hour for the owner steps
below. If they finish later, the owner names a later 5-minute window; the
approval names exactly one, and the session never picks another.

## The blocker that is the owner's decision

The account's all-time realized is **-$21.0406** against
`L5_MAX_CUMULATIVE_LOSS_USD=18.6325`. The session reads that state from the
BTC15m service and refuses while it is exceeded, and also refuses while the
$1.00 cap is larger than the remaining room. I have not changed it and will
not.

The owner's options, with the numbers:
1. **Restate the manual 19:15Z trade from the venue trade export.** It is
   booked at its $5.00 bound; cash-estimated about $2.42. Restated, the
   all-time figure would be about -$18.46, leaving about $0.17 of room. The
   test would then need a cap at or below that room (about $0.15: one or two
   orders of about $0.10).
2. **Change the ceiling yourself.** A new value is your decision. With
   -$21.0406 already realized, the ceiling must exceed $22.04 for a $1.00
   cap to fit.
3. Neither: the test stays blocked.

## Steps, in order

1. OWNER: approve and merge PR (candidate) into
   `integration/l5-safe-release-candidate-20261109`, and approve a DISARMED
   deploy of that commit to `l5-pmus-live`. No variable changes. Needed
   because BTC5m reads the account-wide loss state from the
   `account_risk` block that only this candidate publishes.
   Verify: deployment SUCCESS on the merged SHA; `L5_LIVE_HALT=1` and
   `L5_SHADOW_ONLY=1` unchanged; 0 live orders; GET
   `http://l5-pmus-live.railway.internal:8081/l5/live_orders.json` carries
   `account_risk.readable: true`. Rollback: deploy `ff4b5291a` by commit.
2. OWNER: decide the lifetime ceiling (above).
3. OWNER: create service `l5-btc5m-live` in the same project and
   environment: repository branch with the merged commit, config file
   `deploy/l5_btc5m_live/railway.json` (Dockerfile build, restart policy
   NEVER, one replica), a volume mounted at `/state`, and these variables:

   | variable | value |
   |---|---|
   | PMUS credentials | owner-supplied, same account |
   | `PMUS_Q1_*` arm token | owner-supplied; without it the transport does not arm |
   | `PMUS_SERVICE_ROLE` / `L5_LIVE_AUTH_OWNER_ROLE` | `l5-btc5m-live` / `l5-btc5m-live` |
   | `L5_LIVE_AUTH_LEDGER` | `/state/btc5m/auth.jsonl` |
   | `L5_LIVE_ROOT` | `/state/btc5m` |
   | `L5_EXPERIMENT_ID` | e.g. `b5m-live-0130z` |
   | `L5_LIVE_AUTH_MAX_SUBMISSIONS` | `20` |
   | `L5_LIVE_AUTH_MAX_OPEN` / `_COMPLETE` / `_REPLACE` | `6` / `8` / `6` |
   | `L5_BTC5M_CONTROLLER` | `ON` |
   | `L5_BTC5M_LIVE` | `ARMED_BY_OWNER` |
   | `L5_BTC5M_MARKET` | `cpc-btc-updown-5m-2026-11-12-0130z` |
   | `L5_BTC5M_NOT_AFTER` | `2026-11-12T01:35:00Z` |
   | `L5_ACCOUNT_MAX_EXPOSURE_USD` | `1.00` (or the cap from option 1) |
   | `L5_SHADOW_ONLY` | `0` (this service only) |
   | `L5_LIVE_HALT` | unset (this service's own kill switch; set any value to stop it) |
   | `L5_HALT_URL` | `http://l5-halt-watchdog.railway.internal:8080` |
   | `L5_ACCOUNT_RISK_URL` | `http://l5-pmus-live.railway.internal:8081/l5/live_orders.json` |
   | `L5_BTC5M_FILLS_PORT` | `8082` |
   | `L5_CAMPAIGN_APPROVAL` | the approval document below |

4. OWNER: the approval document (set on `l5-btc5m-live` only):

   ```json
   {"approved": true, "nonce": "<at least 8 characters, new>",
    "series": "cpc-btc-updown-5m-",
    "issued_utc": "<when you set it, ISO-8601 with Z>",
    "expires_utc": "2026-11-12T02:30:00Z",
    "window_starts": ["2026-11-12T01:30:00Z"], "max_windows": 1,
    "max_total_exposure_usd": "1.00", "max_daily_loss_usd": "1.00",
    "max_total_submissions": "20"}
   ```

5. Optional, so the BTC15m ledger restates the BTC5m position at its real
   cost if it ever adopts it: `L5_BTC5M_FILLS_URL=http://l5-btc5m-live.railway.internal:8082/btc5m_fills`
   on `l5-pmus-live`. Unset is safe (the bound stays).

## What protects the account during the test

- Every order spends the owner's single-use claim for this window, with
  the exposure and daily-loss figures from the document; a refusal sends
  nothing.
- Every 5 seconds the session reads the watchdog's halt record, its own
  `L5_LIVE_HALT`, and the account-wide loss state. Any refusal cancels its
  own orders and places nothing more.
- The independent watchdog (`l5-halt-watchdog`, running, 0 orders, not
  halted) asserts the shared halt on any order on the account resting
  longer than 300 s. It cancels only ids the BTC15m registry names, so it
  does not cancel BTC5m orders; the session cancels its own, quotes live at
  most 20 s, and an unmanaged order ends when the market closes at 01:35Z.
- The BTC15m service stays disarmed and never adopts or cancels a BTC5m
  order.

## Reading the result

All lines are in the `l5-btc5m-live` deploy log: `L5_BTC5M_START`,
`L5_BTC5M_STEP`, `L5_BTC5M_BLOCKED`, `L5_BTC5M_CLOSE`, and one
`L5_BTC5M_SESSION_REPORT`.
