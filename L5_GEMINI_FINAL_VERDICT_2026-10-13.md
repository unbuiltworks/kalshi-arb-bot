# L5 Round 6 — Gemini Final Verdict

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues `7a8717ab`
**Estimator:** `a163c201`, frozen and **re-run unchanged**. **Mode:** read-only; no orders, deploys, services, spend; **no workflow dispatched**. Gate applied unchanged.

---

# DECISION: **STOP**

Stop dedicated Gemini strategy investigation. Proceed to PMUS sports / sub-four-hour discovery using the existing live collector.

Both of your stop conditions are met independently:

1. **Sell-side execution is not established** — and Gemini's own order schema indicates it is not the native action.
2. **The subset we are confident *is* executable (maker buys) has negative expectancy**: −0.36¢/contract gross, **−0.78¢ after fees**, median **−20.00¢**, and a cluster bootstrap putting **P(true mean ≤ 0) = 52.1%**.

---

## Check 1 — `we_sell` execution semantics: NOT executable as modelled

Evidence from the repository's own live-trading code and our harvested data:

| Evidence | Finding |
|---|---|
| Order payload schema | `clientOrderId, symbol, side, outcome, orderType, timeInForce, price` — carries **both** `side` **and** `outcome` |
| `outcome` values in code | `outcome == "UP"`, `outcome == "DOWN"`; `NATIVE_SIDE = "UP"` |
| Inventory tracking | `nup, ndn = (up + qty, dn) if outcome == "Up" else (up, dn + qty)` — **separate UP and DOWN inventories** |
| The live canary's actual order | `side=payload.spec.SIDE_BUY` — **buy only** |
| Harvested market symbols | **2,692 of 2,692 are `-UP`**; no `-DOWN` symbol was ever observed |

Gemini prediction markets are **buy-the-outcome** instruments, like Kalshi and Polymarket. "Rest a SELL of UP at the ask" is therefore **not the native action**. An uncovered short of UP is not demonstrated to be permitted, and the frozen proxy credited 29 of 50 fills — and essentially all of the profit — to exactly that action.

**The complement route and why it does not rescue the result.** The economically equivalent native action is a resting **BUY of DOWN at `1 − A`**, which yields identical arithmetic (`P&L = A − payout_UP`) and, because `P(1−P)` and the $0.20–$0.80 rebate band are both symmetric about 0.50, **identical fees and rebates**. So the arithmetic is defensible *in principle*. But it is not verified, and two things are missing:

- **The DOWN book was never observed.** Our collector subscribed only to UP symbols, so DOWN-side depth and queue were never recorded. The depth cap I applied (`ask_qty`) is the correct aggregate size at that level, but queue position within it is unknown.
- **Whether `side: sell` on UP is permitted at all is unresolved.** `api.gemini.com` remained denied throughout (HTTP 000, re-tested this round), so I could not check the venue.

Per your instruction — *we cannot award the strategy profits from positions it could not have established* — the `we_sell` results are reported below but **excluded from the verdict**. The verdict rests on maker buys.

## Check 2 — Join and missingness reconciliation

```
series windows                        687
print windows                        2,127
joinable (series & print)              317
  in settlement set                     17
  decisive (resolutionSide yes/no)      17
  MISSING official resolution          300
duplicate event tickers among joinable   0
joinable windows with null resolution    0   (excluded, never imputed)
settlement records total               201;  matched to a joinable window 17
payout-direction sanity (last quote ≤120s vs resolutionSide): agree 11, disagree 1
```

**Disclosure on reproducibility:** this reconciliation reports 317 joinable windows against the 440 stated in round 5. The difference is cached log-file availability between runs and affects **only windows with no settlement**, which never enter the estimator. The qualifying fill set reproduced **exactly**: n=50 across 16 independent markets, identical P&L. The 1 payout-direction disagreement is disclosed as a data-quality flag; it is a legitimate outcome (a market priced wrong at 120s) but could also indicate a mapping error in one window.

**Selection mechanism, stated plainly:** the 17 resolved windows are not a random sample. They are the intersection of (a) what prior sessions happened to store as settlement evidence and (b) what Railway log retention still holds (~Sep 20 onward). This is a convenience sample, and that alone caps the strength of any conclusion drawn from it.

