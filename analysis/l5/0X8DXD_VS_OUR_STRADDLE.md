# 0x8dxd vs our straddle executor — behavioral comparison

**Production remains disarmed. The executor was NOT modified. No
money-capable deployment, no live campaign, no capital increase.** Every
figure is historical reconstruction from public venue data, never our
realized P&L. The independent probability model stays non-voting
throughout.

---

## 0. The contradiction, resolved

| Question | Answer |
|---|---|
| Same confirmed wallet? | **Yes.** `0x63ce342161250d705dc0b16df89036c8e5f9ba9a` → `name: "0x8dxd"`, `pseudonym: "Blushing-Fine"`, re-confirmed on a fresh live query. |
| Does the capture really end 2026-08-24? | **Yes — not a pagination artifact.** A fresh *uncursored* `limit=5` query on both `/trades` and `/activity` returns 2026-08-24/25 as the newest rows. v2 pages newest-first, so the newest rows are the first ones returned. |
| **Verified latest trading date** | **last trade 2026-08-24 23:29:46Z; last account activity 2026-08-25 00:45:04Z** (a MAKER_REBATE credit). |
| Sept–Nov 2026 activity? | **None.** 23 BTC5m windows sampled across Sept/Oct/Nov (29,000+ public fills, 400–900 distinct traders per window): the wallet and both display names are **absent from every one**. The market is alive — only the trader stopped. |
| Did they shift two-sided → single-sided late favorite? | **Confirmed, and it is the exact reverse of a progression toward something better.** |

They also walked away leaving **6 redeemable winning positions worth
$2,110.80** unclaimed — consistent with abandonment, not rotation.

## 1. Monthly strategy breakdown

Activity tape, complete from 2026-03-23 (the page cap truncates *older*
data; a re-capture extending to 2026-03-07 is running and covers more of
the same March era).

| month | series | windows | fills | fills/win | vwap | sell% | 2-sided% | med t+s |
|---|---|---|---|---|---|---|---|---|
| 2026-03 | BTC5m | 422 | 87,320 | **206.9** | 0.431 | 18.7% | **99.3%** | 93 |
| 2026-03 | BTC15m | 139 | 39,779 | 286.2 | 0.424 | 14.3% | 97.8% | 383 |
| 2026-03 | SOL / XRP / ETH / BTC-other | 143 | 13,837 | ~105 | 0.34–0.44 | 0% | 80–97% | — |
| 2026-04 | other | 1 | 30 | 30.0 | 0.065 | 6.7% | 0% | — |
| 2026-05 → 07 | — | **0** | **0** | — | — | — | — | — |
| 2026-08 | BTC5m | 860 | 3,465 | **4.0** | **0.989** | **0%** | **0.3%** | **291** |

**They stopped trading entirely for May, June and July**, then returned
in August with a different, single-sided strategy.

### Where the money actually came from

| income source | amount | note |
|---|---|---|
| REFERRAL_REWARD | **$49,435.87** | 91% of all incentives. Largest month is **April ($23,873) — a month with 30 fills.** Affiliate income, not a trading strategy, and not available to us. |
| MAKER_REBATE | **$3,967.96** | $3,932.20 of it in **March** (the two-sided era); $35.75 in August. |
| REWARD | $583.13 | |
| TAKER_REBATE | $122.25 | August only — **direct evidence they took liquidity**, so "maker-only" is not a safe description of them. |
| **total incentives** | **$54,109.44** | |
| **trading P&L, verified cost basis** | **−$1,495.53** | 861 windows whose cost reconciles to redemption. **Negative.** |

**Which strategy contributed the most realized net profit in the latest
period? Neither.** August's single-sided 97–99¢ strategy produced
**−$545.86** of trading P&L across 860 windows (851 redeemed windows
accumulated +$1,534; six losing windows cost −$2,081) plus $158 of
rebates. In the one era with verified economics, incentives were the
business and trading was a cost. **0x8dxd is not a confirmed profitable
BTC5m trader**, and the March era's trading P&L is unverifiable because
411 of 421 windows have cost basis arriving outside the trade tape
(SPLIT mints both tokens for $1 and appears as no trade).

The only source-confirmed economics in the straddle era:
**$2,056,680 traded → $3,932.20 maker rebate = 19.1 bps of notional.**

## 2. What 0x8dxd does (March two-sided era) vs what our bot does

