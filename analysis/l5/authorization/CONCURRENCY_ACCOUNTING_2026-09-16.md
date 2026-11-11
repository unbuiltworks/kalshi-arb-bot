# Zero-exposure market concurrency accounting — the live finding, corrected

**Authorization:** owner, 2026-09-16, "fix zero-exposure market concurrency
accounting — smallest safety-preserving change."
**Status:** implemented, full suite green (4987 passed / 27 skipped / 1 xfailed).
**HALT:** remained deployed throughout the change and its testing, as required.

## What the live probe found

The CP1 probe armed at 21:13:02Z and submitted six maker orders on one market
(`aec-dota2-yes-tm-2026-09-16`) at qty 0.22. **Nothing filled.** All six orders
went terminal by cancellation. The campaign then tried to open its second
market and was refused:

```
MandateRefusal:max_concurrent_markets_exhausted
```

`consumption()` computed `markets_open` as "committed AND not settled". A
market that never filled never settles — so it could never leave the open set.
With `max_concurrent_markets = 1`, market 1 held the only concurrency slot
**permanently**. This is terminal, not transient: no amount of waiting frees
it.

## The correction

A market counts as concurrently OPEN while it can still lose money there:

* it has a **live or unresolved** order (no terminal row, or a commit that was
  never linked to a venue order id at all), **or**
* it has **fills that have not settled** — any fill, not just an unmatched
  one, because a fully matched position still carries money to settlement.

Zero live orders **and** zero fills releases the concurrency slot. Nothing else
does.

**Fail-closed on anything unresolved.** An unlinked commit or a missing
terminal row counts as OPEN. "We never found out" must never free concurrency —
the same rule the charge path already uses when it charges an unconfirmed
submission in full.

## What deliberately did not change

| Thing | Why |
|---|---|
| `markets_touched` / `n_markets_touched` | A zero-fill attempt **still** consumes one of `max_total_markets`. Releasing it from *both* counters would turn a 3-market authorization into unlimited market scanning — a worse failure than the one being fixed. |
| The durable ledger | Nothing is deleted. Every commit, link and terminal row stays exactly where it was. This is a different *reading* of the same rows, so the audit trail is intact. |
| The charge arithmetic | A terminal order with `filled_qty 0` already released its reservation, and a fill is still permanent. |
| GuardedTransport, the immutable mandate, `min(rung, mandate)`, CP1/B3 tightening, sizing 0.10/0.125, k 0.00619, $5/market, $15 campaign, 3 contracts/$3 residual, −$6 daily stop | Untouched. |

`max_concurrent_markets = 1` and `max_total_markets = 3` are two different
counters, and this change keeps them different.

## Tests

`tests/test_mandate_concurrency_lifecycle.py` — 28 tests: all eleven
owner-required adversarial lifecycle cases, a literal ledger builder so each
case states its lifecycle explicitly, the exact live failure replayed
end-to-end, and the classification finding below.

`tests/test_cp1_probe_rung.py::test_guarded_transport_is_not_weakened_by_the_rung_work`
was restated. It previously asserted `git diff` on `src/pmus_live_mandate.py`
was empty — the right proof while the CP1 rung was the only change in flight,
but the wrong instrument once the owner authorized a change *inside* that file.
It now asserts the properties that byte-identity was standing in for, read from
the **AST** rather than from text: enforcement is on the wire, there is no
stage allow-list as code, no rung/sizing symbol is defined, called, imported or
branched on, and the wire guard imports nothing from this repository at all.
Six mutations were injected to confirm each property bites; all six were caught
and the module was restored byte-identical.

Text scanning produced a false positive for the third time this session (after
the credential-prose scan and `"open("` matching `open_markets`): this module's
docstrings *name* `counts_against_market_cap()` precisely to explain why it must
never return. The dangerous form is the symbol, not the word.
