# Three-way soccer validation — 28 Nov 2026

**Gate verdict: FAIL.** Do not set `PMUS_RN1_SOCCER=1`. Nothing here is deployed; research and DEV branch only.

Every P&L figure below is THEORETICAL and PRE-REWARD: recorded books, modelled fills, nothing submitted, filled, settled or realized.

## Verdict in brief

- The replay harness was not broken. It traded nothing at 18:20Z because the tapes were short (29 minutes per market, mostly pre-game or half-time) and because the production rules remove almost every fill a soccer maker bid can get.
- With every tape recorded up to 19:37Z (30 closed outcome markets, 10 matches, 21.6 market-hours, 17.2 in play) the baseline filled 3 times and lost on all three; trading-first (edge 2c, hedge give 1c) filled 0 times.
- That is no evidence of positive expectancy, and far too few fills to measure a negative one. The direction of what exists is adverse.
- The sportsbook pre-game fair is still unverified (the odds provider is unreachable from the sandbox).

## 1. Why the replay traded nothing

### What the tapes held at 18:20Z

One 29-minute slice (13:44–14:13Z, 3 s cadence) was the only tape for the 17 markets then closed. The recorder did not run between 14:13Z and 17:58Z.

| match | what the slice covered |
|---|---|
| bun-koe-elv, bun-scp-s04 | pre-game only (kick-off 14:30Z); US mid changed 0–1 times in 29 minutes |
| lal-bet-ray | minutes 44–73 by the clock, mostly half-time |
| sea-fro-par, swe2-nor-lbk | 16 minutes pre-game and the first 13 minutes |
| swe2-osk-esk | post-match; US book two-sided in 70 of 576 frames, spread 0.08–0.83 (correctly dropped) |

### The mechanism (not a bug)

The baseline bid rests one tick behind the US touch (`depth_ticks` 1). Major-league US books are 1c wide with 8,000–60,000 contracts at the touch, so that bid fills only when the US ask falls two ticks within about two frames. Those moves are information, and the international book or the volatility pause sees them first.

Counted over all five tapes (47 markets, baseline, touch model), "fillable" frames are frames whose ask reached the bid quoted two frames earlier:

| outcome of a fillable frame | frames |
|---|---|
| bid already withdrawn by the international guard (fair under the bid) | 96 |
| bid already withdrawn by the volatility / jump pause | 48 |
| other (no bid resting) | 4 |
| filled | 3 |
| total | 151 |

Trading-first needs `fair − px ≥ 0.02`. With a 1c spread and equal mids, `fair − (bid − 0.01) = 0.015`, so it quotes only when the international mid is at or above the US ask. Its decision counts on the closed markets were 18,339 `tf_negative_ev`, 2,165 `tf_no_fair` (three-way gate) and 1,256 orders placed; none filled.

### Tape statistics by league (all tapes, 47 markets)

Per-market rows are in `analysis/l5/sports/soccer/SOCCER_REPLAY_20261128.txt` (`tape` lines).

| league | markets | US spread median / worst p90 | intl spread median / worst p90 | venue gap worst p90 | frames passing 2c edge, YES / NO (mean share) | fillable frames | ≥2c moves: intl first / US first / same frame | US top-of-book qty (median) | US shares traded in tape |
|---|---|---|---|---|---|---|---|---|---|
| epl | 3 | 0.01 / 0.01 | 0.01 / 0.01 | 0.02 | 0.13 / 0.17 | 100 | 62 / 51 / 1 | 15,271 | 523,942 |
| sea | 9 | 0.01 / 0.02 | 0.01 / 0.01 | 0.015 | 0.04 / 0.12 | 12 | 25 / 23 / 1 | 14,692 | 322,226 |
| lal | 3 | 0.01 / 0.01 | 0.01 / 0.01 | 0.01 | 0.07 / 0.16 | 4 | 0 / 3 / 0 | 20,703 | 23,915 |
| bun | 6 | 0.01 / 0.01 | 0.01 / 0.01 | 0.01 | 0.16 / 0.00 | 0 | 0 / 0 / 0 | 12,305 | 38 |
| ere | 6 | 0.01 / 0.05 | 0.01 / 0.015 | 0.02 | 0.02 / 0.06 | 6 | 14 / 56 / 1 | 2,319 | 57,056 |
| bel1 | 9 | 0.01 / 0.05 | 0.01 / 0.03 | 0.035 | 0.11 / 0.16 | 6 | 75 / 35 / 2 | 2,386 | 14,545 |
| ecu1 | 6 | 0.06 / 0.14 | 0.03 / 0.12 | 0.055 | 0.38 / 0.53 | 20 | 149 / 79 / 3 | 1,321 | 21,805 |
| swe2 | 5 | 0.02 / 0.86 | 0.01 / 0.15 | 0.395 | 0.18 / 0.26 | 0 | 49 / 13 / 1 | 851 | 837 |