| dimension | 0x8dxd, measured | our STRADDLE, as coded |
|---|---|---|
| first entry timing | **t+5s** median (p90 t+13s) | t=0 onward, both legs rest all window |
| simultaneous UP/DOWN | **99.3% of windows** | yes — fixed limit on both sides |
| quoting span | **222s** of the 300s window | t=0 → t=270 (`CANCEL_LEAD_S=30`) |
| placement price | BUY median **0.440**, p10 0.21, **p90 0.68** | fixed price bounded **(0, 0.50) exclusive**, ladder optional |
| → reachability | **35.2% of their buy fills are at ≥0.50 — outside our band by construction** | — |
| → deep-rung overlap | only **3.7%** of their buys at ≤0.10 | a 0.05 rung captures ~nothing |
| repeated fills/window | **168 median**, p90 358, max 1,374 | a handful; `MAX_RESTING_ENTRIES` is **ACCUMULATE-only**, not wired to STRADDLE |
| selling | **18.7% of fills**, SELL median 0.370 at t+127 vs BUY 0.440 at t+85 | **never sells** |
| inventory imbalance at window end | **median 18.4%**, p90 56.5% — carries directional residual | completion targets a *matched* cheap pair |
| pair economics | pair cost ≈ **1.00**; margin is spread + rebate | pair cost ceiling **0.90** (policy target 0.96) — a locked cheap pair |
| per-window deployment | **$990 median** | $4.50 (2 × $2.25 clip) — **~220× smaller** |

## 3. The three biggest behavioral gaps

**Gap 1 — We quote where nobody trades.** Their liquidity sits at the
money (BUY median 0.440, interquartile roughly 0.21–0.68). Our straddle
rests at a fixed deep price below 0.50. Only 3.7% of their fills occurred
at ≤0.10. A deep two-sided straddle is not a slow version of their
strategy; it is a different order that does not fill. This is the
mechanical reason our straddle produces almost no fills.

**Gap 2 — One shot versus 168.** They re-quote continuously for 222s and
average 168 fills a window. Our straddle rests a fixed ladder and has no
re-quote-after-fill path (`MAX_RESTING_ENTRIES` governs ACCUMULATE only).
Frequency, not size, is the dimension we are missing — which matches the
standing `PATIENCE → FREQUENCY → SIZE → CONCURRENCY` order.

**Gap 3 — They are a market maker; we are a pair-arbitrageur.** Their
pair cost is ≈$1.00 and they end windows deliberately imbalanced (median
18.4%), earning spread and a **19.1 bps maker rebate**. Our straddle is
built to lock a pair below $0.90. These are different businesses, and the
difference is not a tuning value: near-$1 pair tolerance is **LOCKED** by
`L5_OPERATING_MODEL.md` until equivalent economics are source-confirmed
for our account.

## 4. Smallest changes that would close them — and the gate that comes first

**Nothing here is applied.** Priority order:

1. **Confirm rebate eligibility for our account, before any executor
   change.** This is the decisive question and it needs no code: at 19.1
   bps, our existing $4.50/window × 288 windows = $1,296/day of notional
   earns **$2.48/day** of rebate. If our account earns no maker rebate on
   PMUS, Gap 3 cannot be closed at any size and the March behavior should
   not be replicated at all. Matching their $480/day-scale rebate income
   would need ~$2.5M/day of notional — ~190× our limits — so this is a
   *validation* path, not a profit path.
2. **Gap 1 — config only, no code.** `L5_BTC5M_STRADDLE_PRICE` /
   `_RUNGS` already accept anything in (0, 0.50). Moving the rungs from
   deep toward the money is a variable change inside existing bounds and
   existing ceilings. It raises fill probability *and* adverse selection
   simultaneously, so it belongs in shadow first with our own fill
   accounting.
3. **Gap 2 — smallest real code change.** Extend the existing
   re-quote-after-fill path from ACCUMULATE to STRADDLE, bounded by the
   unchanged $5 per-market ceiling, which caps a window at ~2 clips
   regardless. No new strategy, no new service, no raised ceiling.
4. **Gap 3 — do not attempt yet.** Requires both a confirmed rebate and
   unlocking near-$1 pair tolerance, plus a SELL action the executor does
   not have. Blocked on step 1.

## 5. Validation: 20 reconstructed windows

`analysis/l5/reports/0x8dxd_first20_settled_windows.txt` — 20 consecutive
settled March windows, every fill, inventory after each fill, cost,
redemption, net. 15 profitable, 5 losing, range −$831.64 to +$926.72.
**19 of the 20 do not reconcile to their redemption**, so their stated
net is overstated — which is precisely why the March era's profitability
is reported as unknown rather than positive.

`analysis/l5/reports/0x8dxd_window_44at99_1787613900.txt` — the August
window containing the verified 44-contract Down @ $0.99 fill, reconciling
exactly: 9 fills t+286→306, $694.56 cost, $701.54 redemption, **+$6.98**.
Our `CANCEL_LEAD_S=30` sweeps at t=270, so our executor cannot reach any
fill in that window — and since the August strategy is net negative,
that argues for leaving the cancel lead alone.

## 6. What to replicate next

**Not the August strategy** — measurably net negative over its author's
own 860 windows, and abandoned by its author.

**Not the March strategy on faith** — its trading P&L is unverifiable,
and its one confirmed margin is a 19.1 bps maker rebate whose
availability to our account is unconfirmed.

**Replicate the structure, in shadow, and measure our own fills:**
at-the-money two-sided quoting (Gap 1, config only) with re-quote
frequency (Gap 2), under unchanged ceilings, with the watchdog,
reconciliation and settlement accounting intact and the probability model
non-voting. The decision gate is step 1: whether our account is paid to
provide liquidity. Until that is answered from source, this stays
research.
