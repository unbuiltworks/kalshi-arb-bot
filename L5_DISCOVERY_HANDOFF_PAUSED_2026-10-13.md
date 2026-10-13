# L5 Platform-Wide Discovery — Final Handoff (lane PAUSED)

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · final commit of the lane, following `f9e4bdac`
**Status:** **PAUSED.** Strategic ownership returns to the L5 behavioural replication session. No new venue, branch, service, collector or strategy started. Read-only throughout: no orders, deployments, restarts, storage changes or spending in any round.

This document is an index of **existing evidence only**. It proposes no strategy and duplicates no replication work.

---

## 0. Two qualifications carried into the record

**(a) The $0.50/day sports figure is not a capacity bound.** It is a **gross-equivalent illustration of the existing shadow policy at its observed scale**. It uses *median* pair costs rather than summing individual pair economics; `exposure_hours` is not necessarily elapsed wall-clock time; and concurrent markets complicate any conversion to daily capacity. It supports the STOP FOR RESOURCE ALLOCATION decision; it does not establish a ceiling on sports profitability.

**(b) ITF / ATP / WTA tennis and cricket are UNMEASURED, not negative.** The reconciled funnel covers **NFL, UFC, boxing and CFB only**. The sports-forward collector tracks ~2,601 markets including tennis and cricket, but writes book rows to its volume rather than to logs, so their economics were never measured. Nothing here is a scientific conclusion that sports markets are unprofitable.

Both stops are **resource-allocation decisions**: Gemini short-duration maker quoting, and the existing PMUS NFL/UFC/boxing/CFB sequential-pairing policy. Neither demonstrated repeatable executable profitability.

---

## 1. L5 Polymarket wallet activity and settlement accounting

**Source:** branch `l5-ops-data`, path `data/l5/ops/activity_*.jsonl`. Produced by `.github/workflows/l5-ops-24x7.yml`. 42 consecutive days, Sep 1 – Oct 13, 10–15k records/day. **The only decision-grade, settled-cash dataset in the system.**

Schema per record: `proxyWallet, timestamp, conditionId, type, size, usdcSize, price, asset, side, outcomeIndex, title, slug, eventSlug, outcome, name, _venue, _collected_utc`.

| Finding | Value |
|---|---|
| Wallet | 100% single wallet `0xb945…db68` / `l5Zn1bWoM8eTsK` |
| Record types | `TRADE` 40,327 · `REDEEM` 1,002 · `MAKER_REBATE` 4 · `TAKER_REBATE` 4 (Oct 10–13 sample) |
| Net settled cash flow, 42 days | **$74,184 → $1,766/day mean** |
| Days positive | 31 / 42 (73.8%); best +$10,462, worst −$7,323 |
| Category mix | **BTC 15m up/down 94.3%** of trades ($4.82M notional); BTC/ETH 5m + ETH 15m 5.0%; no sports, no non-crypto |
| Direction | **Zero SELL trades** — all BUY + REDEEM |
| Slate saturation | median **95 of 96** available BTC 15m events traded per day |
| Per-event | mean **+$12.02**, median +$0.30, **50.2%** of events net-positive (p5 −$180, p95 +$208) |
| Two-sidedness | 100% of events bought both outcomes; median min/max share ratio 0.82 |
| Capital | median **$590**/event, p95 $3,034; median hold **12.8 min**; ~96 turns/day |

**Reproduction:** `git archive origin/l5-ops-data data/l5/ops | tar -x`; net cash = `REDEEM + *_REBATE usdcSize` − `BUY usdcSize`, grouped by file date.

**Caveats to verify before relying on $1,766/day:** it is *net cash flow*, not an audited P&L — settlement, rebate and event-level reconciliation were not independently verified. The wallet trades **Polymarket, not PMUS**; transfer is not established. The round-1 decomposition attributing ~78% of gross paired edge to unpaired residual (~−12¢/unpaired share, 11.1% of shares) is an **accounting attribution, not a proven causal loss mechanism or recoverable profit**.

## 2. Gemini — native execution semantics and official resolution

