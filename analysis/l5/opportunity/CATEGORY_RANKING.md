# LANE E — opportunity and capacity ranking across PMUS categories

**Scope.** Ranks the PMUS categories/market types we have recorded data for, against
**the mechanics actually observed from L5** — not against mechanics we wish existed.
Built entirely from artefacts already in this repo. No S3 touched, no habitat
selector refitted, no live_exec spec or deploy artefact read for modification, no
git command run, no order, no credential.

**One live attempt to re-enumerate the current slate was made and FAILED**:
`GET https://gateway.polymarket.us/v1/series` returns HTTP 000 / `CONNECT tunnel
failed, response 403` through this container's agent proxy. So every number below
comes from recorded data, and the newest slate census in the repo is
**2026-09-27**. Anything about the slate *today* is unverified.

---

## 0. The mechanics we have actually observed from L5

From `analysis/l5/COHORT_EXECUTION_MODES.md` and `analysis/l5/FILL_TOXICITY.json`:

| observed property | value | sample |
|---|---|---|
| maker share of fills | **95.5%** | 55,639 maker / 63,569 fills, 527 windows, BTC15m |
| sequence shape | **maker → maker 82.7%** | 259 / 313 attributed sequences |
| fills per window | **121** | BTC15m |
| spread at the moment of fill | median **$0.01** | n = 2,878 |
| settlement adverse selection per fill | **−$0.0041** (CI −0.0251 … +0.0194) | 3,069 fills, 27 markets |
| +60 s forward markout | mean **+$0.004**, p10 −0.145 / p90 +0.165 | n = 2,720 |
| matched pair cost | **0.9624** | BTC15m cohort |
| pair rate, balanced band 0.35–0.65 | **84.25%** | n = 1,143 first legs |

**And the exclusion that governs this lane:** every L5 cohort *outside* BTC15m —
BTC5m (322/322), ETH15m (13/13), ETH5m — is **taker → taker**, 0–0.24% maker, 3–7
fills/window. The mechanism we are ranking for exists in L5's record in **exactly
one cohort**. So "the mechanics we observed" = *tight-spread, high-churn, two-sided
maker-maker inventory working, at the touch, in a short binary*. The discriminator
the habitat work found is **queue turnover** (depth ÷ touch replacement rate), not
spread (`analysis/pmus/sports_habitat_discovery.json: the_third_failure_mode`).

Our own frozen mechanics are narrower still: one market, one 0.01-share-minimum
post-only join at the touch, maker-first, patient completion, $5/market envelope.

---

## 1. The ranking

Rank order is over the eight requested axes weighted the way the observed mechanism
actually works: **two-sided inventory opportunity and passive fill frequency first**
(they are the mechanism), then adverse selection, then spread/depth/TTE/fees/rewards.
Each cell carries its basis and n.

