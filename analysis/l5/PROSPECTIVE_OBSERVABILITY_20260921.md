# Prospective execution observability — A/B/C/D/E

Checkpoint `319e4935`. No P&L optimization, no selector, no live orders, no
authorization constant set. The durable collector and the shadows were not
touched.

## A. Match mechanism × venue-supplied maker/taker label

`PositionSplit` read strictly as **transaction/matching context**. It is not a
queue, not a priority mechanism, and not a second fillable pool — the two
outcome tokens are one book in two conventions (`2d84a002`).

Venue labels from the 10,488-fill window: **9,243 maker-only transactions, 768
taker-only, and zero mixed**, so no fill's label is ambiguous. Random sample of
320 each; single-match restricts to transactions carrying exactly one exchange
fill pair, where nothing else could have produced the split.

| label | scope | n | SPLIT_PRESENT | MERGE_ONLY | NEITHER |
|---|---|---|---|---|---|
| MAKER | all tx | 320 | 84.7% | 3.4% | 11.9% |
| MAKER | **single-match** | 133 | **86.5%** | 0.0% | 13.5% |
| TAKER | all tx | 320 | 95.0% | 0.0% | 5.0% |
| TAKER | **single-match** | 130 | **90.0%** | 0.0% | 10.0% |

**Difference maker − taker = −3.5 pp, 95% CI [−11.3, +4.2]** (bootstrap
[−11.1, +4.1]). **Includes zero.**

> **The match mechanism does not distinguish L5's maker fills from his taker
> fills.** Roughly nine in ten of his transactions carry a complete-set mint
> whichever side of the match he was on. This is the ordinary settlement path
> of the venue, and it marks out nothing special about maker execution.

## B. Prospective recorder — proof from captured L5 events

`src/l5_prospective_recorder.py`. Public GETs only; no credential, no order
verb, no signer — asserted structurally by test (AST: every `Request` is a
plain GET with no `data=`/`method=`, no credential identifier, no mutating verb
literal). Raw streams (`raw_books`, `raw_fills`, `raw_prints`) are written
separately from derived `events`.

**The chain PRE-FILL BOOK → L5 ACTION → MATCH TYPE → POST-FILL BOOK is now
observed directly**, on real fills, with full ladders:

```
EVENT btc-updown-15m-... Up   OPPOSITE px=0.780 q=9.09  MAKER pre_book=yes ahead=5826.02
EVENT btc-updown-15m-... Up   EXTEND   px=0.780 q=2.44  MAKER pre_book=yes ahead=7287.02
EVENT btc-updown-15m-... Down OPPOSITE px=0.160 q=17.67 MAKER pre_book=yes ahead=1573.73
EVENT btc-updown-15m-... Up   OPPOSITE px=0.840 q=60.00 MAKER pre_book=yes ahead= 316.26
```

Four defects were found and fixed by building it, each of which produced a
recorder that looked healthy while recording nothing usable:

1. **Backlog flood.** The first poll returns L5's whole recent 500-fill
   backlog. Emitting it produced 500 "observations" whose pre-fill book was
   null *by construction* — we were not watching when they happened. The seed
   poll now marks them seen and emits nothing.
2. **Habitat pruning inverted.** Habitat tokens were absent from `token_order`,
   sorted to −1, and were evicted **first** — deleting exactly the coverage
   that makes a pre-fill book possible. They are now never pruned.
3. **Discovery could not find the live window.** Gamma
   `?closed=false&order=startDate&ascending=false` returned 112 tokens, **every
   one ending 11+ hours out**; none were trading. The live window is arithmetic
   on the slug epoch (`floor(now/period)*period`), so it is derived, not
   searched — 10 markets, 20 tokens, no search.
4. **Rollover was polled, not detected.** A fixed 120 s refresh let a 15-minute
   window roll at :00, L5 fill 34 s in, and our first snapshot of that market
   land **88 s after his first fill**. The slug set is free to compute, so
   rollover is now detected exactly and the network is touched only on change.

Also: the book sweep was sequential (~40 requests × ~0.3 s) and, with the
per-market print sweep, took over 20 s per loop — long enough that "pre-fill"
snapshots stopped being near the fill. Both sweeps are now parallel.

## C. First raw arriving-flow / depth observations

**A measured venue property that changes the architecture.**
`/trades?market=<conditionId>` **lags roughly 90 seconds.** For a fill at
t=1789957631 the newest print the endpoint would serve for that market was
t=1789957543, while the pre-fill window was 1.3 s wide. So at capture time the
flow numerator is not merely small — it is **structurally unavailable**. The
book side has no such lag, so events are emitted immediately with their ladders
and the flow terms are **backfilled** once the tape settles (`backfill_flow`).
Events still uncovered keep a null and a reason; a zero would claim we looked.

