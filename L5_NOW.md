# L5_NOW — current operating note

> **EXTERNAL REVIEWERS: START AT
> [`analysis/l5/L5_EXTERNAL_REVIEW_CONTEXT.md`](analysis/l5/L5_EXTERNAL_REVIEW_CONTEXT.md).**
> That file is the canonical review handoff and carries the current mechanism,
> economics, falsified-hypothesis graveyard, transfer status, P0 and safety state.
> This file and every other L5 report are historical evidence and may contain
> superseded conclusions — notably the blended `0.35` residual:matched reference,
> which is NOT the maker-ladder target (it is `0.182`).

Updated 2026-09-20. Supersedes nothing in `L5_OPERATING_MODEL.md`; this file
records what the live sports-transfer experiment currently establishes.

## 0. Hypothesis hierarchy, in the order they were tested and fell

1. **Book reach — INSUFFICIENT.** Individual live-sports markets do reach the
   International BTC15m book-reach reference, but reach measures the ask
   descending to your bid, which quote revision alone produces. A fill needs a
   print. Reach is an upper bound on fill opportunity, not a measure of it.
2. **Residual cap — FALSIFIED as the PMUS bottleneck.** Cohort A (cap 3) and
   cohort B (uncapped) on identical streams matched *the same four chains*:
   `A=4, B=4, intersection=4, A_only=0, B_only=0`, with all 32 cap hits
   removed. The cap never stood between a PMUS chain and a match.
3. **Thin touch depth — CONFOUNDED.** The queue-aware fill rule *is* "cumulative
   compatible volume must exceed depth resting ahead", so thin-depth markets
   clear it by construction. On the optimistic branch, which never references
   depth, matched chains span depth 55 to 50,556.
4. **Compatible opposing flow — LEADING PROSPECTIVE HYPOTHESIS, UNTESTED.**
   It separates matched from unmatched in both branches (75–80% above median
   vs 40–42%). It is preregistered in
   `analysis/pmus/PREREGISTRATION_compatible_flow.json` and has NOT been tested
   out of sample. It is not a gate, filter or selection rule, and nothing in
   the shipped path consults it.

## 0a. DISCOVERY DEFECT — FIXED AND DEPLOYED 2026-09-20T15:03Z

**Status: corrected in the durable collector** (`5d70284d`, Railway deployment
`62a54463`, SUCCESS 15:03:31Z) and in the local analysis collectors
(`/v2/sports`, 264 operational slugs, sampling-frame marker written to
`nfl_sunday.jsonl`, `shadow_pmus.jsonl`, `flow_pmus.jsonl` at 15:27Z).

**EVERY PMUS VENUE-WIDE CONCLUSION DATED BEFORE 2026-09-20T15:03Z IS SUPERSEDED
AS A VENUE STATEMENT.** Those numbers remain valid *conditional on the
50-league slate* and are retained as such; none of them may be quoted as a
property of PMUS. This applies to sections 1–5 below, to the habitat rankings,
to the live-slate counts, and to the compatible-flow cohort's denominator.

### The original defect

**`/v2/leagues` is not the venue's league universe.** It returns 50 leagues.
`/v2/sports` returns **265**, every one but `polyclas` marked
`isOperational: true`. League slugs absent from the 50-list still serve events
when queried directly:

| league | in `/v2/leagues` | events returned |
|---|---|---|
| `cfb` (College Football) | no | 197 |
| `itfwo` (ITF Womens) | no | 87 |
| `atp` (ATP main tour) | no | 59 |
| `wta` | no | 46 |
| `dota2` | no | 5 |
| `itfw` | **yes** | **0** |

The index is both incomplete and partly wrong: it lists `itfw`, which is empty,
while the real slug `itfwo` is unlisted. This is NOT the `?limit` truncation
found earlier — the index returns 50 at every limit tried.

Full-slate census on the venue's own `event.live` flag, 2026-09-20 ~14:45Z:

