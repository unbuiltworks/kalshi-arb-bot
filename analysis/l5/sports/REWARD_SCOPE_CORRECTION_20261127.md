# Reward qualification: scope correction, pool keying and calculator audit

27 Nov 2026. Branch `dev/pmus-hybrid-integrated`. **Disarmed: every scope
other than the deployed `market` is opt-in. Not deployed.**

## 1. What the sources verify

| Source | What it says |
|---|---|
| Venue docs, saved copy (`data/pmus/sports_incentives_2026-09-10/doc_incentives_liquidity.md`) | Three **Time Periods**, each "with its own reward pool": early pre-game (listing to 6 h before), day-of pre-game (6 h to start), live (start to settlement). "Rewards are calculated within 5 business days of each time period ending." "**Rewards under $1.00 are not paid out.**" The scope of the minimum is **not stated**. |
| CFTC filings (rules11032642173 / 11032642168, Polymarket US Amended Market Liquidity Incentive Program) | Search snippet: each eligible market has a Liquidity Incentive Schedule of one or more Time Periods defined relative to the event lifecycle. **The full text could not be read**: cftc.gov is refused by this environment's network proxy. The minimum's scope there is unverified. |
| Our reward rows (64 distinct rows recovered from REWARD_CHECK logs, 15–25 Nov; `reward_rows_recovered_20261127.json`) | Rows carry `marketSlug`, `date`, `programType`, `status`; **no programme id or period**. Sub-$1 sport/day groups were **paid**: ATP 15 Nov ($0.11, 5 rows), ATP 18 Nov ($0.04), ITF women 18 Nov ($0.02). Others were **skipped**: Dota 2 17 Nov ($0.40), CS2 17 Nov ($0.07). Mixed outcomes on the same day rule out a per-account-day minimum. Paid sub-$1 groups contradict a strict per-programme-period minimum, **unless** our 133 unseen rows (of about 197) complete those pools. |

**Conclusion.**
- The period/pool structure is verified; the scope of the $1 minimum is not.
- `account_day` has no supporting source, and our records argue against
  it. **It is not to be activated.**
- Per programme and period (Grok) matches the documented pool structure. It
  is the most defensible test, and it is stricter than `account_day`.

## 2. The correction

`PMUS_RN1_REWARD_QUAL_SCOPE=program_period`: one qualification for
selection, RN1_TOUCH_EV, the marginal test and the standing start.

- **Pool key** `programId|period` (`rn1_pool_key`). The programme id
  carries its date, so each pool instance is distinct.
- **Accrual keyed by pool:** `mk["reward_theo_pool"][pool]`, alongside the
  existing day accrual used for calibration.
- **Projection** = this bot's calibrated accrual **in this pool** + the
  calibrated rate of active markets **paying from this same pool** + the
  candidate, over min(3 h, window left, **period left**). Period left is
  the event start for day-of, and event start − 6 h for early; live has no
  known end.
- **What can no longer subsidise a bid:**
  - an expired period's accrual (a different key);
  - another programme or another period of the same market;
  - a market with no paying pool.
  A market with no pool, or a programme without a period, qualifies
  nothing.
- **Unchanged:** the $1 minimum, the per-bid incremental reward, and the
  rule that a reward never rescues a negative trading edge.
- **Feasible ladder** (ChatGPT `3981feabf`) and its regular-path completion
  apply under both unified scopes.

## 3. Reward calculator audit (`src/pmus_sports_rewards.py`, reused, not rebuilt)

| Rule (venue docs) | Implementation | Verdict |
|---|---|---|
| Walk from best price outward, accumulating ALL participants' size until Target Size; orders in range score, beyond do not | `ladder_side_score`: levels best-first, includes ours, stops after cumulative ≥ target | matches |
| Score = Discount Factor^ticks from best × size | `df ** abs(k - best)`, with best over the whole book including our proposed bids | matches |
| Each side normalised to 1.0 per snapshot if Target Size is met | per-side share = ours / total, 0 if not qualified; pool split 0.5 per side | matches (a side that misses target scores 0: conservative) |
| Max spread | applied only when the programme publishes `maxSpread` (both sides within it of mid) | not in the saved US docs; only ever removes reward |
| Period length | day-of 6 h, early from start to event − 6 h, **live assumed 3 h** | live underestimates the rate for short matches (conservative) |
| Legacy `walk_side` / `estimate` | counts our size at best with no discount | used **only** by the `reward_program` log line, never by a decision |

**Maker rebates:** assumed zero (`rebate=D(0)` everywhere, maker fee 0). No
rebate has been credited.

## 4. Volatility adaptation (built, default off)

| Option | Backtest, paired vs production pauses (THEORETICAL) | Verdict |
|---|---|---|
| Pause depth (below the move) | −$0.29 ± 0.13 touch / +$0.19 ± 0.33 through | off |
| Pause depth + minimum size | −$0.26 ± 0.12 / −$0.04 ± 0.35 | off |
| Paused-market rotation (`PMUS_RN1_PAUSE_ROTATE_S`) | frees a slot only when nothing is held | optional |

Production pauses are kept. They halve the backtest loss compared with no
pauses.

## 5. Validation

- **Full regression, flags off:** 6,066 passed. Failure names are identical
  to `a51cfdbd2` (26 pre-existing).
- **Reward, enabled-mode, pause, feasible-ladder and aggregate-limit
  suites:** pass in all three scopes (`market` / `account_day` /
  `program_period`), 185 / 185 each.
- **Test-data fixes:** two stub programmes were missing `period`, which
  every real paying period has; `period` was added.
- **Docker:** clean build. 62/62 modules import with
  `program_period` and every flag on. The engine boots with the lock held
  and ran 75 s with no errors.
