"""A halted probe must still learn that its markets resolved.

OWNER 2026-09-10, step 6 of the recovery: "Restore/reconcile the 17 pending
settlements before calculating the current combined loss budget."

THE GAP THIS CLOSES. The settlement auditor ran inside the campaign loop.
`shutdown_requested()` returns before `armed()` is ever consulted -- which is
the whole point of it -- so the halted process reached `shutdown_idle` and sat
there logging a heartbeat while 17 markets stayed PENDING forever. Worse, the
combined budget that refused the lane stayed frozen at the number that tripped
it: a settlement that turned an unsettled floor into a realized gain could
never be seen, so the brake could never release on evidence.

WHAT IT MUST NOT BECOME. This runs on the shutdown path, so it is read-only by
construction: settle_held reads the venue's POSITION_RESOLUTION activity and
appends to OUR ledger. No order transport, no re-arm, no mark-to-market. The
existing reachability test in test_safe_shutdown.py now walks this path too.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_spring_l5_session as base


class _Stop(Exception):
    pass


def _sleep_n(n: int):
    calls = {"n": 0}

    def _s(_):
        calls["n"] += 1
        if calls["n"] >= n:
            raise _Stop
    return _s, calls


# ------------------------------------------------------- read-only by design
def test_no_transport_means_no_venue_read_at_all():
    """A bare idle call must not reach the venue. `t is None` is the guard,
    and it is checked before anything else."""
    out = cont.shutdown_settle_pass(None)
    assert out["errors"] == ["no_transport"]
    assert out["settled_now"] == [] and out["still_pending"] == []
    assert out["combined_loss_usd"] is None


def test_the_pass_never_raises_even_when_everything_fails(monkeypatch):
    """A halted process that dies while reporting is worse than one that
    reports an error."""
    monkeypatch.setattr(cont, "unsettled_slugs",
                        lambda: (_ for _ in ()).throw(OSError("disk")))
    out = cont.shutdown_settle_pass(object())
    assert out["errors"] == ["unsettled_slugs:OSError"]


def test_missing_credentials_is_reported_not_silently_zero(monkeypatch):
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: ["m1"])
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: False)
    out = cont.shutdown_settle_pass(object())
    assert out["errors"] == ["no_credentials"]
    assert out["pending_before"] == ["m1"]
    assert out["settled_now"] == []


def test_it_carries_the_read_only_label():
    out = cont.shutdown_settle_pass(None)
    assert "no order path" in out["labels"]


# --------------------------------------------------- it actually reconciles
def _pending(monkeypatch, slugs, results):
    seq = iter(results)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: list(slugs))
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(cont, "settle_held", lambda t, s, walk=None: next(seq))
    # Pin the round-robin cursor. The settle pass is now BOUNDED and rotates,
    # and that cursor is durable, so without pinning it these tests inherit a
    # start offset from whatever ran before and the result sequence lands on
    # the wrong slug. Rotation is correct in production -- order does not
    # affect the budget, which prices the whole unsettled set -- but a test
    # that asserts per-slug outcomes has to fix the starting point.
    monkeypatch.setattr(cont, "load_settle_cursor", lambda: 0)
    monkeypatch.setattr(cont, "save_settle_cursor", lambda i: None)


def test_a_resolved_market_moves_out_of_pending(monkeypatch):
    """THE POINT OF STEP 6."""
    _pending(monkeypatch, ["a", "b"],
             [{"status": "SETTLED", "settled": True,
               "realized_confirmed": True, "realized_usd": "1.92",
               "realized_source": "afterPosition.realized"},
              {"status": "PENDING_SETTLEMENT", "settled": False}])
    out = cont.shutdown_settle_pass(object())
    assert [r["slug"] for r in out["settled_now"]] == ["a"]
    assert out["settled_now"][0]["realized_usd"] == "1.92"
    assert [r["slug"] for r in out["still_pending"]] == ["b"]


def test_an_unconfirmed_realized_is_carried_as_unconfirmed(monkeypatch):
    """Settled is not the same fact as realized-confirmed-by-venue, and the
    row must not blur them."""
    _pending(monkeypatch, ["a"],
             [{"status": "SETTLED", "settled": True,
               "realized_confirmed": False, "realized_usd": None,
               "realized_source": None}])
    out = cont.shutdown_settle_pass(object())
    row = out["settled_now"][0]
    assert row["realized_confirmed"] is False
    assert row["realized_usd"] is None


def test_an_audit_failure_is_an_error_not_a_settlement(monkeypatch):
    """HTTP 429 was the live failure at 04:45:24Z. It must never read as
    'nothing pending'."""
    _pending(monkeypatch, ["a"],
             [{"status": "AUDIT_UNAVAILABLE", "error": "HTTPError:429"}])
    out = cont.shutdown_settle_pass(object())
    assert out["settled_now"] == []
    # asserted on the meaningful FIELDS, not on exact dict equality: the record
    # gained per-market timing when the pass was bounded, and a diagnostic
    # record must be free to grow without breaking the property under test.
    assert [ {"slug": r["slug"], "status": r["status"]}
             for r in out["still_pending"] ] == \
        [{"slug": "a", "status": "AUDIT_UNAVAILABLE"}]
    assert out["errors"] == ["a:HTTPError:429"]


# ------------------------------------------------ the budget is RE-priced
def test_the_budget_is_recomputed_after_settling_not_before(monkeypatch):
    """The bug this exists to prevent: reporting the budget that tripped
    rather than the budget as it now stands. unsettled_slugs() is read a
    SECOND time, after the settle loop, so a market that just resolved no
    longer contributes its floor."""
    calls = {"n": 0}

    def _slugs():
        calls["n"] += 1
        return ["a"] if calls["n"] == 1 else []      # 'a' settled in between
    monkeypatch.setattr(cont, "unsettled_slugs", _slugs)
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(cont, "settle_held",
                        lambda t, s, walk=None: {"status": "SETTLED", "settled": True,
                                      "realized_confirmed": True,
                                      "realized_usd": "-6.52"})
    monkeypatch.setattr(cont.base.q1, "realized_today", lambda: D("-6.52"))
    # Pin the stop: the ACTIVE rung supplies it, and the suite's default rung
    # is the -$1.00 one, not the -$15.00 the live probe runs. Asserting
    # against an assumed -15 made this fail for a reason that had nothing to
    # do with what it tests.
    monkeypatch.setattr(cont.base, "daily_loss_stop", lambda: D("-15.00"))
    out = cont.shutdown_settle_pass(object())
    assert calls["n"] >= 2, "the budget must be priced from the POST-settle set"
    assert out["unsettled_floor_usd"] == "0"          # nothing held any more
    assert out["combined_loss_usd"] == "-6.52"
    assert out["combined_budget_usd"] == "-15.00"
    assert out["budget_permits_normal_operation"] is True


def test_a_tripped_budget_is_reported_as_tripped(monkeypatch):
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(cont.base.q1, "realized_today", lambda: D("-15.00"))
    monkeypatch.setattr(cont.base, "daily_loss_stop", lambda: D("-15.00"))
    out = cont.shutdown_settle_pass(object())
    assert out["combined_loss_usd"] == "-15.00"
    assert out["budget_permits_normal_operation"] is False, \
        "-15.00 is INCLUSIVE: the stop fires AT the threshold"


def test_the_budget_figure_is_the_live_helper_not_a_literal(monkeypatch):
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    out = cont.shutdown_settle_pass(None)
    assert out["combined_budget_usd"] == str(base.daily_loss_stop())


def test_a_budget_computation_failure_does_not_lose_the_settlements(
        monkeypatch):
    """Settlements already reconciled are real work; a later arithmetic
    failure must not discard them."""
    _pending(monkeypatch, ["a"],
             [{"status": "SETTLED", "settled": True,
               "realized_confirmed": True, "realized_usd": "1.00"}])
    monkeypatch.setattr(cont.base.q1, "realized_today",
                        lambda: (_ for _ in ()).throw(ValueError("bad")))
    out = cont.shutdown_settle_pass(object())
    assert [r["slug"] for r in out["settled_now"]] == ["a"]
    assert out["errors"] == ["budget:ValueError"]
    assert out["combined_loss_usd"] is None


# ------------------------------------------------------- wiring into idle
def test_the_idle_loop_runs_the_pass_every_beat(monkeypatch):
    """Defined but never called would leave step 6 exactly where it was."""
    seen = {"n": 0}
    monkeypatch.setattr(cont, "shutdown_settle_pass",
                        lambda t=None: seen.__setitem__("n", seen["n"] + 1))
    s, calls = _sleep_n(3)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "SHUTDOWN_VERIFIED"},
                           t=object())
    assert seen["n"] == 3 == calls["n"]


def test_the_settlement_row_reaches_the_log(monkeypatch, capsys):
    monkeypatch.setattr(cont, "shutdown_settle_pass",
                        lambda t=None: {"settled_now": [{"slug": "a"}],
                                        "combined_loss_usd": "-6.52"})
    s, _ = _sleep_n(1)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "SHUTDOWN_VERIFIED"},
                           t=object())
    out = capsys.readouterr().out
    assert '"kind":"shutdown_idle"' in out          # _log emits COMPACT json
    assert '"combined_loss_usd":"-6.52"' in out


def test_idle_still_never_returns_with_the_pass_wired_in(monkeypatch):
    """The property the whole shutdown design rests on. Adding work inside
    the loop must not give it an exit."""
    monkeypatch.setattr(cont, "shutdown_settle_pass", lambda t=None: {})
    s, calls = _sleep_n(4)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "SHUTDOWN_NOT_VERIFIED"},
                           t=object())
    assert calls["n"] == 4


def test_a_bare_idle_call_still_works_and_reads_nothing(monkeypatch):
    """Back-compat: `t` defaults to None, so every existing caller and test
    keeps its old behaviour and cannot touch the venue."""
    s, calls = _sleep_n(2)
    with pytest.raises(_Stop):
        cont.shutdown_idle(sleep=s, report={"verdict": "SHUTDOWN_VERIFIED"})
    assert calls["n"] == 2


def test_run_campaign_hands_the_transport_to_idle():
    """safe_shutdown builds its own transport and drops it. If run_campaign
    did not materialise one, the halted process could never reconcile."""
    import ast
    src = open(cont.__file__).read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_campaign")
    body = ast.unparse(fn)
    assert "shutdown_idle(sleep, rep, t)" in body
    i_t = body.index("base.Transport()")
    assert i_t < body.index("shutdown_idle(sleep, rep, t)")
    assert "base.q1.has_credentials()" in body[:i_t]


def test_the_pass_cannot_re_arm_or_submit():
    """Reachability from the new function, same rule as the shutdown path."""
    import ast
    tree = ast.parse(open(cont.__file__).read())
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef)}
    reachable, frontier = set(), ["shutdown_settle_pass"]
    while frontier:
        name = frontier.pop()
        if name in reachable or name not in fns:
            continue
        reachable.add(name)
        for call in ast.walk(fns[name]):
            if isinstance(call, ast.Call):
                f = call.func
                frontier.append(f.id if isinstance(f, ast.Name)
                                else getattr(f, "attr", ""))
    for banned in ("run_session", "place_order", "submit_order", "post_order",
                   "armed", "run_campaign"):
        assert banned not in reachable, banned
