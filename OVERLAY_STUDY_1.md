# Model-B overlay study — is it the venue, or is it our price?

**The owner's question:** does Gemini lack counterparties, or are we offering
liquidity at prices counterparties do not want to interact with?

**Status.** SHADOW. No order was sent. **Model A — the frozen L5 policy — is
untouched**, and a test pins that neither `continuous_probe` nor
`ws_continuous` can import this module. Every fill figure below is
**THEORETICAL**.

Reproduce: `python3 -m src.gemini_execution.overlay_report` →
`analysis/gemini/overlay_study.json`. Code: `overlays.py` · 33 tests.

**483 cycles** across 19 windows (245 YES-leg, 238 NO-leg). 25 cycles were
refused for an unreadable or crossed book — the controller would not have
quoted into them and neither does this study.

---

## 0. The first result makes one of the three overlays redundant

**`ticks_native_behind_best` is 0 in all 476 cycles where the policy quoted.**

The frozen L5 policy already joins the best passive price. It never quotes
behind it. **NATIVE and JOIN_BEST are the same order**, and the comparison
collapses to two arms: *what we already do* versus *improving by one tick*.

So there is no "we are quoting too far back" problem to fix. That hypothesis
is dead.

## 1. The three arms

| | NATIVE | JOIN_BEST | IMPROVE_1_TICK |
|---|---|---|---|
| cycles where it exists | 476 | 483 | **483** |
| refused / absent | 7 (policy quoted nothing) | 0 | **0** |
| would be best liquidity | 100% | 100% | 100% |
| improves the touch | 0 | 0 | **483** |
| cycles with a reaching print | 15 | 15 | **17** |
| cycles where the queue is readable | 238 | 245 | 245 |
| **theoretical fills respecting the queue** | **1** | **1** | **5** |
| theoretical fill share (queue known) | 0.4% | 0.4% | **2.0%** |
| median seconds still best liquidity | 8.0 | 8.0 | 8.0 |

**IMPROVE_1_TICK was maker-only in all 483 cycles** — the spread was never so
tight that bid+1 tick would reach the ask. It is refused wherever it would
not be maker-only, and it never had to be.

## 2. The mechanism, visible in four cycles

The four cycles where improving fills and joining does not:

| window | TTE | leg | best bid | join | improve | size ahead (join) | ahead (improve) | reaching print |
|---|---|---|---|---|---|---|---|---|
| …190140 | 212.6 | YES | 0.31 | 0.31 | 0.32 | 0.01 | 0 | 0.01 |
| …190445 | 159.7 | YES | 0.89 | 0.89 | 0.90 | **5.0** | 0 | 5 |
| …190555 | 245.1 | YES | 0.83 | 0.83 | 0.84 | **5.0** | 0 | 5 |
| …190605 | 160.5 | YES | 0.61 | 0.61 | 0.62 | **245.0** | 0 | 3.66 |

The last row is the cleanest statement of the problem: we join a bid with
**245 contracts already in front of us**, a **3.66-contract** sell arrives,
and it is consumed 67 times over before it reaches us. One tick better and we
are first in line.

**This is not "no counterparties". It is queue position.**

## 3. And the reason that is not a free lunch

**Adverse selection, measured both ways:**

| | theoretical fills | median mid move | not filled | median mid move |
|---|---|---|---|---|
| NATIVE / JOIN | 1 | **−0.135** | 459 | **+0.100** |
| IMPROVE_1_TICK | 5 | **−0.020** | 460 | **+0.090** |

When our quote is **not** hit, the mid drifts **in our favour** by about 9–10
cents. When it **is** hit, the move is **against us**. That difference is what
adverse selection *is*, and it holds for both arms. IMPROVE's five fills span
−0.145 to +0.27, so the sample is not one-sided — but its median is still
negative while its unfilled median is +0.09.

C8B's one real fill is the same shape and worse: **−0.135** by this measure,
−0.095 on the earlier post-fill sampling.

**Being first in the queue means being first to be run over.**

## 4. How long liquidity survives at the touch

| | seconds still best |
|---|---|
| p25 | 0.0 |
| **median** | **8.0** (one cycle) |
| p75 | 32.0 |
| p90 | 88.0 |
| max | 168.0 |

**46.8% of quotes are outbid within one 8-second cycle.** A quote at the touch
is not a position you hold; it is one you re-establish. That is consistent
with C8B, where the 483-lot bid we joined vanished within one cycle — the
book churns far faster than our 8-second decision loop.

It also means IMPROVE's advantage is perishable: being one tick better is
worth something for a median of 8 seconds before somebody does it to us.

## 5. The complement

Recorded for every cycle whether or not it filled, so the counterfactual is
available either way: the passive and crossing price of the other leg at that
instant. No cycle in this study produced a matched pair, theoretical or
otherwise — 5 theoretical fills, all one-sided, none followed by a complement
at a fee-safe price inside the same window.

## 6. The answer

> **Does Gemini lack counterparties, or are we offering liquidity at prices
> counterparties don't want to interact with?**

**Neither, exactly. The prices are right and the counterparties exist — the
queue position is wrong, and the flow is too thin for queue position to be
worth much.**

Three findings in order of how much they matter:

1. **Our price is already the best price, 100% of the time.** There is nothing
   to fix about where we quote relative to the touch.
2. **Only 15 of 483 cycles (3.1%) had any print that would have reached our
   price at all**, and only 17 of 483 for a one-tick improvement. Queue
   position converts 1 of those into 5. **Even perfect queue position leaves
   96.5% of quoting cycles with nothing arriving.** The binding constraint is
   still flow, not placement.
3. **The fills that queue position would win are adversely selected.** The
   median filled-cycle mid move is negative in both arms while the unfilled
   median is +0.09.

**What I am not proposing.** No admission threshold, no change to Model A, no
one-tick improvement in the live path. A 5-versus-1 theoretical result over
483 cycles, on a study whose own caveat is that *our size would have changed
the book we measured against*, is a hypothesis. Improving the touch makes us
the price other people improve on, and the 8-second median survival says they
would.

**What would test it honestly:** the same overlay run forward on new windows
with the pre-entry data attached, so the theoretical fills accumulate on
evidence that was not used to form the hypothesis. That is shadow work and it
needs no authorization.
