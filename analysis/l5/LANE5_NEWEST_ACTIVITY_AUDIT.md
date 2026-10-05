# Lane 5 — Newest available L5 activity vs the established baseline

**Status:** historical replay / observational audit. RESEARCH ONLY — no live orders, no venue writes.
**Subject:** Polymarket trader `l5zn1bwom8etsk` (display `l5Zn1bWoM8eTsK`), address `0xb945945d...2db68`.
**Generated:** 2026-10-05. Script: `scripts/l5_lane5_newest_activity_audit.py`. Machine output: `analysis/l5/lane5_newest_activity_audit.json`.

## Profit attribution

**NONE.** Every number here is another trader's public activity. No figure in this document is
realized, filled, submitted or settled profit for our account. The `REDEEM`, `REWARD`,
`MAKER_REBATE` and `TAKER_REBATE` records below are L5's own settlement and incentive
cashflows observed in the public feed; they are not evidence that equivalent incentives apply
to our account, and per the operating model they stay locked until source-confirmed for us.

## 1. Fresh pull: FAILED — proxy denies the venue hosts

The existing canonical collector was used, not a new one:
`polymarket_l5.cli fetch` → `polymarket_l5.fetch` → `polymarket_l5.cache.RawCache`
(envelope `url/params/status/payload/fetched_at/tag`).

It was run at 2026-10-05T21:47Z into a fresh raw dir (`data/l5/raw_2026_10_05_attempt/`) so the
failure is recorded in the real provenance envelope rather than only in a log:

```
status 0, error "transport error: Tunnel connection failed: 403 Forbidden"
```

| Host | Port | Result |
|---|---|---|
| `data-api.polymarket.com` | 443 | proxy `HTTP/1.1 403 Forbidden` to `CONNECT` |
| `gamma-api.polymarket.com` | 443 | proxy `HTTP/1.1 403 Forbidden` to `CONNECT` |
| `clob.polymarket.com` | 443 | proxy `HTTP/1.1 403 Forbidden` to `CONNECT` |

`$HTTPS_PROXY/__agentproxy/status` lists all three under `recentRelayFailures` as
`connect_rejected — "gateway answered 403 to CONNECT (policy denial or upstream failure)"`.
TLS verification was not disabled and `HTTPS_PROXY` was not unset.

**So: no new data was collected today.** What follows re-analyses the EXISTING corpus. The newest
records in it are 2026-09-25T16:02Z.

## 2. The baseline was computed on ~2% of the corpus — and on the wrong filter

The established baseline cites **312 events / 9,901 deduplicated fills**, filtering payload records
on `x['name'] == 'l5Zn1bWoM8eTsK'`.

That filter is unsound. `data/l5/raw/activity_gap.jsonl.gz` holds **706,500 payload records** whose
envelope `params.user` is L5's own address, but whose records carry **no `name`, `proxyWallet` or
`bio` field at all** — the `/activity` feed omits the profile block on those pages. A name-only
filter discards every one of them, which is exactly why the baseline has no May, June or July data.

This audit takes identity from **provenance** instead: a response fetched with `params.user =`
L5's address contains L5's activity, and a *contradicting* `name` is what disqualifies a record.
`REDEEM` / `REWARD` / `MAKER_REBATE` / `TAKER_REBATE` records are separated out as cashflows and
excluded from the fill table, so the fill counts stay comparable with the TRADE-only Sep harvest.

| | baseline | this audit |
|---|---|---|
| events | 312 | **17,855** |
| deduplicated fills | 9,901 | **754,234** |
| date range | (Aug-era subset) | **2026-03-19T10:09Z → 2026-09-25T16:02Z** |

Dedupe key is the baseline's own: `(transactionHash, asset, timestamp)`.

### Provenance of the fill table

