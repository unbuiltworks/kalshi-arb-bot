# L5 — why complementary orders stopped: a wrong-side lifecycle defect

Read-only investigation. **The live campaign was not touched, modified, or
redeployed. No taker path exists. No authorization changed.**

---

## 1. The answer: a lifecycle defect, not policy and not a counter limit

In both strands the engine spent the rest of the window **quoting the side it
already held**, and never once quoted the side that would have completed the
pair.

### 1245z, every cycle from 12:50:54 to window close

```
round: 2
leg_to_place:           DOWN          <- but the residual WAS DOWN
plan_unexecutable:      LEG_WOULD_CROSS:DOWN
retirement_blocked_by:  LEG_HOLDS_INVENTORY:UP
```

Residual was **DOWN 1.4**, so the completing side was **UP**. Across 9 minutes
and 106 residual frames: **7 pre-send refusals on DOWN, zero UP intents.**

### 0130z, the same symptom

```
plan_unexecutable:      LEG_WOULD_CROSS:DOWN
retirement_blocked_by:  LEG_HOLDS_INVENTORY:DOWN
```

Residual **DOWN 1.87**, completing side **UP** — and again the plan was trying
to place **DOWN**.

### The mechanism, confirmed in source

`l5_live_execution.py:4425 probe_round_is_safe_to_retire` forbids abandoning a
round when either side holds inventory:

```python
for leg in ("UP", "DOWN"):
    if self.store.position(market, leg) != D(0):
        return "LEG_HOLDS_INVENTORY:%s" % leg
```

That reads **per-market, per-side aggregate** inventory, not the round's own
fills. In 1245z the book was `up 1.4 / dn 2.8` — *both* sides non-zero, because
round 1's UP fill is still on the books. So it returns `LEG_HOLDS_INVENTORY:UP`
on the first iteration, the round can never retire, and the engine stays pinned
to **round 2's frozen plan**, whose leg assignment no longer matches reality.

The side being quoted comes from that frozen plan, not from the live signed
residual. Once they diverge, the engine quotes the wrong side until the window
closes.

### What it is NOT

- **Not a submission-budget limit.** REPLACE had ~27 of 32 tickets free.
- **Not a recovery-counter limit.** The terminal refusals were
  `PRESEND_CROSS` (correctly declining a wrong-sided order that would also
  *worsen* the imbalance) and then `PAST_ENTRY_CUTOFF`.
- **Not intended policy.** `probe_round`'s own docstring states the opposite
  intent: *"a one-sided round is FINISHED before a new pair is started — the
  opposite leg of an existing position always takes priority over opening
  another."* Completion-first is already the design. It simply does not execute.

**The pre-send guard was right every time.** Those 7 refusals declined orders
that would have crossed *and* increased the residual. It was the last line
catching a wrong-sided intent — not the cause.

## 2. What this does to the four policies

| | verdict |
|---|---|
| **A. current, 0.96 maker-only** | Not what is running. What runs is A *with the complement aimed at the wrong side* once a second round's legs are both claimed. |
| **B. continuous maker-complement management at 0.96** | **Already the intended design.** It is not a new policy — it is what the code says it does and does not. The gap between A and B is a bug, not a trade-off. |
| **C. defensive taker with a higher ceiling** | **Premature.** It compensates for a bug. And in 1245z the complement was never ≤0.96 as a *taker* price — but see below, it **was** reachable as a maker. |
| **D. time-dependent ceiling** | **Premature for the same reason**, and it would be fitted to two windows whose losses have a mechanical cause. |

### The decisive measurement

In **both** strands a correctly-sided **maker** complement was inside the
existing 0.96 ceiling, and was never submitted:

| window | residual | correct side | maker price available | pair | inside 0.96? |
|---|---|---|---|---|---|
| 1245z | DOWN 1.4 @ 0.32 | **UP** | ~0.63 (2 ticks behind, 12:48:38) | **0.95** | **yes** |
| 0130z | DOWN 1.87 @ 0.62 | **UP** | 0.31–0.33 (01:32:56–01:33:03) | **0.93–0.95** | **yes** |

So the loss in both windows did **not** require a higher ceiling, a taker
cross, or more openings to avoid. It required quoting the correct side.