- **Price band.** In 15 of 47 markets the YES bid was below `px_min` 0.20 for the whole tape (draws and underdogs), so the YES side is never quotable there. The NO side was in band at least half the time in 42 of 47.
- **Volume.** The venue does trade (EPL: 524k shares in 83 minutes across three markets), but at the touch. A bid one tick behind does not take part.
- **Lead/lag.** The single-frame ≥2c count is crude. The decisive figure is the fillable-frame table above: 144 of 151 were pre-empted.

### Is soccer tradeable under the current rules?

Not on this evidence. The rules give about 0.14 baseline fills per market-hour, each one a fill the guard failed to pre-empt, and no trading-first fills.

An exploratory run quoting at the touch (`depth_ticks` 0, not a production setting, dummy settlement, all 45 markets) gave 5 baseline fills with a 60 s markout of −13.9c per contract against the international mid, and 1 trading-first fill at −31.5c. Quoting closer does not help; the fills are adverse.

### Harness changes

| change | why |
|---|---|
| `src/pmus_directional_backtest.py::load_cross_venue` skips an undecodable line | a tape copied while being written can end in a truncated line, which raised `JSONDecodeError` |
| `three_way_gate` in the replay script | the old script only reported the three-way sum; trading-first now runs on the fair the engine's `rn1_soccer_prob` would grant |
| `gate_audit`, `tape_stats`, `starts` | per-market diagnostics that explain a zero-trade result |
| `match_portfolio` | the three outcome markets of one match as one book |
| alignment by identity | a soccer outcome is the same instrument on both venues, so it is never flipped; a market is still dropped when the generic test finds no match |

The gate refuses a frame's fair for `siblings_missing`, `leg_missing`, `sum_inconsistent` or `unsettled` (own mid moved more than 0.01 within 10 s). The baseline is replayed without the gate because the engine applies the three-way check only to the independent fair (trading-first / directional), not to the per-market guard.

## 2. Three-way sum breaks

A break is a moment when one or more international book sides are pulled, so a mid taken from a wide book is meaningless. Two kinds were recorded.

**Goal moments (all three legs wide).** swe2-nor-lbk at 14:06:25Z:

| time (Z) | draw intl | lbk intl | nor intl | sum of mids |
|---|---|---|---|---|
| 14:06:15 | 0.26/0.27 | 0.19/0.21 | 0.53/0.54 | 1.000 |
| 14:06:18 | 0.13/0.26 | 0.06/0.20 | 0.53/0.82 | 1.000 |
| 14:06:25 | 0.10/0.26 | 0.04/0.48 | 0.58/0.83 | 1.145 |
| 14:06:31 | 0.10/0.99 | 0.05/0.48 | 0.25/0.83 | 1.350 |
| 14:06:52 | 0.03/0.98 | 0.04/0.86 | 0.06/0.96 | 1.465 |
| 14:07:42 | 0.15/0.17 | 0.07/0.10 | 0.75/0.77 | 1.005 |

The sum was outside tolerance for 19 consecutive samples (about 55 s) and peaked at |sum − 1| = 0.48. Note the 14:06:18 row: all three books were already wide and the mids still summed to 1.000. A second pull at 14:08:15Z (nor bid to 0.01 for 12 s) and a third at 14:13Z (6 samples, US book emptying) followed.

