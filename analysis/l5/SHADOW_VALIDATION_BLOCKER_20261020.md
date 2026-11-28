# Shadow validation found a blocker in minutes: the engine gets an empty book

Found at 02:04:25Z on deployment `2286a0de` (commit `51b550ff`), within seven
minutes of activating the two-leg engine in shadow.

## What the records say

Every decision in the 0200z window reads:

```
"key": "TWO_SIDED_PROBE:BOOK_STALE"
"book": {"bid":"0.1100","ask":"0.1200","bid_shares":"694.09","ask_shares":"7515.18"}
"stream_age_s": 0.009          (and 0.029, 0.044, 0.057)
"translation": {"ok": false, "reason": "STRICT_NOT_EVALUABLE"}
```

The book is **fresh** -- nine to fifty-seven milliseconds -- and the engine
still refuses it as stale.

## The cause

`l5_pmus_live_runner.py:1441` calls

```python
rec = ex.handle(row, {}, market=slug, window=str(ws), ...)
```

The second positional argument IS `authenticated`, the book. It is `{}`.

`probe_plan` then does `authenticated.get("stream_age_s")` -> `None`, and
`build_orders` refuses when `age is None or age > MAX_BOOK_AGE_S`. So the
refusal is unconditional: **the two-sided engine can never place an order.**

The `book` and `stream_age_s` in the log are filled by the RUNNER when it
builds the decision record for emission. They never reach the executor. That
is why `translation.reason` has read `STRICT_NOT_EVALUABLE` on every decision
for as long as we have been looking at these records, including on 2026-10-19.

`{}` is deliberate for the L5-following path, and the comment above the call
says so: that path rests at L5's own price through `MAKER_TREATMENT` and does
not need a book. It is why the Oct 19 order (`D6XA5J4G10JH`) submitted
normally despite `translation.ok: false`. The two-sided engine is the only
caller that needs the authenticated book, and it is the one that does not get
it.

## Why this matters more than it looks

It fails CLOSED, so nothing unsafe happened and nothing could. But:

* a bounded live two-leg test would have placed **zero orders** and burned a
  window, an authorization and an owner handshake to discover it;
* shadow validation of entries, complements, rounds and attempts cannot
  produce a single datum until it is fixed, because every decision stops at
  `BOOK_STALE` before any of that code is reached.

So the four fixes are deployed and correct in isolation, and none of them is
observable yet.

## The fix, and why not the obvious one

The obvious fix -- pass the runner's snapshot as `authenticated` -- also
feeds `FT.pmus_translate`, which currently returns `STRICT_NOT_EVALUABLE`
for every L5-following decision. Giving it a real book could change the
pricing and sizing of the L5 path, which has traded real money. That is a
money-path behaviour change disguised as a one-word edit.

The additive fix: attach the authenticated book mirror to the executor, the
way the calibration recorder already is
(`self.executor.calib_book = self.book`), and have `probe_plan` take a fresh
snapshot from it when `authenticated` does not carry the fields. The
L5-following translation path keeps receiving `{}` and does not change at
all, and the two-sided engine gets the book it needs.

Either way it wants its own regression and a shadow re-verification before
any live proposal, because this is the code path that would be carrying money.

## State

Shadow lock engaged, authorization EXPIRED, two-leg engine active in
shadow-only, limits unchanged at $5/$5/1 market/1 order/$5 halt. No order was
placed and none could be.