| source | envelope rows | statuses | fetched | own records (provenance id) | own records (name filter only) |
|---|---|---|---|---|---|
| `activity.jsonl` | 157 | 156×200, 1×400 | 2026-08-30/31 | 78,000 | 78,000 |
| `activity_gap.jsonl.gz` | 1,413 | 1,413×200 | 2026-08-31 | **706,500** | **0** |
| `trades.jsonl` | 22 | 21×200, 1×400 | 2026-08-30 | 10,500 | 10,500 |
| `trades_takeronly.jsonl` | 27 | 27×200 | 2026-08-30/31 | 13,500 | 13,500 |
| `sep/l5_sep23_25_daily.json` | n/a (harvest script) | n/a | ~2026-09-25 | 22,512 TRADE | 22,512 |

## 3. Headline: the mechanics are REAL but DRIFTING, and the baseline is an August snapshot

All figures per period, with event counts. Nothing is pooled across periods.

| period | days | events | fills | 1st-acq price (med) | % favored | T+ into window (med s) | % complemented | time→complement (med s) | EXTENDs (med) | per-fill size (med) |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-03 | 03-19→03-31 | 88 | 1,931 | 0.495 | 45.5% | 72 | 83.0% | 48 | 1 | 10.0 |
| 2026-04 | 04-01→04-30 | 548 | 37,364 | 0.49 | 42.0% | 36 | **97.6%** | 42 | **3** | 16.1 |
| 2026-05 | 05-01→05-29 | 877 | 85,194 | 0.50 | 44.9% | 40 | **99.2%** | 29 | 2 | 18.0 |
| 2026-06 | 06-01→06-30 | 1,031 | 74,645 | 0.51 | 53.2% | 43 | 97.1% | 33 | 2 | 20.0 |
| 2026-07 | 07-01→07-31 | 3,666 | 126,779 | 0.54 | 61.3% | 65 | 70.3% | 52 | 1 | 17.7 |
| 2026-08 | 08-01→08-31 | 10,794 | 406,489 | 0.55 | 62.6% | 37 | 73.0% | 54 | 1 | 11.7 |
| **2026-09** | 09-23→09-25 | **851** | **21,832** | **0.59** | **66.9%** | **104** | **43.5%** | **75** | **0** | 16.0 |

**The baseline numbers (0.588 median, 69.4% favored, T+44, 71.5% complement, 52s to complement,
median 1 EXTEND, median 6 own-fills) land squarely on the Jul–Aug regime.** They are not a
description of L5's policy in general, and they are *not* what the newest data shows.

### Which baseline conclusions HOLD

- **Incremental same-side accumulation is real and present in every period.** `EXTEND` is a
  first-class action throughout: 46.9%–64.8% of events carry at least one same-side EXTEND, with
  monthly medians of 0–3 and maxima of 7–42.
- **Pair completion is still the dominant resolution mechanism**, and completion is still *patient*
  rather than immediate (median 29–75s from first fill to first matched increase).
- **Favored-side bias at first acquisition is real and strengthening** — 42% favored in April,
  66.9% in September, a monotone rise across all seven periods.
- **First acquisition is seconds-to-minutes into the window, not at the open** (median 36–104s).
- **Frequency-before-size holds** in L5's own history: events/month went 88 → 10,794 while median
  per-fill size stayed in a 10–20 band. This is direct external support for the operating model's
  `PATIENCE → FREQUENCY → SIZE → CONCURRENCY` ordering.

### Which baseline conclusions have CHANGED

1. **Completion rate is not ~71.5% — it is regime-dependent and has collapsed.** 97–99% in
   Apr–Jun, 70–73% in Jul–Aug, **43.5% in September** (Sep 23 alone, the one complete day:
   44.8% on 326 events / 9,372 fills). Over half of September events are now opened and never
   complemented. Treating 71.5% as a planning constant would materially misprice our clone.
