# Trading-first v2: evaluation, disarmed candidate, draft validation packet — 28 Nov 2026

## Decision record

| | |
|---|---|
| **(a) Running state** | `pmus-spring-l5-probe` on `8f7cb2c36` (deployment `26b47654`, 11:59:58Z). `SUBMISSION_LOCK_STATE why: locked` (new exposure and completions). 0 open orders, $0 unpaired. `PMUS_SHARP_COVERAGE_LOG=1`. Trading-first flags unset (off). Crypto unchanged and disarmed. |
| **(b) New evidence** | Production fair-coverage read (`FAIR_COVERAGE_20261128.md`). Reward programme census by symbol. NHL name-matching defect found. Tape grid of 57 configurations and hedge repricing. |
| **(c) P&L status** | 6e6cc2d5d campaign: **settled −$1.844**, cash −$1.521, rewards paid $0 (85c). Tape figures below are **historical replay (theoretical)**: nothing submitted, filled or realized. |
| **(d) One bottleneck** | **Independent fair coverage where we can trade.** The markets we traded have none. The markets with fair (NHL, NBA, CFB) are outside the permitted sport list, and their sportsbook fair is stale or unmatched in production. |
| **(e) Next action** | Record real two-venue tapes tonight (running) and replay v2 on them out of sample. Then the owner decides on the scope and quota items in §4. |
| **(f) Cost / deployments** | One authorized disarmed deploy (`8f7cb2c36`). No new services. The recorder runs in the research sandbox with public keyless GETs. |
| **(g) Owner authorization** | Required for anything in §4. Nothing is requested to run now. |

## 1. Disarmed candidate v2

**Identity**
- RC `7133117e19e495cce2272153aa5773d4ddda0086`.
- Branch `release/pmus-sports-trading-first-v2-7133117e1` (code `f33b3da15`, dev `dev/pmus-trading-first`).
- Contains `8f7cb2c36` and `ac1c1bbaf`.

**Regression:** 6,158 passed, 26 failed. The failure set is identical to `ac1c1bbaf`. 33 trading-first and sharp tests added since `ac1c1bbaf`.

**All flags default off.** With them off, the behaviour is that of `8f7cb2c36`.

| Flag | Effect |
|---|---|
| `PMUS_RN1_TRADING_FIRST` | Openings, additions and non-completion ladder levels need an independent fair and an edge before rewards. Rewards are not an input. |
| `PMUS_RN1_TF_EDGE_USD` (0.02) | Minimum bid distance below the conservative fair |
| `PMUS_RN1_TF_DOG_EXTRA_USD` | Extra edge for the side the fair makes < 0.5 (favourite bias without a ban) |
| `PMUS_RN1_TF_CONC_USD` | Required edge rises per contract left unpaired on that side (replenishment stops as marginal EV falls) |
| `PMUS_RN1_TF_WEAKEN_USD` / `_S` | No opening or addition on a side whose independent fair fell by ≥ X over the window (weakening favourite) |
| `PMUS_RN1_TF_HEDGE_GIVE_USD` | Completion repriced down to the side's fair + give. It is never raised, and the pair cap still applies. With no fair, it is unchanged. |
| `PMUS_RN1_TF_SELECT=first\|only` | Markets with an independent fair source take execution slots first, or exclusively |
| `PMUS_SHARP_NAME_MATCH=words` | Fixes NHL nickname matching, with one-to-one pairing and a start-time check |
| `PMUS_SHARP_REFRESH=on_demand` + `PMUS_SHARP_DAILY_CALLS` | Refetches only when odds are older than 900 s, under a daily cap |
| `PMUS_SHARP_COVERAGE_LOG` | Adds no-fair diagnosis (alias, start time, Pinnacle age, live) and per-quote fair coverage |

These are unchanged: maker-only, 2-contract clip, 0.97 pair cap, per-market, campaign and account caps, loss stops, cancellation priority, watchdog, submission lock, and no automatic repeat.

**Not yet checked:** the clean Docker image build for v2. It was done for `ac1c1bbaf`. The file list is unchanged (no new modules).

## 2. Tape evaluation (THEORETICAL, before incentives)

The tape is the 21 Nov two-venue recording: 65 CFB, NBA and NHL markets, with the international fair. Fills are modelled two ways: "touch" (optimistic) and "through" (strict). Latency is 3 s and the maker fee is 0. **Rewards are not modelled; they would be reported separately.**

| Policy | Touch | Strict | Fills (touch) | Paired vs baseline (touch / strict) |
|---|---|---|---|---|
| Baseline (6e6cc2d5d core) | −5.87 | −7.74 | 138 | — |
| Edge ≥ 2¢, hedge within pair cap | +0.97 | +0.82 | 19 | +0.36±0.20 / +0.61±0.33 |
| Edge ≥ 2¢, hedge repriced to fair + 0 | +1.90 | −0.27 | 31 | +0.41±0.20 / +0.53±0.33 |
| Edge ≥ 2¢, hedge repriced to fair + 1¢ | +1.61 | +1.06 | 23 | +0.39±0.20 / +0.63±0.33 |
| Edge ≥ 2.5¢, hedge repriced to fair + 0 | +1.46 | +1.62 | 17 | +0.39±0.20 / +0.67±0.33 |
| Edge ≥ 1.5¢, hedge repriced to fair + 1¢ | +0.90 | −4.09 | 44 | +0.36±0.22 / +0.26±0.41 |
| Edge ≥ 1¢, any variant | −4.5 to −9.7 | −3.5 to −7.5 | 23–75 | ≤ +0.30 |

