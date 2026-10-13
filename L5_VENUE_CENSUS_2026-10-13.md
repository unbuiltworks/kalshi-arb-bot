# L5 Platform-Wide Venue Census — Round 2

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues commit `f470d2aa`
**Mode:** read-only. Only Railway `get-logs` and `describe-*` reads, GitHub reads, and local analysis. No orders, deploys, restarts, variable changes, credentials, new services or spend. No file outside this branch touched.

---

## 0. Two corrections to the previous report (`f470d2aa`)

**Correction 1 — "Gemini dormant since Sep 23" was wrong.** I inferred liveness from deployment date. `gemini-5m-collector` has been **running continuously for a month** and is writing right now:

```
GEMINI_COLLECTOR_HEALTH  2026-10-13T13:04:31Z
  bbo_rows 7,476,069   print_rows 51,053   bbo_age_s 0.7
  subscribed 4   known_windows 66   reconnects 4,137   errors 100
  disk_free_gb 45.78   disk_used_pct 6.3   alarm null
```

**Correction 2 — "PMUS data is volume-locked / unmeasurable" was wrong.** `pmus-ws-evidence-recorder` is also live, emitting two-sided depth and trades with quote context:

```
PMUS_BOOK_ROWS  cpc-btc-updown-15m-2026-10-13-1300z
  bid 0.0800  ask 0.0900  bid_qty 2878.63  ask_qty 2083.00  ask_book_missing false
PMUS_TRADE_ROWS px 0.4800 qty 2.8000 bid_at_trade 0.4800 ask_at_trade …
```

**The method correction that matters:** the data is in the **Railway deploy logs**, readable with `get-logs`. No volume mount, no SSH, no pinned CLI, no `l5-volume-export.yml` repair. Your review was right that I mischaracterized that workflow; it is also now unnecessary for this purpose.

---

## 1. Task 1 — Collector audit (liveness, not deploy status)

| Service | Deploy date | **Actually writing?** | Evidence available in logs |
|---|---|---|---|
| `gemini-5m-collector` | Sep 14 | **YES — live now** | 1s-resolution `[tte, bid, ask, bid_depth, ask_depth]` series; prints `[tte, px, qty]`; per-window close stats; full latency chain |
| `pmus-ws-evidence-recorder` | Oct 12 | **YES — live now** | `bid/ask/bid_qty/ask_qty/status`; trades with `bid_at_trade`/`ask_at_trade`; gateway cross-check |
| `pmus-sports-forward-collector` | Oct 11 | Deploy SUCCESS, not sampled here | sports slate — unmeasured |
| `kalshi-l5-shadow` | Sep 15 | Not verified | — |
| Kalshi GH Actions tape | — | Yes, but **5.8 snapshots/day** vs 288 scheduled | depth-20 book, 2 series |
| `l5-exec-shadow`, `l5-live-habitat`, `kalshi-incentive-shadow` | — | **CRASHED** | — |

### `l5-volume-export.yml` — corrected assessment

`workflow_dispatch` only · `SERVICE_ID` **hardcoded** to `pmus-ws-evidence-recorder` · `REMOTE_DIR` default `/state/pmus-ws/raw` · requires `RAILWAY_TOKEN` + `RAILWAY_SSH_PRIVATE_KEY_B64` + pinned CLI `5.63.1` · operations limited to `files list` / `files download` · output is a **7-day `upload-artifact`**, not a committed dataset · fails closed with no fallback.

It is a single-service manual inventory tool. It is **not** an automatic Kalshi/Gemini exporter and should not be converted into one — the log path below supersedes it.

### Smallest safe recovery method (recommended)

**Read the deploy logs.** Already proven in this session: `get-logs` + `jq`/python reconstructs book series, depth, prints and latency with zero write surface and no change to any collector. Retention is the only limit, which argues for a scheduled read-only harvest into `l5-kalshi-data`-style data branches — *not* for touching the live collectors.

---

## 2. Task 2 — The decisive census

### 2a. PMUS `btc-updown-15m` — deep, efficient, and structurally closed to L5-style making

n = 5,182 unique book observations, 2 concurrent windows, 2026-10-13 12:51–13:06Z.

| Metric | Value |
|---|---|
| Spread | **1.00¢ at p25, median, p75 AND p95** |
| Bid depth | median **2,364** contracts (p25 1,217 / p75 3,587) |
| Ask depth | median **2,201** contracts (p25 974 / p75 3,470) |
| Observations ≥4¢ spread | **0.0%** |
| Observations ≥2¢ spread | 1.9% |
| Genuinely crossed (locked arb) | **0** |
| Feed quality | 92% `OK`, 8% `STALE`, 0% `ask_book_missing` |

