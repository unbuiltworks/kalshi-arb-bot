# L5 Round 4 — Gemini Settlement Access Test

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues `40618c35`
**Mode:** read-only. Railway/GitHub reads, local analysis. No orders, deploys, restarts, variables, services, spend. No workflow dispatched. Gate unchanged.

**Headline:** your hypothesis is **not disproven** — the events endpoint has never been probed. But I **cannot execute the test from this container**: every `gemini.com` host is denied by the environment's network policy. Below: what prior evidence actually covered, a material correction to my own fee numbers from your rounding rules, the 10 tickers staged, and the one unblock needed.

---

## 1. Two corrections to my round-3 work (both yours, both accepted)

### 1a. The terminal-quote requirement was my error

You are right. For a hold-to-settlement valuation, a resting maker fill earlier in the window needs **no** two-sided quote near expiry. I imposed `tte ≤ 5s` *only* to manufacture a settlement proxy, then reported the resulting n=12 as if the data were inadequate. The data were not inadequate for the question — **my estimator was mis-specified**. With an official outcome, the terminal-quote filter disappears and the qualifying set grows from n=12 toward the full print population (3,879 unique prints across 864 print-windows already harvested).

### 1b. "22 days apart" is not out-of-sample — also correct

I called the two blocks "genuinely out-of-sample." That was wrong. Chronological separation alone is not a holdout: out-of-sample requires **rules and thresholds fixed on one period and evaluated unchanged on the other**. I fixed nothing in advance, so both blocks were in-sample exploration. Any future census must pre-register the quote-freshness bound, maker/taker classification, fill-rate assumption and price-band handling on a discovery period and then run them untouched on a holdout.

---

## 2. What prior evidence actually established — and what it did not

Stored artifacts on `claude/l5-execution-instrumentation-io6nzi` (`analysis/gemini/settlement_gate_20260917T*.json`, three runs) record a real authenticated probe from 2026-09-17:

| Endpoint probed | Status | Result |
|---|---|---|
| `/v1/balances` | 200 | reachable |
| `/v1/transfers` | 200 | 9 rows |
| `/v1/notionalvolume` | 200 | returns `api_maker_fee_bps`, `api_taker_fee_bps` — **our account's own fee tier** |
| `/v1/prediction-markets/settlements` | **404** | does not exist |
| `/v1/settlements` | **404** | does not exist |
| `/v1/prediction-markets/positions` | 200 | **0 rows** |
| `/v1/mytrades` (per symbol) | 200 | **0 rows** |

Proof chain outcome: `first_break: "authoritative_outcome"`, `complete: false`.

**The critical observation: `GET /v1/prediction-markets/events/{eventTicker}` was never probed.** The prior run tested two non-existent settlement paths and two *account-scoped* surfaces — and `positions`/`mytrades` were empty **because we hold no positions and have made no trades**, which is not evidence about public market metadata.

So the workflow header's claim — *"the venue publishes NO terminal value anywhere public… Only the authenticated account surface can close it"* — **overreaches its own evidence**. It was measured against a settlement-endpoint and account-position surface, not against the event resolution fields. Your reading is the better-supported one and the test is still open.

One further point in your favour: `resolutionSide` / `settlementValue` / `sourceDetails` are *market metadata*, which on Gemini's prediction-market API is typically **public and unauthenticated**. If so, no credential is required — only network reachability.

---

## 3. Why I could not run the test

### 3a. All Gemini hosts are denied from this container

```
api.gemini.com         HTTP 000   (CONNECT refused)
developer.gemini.com   HTTP 000
www.gemini.com         HTTP 000
exchange.gemini.com    HTTP 000
proxy status: {"kind":"connect_rejected",
               "detail":"gateway answered 403 to CONNECT (policy denial)",
               "host":"www.gemini.com:443"}
```

This is the environment's network policy, **not** a historical-retention problem. To be explicit about the distinction you asked for: **the API fields are not shown to be unavailable historically — they are untested, because this container cannot reach the host.** I am not reporting a negative result; I am reporting a blocked test.

The pages are reachable from your machine, which is why your independent verification succeeded where mine fell back to search.

### 3b. I deliberately did not dispatch `gemini-settlement-gate.yml`

It is the obvious existing path and I am declining it on safety grounds. Although titled read-only, its 140 runs show it has been repurposed as the **live canary execution harness**: recent commits read *"CI15M-4: arm the workflow at the final execution SHA"*, it carries authorization constants (`OWNER-2026-10-03-GEMINI-CI15M-4`), cohort bounds (*"2 windows / 6 orders / 3 per window"*), a frozen strategy fingerprint, and run logs describing **real submitted orders** (*"FILLED q1 @ 0.68 … settled worthless"*). Dispatching it could submit live orders.

That is squarely outside a read-only discovery mandate, so I did not run it and would not without your explicit instruction. This is also why "use existing infrastructure" does not resolve here: the existing Gemini path is no longer read-only.

---

## 4. Material correction: transaction-level rounding changes the fee verdict

Your rounding rules — **fee up to the next cent, rebate down to the next cent, per transaction** — matter more than I allowed. Round 3 quoted a flat "+0.53¢/contract." That is only true at size.

At P=0.50, in-band, by transaction size C:

