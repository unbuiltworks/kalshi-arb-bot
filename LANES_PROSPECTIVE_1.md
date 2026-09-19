# LANES_PROSPECTIVE_1 — the two running lanes, and the thing they could not see

**Scope.** BTC5m and BTC15m running continuously on the same frozen Model A
and the same `GEMINI_RECYCLE` adapter, compared only over **TTE ≤ 300s**.
Plus `EARLY_15M_ADAPTATION` as offline Model B research. No live
authorization; nothing here is submitted, filled, settled or realized.

**`quote_between_tte_s = [0, 300]` is untouched.** It is a measured L5
parameter in the frozen surface. `lanes.py` *reads* it — a test asserts the
module never assigns to it — and every comparison below is bounded by what it
returns.

---

## 1. The finding that matters most: the running lanes had no fill mechanism

`shadow_soak` never calls `Driver.place`. That is the safety property and it
is correct. But it also meant the window record was built with:

```python
"habitat": HB.window_summary(samples, rungs_placed=0, fills=(), ...)
```

— hard-coded — and the recovered `Inventory` was never touched after entry.
So both running lanes reported, and would have gone on reporting for ever:

> first fills **0**, complement fills **0**, matched **0**

beside a real, non-zero print count. **That reads as a market that declined
our quote. It was not.** It was a lane with no fill mechanism at all. Zero
was the absence of the measurement, not the result of it.

This matters directly to the standing instruction to surface the trigger —
`OPEN fill → inventory change → COMPLETE fill → matched inventory` —
the moment a running lane repeats it. **Neither lane could ever have produced
that sequence.** Waiting for it would have been waiting for something
structurally impossible, and the longer the lanes ran the more confidently
wrong the zero would have looked.

### What was added

`src/gemini_execution/hypothetical.py` — a hypothetical fill ledger that
drives the **same queue accounting `tape_replay` already uses offline**,
forward, against the live public tape:

- the displayed queue eats first (`Quote.take`);
- a rung is reached only by the **opposite** aggressor direction
  (`reaches`: a YES rung by a seller, a NO rung at `p` by a buyer at
  `≥ 1−p`);
- a price change loses queue position; the same price keeps the queue but
  takes the new action label;
- the policy is re-asked after **every** fill, so inventory moves before the
  next print is looked at.

Two ordering decisions, both of which could have manufactured fills:

1. **Prints are walked before the rungs are requoted.** Everything on a
   cycle's tape landed while the *previous* cycle's rungs were resting.
   Retargeting first would let a rung priced now catch a print from eight
   seconds ago.
2. **A re-fetched tape cannot fill the same rung twice.** The lane re-reads a
   rolling tape every cycle and most of it is old; prints are deduplicated by
   trade id, and an id-less print is admitted only if strictly newer than
   everything already walked.

It reaches no write path — asserted against the module's own AST, not by a
flag. Every row it emits carries `HYPOTHETICAL`, and every one is an **upper
bound**: Gemini serves no book history, size-ahead is the displayed size at
placement only, and our own presence would have changed the very flow being
replayed — which replay cannot settle in either direction.

### First live reading: the surface almost never holds its place

A smoke window run through the wired lane against the live venue
(`GEMI-BTC05M2609191540-UP`, entered TTE 278.2s, 8 cycles, COMPLETED):

```
rungs_placed 2   requotes 9   rungs_rested 11   fills 0
YES  0.35 → 0.41 → 0.42 → 0.42 → 0.44 → 0.54 → 0.56 → 0.57
NO   0.33 → 0.27 → 0.27 → 0.27 → 0.25 → 0.25 → 0.14 → 0.14
```

The frozen surface repriced on 9 of 16 leg-cycles. **Every reprice sends the
rung to the back of its level**, so a rung that rests eight cycles is not one
rung waiting — it is eleven rungs each waiting a few seconds. That is charged
here for the first time in a running lane, and it is why `rungs_rested`
(placed + requotes), not `rungs_placed`, is the denominator for fill
conversion: dividing by 2 instead of 11 would have flattered this lane five
and a half fold.

