# L5 vs RN1 / Bonereaper: the execution and economic gap, and what the controller restricts

28 Nov 2026. Sources:
- **RN1:** public fills, 30 days (`sports_wallets/rn1_activity_20261022_20261121.jsonl.gz`,
  274,695 fills), its 30-day reconciliation (`RN1_30D_FINDINGS.md`) and
  markouts (`markout/rn1/`).
- **Bonereaper:** platform P&L series (`bonereaper_recent/FINDINGS.md`).
- **L5:** venue ACCOUNT_AUDIT (lifetime since 14 Nov), REWARD_CHECK,
  ORDER/FILL/MARKOUT logs (22–27 Nov) and tonight's campaign logs.
- New measurements are in `reward_economics_20261127/rn1_30d_behaviour_metrics.txt`.

Other traders' figures are HISTORICAL, on another venue. L5 figures are
venue-confirmed.

## 1. Side by side

| Measure | RN1 (30 d) | L5 |
|---|---|---|
| Concurrent markets worked | median **21**, p10 4, p90 89 | **2** (`MAX_MARKETS`); mean active 1.3 tonight |
| Fills per market | median 3, mean 7.5, p90 16 | about 1.5 (tonight: 3 fills in 1 of 2 markets) |
| Time between fills in a market | median 33 s | minutes to never |
| Working span per market | median 49 min | one game, mostly paused |
| Fill roles (opening / heavier-side addition / completion-like) | 13% / **52%** / 35% | lifetime markouts 41% / 16% / 43%; **tonight 1 / 0 / 2** |
| Same-side buys before the second side | median 1 | 0 in both 27 Nov campaigns |
| Fill size | median $30, p90 $595 | about $0.60–1.20 (clip 1–2) |
| Maker share | 88% of $ | 100% (maker-only by rule) |
| Maker trading result | **+0.80% of $** (markout +0.3c at 2 min) | live opening markouts −2.5c (ATP −5.5c), pregame Setka −1.35c |
| Matched pairs | +$869k at pair cost 0.9766 (+2.4%) | gross pair margin −$0.70 on 984 pairs (lifetime) |
| **Unpaired (residual) legs** | −$388k on $18.6M (**−2.1%**); the residual side won 49.6% | **−$54.59 on $57.76 of unpaired cost (−94.5%)** |
| Rebates and rewards | rewards $50.5k (0.08% of $), maker rebate $148k, taker $71k | rewards $24.09 PAID; rebates $0 (none on PMUS) |
| Reward per $ bought | about 0.08% | about 2.3% (we capture more reward per dollar than RN1) |
| Reward-adjusted total | +$702k (+1.1%) | settled −$46.56 + $24.09 = **−$22.47** |
| Bonereaper | taker; market P&L after fees −$150k lifetime; profit is rebates (+$661k) | n/a: the same fills at PMUS fees with no rebate are about −0.9% |

## 2. The economic gap is the residual, and our completion rule creates it

- **Rewards are not RN1's engine.** Its rewards are 0.08% of dollars;
  ours, per dollar, are already about 30× that.
- **RN1 makes money on maker fills** (+0.80%) and does not lose on
  residuals (−2.1%).
