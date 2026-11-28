# Reward economics bottleneck: why projected reward is $0, and the correction

27 Nov 2026, 22:15 UTC. Branch `dev/pmus-reward-economics`. **Every new
behaviour is default off. The running campaign (`9f22fd1c`, `a51cfdbd2`) is
untouched.** All figures are THEORETICAL unless marked venue-confirmed.

## 1. What the live campaign shows (20:47–21:57Z, 128 RN1_QUOTE, 92 RN1_TOUCH_EV)

- **Blocks** (128 quotes):
  - jump pause 51, volatility pause 44;
  - `net_not_positive` 25, which is the economic gate;
  - pilot meter unavailable 2, which is a startup transient.
- **Rewards credited:** `incentive.why = below_minimum_payout` on every
  quote, `projected_usd` $0 (or under $1), so `reward_usd_per_hour = 0` in
  the marginal test.
- **Touch EV**, per market and level, at 2 contracts a side:
  - modelled reward (×0.4 calibration): $0.000–0.15/h, occasionally
    $0.33/h, and up to $4.6/h on a thinning doubles book;
  - modelled fill cost: $0.36/h at the touch and $0.06–0.09/h behind it;
  - so only a few placements are ever positive.

## 2. Why the projected reward is zero

1. **The $1 rule is applied as a per-market projection.**
   - a51 (`market` scope) credits a reward only when the active markets'
     resting estimate × 3 h reaches $1.
   - With nothing resting yet, the projection is $0. With 2-contract bids
     it is $0.3–0.9 even in the best markets.
   - So the marginal test credits $0 and refuses the bid. Without a reward
     nothing rests, so the projection stays $0.
2. **Our own reward rows do not support that rule** (`reward_rows_union`,
   64 rows recovered on 27 Nov, plus the 22 and 23 Nov analysis):
   - 34 of 39 (competition, day) groups were **paid with totals under $1**;
   - rows of $0.01 are paid;
   - no group mixes paid and skipped;
   - skipped rows ($4.00 in all) are not explained by size: cs2 paid $0.12
     on one day and was skipped at $0.07 on another.
   - Of resolved value, **$24.09 was paid and $4.00 skipped: a paid
     fraction of 0.858.**
   - The venue's minimum exists, but its scope is unknown. Treating it as
     "$0 until a market projects $1" understates expected payment for every
     group our records show.
3. **Pregame fill costs use live fill rates.**
   - Lifetime heartbeat phases: **live** 85 fills over 28.2 resting
     contract-hours (3.0/h); **pregame** 26 fills over 127.7 (0.20/h),
     15× fewer.
   - The model charges every market the live rate (4.0 at the touch, 1.5
     behind), so the pregame (day-of) reward programmes, where most
     allowlisted rewards are, are always scored negative.
4. **A completed pair holds a slot.** Live at 21:32Z, a 2 YES / 2 NO
   doubles market wanted no bid for the rest of the match but kept one of
   the two quoting slots: the idle release only applied to a flat (0/0)
   market.

`campaign.mode: off` is not a fault. It reports the optional signed
runtime campaign file (`PMUS_TWO_SIDED_SUBMISSION_LOCK=runtime`), and this
campaign uses a lock window instead. The $8 stop is
`PMUS_RN1_PILOT_LOSS_USD`:
- PILOT_PNL shows `loss_stop_usd: "8"` and `new_openings_block: null`;
- `final_gate` refuses any opening or extension once realized plus book
  worst case, plus resting, reserved and uncertain orders, plus the order
  itself would reach −$8 (`campaign_loss_stop_with_resting`);
- `pilot_block()` is checked on every step.

## 3. Ranking the live slate by attainable reward-adjusted net (public data, 22:00Z)

- **Data:** 4,108 events; 455 markets within ±6 h; 245 with a programme;
  158 with a two-sided book in a paying period. Data and script are in
  `reward_economics_20261127/`.
- **Model:** the venue's own scoring (`quote_estimate`: target-size walk,
  df^ticks, per-side normalisation) at 2 contracts a side, touch vs one tick
  behind, the better placement chosen; production calibration 0.4;
  production fill rates and losses.

| | Deployed model | Corrected model (×0.858 paid fraction, pregame fill rate ×0.25) |
|---|---|---|
| All 158 markets with net > 0 | 11 | 37 |
| **Allowlist (atp, atpdb, setka md/ua, utr, wta, wtadb), 84 markets** | **3** | **29 (22 inside the 0.20–0.92 price band)** |
| Allowlist live (4) / day-of (80) positive | 0 / 3 | 0 / 29 |
| Best allowlisted, in band | UTR day-of +$0.005/h | UTR day-of **+$0.19/h**; Setka UA day-of +$0.05–0.09/h |

