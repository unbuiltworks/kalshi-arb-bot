# Canary 01 — q1 HOLD_OPEN_EXTEND — BUILT, DISARMED, NOT YET RUN

Module `src/pmus_q1_canary.py`. Tests `tests/test_q1_canary.py` (25, all
passing). The reviewed `src/pmus_q1_executor.py` is **unmodified** — `git
diff` against it is empty — so the canary composes its primitives and the
existing campaign's proven paths stay byte-identical.

## Standing caveat, restated because it governs how the result may be read

`HOLD_OPEN_EXTEND`'s only prior evidence is a **5.01-minute partial slice**
measuring mechanics. Its settled economics have never been computed, because
the export that would compute them is blocked. Ladder steps 1–4 (replay,
OOS, shadow, action-by-action) are therefore **unrun for this arm**. The
success criterion — clean lifecycle plus genuinely completed paired
inventory, explicitly not $5/window — is what makes the probe defensible. A
clean result is evidence the plumbing and the pairing work. It is **not**
evidence that holding is profitable.

## Bounds (constants, not knobs)

| bound | value | source |
|---|---|---|
| quantity per leg | 1 | `X.QTY` |
| campaigns per invocation | 1, no auto-restart | loop exits, no relaunch |
| markets | 1 (BTC 15m, slug-gated) | `PMUS_Q1_MARKET_SLUG` |
| outstanding orders | at most 1 | single `resting` slot |
| worst-case loss | **$0.80** | one unpaired long; `entry_ok` caps the price |
| residual cap | 1 contract | `X.RESIDUAL_CAP` |
| daily realized-loss stop | −$1.00 | `X.DAILY_LOSS_STOP`, authoritative ledger |
| scaling within the run | none | no size or market is ever added |
| additional capital | none | q=1 throughout |

## Arming is triple-key and fail-closed

The executor's own dual key is deliberately **not sufficient**: this
behaviour was separately authorized, so it carries its own third key.

```
PMUS_Q1_LIVE=YES
PMUS_Q1_SESSION_ID=<fresh id>     PMUS_Q1_ARM=<same id>
PMUS_CANARY_ARM=<same id>          <-- canary-specific
PMUS_Q1_MARKET_SLUG=<slug>        PMUS_Q1_GATE_EVIDENCE=<same slug>
```

Any absence or mismatch returns `DISARMED` before a single network call, and
a test asserts that a disarmed run touches no transport at all.

## The eight mandated behaviours, and where each is enforced

| # | rule | implementation | test |
|---|---|---|---|
| 1 | rest economically valid maker OPEN/EXTEND | `entry_ok` band + post-only `place_limit_buy` | complement/edge tests |
| 2 | do not chase the touch; preserve queue while valid | `open_still_valid` | holds across a 0.40→0.45 touch walk with **1 placement, 0 requotes** |
| 3 | on fill, switch objective to reducing inventory | fill branch sets `first_cost`, `deficient()` drives the next action | objective-switch test |
| 4 | keep the complement working continuously | re-bounded every poll via `complement_still_valid` | edge-lock test |
| 5 | partial fills reduce only remaining quantity | `partial_remainder_kept`, `resting["qty"] -= filled` | remainder test |
| 6 | contemporaneous fee-adjusted taker completion → complete now | `taker_completion_ok` checked every poll; maker rest cancelled first to prevent a double fill | takes=1 when fee-safe, 0 when not |
| 7 | never add to the long side outside the aggregate-basis rule and residual cap | `extend_permitted` — at cap 1 a second long gives residual 2 > cap, so **False in every reachable state** | explicit test |
| 8 | unconditional cancel/sweep on every exit path | `finally: cancel_all_deadman` + `sweep` ledger row | parametrised over matched / no-fill / halt / stale |

## Recording

Every decision, submission, ACK, fill, cancel, inventory change, pair cost,
fee, rebate credit and cashflow is appended to the durable ledger. A ledger
write failure is re-raised as a `Halt` rather than swallowed, so an
unrecorded campaign cannot continue trading. `realized_pnl` stays `null` and
settlement reads `PENDING`; this process never asserts realized P&L.

## One finding from building the tests

My first fake venue served a fill shape the venue does not send. The
executor **HALTed** with `AMBIGUOUS_FILL_PRICE` rather than guessing a
price — the correct behaviour, demonstrated by accident. The fake now
matches the real schema, including that `avgPx` is the long-side price so a
NO fill arrives as `1 − price`.

## What is NOT done

Nothing has been armed, deployed, or run. No live order has been placed. The
next step needs an operator decision and the arming keys; advancing to
q5/q10 happens only after a clean q1 lifecycle with real paired inventory,
and q30/q50 and any capital increase remain out of scope.
