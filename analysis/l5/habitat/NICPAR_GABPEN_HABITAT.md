# Did the 35.7%-conversion environment score closer to canonical L5?

**Short answer: it scored ~2x the PMUS corpus baseline and is still ~1/5 of the
way to L5 — and the one available control in the same window CONTRADICTS the
hypothesis. n=1. Nothing is concluded and the score still gates nothing.**

## Method — identical to the baseline, deliberately

Same `analysis/l5/habitat/run.py`, same `PMUS_WS_XCHECK` BBO-mid observation
construction, same `l5_habitat_score.window_features`, same
`derive_reference()` over the canonical L5 sequence. The reference and every
candidate go through one function, so a score difference cannot come from
construction.

L5 reference re-derived from **1893 windows**, unchanged in shape:

```
price_changes_per_min   p10 0.8664   p50 2.3438   p90 10.7692
share_move_ge_1c        p10 0.5724   p50 1.0000   p90 1.0000
share_move_ge_2c        p10 0.4070   p50 0.8000   p90 1.0000
share_move_ge_5c        p10 0.2857   p50 0.6250   p90 1.0000
price_span              p10 0.1716   p50 0.7800   p90 0.9600
bands_visited           p10 2.0000   p50 4.0000   p90 7.0000
tte_p50_s               p10 69.0     p50 171.0    p90 524.0
```

## Result

```
market                                     obs  span_s   score   cov    disc
aec-setkamecz-smalib-svimat-2026-09-14      19     244  0.2738  0.86  0.2347
aec-lol-dkc-ktc-2026-09-14                  76    1135  0.2366  0.86  0.2028
aec-itfme-nicpar-gabpen-2026-09-14          78     971  0.2117  0.86  0.1815
aec-wtt-junfei-chichu-2026-09-14            34     581  0.1555  0.86  0.1333
aec-itfwo-alemaz-beazel-2026-09-14          10     232  0.1075  0.86  0.0921
aec-atp-eliyme-sempan-2026-09-14            62    1135  0.0688  0.86  0.0590
```

| | score | discounted |
|---|---|---|
| **nicpar-gabpen** (35.7% COMPLETE conversion) | **0.2117** | 0.1815 |
| prior PMUS corpus baseline (14 markets, ~1 h) | 0.108 | 0.0926 |
| this window's own baseline (6 markets) | 0.1555 | 0.1333 |
| canonical L5 | 1.0 by construction | — |

## Three reasons this is NOT the confirmation it looks like

**1. THE CONTROL IN THE SAME WINDOW GOES THE WRONG WAY.**
`aec-lol-dkc-ktc-2026-09-14` scored **0.2366 — HIGHER than nicpar-gabpen** —
and its session was `SPRING_NO_FILL`: 4 submitted, 0 filled, inventory zero.
The higher-habitat market converted nothing; the lower-habitat one converted
35.7%. At n=2 the score does not order these two the way conversion did.
(Caveat in both directions: dkc-ktc's four orders were OPEN attempts, not
COMPLETEs, so it is the nearest available contrast rather than a clean one.)

**2. THE WHOLE WINDOW SCORES HIGHER, NOT JUST THIS MARKET.** This window's
6-market p50 is 0.1555 against the earlier 14-market baseline's 0.108. So a
large part of nicpar-gabpen's 0.2117 may be *when* it was measured — a
post-maintenance hour, different market mix, different time of day — rather
than anything about the market. The clean comparison is against its own
window's p50 (0.1555), where it is 3rd of 6 and above median, not exceptional.

**3. 0.2117 IS NOT "CLOSE TO L5".** It is roughly one fifth of the way. The
market that produced our best COMPLETE conversion to date is still, on
pre-action variables, a materially poorer habitat than the one L5 operated in.

## Sampling-bias direction, stated as always

`price_changes_per_min` is flagged **sampling-limited** — a rate cannot exceed
the observation cadence, so a coarsely sampled candidate is biased DOWNWARD.
The UP-biased features, where measurement error **favours the candidate**,
score at p50: `share_move_ge_1c` 0.0726, `share_move_ge_2c` 0.0546,
`share_move_ge_5c` 0.0227, `price_span` 0.1404. **Even where the bias helps
them, PMUS candidates score far below the L5 reference.** That asymmetry is
the load-bearing evidence and it is unchanged by this window.

`tte_p50_s` is **UNKNOWN** for every candidate (XCHECK rows carry no TTE);
coverage 0.857 throughout, and the discounted column is the one to quote.

Audit: 101 xcheck rows, 12 markets seen, **6 scored**, 5 skipped too-short, 1
unusable, 2 with a saturated trade counter.

## What this does and does not license

It does **not** establish that habitat explains the 35.7%. The COMPLETE
forensics found a sufficient mechanical explanation already — the pair ran near
0.96 under a 1.00 ceiling, so the engine could post AT the touch 12 times in 14
— and that explanation needs no habitat difference at all.

**The score still gates nothing.** No admission, sizing, concurrency or
production change follows from it, and none is proposed.

To make this answerable rather than suggestive you would need conversion rate
and habitat score paired across enough markets to correlate them — n=1 with a
contradicting control is an observation, not evidence.