This is an observation about what Model A does. **Model A is not changed.**

---

## 2. Prospective TTE ≤ 300 comparison

24 expired contracts per cohort, venue tape only, restricted to the interval
Model A can actually quote in:

| | BTC5m | BTC15m |
|---|---|---|
| contracts | 24 | 24 |
| prints in reachable window | **113** | 47 |
| notional in reachable window | **$1,534.43** | $357.28 |
| prints per contract | **4.71** | 1.96 |
| notional per contract | **$63.93** | $14.89 |
| two-sided contracts | **16** | 8 |
| two-sided share | **0.667** | 0.333 |

**BTC5m wins every column Model A can reach**: 2.4× the prints, 4.3× the
notional, 2× the two-sided share. This is the same inversion found
retrospectively before the promotion was withdrawn, now measured
prospectively over a fresh set of contracts. The whole-window BTC15m
headline (630 prints vs 197, 0.667 vs 0.183 two-sided) is not wrong about
the venue — it is the wrong window for this policy.

**Both lanes stay up regardless.** This is a measurement, not a selection;
nothing here ranks a habitat or changes a lane.

---

## 3. EARLY_15M_ADAPTATION — offline Model B research only

Over 60 expired BTC15m contracts (56 computable), applying the same L5
mechanics in the **earlier** interval (TTE 300–900) that Model A does not
quote in:

| | |
|---|---|
| a first fill was available | **56 / 56** |
| opposite-direction flow followed it | 29 |
| flow reached a fee-safe complement price | **29 (51.8%)** |
| median time from first fill to that price | **124.6s** |
| EXTENDs suppressed by the adapter | 157 |

**The 0.99 pair basis in these rows is set by the replay, not discovered.**
`replay_early()` quotes the complement at `1 − held − tick`, so the basis is
0.99 **by construction on every match** — all 29 returned exactly 0.99.
Reporting "51.8% par-constrained matches" would have been reporting a
tautology as a finding. What survives is narrower and is what the module now
reports: *external flow reached a fee-safe complement price*, not *pairs cost
0.99*. `BASIS_IS_CONSTRUCTED` rides on every row and census so the caveat
cannot be dropped downstream.

It remains an **upper bound**: Gemini serves no book history, so this prices
from the tape and treats queue position as zero.

Model A is untouched by this. Nothing quotes earlier.

---

## 4. BTC15m on Railway

Service `gemini-maker-shadow` (`a15785aa-…`), deployment `6ebd2b34-…`,
commit `e75abb60`, `restartPolicyType: ALWAYS`, 50 GB volume at `/state`.
No new Railway service was created — a parked no-op was repurposed and its
prior configuration is recorded for restore.

Runtime evidence, from the service's own logs:

```
15:14:19Z  Starting Container
15:14:19Z  COHORT BTC15m prefix=BTC15M grid=15m band=(240.0, 280.0) policy=GEMINI_RECYCLE
15:29:43Z  WINDOW GEMI-BTC15M2609191530-UP tte=279.845351934433 cycles=29 -> COMPLETED
```

Discovery → wait → band entry at **TTE 279.8s**, inside the 240–280 band and
inside Model A's quoting window → 29 cycles → terminal `COMPLETED`.

### Automatic rollover, proven on the BTC5m lane

The BTC5m lane has rolled from one contract to the next three times without
intervention, each entry inside the same 240–280 band:

| symbol | entry TTE | cycles | terminal |
|---|---|---|---|
| `GEMI-BTC05M2609191525-UP` | 275.6s | 23 | COMPLETED |
| `GEMI-BTC05M2609191530-UP` | 279.7s | 26 | COMPLETED |
| `GEMI-BTC05M2609191535-UP` | 271.4s | 24 | COMPLETED |

Same loop, same `--windows 0`, same cohort parameterisation as BTC15m —
`contract_cohort.py` supplies the family and nothing else differs — so the
rollover path is the one BTC15m runs. BTC15m's own second window is not yet
in the log at the time of writing; it is expected roughly every 15 minutes
and the chain is not claimed proven on that lane until a second symbol
appears there.

