# L5 Cross-Platform Opportunity Discovery — Measured Report

**Date:** 2026-10-13 · **Mode:** read-only / shadow-only. No orders, no deployments, no config or risk changes, no new services or branches.
**Scope:** opportunity discovery and economics only. Behavioral replication, continuous inventory, PMUS manual-fill comparison and executor work belong to the parallel session and were not touched.

All dollar figures below are **measured from committed evidence in this repository**, not modeled. Status ladder language is preserved: nothing here is called realized unless it is settled cash flow in the wallet tape.

---

## 1. What discovery/collection infrastructure is actually running

### Working (produces usable economic evidence)

| Lane | Mechanism | Cadence actually achieved | Verdict |
|---|---|---|---|
| **Polymarket L5 wallet activity** | `l5-ops-24x7.yml` → `l5-ops-data` branch, `data/l5/ops/activity_*.jsonl` | 42 consecutive days, Sep 1 – Oct 13, 10–15k records/day | **Only decision-grade dataset in the system** |
| Kalshi BTC/ETH 15m tape | `kalshi-l5-tape-5m.yml` → `l5-kalshi-data` branch | **5.8 snapshots/day** (237 runs / 41 days) vs 288 scheduled | Depth-20 book + trades, but far too sparse for frequency work |

### Stale or broken

- **GitHub Actions cron is throttled.** Both collectors declare `*/5 * * * *` (288 runs/day). The Kalshi workflow has 237 total runs across 41 days — every one `success`, spaced 3–7 hours apart. This is not a code defect; GitHub does not honor 5-minute schedules on this repo. **Any lane whose economics depend on sub-hour book cadence cannot be measured from GitHub Actions.**
- **Railway: 59 services, nearly all nominally `live`, but only ~15 have an October deployment.** `SUCCESS` means the last deploy succeeded, not that a collector is writing.
  - CRASHED: `l5-exec-shadow`, `l5-live-habitat`, `kalshi-incentive-shadow`
  - FAILED: `l5-transfer-supervisor-shadow`, `pmus-r5ft-complete-retry-buildcheck`
  - **All six Gemini services last deployed Sep 12–23** — Gemini coverage is dormant.
  - `kalshi-l5-shadow` last deployed Sep 15.
  - Fresh (Oct): `pmus-sports-forward-collector`, `pmus-ws-evidence-recorder`, `pmus-readonly-recovery`, `l5-pmus-live`, `l5-ops-research-live`, `l5-v1-1-shadow`, `btc-ref-collector`, `l5-e1-safety-monitor`.
- **`L5_NOW.md` on `main` is 40 days stale** (snapshot 2026-09-03) and still names `feature/l5-transfer-backtest` at a commit that is no longer the active head. It should not be used as recovery state.
- Collector data on Railway volumes (`gemini-5m-tape`, `kalshi-l5-tape`, `pmus-ws-*`, 21 volumes) is **not readable from a read-only session** — the `l5-volume-export` workflow exists to bridge this and has a history of auth fixes. This is why the wallet tape is the only dataset I could actually measure.

---

## 2. Venues and market categories covered

| Venue | Coverage status | Categories present |
|---|---|---|
| **Polymarket** (global) | Full 42-day fill-level evidence | BTC/ETH up-down 5m & 15m |
| **PMUS** (US-regulated) | Services fresh, data volume-locked | sports slate (1,502 markets tracked historically), BTC 15m |
| **Kalshi** | Sparse public tape only | `KXBTC15M`, `KXETH15M` — 1 at-the-money strike each per snapshot |
| **Gemini** | Dormant since Sep 23 | BTC 15m (prior work) |

**Eligibility caveat that matters economically:** the measured wallet trades on **Polymarket, not PMUS**. PMUS is the US-lawful venue. The economics in §3 are observed on Polymarket's fee schedule and liquidity and are **not automatically transferable to PMUS**.

---

## 3. Measured opportunity frequency and economics

Source: 480,000 TRADE records + 11,000 REDEEM records, single wallet `l5Zn1bWoM8eTsK` (`0xb945…db68`), Sep 1 – Oct 13, $5.33M notional.

### 3a. Realized net cash flow — the ground truth

```
Cumulative net cash (redeem + rebates − buys), 42 days:  $74,184
Mean per day:                                            $1,766
Days positive:                                           31 / 42  (73.8%)
Best day:                        +$10,462     Worst day:  −$7,323
```

This is settled cash flow, not shadow P&L. **It is 3.7× the $480/day objective** — on one wallet, one venue, one market family.

### 3b. Category mix — the strategy is monolithic

