# Live sports opportunity selection: what R5F could be trading, and what it is

OWNER P0: rank currently active sports/game markets by L5-relevant flow,
two-sided activity, movement, spread/depth, completion opportunity, time
remaining and incentive eligibility; games over futures; do not optimize for
raw session count or liquidity rewards.

Measured 2026-09-10 17:22–18:28Z, public keyless reads only. **The live probe
`7ed717fc` (`a88dff63`) was not touched**, and nothing here is on its deploy
watch path.

---

## Headline: 32 markets are R5F-eligible right now. The probe is in none of them, and has not opened a first leg in 3h20m.

| | |
|---|---|
| live sports markets | 33,517 |
| of which **actual games** (non-future) | 25,588 |
| the campaign's own `universe_rows` | **2,037** |
| harvested here (games within 6h) | 340 |
| **R5F-eligible right now** | **32** |
| measured flow in those 32 | **$630,285 / minute** |
| leagues represented in the eligible set | **UCL only** |
| markets R5F worked today | 18 — ITF/Setka tennis, CS2, Dota2, county cricket |
| UCL markets R5F worked today | **0** |

---

## Two measurements here are not proxies

**Flow is real traded notional.** `/v1/markets/<slug>/book` carries a `stats`
block with cumulative `notionalTraded`. Differencing it across two snapshots
ten minutes apart gives dollars actually traded per minute. The existing
ranker (`src/pmus_universe_rank.py`) states in its own docstring that this
term "is not available on the public gateway, so those components score 0
here." **It is available.** That was the gap.

**Completion margin is the spread itself.** PMUS quotes on YES, and a NO buy
at `p` is the complement of a YES sell at `1−p` — the convention
`pmus_q1_executor_v4.outcome_bbo` already encodes. So a maker joining both
touches pays

    maker_pair_cost = best_bid + (1 − best_ask) = 1 − spread

A one-cent spread **is** one cent of matched-pair margin on a one-dollar
pair. This is not a proxy for completion opportunity; it is the quantity.

> A note on that: at a 1-cent spread a completed pair earns **1.0% of the
> capital it ties up**, against **0.625%** from the maker rebate at p=0.50.
> On sports the touch is worth more than the subsidy — consistent with
> yesterday's finding that our one clean completion earned ~10× more from
> trading than from rebate.

---

## The funnel, this deployment (13:25→18:17Z)

```
universe_rows 2,037
   ↓  admitted                                    302
   ↓  sessions                                     23   (18 distinct markets)
   ↓  submitted                                   350
   ↓  filled                                       39
   ↓  markets with a FIRST-LEG FILL              5/18
   ↓  markets with matched pairs at ≤ $1.00         4
   ↓  settlements today                             3
   ↓  credited rebates                          $0.17
```

Matched: 45.38 units. |Residual|: 18.47 units. Spent: $46.26.

**Economics of the three that settled:** matched-pair contribution **+$1.13**
against directional residual **−$7.60**, plus $0.17 credited.

> For every $1 of matched-pair contribution earned, **$6.72 was lost to
> directional residual.** That ratio, not attempt count, is the number to
> move.

### Top refusal reason, by a wide margin

| refusal | count |
|---|---:|
| `strict_evidence_thin_flow` | **1,056** |
| `strict_evidence_off_distribution_or_unknown` | 333 |
| `strict_evidence_market_closed` | 228 |
| `strict_evidence_no_q1_touch_depth` | 65 |
| `strict_evidence_from_future` | 33 |
| `cooldown_until` | 20 |

The gate is working correctly — it is refusing thin markets. It is refusing
them because **the universe it is offered is thin**, not because live sports
is thin. $630k/minute was flowing through UCL at the same moment.

---

## The eligible set, ordered by real traded flow

Gates applied: state OPEN · two-sided book · maker pair ≤ 1.00 · mid inside
R5F's 0.25–0.75 band for ≥50% of the last hour · nonzero measured flow.

| market | type | $/min | pair | spread | min touch qty | both sides moved | σ mid 1h | in band |
|---|---|---:|---:|---:|---:|:--:|---:|---:|
| `atc-ucl-fen-rom-…-rom` | DRAWABLE | 190,991 | 0.990 | 0.010 | 7,673 | Y | 0.1528 | 100% |
| `atc-ucl-fen-rom-…-draw` | DRAWABLE | 109,334 | 0.990 | 0.010 | 19,563 | Y | 0.0877 | 58% |
| `tsc-ucl-slp-rcl-…-3pt5` | TOTAL | 108,103 | 0.990 | 0.010 | 11,268 | – | 0.0045 | 100% |
| `tsc-ucl-psv-sha-…-2pt5` | TOTAL | 86,124 | **0.910** | **0.090** | 10 | Y | 0.1711 | 80% |
| `atc-ucl-psv-sha-…-psv` | DRAWABLE | 44,035 | 0.990 | 0.010 | 47 | Y | 0.1183 | 100% |
| `atc-ucl-psv-sha-…-draw` | DRAWABLE | 38,254 | 0.990 | 0.010 | 23 | Y | 0.0357 | 88% |
| `tsc-ucl-mnu-sab1-…-3pt5` | TOTAL | 15,198 | 0.990 | 0.010 | 2,056 | Y | 0.0039 | 100% |
| `asc-ucl-mnu-sab1-…-neg-2pt5` | SPREAD | 10,349 | 0.990 | 0.010 | 1,845 | – | 0.0062 | 100% |
| `astatc-ucl-bay-bog-…-btts` | PROP | 8,543 | 0.990 | 0.010 | 2,000 | Y | 0.0027 | 100% |
| `atc-ucl-com-rbl-…-com` | DRAWABLE | 5,687 | 0.990 | 0.010 | 50,515 | – | 0.0045 | 100% |