---

## 4b. The fourth axis: BTC15m's book is unreadable most of the time

Measured from the running lanes' own cycles at TTE <= 300. `CP.crossed()`
refuses a book whose bid is at or above its own ask; it is a READABILITY
gate, not a pair-cost ceiling, and a refused cycle rests no rung.

| | cycles | readable | refused |
|---|---|---|---|
| BTC5m (5 windows) | 125 | 76 | **0.392** |
| BTC15m (`…1615-UP`) | 29 | 8 | **0.724** |

The BTC15m window before it rested **0** rungs across 30 cycles, consistent
with a book that was crossed throughout. So BTC15m loses on a fourth measure
Model A can reach, after prints, notional and two-sidedness: the policy
cannot read its book roughly three cycles in four.

Rested rungs track readability closely — BTC5m 33/24, 19/14, 24/23, 14/11,
6/4 (rested/readable) — which is what it should look like when a readable
cycle yields up to two legs and an unchanged price keeps its rung.

### CORRECTION: 72.4% was one window, not a property

Three more BTC15m windows have since run, and the readability figure does not
hold:

| window | cycles | readable | share | rested | fills |
|---|---|---|---|---|---|
| `…1615-UP` | 29 | 8 | 0.28 | 13 | 0 |
| `…1630-UP` | 30 | 25 | 0.83 | **0** | 0 |
| `…1645-UP` | 29 | 29 | **1.00** | 36 | **1** |
| `…1700-UP` | 30 | 29 | 0.97 | 39 | 0 |
| **total** | **118** | **91** | **0.771** | 88 | 1 |

BTC15m is **22.9% refused** across four windows, not 72.4% — and that is
*better* than BTC5m's 39.2%, the opposite of what I reported. The 8/29 window
was the outlier I happened to measure first. I over-generalised from a single
window, which is the same error the probe made two paragraphs above, made
again with a different instrument.

**And a readable book does not imply a rung.** `…1630-UP` had 25 of 30 cycles
readable and rested **zero** rungs. Crossing cannot explain that window, so
something else withheld the surface there and I do not yet know what. The
`books=` field says what it says; it does not say why the policy declined a
book it could read.

What survives from the crossing work: crossing is real, it is heavy in some
windows, and it fully explains zero-rung windows like `…1600-UP`. What does
not survive: the claim that BTC15m is structurally less readable than BTC5m.

### Two corrections to how I measured this

I ran a public-read probe to test the crossing hypothesis and **got the
answer wrong twice** before the running lane settled it.

1. **It checked the wrong gate.** The probe read
   `MD.outcome_book(...)["readable"]`, which is whether the book FETCHED.
   `CP.crossed()` is a separate check applied afterwards. The probe reported
   "61/61 and 12/12 readable, zero refusals" and had not tested the
   hypothesis at all.
2. **Recomputing crossing directly from the same samples, it was
   under-powered and mistimed.** It gave BTC5m 28/61 (0.459, close to the
   0.392 the lane recorded) but BTC15m **0/12**, and I read that as ruling
   the explanation out. Its BTC15m samples span TTE **300 -> 215 only** — one
   contract, twelve reads, ending before the late window. The lane's own 29
   cycles span **274 -> 30** and are 72.4% refused. Crossing concentrates
   near expiry, and the probe stopped before it.

The original hypothesis was right; the probe was wrong. The number to trust
is the running lane's, because it is the one measuring the cycles the policy
actually lived through.

---

## 5. What is still not evidenced

- The ledger above has not yet produced a single **prospective** fill. It was
  written this cycle; the numbers in §2 are venue flow, not lane fills.
- **The BTC15m lane's structured evidence is on a Railway volume this
  container cannot read.** Its per-window log line proves the operational
  chain; the 13-column lane half is reported `UNREADABLE_FROM_HERE` rather
  than as zero.
- No live authorization is requested and none is implied.