| stage | all 265 leagues | reachable via `/v2/leagues` | share |
|---|---|---|---|
| events | 1786 | 353 | 19.8% |
| **live events** | **104** | **21** | **20.2%** |
| markets | 21926 | 11486 | 52.4% |
| two-sided markets | 17790 | 10829 | 60.9% |
| **live two-sided markets** | **260** | **121** | **46.5%** |

Largest live two-sided habitat on the slate is **Taça de Portugal (37)** — more
than EPL (32) — and it is invisible to the collector. Table-tennis, cricket,
basketball and ice-hockey live two-sided markets are **100% invisible**.

**Two earlier numbers are corrected by this.** PMUS was reported as a
50-league venue with 16 live events against International's 58. The venue index
is 265 leagues and 104 live events, so PMUS currently carries *more* live events
than International, not fewer. Every PMUS universe, ranking, habitat and
cohort number in this file was computed on the 50-league slate and has that
slate as its denominator.

Not deployed. The durable collector is mid-NFL-PRE baseline for the 17:00Z
natural experiment and a redeploy resets it. Recommended after that completes.

## 0a-bis. What the corrected universe immediately showed

First durable books window after the fix (100 KB, 15:18:21–15:18:29Z, 231 rows)
carried **13 leagues the 50-league index could not reach**: `tdp` 11, `itfwo` 7,
`nor1` 5, `itfme` 4, `khl` 3, `alsv` 2, `atp`, `ligpor`, `bra`, `els`, `swsl`,
`lal2`, `nls`. `wta`/`cfb`/`dota2`/`setkawoua` were absent from that particular
8-second window, which is not evidence of absence at that cadence.

Local analysis slate after the same fix: **300 markets across 98 events in 40
leagues** (was ~16 leagues), now including `odicr`, `odcup`, `dota2`,
`setkameua`, `itfwo`, `atp`, `atpdb`, `utr`, `bbl`, `bcl`, `lkl`, `etpl`.

**One suspicion retired rather than left open.** Two local collectors selected
liveness via `eventState.live && !ended` while a third used the venue's own
`event.live` boolean. Measured across 1791 events: 96 vs 96, intersection 96,
**zero disagreement either way**. The two rules are equivalent on this venue;
no change was made and no further narrowing exists there.

## 0b. Two accounting defects in the shipped PMUS_SURFACE report

1. **`OPPOSITE_RECYCLING` is pooled across markets.** `recycling()` concatenates
   every surface's fills and looks for an opposite-outcome fill within the mark
   with no slug check, so a BUY_YES fill in one market followed by a BUY_NO fill
   in a different market counts. The reported 52/64 (81.2%) at 300s is therefore
   not same-market inventory recycling. The same-market ceiling on that counter
   is 49/64 = 76.6%, so at least 3 of the 52 are cross-market by construction.
   The unambiguous same-market evidence is per-market
   `matched = min(qty_Up, qty_Down)`: **22.0 units across 8 of 42 markets**.
2. **Ladder totals are not session-cumulative.** `resync` retires a surface when
   its market leaves the slate, taking its fills and ladder with it. The 10:xxZ
   heartbeat (41/46 fills over 62 surfaces) and the 14:17Z one (33/31 over 42)
   are both point-in-time over *resident* surfaces. Full-session totals are
   strictly larger and recoverable only from the per-fill rows.

Neither is a data-loss defect: the `evidenced_fill` rows on the volume retain
the slug, so the underlying evidence is intact and recomputable.

## 0b-bis. Recycling recomputed STRICTLY WITHIN SLUG

The pooled `OPPOSITE_RECYCLING` is not used anywhere below. Rebuilt from the
per-market report of the 14:17:52Z surface session (progress row 15:20:17Z,
750 cycles, **49 resident surfaces**, verdict `TWO_SIDED_EVIDENCED_FILLS`):

| quantity | value |
|---|---|
| surfaces resident | 49 |
| markets with any evidenced fill | 14 |
| **markets filled on BOTH sides (same-market recyclers)** | **7** |
| markets with matched > 0 | 7 |
| total evidenced fills | 50 |
| fills inside a two-sided market | 42 (**84.0%**) |
| same-market matched units | **15** |

