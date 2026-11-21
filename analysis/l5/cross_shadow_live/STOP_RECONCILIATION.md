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

---

## Cutover record — 2026-09-15T12:04Z

| | |
|---|---|
| old SHA | `3ec9e1cd996c6fdb9cd03310da4611e9a828989f` |
| new SHA | `54d1c75da707db699f290c6367f6ee6b6ad764d3` |
| old deployment (rollback ref) | `e926e286-ebaa-4842-b101-d9567bdc5e9a` |
| new deployment | `72f2aebd-7678-4f2c-a2e2-4f30adf612eb` |
| production branch | `feature/l5-transfer-backtest` |
| trigger | `deploy/pmus_spring_l5/DEPLOY_TRIGGER` (the service's only watch pattern) |

**Pre-deploy state, read 11:54:33Z:** serving `3ec9e1cd996c`, uptime 42,685 s,
261 sessions, held 1 (`aec-atp-blaell-hirmor-2026-09-13`),
`CAMPAIGN_RESUMING:SPRING_RESIDUAL_HOLD`, no HALT, last published
`combined_loss −23.2786` against the unchanged −30.00 stop.

**The rollback reference is recorded but carries a warning.** `3ec9e1cd`
under-counts the stop by $7.78 and opens new exposure while doing so. Restoring
it to make the service look healthy is the single response this cutover exists
to prevent. If the new build misbehaves, fail closed instead.

**Scope.** The complete functional change, verified by an AST-stripped,
docstring-excluded diff against `3ec9e1cd`:

```
-  elif kind in ('settled', 'realized'):
+  elif kind == 'realized':
+  def unsettled_detail() -> dict:     # pure read; no mutation, no order path
```

Nothing else. The fidelity, latency and chain-observer work stays on the
development branch and was deliberately excluded.

**Tests:** 3,649 passed / 27 skipped / 1 xfailed on the exact deployed artifact,
against 3,642 recorded for `3ec9e1cd` — exactly the 7 added accounting tests,
nothing removed or newly skipped.


---

## Post-cutover observation — the settle pass has not completed

**Correcting my own earlier report.** I recorded "settlement polling continues"
as verified from the code path. It is running, but it has **not demonstrably
completed a single cycle**, and that should not have been reported as verified.

The boot row gives exact numbers: **70 filled markets, 69 previously closed,
but only 32 carry a venue-confirmed `realized` row.** So the fix returned
**~38 markets** to the pending set, not the handful estimated.

`shutdown_settle_pass` iterates **every** pending slug with no batch limit — the
`SETTLE_AUDIT_BATCH = 5` cap applies only to the campaign loop — and each slug
is a multi-page venue walk. The first pass began at 12:07:00Z. As of 12:30Z,
`shutdown_idle`, which logs once per cycle, has logged **zero** times.

Metrics at 12:29Z: **CPU 0, NETWORK_RX 0, NETWORK_TX 0**, memory resident
0.236 GB. So the process is idle or blocked, **not** grinding through work.
Which of those it is, is not established here.

### Risk position — unchanged and fail-closed

No order can be placed from `shutdown_idle`. Held inventory
(`aec-atp-blaell-hirmor-2026-09-13`) is preserved, nothing was force-closed, the
−$30.00 stop is untouched. The cost is that venue-confirmed realized numbers may
not be getting recorded: an **accounting delay, not capital movement**.

No rollback. `3ec9e1cd` is precisely the build that under-counts the stop and
opens new exposure while doing so.

### The targeted remedy, prepared but NOT deployed

Batch the shutdown-path settle loop the way the campaign loop already does, so a
large pending set drains incrementally across cycles rather than blocking one
pass. That also makes the loop log progress each cycle — which is how this would
have surfaced in minutes instead of by inference from a silent log.

This is a second change to a money-path module and is deliberately left for
explicit authorization.

---

# 2026-09-15T13:2xZ — the bounded settlement pass is verified live, and it exposed the next layer

Three consecutive `shutdown_idle` cycles on `pmus-spring-l5-probe`
(commit `6adcc4c43e67`), read from production logs:

| cycle | cursor → next | batch | pending_total | settled_now |
|---|---|---|---|---|
| 12:56:15Z | 0 → 5 | fokus-nemi … burvol-gasdav | 98 | 0 |
| 13:09:10Z | 5 → 10 | salvas-tremyk … bot-pal | 98 | 0 |
| 13:21:55Z | 10 → 15 | pitser-kimdmy … norgom-ollwal | 98 | 0 |

**The liveness property holds in production:** the durable cursor advances
0 → 5 → 10, each batch is a disjoint set of five, and the pass is bounded at
five of ninety-eight rather than attempting all of them and timing out. That is
exactly what the batching fix was for, and it is now observed rather than
asserted.

## And it makes the real blocker visible

`settled_now` is **0 on every cycle**, and every error is one of two things:

```
HTTP Error 429: Too Many Requests
activity_walk_short_of_since: ended=max_pages; oldest=2026-09-13T03:18:11
```

The markets at the head of the queue settled on **2026-09-06**. The activity
walk exhausts `max_pages` at **2026-09-13** — it cannot reach back far enough
to see their settlement rows at all. So the oldest pending markets are not slow
to settle; under this audit path they are **unreachable**, and no amount of
cursor rotation will reach them.

That is a *pagination horizon* problem, and it is a different defect from the
unbounded-pass problem that was fixed. The fix worked and uncovered the next
layer.

## This does not require intervention, and here is why

| quantity | value | across all three cycles |
|---|---|---|
| `realized_today_usd` | **-22.6851** | unchanged |
| `unsettled_floor_usd` | **-19.7544** | unchanged |
| `combined_loss_usd` | **-42.4395** | unchanged |
| `combined_budget_usd` | -30.00 | |
| `budget_permits_normal_operation` | **false** | unchanged |

The unreachable markets are already counted **conservatively**, at their
guaranteed settlement floor, inside `combined_loss`. The accounting invariant
is doing its job: an unresolved economic result that cannot be confirmed does
not disappear — it keeps weighing against the budget, and the budget stays
closed. A market we cannot audit therefore makes the system *more* restrictive,
never less.

`migration_close` also refused on every cycle
(`fresh_executable_quote_and_depth`: ask=None, depth=None), submitted nothing,
and left the residual holding. Fail-closed, as designed.

**Capital safety needs no action.** The pagination horizon is the next
executor-side settlement blocker and is recorded here rather than worked on:
the standing instruction is that the legacy executor stops being the main
workstream unless capital safety requires intervention, and it does not.