2. **Median EXTENDs before complementing has gone to 0** in September (mean 1.53, p90 4) from
   a median of 3 in April. The distribution is bimodal, not shifted: 46.9% of Sep events still
   EXTEND, but the modal event is now a single un-extended, un-complemented fill.
3. **Fills per event have collapsed**: median 21 (Mar) → 19 (Apr) → 6 (Aug) → **2 (Sep)**, while
   p90 stays high (87–136). L5 runs many more, much shallower events.
4. **First acquisition is later into the window** — median T+104s in Sep vs T+36–43s in Apr–Jun.
5. **Pair-completion cost is NOT at the spring's `<= 0.96` target.** Median completed pair cost
   is 1.00 (Mar), 0.97 (Apr), **0.99 (Aug and Sep)**, mean 0.99–1.00, p90 1.27–1.31 — i.e. a
   large fraction of L5's completions are *above* $1 before fees. This is the near-$1 pair
   tolerance of incentive-era L5. Per `CLAUDE.md` it stays **locked** for us: it only works
   alongside the rebate/reward economics shown in §5, which are not confirmed for our account.

## 4. Category and duration selection — expanded, still crypto up/down

Event counts by `eventSlug` prefix:

| period | btc-updown-15m | btc-updown-5m | eth-updown-15m | eth-updown-5m |
|---|---|---|---|---|
| 2026-03 | 86 | 2 | — | — |
| 2026-04 | 548 | — | — | — |
| 2026-05 | 876 | — | — | — |
| 2026-06 | 667 | 364 | — | — |
| 2026-07 | 1,171 | 2,495 | — | — |
| 2026-08 | 2,155 | 5,228 | 580 | 2,831 |
| 2026-09 | 232 | 392 | 84 | 143 |

- L5 has **moved off 15m as its primary duration**. 5m overtook 15m in July (2,495 vs 1,171) and
  is 63% of September events.
- **ETH was added in August** and is 27% of September events. So the subject did diversify
  underlying — but entirely **within crypto up/down**. Across 17,855 events there is **not one**
  esports, sports, politics, economics or weather market. This does **not** license narrowing our
  engine to crypto (the market-agnostic requirement stands), but it does mean the *behavioral*
  evidence base is crypto-up/down only, and any transfer to another category is extrapolation.

## 5. Mechanics the baseline MISSED

1. **L5 is effectively buy-only.** Across 754,234 fills there are **2 SELL fills, both in March,
   and zero since**. Inventory is never unwound by selling. Exposure is resolved only by (a) buying
   the opposite outcome and (b) `REDEEM` at settlement. The baseline's "side-switch" framing has no
   support in the tape: there is no switching, only complementing. A clone that plans to flatten by
   selling is not replicating this policy.

2. **A hard daily operating session, which later dissolved.** Event share by UTC hour shows
   Apr–Jun activity confined to roughly **06:00–19:00 UTC with a near-zero 00:00–06:00 block**
   (April: 0% of events in hours 0–5). By August the distribution is **flat at 3–4% per hour across
   all 24**, i.e. continuous operation. This looks like a human-supervised session becoming
   automated between June and August, and it is why dozens of Apr–Jun days show "missing hours" in
   the coverage table — those are genuine idle periods, not collection gaps.

3. **A descending size ladder within each event.** Median buy size by ordinal within an event:
   Jun 30.0 → 29.3 → 25.0 → 27.6 → 25.5 → 21.3; Sep **37.0 → 28.2 → 28.2 → 20.0 → 19.5 → 13.3**.
   The first clip is the largest and subsequent EXTENDs get smaller. The baseline's single
   "per-fill size" number hides this, and the taper is steepening over time.

4. **Heavy size quantization on round lots.** Sizes concentrate on 2 / 5 / 6 / 10 / 15 / 20 / 25 /
   30 — e.g. 26,457 fills at exactly 5.0 in August. Plus **10,691 August fills at size < 0.05**
   ("0.0" bucket): dust partial fills, consistent with resting maker orders being nibbled.