**One leg wide for a single sample.** A bid or ask on one leg is pulled for 3–6 s: ecu1-uni-ind (16 episodes of 1–2 samples in the first 22 minutes of the 17:58 slice, peak 0.27 there and 0.39 later), bel1-cer-yrm (peak 0.18), ere-psv-goa (0.06), ecu1-mac-ldu (0.135). Example: ecu1-uni-ind `uni` 0.13/0.76 against a US book of 0.63/0.77.

ecu1-uni-ind also had a true goal at about 18:16Z (draw 0.22 → 0.65). The international book moved about 30 s before the US book.

### Would the production check have refused all three legs?

Yes on the trading-first path, with one timing qualification. `rn1_soccer_prob` computes one sum for the match and returns no fair for every sibling when it is outside 0.04.

Replaying the engine rule frame by frame over all tapes: 118 sampling passes had an inconsistent sum, which is 354 leg-frames.

| what the replayed rule did with those 354 leg-frames | leg-frames |
|---|---|
| refused: `sum_inconsistent` | 278 |
| refused: `unsettled` (own mid moved more than 0.01 within 10 s) | 52 |
| refused: `leg_missing` | 2 |
| granted a fair | 22 |

The 22 are a read-order effect of at most 3 s: a leg read earlier in the same pass than its broken sibling is checked against the sibling's previous, still-good sample. The engine reads the latest sample per leg in the same way, so the refusal starts when the broken reading arrives, not before.

In the swe2-nor-lbk goal no leg had a fair from 14:06:18Z to 14:07:30Z. The first two samples (sums 1.000 and 0.995 on wide books) were refused by the settled-band rule, not by the sum check.

| match | frames | fair granted | sum_inconsistent | leg_missing | unsettled |
|---|---|---|---|---|---|
| swe2-nor-lbk | 1,726 | 81.5% | 128 | 2 | 189 |
| ecu1-uni-ind | 1,699 | 47.9% | 83 | 72 | 731 |
| bel1-cer-yrm | 3,575 | 76.0% | 133 | 262 | 464 |
| ecu1-mac-ldu | 4,149 | 82.0% | 76 | 2 | 670 |
| epl-ips-ast | 4,965 | 87.7% | 6 | 10 | 595 |

Three caveats:

- **The baseline is not covered.** The check binds only through the independent fair. With trading-first off, each leg is guarded by its own international mid and an inconsistent sum stops nothing.
- **Errors can cancel.** At 18:29:35Z all three bel1-cer-yrm international books were wide (cer 0.55/0.72, draw 0.20/0.38, yrm 0.07/0.22). The engine reads the latest sample per leg, and the mix of samples summed to 0.97, so a cer fair of 0.635 passed while the true price was about 0.725. It was one frame of 31,029 granted. The check has no per-leg spread cap.
- **Wide legs pass in thin leagues.** Among frames granted a fair, the leg's own international spread exceeded 5c in 10.8% of ecu1 frames, against 0.07% in bel1 and 0% in epl, sea, lal, bun and ere.

Of the 153 granted frames where the international mid was 5c or more from the US mid, 140 had the international price closer to the US price 45 s later. The 13 others were wide books (12 a US book 6–18c wide, 1 the bel1 frame above). All 7 with a tight US book were the international book leading a goal.

## 3. Replay results

Five tapes, 13:44–14:13Z and 17:58–19:37Z. 47 outcome markets, 16 matches; 32 closed with settlement, 30 replayed (swe2-osk-esk dropped as a post-match book; ere-exc-tel had no book in any frame). Settlement is from the gateway market record.

| run | markets | traded | fills | contracts | spend | trading P&L | matched | residual |
|---|---|---|---|---|---|---|---|---|
| touch / baseline | 30 | 2 | 3 | 6 | $2.62 | −$2.62 | $0.00 | −$2.62 |
| touch / trading-first | 30 | 0 | 0 | 0 | $0 | $0 | $0 | $0 |
| through / baseline | 30 | 1 | 1 | 2 | $1.40 | −$1.40 | $0.00 | −$1.40 |
| through / trading-first | 30 | 0 | 0 | 0 | $0 | $0 | $0 | $0 |

The three baseline fills:

