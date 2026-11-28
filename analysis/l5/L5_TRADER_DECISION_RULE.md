# The L5 Decision Rule, Inferred — and Behavior-Clone vs Selective Filter

Date: 2026-08-31. Source of truth: `l5_decision_rule.json`
(`python -m src.polymarket_l5.decision_rule`). Sample: 240 BTC 15m windows,
42,371 BUY fills, recent era. Fills-only caveat: resting orders are invisible;
the quoting ladder is inferred from fill patterns.

## The machine he actually runs (BTC 15m)

1. **Enter immediately**: first fill median 9s after window open.
2. **Quote BOTH sides continuously and symmetrically, all window**: fills land
   ~50/50 on either side regardless of current imbalance — there is NO
   light-side steering (P(light-side fill) ≈ 0.49 at every imbalance level).
   Sizing is flat (~10–12 contracts) with a mild late ramp.
3. **Tolerate absolute residual, let the ratio decay**: |imbalance|/inventory
   falls 0.46 → 0.05 across the window mostly because inventory grows, not
   because he corrects. He accepts temporary directional inventory exactly as
   hypothesized.
4. **Average down**: after his own side's price falls, his next same-side fill
   continues 66% of the time (a resting ladder being filled on the way down);
   after a rise, 59%.
5. **Escalating defensive completion**: taker share of fills rises
   monotonically 2% (mid-window) → 8.9% (final decile), at prices up to a
   median $0.73 — he pays up late to complete pairs rather than stay naked.
6. **Stop early**: last fill median 87s before expiry (p25 47s).
7. **Pacing**: spend is nearly linear, ~10% of the window total per decile.
8. **Outcome**: 78% of windows end with matched-pair cost < $1.00; heavy side
   splits 136 Down / 104 Up (no directional identity).

This is NOT "perfect two-sided geometry per trade" — it is continuous
symmetric liquidity provision with bounded late-window aggression. On that
point the reframe is confirmed.

## But the behavior clone LOSES to the selective filter — decisively

Replayed on his own tape at 10% scale (same conventions as the clone-state
replay; taker fills pass through; rebate modeled at the flat empirical
0.72¢/$):

| Policy | turnover | trading P&L | +modeled rebate | peak exposure | $/hour |
|---|---|---|---|---|---|
| Mimic: behavior clone + risk caps (price ≤ .95, stop 60s out, spend cap) | $23,817 | −$158.87 | **+$12.61** | $247 | $0.21 |
| Accept-all (his raw behavior) | $39,576 | −$243.64 | +$41.30 | $610 | $0.69 |
| **"Perfect window" tape-contra (frozen policy D)** | $15,991 | **+$450.53** | **+$565.66** | $254 | **$9.47** |

Same tape, same exposure ceiling: the selective filter earns ~14x the behavior
clone per hour. The explanation is already established: **his trading is
breakeven-to-negative by design; his income is rebate/reward flow at ~$200k/day
volume.** Cloning his behavior at small scale clones breakeven trading plus a
small pro-rata rebate. The filter mines the only slice of his flow with
positive trading expectation.

## Synthesis — what to build

The two views compose rather than compete:

- **Operating envelope from his behavior** (confirmed valuable): enter at
  window open, quote both sides continuously, accept temporary imbalance,
  complete defensively late (bounded taker), stop ~60–90s before expiry. This
  is what a maker engine should DO all window — not wait for pristine entry
  geometry.
- **Side/price selection from the filter** (confirmed profitable): within that
  envelope, weight quoting toward the tape-contra side at ≥$0.30 (frozen
  policy D). At our scale, per-dollar quality dominates volume.
- **Execution layer unchanged**: V5.5 bounds, rescue, ledger fixes.

Caveats: identical replay limits as before (we assume 10% of observed fills at
his prices; queue position unmodeled); policy D remains exploratory until the
frozen-constants confirmation on fresh data; rebate economics are venue- and
scale-specific and modeled flat here. And the Kalshi/Gemini portability of all
of this is exactly what the running Lane-2 shadow measurement answers.