- **On the pregame multiplier:** 0.25 is 3.7× the measured pregame fill
  rate, so it is a conservative choice.
- **Where the positive economics are:** **pregame day-of programmes** (UTR
  pool $10 with target 250; Setka $5 with target 3,200). Live allowlisted
  markets are negative under both models.
- **Outside the allowlist:**
  - the best markets are esports (Valorant, CS2), which stay off by owner
    rule;
  - PDC darts and beach soccer, which are not authorized.
  - They are listed only as evidence.

## 4. Corrections (`dev/pmus-reward-economics`, all default off)

| Flag | Change | Fail-closed |
|---|---|---|
| `PMUS_RN1_REWARD_MIN_RULE=paid_fraction` | Each theoretical reward (after calibration) is credited × PAID/(PAID+SKIPPED) from the latest REWARD_CHECK, in place of $0 below a $1 projection. Applies at selection, touch EV, the marginal gate and the standing start. | No reward check, under $5 resolved, older than 4 h, or no programme or pool: **nothing is credited** |
| `PMUS_RN1_FILL_RATE_PREGAME_MULT=0.25` | A market the engine has seen as not live uses fill rate × 0.25; fill loss per contract is unchanged. | Unknown phase keeps the live rate |
| `PMUS_RN1_RELEASE_BALANCED=1` | A completed pair (YES = NO > 0) wanting no bid, with no open order, frees its slot after the existing idle time. | Inventory stays booked in every limit, the meter and the audit; an unpaired market is never released |

**Rotation away from unsuitable markets reuses what exists:**
- a flat market with no positive placement is released after 120 s (idle
  release);
- with `RELEASE_BALANCED`, so is a completed pair;
- `RN1_SELECT_NO_CHURN` and `LIVE_FIRST` keep the ordering.

**Unchanged:** asymmetric accumulation (EXTEND up to +2¢, the
additions-only allocator), continuous replenishment, maker-only execution,
pauses, pair cap 0.97, every cap and stop.

## 5. Validation

- **Full regression:** 6,090 passed, 26 failed; the 26 failure names are
  identical to `a51cfdbd2`.
- **New tests:** 13 for paid fraction, pregame rate and phase (including an
  enabled-mode end-to-end test: a below-$1 projection rests nothing under
  the old rule, nothing with an unknown paid fraction, and the
  reward-carried bids with a known one), and 3 for balanced release.
- **Activation flags on, default rule:** the reward, enabled-mode safety,
  touch-EV, ladder, pause, limit, no-fair, calibration, directional,
  live-first and reselect suites pass (235/235).
- **`paid_fraction` rule with a known paid fraction:** 214/224 pass. The
  10 that differ are the tests of the $1-projection rule itself (standing
  start, account and pool qualification), which this rule replaces by
  design. Every cap, loss-stop, latch, window, grace, completion and
  fill-booking test passes.
- **With an unknown paid fraction:** all reward-carried quoting stops
  (fail closed), as intended.
- **Image:** the changed `src` file was layered onto the clean `8373069fe`
  image. Every `src` file matches the release tree, except the spring
  session file the Dockerfile patches, which matches production. 62/62
  modules import with every flag on, and it boots locked with 75 s free of
  errors.

## 6. Path to venue-confirmed net profit (each step needs owner authorization)

1. **Activate on a bounded window:** this release, existing limits
   unchanged.
2. **Measure in the window:**
   - pregame qualifying contract-hours;
   - pregame fills and markout against the 0.25 assumption;
   - same-side additions and acquisition cost;
   - settled trading P&L.
   Report with `campaign_report.py`.
3. **Confirm rewards:** at T+1 to T+7 days, venue REWARD_CHECK rows for
   those markets (PAID only).
4. **Promotion rule:** settled trading P&L plus PAID rewards > 0 over
   repeated windows. If pregame markouts exceed the model, raise the
   multiplier (more conservative); if rewards come back SKIPPED, the paid
   fraction falls and credit shrinks automatically.

**Expected scale (THEORETICAL):** two allowlisted day-of markets at
+$0.05–0.19/h each is roughly $0.2–0.4/h, before variance. Positive in
expectation, but **far from $480/day**. Rewards scale with resting size,
and size (clip 2, $12 per market) is capped by the current authorization.
Any increase is a separate owner decision after confirmed results.
