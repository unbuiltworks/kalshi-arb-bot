# Snapshot 1: 1 complete window of 12 required, and the blocker is the strike

Census: `analysis/gemini/updown/WINDOW_CENSUS_SNAPSHOT1.json`.
Tape: `analysis/gemini/btc5m_snap/`, 31,570 rows, 4,983 reference
observations, 53 BTC5m tickers seen, 75-minute horizon.

## THE COUNT, FIRST: 1 GENUINELY COMPLETE WINDOW

T1 is **not** runnable. Neither frozen analyzer was run, no OOS was
inspected, and no T1 number is reported.

| missing requirement | tickers |
|---|---|
| **no authoritative strike** | **51 of 53** |
| fewer than 6 early checkpoints | 36 |
| fewer than 6 executable books | 36 |
| fewer than 6 fresh reference observations | 36 |
| no settlement | 1 |

## The early grid works. The strike capture does not.

Two facts separate the diagnosis cleanly:

* **17 tickers have >=6 early checkpoints** (16 with nine, one with eight),
  each carrying executable books and fresh reference coverage. The
  T+15..T+180 collection the preregistration needs is working.
* **Only 2 of 31 tickers ever recorded a non-null `strike`.** Each ticker
  receives just **1 or 2 `window` rows in its whole life** (17 tickers got
  one, 14 got two), and those rows carry `strike_type: "reference"` and a
  populated `availableAt` with `strike: null`.

The strike publishes AT `availableAt`. The recorder captures the contract
shape when it first discovers the window -- which for a BTC5m window is
typically before `availableAt` -- and does not re-record it afterwards, so
the field it needs is read exactly when the venue has not yet set it. The
two that did capture a strike are the two whose `window` row happened to be
taken after their `availableAt` (…1750 captured 85320.36 ~7 min after;
…1800 captured 85359.29 ~5 min after).

This is a **recorder data-capture gap, not a venue limitation**. The strike
is published and readable: cohort 1's settlement audit read it from the
contract object for all five windows, and the live underdog cohort's own
discovery sees `strike_type: reference` on every window. Collecting for
longer does not fix it -- recorder run 1, still collecting to ~23:42Z, is
running the same code and will have the same gap.

## Why this blocks one study and not the other

The chained-D study `b2f6d50a...142f9bc6` is anchored on
`D_t = reference(t) - strike`, so a missing authoritative strike blocks it
outright. The early-window analyzer `f2800e3e...960e90b` uses book state and
reference trajectory and is less dependent on it, but the census gate is
defined across all five requirements and is not being redefined to let a
run through.

## One recovery route exists, and I am not taking it unilaterally

The tape carries **52 BTC5m settlement values with 51 consecutive pairs**,
and the chaining mechanism (next strike = previous window's
settlementValue) is confirmed to the cent on 4 consecutive windows in the
cohort-1 audit and 4 of 4 independently in the recorder tape. So the strike
for nearly every window is **reconstructible** from the previous window's
settlement.

But that is an **inferred** strike, and the census requires an authoritative
one -- "never inferred" is in its definition for exactly this reason. Using
the chaining relation to fill in the causal state of a study whose question
is partly about that relation also risks circularity. Two defensible options,
both yours to pick:

1. **Fix the capture** -- have the recorder re-read the `window` row after
   `availableAt`. Minimal change, but it requires restarting the recorder,
   which is forbidden while run 1 collects, and implementation is frozen.
2. **Preregister the chaining fallback explicitly** -- allow
   `strike := previous window's settlementValue` as a *labelled, non-
   authoritative* substitute, with every window using it flagged, and the
   chained study's own chaining check reported separately so the circularity
   stays visible.

Doing nothing also has a cost: run 1's ~100 windows will land at ~23:42Z
with the same strike gap.

## Also from this tape, as mechanism

The 1 complete window is not a sample and nothing is computed from it. The
early-checkpoint and reference coverage being healthy (4,983 reference
observations across 75 minutes, 9 early checkpoints on 16 of 17 tickers)
means the collection pipeline itself is sound -- the gap is one field.
