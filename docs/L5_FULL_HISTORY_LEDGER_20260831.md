# L5 Full-History Ledger — Accounting-Grade Study (2026-08-31)

**Scope**: complete Polymarket history of trader `l5zn1bwom8etsk`
(`0xb945...db68`), 2026-03-19 → 2026-08-31. Research only — Polymarket is a
study venue, not an execution venue. Nothing here touches the sealed Gemini
C-series or any live code.

**Reproduce**:
```
python -m src.polymarket_l5.backfill --positions   # network; or gunzip data/l5/raw/activity_gap.jsonl.gz
python -m src.polymarket_l5.ledger                 # offline
python -m src.polymarket_l5.eras                   # offline
```
Artifacts: `analysis/l5/l5_ledger.json`, `analysis/l5/l5_ledger_windows.csv.gz`,
`analysis/l5/l5_eras.json`, `analysis/l5/l5_backfill_report.json`. Raw slim
activity archive committed as `data/l5/raw/activity_gap.jsonl.gz` (40MB).

## Data recovery

The venue's `/activity` endpoint rejects offsets beyond ~5000; the original
harvest therefore held only his first ~6 weeks plus late August.
`fetch.run_fetch` now uses the timestamp-windowed DESC walk, and
`backfill.py` recovered the 129-day hole: **706,500 additional rows**
(1,413 pages), closing coverage to the full 2026-03-19 → 2026-08-31 span.
Totals: **742,964 TRADE, 15,243 REDEEM, 109 MAKER_REBATE, 72 TAKER_REBATE,
19 REWARD** rows across **16,501 up/down windows**. No SPLIT/MERGE activity
exists anywhere in his history.

## The accounting (exactly additive, cash-based)

| Component | USD |
|---|---:|
| Bought | 7,423,850 |
| Sold | **2.18** |
| Redeemed (settlements) | 7,437,535 |
| **Net trading cash** | **+13,686** |
| — of which locked matched-pair margin | +155,076 |
| — of which directional residual & timing | **−141,390** |
| Maker rebates | +33,929 |
| Rewards (liquidity program) | +26,956 |
| Taker rebates | +7,323 |
| **Incentive income total** | **+68,208** |
| **Grand total cash** | **+81,895** |

Cross-check: profile shows $70,310 all-time P/L + $3,032 open positions.
Per-market reconciliation against Polymarket's own closed-position `cashPnl`
(1,459 settled markets covered by the positions endpoint): median abs diff
**$0.49**, 74.8% of markets within $1, aggregate ours −$34,775 vs theirs
−$33,640 (≈3% drift — their avgPrice-rounded accounting vs our raw cash).
The derived numbers are corroborated, not exact to the cent; every
behavioral conclusion below is robust at this precision. The earlier
partial-data figures (+$402 early era / −$2,504 late Aug *trading-only*)
were direction-correct but incomplete: they excluded the incentive stream
and the middle months.

## What the business actually is

**Paid liquidity provision, not trading edge.**

- **83% of his profit is incentive income.** Net trading cash on $7.42M of
  turnover is +$13.7k ≈ **0.184¢ per dollar** — weekly-block bootstrap
  95% CI **[−0.08, +0.55]¢/$**, P(≤0) = 8.4%: statistically
  indistinguishable from zero. His total take (trading + incentives) is
  **1.10¢/$, CI [0.83, 1.31]** — tight, positive, and subsidy-driven.
  Pair-locked margin (+$155k) and residual bleed (−$141k) nearly cancel.
- **He never sells.** $2.18 of sell proceeds in 743k trades. No unwind
  legs, no rescues, no stop-losses. Every position is held to settlement
  and redeemed. His answer to one-sided inventory is *hold it and let
  settlement + incentives pay*, not exit engineering.
- **30–45% of his matched pairs cost MORE than $1.00** (median pair cost
  0.975, `pair_cost < $1` in only 55–73% of windows depending on gap
  bucket). Overpaying for a pair is a guaranteed trading loss — he does it
  anyway because two-sided flow earns the reward stream. He is deliberately
  *buying* incentives with controlled trading losses.

## Evolution: he never "got better" per dollar — he got bigger

Weekly total take per dollar of turnover is remarkably flat at
**~0.9–1.7¢/$ from April through August**. The $70k curve is a *turnover*
story: $4.7k/week (Mar) → ~$150k/week (May–Jun) → **$1.5M/week (Aug)**.

What actually changed at each inflection:

1. **W17 (mid-April)**: size jump ($76 → $463 median window) funded by
   capital injection (cumulative profit was ~$180 at the time — the user's
   compounding hypothesis is rejected; ramp outran profit ~100:1).
   Incentive income starts the same week.
2. **W26–W28 (late June)**: switch from 15m-only to mostly 5m windows
   (0% → ~75%), smaller per-window size, *higher* residual share
   (0.08 → 0.33–0.42). Consistent with chasing reward-program uptime
   across more windows rather than tighter pairing.
3. **W32–W35 (August)**: the incentive ramp. Income per turnover rises
   from ~0.3–0.7¢/$ to **~1.4¢/$** while trading edge stays ≈0 (last full
   week: trading **−$7,087**, income **+$20,918**). Peak weeks: ~$21k/week
   income on ~$1.5M/week turnover.

