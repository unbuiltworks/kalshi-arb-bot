# Gemini 5m: complete-tape coverage, eligibility, and the collection verdict

Read-only. Nothing deployed, no risk change, no production change.

## P0 — CANONICAL TAPE

63 tapes -> **182,119 canonical rows, 756 markets** (BTC 378 / ETH 378),
181,384 BBO + 735 prints. Every row keeps `src_file`, `src_t`, `market`,
`asset`, `expiry_t`, `window_start_t`, `tte_s`. Dedup is deterministic on
`(market, kind, t, payload)` for BBO and `(market, print, trade_id, t)` for
prints. **Nothing is forward-filled across a gap** — a gap stays a gap.
All 756 windows took their metadata from the tape's own `window` rows
(`asset`, `expiry`, `window_minutes`); the symbol-parsing fallback was never
needed.

## P0 — COVERAGE

| | BTC | ETH | BOTH |
|---|---|---|---|
| windows discovered | 378 | 378 | **756** |
| complete (>=290 s) coverage | 49 | 48 | **97** |
| coverage >= 240 s | 64 | 63 | 127 |
| coverage >= 120 s | 95 | 95 | 190 |
| **median in-window coverage** | **0 s** | **0 s** | **0 s** |
| median obs/min | 8.3 | 8.3 | 8.3 |
| median max gap | 875.8 s | 875.8 s | 875.8 s |
| two-sided observations p50 | 167 | 170 | 170 |
| prints/window p50 | 0 | 0 | 0 |
| windows with >= 1 print | 103 | 36 | 139 |

## **P0 — THE 70.5% ATTRIBUTION (mandatory)**

**Complete tape: 524/756 = 69.3% NO_QUOTE_COVERAGE, against 70.5% on the
curated extract.**

| attribution | share | evidence |
|---|---|---|
| **EXTRACT_ARTIFACT** | **~1.2 pp, i.e. essentially none** | the figure barely moved (70.5% -> 69.3%) on a tape with 3x the markets and 3x the BBO rows |
| **SOURCE_COLLECTION_GAP** | **dominant** | markets carry a median of **170 two-sided observations** but a **median of ZERO seconds inside their own 300 s window**, and a median max gap of 875.8 s. The collector held subscriptions across bounded ~1.5 h sessions, so most rows are pre-open / post-expiry placeholder quotes for symbols that are live for only five minutes |
| **TRUE_NO_QUOTE** | **not supported** | 97 windows DO have >= 290 s of in-window coverage, which proves the venue quotes through the window when we are listening |
| **UNKNOWN** | residual | the tape cannot distinguish "venue silent" from "collector not listening" for any individual window |

**It was not the extract. It is our collection window, not the venue.**

## P1 — ELIGIBILITY MASKS (in-window evidence required for both)

| | BTC | ETH | BOTH |
|---|---|---|---|
| **STATE_ELIGIBLE** | 87 | 88 | **175** |
| **EXECUTION_ELIGIBLE (STRICT)** | 70 | 17 | **87** |
| EXECUTION_ELIGIBLE (SUPPORTED) | 44 | 47 | 91 |
| habitat-only | 17 | 71 | 88 |
| UNUSABLE | 291 | 290 | 581 |

A first version of this mask called **285** windows state-eligible by scoring
span and density without requiring the observations to be **inside the
window** — it was counting markets densely observed for an hour *before they
opened*, whose median in-window coverage was **zero seconds**. Corrected, it
is 175.

## P2 — HABITAT ON THE EXPANDED DATASET: **0.3944 SURVIVES**

| | BTC | ETH | BOTH |
|---|---|---|---|
| windows scored | 95 | 89 | **184** |
| score p50 | 0.3793 | **0.4337** | **0.4061** |
| discounted p50 (coverage 1.0) | 0.3793 | 0.4337 | **0.4061** |
| obs/min p50 | 11.8 | 11.5 | 11.6 |

**Prior: 0.3944 over 97 windows. Now 0.4061 over 184.** Within noise on
roughly double the sample — **no extraction bias to explain.** PMUS baseline
remains 0.108.