| Category | Trades | Share | Notional |
|---|---:|---:|---:|
| BTC 15m up/down | 450,527 | **94.3%** | $4.82M |
| BTC 5m / ETH 5m / ETH 15m | 23,842 | 5.0% | $0.47M |
| ETH 15m (tagged) | 3,118 | 0.7% | $0.03M |

There is **no sports and no non-crypto activity in this wallet at all.** Every dollar comes from crypto up-down markets settling in 5–15 minutes.

### 3c. Opportunity frequency at net-edge thresholds

Measured on **actual fills**, not theoretical books. Paired edge = `100¢ − (volume-weighted avg Up price + volume-weighted avg Down price)`, i.e. the spread genuinely locked at achieved prices. n = 4,750 events over 42 days. Polymarket charges no per-trade fee on these markets (only 8 rebate records in 480k), so gross ≈ net at entry.

| Net-edge threshold | Events/day | Paired shares/day | Gross locked $/day |
|---|---:|---:|---:|
| ≥ 1¢ | 77.8 | 82,784 | $4,901 |
| ≥ 2¢ | 70.7 | 71,111 | $4,725 |
| ≥ 3¢ | 62.7 | 59,184 | $4,424 |
| ≥ 5¢ | 47.4 | 39,565 | $3,650 |
| ≥ 10¢ | 19.9 | 11,478 | $1,677 |

Paired-edge distribution: **median +3.76¢**, mean +2.93¢ (p5 −21.5¢, p75 +7.9¢, p95 +19.3¢).

**Opportunity is abundant and the slate is already saturated:** the wallet trades a median of **95 of the 96 available BTC 15m events per day**. Frequency is not the constraint.

### 3d. The real constraint — unmatched inventory, not opportunity

Decomposing the same event set (those with completed settlement):

```
Gross locked paired edge:   $4,291/day
Realized net:                 $925/day
Consumed by residual:       −$3,366/day   (78% of gross edge)

Unpaired residual: 11.1% of all shares (28,006 shares/day)
Implied cost per residual share: −12.0¢
```

A residual share bought near 50¢ on a coin-flip should cost ~0¢ in expectation. It costs **−12¢**, which means the unpaired side is **systematically adversely selected** — fills arrive on the side that is about to be wrong. *(This is an accounting decomposition that attributes all profit to the paired portion at average prices; treat the 78% as indicative of magnitude, not exact. The robust figures are the §3a cash flow and the +$12.02 mean per event.)*

Per-event economics confirm this is **positive-expectancy market making, not arbitrage**: mean **+$12.02**/event, median +$0.30, and only **50.2% of events net-positive** (p5 −$180, p95 +$208). 100% of events have both outcomes bought, median min/max share ratio 0.82 — deliberately near-balanced but never fully paired.

### 3e. Capital efficiency — exceptional

| Metric | Value |
|---|---|
| Capital per event | median **$590**, p75 $1,068, p95 $3,034, max $8,923 |
| Holding period | median **12.8 min** (full 15m window) |
| Turnover | ~96 events/day |
| Working capital supporting $100k+/day notional | **~$3–9k** |

Settlement is same-window, so capital recycles ~96×/day. This is the strongest structural feature of the category.

### 3f. Why this does not trivially port to Kalshi — fee math

Kalshi fees are convex and maximized exactly where these markets trade (P≈0.50):

| Leg structure | Fee cost/contract at P=0.50 | Remaining from median 3.76¢ edge |
|---|---:|---:|
| Both legs taker | 3.50¢ | **+0.26¢** |
| Both legs maker | 0.88¢ | +2.88¢ |

**Taker-taker execution on Kalshi destroys ~93% of the observed median edge.** The strategy is only viable on Kalshi with maker fills on both legs. Polymarket's zero per-trade fee is the structural reason the measured $1,766/day exists there. Kalshi's `KXBTC15M` `tapered_deci_cent` ladder (0.1¢ ticks below 10¢ and above 90¢) does favor lopsided strikes, where fees are also lower — a genuine but unmeasured niche.

---

## 4. Strongest candidate market categories (ranked)