Eligible by type: PROP 11 · TOTAL 9 · DRAWABLE_OUTCOME 8 · SPREAD 4.

Two rows deserve attention beyond their flow rank. **`tsc-ucl-psv-sha-2pt5`
carries a 9-cent spread with only 10 contracts at the thinner touch** — a
0.91 pair cost with almost no queue ahead of a small order, which is the
shape R5F is built for and the shape it never saw today. And the high-flow
DRAWABLE rows pair a 1-cent margin with σ=0.15 hourly movement: heavy flow,
but the residual risk between legs is exactly what our −$6.72-per-$1 problem
is made of.

---

## The correction to the obvious story

The tempting conclusion is "the probe trades small markets". **That is not
what the data says.**

| | median lifetime `sharesTraded` |
|---|---:|
| the 18 markets R5F actually worked | **62,404** |
| top 12 eligible UCL markets | **62,501** |
| ratio | **1.0×** |

By lifetime volume the markets R5F chose are indistinguishable from the ones
it is missing. The difference is entirely **contemporaneous** — UCL is
trading now because those matches are live; the tennis and esports markets
had comparable lifetime volume spread thinly over a longer window. This
reproduces, on today's live slate, exactly the distinction task #124 drew
between contemporaneous flow and lifetime volume.

**What I therefore cannot claim.** I have no matched contemporaneous flow
measurement for the 18 markets R5F worked — 17 of 18 are now `EXPIRED` and
the counter cannot be differenced retrospectively. So "R5F would have done
better in UCL" is a **hypothesis this ranking generates, not a result**. It
is precisely what a shadow run answers.

---

## The operational finding that matters more than the ranking

The probe's market timeline for this deployment:

```
13:26Z  worked 20 distinct markets over 93 minutes
14:59Z  entered SPRING_RESIDUAL_HOLD on aec-county-not-ham-2026-09-08
18:17Z  still there — 3h20m, 156 of 180 session_end rows are RESIDUAL_HOLD
```

**One stranded residual has stopped the evidence factory for longer than it
ran.** The market is a County Championship cricket fixture dated 2026-09-08 —
a multi-day match — and residual is held to settlement by policy at
concurrency 1. Its current book is bid 0.77 / ask 0.84, i.e. **outside the
0.25–0.75 band**, so the engine cannot even work it; it can only wait.

Two further facts about that market:

- It is **live and trading** (`MARKET_STATE_OPEN`, last trade seconds ago,
  $10.4M cumulative notional).
- It **does not appear anywhere in the first 20,000 rows** of
  `/v1/markets?active=true&closed=false` under any filter combination I
  tried. The market we are holding is not in the listing our discovery pages
  through.

So the settlement-horizon term the ranker carries is not a nicety. **A
market whose settlement horizon is days can consume the entire live budget
at concurrency 1**, and nothing in the current admission path prices that.

---

## What I am not concluding

- **No change to the live probe, the band, the ceiling, the caps or
  concurrency.** This ranks; it selects nothing and submits nothing.
- **The eligible set is one 10-minute window on one Thursday.** UCL dominates
  because six Champions League matches were live at 18:15Z. At 02:00Z the
  answer will be different, which is the argument for running this
  continuously rather than reading this table as a standing verdict.
- **Incentive eligibility is reported, never scored** — every one of the 32
  happens to carry a program, which is exactly why letting it into the
  ordering would have been invisible and wrong.
- **340 of 25,588 game markets were harvested.** The 32 is a floor on what is
  eligible, not a census.

## Provenance

`data/pmus/sports_opportunity_2026-09-10/`: both book+stats snapshots
(gzipped, with start/end timestamps), the scored universe, the eligible set,
and the current state of every market R5F worked today. Ranking logic and its
conventions live in `src/pmus_sports_opportunity_rank.py` with 21 tests
covering the spread-is-the-margin identity, the `offers`-not-`asks` trap, and
the rule that unmeasured flow is `None` rather than zero.
