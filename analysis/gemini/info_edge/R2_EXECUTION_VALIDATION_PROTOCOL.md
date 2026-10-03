# R2 execution-validation protocol — PREPARED, NOT ARMED

**Nothing is armed. No order will be submitted until the owner authorizes this
document explicitly.** `arming.EXPECTED_AUTH_ID` currently holds the SPENT
`OWNER-2026-10-03-GEMINI-SEMANTICS-1`; the gate workflow's armed step would
return `STOPPED_NO_LATCH`.

## What this is, and what it is not

**It is NOT a profitability estimate for R2.** R2's profitability is decided
by the sealed statistical gate at **100 independent prospective R2 windows**
(58 to go as of this writing), and these probes are deliberately far too few
to say anything about edge.

**It IS an execution-model audit.** It answers one question: *when our model
says an R2 opportunity is executable at a frozen price and depth, what does
the venue actually do?* Seven measurements:

| # | measurement | modelled | actual |
|---|---|---|---|
| 1 | executable price | frozen pre-submit price | `avgExecutionPrice` |
| 2 | obtainable quantity | displayed depth at/inside the limit | `filledQuantity` |
| 3 | signal-to-submit latency | — | signal ts → ack ts |
| 4 | qualification-to-fill rate | qualifying opportunities | filled orders |
| 5 | refusals / vanishing liquidity | — | `MISSED` / `NOT_EXECUTABLE` counts |
| 6 | fees actually charged | `ceil(0.07·C·P·(1−P), ¢)` | whatever the account reports |
| 7 | settlement reconciliation | payout from resolution side | authenticated positions → flat |

Measurement 6 is the one most likely to come back empty: the venue's order
surface carries **no fee field** (established by SEMANTICS-1), so a confirmed
fee needs a cashflow or ledger surface we have not yet located. If it stays
unobservable, that is a finding, not a gap to fill with the formula.

## Mechanical implementation of frozen R2 — no additions

The live path evaluates **exactly** the rule frozen at
`6bc5c1f730dd0d4639eb4c9459803dbf9d68dee5` (fingerprint
`64b178378806b5c257b58b29d0ba49da`), in this order:

```
1  POST-STRIKE      observation time >= contract strike.availableAt   (CHECKED)
2  FAVORED SIDE     UP if coinbase - strike > 0 else DOWN
3  |z| >= 1.0       z = |coinbase - strike| / (sigma_1s * sqrt(TTE) * coinbase)
                    sigma_1s = sd of per-second Coinbase log returns over the
                    preceding 300 s, >= 20 consecutive-second returns required
4  EXECUTABLE       favoured UP   -> best ASK exists
                    favoured DOWN -> best BID exists (price = 1 - bid)
5  FREEZE           record price, size, depth at/inside that price
6  RE-READ          immediately before submit: an offer must still sit at or
                    below the frozen price with >= 1 contract of displayed
                    size at or below it
7  SUBMIT           ONE limit BUY, q=1, limit = the frozen executable price,
                    makerOrCancel false (the proven taker path)
8  RECORD           the seven measurements, then settle and reconcile
```

**No additional predictive filter. No threshold optimization. No TTE
selection.** `z >= 1.0` is the trigger; 0.75, 1.25 and 1.50 do not appear in
the live path at all. Depth is **measured**, never used to qualify.

**One opportunity per independent market.** A ticker that has produced a probe
is added to `probe_windows.json` and can never produce another.

**Refused, structurally:** EXTEND, FLIP, complement, averaging, chasing,
automatic repricing, any second order in a market, any substitute price. If
the qualifying offer is gone at step 6, the probe records
**`MISSED / NOT_EXECUTABLE`** with the full book state and **stops**. That
refusal is a measurement, not a failure.

## Risk bounds