*(Note: an initial pass reported 4.4% "crossed." That was my test counting `spread == 0`; inspection shows those are `bid == ask == 0.99` — zero-width books pinned at the settlement boundary. There is no PMUS arbitrage.)*

**This is the single most important finding.** L5's measured median paired edge on Polymarket is **3.76¢**. PMUS never offers it: the spread is pinned at 1¢ essentially 100% of the time. A perfect maker capturing the entire touch on both legs earns ~1¢ paired — before absorbing the adverse selection that the round-1 decomposition valued at ~−12¢ per unpaired share. **The L5 mechanism does not transfer to PMUS on price structure alone**, independent of fees, latency or queue position. PMUS is a deep, tight, efficient venue — excellent for taking if you have alpha, closed to spread capture.

### 2b. Gemini BTC/ETH 5m & 15m — the only venue with maker room

n = 501 window-closes (BTC 251 / ETH 250; 5m 382 / 15m 119) over 15.8h → ~750 windows/day.
Prints: 399 market-windows over 29.1h → ~330 windows/day with prints, ~1,400 prints/day, **~49,000 contracts/day venue-wide**.

| Metric | Median | p25 / p75 |
|---|---|---|
| **Quoted spread** | **15¢** | 9¢ / 19¢ |
| Depth at touch | **10** contracts | 10 / 25 |
| Two-sided time | 66.7% | 52% / 73% |
| Observations per minute | 54.5 | 49 / 57 |
| Prints per window | 0 (mean 1.59) | 0 / 2 |
| Print size | 10 contracts | mean 34.9, p95 100, max 555 |
| Contracts per printing window | 44.7 | mean 148 |

Latency chain (our own submit path): `due→subscribe` **52ms**, `subscribe→ack` **3ms**, `ack→first_frame` **97ms**.

**Gemini is the structural inverse of PMUS:** 10–15¢ of spread to work with, but thin touch depth, a third of the time one-sided, and a median of **zero prints per window** — the volume is concentrated in a minority of windows.

### 2c. Gemini maker economics — method proven, result NOT decision-grade

I ran the repo's own `MAKER_PRINT_PROXY` (from `ARB_EDGE_CENSUS_2026-08.md`) against live data: for each print, take the prevailing quote, treat a print at/through the touch as a proxy fill, and mark out to a decisive settlement proxy.

```
proxy maker fills: n=42 across 22 windows, 1,223 contracts
  we_buy  n=17  797 contracts   mean −6.94c/contract   median −15.00c
  we_sell n=25  426 contracts   mean +3.92c/contract   median  +4.00c
  unweighted mean        −0.48c/contract
  size-weighted mean    +21.24c/contract
  median spread at fill   10.0c
```

**I am not reporting this as an edge.** n=42 is far below the pre-committed **n ≥ 200** bar, and the unweighted (−0.48¢) versus size-weighted (+21.24¢) divergence shows one or two large fills dominating — an unstable estimate. Settlement is a proxy (terminal mid), not certified settlement.

What it does establish: the measurement is **feasible today on data already flowing**, and the buy-side sign (−6.94¢) is consistent with the adverse selection seen in round 1 — we get filled buying into windows that then settle against us.

### 2d. Kalshi — unchanged

Fee math stands: taker-taker at P=0.50 costs **3.50¢/contract** against a 3.76¢ reference edge. Maker-maker costs 0.88¢. But per your instruction I do **not** assert maker-maker viability from fees: there is no Kalshi fill evidence, and at 5.8 snapshots/day there cannot be. Kalshi's tape is also snapshot-based and covers only the current and next market, so it cannot establish queue priority or fill probability even at perfect cadence.

---

## 3. Task 3 — Ranked decision table

| Rank | Venue / category | Qualifying opportunities/day | Executable price & depth | Maker/taker economics | Fill evidence | Est. daily net capacity | Gate verdict |
|---|---|---|---|---|---|---|---|
| **1** | **Gemini BTC/ETH 5m+15m** | ~330 printing windows; ~1,400 prints; ~49k contracts | 10–15¢ spread, **10–25 contracts** at touch | Only venue with spread to capture; fees **unverified** | **n=42 proxy only — insufficient** | **$49–$147 est.** (5–10% capture at 2–3¢ net) | **UNMEASURABLE → run the proxy census** |
| 2 | PMUS BTC 15m | 96 windows, continuous | **1¢ spread**, 2,200–2,400 contracts | No spread to capture; 0% of obs ≥4¢ | Live trades w/ quote context | **~$0 for L5-style making** | **ARCHIVE for spread capture** (retain as execution venue) |
| 3 | Kalshi BTC/ETH 15m | 96 windows | depth-20 book, 5.8 obs/day | Taker-taker 3.50¢ kills 93% of ref edge | **None** | — | **UNMEASURABLE** (cadence + no fills) |
| 4 | PMUS sports / sub-4h non-crypto | unknown | collector live, unsampled | — | — | — | **UNMEASURABLE** |
| 5 | Cross-venue locked arb (K↔P) | — | — | — | nine-key lower bound | — | **ARCHIVE** (per capacity gate) |

