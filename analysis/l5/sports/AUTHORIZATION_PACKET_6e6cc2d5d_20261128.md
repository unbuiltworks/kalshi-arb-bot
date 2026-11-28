# Authorization packet: bounded live campaign on 6e6cc2d5d

28 Nov 2026, 00:05 UTC. **Production is locked (`6fb57e27`, `a51cfdbd2`,
`why: locked`). Nothing is deployed or armed.**

## 1. Release

| | |
|---|---|
| SHA | `6e6cc2d5d27d2e169010fc853760962707b1a2f2` |
| Branch | `release/pmus-sports-reward-economics-6e6cc2d5d` (not connected to any service) |
| Code | `e3ef7eb79`; the release commit adds only the trigger line |
| Base | `8373069fe` (contract-period reward scope, unknown terms fail closed, feasible ladder, additions-only allocator), which builds on deployed `a51cfdbd2` |

**c6fdc12b1 → 6e6cc2d5d (`src` diff, `src/pmus_sports_two_sided.py` only):**
1. `PMUS_RN1_FILL_RATE_PREGAME_MULT` becomes a per-competition table.
   Default "1": unchanged.
2. `PMUS_RN1_RANK_BY` (`per_dollar` by default, which is unchanged;
   `net_per_hour` ranks positive markets by the slot's reward-adjusted net,
   with no turnover or live-first factor).

Everything else in the diff is tests (pool identity, balanced release,
ranking, per-sport multipliers), docs and the trigger. With every flag at
its default, behaviour equals `c6fdc12b1`.

## 2. Integrity and regression

- **Full suite:** 6,100 passed, 26 failed. The 26 failure names are
  identical to `a51cfdbd2`.
- **Activation suites:** every cap, loss-stop, latch, window, grace,
  completion and fill-booking test passes under the exact activation set.
  The tests that differ assert the defaults this set replaces: the $1
  projection rule, per-dollar ranking, live-first ordering, exact reward
  multipliers.
- **Clean image built from scratch at `6e6cc2d5d` (28 Nov):**
  - 63 `src` files;
  - all identical to the release tree, except `pmus_spring_l5_session.py`,
    which the Dockerfile patches at build time and which is byte-identical
    to production `a51cfdbd2` (`62a6b9da…`);
  - identical to the image used for testing;
  - 62/62 modules import;
  - boots with the activation set and the lock at `1` (`why: locked`), with
    no loop error, latch or market error over 100 s.
- **Reward pool identity:** one contract time period is
  (slug, programId, period, start). Each accrual is summed once per scope,
  and own size is never counted as competition (tests in
  `test_pmus_reward_qualification_scope.py`). Same-day attribution across
  two campaigns on one market is flagged in the report and must be assigned
  to one campaign only.
- **Shared allocator:** byte-identical on crypto (`5b8b7e0b…`). Crypto stays
  disarmed.

## 3. Activation diff (exactly these, against production)

| Variable | Now | Campaign |
|---|---|---|
| Image | `a51cfdbd2` | `6e6cc2d5d` |
| `PMUS_RN1_REWARD_QUAL_SCOPE` | `market` | `contract_period` |
| `PMUS_RN1_REWARD_MIN_RULE` | `projection` | `paid_fraction` |
| `PMUS_RN1_FILL_RATE_PREGAME_MULT` | 1 | `0.1,setkameua:0.35,setkamemd:0.35` |
| `PMUS_RN1_RELEASE_BALANCED` | 0 | `1` |
| `PMUS_RN1_RANK_BY` | `per_dollar` | `net_per_hour` |
| `PMUS_RN1_LIVE_FIRST` | 1 | `0` |
| `PMUS_RN1_DIRECTIONAL` | 0 | `1` (additions only) |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `1` | `window:<START>/<START+3h>` |

**Unchanged, every existing limit and protection:**
- $8 campaign stop, counting resting, in-flight and uncertain orders;
- $10 lane daily stop; $20 at-risk and account drawdown;
- $12 per market, $5 unpaired per market, $60 total spend, $10 total
  unpaired;
- 2 markets, clip 2, pair cap 0.97;
- maker-only (no taker);
- jump and volatility pauses, safety latch, independent watchdog,
  completions-only grace of 7,200 s;
- allowlist atp, atpdb, setkamemd, setkameua, utr, wta, wtadb; crypto
  disarmed; esports off; no cricket; no automatic repeat.

## 4. Loss bound

- **Maximum feasible campaign loss:** $8.00.
- **Lifetime room:** $8.4306 (ceiling $75.00 − $66.5694), so it fits.
- **Account drawdown capacity:** $13.8777.
- **Preflight rule:** do not arm unless a fresh lifetime room is ≥ $8.00.

## 5. Sequence

1. Point the sports service at the release branch with the lock held at `1`,
   and set the variables above (except the window).
2. **Verify:**
   - deployed commit `6e6cc2d5d`;
   - `boot` shows RN1 on, with the new flags visible in behaviour;
   - `why: locked`;
   - at least 5 minutes with no loop error, import error or latch;
   - venue reconcile shows 0 orders and 0 positions; watchdog OK;
   - ledger lifetime room ≥ $8.00;
   - REWARD_CHECK readable, so the paid fraction is known. **If it is
     unknown, nothing reward-carried rests (fail closed).**
3. Record the timestamps, then set the window at the next quarter hour: three
   hours of trading, then two hours of completions-only grace.
4. **Scheduled:** mid-checks at +1 h and +2 h, a window report, then the
   close-out and relock to `1`.
5. **On any failure:** stay locked and roll back to `a51cfdbd2`.

## 6. Measured against tonight's baseline (a51cfdbd2, 20:47–23:30Z)

| Measure | Baseline | Target that counts as improvement |
|---|---|---|
| Capital utilization (time-weighted, share of $60) | 1.9% ($1.13 mean) | ≥ 3× (≥ $3.4 mean) |
| Reward-qualifying time (share of minutes) | 5.5% | ≥ 30% |
| Pregame resting contract-seconds | 0 | > 0, with pregame bids resting in positive-net Setka/UTR day-of markets |
| Same-side additions (fills) | 0 | Reported (additions only on positive marginal EV; not a target in itself) |
| Completed-pair profit | +$0.06 | Reported |
| **Settled trading P&L + PAID rewards per hour** | +$0.06 ÷ 2.7 h = +$0.02/h | **The decision measure**: confirmed 1–7 days later. Modelled +$0.3/h with 2 slots (THEORETICAL) |

**Failure criteria:**
- pregame fill markouts worse than −3¢ per contract at 300 s;
- pregame fill rates above the per-sport multipliers;
- or rewards returning SKIPPED.

Any of these means the multipliers are raised or the release is withdrawn.
The paid fraction shrinks automatically if skips rise.

## 7. Authorization text

"I authorize deployment of sports release
6e6cc2d5d27d2e169010fc853760962707b1a2f2 with exactly the activation
variables in AUTHORIZATION_PACKET_6e6cc2d5d_20261128 §3, lock held through
verification, then one three-hour live campaign from the next quarter hour
plus two hours of completions-only grace. All existing limits unchanged: $8
campaign loss stop, maker-only, two markets, 2-contract orders, crypto
disarmed, no automatic repeat."
