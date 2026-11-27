# Reward qualification scope: the 16:02 discrepancy

27 Nov 2026. Fix branch `dev/pmus-reward-qualification-scope`, built on the
deployed `a51cfdbd2`. **Flag `PMUS_RN1_REWARD_QUAL_SCOPE` defaults to
`market`, which leaves behaviour exactly as deployed. Not deployed.**

## What the logs show (production, a51cfdbd2)

16:02:36 UTC, `aec-setkamemd-copmih-dumvit-2026-11-27`, pregame, book
0.48/0.49, nothing resting:
- **RN1_TOUCH_EV:** `reward_mult` 0.4. At depth 1, each side has reward
  $0.146199/h against fill cost $0.063/h, so EV +$0.083199/h per side and
  **net +$0.166398/h**.
- **RN1_QUOTE (same pass):** every bid is
  `marginal_ev_not_positive`, with **`reward_usd_per_hour: 0.0`**,
  ev −$0.063/h (the fill cost alone) and `incentive` =
  `{why: below_minimum_payout, projected_usd: 0.0, min_payout_usd: 1.0}`.
- **16:03:50:** TOUCH_EV net +$0.207334/h (depth 2), and the same zero-reward
  refusal at 16:04:07.

## Why: four sites, three qualification scopes

| Site | What is tested against $1 | Scope | Horizon | ×0.4 calibration |
|---|---|---|---|---|
| Selection `pays` (`rn1_opportunity`) | (active markets' resting estimate + candidate) × 3 h | account | 3 h | **not applied** |
| RN1_TOUCH_EV (selection score and placement) | **no minimum at all** | none | none | applied |
| `rn1_incentive` (marginal test, normal) | Σ over active markets of the resting estimate × 3 h | account | 3 h | applied |
| `_rn1_boot_projection` (marginal test, standing start) | **this market's planned ladder only** × 3 h | **market** | 3 h | applied |

- At 16:02 nothing was resting, so `rn1_incentive` projected $0 and the
  standing start applied. It counted only copmih's planned bids. At the
  TOUCH_EV rates (about $0.29/h calibrated for both sides), the 3 h
  projection is about $0.88, under $1, so the reward was credited as zero.
- The other active market (mocdav-ghifio, refused in the same way at
  16:02:19) was counted in neither market's test.
- Measured at the account scope that `rn1_incentive` and the selection check
  use, the two markets together project about $1.7.
- Meanwhile TOUCH_EV, which ranks markets for selection and chooses
  placement, credited each bid with no minimum. Markets were therefore
  selected and held slots on a reward the order gate would not credit.

**Venue scope.** The minimum is not per market: individual rows of $0.01
are paid. A day's total under $1 was skipped in September. But on 17 Nov a
tennis row total of $0.40 was paid while $0.47 of esports was skipped
(`analysis/l5/rewards/REWARD_PAYOUT_THRESHOLD_20261122.md`). The exact rule
is not established. The best-supported scope is a day total across the
account (possibly per programme). None of the evidence supports a
per-market or per-order scope, or a fixed 3-hour window that ignores the
day's accrual and the time left.

## The correction (`PMUS_RN1_REWARD_QUAL_SCOPE=account_day`)

All four sites use one function, `Engine.rn1_reward_qualifies`:

```
projected = calibrated theoretical reward already accrued today (all markets, mk.reward_theo)
          + (calibrated resting rate of every active market,
             or its planned TOUCH_EV rate when nothing rests yet, fresh <= 120 s,
             + the candidate's calibrated rate)
            x min(3 h, time left in the trading window, time left in the venue reward day)
qualifies = projected >= RN1_REWARD_MIN_DAY_USD   ($1, unchanged)
```

- **Selection:** the same test, now calibrated.
- **RN1_TOUCH_EV:** a reward counts only when the account qualifies with
  this market's scored bids added. Otherwise the same scoring runs with no
  reward, so selection, placement and the gate agree.
- **Marginal test:** the normal projection and the standing start are the
  same account/day total. The standing start runs whenever the total is
  short.
- **The gate is not bypassed:**
  - each bid is still credited only its own calibrated incremental reward;
  - the reward never rescues a negative trading edge;
  - the $1 minimum is unchanged;
  - a single market whose planned ladder projects under $1, with nothing
    else active, is still refused (tested).

## Tests

- `tests/test_pmus_reward_qualification_scope.py` (11):
  - the 16:02 case (each market short alone, the account qualifies);
  - calibration;
  - stale plan or estimate counts nothing;
  - today's accrual counts;
  - the window end caps the horizon;
  - `rn1_incentive`, TOUCH_EV and the standing start in both scopes;
  - the minimum is unchanged.
- **Test infrastructure:** the enabled-mode feed also serves the day
  projection, so the "projection falls below the minimum" tests hold in
  both scopes.
- **Suite results:** the enabled-mode safety, touch-EV, no-fair marginal-EV
  and calibration suites pass in both scopes (119 / 119).

## Validation

- **Full regression, flags off:** 26 failed, 6014 passed. Failure names are identical to `a51cfdbd2` (26 pre-existing).
- **Image:** Docker Hub rate-limited (429) the base-image pull, so this
  branch's `src/pmus_sports_two_sided.py` (the only `src/` file changed)
  was layered onto the locally built `a51cfdbd2` image. Results:
  - 61/61 `src` modules import with `PMUS_RN1_REWARD_QUAL_SCOPE=account_day`
    and the activation flags;
  - the engine boots with RN1 on and the lock at `1`;
  - 75 s with no loop error, latch or market error.
