# Trading-first evaluation and disarmed candidate — 28 Nov 2026

Owner direction (#251, 04:03Z): trading-first hybrid. Favourite-biased value accumulation, an economical underdog hedge, and rewards as a supplement only. No live authorization was granted. **Nothing here was deployed or armed.**

## Candidate (disarmed)

| | |
|---|---|
| Release candidate | `8f7cb2c368c6738dd6b72ae4994ae5fd4691bafa`, branch `release/pmus-sports-trading-first-8f7cb2c36` (code `a3e27b21d`) |
| Baseline | `ac1c1bbaf`, an ancestor, with all of its engineering improvements kept |
| Dev branch | `dev/pmus-trading-first` (from `dev/pmus-request-efficiency`) |
| Regression | 6,135 passed, 26 failed. The failure set is identical to ac1c1bbaf. 9 new tests in `tests/test_pmus_trading_first.py` |
| Flags | All default off. With the flags off, behaviour is byte-for-byte the ac1c1bbaf path |

`PMUS_RN1_TRADING_FIRST=1` applies to every exposure-adding bid: openings, same-side additions, and ladder levels that are not completions. Each such bid must pass a **pre-reward** test:

- **Independent probability required.** The probability comes from `rn1_directional_prob`: the settled international fair, or the age-limited sportsbook fair with `RN1_DIR_SHARP`. The venue's own price is never used. If no probability is available, the bid is refused with `tf_no_fair`.
- **Edge required.** The bid must be at least `PMUS_RN1_TF_EDGE_USD` (default 0.02) below the conservative fair for its side. Otherwise it is refused with `tf_negative_ev`.
- **Rewards cannot rescue a bid.** Reward credit is not an input to this test. In the fixture, a programme projecting $9/day with qualifying status still places nothing without fair (test passes).
- **Optional filters.** `PMUS_RN1_TF_FAV_ONLY` (with `PMUS_RN1_TF_FAV_MIN`, default 0.5) and the falling-side filter `PMUS_RN1_TF_FALLING_S` / `_TICKS`.
- **Completions exempt.** Pair-cost limits govern completions, unchanged. The test asserts a NO completion at or below 0.97 − avg still rests without fair.
- **Fails closed.** An error refuses the bid.
- **Logging.** `RN1_QUOTE.trading_first` logs the pre-reward edge per side, separately from the reward EV fields.

Unchanged: maker-only, caps, loss stops, cancellation priority, watchdog, authorization and submission lock.

## Tape evaluation (THEORETICAL, pre-reward)

**Method.** Recorded two-venue books from 21 Nov: 65 CFB/NBA/NHL markets, of which 19 traded at touch. The run uses the production quote, guard and ladder logic with production pauses. Fills are modelled (touch = optimistic, through = strict) with 3 s latency and a maker fee of 0. **Rewards are not modelled**, so all figures are pre-reward settled trading P&L. This is the **only** two-venue sports tape available, so it covers one evening.

### Main results

| Policy | Touch: trading (matched / residual) | Touch: fills | Through: trading | Paired vs A (touch / through) |
|---|---|---|---|---|
| A baseline (6e6cc2d5d-like core) | −5.87 (+5.31 / −11.18) | 138 | −7.74 | — |
| B falling-side filter (30 s, 2 ticks) | −5.27 | 111 | −4.55 | +0.03±0.12 / +0.23±0.29 |
| Fair + edge ≥ 0, both sides | −4.71 | 113 | −5.41 | +0.06±0.12 / +0.17±0.33 |
| Fair + edge ≥ 0, favourite-only | −9.73 | 55 | −7.23 | −0.20±0.34 / +0.04±0.38 |
| Fair + edge ≥ 0, underdog-only (control) | −1.77 | 67 | −4.24 | +0.22±0.22 / +0.25±0.40 |
| **Fair + edge ≥ 2¢, both sides** | **+0.97 (+0.71 / +0.26)** | 19 | **+0.82** | **+0.36±0.20 / +0.61±0.33** |
| Fair + edge ≥ 2¢, favourite-only | +0.83 | 15 | +0.75 | +0.35±0.20 / +0.61±0.33 |
| Fair + edge ≥ 4.2¢ (the live fill-loss calibration) | 0.00 | 0 | 0.00 | — |

Full output: `TRADING_FIRST_TAPES_20261128.txt`, `TRADING_FIRST_SWEEP_20261128.txt` and `TRADING_FIRST_ROBUST_20261128.txt`, all under `analysis/l5/sports/directional/` on the dev branch.

### Favourite vs underdog in the baseline (touch, per fill, classified by the international fair)

| Role | Side | Fills | Avg px | Settle − cost | Markout 30 s / 120 s / 300 s |
|---|---|---|---|---|---|
| Open/add | underdog | 49 | 0.323 | +15.54 | −2.8 / −2.6 / −2.6¢ |
| Open/add | favourite | 41 | 0.687 | −19.59 | −0.4 / −1.3 / −3.6¢ |
| Complete | underdog | 22 | 0.344 | +9.29 | −3.7 / −2.2 / −2.3¢ |
| Complete | favourite | 26 | 0.660 | −11.12 | −1.1 / −2.8 / −5.2¢ |

With strict "through" fills, favourite open/add markouts were −0.2 / +1.8 / −3.5¢ and underdog open/add markouts were −7.5 / −8.5 / −6.5¢.

### Findings

1. **Value matters, not the favourite label.**
   - The only configurations that were positive before rewards required a bid at least 2¢ under the independent fair. That held in both recording halves for touch and through fills, with one exception: half 1 at touch, where the baseline itself was +3.44, so the gate was worse paired.
   - Volume is tiny: 2–19 fills. The paired gain is about 1.8σ, and most of it comes from not trading losing inventory. **This is not evidence of a profitable strategy, only of a less unprofitable one.**
2. **The favourite bias is not validated.**
   - Markouts favour favourite acquisition: less adverse selection, which agrees with the live finding.
   - Settlement went the other way on this evening, because favourites lost. With 19 markets, settlement is noise (per-market SE about $0.35).
   - Favourite-only without an edge requirement was the worst variant at settlement. Per #251, BBO favourite status is only a diagnostic. FAV_ONLY therefore stays off by default and is not recommended.
3. **The live fill-loss calibration (4.2¢ behind the touch) is incompatible with maker accumulation.** No maker bid on the tape ever sat that far under the international fair. Under the live calibration, trading-first therefore means almost no directional accumulation. This is the honest result.
4. **Positive completion markouts (item 3) do not transfer.**
   - Live completions 2 or more ticks behind the touch marked +3.4¢ at 30 s and +5.4¢ at 120 s. The earlier analysis attributed this to the falling-side/momentum effect.
   - On the tape, simulated completions also mark negative.
   - Applying the same characteristic to acquisition (the falling-side filter) changed P&L by +0.03±0.12 at touch and +0.23±0.29 through. That is not significant, and the filter stays off.
   - The favourable completion markout is best explained by completions being conditional on our first leg having been run over (we buy the side that just got cheap after an informed move). That condition cannot be imposed on a fresh opening.
5. **Hedging.** In every edge-gated run, underdog exposure arose only as completion, judged by pair-cost limits (cap 0.97) and portfolio outcome. Matched P&L stayed positive in all runs, and the residual drove the losses. No fixed hedge ratio or forced pairing was added.

## External probability coverage (item 5)

- **On the tape:** the international fair was present for nearly all frames. `tf_no_fair` refused only 54–57 decisions.
- **In production:** **0 of the 24 campaign fills had a fair.** With trading-first enabled, production would currently place almost no directional openings or additions. That is the correct, fail-closed outcome until coverage exists.
- **Provider coverage is still unmeasured in production.** `OddsClient.coverage()` (read-only, `PMUS_SHARP_COVERAGE_LOG=1`) is in ac1c1bbaf and in this candidate, but reading it needs a production deploy with the key. That needs authorization.
- **Sources found so far:**
  - `SPORT_KEYS` covers nba/nhl/nfl/cbb/wcbb/cfb.
  - Tennis has no configured source.
  - The prediction-market-ev-engine repository has no licence.
  - The polymarket-tennis repository carries scores only.
- **Crypto:** unchanged and disarmed. There was no BTC5m promotion and there is no new evidence.

## Recommendation (not an authorization request)

- Do not run trading-first live yet. On current production coverage it would mostly not trade.
- The useful next step is the read-only coverage measurement (`PMUS_SHARP_COVERAGE_LOG=1`), if the owner approves that deploy. The step after that is recording more two-venue tapes across sports, so the 2¢ edge result can be tested out of sample.
- Rewards remain supplementary. Under trading-first, a reward can only add to bids that already have a pre-reward edge.