| # | category / market type | passive fill frequency | spread | depth (touch, contracts) | adverse selection | two-sided inventory opportunity | TTE structure | fees | CONFIRMED rewards |
|---|---|---|---|---|---|---|---|---|---|
| **1** | **tennis — `atpcq`/`atp`/`setkame*` match_winner** | **EVIDENCED (ours).** 20 maker / 0 taker fills over 3 settled sports markets 2026-09-10 (2 tennis); 4 maker fills on `aec-atp-jayfri-maxbas` 2026-09-13; 9 tennis markets in the R1→R4 scoreboard. **OBSERVED (book):** largest habitat sample on PMUS — 12 mk / **1,382 states** / 212.7 obs-min, buy-reach@120s **31.3%**, sell-reach **31.7%**, recycle **18.06/hr**, median reach 39.3 s | median **$0.01**, 69.2% ≤1c (n=1,382) | median bid **3,773**; touch move **0.78/min** → poor turnover | **NOT MEASURED as a markout.** Only proxy: realized net **+$0.102** on $20.80 turnover, 9 markets, pair +1.0478 against residual −1.0858 | **EVIDENCED, and the best in the programme on pair cost inside discipline:** `aec-setkamecz-mulpat-holtom-2026-09-10` — 11 maker fills, **19.87 matched pairs at pair cost 0.9477**, residual **0.00**, fully matched. Book: two-sided reach only **6.3%** (n=1,382) | **WORST axis.** No fixed expiry → the PMUS clock defect (`SPORTS_TTE_DEFAULT_S = 1e9`, **635/635** decisions keyed `tte>10m`) | maker θ **−0.0125** (paid), taker θ 0.0695, published 2026-09-17 | **$0.14** credited (R4, 3 markets) + **$0.13** credited 2026-09-10. Liquidity rewards **$0.00** — 0 of 239 traded sports slugs carried a program |
| **2** | **soccer — `sea`/`flc`/`lal`/`epl` full_time_winner & team_full_game_total** | **EVIDENCED (ours):** `atc-epl-cov-bha-2026-09-13-bha` 7 maker fills; `asc-lal-lev-fcb-...-pos-2pt5` 6 maker fills, both post-only at the touch, rest 1.94–31.92 s. **OBSERVED (book):** `sea\|team_full_game_total` buy-reach **67.8%** (3 mk / 224 st / n120=180) — highest on PMUS at n≥100 — but sell-reach only **10.6%** | median **$0.01**; `sea` winner 98.2% ≤1c; `mls` totals/spreads degenerate at 0.12–0.16 | `sea` winner **45,538**, totals **12,535**, `mls` drawable 225; move 0.28–0.79/min → **frozen wall** | **NOT MEASURED as a markout.** Natural experiment recorded: cov-bha drifted *for* us (YES 0.47→0.99) and completed 4/32; jayfri drifted *against* and completed **0/10** | **BEST EVIDENCED in the programme:** cov-bha **5.67 pairs at 0.7636, zero residual**; lal-lev-fcb **7.07 pairs at 0.9477**. Both inside the ≤0.96 spring target. Book two-sided reach 2.2–5.0% | no fixed expiry; clock defect applies | same schedule | **$0.00** credited (turnover below the one-cent rebate floor) |
| **3** | **crypto — `cpc-btc-updown-15m`** | **EVIDENCED (ours):** Probe 07 **9 submitted / 9 rested / 9 maker-filled**, 0 taker, rest 1.5–204.8 s, in one 15-min window (≈36 fills/hr); Probe 08 5 fills. 35 taker actions correctly refused | median **$0.03**, p90 0.04 (n=167 samples, one window) — **3× the sports leaders**; tick 0.01 | bid **2,600** / ask **10,000**; **60.7% of 89 closed windows carried ZERO volume**; traded windows median 14,956 contracts | **NOT MEASURED for us on PMUS.** L5's own BTC15m figure (−0.0041/fill, n=3,069) is from **PM International**, a different venue — it is a reference, not a PMUS measurement | **EVIDENCED, and the WORST we hold:** matched pair cost **1.0299** (Probe 07, 2.47 pairs) and **1.1986** (Probe 08, 3.93 pairs) — both **above par**, far outside ≤0.96. SHADOW/THEORETICAL only: ≥2c gross complements reachable in 62.5% of 315 decision states, median 29.9 s | **BEST axis, outright.** Native literal 15-min clock, `startTime`/`endDate`; 6 load-bearing properties **IDENTICAL** to L5's source (instrument, BRTI resolution, index, window, payout, binary structure); 96 windows/day | same schedule | **$0.05** credited in total ($0.01 Probe 07 + $0.04 Probe 08). `crypto_up_down` liquidity pool is **$30/day shared across ~120 markets**; nothing credited → **$0.00** |
| **4** | **esports — `cs2`/`lol`/`valorant` match_winner** | **EVIDENCED (ours):** 6 maker / 0 taker fills on `aec-cs2-ica-ff-2026-09-10`; 2 cs2 markets in R1. **OBSERVED:** cs2 7 mk / 648 st reach **19.0%**, recycle 10.8/hr; lol 4 mk / 294 st **20.5%**, 10.67/hr; valorant 2 mk / 428 st 12.9% | median **$0.01** (cs2 74.7% ≤1c, lol 78.6%); valorant 0.02 | **cs2 142, lol 112** — the **thinnest tight books on PMUS**, i.e. the closest profile to L5's source (65) of anything we have recorded | **NOT MEASURED.** One evidenced adverse case: cs2-ica-ff realized **−$3.9028** with residual −9.08 against pair +0.0924 | cs2 pair cost **0.98** on 4.62 pairs (evidenced, borderline). Two-sided reach 0.0–4.6%. Series-total-maps sub-types are degenerate (spread 0.21–0.50, depth 5) | no fixed expiry; clock defect | same | **$0.04** credited (cs2-ica-ff) |
| **5** | **baseball — `mlb` moneyline LIVE** | **OBSERVED only.** 8 mk / 1,339 st reach@120s **29.9%**, two-sided **9.1%** — best two-sided at n>1,000 in the 28-min sweep. One market at a different hour read 1 mk / 50 st, reach **77.8%**, sell **69.4%**, two-sided **47.2%**, recycle 47.36/hr — **n = 36 answerable states, not a category reading**. Ours: 6 baseball markets but only **$0.238** turnover | median **$0.005** — **tighter than L5's own source**, 94.2% ≤1c | bid **3,951** / ask **68,095**, move **0.64/min** → the canonical "tight but frozen" wall | **NOT MEASURED** | book two-sided 9.1%; ours: net **−$0.029** on $0.238 turnover (6 markets) — size far below the rebate floor, shape evidence only | no fixed expiry; clock defect | same | **$0.00** |
| **6** | **basketball — `wnba` (no `nba` data at all)** | **OBSERVED, and essentially zero.** `wnba\|moneyline\|LIVE` 1 mk / 204 st reach@120s **0.0%**; `wnba\|totals\|LIVE` 1 mk / 204 st 11.1%, two-sided 0.0%. **No fill ever taken in this category** | 0.01 | wnba ML **20,329** bid, move 0.0/min | not measurable | **none observed** | no fixed expiry | same | **$0.00** |
| **7** | **football — `nfl`/`cfb` moneyline, spreads, totals** | **OBSERVED, and zero.** Every pregame football group sampled reads reach@120s **0.0%** with touch move **0.00/min**: nfl ML PRE 2 mk / 408 st, nfl totals PRE 11 mk / 2,243 st, nfl spreads PRE 13 mk / 2,652 st. **No NFL or CFB LIVE reach has ever been measured** | nfl ML PRE **0.005** (100% ≤2c); nfl spreads PRE 0.05 (15.4% ≤2c) | nfl ML PRE **2,252,045** bid / 2,347,077 ask — the most extreme wall recorded anywhere | **NOT MEASURED** | **EVIDENCED, and the worst:** 5 markets, $34.68 turnover, net **−$1.5476**; `asc-cfb-wisc-nd-...-pos-10pt5` alone **−$6.2255**, pair +4.4773 against residual −6.3049 | no fixed expiry; clock defect | same | **$0.00 credited**, despite carrying the *largest* liquidity pools on the venue ($750–$4,500 per program/period) — target sizes are 25,000–500,000 contracts and our 12-contract share models to ≈$0 |
| **—** | **combat / ice / other single-market groups — `ufc`, `boxing`, `nhl`, `darts`, `table-tennis`** | **NOT RANKABLE.** `ufc\|moneyline\|LIVE` has the most L5-like profile on PMUS — reach **45.8%**, two-sided **33.3%**, median reach **16.0 s**, depth 360/257 — on **1 market and 39 states**. `boxing\|PRE` and `nhl\|PRE` read 0.0% reach. No fill ever taken | ufc 0.02, nhl PRE 0.02, boxing PRE 0.03 | ufc 360 (thin, promising); boxing 5,547; nhl PRE 153 | not measurable | ufc two-sided 33.3% at n=39 — **a hypothesis, not a reading** | no fixed expiry | same | **$0.00** |
| **—** | **economics, politics, commodities, weather, culture, props-ex-sports** | **NOT OBSERVED — NOT RANKABLE AT ALL.** The PMUS live funnel of **11,631 markets across 61 live events (2026-09-13)** found `live_events_unfiltered 61 == live_events_categories_sports 61`: the entire PMUS live universe at that instant was category `sports`. The gateway serves **50 leagues**, all sports. The only non-sports PMUS product enumerated anywhere in this repo is the BTC up/down / strike / range / hit series. **Zero observations in any of these categories.** The live re-enumeration that would update this was blocked by the proxy | — | — | — | — | — | — | — |