First joined observations (n=16, **one market**, backfilled):

| action | price | depth ahead | arriving executable | **flow / ahead** | window | prints |
|---|---|---|---|---|---|---|
| OPPOSITE | 0.780 | 5,826.0 | 2.4 | **0.0004** | 16.6 s | 5 |
| EXTEND | 0.780 | 7,287.0 | 2.4 | **0.0003** | 16.6 s | 5 |
| OPPOSITE | 0.160 | 1,573.7 | 0.0 | **0.0000** | 16.4 s | 13 |
| OPPOSITE | 0.840 | 316.3 | 10.6 | **0.0336** | 11.3 s | 8 |
| OPPOSITE | 0.140 | 415.0 | 0.0 | **0.0000** | 14.1 s | 10 |

> **The striking thing is how small the ratio is.** L5 filled as a maker while
> arriving executable flow was three to four orders of magnitude smaller than
> the depth resting ahead of him. Under a conventional FIFO queue model these
> fills should not have occurred at all: the flow that arrived could not have
> cleared 1% of the queue, let alone all of it.

This is **descriptive only**. n=16 from a single market is far below the
preregistered floor of **200 maker fills with a non-null pre-fill book**, so the
H-FLOW comparison declared in `PROSPECTIVE_PREREGISTRATION_20260921.md` is **not
run and no threshold is fitted**.

## D. q=1 concurrency safety — the hole is closed

**Single-writer ownership** via `fcntl.flock(LOCK_EX | LOCK_NB)` on
`<ledger>.lock`, acquired in the constructor, held for the ledger's whole life.
A second live process **refuses** (`LedgerLocked`) rather than opening a second
writer. `exclusive=True` is the default and opting out is explicit.

Correct across a crash by construction: the kernel drops a dead process's flock,
so a **restart is not deadlocked by its own dead predecessor**, while a **live**
peer still refuses.

Adversarially tested with genuinely separate interpreters:

| test | result |
|---|---|
| second live process opens the same ledger | **REFUSED** |
| second process tries to PLACE while first holds it | **REFUSED_BY_LOCK** |
| two children race for an unheld ledger | exactly one `ACQUIRED`, one `REFUSED` |
| owner dies holding the lock (`os._exit(9)`) | restart acquires; **exposure survived**; no new PLACE |

**How venue reconciliation bounds unobserved fills** — `reconcile_fills()`.
The open-order check cannot catch a fill we never saw: the fill *removes* the
order from the venue's open list, so open-order agreement is restored by exactly
the event that matters. Remaining quantity then reads high (safe for admission)
but **directional residual reads low**, and residual is the number gating further
one-sided exposure. So the venue's own fill record is authoritative, any
shortfall is a **refusal**, and nothing is silently written into the ledger —
a silent repair would destroy the evidence that the two disagreed.

**Tests: 31 adversarial (was 22) + 21 recorder = 52 passing.** Authorization
constants remain `None`; `src/gemini_execution/` still has zero diff.

## E. The exact next falsifiable hypothesis

> **H-QUEUE-IRRELEVANT: L5's maker fills are not gated by the same-outcome FIFO
> queue at all. Conditional on him resting at a price, the probability of a
> maker fill is independent of `depth_ahead_of_fill_price`, and is instead
> driven by the arrival of a counterparty buying the complement.**

**Why this and not another.** The first 16 joined observations show fills
occurring with `flow/ahead` of 0.0000–0.0336 — arriving executable same-outcome
flow three to four orders of magnitude below the resting queue. Either the queue
is not the binding constraint, or our depth measure is wrong. H-QUEUE-IRRELEVANT
says the former, and it is the hypothesis that would explain both this and the
86.5% mint rate on maker fills.

**Prediction that can fail:** across ≥200 maker fills with pre-fill books,
`depth_ahead_of_fill_price` carries **no** discriminating information about
whether a fill occurs, while complement-side buy arrival does.

**What falsifies it:** a monotone relationship between depth ahead and fill
occurrence; or complement-buy arrival showing no relationship; or the whole
effect vanishing under a within-market shuffle.

**This supersedes H-FLOW as the primary test** — H-FLOW assumed the ratio's
denominator was the binding quantity, and the first observations put that in
doubt. Both remain preregistered; H-QUEUE-IRRELEVANT is tested first. **No
selector is built from either, and neither is fitted before the 200-fill floor.**

## Not claimed

- n=16, one market, one hour of one day. Nothing here is a rate or a law.
- `depth_ahead` is an **upper bound**: anonymous depth hides arrival order.
- Flow is **backfilled**, so it inherits the tape's ~90 s lag and its
  whole-second resolution; the window widths (0.2–16.6 s) are recorded per event.
- The recorder has **not** been deployed to the durable Railway service. The
  code is committed; deployment is a separate decision.
