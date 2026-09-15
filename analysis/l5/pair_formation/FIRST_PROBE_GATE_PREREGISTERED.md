# First bounded BTC15m probe — gate PREREGISTERED 2026-09-15T19:0xZ

**Written before any of the three arms has produced a two-sided sequence.**
Every threshold is a number, fixed now. Choosing one after seeing which arm or
venue happened to do well is the failure this document exists to prevent; a
later change must be a new commit that says what changed and why.

**Scope of what passing authorizes:** a *tiny bounded shadow-to-live probe* on
**one venue, one cohort (BTC15m), one market at a time**. It does not authorize
size, concurrency, a second venue, or any change to the executable pair
ceiling. **Live authorization is a separate human decision and is not requested
here.**

**Polymarket and Gemini qualify INDEPENDENTLY.** Whichever passes first may go
first. Neither waits for the other, and a failure on one is not evidence about
the other.

---

## G-P1 · Repeated prospective two-sided sequences

| | |
|---|---|
| metric | windows that reached **evidenced** two-sided inventory, on one venue, under one arm |
| threshold | **≥ 20** |
| prospective means | every quote was recorded with its price and time **before** the fill that decided it, and decided only from public prints published **after** placement |
| why 20 | below ~20 a single lucky window dominates the pair-cost median; 20 spans several hours and multiple book regimes |

Sequences from different arms are **not** pooled. An arm qualifies on its own
record.

## G-P2 · Positive settled shadow economics

| | |
|---|---|
| metric | **settled matched-pair P&L + settled residual P&L − fees**, summed over the qualifying sequences |
| threshold | **> $0**, and **matched-pair P&L > 0 on its own** |
| why both | a total rescued by a lucky directional residual is not the mechanism. The matched leg is the part we claim to reproduce, and it must stand up by itself |
| settled means | the venue's own resolution, `closed` with an outcome price ≥ 0.99. No unresolved market contributes |

## G-P3 · Pair economics, not just pair formation

| | |
|---|---|
| metric | effective pair cost after known fees, per sequence |
| threshold | **median ≤ 0.96** (the adapted executable ceiling) **and 0 pairs above par** |
| why | arm R's first live pairs were two-sided at a median **1.43** — a guaranteed 43 ¢/share loss. Becoming two-sided is not success. A single above-par pair fails this gate outright |

## G-P4 · Bounded residual

| | |
|---|---|
| metric | residual quantity at window settlement, per sequence |
| threshold | **≤ 50 %** of matched quantity at the median, and **≤ 100 %** at p90 |
| additional | residual duration reported; no threshold, because a residual that settles inside the window is a different risk from one held to expiry |
| why | the engine's first economic result was **100 % directional residual**. A candidate that reaches two-sided while carrying an unbounded naked leg has not fixed anything |

## G-P5 · No hindsight fills

| | |
|---|---|
| metric | evidenced fills whose deciding print predates the quote's placement |
| threshold | **0** |
| additional | every fill's deciding prints retained; coverage vouched (continuous observation across the quote's life) or the verdict is UNKNOWN, never a fill |
| why | this is the one failure that would make every other number here meaningless |

## G-P6 · The arm is the one being promoted

| | |
|---|---|
| rule | the qualifying arm's **exact frozen configuration** — grid ticks, desired inventory, cancel rules — is what a probe would run |
| threshold | 0 differences between the scored configuration and the proposed probe configuration |
| why | qualifying arm D and then probing arm S would be promoting something that was never measured |

## G-P7 · Venue independence is real, not assumed

| | |
|---|---|
| rule | all of G-P1…G-P6 satisfied **on one venue's own rows**, with no pooling across venues |
| why | Polymarket pays a maker nothing and Gemini charges a ceil-cent fee; their pair economics are not the same quantity and a pooled median describes neither |

---

## Decision rule

**A venue+arm pair qualifies when G-P1 … G-P7 hold simultaneously, in one
report, over one continuous sample on that venue under that arm.** No partial
credit, no pooling, no combining a good pair-cost run with a different good
residual run.

On qualification, the report says so and **stops**. It produces an evidence
package for a human decision. It does not enable live trading, does not size
anything, and does not alter the executable pair ceiling.

If any gate fails, the report names which, with the number, and no probe is
proposed.