### What the ranking says in one line

> **BTC15m does NOT win.** It wins **one** axis — TTE structure, where it wins
> outright and is the only native-clock cohort available — and ties on fees. It
> **loses** passive fill frequency, spread, two-sided inventory opportunity and
> confirmed rewards to tennis and soccer, and loses spread to MLB. Its evidenced
> pair economics (1.0299, 1.1986) are the **worst we hold**, against soccer's
> **0.7636** and tennis's **0.9477**, which are the only evidenced pair costs
> inside the ≤0.96 spring discipline.

### And the honest limit on that statement

Tennis's and soccer's lead rests on **one fully-matched market each** (19.87 and
5.67 pairs) inside a 212-minute and a 100-minute observation window respectively.
That is not a verdict; it is a reason to measure those categories properly before
BTC15m is treated as the destination. Tennis also loses the single axis that the
frozen controller is most sensitive to — the clock — and no sports category has a
fix for that.

---

## 2. Data thinness, per category, quantified

| category | book states | markets | obs-minutes | our evidenced maker fills | our settled markets | markouts measured |
|---|---:|---:|---:|---:|---:|---|
| tennis | 1,382 (+ 245 INTL ref) | 12 | 212.7 | ≥24 | 9 | **none** |
| soccer (sea/flc/mls/lal/epl) | 224 + 224 + 224 + 208 + 2,847 | 27 | ~35 each, mls 440 | ≥13 | 4 | **none** |
| crypto BTC15m | 167 public-tape samples, 1 window; 315 authenticated decision states; 89 closed windows of volume | 1 series | 7.5 (tape) | 14 | 4 probes | **none on PMUS** |
| esports | 648 + 294 + 428 | 13 | 100 / 45 / 66 | 6 | 3 | **none** |
| baseball | 1,339 (+1,588 totals, 1,688 spreads) | 8–9 per type | 28-min sweep | 0 | 6 (trivial size) | **none** |
| basketball | 204 + 204 + 26 | 1–2 | 28-min sweep | **0** | 0 | — |
| football | 408 + 2,243 + 2,652, **all PREGAME** | 2–13 | 26-min sweep | ≥8 (R3L/R1) | 5 | **none** |
| ufc / boxing / nhl / darts / tt | 39 / 204 / 407 / 1 / 7 | 1–2 | minutes | **0** | 0 | — |
| economics, politics, commodities, weather, culture | **0** | **0** | **0** | **0** | **0** | — |

