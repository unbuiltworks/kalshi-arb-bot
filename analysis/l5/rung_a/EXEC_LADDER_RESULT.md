# Execution attribution ladder — result

`src/pmus_exec_ladder.py` starts from `pmus_native_parity.replay_window` and
attaches a lifecycle observer, then moves one execution responsibility per
rung. Rung 0 reproduces the frozen baseline **by construction**, and is
asserted window-by-window, not just on the mean.

No rejection probability is modelled anywhere. R4 is n=1; `reject_rule`
defaults to None and no rung turns it on.

| rung | mean net $/win | comps | maker fills | taker fills | matched | residual | fees | Δ prev | Δ frozen |
|---|---|---|---|---|---|---|---|---|---|
| **R0 frozen control** | **6.116725359099999999999999992** | 44 | 136 | 0 | 8.3433683809 | −1.1529523809 | 1.07369064 | — | **0** |
| R1 canonical lifecycle | 6.116725359099999999999999992 | 44 | 136 | 0 | 8.3433683809 | −1.1529523809 | 1.07369064 | **0** | 0 |
| **R2 one fill per real order** | **5.922062959099999999999999992** | 43 | 127 | 0 | 8.3324160000 | −1.3420 | 1.06835304 | **−0.19466240** | −0.19466240 |
| R3 maker/taker semantics | 5.922062959099999999999999992 | 43 | 127 | 47 | 8.3324160000 | −1.3420 | 1.06835304 | **0** | −0.19466240 |
| R4 measured latency | 5.922062959099999999999999992 | 43 | 127 | 47 | 8.3324160000 | −1.3420 | 1.06835304 | **0** | −0.19466240 |
| R5 terminal/reject/partial | 5.922062959099999999999999992 | 43 | 127 | 47 | 8.3324160000 | −1.3420 | 1.06835304 | **0** | −0.19466240 |
| **R6 production accounting** | **5.922062959099999999999999992** | 43 | 127 | 47 | 8.3324160000 | −1.3420 | 1.06835304 | **0** | −0.19466240 |

## The headline

**Execution-faithful result: $5.9220629591/window — above the ≥$5 target.**

It lands exactly on the conservative shadow comparator, which is a real
convergence rather than a coincidence: removing the unsupported refills and
requiring one fill per real order are the same constraint reached two ways.

**Exactly one rung costs anything.** R1, R3, R4, R5 and R6 are all zero:

* **R1 = 0** means the own-terms round trip through
  `pmus_order_lifecycle.classify` on the venue's own record shape is lossless.
  Production reads fills the way the replay does.
* **R3 = 0** means maker/taker semantics cost nothing; the 47 taker completion
  legs become real orders and still fill at the walked ladder cost.
* **R4 = 0** means one-observation fill latency costs nothing at this tape's
  cadence. R4's measured 10.701 s submit→fill is about one observation here, so
  latency is represented as exactly that and no more.
* **R5 = 0** and **R6 = 0** mean terminal classification, reservation release
  and registry accounting are bookkeeping: they change what we *know*, not what
  fills.

## Lost completions — all 13, one reason

`NO_LIVE_ORDER_AT_THIS_PRICE`: frozen refills a notional clip whose order has
already filled in full, and the engine emitted no re-quote in between.

| window | ticks | side / price |
|---|---|---|
| 2345z (3) | 39, 53, 57 | down 0.02; up 0.99 ×2 |
| 0030z (8) | 29, 31, 33, 35, 37, 47, 51, 55 | down 0.98 ×4, up 0.01, down 0.99 ×3 |
| 0115z (1) | 50 | up 0.98 |
| 0230z (1) | 27 | down 0.94 |

Every one is at an extreme price (0.01, 0.02, 0.94, 0.98, 0.99). Per-window
effect: 0030z 3.0000 → 0.0000, 0115z 5.7702 → 4.7702, 0230z −0.4518 → −1.3184
(and the one lost completion), 2345z unchanged to 7 d.p. The 13 events exceed
the 10 the static audit found because suppressing a fill changes the states
that follow.

## Remaining uncertainties, stated

1. **`CSDKDZZVRYAQ`'s rejection cause is UNKNOWN** and no rejection rate is
   modelled. If post-only refusal at a one-tick touch is systematic, it would
   reduce this figure, and the ladder cannot say by how much from n=1.
2. **Throughput is not demonstrated.** The 3.18% the refills represent is NOT
   assumed recoverable. Reclaiming it needs several sequential orders inside a
   few ticks (0030z would need eight), which no live run has shown.
3. **Fees stay where frozen puts them** (maker fills booked with `fee=None`), so
   the ladder isolates execution and does not move the fee model.
4. **One live authenticated fill exists** (R4: 10 UP @ 0.26). q10 is still
   9.42× below the frozen clip of 50, and the ladder is computed at clip 50.