## The bounded recovery pass could not run

`api.gemini.com` → **HTTP 000**, proxy reporting 403 policy denial on CONNECT, re-tested this round. The 300 missing resolutions could not be recovered. I did not dispatch any workflow, and did not touch a collector or the volume.

**My recommendation is that this recovery is no longer the right next step.** It is only worth running *after* sell-side executability is resolved, because without executable sells the measurable strategy is buy-only — and buy-only is already negative. More windows would refine a negative number.

## Results — frozen estimator, every requested cut

### The executable subset: BUY-ONLY (pooled)

```
fills=21   independent markets=10   contracts=1,664
GROSS                      -$5.99     -0.36c/ct
NO PROMO / NO REBATE      -$12.90     -0.78c/ct     <- most conservative
  median per fill                     -20.00c
EX-BEST MARKET           -$261.60    -22.70c/ct
EX-LARGEST FILL          +$182.38    +15.44c/ct
EX-BOTH                   -$73.23    -10.94c/ct
CLUSTER BOOTSTRAP (resample markets, n=10): 95% CI [-59.69c, +49.11c]  median -0.81c
  P(true mean <= 0) = 52.1%
```

**This is the verdict-bearing number. It is negative, its median is −20¢, and it is a coin flip whether the true mean is positive at all.**

### For completeness: including the unverified sells

| Sample | Fills | Mkts | Gross | No promo/rebate | Ex-best market | Ex-both | 95% CI (cluster) | P(mean≤0) |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| Discovery Sep 21–22 | 33 | 11 | +21.13¢ | +20.69¢ | **−2.31¢** | **−2.31¢** | [−33.77, +49.92] | 20.7% |
| Holdout Sep 23/Oct 3–4 | 17 | 5 | +2.82¢ | +2.43¢ | **−53.01¢** | **−33.18¢** | [−66.74, +47.27] | 39.2% |
| Pooled | 50 | 16 | +14.41¢ | +13.99¢ | **+0.31¢** | **−0.11¢** | [−36.00, +46.54] | 25.4% |

Side split (pooled): `we_buy` −0.36¢ gross / −0.78¢ net, median −20.00¢ · `we_sell` +40.80¢ gross, median +36.00¢.

**Every concentration-robust cut is at or below zero.** Removing one market takes discovery from +21.13¢ to −2.31¢, the holdout from +2.82¢ to −53.01¢, and the pooled result to +0.31¢ gross / −0.11¢ net. ETH 5m and ETH 15m produced **zero** qualifying fills in any period.

### Fill and queue sensitivity

Proxy fills are **not** a realizable scenario and **not** an upper bound on profit — a point I accept and will not restate as one. They are an upper bound on *fill count* only: each assumes our resting order was at the front of an unobserved queue. Scaling matters only if the per-contract number is positive; at −0.78¢ for buy-only, **every fill rate from 0% to 100% yields ≤ $0/day**, and higher fill rates make it worse. The zero-fill scenario is $0/day.

### Exposure, collateral, capital efficiency

Positions are fully prepaid binary contracts — no leverage, no margin call, no borrow. Capital per fill is `price × contracts`; across all 50 fills roughly **$1,300 total**, ~$80 per window, recycling every 5–15 minutes. Maximum loss is the premium paid. **Capital was never the constraint on this lane** — capital efficiency is excellent and irrelevant, because expectancy is negative.

### Daily net capacity

**Not assignable.** A bound, not an estimate: with buy-only expectancy at −0.78¢/contract after fees and a 52% probability the true mean is ≤ 0, the conservative bound on daily net capacity is **≤ $0/day**. The lane does not reach $50/day, let alone $150/day. Including the unverified sells and taking the pooled figure ex-best-market gives +0.31¢ gross → under 10% capture of ~49,000 contracts/day ≈ **$15/day**, still far below the archive threshold.

## Gate application — unchanged

`ARB_MECHANISM_CAPACITY_GATE.md`, thresholds untouched: **< $50/day ⇒ archive / end dedicated engineering.** Gemini falls in that band on the executable subset. This is now a measured **STOP**, not an `UNMEASURABLE`: we have official settlement, a frozen estimator, a demonstrated end-to-end join, and the answer is that the executable part is negative.