Full grid: `TRADING_FIRST_GRID_20261128.txt`. Other output: `_BEST_`, `_REPRICE_`, `_HYBRID_`. All files are under `analysis/l5/sports/directional/` on the dev branch.

**Favourite vs underdog** (edge ≥ 2¢, hedge within cap, touch):
- Openings and additions were **11 favourite vs 2 underdog**. Requiring positive EV produces the favourite bias by itself; no favourite-only rule is needed.
- Favourite entries: +$3.05 settle − cost. Underdog entries: −$1.32.
- Markouts were mixed on small samples: favourites −3.0 / +4.3 / −4.1¢ at 30 / 120 / 300 s.
- In the baseline, openings and additions were 41 favourite vs 49 underdog.

**Hedge economics:**
- Hedging only at or below fair, and holding the favourable residual otherwise (the asymmetric inventory), raised settled P&L in most variants.
- That gain depends on settlement: recording half 1 was −$1.50 at touch for `give 0` with refusal.
- The engine rule reprices rather than refuses, which keeps more completions. It raised matched P&L from +$0.71 to +$1.64 at touch.
- Tighter pair caps (0.95) cut volume to 3–8 fills, with no clear gain.

**Threshold:**
- 1–1.5¢ loses. 2¢ and 2.5¢ are indistinguishable on this sample. **2¢ is not shown to be optimal.**
- The live-calibrated fill loss (4.2¢ behind the touch) admits nothing.

**Limits:**
- One evening and 3–5 traded markets. Paired differences are 1.8–2.2σ.
- Most of the improvement comes from not buying the baseline's losing inventory.
- **This is not evidence of profitable live trading.** An out-of-sample tape is required.

## 3. Coverage: where trading-first can trade (production, 28 Nov)

Full report: `FAIR_COVERAGE_20261128.md`.

- **The currently permitted sports have no independent fair:** Setka, UTR, ATP, ATP doubles, WTA, WTT. They have no provider key and no international listing.
- **Sports with fair and reward programmes** are NHL, NBA and CFB in-play. They are not in `allow_sports`, and their large target sizes (8,000–86,000) make rewards a small supplement.
- **Sportsbook fair is usable about 8% of the time**: a 3 h poll against a 15 min age limit, with 262 of 500 calls remaining.
- **NHL fails to match on every game** (nicknames). Fixed on v2 behind a flag.
- **International book:** free and in-play. The guard's WebSocket was not connected in production, and it matched 0 markets in the last campaign. It was never offered NHL, NBA or CFB markets because of `allow_sports`.
- **ETH, SOL, XRP and BTC 5m/15m up/down:** 0 of 68 windows have a reward programme. No crypto opportunity on rewards today. #250 is informed.

## 4. Draft bounded live-validation packet — NOT READY (BLOCKED)

The packet is drafted so that the owner can see exactly what would be asked. **It is not a request now.** The evidence criteria below are not yet met.

**Entry criteria**, all required before this packet is presented for approval:
1. **Out-of-sample tape replay.** v2 on two-venue tapes recorded after 28 Nov (recording in progress) must show positive settled trading P&L before incentives, at both fill models, with a paired gain vs baseline > 2σ. If it does not, BLOCKED stands.
2. **Production fair read.** With v2 deployed disarmed and `PMUS_RN1_TF_SELECT=first`, `PMUS_SHARP_NAME_MATCH=words` and the coverage log on, `fair_coverage.any` must be true on ≥ 80% of candidate quotes in the proposed sports.
3. **International guard connected** and matching the proposed markets (`intl_guard.matched` > 0, `ws.connected` true).

**What the packet would ask** (unchanged limits marked "same"):

| Item | Proposed |
|---|---|
| Release | v2 `7133117e1…` |
| Sports scope | **NHL, NBA, CFB moneyline only.** This is a scope change: it adds them to `allow_sports` (`PMUS_TWO_SIDED_SELECT_ALLOW_SPORTS` / open-gate list). Esports, cricket and crypto stay off. |
| Flags | `TRADING_FIRST=1`, `TF_EDGE_USD=0.02`, `TF_HEDGE_GIVE_USD=0.01`, `TF_SELECT=only`, `TF_WEAKEN_USD=0.02`; `SHARP_NAME_MATCH=words` |
| Fair source | International book (settled band). Sportsbook only if a quota decision is made. |
| Clip, markets, pair cap | same: 2 contracts, 2 markets, 0.97 |
| Campaign loss stop | same: $8 |
| Duration | one 3 h window plus 2 h completions-only grace, no automatic repeat |
| Reporting | settled trading P&L before incentives. Rewards reported separately as paid / pending / skipped. Favourite and underdog entries, markouts, hedge economics, and fair coverage per fill. |

**Owner decisions this would need:** the sports scope change, and optionally a larger odds-provider plan. A paid plan changes the cost of the deployment and needs a quota/cost decision; the plan price could not be verified from the sandbox.