**Native order semantics** (from repo code and the live canary, not docs): payload `{clientOrderId, symbol, side, outcome, orderType, timeInForce, price}` with `outcome ∈ {UP, DOWN}`, `NATIVE_SIDE = "UP"`, **separate per-outcome inventories** (`nup`/`ndn`). The live canary submitted `side=SIDE_BUY` only. All 2,692 harvested market symbols are `-UP`; no `-DOWN` symbol was observed. **Buy-the-outcome instruments**, like Kalshi and Polymarket. Resting a SELL of UP is **not** the native action; the equivalent native action is a resting BUY of DOWN at `1−A` (identical arithmetic and fees, since `P(1−P)` and the rebate band are symmetric about 0.50). **That complement path is unverified, not disproven.**

**Official resolution is retrievable and already stored:** branch `claude/gemini-btc-economic-viability-h3u7zl`, `analysis/gemini/**` — `resolutionSide` ("no" ×12,995, "yes" ×9,988), `settlementValue`, `resolvedAt`. **201 distinct resolved event tickers**, Aug 27 – Oct 4. Ticker mapping confirmed empirically: `GEMI-<EVENT>-UP` → `<EVENT>`. This **refutes** the `gemini-settlement-gate.yml` header claim that the venue "publishes NO terminal value anywhere public."

**Fees (rates verified; account eligibility NOT verified):** maker `0.0175·C·P(1−P)`, taker `0.07·C·P(1−P)`, maker rebate `0.30 ×` calculated taker fee capped at 5% of notional, eligible only for executions in **$0.20–$0.80** on contracts listed on/after 2026-03-18. **Maker Fee-Free promo 2026-09-02 → 2026-12-31.** Transaction-level rounding (fee **up**, rebate **down**) makes per-contract economics size-dependent: at P=0.50 post-promo, C=1 is **−1.000¢/ct**, C=10 is 0.000¢, C=25 is +0.080¢. No minimum size is recommended as a rule.

**Market structure:** spread median 16¢ (BTC 5m) / 6.5¢ (BTC 15m); depth at touch 10–25 contracts; ~49,000 contracts/day venue-wide; **100% of 152,597 logged series rows are two-sided — the one-sided book is structurally unobservable in this feed**, and two-sided quoting ends a median ~98s before expiry.

**Frozen estimator** `a163c201` and its result `7a8717ab`: n=50 fills / 16 markets; buy-only (the confidently executable subset) **−0.36¢ gross, −0.78¢ after fees**, median −20.00¢, cluster-bootstrap 95% CI **[−59.69¢, +49.11¢]**, P(mean ≤ 0) = 52.1%. Every concentration-robust cut ≤ 0. **No capacity figure is supportable** — the CI includes substantial positive expectancy, and scaling an adversely-selected fill mean by a fill rate is invalid.

## 3. PMUS single-book price semantics

The sports collector (`src/pmus_sports_offers_hotfix.py:bbo_book_row_offers`) emits **one `bid`/`ask` pair per market slug**, from `bestBid`/`bestAsk`, plus `bid_touch_qty` / `ask_touch_qty`. Two schema facts worth carrying: PMUS `/v1/markets/{slug}/book` serves sell depth under **`offers`**, not `asks`; and top bid size is **`bid_touch_qty`**, not `bid_qty`.

**Consequence:** on this complementary single book, `YES_ask + NO_ask = ask + (1 − bid) = 1 + spread`. **Simultaneous taker pairing costs more than $1 by construction** and can only profit on a crossed book. The relevant mechanism is sequential maker pairing.

**BTC 15m book (`cpc-btc-updown-15m`), n=5,182 unique observations:** spread **1.00¢ at p25, median, p75 and p95**; bid depth median 2,364, ask depth median 2,201 contracts; **0.0%** of observations reach 4¢; 1.9% reach 2¢; **zero genuinely crossed** books (an initial 4.4% reading was a test counting `spread == 0`, which are `bid == ask == 0.99` at the settlement boundary); feed 92% `OK`, 8% `STALE`, 0% `ask_book_missing`. **Negative verdict applies only to static simultaneous spread capture** — it says nothing about sequential accumulation at changing prices, which is replication's question.

