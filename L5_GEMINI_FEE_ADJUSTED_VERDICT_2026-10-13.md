# L5 Round 3 — Gemini Fee-Adjusted Verdict

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues `9bee2e4e`
**Mode:** read-only. Railway `get-logs` reads, web reads, local analysis. No orders, deploys, restarts, variables, services or spend. Gate `ARB_MECHANISM_CAPACITY_GATE.md` applied unchanged.

---

## 0. Corrections carried in

### 0a. PMUS verdict narrowed (your correction — accepted)

Round 2 said "ARCHIVE for spread capture." That was too broad. The correct scope:

> **Negative verdict applies only to static, simultaneous spread capture.** PMUS `btc-updown-15m` quotes a 1¢ spread at p25 through p95 (n=5,182), so there is no static two-sided spread to harvest. This says **nothing** about sequential accumulation of UP and DOWN at changing prices over a window — which is a different mechanism, is not tested by a spread distribution, and belongs to the replication lane. PMUS remains an open, lawful, deep venue (2,200–2,400 contracts at touch) for that question.

### 0b. Round-2 Gemini n=42 figure is withdrawn — it was biased, not merely small

I re-examined the round-2 proxy. Its settlement proxy was "the mid at the last series observation." I have now established that the series ends a **median 98.5 seconds before expiry** (§2a). So round 2 marked out to a **mid-window price, not settlement**. That is a methodological error, not a sample-size issue. The `+21.24¢` size-weighted figure is withdrawn and should not be cited.

---

## 1. Gemini fees and rebates — verified rates, unverified eligibility

Direct verification was **blocked**: the environment's network policy denied `www.gemini.com:443` (403 on CONNECT). Rates below come from search results citing Gemini's published pages, which matched your figures; I could not read the primary pages myself.

| Item | Value | Confidence |
|---|---|---|
| Maker rate | **0.0175 × C × P × (1−P)** | Confirmed, matches your figure |
| Taker rate | **0.07 × C × P × (1−P)** | Confirmed, matches your figure |
| Maker rebate | **0.30 × calculated taker fee**, capped at **5% of fill notional** | Confirmed |
| Rebate price band | **$0.20 – $0.80** execution price | Confirmed |
| Contract eligibility | contracts listed **on/after 2026-03-18** | Confirmed |
| Rebate rate history | 0.70 / 0.50 during promotions; **0.30** for crypto et al. from **2026-06-11** | Confirmed |
| **Maker Fee-Free Trading Promo** | **2026-09-02 → 2026-12-31**, waives maker fees on qualifying trades | Confirmed — **new, material** |
| Taker Rewards | extended through 2026-12-31; stackable with Maker Rebates and Liquidity Rewards | Confirmed |
| Settlement fee | none reported | Secondary source only |
| Transaction-level rounding | round up to the cent | **Secondary source only — Gemini's page does not state it** |
| Partial fills | immediate portion taker-rated, resting remainder maker-rated | Confirmed |
| **Our account's eligibility** | **NOT VERIFIED** | Requires authenticated account check |

### Fee-adjusted maker economics, per contract

Using the verified formulas (not assuming rebate eligibility):

| Price | Maker fee (normal) | Rebate (0.30 × taker) | Net during promo | Net post-promo |
|---|---:|---:|---:|---:|
| P=0.50 | −0.44¢ | **+0.53¢** | **+0.53¢** | **+0.09¢** |
| P=0.35 | −0.40¢ | +0.48¢ | +0.48¢ | +0.08¢ |
| P=0.20 (band edge) | −0.28¢ | +0.34¢ | +0.34¢ | +0.06¢ |
| P=0.85 (out of band) | −0.22¢ | **none** | 0¢ | −0.22¢ |

The 5%-of-notional cap never binds at these prices (2.5¢ cap vs 0.53¢ rebate at P=0.50).

**Revised finding: fees are not the binding constraint on Gemini.** In-band maker economics are approximately zero to slightly *positive* — the opposite of Kalshi, where taker-taker costs 3.50¢/contract at P=0.50. My round-2 framing of Gemini fees as an unknown risk is resolved in Gemini's favour.

**But fees cannot carry the lane.** At the measured ~49,000 contracts/day venue-wide, rebates at 10% capture yield **4,900 × 0.53¢ = $26/day**. Even capturing *100%* of venue volume gives $257/day from rebates alone. Rebates alone do not clear $50/day at realistic capture, so the verdict still rests entirely on spread capture net of adverse selection.

---

## 2. The n ≥ 200 certified-settlement census — cannot be completed from retained logs

**Answer to your question: no, we do not have enough retained records**, and the reason is specific and newly identified.

### 2a. Why — the two-sided book disappears before expiry

Retention is not the problem: logs reach back to **Sep 20** (23+ days), and I harvested **two blocks 22 days apart** (Sep 21 20:00–23:59Z and Oct 13 08:00–13:00Z) giving **252 series windows, 864 print windows, 124 joinable, 44,005 unique quote observations, 3,879 unique prints** — genuinely out-of-sample, not pooled correlated snapshots.

