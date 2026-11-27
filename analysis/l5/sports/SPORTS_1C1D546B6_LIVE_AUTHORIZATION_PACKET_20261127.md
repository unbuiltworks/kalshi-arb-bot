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

---

## Revision 1 — 27 Nov 2026, 12:25 UTC (after the Stage 1 close-out)

This revision replaces the Timing section, the two changed limits and the
authorization line above. Everything else in this packet stands.

### Pre-authorization checks (owner's five items)

**1. paryur and savvol against the unified controller.** Both are Setka Cup
markets with no fair value. Stage 1 build `31ddf1b06` has no
`MARGINAL_EV_NO_FAIR` rule, so every RN1_QUOTE logged `marginal_ev: null`.
Opens and extensions in no-fair markets were never judged on EV.

| Market | Fills (all NO, pregame, maker) | Reward projection at each open/extend (uncalibrated) | Result |
|---|---|---|---|
| paryur | open 2@0.47 + 1.72@0.45 (00:46); extend 1.73@0.47, 1.75@0.45 (00:48); extend 1@0.47, 0.75@0.45 (02:31); 8.95 total, $4.12 | $0.0034 at the open; $0.86–1.96 at 00:48; $0.045–0.057 at 02:30 | lost −4.1221 |
| savvol | open 2@0.49, 2@0.47, 1.19@0.45 (00:56); extend 0.5@0.40 (03:55); extend 1@0.39 (05:42); 6.69 total, $3.05 | $0.57 at the open; $0.026–0.87 at the extends | 1 pair; residual lost −2.5903 |

Under `1c1d546b6` with `MARGINAL_EV_NO_FAIR=1`, a no-fair bid needs a
qualifying reward larger than its fill cost. Qualification needs the
calibrated 3-hour projection to reach $1 (`rn1_incentive` multiplies by
`rn1_reward_cal`, table `*:0.4`).
- The highest uncalibrated projection logged at any of these decisions was
  $1.964 (paryur, 00:48). Calibrated, that is $0.79, which is below $1.
- With no qualifying reward, `marginal_ev_no_fair` refuses every bid.
- The standing-start path is calibrated too. At 0.4 it needs ≥ $2.50
  uncalibrated, which was never reached.
- **Every one of the 12 paryur and savvol opening/extension fills would have
  been refused.** The −$6.71 residual loss would not have been taken, and
  the $0.035 savvol pair edge would not have been earned.

Inference, not measured per bid: the other Stage 1 Setka pregame markets
(kobvit, kolval, kuzdmy, sydvik; +$0.63 pair edge) logged the same
below-minimum projections and would most likely also have been refused.

**Expectation:** in no-fair markets the release trades only where a
calibrated reward qualifies. Setka pregame mostly does not, because its
programme pays "Live", from event start. The window may therefore see few
fills or none. That is the intended behaviour, not a failure.

**2. The $8.14 lifetime allowance.** Account risk at 12:12:39: lifetime
−66.8569 against the $75 ceiling, `loss_room_usd` 8.1431.
- `l5_account_risk.evaluate` refuses openings once realized lifetime reaches
  −$75. It is a realized-loss halt and does not reserve room for open
  exposure.
- The pre-trade worst-case bound is the campaign stop with resting counted
  (`campaign_loss_stop_with_resting`, the final gate before submission). It
  is currently $10, which is above $8.14.
- **To make $8.14 binding before any order, this revision sets
  `PMUS_RN1_PILOT_LOSS_USD=8`.** That tightens the stop and weakens nothing.
- The campaign marker resets to the new window start (`adopt_campaign_marker`),
  so Stage 1's −$5.86 is not counted against the new stop.
- Account drawdown room is 13.59 of 20. The bot daily stop is $10, mark to
  market.

**3. Reward pool scope.** Unchanged: per market, medium-high confidence
(SPORTS_RELEASE_NEXT_20261127.md §10.3, from 18 Nov Setka PAID rows at an
implied ≥ $42–51/h). The packet keeps `PMUS_RN1_REWARD_POOL_SCOPE=market`.
Only PAID rewards count as income.

**4. Ledger fallback.** For savvol at 07:13:19 the ledger logged
`VENUE_NET_NE_FEED_NET`, because the venue had already netted the expired
position. It then:
- booked the conservative $1 bound (−5.69, loss overstated by $3.13);
- published account risk unreadable, so sports refused openings
  (`ACCOUNT_RISK_SAYS_UNREADABLE` 07:13:26) while completions continued;
- at 07:32:35 `L5_VENUE_COST_ATTESTATION` restated it to −2.5555
  (delta +3.1345, venue attested, same UTC day);
- at 07:33 account risk was readable again.

Ledger day −5.8631 equals the cash change exactly; unattributed $0. The
fallback only ever overstated the loss and blocked openings. It never
understated risk. Cost: about 20 minutes of opening pause per market that
carries a residual at settlement.

**5. Fresh state.**
- Sports reconcile 12:16:20: ok, 0 positions, 0 mismatches.
- Watchdog 12:21:26: 0 open orders, cancel enabled, all three registries OK.
- Lock 12:12:36: `locked`; new exposure and completions both locked.
- Ledger 12:21:28: unresolved 0, unattributed 0, day −5.8631, lifetime
  −66.8569, drawdown 6.4098 of 20, sibling feed CONFIRMED 3 / CONTRADICTED 0.
- Cash 306.0004.

### Timing (replaces the section above)

Verification finished at 12:21 UTC. A deploy and post-boot verification
must come first, so the earliest realistic quarter hour is 12:45.

- **Window:** `2026-11-27T12:45:00Z` → `2026-11-27T15:45:00Z`.
- **Completions-only grace:** to `2026-11-27T17:45:00Z` (7,200 s).
- **Lock value:** `window:2026-11-27T12:45:00Z/2026-11-27T15:45:00Z`.
- If post-deploy verification finishes after 12:40, start at the next
  quarter hour after it, for three hours, with the same two-hour grace.

### Limits changed by this revision

| Limit | Was | Now |
|---|---|---|
| Campaign loss stop, resting counted (`PMUS_RN1_PILOT_LOSS_USD`) | $10 | **$8** (tightened, below the $8.14 lifetime room) |
| Ledger cumulative-ceiling room | 14.02 | 8.14 (state, not a setting) |
| Account drawdown room | 11.29 | 13.59 (state, not a setting) |

All other limits, flags and protections are unchanged.

### Authorization line (replaces the one above)

> Authorized: deploy sports release 1c1d546b6d80ccb3eb5c98bf295b7e84e8195e7a to pmus-spring-l5-probe with the lock held at 1, the activation variables in SPORTS_1C1D546B6_LIVE_AUTHORIZATION_PACKET_20261127.md and PMUS_RN1_PILOT_LOSS_USD=8; after boot, reconcile, account-risk and watchdog verification run one three-hour campaign 2026-11-27T12:45:00Z–15:45:00Z (or the next quarter hour after verification if later, for three hours) with completions-only grace to two hours after the window end; PMUS_TWO_SIDED_MAX_MARKETS=2, PMUS_RN1_PIN empty; all other capital limits, loss stops and protections unchanged; maker-only; esports off; no cricket; crypto unchanged and disarmed; no automatic repeat.