5. **Low concurrency, and it is a deliberate ceiling.** Max simultaneously-open events is
   **1 (Mar), 2 (Apr–Jul), 4 (Aug), 3 (Sep)** — despite 10,794 events in August. L5 scaled
   frequency ~120× while holding concurrency to ≤4. Strong external support for keeping our live
   probe at one market and promoting concurrency last.

6. **Settlement and incentive cashflows are visible, and are large.** Median `usdcSize` per record:

   | period | REDEEM (n, med, max) | MAKER_REBATE (n, med) | TAKER_REBATE (n, med) | REWARD (n, med) |
   |---|---|---|---|---|
   | 2026-04 | 848, 254.09, 2,658.97 | 11, 77.79 | — | — |
   | 2026-06 | 1,021, 572.44, 2,350.14 | 21, 77.24 | 18, 13.74 | — |
   | 2026-07 | 3,353, 186.92, 2,909.36 | 23, 98.12 | 27, 77.35 | — |
   | 2026-08 | 9,759, 115.98, 9,298.01 | 28, **1,161.04** | 28, 114.76 | 20, **1,630.07** |
   | 2026-09 | 657, 80.00, 7,147.59 | 3, 969.57 | 3, 53.96 | — |

   Maker rebates jumped ~12× in August (median 98 → 1,161 per record) and `REWARD` records appear
   for the first time. **The August–September regime change in trading behavior coincides exactly
   with the arrival of large incentive cashflows.** That is the most plausible explanation for the
   near-$1 pair tolerance and the collapsing completion rate: L5 is being paid to rest liquidity,
   so an uncompleted resting fill is no longer a loss to it. We are not. This is the single
   strongest reason the September behavior must **not** be cloned on our account.

7. **Maker/taker cannot be resolved from this corpus.** The `/activity` feed has no liquidity flag.
   Cross-referencing the taker-only endpoint over Aug 28–31 matches 11,810 of 69,899 fills, giving
   a **16.9% taker-share lower bound only** — that feed is itself offset-capped and partial, so
   every fill it missed is miscounted as maker. Reported as a floor, not an estimate.

## 6. Coverage caveats

- **September is 2.6 days, not 3**, and it is not from the canonical collector — it comes from
  `scripts/l5_harvest_sep_activity.py`, which pages by raw `offset` (capped near 5,000 by the API)
  with day-bounded `start`/`end`. Verified holes: **2026-09-24 has no fills in hours 02–06 UTC**
  and **2026-09-25 stops at 16:02** (harvest run time).
- The September findings therefore rest primarily on **2026-09-23, which is complete**
  (00:00:07 → 23:56:08, 326 events, 9,372 fills) and which reproduces the drop on its own
  (44.8% complemented, median 2 fills/event, median 0 EXTENDs, T+114s).
- Mar–Jul are reconstructed from the windowed-DESC collector and are dense but not provably
  gapless; Aug is the densest period.

## 7. Verdict

The mechanics still hold **in kind** — incremental same-side accumulation, patient complementing,
favored-side first acquisition, frequency-before-size, low concurrency — but **not in parameter**.
Every headline baseline constant has drifted monotonically across seven periods, and the newest
period is the furthest from the baseline on completion rate, EXTEND count, fills per event and
entry timing.

Operationally:

- The baseline should be relabelled as **"Jul–Aug 2026 regime"**, not "L5's policy".
- Conclusions for **May, June and July are genuinely NEW** here — the baseline had no data for
  those months because of the `name`-filter defect, not because the data was absent.
- The **September conclusions are drawn from 2.6 days of partial, non-canonical collection** and
  should be treated as a signal to re-pull, not as a settled finding. A fresh canonical pull is
  required and is currently **blocked by the proxy** (§1).
- The September regime correlates with large maker rebates and rewards that are **not confirmed for
  our account**, so it stays locked under the existing rebate discipline.

