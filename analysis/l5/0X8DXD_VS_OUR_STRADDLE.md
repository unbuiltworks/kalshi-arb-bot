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

**Re-captured untruncated (`PAGE_CAP=1500`, 750,500 rows, 2026-03-12 →
2026-08-25); every figure below is from that tape.** The first capture
had truncated March to a fifth of its real size, which moved several
numbers — corrections are marked ⚠. The tape still ends at the
1500-page cap, so March remains partial at its oldest edge.

| month | series | windows | fills | fills/win | vwap | sell% | 2-sided% | med t+s |
|---|---|---|---|---|---|---|---|---|
| 2026-03 | BTC5m | **2,734** ⚠ | **392,634** ⚠ | **106 median** ⚠ | 0.440 | 17.3% | **95.8%** | 93 |
| 2026-03 | BTC15m + SOL/XRP/ETH/other | — | 333,407 | — | 0.34–0.44 | — | 80–98% | — |
| 2026-04 | other | 1 | 30 | 30.0 | 0.065 | 6.7% | 0% | — |
| 2026-05 → 07 | — | **0** | **0** | — | — | — | — | — |
| 2026-08 | BTC5m | 860 | 3,465 | **4.0** | **0.989** | **0%** | **0.3%** | **291** |

**They stopped trading entirely for May, June and July**, then returned
in August with a different, single-sided strategy.

### Where the money actually came from

| income source | amount | note |
|---|---|---|
| REFERRAL_REWARD | **$49,435.87** | 91% of all incentives. Largest month is **April ($23,873) — a month with 30 fills.** Affiliate income, not a trading strategy, and not available to us. |
| MAKER_REBATE | **$13,537.44** ⚠ | $13,501.69 of it in **March** (the two-sided era); $35.75 in August. |
| REWARD | $583.13 | |
| TAKER_REBATE | $122.25 | August only — **direct evidence they took liquidity**, so "maker-only" is not a safe description of them. |
| **total incentives** | **$54,109.44** | |
| **trading P&L, August era** | **−$545.86** | 860 of 860 windows accounted — the one era that is cleanly measurable. |

**Which strategy contributed the most realized net profit in the latest
period? Neither.** August's single-sided 97–99¢ strategy produced
**−$545.86** of trading P&L across 860 windows (851 redeemed windows
accumulated +$1,534; six losing windows cost −$2,081) plus $158 of
rebates. In the one era with verified economics, incentives were the
business and trading was a cost. **0x8dxd is not a confirmed profitable
BTC5m trader**, and the March era's trading P&L is **unverifiable in both
directions**: 2,514 of 2,719 March windows (92%) have cost basis arriving
outside the trade tape, because a SPLIT mints both tokens for $1 and
appears as no trade. The 205 March windows that *do* reconcile read
−$39,038 (−30.2%); the 2,514 that do not read +$193,819 (+5.1%). **Neither
is a valid estimate**, because whether a window reconciles is itself
determined by whether SPLIT was used in it, which is part of the
strategy. This gap is not closable from these APIs.

The only source-confirmed economics in the straddle era:
**$10,061,554 traded → $13,501.69 maker rebate = 13.42 bps of notional** ⚠
(the truncated tape read 19.1 bps).

## 2. What 0x8dxd does (March two-sided era) vs what our bot does

