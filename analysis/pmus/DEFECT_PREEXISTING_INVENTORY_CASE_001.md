# DEFECT_PREEXISTING_INVENTORY_CASE_001 — the flat-store guard cannot fire

Status: **OPEN. Deliberately NOT fixed before Rung A** (no code change, no rebuild).
Recorded 2026-09-28 for correction after the Rung-A probe completes.

## What is wrong

`scripts/l5_pmus_live_oneshot.py` `run()`:

    pre = {sd: store.position(market, sd) for sd in ("up", "down")}
    if any(D(str(v)) != 0 for v in pre.values()):   # -> PREEXISTING_INVENTORY

`InventoryStore.position` (`src/l5_live_execution.py:1076`) does no canonicalisation:

    return D(str(self.state["positions"].get("%s|%s" % (market, side), 0)))

The ONLY writer of `positions` is `apply_fill`, which canonicalises through
`canonical_side()` and therefore always writes `"<market>|UP"` / `"<market>|DOWN"`.
`run()` asks for `"<market>|up"` / `"<market>|down"`, which that writer never
creates. The guard reads two keys that cannot exist and concludes "flat".

## Measured, not inferred

Booking one UP fill through the sole writer, then asking the guard's own question:

    positions keys          : ['cpc-btc-updown-15m-2026-09-28-1600z|UP']
    run() flat check (lower): {'up': '0', 'down': '0'}
    upper-case read         : {'UP': '1', 'DOWN': '0'}
    residual()              : 1
    => run()'s 'any non-zero' verdict on a store holding 1 UP: False

`residual()` (line 1079) uses `"UP"`/`"DOWN"` and is correct; only the `run()`
call site is wrong. The artifact's own tests did not catch it because they
assert the refusal path by seeding state, not by booking a fill through
`apply_fill` first.

## Why it matters

`PREEXISTING_INVENTORY` exists so the settlement-independent floor is only ever
computed over inventory whose cash basis THIS session observed. With the guard
inert, a probe inheriting a non-empty store would compute a floor from a
position it never priced -- a guessed floor, which the code itself calls worse
than no floor.

## Why Rung A proceeded anyway

Owner-approved structural workaround, 2026-09-28: the three state paths were
pointed at fresh, never-used, session-unique values
(`L5_PROBE_STATE`, `L5_SESSION_JOURNAL`, `L5_LIVE_AUTH_LEDGER`), set with
`skipDeploys: true`. An empty store becomes structural rather than asserted, so
the guard has nothing to catch. This does not fix the defect and is not a
substitute for fixing it.

## The fix, for after Rung A

At the call site, ask the question in canonical terms -- `store.residual(market)`,
or `canonical_side()` on the two side words before calling `position()`. Add a
test that books a fill through `apply_fill` and then asserts the refusal fires,
so the negative control exercises the real writer instead of hand-seeded keys.
Re-certify; the closure does not change.