Annualizing honestly: his *current* run-rate is ~$14–23k/week ≈
$700k–$1.2M/yr **only while the venue's reward program pays ~1.4¢/$ and he
sustains $200k+/day of two-sided turnover**. Over the actual 5.5 months he
netted $82k. The whole enterprise is downstream of the incentive program's
existence and pricing.

## Temporal-pairing test: REJECTED

Does he manufacture sub-$1 pairs by legging in over time (buy one side,
wait, complete cheaper)? **No.** Median pair cost is flat across
first-leg → first-opposite-fill gap buckets (≤2s: 0.979; 2–30s: 0.977;
30–120s: 0.977; >120s: 0.975) — waiting buys nothing. And net economics
*degrade* with gap: ≤2s +$2,833; 2–30s +$1,737; 30–120s −$1,363; >120s
−$2,807 (residual bleed grows with exposure time). The pairs are not
temporal arbitrage; they are the mechanical byproduct of quoting both
sides, and faster completion is strictly better.

## Gemini-testable findings (each independently falsifiable on our tape)

1. **Hold-to-settlement is L5's exit policy — whether it BEATS rescuing is
   an open counterfactual.** (Audit correction: L5 proves holding can
   coexist with profitable overall economics when subsidies are large; it
   does not give the P&L of rescuing those same positions.) Test: compare
   C-series-style variants with rescues disabled and residual settled, on
   settlement-scored economics (fresh predeclared clock — NOT a change to
   the sealed C-series).
2. **The profit condition is incentives ≥ ~1¢/$ of turnover.** L5 is only
   viable because rewards+rebates pay ~1.4¢/$ at peak. Test on Gemini
   Sept 2: measure the live maker-rebate + fee-waiver + Liquidity Rewards
   stack per dollar of two-sided turnover; if it can't reach ~1¢/$ at our
   sizes, the subsidized-liquidity lane is not viable there.
3. **Completion speed, not patience, wins.** Pair cost is gap-invariant
   and residual time is pure cost. Favors our maker-complement-fast
   mechanics over any wait-for-better-price logic.
4. **Scale horizontally, hold per-window size modest.** His median window
   is ~$100–300 even at $1.5M/week; growth came from more windows and more
   hours, matching our q5–q10 pairing-efficiency finding.
5. **Tolerate >$1 pairs ONLY as an explicit, metered subsidy purchase.**
   On Polymarket the reward stream justified it. On Gemini our hard rule
   (YES+NO ≤ $1.00) stays until item 2 is verified live — then any
   loosening is a predeclared, audited experiment with the incentive
   income measured against the pair overpay, never assumed.

## Caveats

- Polymarket has no trading fees; Gemini's ceil-per-fill fees change pair
  economics. All transfers are behavioral, not P&L-level.
- Reward-program terms (rates, uptime rules) differ by venue; L5's 1.4¢/$
  is a Polymarket number.
- Reconciliation is ~$0.50/market median, not cent-exact; `unredeemed_qty`
  in the windows CSV includes worthless losing-side shares by construction.
- The reconstructed grand total (+$81.9k) does not exactly match the
  profile's ~$70.3k P/L + ~$3.0k open positions. Per-market reconciliation
  is strong, but the account-level profile measures evidently use
  different semantics (e.g. incentive treatment, avgPrice rounding);
  treat the profile figure as corroboration, not ground truth.

## Size-tier implication for Gemini (audit addendum, 2026-08-31)

Gemini's crypto maker rebate is 30% of the raw taker formula
(0.30 × 0.07 × C × P(1−P)), floored to the cent per fill, 20–80¢ fills
only — a theoretical ceiling of ~1.05¢ per $1 of balanced paired turnover,
i.e. exactly the neighborhood L5's economics say matters. Sept 2 also
waives qualifying maker fees through Dec 31, and Liquidity Rewards stack
independently (min size 10, two-sided tight quotes, 50% uptime).

- **q1** — execution canary only; per-fill rebates floor to $0.
- **q5** — earns some rebate but sits below the LR 10-contract minimum;
  cent-flooring still punitive.
- **q10** — first economically meaningful tier: meets the LR minimum and
  flooring loss is small. Tonight's sealed C10 read is therefore the most
  economically significant of the C-series.
- **q25+** — approaches the theoretical rebate rate and more LR share, but
  only matters once inventory absorption at that size is proven safe.

Target math: at ~1¢/$ net subsidy, $480/day needs ~$48k/day of maker
turnover (~$34k/day at L5's peak 1.4¢/$), MORE after residual losses and
any pair overpay. Turnover ≠ bankroll — capital recycles through
short-duration windows; L5 scaled by turnover and breadth, not position
size. The decisive live measurement is Sept 2: the actual credited
rebate + fee-waiver + LR stack per dollar of two-sided turnover at our
sizes. ≥~1¢/$ and controlled residual ⇒ the L5 model may transfer;
0.3–0.5¢/$ or residual-dominated ⇒ it does not support the target.
