# L5 — reconciliation: the wrong-side defect is real but did not cause either loss

Read-only. **Nothing implemented. Nothing deployed. The live campaign and its
authorization are untouched.**

You asked me to reconcile the contradiction before building. Doing so disproved
my own headline, so I stopped rather than implement on a premise I had just
refuted.

---

## 1. The contradiction, resolved

The two statements were about **different prices for different actions**, and
both are true:

| route for the UP complement | frames with pair ≤ 0.96 (of 28) | when |
|---|---|---|
| **taker cross** (executable now vs displayed depth) | **0** | never |
| maker **at the touch** | 3 | 12:48:38–12:48:59 |
| maker **2 ticks behind** (what the engine quotes) | **5** | **12:48:32–12:48:59** |

My earlier *"never achievable at ≤0.96"* was true of **taker**. My later
*"0.95 maker was inside the ceiling"* was true of **maker 2 ticks behind**. The
error was presenting them as the same quantity. The sampler's own label says
exactly this and I read past it:

> *"pair_if_maker is conditional on a maker fill that displayed depth does not
> guarantee."*

A passive maker price is an **offer to wait**. A taker ask is an **executable
fill**. Only the second is a completion you can rely on.

## 2. The correct side WAS placed, in time, at a qualifying price

This is what breaks the wrong-side thesis.

| window | qualifying maker window | complement actually placed | result |
|---|---|---|---|
| **1245z** | 12:48:32 → 12:48:59 (27s) | **UP `412c851a` at 12:48:42** — inside it | rested 2m, **did not fill**, cancelled 12:50:44 |
| **0130z** | ~01:32:56 → 01:33:03 (6.4s) | **UP `d2f217fb` at 01:33:03.366, own 0.31 → pair 0.93** | **did not fill** |

In both windows the engine quoted the **correct side**, at a price **inside the
0.96 ceiling**, during the **only interval where it qualified**. The wrong-side
`leg_to_place: DOWN` behaviour began at 12:50:54 — *after* no qualifying
complement existed by any route.

**So the wrong-side defect cost $0.00 in both windows.** It is real, it wastes
the remainder of the window, and it pins the engine to a dead plan — but it is
not why either window lost money.

## 3. Quote depth would not have helped either

The next candidate was resting closer to the touch. Direction kills it:

- **1245z**: the UP price rose **monotonically 0.65 → 0.99** from 12:48 to
  12:58. A resting UP **bid** fills when **sellers come down**. They never did.
  At the touch, 1 tick behind, or 2 — none of them fill into a market moving
  away.
- **0145z**: the price fell **back to** our resting bid, and it filled at own
  0.52 for **+$0.2028**.

**The discriminator between the strands and the profitable window is market
direction, which we cannot predict** — exactly what `L5_OPERATING_MODEL.md`
says we have no demonstrated skill at. It is not side selection and not depth.

## 4. What could actually have completed each window

| window | only completing route | available for |
|---|---|---|
| 1245z | **taker at 0.97–0.98** | ~27 s — **above the 0.96 cap** |
| 0130z | taker at 0.95–0.96 | ~6.4 s — at/inside the cap |
| 0145z | maker, no payment | the whole window; it paid |

And a maker complement never became reachable again in 1245z: 12:52:50 was the
best later moment and implied pair 0.98. The book did not come back.

## 5. Where that leaves the recommendation

**My earlier recommendation stands, and the wrong-side finding was a detour.**
That is two reversals in a row and I would rather state it flatly than
re-litigate it: the lever that completes these windows is a **separate, higher
ceiling for completing a residual than for opening a pair**, and at the
observed prices it needs a **taker** cross — which you have forbidden and which
I have not built.

The arithmetic from `L5_STRAND_ECONOMICS_1245Z.md` is unchanged and is the
whole case:

```
mean pair gain   +$0.1799      mean strand loss  -$0.8037      4.5 : 1
EV per opening   -$0.0168      breakeven strand rate 18.3% vs observed 20%
completing 1245z at 0.98: -$0.028   versus carrying it: -$0.448
```

## 6. The wrong-side defect is a LATENT cost worth fixing anyway

Not as the economic fix — as correctness. Consider a 0145z-shaped window where
the book moves away and then **comes back**: the engine would be quoting the
wrong side at the moment the opportunity returned, and would miss a completion
it could have had. In 1245z the book never came back, so the defect cost
nothing. That is luck, not safety.

The mechanism, confirmed in source at `l5_live_execution.py:4425`:

```python
for leg in ("UP", "DOWN"):
    if self.store.position(market, leg) != D(0):
        return "LEG_HOLDS_INVENTORY:%s" % leg
```

Per-market **aggregate** per-side inventory, not the round's own fills. With
`up 1.4 / dn 2.8` both sides are non-zero, so it returns on `UP`, the round can
never retire, and the frozen plan's stale leg assignment keeps driving the side
choice.

**What I would change, on your word** — and it is small:

1. derive the complement side and quantity from the **live signed residual**
   (realized ± outstanding, so partial fills and resting orders both count),
   rather than from the frozen round plan;
2. make `probe_round_is_safe_to_retire` consult the **round's own** fills
   instead of aggregate per-side inventory;
3. guard against a duplicate complement or a sign reversal by requiring the
   new order to strictly reduce `|prospective residual|` — the test
   `submission_class` already computes.

No new exposure, no taker, no ceiling change, no budget change, no change to
opening frequency, and inert in 0145z. Deterministic regression tests would
reproduce both frozen states, the cancellation, and the re-evaluation.

**But I am not claiming it recovers money.** On the measured evidence it
recovers **$0.00** of the $1.45 lost, and its value is preventing a loss that
has not yet happened.

## 7. What I did not do

I did not implement, deploy, or modify anything. I did not touch the running
campaign or its authorization. No new service, no strategy change, no capital
change.

**The decision I would like from you:** whether to spend the implementation on
the wrong-side defect as a correctness fix (my recommendation: yes, but
scheduled, not urgent), while the economic question — a completion ceiling
above the opening ceiling, which needs a taker path — stays open and
unauthorized.
