# RN1/Bonereaper hybrid: shared inventory allocator, results

27 Nov 2026. **Disarmed release candidates; nothing deployed or armed.**

Every P&L below is THEORETICAL: recorded books, modelled fills, nothing
realized.

## What was built (reusing the existing engines)

- **Shared allocator** `src/pmus_directional_alloc.py`. Pure, with no venue
  imports. The identical file is on both branches; the crypto branch checks
  its sha256.
  - Marginal EV per contract: conservative fair − price − adverse-selection
    fill loss − fee + calibrated reward − inventory-concentration charge.
  - The favoured side has hysteresis. The lighter side is bought when edge
    plus the variance reduction pays.
  - Ladder levels are judged on the inventory ahead of them.
  - **New:** reference freshness (refuse past a maximum age; widen the band
    by drift × age).
  - **New:** `size_hint`, a favoured-side clip capped at the per-order
    maximum.
  - **New:** `ladder_hint`, extra favoured-side ladder levels.
- **Sports** (`dev/pmus-directional-allocation`), all default off:
  - probability chain: settled international fair band, then the sportsbook
    de-vigged fair value from the existing sharp shadow feed
    (`PMUS_RN1_DIR_SHARP`; max age and drift), never the traded price;
  - favoured-side extra levels (`PMUS_RN1_DIR_EXTRA_LEVELS`);
  - upsizing (`PMUS_RN1_DIR_UP_MULT`, capped at the final gate's maximum);
  - EV-priced additions in place of the "don't chase" cap
    (`PMUS_RN1_DIR_PRICE_ADDS`; fail closed on error).
- **Crypto** (`dev/l5-crypto-shared-allocator`, from the latest crypto line
  `19ca55ad2`). The controller is not modified.
  - `src/l5_crypto_alloc_adapter.py` judges the existing `decide()`'s
    exposure-adding orders.
  - Probability: the controller's own settlement-aligned digital model,
    either Φ((reference − strike)/(σ√τ)) per symbol (BTC, ETH, SOL, XRP)
    or the reference model's recorded distance and sd.
  - Band: from volatility uncertainty and reference age; a stale reference
    is refused.
- **Audit:** `BALANCING_AUDIT_20261127.md`. The main remaining limit on
  favourite pivoting is that the lighter side may only be bought as a
  completion under the pair cap. That is a pair-cost protection and was
  kept.

## Sports: 65 recorded CFB/NBA/NHL markets with the international book (21 Nov)

| THEORETICAL | Baseline (deployed rules) | Additions-only allocator (recommended setting) | + EV-priced additions |
|---|---|---|---|
| 1. Settled trading P&L after fees, strict fills | −$14.64 | **−$3.89** | −$6.83 (fill loss vs lead) |
| Same, optimistic fills | −$12.67 | −$8.60 | −$10.08 |
| 2./3. Confirmed rebates / rewards | $0 / $0 | $0 / $0 | $0 / $0 |
| 4. Total realized net | none | none | none |
| 5. Modelled incentives | not modelled (no programme recorded) | — | — |
| 6. Matched / residual, strict | +$5.52 / −$20.16 | +$5.24 / −$9.13 | +$6.06 / −$12.88 |
| 7. Spend (= peak capital; nothing settled in the window) / ROC | $82.81 / −17.7% | $60.66 / −6.4% | $76.00 / −9.0% |
| Paired per market vs baseline, strict | — | **+$0.63 ± 0.29** (t 2.2) | +$0.46 ± 0.32 |

- **Favoured-side extra levels:** no effect. The deeper levels sit 6–8 ticks
  behind and never filled on these books; the instrumented run confirms they
  were placed.
- **Upsizing:** capped at the per-order maximum (2), so it cannot change a
  size without a limit change.

## Crypto: 187 recorded BTC 5-minute windows; existing controller vs + adapter

Identical data and fill rule (`analysis/l5/btc5m/allocator_replay.py`,
`ALLOCATOR_REPLAY_RESULT.txt`, crypto branch).

| THEORETICAL | Existing controller | + allocator, fill loss 0.01 | + allocator, fill loss 0.02 | additions only |
|---|---|---|---|---|
| 1. Settled trading P&L after fees (maker fee 0) | −$4.59 | −$4.28 | −$4.18 | −$4.33 |
| 6. Matched / residual, per window | −0.84c / −1.61c | −1.13c / −1.16c | −1.18c / −1.06c | −1.27c / −1.05c |
| Fills / spend | 3,084 / $90.66 | 2,794 / $81.96 | 2,737 / $80.68 | 2,807 / $83.51 |
| Paired net per window vs existing | — | +0.17c [−0.7, +1.0] | +0.22c [−0.7, +1.1] | +0.14c [−0.7, +1.0] |
| 2./3./5. Rebates / rewards / modelled | $0 / $0 / not modelled | | | |

## Findings

1. **The allocator reduces residual losses in both lanes, and most clearly
   on sports with a leading fair value.**
2. **No configuration is profitable on trading alone in either lane.**
   Matched pairs are the only positive component on sports and are negative
   on BTC5m.
3. **Total realized net cannot be judged from these books.** Incentives are
   not recorded for these markets, and our PAID rewards to date ($24.09)
   are account-wide.
4. **The $480/day target is not supported by any result here.** The best
   sports figure, about −$0.23 per market before incentives, still loses.
5. **The recommended setting is additions-only**, the defaults:
   - `PMUS_RN1_DIRECTIONAL=1`;
   - `PMUS_RN1_DIR_JUDGE_OPENINGS=0`;
   - `PMUS_RN1_DIR_PRICE_ADDS=0`;
   - `PMUS_RN1_DIR_EXTRA_LEVELS=0`.

   EV-priced additions did worse than additions-only on the same books.
6. **Bounded live validation:** it only acts where a settled international
   (or fresh sportsbook) probability exists. A bounded window would need
   those markets (ATP/WTA name-matched, three-way soccer). Today's Setka
   markets would see no change.

## Not established

- profitability;
- live fills in fair-valued markets;
- incentive economics of these markets;
- results outside one evening (sports) and one day (BTC5m);
- ETH, SOL and XRP: the adapter supports them, but there is no recorded
  data for them here.