The blocker is structural:

```
len(SERIES rows) == obs  for 131/132 windows   -> the series IS the two-sided observation set
CLOSE tte_last      median  98.5s  <- where two-sided quoting STOPS
CLOSE tte_last_any  median   0.51s <- where the collector actually stops watching
complete=True: 0 / 132 windows
```

And the relationship is monotone — the more two-sided a window is, the later it stays two-sided:

| two-sided quartile | two_sided_pct | two-sided ends at | one-sided obs |
|---|---:|---:|---:|
| Q1 | 37.3% | 163.3s before expiry | 184 |
| Q2 | 61.8% | 103.3s | 110 |
| Q3 | 69.5% | 80.9s | 87 |
| Q4 | 77.8% | 65.3s | 63 |

**Gemini's book goes persistently one-sided a median ~98 seconds before expiry** — roughly the final third of a 5-minute window. Makers pull a side and do not come back.

This has two consequences. Economically: **you cannot run a two-sided maker book through the final third of the window**, which is exactly where settlement risk concentrates. Methodologically: there is no two-sided quote near expiry, so no terminal-quote settlement.

### 2b. Certified settlement is not reachable at scale either

- **No settlement events in the Gemini collector logs.** A filter for `SETTLE|SETTLEMENT|RESOLVE|RESOLUTION|OUTCOME` over Oct 10–13 returned zero records.
- **`btc-ref-collector` is a genuine independent reference** (nanosecond-stamped BTC BBO + trades, e.g. `bid 83334.66 / ask 83334.67`), and is the methodologically correct settlement source. But it covers only ~Oct 2 onward, and establishing settlement requires a reference price at each window's open and expiry — one narrow log query per boundary. At ~60–100 windows per day-block that is impractical through the log API.

### 2c. What the strict census actually yields

Applying proper causality — **quote must strictly precede the print** (larger tte), quote no more than 3s stale, decisive terminal state required at tte ≤ 5s:

```
QUALIFYING PROXY FILLS: n=12 across 3 independent market-windows
  skipped: no_decisive_terminal 113, terminal_not_decisive 8, inside_spread 6
```

**n=12 against a bar of n≥200.** I am reporting no markout from this. The buy/sell split (n=4 / n=8) and the 283-contract total are too small and too concentrated to carry any sign, let alone a magnitude. Reporting per-contract net expectancy, tail losses or capacity from 12 observations would be the exact error the gate was written to prevent.

### 2d. Concentration and fill-rate bounds — what can be said

| Quantity | Measured | Implication |
|---|---|---|
| Print size | median **10**, mean 34.9, p95 **100**, max **555** | Severely right-skewed; a handful of prints dominate any P&L estimate. This is why round 2's weighted and unweighted means diverged by 22¢. |
| Depth at touch | BTC 5m **25**, BTC 15m 10, ETH 5m 10, ETH 15m 10 | Per-window size is **depth-bound**, not edge-bound. |
| Spread | BTC 5m **16.0¢**, ETH 5m 18.0¢, BTC 15m 6.5¢, ETH 15m 7.0¢ | BTC 5m is the best structural cell. |
| Prints per window | median **0**, mean 1.59 | Most windows offer no counterparty at all. |
| Two-sided availability | 66.7% of window, ending ~98s pre-expiry | Quoting time is ~⅔ of each window. |
| Submit latency | due→subscribe 52ms, ack 3ms, first frame 97ms | Not the binding constraint. |

**Fill-rate bounds must stay conservative and unproven.** A print at our touch does not mean our resting order fills: we do not know queue position, and at 10–25 contracts of displayed depth we would frequently be behind existing size. A defensible bound is **0–30% of prints at our quoted level**, and that range is an assumption, not a measurement.

---

## 3. Gemini incentive programs — materially relevant, accessibility unverified

| Program | Status | Material? | Accessible to us? |
|---|---|---|---|
| Maker Rebate (0.30 × taker fee, $0.20–$0.80) | Active, crypto from 2026-06-11 | Yes — makes in-band making fee-positive | **Unverified** — needs account-level check |
| Maker Fee-Free Promo | 2026-09-02 → **2026-12-31** | Yes — removes maker fee entirely | **Unverified**; expires in ~11 weeks |
| Taker Rewards | extended to 2026-12-31 | Not for a maker strategy | n/a |
| Liquidity Rewards | Referenced as stackable | **Potentially decisive** — could exceed spread capture | **Unknown; likely requires an application and quoting obligations** |

Liquidity-rewards and designated-market-maker programmes typically carry uptime, spread-width and size obligations, and are granted by application. Given measured depth of 10–25 contracts, such obligations could easily exceed what this strategy would otherwise post. **Do not model any of these as available until eligibility is confirmed on the account.** Two have hard end dates inside this quarter, so any capacity estimate leaning on them is temporary by construction.

---

## 4. Revised fee-adjusted Gemini verdict