| # | Category | Freq/day | Net expectancy | Liquidity | Settlement | Capital eff. | Confidence |
|---|---|---|---|---|---|---|---|
| **1** | **Polymarket BTC 15m up/down** | 95 events | **+$12.02/event measured** | deep enough for $600–3k/event | 15 min | excellent | **High** — 42d, 450k fills, settled cash |
| **2** | Polymarket BTC/ETH 5m up/down | ~288 slots | positive, 5% of volume | thinner | **5 min** | excellent | **Medium** — real but under-sampled |
| **3** | Polymarket ETH 15m | ~95 | positive, small | thin | 15 min | excellent | **Medium** |
| **4** | Kalshi KXBTC15M / KXETH15M | 96 | **unmeasured**; maker-only viable per §3f | depth-20 book exists | 15 min | good | **Low** — 5.8 snapshots/day |
| **5** | PMUS sports short-duration | unknown | unmeasured | 1,502 markets tracked | hours | fair | **Low** — volume-locked |
| **6** | Gemini BTC 15m | unknown | unmeasured | unknown | 15 min | unknown | **None** — dormant 3 weeks |
| **7** | Cross-venue locked arb (K↔P) | — | **prior verdict: nine-key lower bound** | — | — | — | **None** |

**Conclusion: crypto up-down markets settling in 5–15 minutes are the category.** They win on every axis the mission names — frequency, measured expectancy, settlement speed and capital efficiency. Sports contributes zero measured dollars in this wallet. Cross-venue locked arbitrage remains unproven and, per `ARB_MECHANISM_CAPACITY_GATE.md`, its Aug-28 Kalshi↔Polymarket result is explicitly a **nine-key census / lower bound** invalidated by an identity/matcher defect — it should not receive engineering time ahead of the above.

---

## 5. The biggest blocker

**Two, in priority order.**

**1. Economic (the one that costs money): unmatched-inventory adverse selection.** ~78% of gross paired edge — on the order of **$3.4k/day** — is consumed by an 11% residual that loses ~12¢/share. Opportunity frequency is already saturated at 95/96 events; **no amount of additional discovery adds a dollar.** The entire remaining upside in the proven category is pairing completion and residual management. This is adjacent to the other session's continuous-inventory work and should be handed to it rather than duplicated.

**2. Data (the one that blocks every other venue): no venue has book-level cadence outside Polymarket.** GitHub Actions caps at ~5.8 snapshots/day against 288 scheduled, and the always-on Railway collectors write to volumes that read-only sessions cannot reach. Consequently **Kalshi, Gemini and PMUS economics are currently unmeasurable at any cent threshold** — not negative, genuinely unmeasured. Per the capacity gate's own rule, these must be classified `UNMEASURABLE_ON_CURRENT_RIG` rather than assigned a verdict.

---

## 6. Next concrete action

**Make the Railway volume export work, then run the Kalshi maker-leg census.** Single action, in order:

1. **Fix `l5-volume-export.yml`** (its last four commits are all auth fixes) so `kalshi-l5-tape` and `gemini-5m-tape` volumes land on `l5-kalshi-data` the way `l5-ops-data` already works. This converts two dormant-but-running collectors into decision-grade datasets without new services, branches or spend.
2. **Stop treating GitHub Actions cron as a collector** for anything cadence-sensitive; it is a 5.8/day transport, not a 5-minute one. Keep it for the wallet tape, where that cadence is sufficient because the records are cumulative.
3. **Then** run the §3c threshold census against Kalshi's depth-20 book under the maker-maker fee structure from §3f — the only Kalshi configuration the fee math permits — and apply the existing `ARB_MECHANISM_CAPACITY_GATE.md` $50/$150 bands to the result.

Do **not** open a new strategy lane. The measured evidence says the proven category is already found and already saturated; the value is in (1) closing the residual leak in the lane that works, and (2) unlocking measurement on the venues that are currently dark.

### Integration into the canonical pipeline

The measurement in §3 is reproducible from committed data with no new infrastructure: `l5-ops-data` → `data/l5/ops/activity_*.jsonl`. Recommended canonical home is the existing `l5-ops-24x7.yml` **daily-regression** job (cron `17 3 * * *`, already on `feature/l5-canonical`), emitting the §3c threshold table and the §3d residual decomposition as a daily artifact. That is an added read-only metric on an existing job — no new service, deployment, branch or capital authorization.

---

## Appendix — reproduction

```bash
git fetch origin l5-ops-data
git archive origin/l5-ops-data data/l5/ops | tar -x -C <workdir>
# §3a cash flow: sum REDEEM + *_REBATE usdcSize minus BUY usdcSize, grouped by file date
# §3c thresholds: group TRADE/BUY by conditionId; per outcome volume-weighted avg price;
#                 paired edge = 100 - sum(avg prices); paired shares = min(shares per outcome)
```

Infrastructure claims verified against: `actions_list` on `kalshi-l5-tape-5m.yml` (237 runs), Railway `describe-environment` on `arb-ev-wallet-shadow` (`50fb7357-…`, production `f3642d27-…`), and `git for-each-ref` on all 60+ remote branches.
