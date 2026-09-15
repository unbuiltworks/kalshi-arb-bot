# L5_CHAIN_OBSERVER cross-validation — the gate, and a bug it caught in itself

**Status: NOT PROMOTED.** `observer.PROMOTED` is `False` and is flipped by a
human. This records evidence toward that decision and nothing else: the
observer is connected to no execution path, no FIDELITY input and no order
verb.

## Why this matters now

Observation lag is **second on the measured blocker list**. Median intent age
at decision is **17.6 s against a 30 s validity** — 59 % of an intent's life is
already spent before we can price anything. The chain carried the same fill
~19.6 s earlier than `/activity` and won 10 of 10 joined observations. A ~4 s
route instead of a ~15 s one would roughly triple usable intent life.

None of which counts if the two sources disagree about **what happened**. A
faster feed that misses fills is worse than a slow one, because a missed fill
is indistinguishable from L5 not trading — the engine would read an absence as
a decision.

## The test

Joined on `transactionHash`, over a **settled window**: 900 s of history ending
300 s in the past, against a measured p95 publication lag of ~30 s — a 10×
cushion, so one slow publish cannot masquerade as a chain miss.

| set | meaning | gate |
|---|---|---|
| `chain_only` | the chain saw it before publication | **expected, benign** |
| `feed_only` | a fill the chain **missed** | **must be empty** |

## The bug this caught — in the validator, not the observer

The second pass reported `feed_only: 1` — *"the chain MISSED 1 fill the venue
published"*. It had not.

```
queried blocks : 93850180 (13:51:43) .. 93850780 (14:06:43)
missed tx block: 93850179  (13:51:42)   <- one block BELOW the range
time window    : 13:51:41 .. 14:06:41   <- but its timestamp is INSIDE
```

The transaction carried L5 at topic positions 2 and 3 on a known exchange
address, so the scan would have found it — **it was never asked about that
block**. My `block_at` accepted any block within one block-time *after* the
target and used it for both bounds, despite a docstring promising "at or
before". The low bound therefore opened *inside* the time window and excluded
real fills beneath it.

Fixed: `block_at` takes a `side` (`floor` / `ceil`) with a block of slack each
way, so the **block range strictly contains the time window**. Excess is
harmless — it can only produce `chain_only`. Pinned by four regression tests,
including the live case.

Had I re-run until it came back clean instead of investigating, a false
promotion blocker would have stood as a fact about the chain.

## A metric that was measuring nothing

The first version reported a chain "lead" of **0.0 s** across 40 shared fills —
p05, p50, p95 and max all zero. That is not "no lead": Polymarket's activity
feed publishes the **on-chain event time**, so I was differencing one clock
against itself and the answer is zero by construction.

Removed and replaced with a function that says so. Observational lead — when
each surface could first be *read* — belongs to the first-seen probe
(`analysis/l5/latency/chainwatch.py`), which measured ~19.6 s. A settled-window
reconciliation runs every two minutes and structurally cannot see first
arrival, so it no longer pretends to.

## Evidence so far

| | |
|---|---|
| settled-window passes | 4 |
| clean (`feed_only` empty, no blocker) | **3** |
| the one failure | the pre-fix pass above, root-caused to the validator |
| agreement rate | 0.973–0.990 |
| fills per 15-min window | 188–193 |

**Three clean passes is not a promotion test.** The gate is continuous and
accumulating; promotion needs a long run of clean settled windows, and the
decision is the owner's.
