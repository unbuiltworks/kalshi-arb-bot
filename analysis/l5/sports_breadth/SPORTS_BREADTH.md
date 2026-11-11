# L5 sports breadth: enumeration, frozen habitat score, canonical shadow

Observation only. Read-only GETs and public websockets. No order was placed on
any venue. No per-market cap, residual cap, concurrency, daily-loss limit or
total exposure changed. No sports strategy exists; no sport-specific tuning
exists. Nothing is deployed.

**Sample: one 8-minute live session, 2026-09-14 ~15:30Z, 107 scored markets
across 8 venue x sport cohorts.** Everything below is a first pass on that
sample. The pipeline is built to run continuously; this is not a venue
verdict.

## 1. Enumeration (market-agnostic, both venues)

| venue | active events | tradeable contracts | categories |
|---|---|---|---|
| Gemini | 982 | **8,630** | Sports 531, Politics 221, Weather 98, Crypto 91, Commodities 18, Economics 9, Business 6, Culture 5, Tech 3 |
| PMUS | ~38 live | **72** | sports only (table-tennis, tennis, esports, soccer) |

Gemini sports stream over the **same** websocket as the crypto windows, so no
second Gemini pipeline was created. Enumeration, scoring and the shadow all
run through one code path for both venues.

## 2. Habitat, frozen score, no tuning — venue x sport x pass

Missing-data penalty is the scorer's own `coverage_discounted_score`; the
sample-size penalty is shrinkage toward the global median with `k=5`
pseudo-markets fixed in advance. `n` and the raw median are always shown.

| cohort | n | raw | shrunk | spread (ticks) | depth | attainable pair | osc | 1–4 s share | price chg/min | trades/min | cadence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PMUS/soccer [DEEP] | 3 | 0.0899 | 0.0337 | 1.0 | 1,146 | **0.99** | 0 | **0.0** | 0.504 | 0.0 | 2.0 s |
| GEMINI/baseball | 20 | **0.0** | 0.0 | 1.0 | 1,050 | **0.99** | 0 | — | **0.0** | 0.0 | 1.0 s |
| GEMINI/Bitcoin 5m | 14 | 0.0 | 0.0 | 1.0 | 750 | 0.99 | 0 | 0.949 | 0.0 | 0.0 | 1.0 s |
| GEMINI/Ether 5m | 13 | 0.0 | 0.0 | 1.0 | 750 | 0.99 | 0 | 0.667 | 0.0 | 0.0 | 1.0 s |
| PMUS/tennis [DEEP] | 1 | 0.0351 | 0.0059 | 1.0 | 18,229 | 0.99 | 0 | 0.0 | 0.508 | 0.0 | 2.0 s |
| GEMINI/american_football | 2 | 0.0 | 0.0 | 1.0 | 1,259 | 0.99 | 0 | — | 0.0 | 0.125 | 1.0 s |

Cohort medians are dominated by dead windows. The **best individual markets**:

| market | venue | score | obs | spread (ticks) | attainable pair | osc | 1–4 s | chg/min | prints |
|---|---|---|---|---|---|---|---|---|---|
| GEMI-BTC05M…1535-UP | Gemini | **0.4492** | 297 | 21 | **0.79** | 13 | **0.949** | **29.90** | 12 |
| GEMI-ETH05M…1535-UP | Gemini | 0.3970 | 176 | 29 | **0.71** | 5 | 0.723 | 11.38 | 0 |
| GEMI-BTC05M…1540-UP | Gemini | 0.3134 | 426 | 2 | 0.98 | 10 | 0.855 | 7.38 | 2 |
| atc-egpl-gai-smo…draw | PMUS | 0.0987 | 123 | 2 | 0.98 | 1 | **0.0** | 0.63 | 0 |
| aec-atp-petbru-filrom | PMUS | 0.0351 | 122 | 1 | 0.99 | 0 | **0.0** | 0.51 | 0 |
| GEMI-NFL…DEN-KC-M-KC | Gemini | **0.0** | 455 | 1 | 0.99 | 0 | — | **0.0** | 1 |

## 3. Against canonical L5 (1,893 windows)

L5's own reference: `price_changes_per_min` p50 **2.34**, p90 10.77;
`price_span` p50 0.78; `bands_visited` p50 4. Demand side (what L5
*consumed*): `fills_per_min` p50 **3.0**, `switches_per_min` p50 **1.0**.

The score's inference runs **one way only** — below the reference is a
conclusive habitat deficit; above it is *not* conclusive sufficiency.

* **Gemini sports are conclusively deficient.** 454 observations per market
  over 8 minutes with **zero price changes**. Not slow — static.
