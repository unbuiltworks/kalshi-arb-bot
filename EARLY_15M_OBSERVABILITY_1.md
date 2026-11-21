# EARLY_15M_ADAPTATION — what can and cannot be reconstructed

You asked for actual pair bases from actual tape and book observations, and
for `UNOBSERVABLE` rather than a constructed price wherever the book is
missing. **For Gemini's early interval the book is missing almost everywhere,
so most of the list is UNOBSERVABLE and the study cannot be completed as
specified.** That is the finding, not a failure to try.

## Why

Gemini's public API serves **`/v1/trades/{symbol}` for expired contracts but
no book history**. A maker's held-leg execution price and the executable
complement price both require knowing where an order could have rested, which
requires a book. Without one, any price is chosen by the replay rather than
observed — which is exactly the defect that made the earlier 0.99 result a
tautology.

Our own recorders do not fill the gap:

- the running lanes enter at **TTE 240–280**, so every book they record is
  **inside** the band, not above it;
- the only Gemini BTC15m books ever recorded above TTE 300 are **46 cycles
  from one contract**, `GEMI-BTC15M2609191515-UP` — the mis-banded first run
  kept as evidence of that defect;
- of the **96 contracts EARLY_15M replays, exactly 1** has any recorded book
  at TTE > 300.

## The counts, independently

| quantity | count | status |
|---|---|---|
| contracts replayed | 96 | observable |
| computable from tape | 91 | observable |
| first-fill states | 91 | **tape-observable only** — external flow crossed *some* price; the price itself is chosen by the replay, not observed |
| opposite flow after the first fill | 48 | observable (tape: opposite aggressor direction) |
| median time to that flow | 133.8s | observable |
| **price-observable complements** | **1 of 96** | book exists for one contract only |
| **maker-reachable complements** | **UNOBSERVABLE** | needs a book at the complement moment |
| **≤ $1 pairs** | **UNOBSERVABLE** | needs both legs' actual executable prices |
| **fee-positive pairs** | **UNOBSERVABLE** | needs the basis first |
| **median actual pair basis** | **UNOBSERVABLE** | the 0.99 in the earlier run was `1 − held − tick`, set by the replay |
| terminal residual if no complement | **UNOBSERVABLE** | follows from the same missing prices |
| EXTEND/COMPLETE sequence frozen L5 would request | derivable | but only against a constructed book, so not reported as observation |

## What survives

One sentence, and it is worth keeping: **over 91 replayable expired BTC15m
contracts, external opposite-direction flow reached a fee-safe complement
price in 48 (52.8%), median 133.8s after the first fill.** That is a statement
about *flow*, which the tape does observe. It is not a statement about what a
pair would have cost, and it never was.

## What would make it answerable

Recording the BTC15m book at TTE > 300 prospectively — which nothing currently
does, because both lanes enter at 240–280. That is a collection change, not an
analysis one, and it is not made here.

## The stronger evidence is on the other venue

Polymarket L5's own book context **is** recorded at fill time (689
`l5_event_context` rows, all carrying `book_context_by_token`), and his actual
fills need no book at all to segment. See `L5_TTE_SEGMENTS_1.md`: 99.3% of his
BTC15m fills, and all of his recycling, occur at first-fill TTE > 600.