**ETH is the better habitat of the two**, and on the UP-biased features where
measurement error favours the candidate: `share_move_ge_1c` 0.224 vs 0.117,
`share_move_ge_2c` 0.292 vs 0.170, `bands_visited` 0.500 vs 0.375.
`tte_p50_s` holds at 0.803 (BTC) / 0.840 (ETH). `price_changes_per_min` is
`sampling_limited` on 74 of 184, so its ~0.84 is a lower bound.

## P3 — **[0.20, 0.80] IS NOT AN L5 BEHAVIOURAL RULE**

23,955 canonical runs by the price band the run STARTS in:

| band | runs | share | fills/run | P(switch) | matched% | **pair p50** | tte p50 |
|---|---|---|---|---|---|---|---|
| <.05 | 1390 | 5.8% | 2.52 | 59.9% | 89.0% | **0.9642** | 105 |
| .05-.10 | 1190 | 5.0% | 3.71 | 88.2% | 90.9% | **0.9674** | 218 |
| .10-.20 | 2402 | 10.0% | 3.92 | 93.3% | 90.5% | **0.9754** | 291 |
| .20-.35 | 3376 | 14.1% | 3.73 | 96.4% | 89.3% | 0.9865 | 457 |
| **.35-.65** | 7478 | 31.2% | 2.98 | 95.3% | **84.2%** | **0.9991** | 518 |
| .65-.80 | 3820 | 15.9% | 2.58 | 92.9% | 86.9% | 0.9979 | 408 |
| .80-.90 | 2429 | 10.1% | 2.45 | 90.1% | 89.9% | 0.9884 | 273 |
| .90-.95 | 1242 | 5.2% | 2.16 | 85.7% | 91.0% | 0.9847 | 174 |
| >.95 | 628 | 2.6% | 2.10 | 68.5% | 91.8% | 0.9782 | 119 |

**38.7% of L5's runs start OUTSIDE [0.20, 0.80]** (9,281 of 23,955).

**And the behaviour there is not merely present — on pair economics it is
BETTER.** Pair cost improves monotonically toward both extremes: 0.9991
mid-band against 0.9642–0.9754 below 0.20 and 0.9782–0.9884 above 0.80.
Matched fraction is HIGHER at the extremes (89–92%) than mid-band (84.2%).
The mid-band that our gate keeps is the region where L5's pairs sit closest
to par.

Structural differences that ARE real: runs at the extremes are shorter in the
tails (2.1–2.5 fills vs 3.7–3.9 in .05–.35) and **switch far less often**
(<.05: 59.9%, >.95: 68.5%, against ~95% mid-band), with much shorter TTE
(105–174 s vs 408–518 s). So extreme-price behaviour is **later in the window
and more terminal**, but it is not economically worse.

**Classification, kept in separate columns as instructed:**

| | verdict |
|---|---|
| behavioural fidelity to L5 | **[0.20,0.80] is NOT faithful** — L5 starts 38.7% of runs outside it |
| venue economic / risk admissibility | **retained, unchanged, not removed** — it is a fee-safe pair rule, and this analysis does not license changing it |

Note the tension worth naming: fees scale with `p(1-p)`, so the excluded
region is the LOW-FEE region, which is why its pair costs are better. A
fee-safety band that excludes the cheapest-fee prices is doing something
other than what its name says.

**Suppression on Gemini, once coverage is accounted for:** across the 175
STATE_ELIGIBLE windows and 5,924 in-window two-sided observations, **18.0%
(1,064) have a mid outside [0.20, 0.80]**. Only **3 of 175 windows (1.7%)**
have zero in-band observation, but **142 of 175 (81.1%)** contain at least
one out-of-band observation — which matters because an entry-band refusal at
zero inventory does `break` and ends the window.

## P4 — DECISION

| | |
|---|---|
| STATE research | **SUFFICIENT** — 175 STATE_ELIGIBLE windows |
| EXECUTION verdict | **UNKNOWN and unreachable on this tape** |

**735 prints exist across the ENTIRE canonical tape.** 87 windows are
STRICT-eligible. The pre-registered floor of **>= 30 matched campaigns AND
>= 30 complement fills** cannot be met from history at that print density —
and, per instruction, that floor blocks a distributional EXECUTION verdict,
not state-visitation research.

**No fills will be invented to close it.**
