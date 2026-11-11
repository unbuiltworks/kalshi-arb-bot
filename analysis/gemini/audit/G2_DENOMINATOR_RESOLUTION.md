# G2's denominator: resolved from provenance, and the answer is FAIL CLOSED

**Question.** Both decoder implementations agree the numerator is 72 ambiguous
and 134 decoded. They disagree on `observed`, because one merges book
observations across capture files and the other classifies per file:

| reading | STALE | PRE_CAPTURE | observed | ambiguous rate | vs G2's 25% |
|---|---|---|---|---|---|
| merged across files | 74 | 58 | 280 | **25.7%** | FAIL, marginally |
| per capture file | 17 | 96 | 223 | **32.3%** | FAIL |

*(The two readings straddled the threshold when first found at n=118; at the
current sample both fail, which does not change the argument below.)*

## What the pre-registration actually says

`P10_DECODER_SPEC.md` §3, unchanged since commit `3fc55c5`:

> For a trade at venue time `tms`, take **every book observation of the same
> instrument** within `max_stale_s` of it (before or after).

and, for the PRE_CAPTURE class:

> trade precedes **our first book for that instrument** → PRE_CAPTURE

**The language is instrument-scoped.** Both qualifiers name the *instrument*
and the *staleness window*. Neither mentions a capture file. Read literally,
the specification merges: a book observation of `GEMI-BTC15M…-UP` is a book
observation of that instrument whoever recorded it.

## Why that is not sufficient to resolve it

The specification was written when decoding ran over **one capture file**.
`p10b_decode.main(path, …)` takes a single path, and at authorship exactly one
recorder existed. Under those conditions "every book observation of the same
instrument" and "every book observation in this file" are the *same set*, so
the phrase distinguishes nothing. Multi-file merging was introduced later, by
`p10h_gate` — written by me on 2026-09-22, after the gate was pre-registered.

So the instrument-scoped wording is instrument-scoped **by default, not by
decision**. The author did not choose merged over per-file; the author never
faced the question. A specification that never contemplated a case does not
determine it.

## The resolution

**G2 stays FAIL CLOSED, and the ambiguity is not resolved by selection.**

The reading that the literal language favours — merged — is also the reading
that produced the *better* number when the question was found (25.7% against
32.3%, either side of the 25% threshold). That is exactly the circumstance in
which picking the literal reading is indistinguishable from picking the
passing one, and the standing instruction is explicit: resolve from
pre-existing provenance, not from which interpretation passes; where provenance
does not determine it, leave the gate FAIL CLOSED.

It does not determine it. So the gate fails closed.

## How this stops recurring

The question exists only because one instrument's books were split across
several concurrently-running capture files. The persistent recorder
(`src/gemini_execution/p10_recorder.py`) writes **one append-only stream per
cohort**, so an instrument's book observations live in a single ordered
artifact and merged-vs-per-file is not a question that can be asked of it.

This is a structural fix for the *next* population, not a retrofit to this
one. **Today's 144 decoded rows keep their provenance and their FAIL-CLOSED
status.** They are not re-scored, not re-merged and not discarded.
