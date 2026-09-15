# P0: the daily stop was under-counting, and the conservative figure has breached

**Status: FAIL CLOSED. The conservative calculation is −31.0612 against an
unchanged −$30.00 stop. No new campaign may start.** Existing position
management and settlement continue. Nothing is force-closed. The limit is not
changed.

## The four numbers

| | value |
|---|---|
| executor `combined_loss` | **−23.2786** (its own `risk_set` row, 2026-09-15T10:59:42Z) |
| independent conservative reconstruction | **−31.0612** |
| difference | **−7.7826** |
| stop (unchanged) | −30.00 |

**Markets causing the difference** — charged by the conservative calculation,
absent from the executor's `risk_bearing`, and with no venue-confirmed terminal
state:

| market | state | charged |
|---|---|---|
| `aec-wta-cribuc-biaand-2026-09-13` | PENDING | −4.0686 |
| `aec-atpcq-andfen-ryaseg-2026-09-13` | PENDING | −2.1404 |
| `aec-wtt-kanpet-zijcui-2026-09-14` | PENDING | −0.9976 |
| `aec-itfme-nicpar-gabpen-2026-09-14` | PENDING | −0.5760 |
| **total** | | **−7.7826** |

The arithmetic closes exactly: the executor charges only `blaell-hirmor`
(−0.5935) against a realized −22.6851; the conservative figure charges all five
unresolved floors (−8.3761). Same realized term, different unsettled term, and
the gap is precisely the four dropped markets.

## Root cause: two functions in two modules disagree about "settled"

```python
# pmus_settlement_auditor.append_authoritative_ledger
write("settled", resolution=...)                     # ALWAYS
if result["realized_confirmed"] and result["realized_usd"] is not None:
    write("realized", realized_usd=...)              # ONLY when confirmed
```

```python
# pmus_l5_continuous_session.unsettled_slugs   (BEFORE)
elif kind in ("settled", "realized"):
    settled.add(slug)                                # closes on EITHER

# pmus_q1_executor.realized_today
if row.get("kind") == "realized" ...                 # sums ONLY realized
```

A settlement the venue would not put a number on wrote `settled` but not
`realized`. That **removed the market's guaranteed-loss floor from
`combined_loss` and added nothing back**. The dollars left both terms and were
in neither.

This silently defeated the invariant `combined_loss` documents for itself:
*"a market that settles leaves the HELD set and its result enters
realized_today in the same transition. Nothing is in both."* The transition is
implemented in two modules, and they did not agree on what "settles" means.

### Why it is terminal, and why polling stopped

`UNCONFIRMED → CONFIRMED` has happened **zero** times across the whole ledger
(`PENDING→CONFIRMED` 29, `PENDING→UNCONFIRMED` 7). It is a terminal state, not a
queue — because `pmus_settlement_sweep.pending_slugs()` reads the *same*
`unsettled_slugs()`. Once a market left that set it stopped being charged **and
stopped being polled**, so it could never acquire the number that would confirm
it. `nicpar-gabpen` was audited once, at 2026-09-14T11:53:58Z, and never again.

## The fix: restore one definition, and three things follow

`unsettled_slugs()` now closes a market **only** on a `realized` row — the
venue's own confirmed number. From that single change:

1. **Accounting** — unconfirmed markets keep being charged in `combined_loss`.
2. **Polling** — `pending_slugs()` keeps auditing them until a terminal
   venue-supported state is reached.
3. **New exposure** — `pmus_boundary_preflight` check #3 `settlements_current`
   requires `len(unsettled_slugs()) == 0`, so campaign advancement blocks.

No new gate was added, no limit changed, no position closed. The executor
already had the correct lane: tripping the combined budget switches to
**risk-reduction-only** — no new OPEN/EXTEND, COMPLETE only where
`reduces_risk_only()` holds and the ≤1.00 pair ceiling is satisfied, otherwise
hold to settlement. No emergency taker flattening, no above-par rescue, no cap
increase. Restoring the invariant is what makes that lane fire honestly.

`unsettled_detail()` reports **`NEVER_SETTLED`** vs
**`SETTLED_REALIZED_UNCONFIRMED`** so "still charged" is legible rather than
mysterious.

**This fails closed in the intended direction:** a market the venue never
confirms stays charged and keeps blocking new campaigns, rather than vanishing
from the budget. No P&L is manufactured for an unconfirmed settlement — the
conservative figure refuses to *stop charging*, which is not the same as
inventing a number.

## Why the four PENDING markets left `risk_bearing` — the same bug

They look like a separate population in my log-derived reconstruction, which
labels them PENDING because it only sees settlement rows that were logged. But
the executor's own ledger must say otherwise, and its old code says why:

`unsettled_slugs()` dropped a slug **only** on a `settled` or `realized` row.
These four are absent from `risk_bearing`, so they were dropped. They carry no
confirmed realized — none appears in Ledger D. A market can only satisfy both
conditions by holding a **bare `settled` row**: resolved at the venue, no number
attached. That is precisely the unconfirmed-settlement bug.

So one defect explains both populations, and the −7.7826 is not a second
problem. (Stated as a deduction from the old code's own logic, with its
assumption named: that they left via `unsettled_slugs()` rather than some other
path. This session cannot read the executor's volume directly.)

## What is deliberately not claimed

The five markets my reconstruction *labels* `SETTLED_REALIZED_UNCONFIRMED`
contribute **0.0000** to today's difference — their floors are all non-negative,
so the negatives-only rule charges nothing for them either way. The money today
comes from the four above. The distinction matters because it means the gap is
driven by *which markets stopped being charged*, not by how much any individual
unconfirmed settlement was worth.

## Demonstrated, not asserted

Against a ledger shaped like production's:

```
OLD definition still charging : ['open_1']
NEW definition still charging : ['open_1', 'unconf_a', 'unconf_b']
detail : {'unconf_a': 'SETTLED_REALIZED_UNCONFIRMED',
          'unconf_b': 'SETTLED_REALIZED_UNCONFIRMED',
          'open_1':   'NEVER_SETTLED'}
```

The unconfirmed markets return to the charged set; the confirmed one stays
closed, so nothing is double-counted.

Full suite **3,895 passed**, and notably **no existing test broke** — the old
behaviour was never pinned by a test, which is how it survived. It is pinned
now, from both ends, in `tests/test_pmus_accounting_invariant.py`.
