# One BTC5m live window — configuration and arming packet (issue #247)

Prepared 2026-11-20 04:20Z. **Nothing here is applied.** `l5-btc5m-live` is
disarmed on `a178c20f4348`. No order has been submitted. Each step below
needs the owner's explicit approval.

Variable *values* on the service have never been read (that call returns the
account's API secrets), so where a current value is unknown the step sets it
explicitly or says what would show it.

## Window

`T` = window start. Default **2026-11-20 15:00:00Z**; any later 5-minute
boundary works by substitution.

| | |
|---|---|
| market | `cpc-btc-updown-5m-2026-11-20-1500z` |
| trading window | 15:00:00Z to 15:05:00Z; the lane stops placing at 15:04:30Z |
| `L5_BTC5M_NOT_AFTER` | `2026-11-20T15:05:00Z` |

## Three corrections to the earlier configuration, found by reading the code

1. **`L5_BTC5M_VENUE_CONSTRAINTS=REQUIRE` would have stopped every order on
   the deployed build.** The session rebuilt the book it steps the executor
   with and dropped the venue's minimum size and tick, so the check could
   never pass in production. Fixed on this branch (`step_book`, 5 new tests);
   not deployed. Without the fix, leave that variable unset, as in every
   earlier live window.
2. **The envelope must name strategy `STRADDLE`**, not `BTC5M`.
3. **The campaign approval is valid for 4 hours**, the probe document for 6.
   Both must be written close to the window.

## Step 1 — build (only if the venue-size check is wanted)

Release the commit containing this file to `l5-btc5m-live`, disarmed. Its
only code change from `a178c20f4348` is `step_book` in `l5_btc5m_runner.py`.
`DEPLOY_TRIGGER` is updated in the same commit so Railway builds it.

## Step 2 — configuration, still disarmed (one change set, one restart)

| variable | set to | why |
|---|---|---|
| `L5_BTC5M_STRADDLE_REQUOTE_TICKS` | `2` | follow the book |
| `L5_BTC5M_STRADDLE_MAX_WINDOW_LOSS_USD` | `0.20` | window loss ceiling |
| `L5_BTC5M_STRADDLE_LEG_MAX_AGE_S` | `240` | clear of the watchdog's 300s |
| `L5_BTC5M_INVENTORY_RANKING` | empty | the probe refuses with ranking on |
| `L5_BTC5M_STRADDLE_P_PAIR_NATURAL`, `_P_COMPLETE`, `_P_LONE_WIN` | empty | the probe refuses while any rate is set |
| `L5_LATE_FAVORITE`, `L5_LATE_FAVORITE_WINDOW_S` | empty | off |
| `L5_BTC5M_TAKER_COMPLETION` | empty | no taker |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | `60` | total submissions |
| `L5_BTC5M_MARKET` | the market above | one named window |
| `L5_BTC5M_NOT_AFTER` | `2026-11-20T15:05:00Z` | |
| `L5_DEPLOYMENT_ID` | `btc5m-probe-20261120` | an owner-chosen label the envelope must repeat exactly; it is compared with nothing else |
| `L5_BTC5M_VENUE_CONSTRAINTS` | `REQUIRE` | only with step 1 |

Unchanged: clip `0.05`, per side `0.30`, unmatched `0.10`, pair ceiling
`0.99`, book-relative on, repeat on, two live orders, account cap, arm token,
shadow lock, envelope. The lane stays `can_submit: false`.

Verify from the boot log: `L5_BTC5M_EFFECTIVE_CONFIG` shows requote 2, loss
ceiling 0.20, leg age 240, ranking false, the three rates unset, taker
false, live-order bound 2; `L5_BTC5M_PLAN` names the market.

## Step 3 — arming (one change set, one restart, before `T`)

The lane must boot **before** `T` (after `T` it refuses
`CAMPAIGN_ALREADY_STARTED`), and `l5-pmus-live` must not have restarted in
the 15 minutes before. All of these together:

| variable | value |
|---|---|
| `L5_PERSISTENT_ENVELOPE` | document A |
| `L5_CAMPAIGN_APPROVAL_BTC5M` | document B |
| `L5_BTC5M_BEHAVIOUR_PROBE` | document C |
| `L5_BTC5M_LIVE` | `ARMED_BY_OWNER` |
| `L5_SHADOW_ONLY` | `0` |
| `L5_SUBMIT_PAUSE` | `0` |
| `L5_LIVE_HALT` | empty |
| `PMUS_Q1_LIVE` / `PMUS_Q1_SESSION_ID` / `PMUS_Q1_ARM` | `YES` / one label / the same label |

**A — standing envelope.** Expires one minute after the window so the lane
leaves through its own end-of-campaign path and writes its resting state.

```json
{"approved":true,"mode":"PERSISTENT","deployment":"btc5m-probe-20261120","strategies":["STRADDLE"],"series":["cpc-btc-updown-5m-"],"taker_allowed":false,"max_aggregate_usd":"0.60","max_per_market_usd":"0.60","max_daily_loss_usd":"0.60","expires_utc":"2026-11-20T15:06:00Z","nonce":"env-<8+ characters>"}
```

**B — campaign approval.** One window. A new nonce starts every submission
counter at zero.

```json
{"approved":true,"nonce":"b5m-<8+ characters>","series":"cpc-btc-updown-5m-","issued_utc":"<when written, within 4h before T>","expires_utc":"2026-11-20T15:10:00Z","max_windows":1,"max_total_exposure_usd":"0.60","max_daily_loss_usd":"0.60","window_starts":["2026-11-20T15:00:00Z"]}
```

**C — behaviour probe.**

```json
{"class":"BEHAVIOUR_PROBE_EV_UNPROVEN","acknowledgement":"ENTRY EV IS UNPROVEN. NO PAIRING RATE HAS BEEN MEASURED OR IS ASSERTED. THIS WINDOW IS EXPECTED TO LOSE MONEY, UP TO max_window_loss_usd.","entry_ev":"UNPROVEN","market":"cpc-btc-updown-5m-2026-11-20-1500z","window_start_utc":"2026-11-20T15:00:00Z","expires_utc":"2026-11-20T15:05:00Z","issued_utc":"<when written, within 6h before T>","max_window_loss_usd":"0.20","nonce":"probe-<8+ characters>"}
```

## What happens before the first order

In this order, each a refusal that sends nothing:

1. the envelope parses for this deployment and the durable state arms;
2. the transport preflight: credentials, market open, **no open order on
   the account that a managed lane does not claim**;
3. arming: shadow lock, pause, halt, campaign approval, reconciliation;
4. **every 5 seconds, before any decision: the watchdog's halt, then the
   account-risk record** — it must be under 30 seconds old, carry both
   limits with room, and that room ($1.00) must cover the approved exposure
   ($0.60);
5. per order: post-only, exposure caps, the submission budget, the envelope,
   the shared reservation.

## Bounds

| | |
|---|---|
| orders | post-only buys, UP and DOWN together; no taker, no completion crossing |
| clip / per side / unmatched | $0.05 / $0.30 / $0.10 |
| live orders | 2 |
| window loss ceiling | $0.20 |
| approved exposure | $0.60 |
| account room | $1.00 from $321.4782 |
| submissions | 60 |
| watchdog | independent; 300s order age; cancels exact owned ids |

## Not known until it runs

* Several switches exist on the service with values I have not read: the
  shadow lock, the pause, the three transport tokens, the experiment id, the
  halt URL, the shared reservation path, `L5_BTC5M_STRADDLE_PRICE` (must be
  between 0 and 0.50 even though it is not used for pricing here). Step 3
  sets the arming ones. A wrong value among the rest makes the lane refuse;
  it cannot make it trade more.
* A stored state of HALTED or STOPPED from an earlier campaign cannot be
  cleared by any code path and would stop arming. The disarmed loop has been
  resting normally, which suggests it is not latched, but that is only
  confirmed when a valid envelope is read.
* **If the watchdog's halt record cannot be read during the window, the
  lane latches HALTED permanently** and stops. That is by design.
* Whether the venue accepts a $0.05 order. At 0.50 that is 0.10 share. The
  size check in step 1 reads the venue's minimum before the first order.
* The entry-EV gate is waived for this window by document C. It is expected
  to lose money, up to $0.20.

## After the window

Disarm in one change set: `L5_BTC5M_LIVE` empty, `L5_SHADOW_ONLY=1`. Then
the report: actual fills and settled net P&L from venue cashflows, not
simulated figures.

Rollback of the build: Railway deployment `cd13ec23` (`a178c20f4348`).