| dimension | 0x8dxd, measured | our STRADDLE, as coded |
|---|---|---|
| first entry timing | **t+7s** median (p90 t+23s) | t=0 onward, both legs rest all window |
| simultaneous UP/DOWN | **95.8% of windows** | yes — fixed limit on both sides |
| quoting span | **222s** of the 300s window | t=0 → t=270 (`CANCEL_LEAD_S=30`) |
| placement price | BUY median **0.440**, p10 0.21, **p90 0.67** | fixed price bounded **(0, 0.50) exclusive**, ladder optional |
| → reachability | **35.6% of their buy fills are at ≥0.50 — outside our band by construction** | — |
| → deep-rung overlap | only **2.9%** of their buys at ≤0.10 | a 0.05 rung captures ~nothing |
| repeated fills/window | **106 median**, p90 302, max 1,374 | a handful; `MAX_RESTING_ENTRIES` is **ACCUMULATE-only**, not wired to STRADDLE |
| selling | **17.3% of fills**, SELL median 0.370 vs BUY median 0.440 | **never sells** |
| inventory imbalance at window end | **median 21.2%** — carries directional residual | completion targets a *matched* cheap pair |
| **repricing** | works a **0.310-wide band**, median **14 distinct 1¢ levels** per outcome per window | **one fixed price**, optional small ladder; no re-quote after fill |
| **completion / pair cost** | median completed pair **0.9342**; **78.0%** of windows < 1.00, **62.2%** ≤ 0.96, **36.8%** ≤ 0.90 | completion ceiling **0.90** in code (`COMPLETION_PAIR_COST_MAX`), stated policy target 0.96 |
| second leg timing | first Up buy to first Down buy: median **10s** (p90 54s) — both sides opened almost at once | both legs rest from t=0 |
| matched fraction at window end | median **0.63** of the smaller side matched | completion aims at a fully matched pair |
| per-window deployment | **$990 median** | $4.50 (2 × $2.25 clip) — **~220× smaller** |

## 3. The three biggest behavioral gaps

**Gap 1 — We quote where nobody trades.** Their liquidity sits at the
money (BUY median 0.440, p10–p90 0.21–0.67). Our straddle rests at a
fixed deep price below 0.50. Only 2.9% of their fills occurred at ≤0.10,
and 35.6% were at ≥0.50, outside our band entirely. A deep two-sided straddle is not a slow version of their
strategy; it is a different order that does not fill. This is the
mechanical reason our straddle produces almost no fills.

**Gap 2 — One shot versus 106.** They re-quote continuously for ~222s and
take a median of 106 fills a window (p90 302, max 1,374). Our straddle rests a fixed ladder and has no
re-quote-after-fill path (`MAX_RESTING_ENTRIES` governs ACCUMULATE only).
Frequency, not size, is the dimension we are missing — which matches the
standing `PATIENCE → FREQUENCY → SIZE → CONCURRENCY` order.

**Gap 3 — Our completion gate is set tighter than the pair economics
they actually achieved, and we cannot sell.** ⚠ **This corrects an
earlier claim of mine that 0x8dxd ran ≈$1.00 pairs and was therefore in a
different business. Measured, they are in the same business as our
straddle:** the combined cost of their matched pair is **median 0.9342**,
with **78.0%** of two-sided windows below 1.00, **62.2% at or below
0.96** — our stated spring target — and **36.8% at or below 0.90**. They
reach it by working a wide moving band, not by resting deep.

Two consequences. First, `COMPLETION_PAIR_COST_MAX = 0.90` in code is
*tighter* than the 0.96 the operating model states as spring discipline,
and it would refuse roughly **63%** of the pairs 0x8dxd actually
completed. Second, **17.3% of their fills are SELLs** (median 0.370,
later in the window than their buys) and they finish with a median
matched fraction of 0.63 — they recycle and re-shape inventory
intra-window. Our executor has **no sell action at all**, so a leg bought
too expensively can only be held to settlement.

## 4. Smallest changes that would close them — and the gate that comes first

**Nothing here is applied.** Priority order:

1. **Confirm rebate eligibility for our account, before any executor
   change.** This is the decisive question and it needs no code: at **13.42 bps**,
   our existing $4.50/window × 288 windows = $1,296/day of notional earns
   **$1.74/day** of rebate. If our account earns no maker rebate on
   PMUS, Gap 3 cannot be closed at any size and the March behavior should
   not be replicated at all. Matching a $480/day rebate income would need ~$3.6M/day of notional —
   ~2,750× our daily notional — so this is a *validation* path, not a
   profit path.
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
4. **Gap 3, part one — report only, needs your approval.** Aligning
   `COMPLETION_PAIR_COST_MAX` from 0.90 to the 0.96 the operating model
   already states would accept 62.2% of their observed pairs instead of
   36.8%. That is a **code ceiling**, and your standing instruction is
   not to raise code ceilings before the first live validation, so I have
   **not** changed it and am not asking to. It is recorded here as the
   measured reason the gate is mis-set relative to stated policy.
5. **Gap 3, part two — a SELL action. Do not attempt yet.** It is new
   execution capability, not a parameter, and it interacts with the
   reservation, the ownership registry and settlement reconciliation.
   Blocked behind step 1 and a separate design review.

