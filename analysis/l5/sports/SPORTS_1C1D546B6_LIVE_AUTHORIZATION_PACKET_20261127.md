# Live authorization packet: sports release 1c1d546b6, three hours

27 Nov 2026. **Not deployed or armed.**

## Release

- **SHA:** `1c1d546b6d80ccb3eb5c98bf295b7e84e8195e7a`, branch
  `release/pmus-sports-next-1c1d546b6`.
- **Contents:** the accepted integrated candidate `a17a9c978`, plus the
  test-only fixture `8e6366786`, plus a trigger line. `src/` is identical to
  `a17a9c978`.
- **Regression:**
  - full suite flags off: 24 failed / 6,003 passed against `31ddf1b06` at
    24 / 5,755, with identical names;
  - enabled-mode safety module: all 57 pass under the activation config and
    flags off;
  - targeted suites under the activation config: 57 classified failures, no
    safety failure (SPORTS_RELEASE_NEXT_20261127.md §10).

## Timing

Stage 1 is not extended. Its window ends 05:45 and its completions-only
grace ends 07:45 UTC.

**Proposed window: 08:00–11:00 UTC 27 Nov, completions-only grace to
13:00.** The steps:
1. After the 07:50 Stage 1 close-out (lock `1`), deploy `1c1d546b6` with the
   activation variables and lock `1`.
2. Verify boot, reconcile, account risk and watchdog.
3. Set `PMUS_TWO_SIDED_SUBMISSION_LOCK=window:2026-11-27T08:00:00Z/2026-11-27T11:00:00Z`.
4. If verification finishes after 07:55, start at the next quarter hour for
   three hours.

The window starts after Stage 1 so that Stage 1's carried inventory is not
outside the new campaign's spend and unpaired accounting.
- It still counts in at-risk, lane, account drawdown and the loss stops.
- Carried markets keep their completion bids and reconciliation.

## Activation variables (sports service; all others unchanged)

| Variable | Value | Note |
|---|---|---|
| `PMUS_RN1_MARGINAL_EV` | 1 | already 1 |
| `PMUS_RN1_FIT_ALL_LEVELS` | 1 | already 1 |
| `PMUS_RN1_EDGE_GATE` | 0 | already 0 |
| `PMUS_RN1_MARGINAL_EV_NO_FAIR` | 1 | new |
| `PMUS_RN1_SLOTS_COUNT_INVENTORY` | 0 | new |
| `PMUS_RN1_LIVE_FIRST` | 1 | new |
| `PMUS_RN1_SELECT_NO_CHURN` | 1 | new |
| `PMUS_RN1_DURATION_RANK` | 1 | new |
| `PMUS_ECONOMICS_REPORT` | 1 | new; logging only |
| `PMUS_RN1_TOUCH_EV` | 1 | new |
| `PMUS_RN1_REWARD_CALIBRATION` | 1 | new |
| `PMUS_RN1_REWARD_CALIBRATION_TABLE` | `*:0.4` | provisional, not a verified payout ratio |
| `PMUS_RN1_REWARD_POOL_SCOPE` | `market` | default |
| `PMUS_RN1_SIDE_INDEPENDENT` | 1 | new |
| `PMUS_RN1_EV_HOLD` | 1 | new; 60 s window, $0.002 replace cost |
| `PMUS_RN1_NET_REPOST_MIN_S` | 120 | new |
| `PMUS_RN1_FILL_LOSS_TOUCH` | 0.089 | measured, n 5 |
| `PMUS_RN1_FILL_LOSS_BEHIND` | `0.042,atp:0.063,atpdb:0.06,wtadb:0.06` | measured |
| `PMUS_RN1_FILL_RATE_TOUCH` | 4.0 | |
| `PMUS_RN1_FILL_RATE_BEHIND` | 1.5 | |
| `PMUS_RN1_JOIN_MAX_RANGE_COMP` | unset | volatility threshold stays 1c |
| `PMUS_TWO_SIDED_MAX_MARKETS` | 2 | unchanged |
| `PMUS_RN1_PIN` | empty | unchanged |

## Limits (all unchanged)

| Limit | Value |
|---|---|
| Account drawdown | $20 (room now 11.29) |
| Lane | $20 |
| At-risk (fills + resting + in-flight) | $20 |
| Unpaired, total / per market | $10 / $5 |
| Per market | $12 |
| Campaign spend | $60 |
| Campaign loss stop, resting counted | $10 |
| Bot daily stop | $10 |
| Ledger daily halt / cumulative ceiling | $50 / $75 (room 14.02) |
| Recovery unmatched halt | $15 |

Maker-only, post-only, the final-gate loss backstop, the lock, latch, jump
pause, reactive pull and watchdog cancel all stay on. Esports off; crypto
unchanged and disarmed; no automatic repeat.

## Measured during the window, reported hourly

- touch fills and their 300 s markouts against 8.9c;
- qualifying time at the touch against behind;
- `REWARD_CALIBRATION` ratios;
- per-side replacement latency and blocked seconds against Stage 1;
- inventory by side, capital in use;
- settled P&L (settlement-only) and PAID rewards;
- `CAMPAIGN_ECONOMICS`.

## Stop conditions (relock to `1`)

- the campaign loss stop or the daily halt;
- touch-fill loss above 12c after 5 or more touch fills;
- any `gate_error` or reconciliation latch;
- any order that is not post-only;
- the reward check down for 2 hours;
- a reconciliation failure.

## Rollback

Set the source back to `31ddf1b06341d15712fe655693acd028a5077e9f`, or unset
the new flags. Either way the lock goes to `1` first.

## Authorization line

> Authorized: after the Stage 1 close-out, deploy sports release 1c1d546b6d80ccb3eb5c98bf295b7e84e8195e7a to pmus-spring-l5-probe with the lock held at 1 and the activation variables in SPORTS_1C1D546B6_LIVE_AUTHORIZATION_PACKET_20261127.md; after boot, reconcile, account-risk and watchdog verification run a three-hour campaign 08:00–11:00 UTC 27 Nov (or the next quarter hour after verification if later, for three hours) with a two-hour completions-only grace; PMUS_TWO_SIDED_MAX_MARKETS=2, PMUS_RN1_PIN empty; all capital limits, loss stops and protections unchanged; maker-only; esports off; crypto unchanged and disarmed.