**The three thinness facts that matter most.**

1. **No post-fill markout has ever been computed on our own fills, in any
   category, on either venue.** The only toxicity measurement in this repo
   (`FILL_TOXICITY.json`) is of **L5's** fills on **PM International**. The
   adverse-selection column above is therefore an inference from realized
   residual losses in every row, and I have labelled it so in every row.
2. **Football's entire reach dataset is pregame.** `habitat_search_v2.json`
   says so itself: 176 of 229 sampled markets were NFL pregame for games days
   away, `move/min 0.00`, and the artefact states "THIS SAMPLE CANNOT ANSWER THE
   REACH QUESTION". Football's rank 7 reflects its **evidenced −$1.55 realized**
   and its walls, not a measured live reach — because there is none.
3. **The sports habitat sweeps ran off-peak.** The 127-market sweep ran
   01:45–02:15 UTC, "the TAIL of the US live-sports evening"; phase 2 ran
   02:40–03:10 UTC "when essentially no US sport was live". Both artefacts
   pre-register a repeat at peak and that repeat has not happened. No PMUS sports
   category met the preregistered habitat thresholds in either sweep
   (**qualifying_categories: 0**).

---

## 3. What would have to be TRUE for each category to be executable by our
## current frozen mechanics

Frozen mechanics assumed throughout: **one market at a time, 0.01-share minimum,
post-only at the touch, maker-first, patient completion, $5/market envelope,
residual ≤10 shares, no taker fallback, no unsupported rebate assumption.**

**tennis (`atpcq` match_winner).** Needs (a) the clock defect fixed by passing a
real settlement horizon — today 635/635 decisions key `tte>10m`, so the frozen
controller is running with one TTE bucket and the patience schedule is inert;
(b) two-sided reach above its measured **6.3%**, or an explicit acceptance that
completion will be one-sided and slow; (c) the 0.9477 pair cost to reproduce
beyond n=1 market. Fees and rewards already work here — it is the only category
with rebates credited twice.

**soccer (`sea`/`flc`/`epl`).** Needs (a) the same clock fix; (b) the buy/sell
reach asymmetry resolved — 67.8% buy against 10.6% sell on `sea` totals means our
patient *completion* side is the hard side, and that asymmetry is exactly the
Probe 08 failure mode; (c) the evidenced 0.7636 to be shown not to be a function
of favourable first-leg drift, which section E of the R5F forensic says it was.

**crypto BTC15m.** Needs (a) a crypto entry band, separately reviewed, not a
quiet widening of the sports 0.20–0.80 — only **54%** of observed mids fall inside
it; (b) the taker-fee coefficient corrected from 0.06 to the venue's published
**0.0695** (this is flagged as a live defect on the execution path and it loosens
the fee-safe complement bound on *every* PMUS market, sports included); (c) pair
cost brought from 1.0299/1.1986 to ≤0.96 — which at a 0.03 median spread and a
0.01 tick means a completion rule demanding **≥2c, realistically ≥3c gross**,
since a 1c gross pair edge is **worth less than nothing** after the 1.67c median
complement taker fee; (d) the 60.7% zero-volume window rate accepted as the duty
cycle.