### What is NOT observable, and so is not claimed

Order placements, cancellations, order ids and the book are absent from
both v2 routes: the tape holds **fills only**. So repricing is visible
only as the price path of fills, which bounds it (a 0.310-wide band, 14
distinct 1¢ levels) without showing the quotes that produced it. Whether
any individual fill was maker or taker is likewise unobservable — and
the account carries **both** MAKER_REBATE and TAKER_REBATE credits, so
"maker-only" is not a safe description of them. Queue position, fill
probability and adverse selection at our size cannot be read from
another trader's fills at 220x our size.

## 5. Settlement reconciliation, all completed windows (Priority 1)

3,579 completed BTC5m windows accounted, losers included — resolution is
the venue's winner flag, so a window where they held only the losing side
is present rather than filtered out.

| era | cost basis | n | BUY cost | SELL proceeds | redemption | net | win% |
|---|---|---|---|---|---|---|---|
| MARCH | reconciled | 205 | $129,163 | $13,578 | $76,546 | **−$39,038** | 23.9% |
| MARCH | UNRECONCILED | 2,514 | $3,827,414 | $765,133 | $3,256,100 | **+$193,819** | 62.6% |
| AUGUST | reconciled | 851 | $564,369 | $0 | $565,903 | **+$1,534** | 99.6% |
| AUGUST | no-redeem | 6 | $4,181 | $0 | $2,100 | **−$2,081** | 50.0% |
| AUGUST | UNRECONCILED | 3 | $821 | $0 | $822 | +$1 | 100% |

Trading net on **verified** cost basis: **−$39,585**. On windows with
cost basis **missing**: **+$193,820**, which is not a measurement.

Observable incentives, kept strictly separate because the venue credits
them as account-level rows naming no market: REFERRAL_REWARD $49,435.87,
MAKER_REBATE $13,537.44, REWARD $583.13, TAKER_REBATE $122.25, YIELD
$0.23 — **$63,678.92** total. **Per-trade fees are not observable at
all** on either route (the TRADE row's `usdc_size` equals price x size
exactly); that is not the same as zero.

## 6. Validation: 20 reconstructed windows

**Representative** (stratified across era x outcome x cost-basis
integrity, so no behaviour on the tape is invisible):
`0x8dxd_reconciliation_and_20_representative.txt`, with full fill tapes
in `0x8dxd_20_representative_full_tapes.txt`. 14 March windows (48–308
fills, BUY $526–$3,370, SELL proceeds up to $821, window-end imbalance
1.1%–96.7%) and 6 August windows (1–19 fills, always 100% imbalanced,
never any sell).

The August rows show the risk shape in one glance: wins of **+$0.09,
+$0.64, +$0.93, +$1.20, +$9.35, +$10.00** against a single
**−$889.97** — one 1-contract-type loss is 89x the largest win in the
sample.

**Chronological companion:** `0x8dxd_first20_settled_windows.txt` — the
first 20 settled March windows, 15 profitable / 5 losing, −$831.64 to
+$926.72. **19 of the 20 do not reconcile**, which is why March is
reported as unknown rather than positive.

`analysis/l5/reports/0x8dxd_window_44at99_1787613900.txt` — the August
window containing the verified 44-contract Down @ $0.99 fill, reconciling
exactly: 9 fills t+286→306, $694.56 cost, $701.54 redemption, **+$6.98**.
Our `CANCEL_LEAD_S=30` sweeps at t=270, so our executor cannot reach any
fill in that window — and since the August strategy is net negative,
that argues for leaving the cancel lead alone.

## 7. What to replicate next

**Not the August strategy** — measurably net negative over its author's
own 860 windows, and abandoned by its author.

**Not the March strategy on faith** — its trading P&L is unverifiable,
and its one confirmed margin is a 13.42 bps maker rebate whose
availability to our account is unconfirmed.

**Replicate the structure, in shadow, and measure our own fills:**
at-the-money two-sided quoting (Gap 1, config only) with re-quote
frequency (Gap 2), under unchanged ceilings, with the watchdog,
reconciliation and settlement accounting intact and the probability model
non-voting. The decision gate is step 1: whether our account is paid to
provide liquidity. Until that is answered from source, this stays
research.