Preserved as independently useful, per the gate's wording: the Gemini collector (low cost, running), the official-settlement recovery method, and the frozen estimator — all reusable without further Gemini strategy engineering.

## Next: PMUS sports / sub-four-hour discovery

Proceeding there, using the already-live `pmus-sports-forward-collector` (volume `/state/pmus-sports`, deployed Oct 11). It is the only large universe with a live collector and no economic measurement — historically 1,502 markets tracked. No new infrastructure, no spend.

Three transferable lessons, which I will apply there from the start rather than discover again:

1. **Establish executability before measuring economics.** Four rounds of Gemini P&L were dominated by an action the venue may not support. The first PMUS question is what order types and sides the venue actually accepts.
2. **Report concentration-robust statistics first.** A mean over 5–16 windows is not evidence. Ex-best-market and cluster bootstraps should be in the first pass, not the sixth.
3. **Check what the feed cannot see.** The Gemini series silently contained only two-sided observations; a third of each window was invisible. I will characterise PMUS feed coverage before interpreting it.

## Handoff to the L5 replication session (informational only; no change requested)

Gemini has accessible official settlement evidence (`resolutionSide`, `settlementValue`, `resolvedAt`; ticker mapping `GEMI-<EVENT>-UP` → `<EVENT>`). Its apparent maker profitability is **highly concentrated and does not survive removing one market**, and **buy-side adverse selection is now observed in three independent datasets** (Polymarket residual ~−12¢/share; Gemini buy-only median −20.00¢; the withdrawn round-2 proxy, same sign). Gemini prediction markets are buy-the-outcome with explicit `outcome ∈ {UP, DOWN}` and separate per-outcome inventories — relevant if replication ever models a short leg. No execution change is requested.

---

# AMENDMENT (round-6 review accepted) — verdict reclassified

The decision to stop is unchanged. Three claims above were overstated and are corrected here; where they conflict with the text above, **this amendment governs**.

## Reclassification

**`STOP FOR RESOURCE ALLOCATION`** — executable profitability not demonstrated, insufficient reliable capacity evidence, and other markets now have higher research priority. **Not** "proven unprofitable."

## 1. "Capacity bounded at ≤ $0/day" — withdrawn

A negative sample mean does not establish a bound. The cluster bootstrap 95% CI is **[−59.69¢, +49.11¢]**, which includes substantial positive expectancy; `P(true mean ≤ 0) = 52.1%` is a coin flip, not a ceiling.

Worse, the arithmetic I used was invalid in a second way: **daily profitability cannot be obtained by scaling a sample mean by an assumed fill rate**, because proxy fills are *adversely selected* rather than randomly drawn — we are filled precisely when someone wanted to trade through our level. Multiplying a mean built from such fills by a fill rate compounds that selection. The correct statement is that **no capacity figure, positive or negative, is supportable from this sample.**

## 2. "SELL is not executable" → **unverified, not disproven**

What is established: the schema is `{side, outcome}` with `outcome ∈ {UP, DOWN}`, inventories are tracked per outcome, the live canary used `SIDE_BUY`, and all 2,692 harvested symbols were `-UP`.

What is **not** established: that a realistically resting **BUY of DOWN** could not capture equivalent economics. The native schema supports it; we simply never observed the DOWN book and never checked venue permission. The complement path remains **open and untested**, not closed.

## 3. Adverse-selection evidence: **two observations, not three**

The round-2 markout was withdrawn as biased (its settlement proxy was a mid-window price). An invalidated measurement cannot serve as corroboration. The defensible count is **two**: the Polymarket unpaired-residual decomposition (~−12¢/share, itself an accounting attribution) and the Gemini buy-only median (−20.00¢). Both are suggestive; neither is decision-grade.

## Preservation

Collector, settlement records (201 resolved event tickers), the ticker mapping, and the frozen estimator `a163c201` are preserved as independently useful. **No further Gemini engineering unless materially new evidence changes the economics** — specifically: verified DOWN-side executability, or a materially larger settled sample.
