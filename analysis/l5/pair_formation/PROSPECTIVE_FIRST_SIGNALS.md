# R/S/D prospective — first signals (2026-09-15T20:38Z, ~81 min in)

Deployment `b2a42330` @ `01900cc1` on `l5-exec-shadow`. Supervisor healthy:
`chain_validate, exec_shadow, gemini_depth` running, 0 failures, uptime 4,749 s.
Chain validation 141/142 (0.993), attribution 25/25 on both price and size.

**Shadow only.** Every row carries `would_submit: false`, `shadow_only: true`.
No order was submitted and no cash moved. Nothing below is realized profit.

## The headline: every pair formed is above par

| arm | windows w/ fill | P(two-sided) | median pair cost | above par | within 0.96 | settled matched | settled residual |
|---|---|---|---|---|---|---|---|
| R | 0 | — | — | 0 | 0 | — | — |
| S | 9 | 0.5556 | **1.085** | **5 / 5** | **0** | −$43.79 | −$162.71 |
| D | 11 | 0.4545 | **1.098** | **5 / 5** | **0** | −$60.86 | −$593.04 |

**All three arms fail G-P3 outright** (median ≤ 0.96 **and** zero pairs above
par). Not one pair in the whole prospective sample landed inside the ceiling.

This is the deficiency already flagged when arm R produced pairs at 1.43,
now reproduced prospectively in S and D. Becoming two-sided is not success:
S reaches two-sided more often than R and every pair it forms loses money on
the matched leg by construction.

D's residual is the second known failure repeating: residual qty 1,600 against
matched 410. The directional leg dominates the book again.

## PM / Gemini divergence is structural, not marginal

| | Gemini | PMUS |
|---|---|---|
| two-sided windows (S and D) | **0** | 0.8333 |
| pair availability "admissible" | 90.0 % | **0.0 %** |
| availability pair cost, median | 0.395 | 0.990 (p10 = p50 = p90) |
| settled matched P&L | $0.00 | −$43.79 (S) / −$60.86 (D) |
| settled residual P&L | −$53.70 (S) / −$386.90 (D) | −$109.01 (S) / −$206.14 (D) |

The two venues fail in opposite ways. **Gemini forms no pairs at all** despite
90 % of planned pairs scoring "admissible" at a median 0.395 — that availability
number is not executable, and is the cheapest-admissible-pair pathology
(a near-worthless quote on one side makes an arithmetic pair that never fills).
**PMUS** forms pairs readily and every one of them is above par, with its own
availability pinned at 0.99 across the whole distribution.

Neither venue qualifies. They qualify independently, and independently both
fail.

## Flow is NOT the problem

The depths do receive flow. Under the strictest criterion
(`VOLUME_AHEAD_CLEARED`), first-fill probability by resting depth:

| depth | PMUS · D | Gemini · D |
|---|---|---|
| 0 t | 0.5607 | 0.1176 |
| −1 t | 0.4762 | 0.2500 |
| −2 t | 0.2892 | 0.2917 |
| −3 t | 0.2632 | 0.3148 |
| −4 t | 0.2262 | 0.3684 |

PMUS declines with depth, Gemini *rises* with it. Every rung of the frozen
0…−4 grid is receiving decided quotes. **`sweet_spot` is `null` everywhere**,
not because no depth fills but because no depth has yet produced a *viable*
pair — the frontier refuses to name a sweet spot on fill rate alone.

## Queue realism earned its keep

Gemini · S, same quotes: **TOUCH 0.4354 → VOLUME_AHEAD_CLEARED 0.068.** A
6× gap. Scored on TOUCH we would have recorded a 44 % fill rate we never had,
and D would have looked fillable on queue priority it did not possess.

## What this does and does not authorize

Nothing changes. The policy is frozen and **no tuning is taken from these
outcomes**, as instructed. The ≥20-sequence preregistered gate remains the
live-probe gate, and on this evidence no probe is proposed: G-P3 fails before
G-P1 is even reached.

The honest reading is that the current candidates reproduce L5's *pair
formation* and not his *pair economics* — he grinds to ~0.9624 over ~121 legs;
these arms land at 1.085–1.098 in a handful. That gap is the research question,
not a parameter to retune.
