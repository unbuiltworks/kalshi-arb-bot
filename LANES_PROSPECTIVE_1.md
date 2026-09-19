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

---

## 5. What is still not evidenced

- The ledger above has not yet produced a single **prospective** fill. It was
  written this cycle; the numbers in §2 are venue flow, not lane fills.
- **The BTC15m lane's structured evidence is on a Railway volume this
  container cannot read.** Its per-window log line proves the operational
  chain; the 13-column lane half is reported `UNREADABLE_FROM_HERE` rather
  than as zero.
- No live authorization is requested and none is implied.
