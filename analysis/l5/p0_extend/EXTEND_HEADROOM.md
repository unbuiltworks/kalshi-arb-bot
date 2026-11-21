# P0 — does EXTEND destroy completion headroom? **No.**

**I was wrong last turn.** I reported EXTEND as the mechanism converting completable inventory into stranded residual. Measured properly against every EXTEND in the available corpus, it is not.

Generator `analysis/l5/p0_extend/extend_headroom.py`. Nothing deployed.

---

## Data provenance — and a limit I cannot work around

**The durable 9,055-order ledger is NOT reachable from this session.** It lives on the probe's own volume (`/state/pmus-spring/ledger.jsonl`); there is no shell on that container and no HTTP export, and adding one would be a production change. I checked for a local copy — none exists.

The corpus is what Railway log retention serves: **2026-09-13T12:08Z → 19:54Z**, 220 sessions, 61 markets, 993 decisions, **214 measurable EXTEND transitions across 9 markets**. I paged back further; retention for the prior deployment begins at 12:08Z. Nothing below is extrapolated to the full ledger.

## The A/B/C/D table

Headroom is `pair_ceiling − held_side_average_cost`. "Completable" means the **observed** `pair_at_touch` (held cost + L5's own complement touch, recorded on every COMPLETE decision) was at or under the ceiling. Each EXTEND is bracketed by the real COMPLETE observations either side of it; an EXTEND without an observation on one side is **dropped, not guessed**.

**Ceiling 1.00 — 214 transitions, 9 markets**

| | count | share |
|---|---|---|
| **A — completable → NOT completable** | **31** | 14.5% |
| B — completable → still completable | 67 | |
| **C — not completable → completable** | **36** | 16.8% |
| D — already unreachable, still unreachable | 80 | |

**Fee-safe 0.98 — 214 transitions**: A **14**, B 38, C **20**, D 142.

**C > A at both ceilings.** EXTEND restored completability slightly more often than it removed it.

## The decomposition that settles it

`Δpair = Δheld_cost (ours) + Δl5_touch (the market)`. Attributing the market's own drift to our EXTEND would convict it of damage it did not do.

| ceiling | A cases | **from our cost rise** | from market move |
|---|---|---|---|
| 1.00 | 31 | **0** | **31** |
| 0.98 | 14 | **0** | **14** |

**Every single loss of completability was the complement touch moving away from us — none was our own average cost rising.** Median `Δheld_cost` across all 214 transitions is **0**; median headroom before and after is **identical** (0.5923).

The measurement is not degenerate: held cost genuinely changed in 100 of 214 transitions and the L5 touch in 154 of 214.

*(One case initially appeared attributable to us. It was a `+1E-27` Decimal artifact passing a `> 0` test while the real move was `market touch +0.1200`. A 0.1-cent materiality threshold now applies — without it the attribution is noise.)*

## Why the market-level correlation looked so strong

Last turn's 9/9-vs-0/3 split is real but **confounded**. EXTEND is a *symptom* of markets whose complement never arrives, not a cause:

- The engine keeps sampling EXTEND because the state stays imbalanced.
- The state stays imbalanced because the complement ask is 60 ticks away (P0-B).
- The 3 "clean" markets completed on their **first** attempt, so they never reached a state where extending was possible.

The arithmetic I cited — rising average cost shrinking headroom — is true in principle and **does not happen in practice at our sizes**: our EXTENDs are ~1.6–1.8 contracts at prices close to the existing average, so they barely move it.

## What this means for the A/B/C/D menu

**C (change continuous inventory handling) is no longer supported by the evidence.** Suppressing or gating EXTEND would remove 36 rescues to avoid 31 losses that were not EXTEND's doing. On this corpus it would make things slightly worse.

The evidence continues to point where P0-B pointed: **the complement is not there.** Median taker pair 1.0000 (strict) / 1.1352 (upper bound); complement ask typically ~60 ticks away; zero cases where the book reached our resting price. That is **A — first-fill/admission selection**: entering markets whose complement side can actually be bought.

## Caveats I am not hiding

- 9 markets, 7.8 hours, one venue session. This refutes the EXTEND hypothesis **on this corpus**; it does not prove EXTEND is harmless at larger size, where a bigger clip would move the average cost materially.
- 27.1% of transitions have identical bracketing observations — real quiet periods, but they dilute A and C equally.
- P1 counterfactuals (CF1–CF4), P2 (does L5 preserve headroom) and P3 (state-key omission) are **not delivered**: CF1/CF2 target a mechanism that the A/B/C/D table says is not operating, and running them first would be fitting a fix to a cause I have just disproved.
