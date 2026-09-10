"""HALT -> cancel -> VERIFY -> stop -> preserve -> disarm.

OWNER 2026-09-10, after the R5F halt on MIL -2.5: "We should not design a
live autonomous system whose safe shutdown procedure is 'Brett, open the app
and cancel whatever is left.'" The defect was structural -- sweep_orphan_orders
sat inside the `if armed:` branch, so disarming was the exact transition that
could orphan a resting order, and the disarmed process that came up next never
swept. These tests pin the fix and, more importantly, pin that it FAILS CLOSED.
"""
from __future__ import annotations

import pytest

from src import pmus_l5_continuous_session as cont


class FakeQ1Base:
    def __init__(self, pages):
        self.pages = list(pages)
        self.reads = 0

    def _get_with_retry(self, t, path):
        assert path == "/v1/orders/open"
        self.reads += 1
        page = self.pages[min(self.reads - 1, len(self.pages) - 1)]
        if isinstance(page, Exception):
            raise page
        return page


@pytest.fixture
def wired(monkeypatch):
    def _wire(pages, *, ours=("m1",), sweep=None, held=("m1",)):
        fake = FakeQ1Base(pages)
        monkeypatch.setattr(cont.base.q1, "base", fake, raising=False)
        monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)
        monkeypatch.setattr(cont, "ledger_slugs", lambda: set(ours))
        monkeypatch.setattr(cont, "held_markets", lambda t: list(held))
        monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: (
            sweep if sweep is not None
            else {"seen": 1, "cancelled": [{"order_id": "o1", "slug": "m1",
                                            "confirmed": True}],
                  "foreign": [], "errors": []}))
        return fake
    return _wire


def test_verified_only_when_a_fresh_read_shows_ours_gone(wired):
    wired([{"orders": []}])
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_VERIFIED"
    assert rep["still_open_ours"] == []


def test_verification_is_a_second_read_not_the_sweep_s_own_word(wired):
    """The whole point. A cancel the venue did not honour is indistinguishable
    from one it did, looking only at the sweep's return value."""
    fake = wired([{"orders": [{"id": "o1", "marketSlug": "m1"}]}],
                 sweep={"seen": 1, "cancelled": [{"order_id": "o1",
                                                  "slug": "m1",
                                                  "confirmed": True}],
                        "foreign": [], "errors": []})
    rep = cont.safe_shutdown(t=object())
    assert fake.reads == 1, "must re-read the venue after sweeping"
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"] == "ours_still_open"
    assert rep["still_open_ours"] == [{"order_id": "o1", "slug": "m1"}]


def test_a_failed_verify_read_is_not_verified_never_assumed_clean(wired):
    wired([ConnectionError("boom")])
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"].startswith("verify_read_failed:")


def test_sweep_errors_block_verification(wired):
    wired([{"orders": []}],
          sweep={"seen": 1, "cancelled": [], "foreign": [],
                 "errors": ["o1:Timeout"]})
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"] == "sweep_errors"


def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: False)
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_NOT_VERIFIED"
    assert rep["reason"] == "no_credentials"


def test_a_foreign_order_does_not_block_our_shutdown_but_is_reported(wired):
    """Not ours to cancel. Reporting it is the honest outcome; refusing to
    confirm OUR shutdown because someone else has an order would make the
    procedure unusable."""
    wired([{"orders": [{"id": "x9", "marketSlug": "not-ours"}]}])
    rep = cont.safe_shutdown(t=object())
    assert rep["verdict"] == "SHUTDOWN_VERIFIED"
    assert rep["foreign_open"] == [{"order_id": "x9", "slug": "not-ours"}]


def test_held_inventory_is_preserved_and_reported_never_liquidated(wired):
    wired([{"orders": []}], held=("m1", "m2"))
    rep = cont.safe_shutdown(t=object())
    assert rep["held_preserved"] == ["m1", "m2"]
    # scan the EXECUTABLE body, not the docstring -- prose about "stop
    # submitting" is not a call to submit, and a test that cannot tell the
    # difference would fail for the wrong reason and pass for the wrong one
    import ast
    tree = ast.parse(open(cont.__file__).read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "safe_shutdown")
    stmts = fn.body[1:] if (isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)
                            and isinstance(fn.body[0].value.value, str)) else fn.body
    code = "\n".join(ast.unparse(st) for st in stmts)
    for banned in ("place_order", "submit_order", "run_session", "sell"):
        assert banned not in code, f"shutdown path must never {banned}"
    calls = {ast.unparse(n.func) for n in ast.walk(ast.parse(code))
             if isinstance(n, ast.Call)}
    assert "sweep_orphan_orders" in calls
    assert not any("place" in c or "submit" in c for c in calls), calls


def test_shutdown_short_circuits_before_armed_is_consulted(monkeypatch):
    """It must not matter whether the process is armed: the orphan window is
    created BY disarming, so a shutdown that only works while armed fixes
    nothing."""
    monkeypatch.setenv(cont.SHUTDOWN_ENV, "1")
    seen = {}
    monkeypatch.setattr(cont, "safe_shutdown",
                        lambda t=None: {"verdict": "SHUTDOWN_VERIFIED"})
    monkeypatch.setattr(cont.base, "armed",
                        lambda: seen.setdefault("armed_consulted", True))
    rep = cont.run_campaign(t=object(), campaign_s=0.0)
    assert rep["mode"] == "SHUTDOWN"
    assert rep["status"] == "SHUTDOWN_VERIFIED"
    assert rep["sessions"] == []
    assert "armed_consulted" not in seen


def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(cont.SHUTDOWN_ENV, raising=False)
    assert cont.shutdown_requested() is False
    for on in ("1", "YES", "yes", "true"):
        monkeypatch.setenv(cont.SHUTDOWN_ENV, on)
        assert cont.shutdown_requested() is True