For contrast only, from the same row: pooled `n_opposite_within_300s` = 45/50
(90.0%). **That number pairs fills across different markets and is not a
same-market statistic.** The 84.0% above is a *market-level* measure — fills in
a market that reached both sides at some point — and is therefore an upper
bound on any timed same-market recycling rate, not the rate itself. The timed
rate needs per-fill timestamps and is not yet computed.

### The seven same-market recyclers, ranked

| slug | league | PLACED | FLOW | FILL | MATCHED | flow/placed | fill/flow |
|---|---|---|---|---|---|---|---|
| `aec-odicr-zim-aus-2026-09-20` | ODI cricket | 223 | 48 | 13 | **6** | 21.5% | 27.1% |
| `aec-itfwo-sarems-petkon-2026-09-20` | ITF Womens | 286 | 95 | 6 | 3 | 33.2% | 5.5% |
| `aec-dota2-aur-navi-2026-09-20` | Dota 2 | 33 | 10 | 7 | 2 | 30.3% | 58.3% |
| `atc-lal-atm-rma-2026-09-20-rma` | La Liga | 107 | 42 | 5 | 1 | 39.3% | — |
| `aec-cs2-lav-vexar-2026-09-20` | CS2 | 53 | 12 | 5 | 1 | 22.6% | — |
| `aec-setkameua-skhser-smyvas-2026-09-20` | Setka Cup UA | 215 | 36 | 4 | 1 | 16.7% | — |
| `aec-odcup-lei-mid-2026-09-20` | One-Day Cup | 110 | 11 | 2 | 1 | 10.0% | 18.2% |

**Five of the seven — `odicr`, `itfwo`, `dota2`, `setkameua`, `odcup` — are in
leagues the 50-league index could not reach.** Only `lal` and `cs2` were
reachable. Cricket is the leading category (7 of 15 matched units), a sport
that was **100% invisible** before the fix.

Per-league matched-per-fill: `odcup` 0.50, `itfwo` 0.50, `odicr` 0.46,
`dota2` 0.29, `setkameua` 0.25, `lal` 0.17, `cs2` 0.14.

This is SHADOW/MODELED throughout. `EVIDENCED_FILL` is a queue-ahead model of an
unpublished queue, never venue confirmation, never submitted, filled, settled or
realized. The surface watches ~49 of 1527 markets in the tape, so this ranks
what it watches, not the venue.

## 0c. Where PMUS actually recycles L5-style inventory

From the closed `pmus_surface_20260920T043537Z.jsonl` session, final `progress`
row at 14:17:04Z (6978 cycles, 42 resident surfaces, tape 14732 readable
observations, 0 unreadable):

| slug | fills | matched | resid | YES/NO fills | obs |
|---|---|---|---|---|---|
| `aec-atp-norgom-stesak-2026-09-20` | 19 | **8** | +2 | 10 / 9 | 918 |
| `atc-lal-get-mal-2026-09-20-get` | 8 | 3 | −2 | 3 / 5 | 1558 |
| `aec-cs2-lav-ence-2026-09-20` | 8 | 3 | −2 | 3 / 5 | 1032 |
| `atc-epl-bor-liv-2026-09-20-liv` | 7 | 3 | −1 | 3 / 4 | 798 |
| `atc-lal-get-mal-2026-09-20-draw` | 5 | 2 | −1 | 2 / 3 | 1156 |
| `aec-dota2-pips4-yb-2026-09-20` | 5 | 1 | +3 | 4 / 1 | 558 |
| `aec-wta-gabknu-lispig-2026-09-20` | 3 | 1 | +1 | 2 / 1 | 406 |
| `aec-cfb-miss-fl-2026-09-26` | 2 | 1 | 0 | 1 / 1 | 198 |
| 6 further slugs | 1–2 | 0 | ±1..2 | — | 78–3918 |

