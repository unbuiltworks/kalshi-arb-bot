# Continuous-inventory replay: preconditions measured, experiment NOT yet decidable

Read-only. Nothing deployed, no risk change, no production change.

**The continuous-inventory replay is NOT built, and this document explains why
building it against the current tape would have produced an underpowered
answer rather than an answer.** Three measurements, all made first as
instructed, change what the experiment has to be.

---

## 1. WHY ONLY 29/120 WINDOWS BECAME CAMPAIGNS — IT IS NOT THE ENTRY GATE

Run over **all 400 symbols** (not the earlier 120-symbol sample):

| window status | n | share |
|---|---|---|
| **NO_QUOTE_COVERAGE** | **282** | **70.5%** |
| NO_FILL | 112 | 28.0% |
| RESIDUAL_HOLD | 5 | 1.3% |
| MATCHED | 1 | 0.25% |

**The dominant cause is missing quote coverage, not a strategy refusal.**
`NO_QUOTE_COVERAGE` is returned when the tape holds no two-sided BBO row
inside the entry window (`MIN_TTE_S 105 <= expiry - t <= ENTRY_MAX_TTE_S
300`). For 70.5% of symbols the tape simply does not cover the minutes the
engine is allowed to enter in. That is a **collection gap**, and it agrees
with the independent finding on the habitat side that the median in-window
span was 159 s of a 300 s window.

## 2. `entry_band_left` IS SECONDARY — AND IT IS NOT FAITHFUL TO L5

The Gemini adapter's entry band is `MIN_PRICE 0.20 / MAX_PRICE 0.80`
(`transfer_backtest`, vendored from `pair.MIN_PRICE @ 80f40c86`).

**Every observed refusal price is BELOW 0.20:** the stop reasons are
`entry_band_left:0.17` (9), `:0.19` (6), `:0.18` (6), `:0.06` (4), `:0.02`
(4), `:0.09` (3), `:0.16` (3), and a tail below that.

Against canonical L5's own fills:

| | value |
|---|---|
| L5 ALL-fill price p25 | **0.190** — below our MIN_PRICE |
| L5 first fills inside [0.20, 0.80] | 1749/2216 = **78.9%** (so **21.1% outside**) |
| L5 all fills inside [0.25, 0.75] | 35318/71984 = **49.1%** |
| L5 all fills inside [0.05, 0.95] | 66969/71984 = 93.0% |

**L5 routinely enters where our band refuses.** A quarter of its fills sit at
or below 0.19. The band is a fee-safe PAIR-ECONOMICS constraint, not a
behavioural claim about L5 — but its *effect* is to suppress state visitation
in a region L5 demonstrably occupies.

**And the suppression is worse than one refused action.** In
`replay_window`, an entry-band refusal at zero inventory does
`stop_reason = rec["refusal"]; break` — **a single out-of-band observation
terminates the entire window**, not just that decision.

**Verdict: the gate is an unsupported transfer assumption, not L5-faithful.
It is NOT removed, per instruction.**

## 3. MAX_ACTIONS=6 DID MATERIALLY SUPPRESS THE PRIOR RESULT — BUT DOES NOT CHANGE THE VERDICT

Same 400 symbols, `stochastic:strict`, cap 6 vs a research ceiling of 400
with runaway protection:

| | cap = 6 | research ceiling 400 |
|---|---|---|
| decisions/window p50 | **6 (pinned)** | **8** |
| p75 / p90 / max | 6 / 6 / 6 | **12 / 19 / 30** |
| windows AT the cap | **72/118 = 61.0%** | 0/118 = 0.0% |
| total fills | 7 | **13** |
| complement fills | 1 | **2** |
| MATCHED windows | 1 | **2** |

**61% of windows were pinned exactly at the ceiling** — p50, p75 and p90 were
all 6, which is the signature of a distribution determined by its cap rather
than by the market. Removing it roughly **doubles** fills, complement fills
and matched windows, and the natural decision count becomes p50 8, p90 19.

**It does not change the verdict.** 2 matched campaigns of 118 against a
floor of 30 is still short by a factor of fifteen. And p50 8 decisions across
a 300 s window is ~37 s per action against L5's ~5 fills and 2 switches per
5 minutes — **the action cap was not the binding constraint.**

---

## WHY THE CONTINUOUS-INVENTORY REPLAY IS NOT BUILT YET

Building it now would run against **118 windows that produce any decision at
all**, of which **2** reach both sides. Continuous intention could at best
move a handful of those. The pre-registered floor — **>= 30 matched campaigns
AND >= 30 complement fills** — is unreachable on this tape under ANY
persistence rule, because 70.5% of the symbols never present a quote in the
entry window in the first place.

**That makes the current blocker DATA, not code.** A continuous-inventory
result computed here would be underpowered by construction and could not
honestly be called SUPPORTED or KILLED.

## ETH IS RECOVERABLE AND ROUGHLY DOUBLES THE 5m DATASET

The asset field in the `policy_d_port` tapes is **`market`**, not `symbol` —
which is why the earlier extraction returned empty. Across 1,001,265 tape
lines:

| Gemini prefix | bbo rows |
|---|---|
| **ETH05M** | **90,738** |
| **BTC05M** | **90,646** |
| ETH15M | 34,312 |
| BTC15M | 34,286 |

**ETH 5m is present at parity with BTC 5m** and is normalizable into the
identical schema with no model change. Note the constraint that matters for
fill evidence: **prints are sparse** (BTC05M 659, ETH05M below the top ten),
and `replay_window` requires a symbol to appear in BOTH bbo and prints.
Quote coverage expands readily; execution evidence does not.

## VERDICT ON CONTINUOUS INVENTORY: **STILL UNKNOWN**

Not SUPPORTED and not KILLED. The falsification criteria are unchanged and
untested. Nothing about the hypothesis has been weakened — the canonical
evidence for it (73.4% of L5's same-side continuations at a new price) still
stands. It simply cannot be decided on 118 decision-producing windows with 2
matched campaigns.