* **PMUS sports are conclusively deficient.** 0.50–0.63 price changes/min
  against L5's 2.34, and this is **not** a cadence artefact: the DEEP pass ran
  at a 2.0 s sweep, which *can* resolve the 1–4 s band, and found a **0.0**
  share of transitions in it.
* **Gemini crypto 5m is the only cohort above the reference** on movement
  (29.9 chg/min on the best window, above L5's p90), and section D/E already
  established why that is not sufficiency: no reachable two-sided flow.

## 4. Canonical L5 shadow — 107 markets, all cohorts

Same unchanged `pmus_l5_persistence_v2` (model `bbb55bf90638d2bd`), same
replay, each venue's own adapter. `L5_BEHAVIORAL_ACTION` and
`VENUE_ADMISSIBLE_ACTION` kept separate throughout.

| cohort | markets | matched | fills (STRICT) | complement | BEH | VENUE | requotes | intentions | prints |
|---|---|---|---|---|---|---|---|---|---|
| GEMINI/Bitcoin | 14 | **0** | 2 | **0** | 188 | 91 | 67 | 24 | 14 |
| GEMINI/baseball | 20 | **0** | 0 | **0** | 20 | 20 | **0** | 20 | 4 |
| GEMINI/Ether | 13 | **0** | 0 | **0** | 136 | 36 | 21 | 15 | 0 |
| PMUS/tennis | 16 | **0** | 0 | **0** | 1,807 | 50 | 37 | 13 | 0 |
| PMUS/esports | 24 | **0** | 0 | **0** | 5,998 | 26 | 14 | 12 | 0 |
| PMUS/table-tennis | 9 | **0** | 0 | **0** | 373 | 30 | 22 | 8 | 0 |
| PMUS/soccer | 9 | **0** | 0 | **0** | 1,613 | 12 | 6 | 6 | 0 |
| GEMINI/american_football | 2 | **0** | 0 | **0** | 2 | 2 | **0** | 2 | 2 |

**ZERO matched campaigns and ZERO complement fills in every cohort, in every
tier, including the OPTIMISTIC front-of-queue upper bound** (which reaches
only 5 fills across all 107 markets).

Two readings the separated counters make possible:

* **Gemini sports: 20 markets, 20 intentions, 0 requotes.** Exactly one
  intention per market that rested the entire window without the touch moving
  once. The engine had nothing to respond to.
* **PMUS: enormous BEH against tiny VENUE** (esports 5,998 → 26). The policy
  re-evaluates constantly and is almost always **refused or holding** — the
  gap between the two counters *is* the admissibility problem, and reading BEH
  alone would have looked like a busy engine.

## 5. The pair-economics trap, and it cuts against sports

A maker joining both touches pays `bid + (1 − ask) = 1 − spread`. So a
**tight** book gives a **dear** pair:

| cohort | spread p50 | attainable pair | vs spring target ≤ 0.96 |
|---|---|---|---|
| PMUS sports | 1–2 ticks | **0.99** | **FAILS** |
| Gemini sports | 1 tick | **0.99** | **FAILS** |
| Gemini crypto 5m (live windows) | 21–29 ticks | **0.71–0.79** | passes |

PMUS and Gemini sports quote beautifully tight, deep books — and that is
exactly what puts locked pair economics out of reach. The only cohort whose
pair cost clears the spring target is the one whose prints never reach a
maker at the touch.

## 6. Verdict

**No venue x sport cohort shows materially better habitat AND executable
capture, so there is no bounded live-probe decision to bring.** The two
requirements fail in opposite directions:

* sports (both venues): pair cost 0.99, movement conclusively below L5's
  reference, zero prints reaching a maker;
* Gemini crypto 5m: pair cost attainable and movement above reference, but
  zero reachable two-sided flow (sections D/E).

Nothing is promoted. The ≥30 matched campaigns / ≥30 complement fills floor
is untouched, the habitat score still gates nothing, and PMUS bounded
production is unchanged.

**What this changes going forward:** the system now enumerates 8,700+
contracts across every lawful category on both venues and scores them on one
frozen scale, so the search is no longer restricted to whatever we happened to
be watching. Categories not yet observed — Gemini Politics (221 events),
Weather (98), Commodities (18) — are enumerated and eligible but unscored, and
are the obvious next cohorts.

**Known limits, stated rather than buried:** one 8-minute sample; PMUS WIDE ran
at a 27.2 s sweep, too coarse to score (its markets are reported undersampled,
not ranked); 17 of 107 windows hit the 400-action research ceiling; Gemini
publishes no tick size, taker fee, rebate or reward schedule, so those stay
UNKNOWN and are counted in the census's missing-data report rather than
imputed.
