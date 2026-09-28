# Capacity ladder — REVIEW DOCUMENT ONLY. Not authorized, not armed.

**Purpose: measure whether real BTC15m execution supports the shadow assumptions.**
It is not a profit attempt and it does not select anything. No new strategy parameter,
threshold, completion rule, queue assumption or challenger appears here.

## What is being measured
**EFFECTIVE FILLED THROUGHPUT, not a single order.** "q50" in the shadow arm means 50
contracts of *matched inventory built over a window*; the shadow model already produces
it from 3–6 incremental fills. A rung is therefore expressed as a **cumulative filled
target**, and a rung passes on **authenticated, reconciled fills** — never on submitted
or acknowledged quantity.

The three shadow assumptions under test:
1. **maker fills happen at all** at our queue position (the queue-aware model is a
   model; zero live BTC15m fills exist);
2. **the ladder supports a completion** at the price the frozen condition admitted;
3. **cumulative filled quantity per window** reaches the clip the shadow arm assumes.

## Rungs

| rung | cumulative filled target | max single order | purpose | advance only on |
|---|---|---|---|---|
| **A** | 1 contract | 1 | does a maker order fill at all | 1 authenticated fill, reconciled, settled |
| **B** | 5 contracts | 5 | reach the **demonstrated** clip (5.31 ever filled) | ≥5 filled across ≥2 fills, all reconciled |
| **C** | 10 | 5 | does throughput accumulate across fills | ≥10 filled, ≥1 completion admitted by the frozen condition |
| **D** | 25 | 10 | first rung above demonstrated capacity | ≥25 filled, LOCK5 evaluated every action |
| **E** | 50 | 10 | the shadow arm's q50, as throughput | ≥50 filled |

**Rung A is where the evidence is.** 5.31 contracts is the largest clip ever filled
live, on ATP tennis, in 2026-09. D and E are **above demonstrated capacity** and remain
modeled extrapolation until their own fills exist.

## Invariants every rung retains, without exception
* **exact frozen LOCK5 semantics** — `settlement_independent_floor` consumed read-only,
  threshold exactly $5.00 before rebates, latch prohibits OPEN/ACQUIRE/EXTEND;
* **authoritative reconciliation** — the venue is the authority; inventory and cash move
  only from `VENUE_FILL` or `RECONCILIATION`, never from an ACK;
* **the mandate is above strategy intent** — a refusal is final and LOCK cannot override
  it;
* **observability or abort** — monitoring uncertainty aborts the rung;
* **one market, one window, one spent authorization**;
* **no rung advances on modelled, submitted or acknowledged quantity.**

A rung that fails does not retry at a larger size. Ever.