**The venue's top recycler is main-tour ATP tennis, in a league the collector
cannot see.** Four of the eight matched slugs — ATP, WTA, Dota 2, CFB — are in
unlisted leagues. This is also the second time today a habitat conclusion has
turned on discovery coverage rather than market behaviour.

## 0d. The two PMUS pipelines barely intersect

Of the 14 fill-producing surface slugs, 6 exist in the local collector at all
and only **1** is a COMPLETE-eligible chain in the frozen pipeline
(`atc-lal-get-mal-2026-09-20-get`, flow/min 0.928, queue-aware fill yes,
queue-aware matched no / optimistic matched yes; the surface independently
recorded 8 fills and 3 matched units on that same slug).

**n=1 cannot corroborate or contradict the preregistered compatible-flow
hypothesis**, and that chain predates the preregistration stamp, so it is
exploratory. The hypothesis and Model A are unchanged. No selector was fitted.

## 0e. Modeled pair basis, matched sequences only

MODELED. No order was ever submitted. Maker fee is 0 on both venues; the PMUS
maker rebate is venue-credited but not attributable at fill level
(`/v1/transfers` carries no order id) and is **excluded, not estimated**.

| branch | venue | eligible | matched | median basis | range | % < $1 |
|---|---|---|---|---|---|---|
| queue-aware | PMUS | 27 | 5 | **0.9800** | 0.9200–0.9800 | 100% |
| optimistic | PMUS | 37 | 14 | 0.9800 | 0.7000–0.9950 | 100% |
| queue-aware | INTL *(ref)* | 33 | 12 | 0.9475 | 0.6858–1.0342 | 92% |
| optimistic | INTL *(ref)* | 68 | 33 | 0.9600 | 0.6600–1.0500 | 91% |

Queue-aware and optimistic are never compared across venues and never pooled.
Median modeled gross on the PMUS queue-aware branch is +$0.0200 per pair on
n=5. That is an observation, not edge, profit or expectancy.

All five PMUS queue-aware matches are LoL/CS2 — but that pipeline's slate
excludes ATP, WTA, CFB and table-tennis entirely, so "esports" here is a
property of the slate, not an established property of the venue. The esports
habitat claim stays withdrawn.

## 1. The PMUS zero-match is NOT a liquidity failure

> **SUPERSEDED AS A VENUE STATEMENT (see 0a).** Computed on the
> 50-league slate; valid only conditional on it.


Across the venue-separated chain run:

| venue | first fills | matched | dominant break |
|---|---|---|---|
| PMUS (executable) | 13 | 0 | `1a_POLICY_NEVER_RECOMMENDED_COMPLETE` 92.3% |
| International (reference only) | 33 | 11 (33.3/100) | `1a` 57.6% |

**The frozen policy declined to seek an opposite position in 92.3% of observed
PMUS chains; only 3 chains were ever COMPLETE-eligible.** Nothing in that result
shows PMUS liquidity cannot complete a pair. Conditional on becoming
COMPLETE-eligible, PMUS is 0/3 — a sample of three. No transfer verdict either
way.

## 2. CORRECTION: the "p10-25c only" rule was mine, not v2's

On 2026-09-20 I reported that "the frozen v2 model recommends COMPLETE in
exactly one price bucket: p10-25c." **That statement was wrong as framed.** It
came from a probe that covered only `imb1-10 | tte>10m` with
`last_rel ∈ {open, extend}`. At that same slice with `last_rel=complete`, v2
recommends COMPLETE in *every* price bucket.

## 3. What v2 actually encodes: persistence in `last_rel`

Audited against the canonical L5 corpus, systematic-regime cohort, 69,003
imbalanced decisions, replicating `build_model`'s own decision loop:

| last_rel | n | L5's ACTUAL complete rate | v2 says |
|---|---|---|---|
| extend | 33,889 | **31.5%** | extend |
| complete | 31,931 | **68.4%** | complete |
| open | 1,902 | 34.5% | extend |