| C | Maker fee | Rebate | Post-promo net | Post-promo ¢/contract | **Promo ¢/contract** |
|---:|---:|---:|---:|---:|---:|
| 1 | $0.01 | $0.00 | −$0.01 | **−1.000¢** | +0.000¢ |
| 2 | $0.01 | $0.01 | $0.00 | 0.000¢ | +0.500¢ |
| 5 | $0.03 | $0.02 | −$0.01 | **−0.200¢** | +0.400¢ |
| **10** (median print) | $0.05 | $0.05 | $0.00 | **0.000¢** | **+0.500¢** |
| **25** (BTC 5m depth) | $0.11 | $0.13 | +$0.02 | +0.080¢ | +0.520¢ |
| 100 | $0.44 | $0.52 | +$0.08 | +0.080¢ | +0.520¢ |
| 555 (max observed) | $2.43 | $2.91 | +$0.48 | +0.086¢ | +0.524¢ |

Out of band (P=0.85): rebate is zero — **−0.300¢/contract** at C=10 post-promo, 0¢ during the promo.

**Two revisions to round 3:**

1. **Single-contract executions are fee-negative, not fee-neutral.** At C=1 the fee rounds up to a full cent while the rebate rounds down to zero: **−1.0¢/contract**, which alone exceeds plausible edge. Any quoting policy must avoid 1-contract transactions.
2. **The rebate advantage is temporary and ≈ cancels the fee after 2026-12-31.** Post-promo in-band net is **0.000¢ at C=10** and **+0.080¢ at C=25** — effectively zero. The "+0.53¢" tailwind exists *only* while the Maker Fee-Free promo runs (to **2026-12-31**, ~11 weeks). From January the lane must stand on spread capture alone.

So my round-3 framing ("fees are favourable") needs narrowing: **fees are favourable only during the promo and only at transaction sizes ≥ ~10 contracts, in-band.** Account tier is still unverified, and `/v1/notionalvolume` — already proven to answer 200 — is the exact read that settles it.

---

## 5. Staged test — ready to run the moment access exists

Ten historical tickers from retained logs, selected by print count across both blocks (the per-cell split you asked for):

```
GEMI-BTC05M2610130830-UP   BTC  5m  prints=15   expiry_t=1791880200
GEMI-BTC05M2610130340-UP   BTC  5m  prints=14   expiry_t=1791862800
GEMI-BTC05M2610130620-UP   BTC  5m  prints=10   expiry_t=1791872400
GEMI-BTC15M2610130715-UP   BTC 15m  prints=31   expiry_t=1791875700
GEMI-BTC15M2610130615-UP   BTC 15m  prints=23   expiry_t=1791872100
GEMI-BTC15M2610130700-UP   BTC 15m  prints=21   expiry_t=1791874800
GEMI-ETH05M2610130340-UP   ETH  5m  prints=11   expiry_t=1791862800
GEMI-ETH05M2610130320-UP   ETH  5m  prints= 6   expiry_t=1791861600
GEMI-ETH05M2610130820-UP   ETH  5m  prints= 6   expiry_t=1791879600
GEMI-ETH15M2610130315-UP   ETH 15m  prints= 7   expiry_t=1791861300
```

Note these are **market** symbols (`…-UP`); the **event** ticker likely drops the side suffix. Confirming that mapping is part of the "exact contract identity" check — a wrong ticker form would produce a 404 that must not be misread as absent retention.

Data already in hand to join against: **252 series windows, 864 print windows, 3,879 unique prints, 44,005 unique quote observations**, across Sep 21 and Oct 13 blocks. With the terminal-quote filter removed (§1a), that is plausibly enough for n ≥ 200 **without** a volume inventory — which is why I have not touched the volume, per your instruction.

---

## 6. Status and next action

| Item | State |
|---|---|
| Gemini fee/rebate **rates** | Verified (yours, plus my search corroboration) |
| Transaction-level rounding | **Now modelled** — changes the verdict at small size and after Dec 31 |
| Our account's fee tier / eligibility | **Unverified.** `/v1/notionalvolume` answers 200 and is the exact read |
| Official historical resolution fields | **UNTESTED** — never probed; container cannot reach the host |
| Maker fill probability / queue priority | **Unproven**, unchanged. A resolved outcome validates payout, not fill |
| Gemini capacity verdict | **UNMEASURABLE_ON_CURRENT_RIG**, unchanged. Gate applied unchanged |

**The single unblock — one setting, not infrastructure:** add **`api.gemini.com`** (and `developer.gemini.com` for the spec) under **Allowed domains** in this environment's Network access settings, keeping "Allow package managers" ticked: https://code.claude.com/docs/en/cloud-environments#network-access

With that, in one read-only pass and no new component, I can: probe the ten tickers for populated `resolutionSide` / `settlementValue` / `strike` / `resolvedAt` / `sourceDetails`; confirm the event-vs-market ticker mapping; join official outcomes to the existing prints with strict quote-before-print causality; pre-register rules on the Sep 21 block and evaluate unchanged on Oct 13; and report per-cell markouts for BTC 5m / BTC 15m / ETH 5m / ETH 15m under conservative 0–30% fill bounds, with promo and post-promo fee scenarios.

**If the allowance is declined**, the alternative is a *new* minimal read-only public-GET workflow — which I have not created, because you said not to begin with new infrastructure and because the existing Gemini workflow is unsafe to reuse (§3b). That needs your explicit go-ahead.

**Unchanged assessment:** the binding constraint remains maker execution economics — fill probability and adverse selection — not more quotes. Settlement access makes that constraint *measurable*; it does not resolve it. If Gemini still fails after the join, the strongest next candidate remains **PMUS sports / sub-4h non-crypto breadth** via the live `pmus-sports-forward-collector` — the only large universe with a live collector and no economic measurement.