- **We lose almost the whole cost of every residual leg.** Analysis of
  22 Nov (issue #251) found the 47 first legs that never paired lost
  −43.5¢ each, and all 47 lost. The mechanism:
  - the opposite side is bid only where the pair cost is ≤ 0.97;
  - that price is reached only when the market has moved in our favour;
  - so winners are paired early, for a few cents, and losers stay unpaired
    to settlement.
  - This is adverse selection built into the completion rule (balancing
    audit S1).
- **RN1 instead** buys each side on its own value (a fair value from a
  sharper source; it trades through moves and is right afterwards in every
  price band). Pairs are a by-product.

## 3. Restrictions: protection or unnecessary?

| # | Restriction | Evidence | Classification | Action |
|---|---|---|---|---|
| R1 | One slot cap, `MAX_MARKETS` = 2, for live and pregame alike | RN1 median 21 concurrent. Our positive economics are pregame; live markouts are negative. Capital use is 1.9% of $60; the request budget used is about 0.2 of 1 rps at 2 markets | **Execution restriction** (concurrency); the dollar caps are the protection | **Implemented:** `PMUS_RN1_MAX_LIVE_MARKETS`, a separate live cap, so pregame coverage can widen under an authorized `MAX_MARKETS` without adding live exposure |
| R2 | Jump / volatility pauses pull every bid | 72% of tonight's quote decisions; they block 41 of 46 positive extension evaluations and 31 of 42 heavy-side decisions. Backtest: production pauses −$5.87 vs no pauses −$12.67. Live markouts negative | **Genuine protection** without a fair value | Kept. Pregame markets are rarely paused, so concurrency goes there. Pause depth stays off (backtest worse) |
| R3 | Lighter side only as a completion, at pair cost ≤ 0.97 (S1) | Residual −94.5% of cost vs RN1 −2.1%; winners paired, losers kept | **Pair-cost protection by owner rule.** The evidence says it causes most of the trading loss | **Owner decision.** Not changed. Proposal in §5 |
| R4 | No fair value in most allowlisted markets | `fair: null` on all 296 quotes tonight. The international guard name-matches ATP/WTA singles only (`no_intl` 1,361, matched 0). RN1's edge is fair value | Missing capability, not a restriction | Next engineering step (§5): international matching for doubles and UTR where listed |
| R5 | Clip 2 ($12 per market, $60 total) | RN1's profit is in fills over $500. Our capacity analysis shows reward saturating at clip 2 in pregame pools | **Capital limit** (protection) | Kept. Owner decision after confirmed results |
| R6 | Same-side addition "don't chase" (+2¢) and the concentration charge | Additions were refused mainly by pauses (R2) and live fill-cost pricing. In pregame under 6e6cc2d5d they price positive | Strategy pricing | Kept. 6e6cc2d5d's pregame fill rates unlock pregame additions |
| R7 | Maker-only | RN1 taker fills are flat after fees; Bonereaper's taker edge is rebates PMUS does not pay | Correct for PMUS | Kept |

## 4. Implemented (`dev/pmus-rn1-coverage`, on 6e6cc2d5d; default off)

`PMUS_RN1_MAX_LIVE_MARKETS=k`:
- **Selection:** skips a live candidate while k live markets claim slots.
- **New exposure:** a live market may add none while k other live markets
  hold slots.
- **Completions:** never blocked.
- **Total:** `MAX_MARKETS` still bounds the total.
- **Unknown phase:** fails closed.

Tests are in `tests/test_pmus_max_live_markets.py`: off is unchanged; live is
capped while pregame passes; selection takes pregame; the total cap binds;
unknown phase fails closed.

## 5. Bounded validation plan, and the decisions it needs

1. **Campaign A (packet ready, no new scope):** 6e6cc2d5d with the packet
   settings, 2 markets. It establishes pregame quoting, pregame fill rate and
   markout by sport, and same-side additions. This is the baseline for
   concurrency.
2. **Campaign B (owner decision: concurrency):** this branch with
   `PMUS_RN1_MAX_LIVE_MARKETS=2` and `MAX_MARKETS=6`. Every dollar cap is
   unchanged ($60 total, $10 unpaired, $8 stop), so the extra four slots can
   only be pregame.
   - **Modelled:** the top 6 in-band pregame markets at about +$0.4/h
     (THEORETICAL).
   - **Request budget:** about 0.6 of 1 rps expected.
   - **Pass:** reward-qualifying time ≥ 30%, pregame markouts no worse than
     −3¢, and settled trading + PAID rewards per hour above Campaign A.
3. **Owner decision (R3), not implemented:** where a settled fair value
   exists (the international price), judge the lighter side by its own value
   instead of only as a ≤ 0.97 completion. The allocator already computes
   this (`pmus_directional_alloc`, completions currently exempt).
   - **Evidence for:** the residual −94.5% vs RN1 −2.1%.
   - **Risk:** pairs above $1, which is exactly what the pair-cost rule
     prevents.
   - **Bounds:** behind a flag, fair-valued markets only, every cap
     unchanged.
4. **Engineering next (R4):** extend international fair-value matching to
   doubles and UTR, so fair value, and with it value-based additions, covers
   more of the allowlist. Read-only first: measure coverage.

**Not claimed:** profitability. The gap to RN1 is fair-value knowledge and
size, and the residual mechanism. Concurrency and pregame make the reward
economics positive in expectation (modelled), and the next campaigns measure
whether they are positive in confirmed money.