**esports (`cs2`/`lol`).** Needs (a) clock fix; (b) acceptance that depth 112–142
is a *feature* (it is the only PMUS profile near L5's 65) and a measurement of
whether a 112-deep book strands more often — the BAND_SPREAD caveat says it
should, and the 28.2% incidence figure was measured in books 80× deeper;
(c) hard exclusion of the degenerate `series_total_maps` sub-types.

**baseball (`mlb` moneyline LIVE).** Needs (a) clock fix; (b) a peak-hours repeat
of the habitat sweep showing reach rises materially above **29.9%** — the
artefact's own stated gating step; (c) a route past a 3,951-contract touch
replaced 0.64 times a minute. At 0.01-share minimums we can *join*; the open
question is queue position, which is **unproven** everywhere in this programme.

**basketball.** Needs any sample at all: 1–2 markets and 0% reach is not a basis.
Needs NBA data, which does not exist in this repo.

**football (`nfl`/`cfb`).** Needs (a) a **live** reach measurement, which has never
been taken; (b) a reason to believe a 2.25-million-contract touch is joinable;
(c) an explanation of the −$6.23 single-market CFB spread loss before any further
spread-shape exposure. Its large liquidity pools are **not** a reason — target
sizes of 150k–500k contracts make our share ≈$0 and nothing has ever been
credited.

**combat / ice / other.** Needs n. `ufc` is the most interesting single reading on
the venue and it is 39 states on one market.

**economics, politics, commodities, weather, culture.** Needs the products to be
shown to exist on PMUS at all. CLAUDE.md requires the engine stay market-agnostic
and it is — `PMUSAdapter` and `pmus_q1_executor` are slug-based with zero
sports-specific order-path references — but **agnostic machinery is not the same as
available markets**, and we have zero observations here. Stating a rank for these
would be ranking on vapour.

---

## 4. Sources (every number above traces to one of these)

- `analysis/pmus/sports_habitat_discovery.json` — 127 markets, 204 sweeps, 25,908 observations, 28-min window, 0 qualifying categories
- `analysis/pmus/habitat_search_v2.json` — 4,551-market one-sweep census; phase-2 reach invalidated by its own CRITICAL_LIMITATION
- `analysis/pmus/live_rankings_and_shadow.json` — 87 category rows (14 PMUS), recycle/hr, two-sided, TRADED_FLOW_AUDIT
- `analysis/pmus/live_slate_habitat.json`, `live_discovery_coverage.json` — slate census, 50 PMUS leagues vs 469 International
- `analysis/pmus/universe/live_funnel.json` — 11,631 markets / 61 live events / by_sport buckets, and the categories-filter finding
- `analysis/pmus/P24_PMUS_BTC15M.md`, `p24_pmus_btc15m_window_volumes.json` — 128 windows, 89 closed, 60.7% zero
- `analysis/pmus/EXPLORATORY_OPPORTUNITY_FREQUENCY.md` — 315 authenticated decision states (exploratory, top-of-book only)
- `analysis/pmus/pmus_published_fee_schedule.json` — taker θ 0.0695, maker θ −0.0125
- `analysis/pmus/evidence/PROBE_06..08`, `probe08/PROBE_08_FINAL.md`, `BPRIME_FOUR_MARKET_RESULT.md`, `K2*` — our own venue-confirmed fills and realized nets
- `analysis/l5/COHORT_EXECUTION_MODES.md`, `FILL_TOXICITY.json`, `BAND_SPREAD_FINDINGS.md`, `BAND_SPREAD_GRID.md`, `CAPACITY_CURVE.md`, `CAPACITY_AND_CLONE.md`, `EXEC_SHADOW_ECONOMICS.md`, `L5_PROFITABILITY.md`, `l5_economic_scoreboard.json`, `REALIZED_BRIDGE.md`, `LIVE_R5FT_FORENSIC_2026-09-13.md`
- `data/pmus/sports_incentives_2026-09-10/{credited_reconcile,depth,README}.json|md`
- `data/pmus/sports_opportunity_2026-09-10/{ranked_universe,eligible,README}` — 340 markets, 32 eligible, all UCL

**Not used, by instruction:** `analysis/l5/s3/**` (live CI collection in flight),
`deploy/**`, `scripts/l5_exec*`, any `live_exec*` spec or frozen fingerprint. The
habitat selector artefact named in the brief
(`analysis/pmus/habitat_selector_frozen.json`, sha256 `f9dcd886549a2865`) **does
not exist anywhere in this working tree** — `grep` for both the filename and the
hash returns nothing. Nothing was refitted, because there was nothing to refit.
