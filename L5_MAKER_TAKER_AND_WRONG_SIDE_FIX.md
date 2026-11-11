# L5 — maker/taker execution evidence, the TTL question, and the wrong-side fix

Fix implemented and tested. **Nothing deployed. The live campaign and its
authorization are untouched. No taker path exists. No budget changed.**

---

## PART 1 — Is L5 a maker or a taker?

### Method

Two venue-API pulls with explicit execution-side filters:
`data/l5/raw/trades.jsonl` (`takerOnly: "false"`) and
`trades_takeronly.jsonl` (`takerOnly: "true"`). This is **execution-side
evidence from the venue**, not inferred from rebate counts — and it corrects my
earlier rebate-based suspicion, which was wrong.

**Matched overlap: 2026-08-30 07:49 → 20:52 UTC (13.1 h), 312 markets,
10,457 unique trades.**

First I established what `takerOnly=false` returns: **2,001 of the 2,009 taker
rows fall inside it**, so it is *all* trades and taker is a strict subset. (The
8 exceptions are window-boundary artefacts.)

### 1. Overall split

| | unique trades | share |
|---|---|---|
| **maker** | 8,448 | **80.8%** |
| **taker** | 2,009 | **19.2%** |

**L5 is predominantly a maker.**

### 2. It changes dramatically by action type

| action | n | taker share |
|---|---|---|
| **OPEN from flat** | 321 | **87.2%** |
| EXTEND | 5,642 | 18.2% |
| **COMPLETE** | 4,537 | **15.5%** |

**He crosses to get IN, then rests to work the position.**

### 3. Does he cross to complete residual inventory?

**Yes, but it is his least taker-heavy action — 15.5%, about one completion in
six and a half.** He completes as a maker ~85% of the time.

**This contradicts the taker-completion change I kept recommending.** Completion
is precisely where L5 does *not* pay the spread.

### 4. How it varies with state

By **imbalance already held** — monotonic, and the strongest signal found:

| imbalance held | n | taker share |
|---|---|---|
| 0 | 321 | **87.2%** |
| 1–50 | 1,941 | 33.7% |
| 51–200 | 4,723 | 17.3% |
| >200 | 3,515 | **7.2%** |

By **price** — U-shaped, cheapest prices most patient:

| price | taker share |
|---|---|
| 0.0 | 21.2% |
| 0.1 | 10.8% |
| **0.2** | **8.4%** (minimum) |
| 0.5 | 22.2% |
| 0.8 | 30.0% |
| **0.9** | **36.6%** (maximum) |

He pays to act when a position is near resolution, and waits when it is cheap
and there is time. Volatility was not separable from this sample.

### 5. Is the high-frequency buying taker or maker?

**Maker — 80.8%.** And the arrival rate settles it:

| | n | median gap | p25 | p75 |
|---|---|---|---|---|
| between **maker** fills | 8,443 | **2 s** | 0 s | 5 s |
| between taker fills | 1,694 | 16 s | 3 s | 42 s |

His maker fills arrive every **~2 seconds**. The frequency is maker flow, not
crossing.

### Against our engine

| | L5 | our BTC15m engine |
|---|---|---|
| open from flat | **87.2% taker** | **maker-only** |
| extend | 18.2% taker | maker-only |
| complete | 15.5% taker | maker-only |
| maker fill cadence | **~2 s** | 122 s and no fill |

**The gap is at the opening, not the completion.** And that matters
mechanically: a *resting* opening leg is adversely selected — our UP bid fills
when UP is falling, our DOWN offer fills when DOWN is falling, so the leg that
fills is systematically the one about to be wrong. That is precisely the shape
of both strands: 1245z DOWN filled at own 0.38 and UP then ran away; 0130z DOWN
filled at own 0.62 and UP ran away.

**So the L5-faithful change is to cross to OPEN, not to cross to complete** —
the opposite of what I have been proposing. It needs taker authorization, which
is forbidden, and I have not built it.

### Uncertainty, stated plainly

- **One 13.1-hour day.** Not seasonally representative; a second matched window
  on a different date would be the first thing to check.
- `OPEN from flat` is the smallest cell at **n=321**; 87.2% carries roughly
  ±4 points.
- `takerOnly=false` = all trades is an **inference from the 99.6% subset
  overlap** — strong, but an inference about API semantics.
- The matched window truncates markets at its edges, so per-market action counts
  from it (median 6) understate. The full-corpus figure stands: **5,412 of 5,503
  markets with 10+ buys**.

---

## PART 2 — Is the 120 s maker TTL destroying queue priority?

**No — it is not the binding constraint, and lengthening it would not have
helped.**

Our order `412c851a` rested **122 s** at own **0.62** while the best bid was
**0.64** — two ticks behind — with **`queue_ahead: 5023.03`** contracts in front
of a 1.4-contract order, and **494** at its own level.

Against that, L5's median maker-fill gap is **2 seconds**. We had **60×** the
time his fills typically need. Duration was never the constraint; **price and
queue position were.**

