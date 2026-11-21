"""The settlement sweep's `not held` gate, and what actually keeps it alive.

WHY THIS FILE EXISTS. The P1 commit (4be41090) kept the sweep's `not held`
gate and justified it like this:

    "The `not held` gate STAYS. The loop already alternates (open_cycle), so
     `held` is None every other pass and the sweep runs continuously anyway"

THAT REASON IS FALSE at the rung we actually run. The alternation is

    open_cycle = capacity and (_hold_rr["i"] % 2 == 0)
    held = None if (open_cycle or not held_all) else _next_held(held_all)

so it is gated on `capacity`, and

    capacity = len(acquiring) < concurrency and not allowance_spent
    acquiring = [s for s in held if s not in dormant]

At concurrency=1 with one NON-dormant held market, acquiring has one entry,
capacity is False, open_cycle is never True, `held` is never None and the
sweep NEVER RUNS. Not "less often" -- never.

WHAT ACTUALLY KEEPS IT ALIVE IS RESIDUAL QUARANTINE. A held market that has
gone quiescent becomes dormant, leaves `acquiring`, frees the slot, and
capacity returns -- and only then does the alternation resume and the sweep
run. LIVE 2026-09-13: jayfri reconciled at 14:01:57Z while cov-bha was still
held, which is only possible with capacity True, which at concurrency=1 is
only possible with cov-bha dormant.

So the deployed configuration is safe, and it is safe FOR A REASON NOBODY
WROTE DOWN: timely settlement reconciliation at concurrency=1 depends on
PMUS_RESIDUAL_QUARANTINE being enabled. Turning quarantine off -- a change
that looks like it only affects acquisition scheduling -- would silently
starve settlement auditing completely whenever a position is held.

These tests pin that dependency so it cannot be removed by accident.
"""
import os

os.environ.setdefault("PMUS_Q1_STATE_DIR", "/tmp/pmus-sweep-liveness-test")

from src import pmus_l5_continuous_session as S


def _sweep_runs(held_all, dormant, concurrency, hold_rr):
    """Replay the loop's own two lines against the real acquisition_state."""
    slots = S.acquisition_state(held_all, dormant=dormant,
                                concurrency=concurrency, allowance_spent=False)
    open_cycle = slots["capacity"] and (hold_rr % 2 == 0)
    held = None if (open_cycle or not held_all) else "<a held market>"
    return held is None


def test_flat_account_always_sweeps():
    for i in range(6):
        assert _sweep_runs([], dormant=set(), concurrency=1, hold_rr=i)


def test_one_non_dormant_held_market_starves_the_sweep_completely():
    """THE DEFECT THE P1 COMMENT DENIED. Not every other pass -- never."""
    for i in range(200):
        assert not _sweep_runs(["m1"], dormant=set(), concurrency=1, hold_rr=i)


def test_quarantine_is_what_restores_the_sweep():
    """Same market, same rung; the ONLY difference is dormancy."""
    starved = [_sweep_runs(["m1"], set(), 1, i) for i in range(6)]
    healed = [_sweep_runs(["m1"], {"m1"}, 1, i) for i in range(6)]
    assert starved == [False] * 6
    assert healed == [True, False, True, False, True, False]


def test_the_dependency_holds_for_every_held_count_at_concurrency_one():
    for n in range(1, 8):
        held = [f"m{k}" for k in range(n)]
        assert not any(_sweep_runs(held, set(), 1, i) for i in range(12)), \
            f"{n} non-dormant held markets should starve the sweep"
        assert any(_sweep_runs(held, set(held), 1, i) for i in range(12)), \
            f"{n} dormant held markets should let the sweep run"


def test_higher_concurrency_leaves_a_free_slot_and_sweeps_without_quarantine():
    """At concurrency 2 one held market still leaves capacity, so the
    alternation the P1 comment described does happen -- which is why the
    claim looked right. It is right only above concurrency 1."""
    assert any(_sweep_runs(["m1"], set(), 2, i) for i in range(6))
    assert not any(_sweep_runs(["m1", "m2"], set(), 2, i) for i in range(6))


def test_acquisition_state_is_the_single_definition_both_halves_read():
    """If this function stops deciding capacity, every test above is moot."""
    st = S.acquisition_state(["m1"], dormant=set(), concurrency=1,
                             allowance_spent=False)
    assert st["acquiring"] == ["m1"] and st["capacity"] is False
    st = S.acquisition_state(["m1"], dormant={"m1"}, concurrency=1,
                             allowance_spent=False)
    assert st["acquiring"] == [] and st["capacity"] is True