| Input | Status |
|---|---|
| Opportunity frequency | **Measured:** ~750 windows/day, ~330 with prints, ~1,400 prints/day, ~49,000 contracts/day |
| Spread available | **Measured:** 16¢ BTC 5m / 6.5¢ BTC 15m |
| Depth / size cap | **Measured:** 10–25 contracts at touch |
| Two-sided quoting time | **Measured:** ~⅔ of window; ends ~98s pre-expiry |
| Fees and rebates | **Verified rates; eligibility unverified.** In-band net ≈ **+0.53¢/contract** during promo |
| Latency | **Measured:** <150ms submit path |
| **Adverse selection / markout** | **NOT MEASURED — n=12 vs bar of 200** |
| **Fill probability** | **NOT MEASURED — queue position unknown** |

### Verdict: `UNMEASURABLE_ON_CURRENT_RIG`

Not negative — **unmeasured**, on the one input that decides it. Illustrating the sensitivity at 10% capture (4,900 contracts/day), promo rebate included:

| Assumed net capture after adverse selection | Daily net | Gate band |
|---|---:|---|
| 0¢ (adverse selection eats the spread) | **$26** | **< $50 → archive** |
| 1¢ | $75 | $50–149 → sidecar |
| 3¢ | $173 | ≥ $150 → validate |
| 5¢ | $271 | ≥ $150 → validate |

The entire verdict swings across all three gate bands on a quantity I could not measure. **Per the gate, no numeric capacity may be assigned.** Fees are resolved and favourable; they are simply not what was blocking.

### The single unblock

The full data already exists: **7,476,069 BBO rows and 51,053 prints on the `gemini-5m-tape` volume** (`/state/gemini-5m`, 45.8 GB free, 6.3% used). The logs carry only the two-sided subset; the volume has everything including the one-sided final third and the pre-expiry book.

So — and this partially restores a round-1 instinct I withdrew in round 2, now for a precise reason and a *different service* — the correct next step is a **read-only inventory of the `gemini-5m-tape` volume** (service `26b2f2de`, volume `53a94878`), not of the PMUS recorder that `l5-volume-export.yml` hardcodes. That workflow's `SERVICE_ID`/`REMOTE_DIR` would need to point elsewhere; it remains manual, single-service and artifact-only, so this is a *new, isolated* read-only path rather than a repair of it. Combined with `btc-ref-collector` for certified settlement, that supports the n≥200 census with no new collector and no change to the live one.

---

## 5. If Gemini fails — strongest next discovery candidate

**PMUS sports and sub-four-hour non-crypto breadth**, via the already-live `pmus-sports-forward-collector` (deployed Oct 11, volume `/state/pmus-sports`).

Rationale: after three rounds, all four crypto venues are now characterised — Polymarket (edge exists, not our venue), PMUS (deep, 1¢ static spread), Kalshi (fee-adverse, 5.8 snapshots/day), Gemini (wide spread, thin depth, unmeasured markout). The sports slate is the **only large universe with a live collector and no economic measurement at all** — historically 1,502 markets tracked. It is unmeasured rather than unpromising, and measuring it needs no new infrastructure.

Secondary candidate: Kalshi's `tapered_deci_cent` ladder (0.1¢ ticks below 10¢ and above 90¢), where tick granularity is 10× finer and the convex fee is near its minimum — the one Kalshi cell the fee math does not obviously kill.

---

## 6. Summary

**Truly running:** `gemini-5m-collector` (7.48M BBO rows, live), `pmus-ws-evidence-recorder` (live, two-sided depth), `btc-ref-collector` (live, ns-stamped reference, ~Oct 2+), `pmus-sports-forward-collector` (live, unmeasured), Kalshi GH Actions tape (5.8/day), Polymarket wallet tape (42 days). Three services CRASHED.

**What changed:** no infrastructure, deployments, services, credentials, thresholds or spend. One markdown file on this research branch. Gemini fee rates verified and found non-binding; two active incentive programmes discovered; round-2's n=42 markout withdrawn as biased; PMUS verdict narrowed to static spread capture.

**Biggest blocker to realized profitability:** adverse selection and fill probability on the one venue with spread to capture remain unmeasured, because the log feed carries only the two-sided subset and that subset ends ~98s before expiry — precisely where the risk is.

**Next concrete action:** read-only inventory of the `gemini-5m-tape` volume, then run the n≥200 proxy from volume data with `btc-ref-collector` settlement, stratified by time-to-expiry and reporting buy/sell markouts separately with conservative 0–30% fill bounds. Separately, confirm account eligibility for the Maker Rebate and Maker Fee-Free promo before either is modelled. Neither needs a deployment or trading authorization.

**Network note:** `www.gemini.com` is denied by this environment's network policy. To let me verify the fee and rebate pages directly, add `www.gemini.com` and `developer.gemini.com` under Allowed domains in the environment's Network access settings (keep "Allow package managers" ticked): https://code.claude.com/docs/en/cloud-environments#network-access