The TTL does have a real second-order cost — cancel-and-replace resets priority,
so a shorter TTL is strictly worse than a longer one *at the same price*. But it
is dominated by being two ticks back. **Recommendation: do not change the TTL;
it is not where the loss is.**

---

## PART 3 — The wrong-side fix (implemented)

### Exact diff — `src/l5_live_execution.py`, 68 insertions, 1 deletion

Two new methods and two call sites. No other file changed.

**`probe_leg_order(market, legs, cheapest_first)`** — the side now comes from
the **prospective signed residual** (`store.residual` + `store.outstanding_
residual`), so filled, partially filled, pending and resting orders all count.
The reducing side goes first; cheapest-first is returned unchanged when the
prospective residual is zero, and on any read error.

```python
order = sorted(legs, key=lambda k: D(legs[k]["price"]))
+ order = self.probe_leg_order(market, legs, order)
  nxt = next((k for k in order if not claimed.get(k)), None)
```

**`probe_leg_qty_cap(market, side, qty)`** — a reducing leg is capped at
`abs(prospective residual)`, so 1.4 offered against 0.5 held can never flip the
book to 0.9 the other way. Only ever shrinks; never touches an opening leg.

```python
- executable = D(leg["qty"])
+ executable = self.probe_leg_qty_cap(market, nxt, D(leg["qty"]))
```

### What I deliberately did NOT change

**Round retirement.** You asked for it to evaluate round-owned fills rather than
aggregate per-side inventory. I left `probe_round_is_safe_to_retire` alone. It
is a *protective* predicate — "an unknown is not a zero" — and making it
round-scoped would permit abandoning a round that still holds inventory. The
side-selection fix removes the harm without touching it: the engine now quotes
the reducing side regardless of what the frozen plan thinks. **Loosening a
safety gate to tidy a symptom is the wrong trade, so I am flagging it rather
than doing it.**

### Regression tests — `tests/test_l5_wrong_side_residual.py`, 22 tests

Reproduce both failing states exactly:

- **1245z**: `up 1.4 / dn 2.8` → residual −1.4 → must offer **UP**
- **0130z**: `up 0 / dn 1.87` → residual −1.87 → must offer **UP**
- a price-flipped variant proving cheapest-first alone would pick the wrong side
- **0145z unchanged**: `dn 1.56` → offers **UP**, which is what the engine
  actually did, and the 1.56 complement is **not** shrunk — the profitable
  5m23s wait is undisturbed
- resting complement counted (not ordered twice); partial fill leaves only the
  remainder to reduce; under-sized resting complement still asks for UP
- overshoot capped 1.4 → 0.5; opening leg never capped; cap only shrinks
- unreadable residual keeps today's rule rather than halting
- both legs always returned exactly once; call sites wired (structural)

### Two existing tests updated, with the reason recorded in each

Both in `test_l5_two_sided_wiring.py`, same root cause, and I verified with
instrumentation rather than assuming:

1. `test_accumulation_keeps_working_pairs_past_the_first` asserted a rigid
   `["DOWN","UP"]` alternation. Measured prospective residual across those
   rounds: **−3, then −2, then −1**. The old rule opened **another DOWN leg**
   on top of a DOWN-heavy book each time — the exact shape that stranded 0130z
   and 1245z. Now asserts equal UP/DOWN counts and cheapest-first on the first
   leg from flat.
2. `test_restart_recovery_cannot_replace_a_claimed_leg` expected `["DOWN"]`.
   Measured: `res=0, out=-2` — realized inventory balanced, but the resting
   remainders **DOWN-heavy by 2**, so UP is the reducing side. The claim
   behaviour the test exists to pin is unchanged.

### Regression: no new failures

| | baseline | with the fix |
|---|---|---|
| failed | 58 | **58** |
| errors | 23 | **23** |
| passed | 13,268 | **13,351** |

Directly-affected suites: `test_l5_two_sided_probe`, `test_l5_e2e_window`,
`test_l5_gates_cannot_vanish`, `test_l5_presend_cross_recheck`,
`test_l5_opening_notional_cap`, `test_l5_notional_cap_cannot_stack` —
**232 passed**.

### Preserved, verified by the untouched suites

maker-only · 0.96 pair ceiling · $0.80 opening-notional cap · $2 deficit gate ·
$5 exposure · $5 campaign loss · 8-window max · submission budgets ·
independent watchdog · all automatic halts.

### Deployment plan — for your approval, not executed

1. The running campaign finishes on its own terms (max 8 windows, deadline
   15:15Z). **Nothing is deployed mid-flight.**
2. After it disarms, deploy at a window boundary with `L5_SHADOW_ONLY=1` and
   confirm the verifier still reads `PASS: true, checked: 26`.
3. Observe one shadow window and confirm, from `L5_LIVE_DECISION`, that
   `leg_to_place` tracks the signed residual.
4. Re-arm only with a fresh experiment id, approval payload and deadline.