| time (Z) | market | side | price | fair at fill | result |
|---|---|---|---|---|---|
| 18:32:23 | bel1-cer-yrm cer | NO | 0.26 | 0.25 | cer won; −$0.52 |
| 18:44:11 | epl-ips-ast ast | YES | 0.70 | 0.715 | 27 s later the international book was 0.38/0.39; draw; −$1.40 |
| 18:54:47 | epl-ips-ast ast | YES (extend) | 0.35 | 0.345 | draw; −$0.70 |

No pair was completed, so matched P&L is zero and every dollar spent was lost. Time-weighted resting bids were $57.78 (baseline) and $7.32 (trading-first).

### Match-level portfolio

| match (touch / baseline) | P&L by result | worst | peak loss | locked sets | settled result | P&L |
|---|---|---|---|---|---|---|
| bel1-cer-yrm | cer −0.52, draw +1.48, yrm +1.48 | −0.52 | 0.52 | 0 | cer | −$0.52 |
| epl-ips-ast | ips −2.10, draw −2.10, ast +1.90 | −2.10 | 2.10 | 0 | draw | −$2.10 |

Both stayed inside the $5 unpaired and $12 spend caps the engine applies to the match. No match held more than one outcome, so the joint arithmetic, the cross-outcome hedge and the locked-set path were never exercised on real tapes; they are covered only by unit tests.

## 4. League coverage

Source: the venue's public sports catalogue (1,449 soccer full-time-winner markets, 66 league codes) and a book snapshot at about 18:22Z, for the 238 matches starting 28–29 Nov UTC. Each match was run through the production resolver `pmus_sports_xv.resolve_three_way` with every league enabled. It agreed with a same-slug lookup on the international catalogue for every match.

Only 10 league codes resolve internationally at all:

| league | in `SOCCER_KEYS` | matches 28 / 29 Nov | all three legs resolved | intl spread / liquidity (median) | US spread median / p90 | US top qty (median) | recorded |
|---|---|---|---|---|---|---|---|
| sea | yes | 2 / 4 | 6 of 6 | 0.01 / $96k | 0.01 / 0.01 | 3,857 | 3 matches |
| epl | yes | 1 / 6 | 4 of 7 | 0.01 / $127k | 0.01 / 0.01 | 7,959 | 1 match |
| lal | yes | 2 / 4 | 3 of 6 | 0.01 / $84k | 0.01 / 0.01 | 1,503 | 1 match (half-time) |
| bun | yes | 1 / 3 | 1 of 4 | 0.01 / $76k | 0.01 / 0.01 | 4,136 | 2 matches (pre-game only) |
| ere | yes | 2 / 5 | 6 of 7 | 0.01 / $18k | 0.02 / 0.03 | 778 | 2 matches |
| bel1 | no | 3 / 4 | 7 of 7 | 0.01 / $10k | 0.01 / 0.02 | 886 | 3 matches |
| bra | no | 0 / 7 | 6 of 7 | 0.01 / $16k | 0.02 / 0.02 | 716 | none |
| ecu1 | no | 3 / 0 | 3 of 3 | 0.01 / $10k | 0.02 / 0.12 | 1,028 | 2 matches |
| par1 | no | 1 / 9 | 6 of 10 | 0.042 / $1.1k | 0.48 / 0.97 | 76 | none |
| uzb1 | no | 0 / 4 | 4 of 4 | 0.03 / $1.2k | 0.03 / 0.06 | 144 | none |

- **In `SOCCER_KEYS` but with no international listing on this slate:** lg1 (8 matches), ligpor (4), lmx (2), eflch (1). mls has no match on the slate. Their only possible fair is the unverified sportsbook one.
- **Unresolved matches inside resolved leagues** (different team codes or not listed): epl-ars-mnc, epl-bha-new, epl-ful-bor, lal-dep-fcb, lal-elc-atm, lal-rma-ala, bun-bvb-lev, bun-scf-rbl, bun-stu-fra, ere-sit-shc, bra-scp-gre.
- **The other 44 league codes** on the slate resolve nothing internationally and are not in `SOCCER_KEYS`. swe2 was not on the open slate at snapshot time.
- **Sportsbook coverage** could not be checked: provider key names, club aliases and the pre-game fair remain UNVERIFIED.