**Native action** (from the live adapter's own decision output): `decision: "quote"`, `side ∈ {YES, NO}`, `inventory_effect: "+1 NO"`, `q1_size: 1`, `max_pair_cost: 0.98`, band 0.20–0.80, `order_entry: false`, DISARMED. This establishes **modelled order semantics only** — not order acceptance, fill or settlement on a given contract.

## 4. Reconciled PMUS sports shadow findings

**Two modules, two populations — not a disagreement.** `virtual_completions_le_1` / `virtual_first_fills` (`pmus_sports_shadow_breadth.classify_actions`) count **replayed policy actions** (`decision=="quote"`, `fill_class=="possible"`) bucketed by `relation` open/extend/complete, deduped by `(slug,t,relation,target_price)`; the `le_1` suffix is **vestigial**. `VIRTUAL_MATCHED` (`pmus_execution_shadow`) counts **proposals followed forward to terminal status**.

**The round-8 "0.230% completion / 99.770% residual" figure is WITHDRAWN** — it divided complete-relation action counts by open-relation action counts across two accumulation windows in a module that counts actions, not outcomes. It is not a funnel and must not be cited.

**The one citable cohort:**

| Stage | Count |
|---|---:|
| Proposals total | 2,402 |
| Resolved (terminal) | 2,011 (= 56 + 1,955; excludes 391 `SUPERSEDED_BY_REQUOTE`) |
| `VIRTUAL_MATCHED` | **56** |
| `NO_FILL` | 1,955 |
| **p(completion \| first fill)** | **2.78%** |
| Actual fills | **0** |
| Verified net P&L | **not measured** |
| Exposure | 164.29 h |

**Reachability — the named bottleneck** (1,956 measured; a *third* denominator, not merged): `never_reached` 1,893 (**96.8%**), `reached_our_price` 63, `filled` 55, `reached_but_unfilled` 8; median gap **2¢**, median favourable excursion **0.0¢**; median time to touch 116.8s. The module's own verdict: *"REACHABILITY: most complements were never reached by the market. Look at first-leg price, not at fill mechanics."*

**Queue arms:** $0.980 → trade-through 4.12%, displayed-ahead ~274,406 units; $0.985 → 4.40%, ~219,385; $0.990 → 17.00%, ~98,707; $0.995 and touch → 41.87% / 48.65%, 0. Profitable pair costs sit 2–3 ticks inside the touch behind large better-priced size; where queue-ahead is zero the gross edge is ~0.5¢, below the 1¢ gate before fees.

**Timing:** `frozen_300s` filled 56/2,011, median 46.3s; `research_1800s` adds **337** later fills (19.5% by 1,800s) — at the cost of longer unpaired exposure, which is unpriced.

**Distance bands:** 1,617 of 1,956 observations sit 3–4 ticks away at a **0.37%** fill rate with 4,244 residual units; the high-fill bands (0–2 ticks, 40–57%) hold only 78 observations. **Predictor separation:** matched markets had median flow **$204.28/min** vs **$15.36/min** for no-fill.

**Per sport:** NFL n=1,521 p=2.37% pair cost $0.9167 · UFC n=312 p=2.56% $0.98 · boxing n=151 p=4.64% $0.97 · CFB n=27 p=18.52% $0.84 but `sufficient_sample: false`.

## 5. Known limitations — read these before reusing any number above

- **Nothing in this lane is realized profit.** Every PMUS and Gemini figure is theoretical, shadow or proxy. Actual fills: **0**. Only the Polymarket wallet carries settled cash, and even that is unaudited net cash flow.
- **Queue priority is unknown everywhere.** PMUS `queue_ahead_qty` is true displayed cumulative *better-priced* size, side-aware, computed **only where `book_levels` was retained** and null — never guessed — otherwise. It answers "how much better-priced size must clear before anyone reaches us," **not** our FIFO position at our own price. Zeros at the touch arms mean our price *is* the touch, not that we are first in line.
- **Virtual fills are upper bounds.** `pmus_sports_l5_decision._fill_evidence` takes the first later inferred trade at/through the resting price, strictly causal, one per quote; the module states it *"is an UPPER BOUND — queue position is unproven"* and bounds quantity by what printed through, because *"we cannot be ahead of a queue we cannot see."* The 55/63 conditional ratio does **not** establish that fill mechanics are sound.
- **Residual is unpriced, and `risk_created_units = 0.0` is not an error.** It sums over *matched* proposals only, correctly reporting that completions created no new residual. Unpaired first-leg residual was never in that field. `pmus_execution_shadow` has **no settlement, outcome, resolution or payout field at all**, so first-leg exposures cannot be valued. **Missing evidence: a PMUS market-resolution record joinable to market slug.**
- **Fee uncertainty.** Gemini rates verified but **account eligibility unverified**; the Maker Fee-Free promo **expires 2026-12-31**; rounding is transaction-level. PMUS: `maker_rebate(p) = 0.0125·p(1−p)` is a **hardcoded local copy** while `taker_fee` deliberately reads an effective-dated schedule — and that rebate supplies 38% of the assumed edge in `theoretical_dollars_per_capital_hour`.
- **`theoretical_dollars_per_capital_hour` is definitional, not economic:** `(matched/slots) × (MIN_EDGE + 2·maker_rebate(0.5)) / (SLOT_S/3600)` with `MIN_EDGE = $0.01`, `SLOT_S = 300` → `0.01625/0.08333 = 0.195`. No capital appears in it; the edge is the **configured gate threshold**; match rate is the only measured input. It must never be read as realized or as a return on capital.
- **Collector cadence and coverage.** GitHub Actions throttles the declared `*/5` cron to **~5.8 runs/day** (237 runs / 41 days), so Kalshi book cadence is unusable for frequency work. `pmus-ws-evidence-recorder` holds **BTC only** — zero sports book rows. Gemini series contains **only two-sided** observations. Kalshi taker-taker costs **3.50¢/contract** at P=0.50.
- **Sample discipline.** Chronological separation alone is not out-of-sample; rules must be fixed on one period and run unchanged on another (as in `a163c201`). The Gemini settled set is a **convenience sample** — the intersection of what prior sessions stored and what retention holds.

## 6. For the infrastructure owner — `l5-pmus-observer` volume full

```
OSError: [Errno 28] No space left on device:
  '/state/l5-pmus-obs/windows/cpc-btc-updown-15m-2026-10-13-1630z'
```
Repeating every ~5s. Service `9ce058ce-1cbc-423b-80b1-7fd07544f499`, volume `l5-pmus-obs-evidence` (50 GB) at `/state/l5-pmus-obs`, project `arb-ev-wallet-shadow` (`50fb7357-…`), env production (`f3642d27-…`).

- **Data is not unique:** crypto `cpc-btc-updown-15m` window records. `pmus-ws-evidence-recorder` is healthy and writing the same market family (1,945 book observations on the `1630z` window alone). Granularity differs — window records vs. book/trade rows — so coverage **overlaps but is not provably identical**.
- **Existing files are intact; new writes are lost.** The actionable risk is the pattern: **the process stays running while persistence fails**, so health signals can look normal while the historical record silently gaps.
- **No sports-discovery impact** — restoring it would not advance sports work.
- **Nothing was done:** no file deleted, no volume resized, no service restarted, no unrelated infrastructure touched. Triage this without disturbing healthy collectors or deleting un-backed-up evidence.

Also worth noting for maintenance: `l5-exec-shadow`, `l5-live-habitat` and `kalshi-incentive-shadow` are **CRASHED**; `l5-transfer-supervisor-shadow` and `pmus-r5ft-complete-retry-buildcheck` are **FAILED**. Of ~59 services in the project, only ~15 have an October deployment.

## 7. Preserved assets and the resumption condition

**Preserved:** this branch's nine reports (`f470d2aa` → `f9e4bdac`); the frozen Gemini estimator `a163c201`; the 201-ticker Gemini settlement set and its ticker mapping; the Polymarket 42-day wallet tape; the Kalshi 41-day tape; and the running collectors (`gemini-5m-collector`, `pmus-ws-evidence-recorder`, `pmus-sports-forward-collector`, `btc-ref-collector`).

**Also preserved as reusable method**, independent of any venue verdict: establish venue-native executable actions before assigning P&L; audit whether a headline metric is definitional; report concentration-robust statistics (ex-best-market, cluster bootstrap) in the first pass; characterise what a feed cannot see before interpreting it; and pre-register an estimator before measuring a holdout.

**Resumption condition:** this lane resumes only when the replication session identifies a **specific missing venue capability or economic measurement**, or materially new evidence justifies a targeted investigation. It does not resume for another market census.

**Open, named, and unresolved** — carried for whoever needs them: Gemini DOWN-side executability (unverified, not disproven); PMUS sports settlement joinability (no resolution record found); ITF/ATP/WTA tennis and cricket economics (unmeasured, book data on volume only); Gemini and PMUS account-level rebate eligibility; and Kalshi's `tapered_deci_cent` ladder below 10¢ and above 90¢, where ticks are 10× finer and the convex fee is near its minimum.