### What this fix is and is not

**It recovers $0.00 of the $1.45 lost.** In both windows the correct side was
quoted once, inside the only interval where it priced under the ceiling, and did
not fill. Its value is the window where the book leaves and **comes back** —
there the old code would be pointing the wrong way when the opportunity
returned. That is a latent loss, not a realized one.

**The economic bottleneck remains open** and the maker/taker evidence has moved
where it points: from crossing to complete, to **crossing to open**. Both need
authorization neither has.

---

## PART 4 — Campaign 2 reconciled, and it confirms the diagnosis

The campaign finished on its own: **8/8 windows spent, OPEN budget 16/16
exhausted**, 25 of 56 submissions used, flat at close, no halt
(`would_halt: false`, $3.67 of daily room unused).

### Settled, per window — venue `VENUE_SETTLEMENT_PX`, nothing modelled

| window | matched | pair P&L | residual | residual P&L | **settled net** |
|---|---|---|---|---|---|
| 1245z | 1.40 | +0.1540 | 1.40 DOWN | −0.4480 | **−0.2940** |
| 1300z | 0 | 0 | 1.26 UP | −0.7938 | **−0.7938** |
| 1315z | 1.63 | +0.0815 | — | 0 | **+0.0815** |
| 1330z | 1.29 | +0.4858 | 0.93 DOWN | −0.2728 | **+0.2130** |
| 1345z | 1.50 | +0.0903 | 1.56 UP | −0.8109 | **−0.7206** |
| 1400z | 1.11 | +0.0555 | — | 0 | **+0.0555** |
| 1415z | no fills | — | — | — | 0 |
| 1430z | flat, OPEN exhausted | — | — | — | 0 |
| **total** | | **+0.8671** | | **−2.3255** | **−1.4584** |

Chain-verified against the venue `realized_today_after` ledger at every step:
start of UTC day **−11.62130** → earlier windows **+0.1256** → this campaign
**−1.4584** → **−12.95410**, and `utc_day_realized_usd: −1.332800`. Exact.

### The finding

**Matched-pair economics are positive: +$0.8671 across six traded windows.
Stranded residual cost −$2.3255 — 2.68× the pair profit. Stranding is the
entire deficit, and four of six traded windows stranded.**

**The pair strategy works. The entry mechanism loses more than the pairs make.**

### Independently confirmed at 453-window scale

The shadow controller, running continuously beside the money path (**shadow, not
realized**), reports the same shape on a vastly larger sample:

| | live, 6 windows | shadow, **453 settled windows** |
|---|---|---|
| pair net | **+$0.8671** | **+$73.4802** |
| residual net | **−$2.3255** | **−$149.1802** |
| net | −$1.4584 | **−$77.4100** |
| residual ÷ pair | **2.68×** | **2.03×** |
| net per settled window | −$0.2431 | **−$0.1709** |

And it names the mechanism outright:

- **fill rate 0.8192** — our quotes do fill
- **adverse rate 0.7754** — **1,219 of 1,572 fills were adverse, $89.10**
- all three scenarios (CONSERVATIVE/BASE/OPTIMISTIC) land within $0.33 of each
  other, so this is not a fill-model artefact
- `possible_misses: 0` against 117,937 justified refusals — **we are not losing
  money by being too cautious.** Every refusal protected money or admitted
  ignorance.

**Three quarters of our maker fills are adverse.** That is what a resting
opening leg *is*: it fills when the market is coming to take it, which is when
it is about to be wrong. The $0.80 cap scales the loss down; it does not change
the sign.

### This is the same fact as the L5 evidence, from the other end

L5 opens **87.2% taker**. He does not rest an opening leg to be adversely
selected — he pays the spread to choose his moment, then rests to work the
position (18.2% extend, 15.5% complete). Our engine rests **100%** of its
openings. The adverse rate is the price of that difference.

**So the single highest-impact change is the entry mechanism, and the evidence
now agrees from three directions:** L5's own 87.2%, our 77.5% adverse rate over
1,572 shadow fills, and −$2.33 of live stranding against +$0.87 of working pair
economics.

It requires taker authorization at entry. **I have not built it and am not
asking to deploy it** — the next step is a replay of taker-at-entry against the
453 shadow windows, which needs no authorization and no capital.

### What this does to the other candidates

- **Raise the completion ceiling** — superseded. L5 completes as a maker 84.5%
  of the time. Completion is not where he pays.
- **Change the 120 s TTL** — not the constraint. Confirmed in Part 2.
- **Open less often** — contradicted. `possible_misses: 0`, and pair economics
  are positive; fewer openings shrink both sides.
- **The wrong-side fix** — keep it, but for the right reason. It recovered
  $0.00 in the two measured windows, yet the shadow shows
  **`already_resting_opposite_side: 1,745` across 46 windows** where the
  controller wanted the side it was not resting. The state recurs. It is
  correctness with a real forward-looking frequency, not a revenue change.