### Allowlist recommendation (conditional on a later PASS)

| group | leagues | reason |
|---|---|---|
| first campaign | `sea,epl` | deepest books on both venues, three-way sum inside 0.04 in 100% of recorded 5 s buckets, in `SOCCER_KEYS` |
| after their own in-play tapes | `lal,bun,ere` | resolve and are tight, but lal and bun have no usable in-play recording yet |
| exclude | bel1 | resolves and is tight, but has no sportsbook key and US size of 900–2,400 |
| exclude | ecu1 | US spreads 6–14c, international spreads to 12c, a fair granted in only 48–82% of frames |
| exclude | par1, uzb1, swe2 | empty or thin books |
| exclude | lg1, ligpor, lmx, eflch, mls | no international fair |
| exclude | bra | not recorded |

## 5. Gate verdict: FAIL

### What is missing

1. **Fills.** 3 baseline fills and 0 trading-first fills in 21.6 market-hours. A gate needs on the order of 100 fills per policy with settlement, on at least 20 full matches (pre-game to full time) in the candidate leagues.
2. **Any positive result.** What exists is −$2.62 on $2.62 of spend, with no completed pair.
3. **A reason to expect better.** The fill mechanics (section 1) say more tape under the same rules produces more of the same: mostly no fills, and adverse ones when the guard is late.
4. **Sportsbook soccer coverage.** Unverified.
5. **The match-level paths on real tapes.** Never exercised.
6. **A per-leg international spread cap** in the three-way check (section 2).

### When the evidence could exist

- **Tonight.** Still being recorded and closing by about 23:30Z: ecu1-mac-ldu (19:00), bel1-lom-zul, bel1-rch-bev and sea-int-gen (19:45), ere-cam-spa (20:00), ecu1-bsc-sda (21:30). The recorder stops at 23:58Z. That adds two candidate-league matches recorded in full (sea-int-gen, ere-cam-spa).
- **29 Nov.** 19 resolvable matches in sea, epl, lal, bun, ere and bel1 kick off between 11:15Z and 20:00Z and close by about 22:00Z. The running recorder's slate lists 28 Nov only, so a second recorder with a 29 Nov slate has to be started (the running ones were not touched). That would give about 140 market-hours and, at the observed rate, roughly 20 baseline fills.
- **Earliest honest re-evaluation: 29 Nov about 22:30Z.** It would still be thin for the baseline, and it decides whether trading-first ever fills. A PASS is unlikely without a rule change, which is a separate decision and not a recording problem.

### Configuration a later PASS would need

For reference only; not recommended now.

- `PMUS_RN1_SOCCER=1`
- `PMUS_RN1_SOCCER_LEAGUES=sea,epl`
- `PMUS_TWO_SIDED_INTL_THREE_WAY=sea,epl` (the guard maps only `sea` by default; without this EPL has no international fair)
- `PMUS_RN1_TRADING_FIRST=1` (otherwise the three-way refusal does not bind)
- If `PMUS_TWO_SIDED_STRICT_SPORTS=1`: add `sea,epl` to `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS`
- `PMUS_SHARP_SOCCER` stays 0 until the odds provider is verified

Risks that would remain: fills are adversely selected around goals (the international book led the US book by 6–30 s in recorded goals); a resting bid survives the 3 s cancel latency; partially resolved leagues have matches with no fair; errors across three wide legs can cancel inside the 0.04 tolerance.

## Files

- `scripts/pmus_soccer_replay.py` — replay, three-way gate, tape statistics, match portfolio
- `src/pmus_directional_backtest.py` — loader skips a truncated line
- `tests/test_l5_soccer_replay.py` — 9 tests
- `analysis/l5/sports/soccer/SOCCER_REPLAY_20261128.txt` — full replay output, including per-market tape statistics
- `analysis/l5/sports/soccer/soccer_market_meta_20261128.json` — settlements used
- `analysis/l5/sports/soccer/league_listing_20261128.json` — league table for all 58 league codes on the 28–29 Nov slate

Reproduce: copy the tapes, then `python3 scripts/pmus_soccer_replay.py <copies>/cross_venue_*.jsonl --meta <meta.json> --out <prefix>`.