| bound | value | basis |
|---|---|---|
| quantity | **q = 1** | fixed, never sized |
| probes | **10**, one per distinct market | enough to estimate a fill rate to ±~15pp; too few for any edge claim |
| max loss per probe | **$1.02** | worst case price 1.00 + fee 0.02 |
| structural worst-case aggregate | **$10.20** | 10 × $1.02, if every probe fills and every one loses |
| **hard stop: cumulative realized loss** | **$8.00** | stops before the structural max; realized means settled cashflow, not marks |
| max concurrent orders | **1** | checked against the venue, not our bookkeeping |
| per-market cash cap | **$5.00** | unchanged from SEMANTICS-1 |

Expected cost if R2's prospective numbers hold (86.2% correct, mean price
0.810): roughly **−$0.4 net over 10 probes**. The $8.00 stop exists for the
case where they do not.

## Authorization mechanics

One authorization, one probe — the same discipline as SEMANTICS-1, not a
batch licence:

1. Owner grants an identifier, e.g. `OWNER-<date>-GEMINI-R2-EXEC-1`.
2. It goes into **both** `arming.EXPECTED_AUTH_ID` and the workflow step's
   `GEMINI_CANARY_AUTH`, with a fresh `GEMINI_CANARY_AUTH_EXPIRES`.
3. `transport.place(allow_taker=True)` additionally requires the live
   authorization to equal `payload.SEMANTICS_PROBE_AUTH_ID`, so that constant
   must be moved to the new id in the same commit. Until then the taker path
   is unreachable and every strategy stays maker-only.
4. The latch is consumed and persisted **before** the venue sees anything, so
   a retried run places one order, never two.
5. **Ten probes means ten authorizations.** No single grant licences a batch.
   If that is too much ceremony, the alternative is a cohort-style grant with
   an explicit `--max-probes 10` budget and a per-probe latch slot — the
   owner's call, not mine.

## Stop conditions — any one halts everything

- cumulative **realized** loss reaches **$8.00**;
- **10** probes completed;
- any **semantic contradiction**: a fill above the frozen limit, a mapping
  mismatch, an unexplained partial, an ambiguous send, or a reconciliation
  failure → writes the HALT file, which blocks every later submission until a
  human clears it;
- live terms read fails (readable, no missing fields, contract `active`,
  market `open`);
- the account is not flat when a probe starts;
- the statistical gate is reached — at 100 R2 windows, probing pauses until
  the single statistical read is done and the owner decides.

## Separation from the statistical gate

**Hard separation, decided now rather than later.**

- Probe windows are written to `probe_windows.json` and **excluded** from the
  statistical cohort, by `r2_window_counter.py` and by the eventual read. A
  q=1 taker order against a median displayed size of 681 should not move the
  touch, but "should not" is an assumption and the sealed validation will not
  rest on one.
- The gate counter is **blind**: it prints a window count and discards
  correctness, price and EV. There is no interim R2 performance read, by
  construction.
- **Live P&L is never pooled with the statistical result.** Execution-audit
  cashflow is its own ledger and its own document.

## What would make this worth arming

Honestly: it is worth arming *now* precisely because it is not a bet on R2.
The seven measurements are needed whether or not R2 survives the gate — if
modelled and actual execution diverge, every EV figure in this lane is built
on sand, and better to learn that on $8 of q=1 probes than after the gate
passes. If R2 fails the gate, the execution audit still tells us what our book
model is worth for anything else on this venue.

What I would **not** do is let these probes become an interim read on R2's
profitability. Ten q=1 fills cannot distinguish +0.04/contract from zero, and
any P&L they produce must be reported as execution-audit cashflow and nothing
else.

## Current state

| | |
|---|---|
| R2 rule | **immutable** at `6bc5c1f7`, fingerprint `64b1783788...` |
| statistical gate | 42 / **100** independent prospective R2 windows — **58 to go** |
| interim performance reads | **none**, and none available before the gate |
| live authorization | **none in force**; SEMANTICS-1 spent |
| this protocol | **prepared, NOT armed** |
| collection | running, blind |