**This supersedes my previous recommendation.** I proposed raising the
completion ceiling, on the reasoning that a strand loses the whole notional
while an over-cap completion loses almost nothing. The arithmetic was right but
the premise was wrong: the complement was not unaffordable, it was never
offered. I was proposing to relax a safety limit to work around a defect I had
not yet found.

## 3. 0145z — the fix is inert where things already work

0145z is the counterexample the brief rightly insisted on: DOWN filled
01:47:01, residual DOWN, complement **UP**, and the UP **did** fill as a maker
at own 0.52 at 01:52:24 — pair 0.87, **+$0.2028** after 5m23s of patience.

There the frozen plan's leg assignment **still matched** the residual, so the
engine quoted UP and waiting paid. A fix that derives the side from the live
residual changes **nothing** in 0145z: it only alters behaviour when the frozen
side differs from the residual-reducing side, which it did not.

That is the property that makes this change safe to prefer over C and D: it
cannot pre-empt a profitable wait, because it does not touch *when* to
complete — only *which side* completes.

## 4. Where the defect appears

| outcome | windows | wrong-side quoting |
|---|---|---|
| **stranded** | 0130z, 1245z | **2 of 2** |
| completed | 0030z, 0045z, 0100z×2, 0115z×2, 0145z, 1245z-R1 | **0 of 8** |

Present in every strand, absent from every completion. n is small, but the
mechanism is identified in source, which is far stronger than the association
alone — and it explains the symptom that puzzled both post-mortems: minutes
remaining, tickets free, and no order sent.

## 5. Net P&L, fees, fill assumptions

| window | realized | fees | residual loss |
|---|---|---|---|
| 0130z | −$1.1594 | $0.00 commission on every leg | −$1.1594 (whole notional) |
| 1245z | −$0.2940 | $0.00 | −$0.4480, against +$0.1540 of pair |
| 0145z | **+$0.2028** | $0.00 | none |

**Fill assumption, stated honestly:** a correctly-sided maker complement at
0.63 (1245z) or 0.32 (0130z) was *placeable within the ceiling*. Whether it
would have **filled** is not knowable — it was never resting. 0145z is the
evidence that a correctly-sided maker complement does fill when it is given the
window: 5m23s, and it paid. So the claim here is deliberately narrow:

> The engine never gave itself the chance. It is not that patience failed; it
> is that the order was pointed the wrong way.

I am **not** claiming a recovered $1.45. I am claiming two windows were lost
without the correct order ever being submitted.

## 6. Against L5's sequential behaviour

L5's own corpus (662,116 buys, 5,503 BTC15m markets) shows **46.5% of his buys
reduce an existing imbalance** — 307,614 completing actions, interleaved with
extends, across ~120 actions per market. He is continuously re-aiming at
whichever side reduces his residual, which is precisely the behaviour this
defect prevents in ours. His median held imbalance of 99 contracts is
sustainable *because* every action re-evaluates the side.

So the fix moves us toward L5's observed behaviour, not away from it — unlike
C and D, which have no counterpart in 165 days of his activity (2 sells in
768,325 trades).

## 7. The single best-supported change

**Derive the complement side from the live signed residual, not from the frozen
round plan.** When a round holds inventory and its complement has not filled,
re-target the complement to whichever side reduces `|residual|`.

- **No new exposure.** It only ever replaces a wrong-sided order with a
  risk-reducing one.
- **No taker, no sell path, no ceiling change, no budget change, no change to
  opening frequency.** The 0.96 ceiling, $0.80 cap, $2 deficit, $5 limits and
  the watchdog all stay exactly as they are.
- **Inert where the engine already works** — including 0145z.
- **Fixes the stated bottleneck directly:** it is the reason complementary
  orders stopped being submitted.

Secondary, and I would do it in the same change: make
`probe_round_is_safe_to_retire` consult the **round's own** fills rather than
per-side aggregate inventory, so a round whose legs are both claimed and whose
own fills are matched can retire instead of pinning the engine to a stale plan.

**Not recommended now:** policies C and D. Both are compensations for this
defect, both need a limit relaxed or a taker path opened, and both would be
fitted to two windows whose losses have a mechanical explanation. Revisit only
if strands persist after the side selection is correct.

**Status:** nothing implemented, nothing deployed, campaign running unchanged
under its existing limits.