v2 tracks this correctly. Only 14 of 165 cross-tab cells (6.5% of decisions)
mismatch, and those sit at the 35–45% boundary. **Verdict: A, a real L5
behavioural rule — not a discretization artifact.** The marginal ~50/50
complete rate seen per price bucket is a *mixture* of these two modes; price is
not the predictor, `last_rel` is.

## 4. Residual-cap hypothesis FALSIFIED for this cohort

Two cohorts on identical market streams, differing only by rebinding
`MAX_RESIDUAL_UNITS`:

| PMUS | first fills | eligible | matched |
|---|---|---|---|
| A cap 3 (shipped) | 40 | 20 | **4** |
| B uncapped | 40 | 14 | **4** |

`A=4, B=4, intersection=4, A_only=0, B_only=0` — **the same four chains**.
Uncapping removed all 32 cap hits and added zero matches. The chains that
matched closed before the cap could bind.

**The current transfer bottleneck is compatible opposing flow and recycling
latency**, pending the >=25 COMPLETE-eligible checkpoint and the NFL LIVE test.
On International, uncapping actually *costs* matches (A=26, B=15) because
`last_rel=extend` persistence keeps sequences open instead of closing them.

## 5. Habitat signature: flow rate, not depth (CORRECTED)

At the 25-eligible checkpoint (28 optimistic / 24 queue-aware), the separator
between matched and unmatched PMUS COMPLETE-eligible chains is **compatible
flow rate**, not touch depth:

| feature | P(above overall median) matched | not matched |
|---|---|---|
| compatible flow/min — optimistic | **75%** | 40% |
| compatible flow/min — queue-aware | **80%** | 42% |
| touch depth — optimistic | 25% | 60% |
| movement/min — either branch | 38–40% | 42–50% |

**The depth signal I reported earlier is substantially tautological and I am
withdrawing the strong form of it.** The queue-aware fill rule *is* "cumulative
compatible volume must exceed the depth resting ahead", so thin-depth markets
clear it by construction. On that branch matched depth separates perfectly
(0% above median) — but that is the rule, not a discovery.

The optimistic branch does not reference depth, and there matched chains span
depth **55 to 50,556**: an EPL draw market with 50,556 depth matched because it
carried the highest compatible-flow rate on the PMUS slate (1.386/min). Matched
leagues on that branch are cs2, epl, flc and lol — not esports alone.

Movement/min does not separate at all in either branch.

## 6. The old inventory-regime framing



L5's own recorded behaviour sits at:

| imbalance | share of L5 decisions | actual complete rate |
|---|---|---|
| imb>50 | **75.6%** | 48.9% |
| imb10-50 | 19.9% | 48.9% |
| imb1-10 | **4.5%** | 50.4% |

The frozen shadow caps residual at `MAX_RESIDUAL_UNITS=3`, so every shadow chain
lives permanently in `imb1-10` — the regime L5 occupied 4.5% of the time — and
is then frozen at `no_action` by the cap after roughly two EXTENDs. L5 flips
extend→complete about 31.5% of the time *per decision*, so it completes
constantly over a long run; a three-decision sequence terminated by a cap gets
few chances to flip.

**This is a diagnosis, not a proposed change.** No model, bucket, threshold,
cap or parameter has been modified.

## 7. Habitat: book reach is not executable recycling

Individual live-sports markets do reach the International BTC15m book-reach
reference (INTL lol 90.9%, wta 77.5%, PMUS sea full-game-total 71.5% at 120s).
But P(a reach is followed by a *compatible print* within 60s) reorders
everything: PMUS cs2 76.9%, PMUS valorant 77.8%, INTL wta 6.7%, PMUS atpcq
tennis 0.0%. Reach is an upper bound on fill opportunity, not a measure of it.

## 8. Standing separations

* International is **reference/fidelity only** — L5's own venue, not executable
  for us, never a trading target.
* PMUS is the **primary executable transfer target**.
* The two are never pooled in any flow or chain statistic (asserted in code:
  zero slug overlap, disjoint venue tags).
* Gemini A+B continues unchanged as the negative control.
* No orders, no arming, no live capital, no Model A change.