### Single most promising mechanism for additional shadow validation

**Gemini BTC/ETH short-duration maker quoting.** It is the only venue measured that satisfies the *structural precondition* for the L5 mechanism — 10–15¢ of quoted spread against a 3.76¢ reference edge requirement — while carrying real volume (~49k contracts/day) and a sub-150ms submit path. Everything else either has no spread (PMUS), no data (Kalshi), or a standing negative verdict (cross-venue).

**It has not cleared the gate.** Estimated capacity straddles $50–$147/day, and that estimate rests on assumed capture rates, not fills.

### If nothing clears the gate — the precise missing measurement

Nothing clears it. The missing measurement is exactly one thing:

> **Gemini maker fill probability and settlement markout at n ≥ 200, with certified settlement and verified Gemini fee schedule** — queue position, cancel/replace behaviour, and realized adverse selection by time-to-expiry.

Two named unknowns block a numeric verdict: **(a)** Gemini's fee schedule for these contracts is not established in this repository and must be measured, not assumed; **(b)** touch depth of 10–25 contracts caps per-window size regardless of edge, so capacity is depth-bound rather than edge-bound.

---

## 4. Summary

**What is truly running:** two live high-frequency collectors (Gemini, PMUS-WS) writing book+trade evidence into Railway logs; the Polymarket wallet tape (42 days, decision-grade); a sparse Kalshi snapshot tape at 5.8/day; three crashed services. Gemini and PMUS were both misreported as dark in round 1.

**What changed:** no infrastructure, no deployments, no services, no spend, no credentials, no thresholds. One markdown file added on this research branch. The *access path* changed — Railway logs replace the volume-export route entirely.

**Biggest blocker to realized profitability:** we still have no venue with both (a) enough quoted spread to support the L5 mechanism and (b) demonstrated fill economics. PMUS has the liquidity but structurally lacks the spread; Gemini has the spread but unproven fills and depth-bound size. This is now a *specific, measurable* gap rather than a general data outage.

**Next concrete action:** run the `MAKER_PRINT_PROXY` census on Gemini to n ≥ 200 fills using the live log feed — joining `GEMINI_WINDOW_SERIES` with `GEMINI_WINDOW_PRINTS` per market-window, marking out to certified settlement, stratified by time-to-expiry — and establish the Gemini fee schedule from primary venue documentation. Report against the unchanged $50 / $150 bands. This requires no new service, no deployment and no trading authorization.

---

## Appendix — Handoff to the L5 replication session (informational only)

No code, threshold, branch or service change is requested. Venue mechanics only.

1. **PMUS spread structure.** `cpc-btc-updown-15m` quotes a **1¢ spread at p25 through p95** with 2,200–2,400 contracts at touch (n=5,182). Any replication assuming Polymarket-like spread capture on PMUS should be re-examined against this.
2. **Polymarket economics do not transfer.** The 3.76¢ median paired edge requires spread PMUS does not offer. Fees aside, this is a price-structure limit.
3. **Adverse selection has an independent second observation.** Round 1 implied ~−12¢ per unpaired share on Polymarket; the Gemini buy-side proxy independently shows **−6.94¢** mean on proxy buy fills. Two venues, same sign: fills arriving on the side about to be wrong. Directionally supportive, neither decision-grade.
4. **Feed quality to reconcile.** PMUS book rows carry `status` (8% `STALE`) and `ask_book_missing`. Any fill or inventory assumption read from this feed should exclude `STALE` rows.
5. **Latency is not the binding constraint.** Measured submit path: due→subscribe 52ms, ack 3ms, first frame 97ms.
6. **Caveat.** Round-1 figures describe the observed Polymarket wallet, not our realized trading. The 78% residual decomposition is accounting, not a proven causal loss mechanism or recoverable profit. Audit settlement, rebate and event-level reconciliation before treating them as independently verified.
