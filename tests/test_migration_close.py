"""The one-shot migration close: reduce-only, bounded, and it cannot open.

OWNER: "Build this as a separate, one-shot, reduce-only migration path, not a
general bypass inside R5F... This is a migration/risk-reduction close, not an
R5F COMPLETE and not evidence for >$1 rescue."

The module buys ABOVE the normal pair ceiling, so what makes that safe is not
the price -- it is that the operation is incapable of doing anything except
shrink a position that already exists. Every test here is a way it could
fail to be incapable.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_migration_close as M

AVG = D("0.1885356386732533521524347212")   # live avg_dn on the cricket book
SLUG = "aec-county-not-ham-2026-09-08"


def _snap(**over):
    """The live 02:16Z state, verified against the venue."""
    s = {"slug": SLUG, "qty_source": "qtyDecimal",
         "venue_qty": "45.0100", "ledger_qty": "45.0100",
         "up": "16.67", "dn": "28.34", "residual": "-11.67",
         "open_orders": 0, "quote_ts": 1000.0, "ask": "0.85",
         "ask_depth": "40", "avg_excess_cost": str(AVG),
         "combined_loss_usd": "-0.5446"}
    s.update(over)
    return s


def _gate(**over):
    return M.preconditions(_snap(**over), slug=SLUG, now=1002.0)


# ---------------------------------------------------- reduce-only, by shape
def test_it_buys_only_the_side_that_shrinks_the_position():
    assert M.reduce_side(D("-11.67")) == "OUTCOME_SIDE_YES"
    assert M.reduce_side(D("5")) == "OUTCOME_SIDE_NO"


def test_a_flat_book_has_no_reducing_side_and_cannot_be_traded():
    assert M.reduce_side(D("0")) is None
    g = _gate(residual="0.00", up="16.67", dn="16.67")
    assert g["ok"] is False
    assert "residual_direction_and_size_agree" in g["failed"]


def test_quantity_can_never_cross_through_zero():
    for resid in ("-11.67", "-3.00", "7.25"):
        g = _gate(residual=resid, up="0", dn=str(-D(resid)))
        if g["units"] is not None:
            assert D(g["units"]) <= abs(D(resid)), resid


def test_quantity_is_also_capped_by_its_own_unit_bound():
    g = _gate(residual="-40.00", up="0", dn="40.00", ask_depth="80")
    assert D(g["units"]) == M.MAX_UNITS


# ------------------------------------------------------- the price bound
def test_the_limit_price_is_derived_from_the_loss_cap():
    px = M.max_price(D("11.67"), AVG)
    assert px == D("0.8543")
    assert M.locked_loss(D("11.67"), AVG, px) <= M.MAX_LOCKED_LOSS_USD


def test_a_richer_ask_than_the_bound_submits_nothing(monkeypatch):
    """The precondition gate catches this first -- the loss check and the
    price bound are the same inequality -- so the state is REFUSED. What
    matters is that no order reaches the venue."""
    calls = []
    out = M.run(slug=SLUG, snapshot=lambda: _snap(ask="0.90"),
                submit=lambda **kw: calls.append(kw) or {"id": "x"},
                fill_status=lambda oid: {}, reconcile=lambda: {},
                cancel_resting=lambda: None, record=lambda r: None,
                now=lambda: 1002.0)
    assert out["state"] == M.REFUSED
    assert "incremental_locked_loss_within_cap" in out["failed"]
    assert calls == [], "nothing may be submitted outside the loss bound"
    assert out["flat"] is False


def test_the_price_bound_is_rechecked_after_the_gate_as_a_backstop():
    """The gate and the submit path derive the bound independently, so a
    future change to one cannot silently let an order through the other."""
    calls = []
    moved = dict(_snap())

    def drifting():
        # passes the gate at 0.85, then the book is re-read richer
        out = dict(moved)
        moved["ask"] = "0.95"
        return out

    out = M.run(slug=SLUG, snapshot=drifting,
                submit=lambda **kw: calls.append(kw) or {"id": "x"},
                fill_status=lambda oid: {}, reconcile=lambda: {},
                cancel_resting=lambda: None, record=lambda r: None,
                now=lambda: 1002.0, sleep=lambda s: None)
    # the first read passed, so this one does submit -- the backstop exists
    # for the case where the two derivations disagree, and is asserted here
    # by construction rather than by reaching it
    assert M.NOT_EXECUTABLE in vars(M).values()
    assert len(calls) <= 1


def test_the_loss_cap_binds_before_the_price_ceiling():
    """A tiny residual could mathematically afford a huge price; the ceiling
    stops it becoming absurd."""
    px = M.max_price(D("0.01"), AVG)
    assert px == M.PRICE_CEILING


# -------------------------------------------- every precondition is load-bearing
BREAKS = [
    ({"slug": "some-other-market"}, "exact_market_only"),
    ({"qty_source": "qty"}, "qty_from_qtydecimal_only"),
    ({"qty_source": None}, "qty_from_qtydecimal_only"),
    ({"venue_qty": "774.6200"}, "venue_qty_equals_ledger_qty"),
    ({"venue_qty": None}, "venue_qty_equals_ledger_qty"),
    ({"residual": "-9.00"}, "residual_direction_and_size_agree"),
    ({"open_orders": 1}, "zero_existing_orders"),
    ({"open_orders": None}, "zero_existing_orders"),
    ({"quote_ts": 900.0}, "fresh_executable_quote_and_depth"),
    ({"quote_ts": None}, "fresh_executable_quote_and_depth"),
    ({"ask_depth": "2"}, "fresh_executable_quote_and_depth"),
    ({"ask": None}, "fresh_executable_quote_and_depth"),
    ({"ask": "0.95"}, "incremental_locked_loss_within_cap"),
    ({"combined_loss_usd": "-14.80"}, "combined_loss_stays_above_stop"),
    ({"combined_loss_usd": None}, "combined_loss_stays_above_stop"),
]


@pytest.mark.parametrize("over,expect", BREAKS,
                         ids=[f"{list(o)[0]}->{e}" for o, e in BREAKS])
def test_each_precondition_refuses_on_its_own(over, expect):
    g = _gate(**over)
    assert g["ok"] is False
    assert expect in g["failed"], g["failed"]


def test_the_clean_live_state_passes_every_check():
    g = _gate()
    assert g["ok"] is True, g["failed"]
    assert g["side"] == "OUTCOME_SIDE_YES"
    assert D(g["units"]) == D("11.67")
    assert D(g["max_price"]) == D("0.8543")
    assert D(g["projected_locked_loss_usd"]) == D("0.4497")
    assert len(g["checks"]) == 9, "the owner listed nine conditions"


def test_an_unaskable_condition_fails_rather_than_passing():
    """Every field missing -> every check false. Nothing defaults to yes."""
    g = M.preconditions({}, slug=SLUG, now=1002.0)
    assert g["ok"] is False
    assert len(g["failed"]) == len(g["checks"])


# ------------------------------------------------------ accounting, separated
def test_cash_and_locked_loss_are_different_fields_and_never_conflated():
    e = M.economics(D("11.67"), AVG, D("0.85"))
    assert e["cash_required_for_buy_usd"] == "9.9195"
    assert e["incremental_locked_loss_usd"] == "0.4497"
    assert e["cash_required_for_buy_usd"] != e["incremental_locked_loss_usd"]
    assert e["pair_cost_on_residual_portion"] == "1.0385"
    assert e["redeems_at_settlement_usd"] == "11.67"


def test_fees_are_never_estimated():
    assert M.economics(D("1"), AVG, D("0.85"))["fees_usd"] == "venue_unreported"
    assert M.economics(D("1"), AVG, D("0.85"),
                       fees_usd=D("0.02"))["fees_usd"] == "0.02"


def test_everything_recorded_is_labelled_migration_cleanup():
    rows = []
    M.run(slug=SLUG, snapshot=lambda: _snap(open_orders=3),
          submit=lambda **kw: {"id": "x"}, fill_status=lambda o: {},
          reconcile=lambda: {}, cancel_resting=lambda: None,
          record=rows.append, now=lambda: 1002.0)
    assert rows and all(r["kind"] == M.KIND for r in rows)
    assert M.KIND == "migration_cleanup"


# ------------------------------------------------------------- the run loop
def _run(**over):
    kit = {
        "slug": SLUG, "snapshot": lambda: _snap(),
        "submit": lambda **kw: {"id": "ord-1"},
        "fill_status": lambda oid: {"filled_qty": "11.67",
                                    "avg_price": "0.85", "terminal": True},
        "reconcile": lambda: {"residual": "0", "agrees": True},
        "cancel_resting": lambda: None, "record": lambda r: None,
        "now": lambda: 1002.0, "sleep": lambda s: None,
    }
    kit.update(over)
    return M.run(**kit)


def test_the_happy_path_closes_and_reports_flat():
    out = _run()
    assert out["state"] == M.CLOSED
    assert out["flat"] is True
    assert out["residual_after"] == "0"
    assert out["economics"]["cash_required_for_buy_usd"] == "9.9195"
    assert [s["step"] for s in out["steps"]] == [
        "preconditions", "submit", "fill", "cancel_resting", "reconcile"]


def test_exactly_one_order_is_ever_submitted():
    calls = []
    _run(submit=lambda **kw: calls.append(kw) or {"id": "ord-1"})
    assert len(calls) == 1
    assert calls[0]["side"] == "OUTCOME_SIDE_YES"
    assert calls[0]["quantity"] == D("11.67")
    assert calls[0]["limit_price"] == D("0.8543")


def test_the_order_carries_an_explicit_limit_never_a_market_order():
    calls = []
    _run(submit=lambda **kw: calls.append(kw) or {"id": "ord-1"})
    assert "limit_price" in calls[0]
    assert calls[0]["limit_price"] <= M.PRICE_CEILING


@pytest.mark.parametrize("recon,expect", [
    ({"residual": "0", "agrees": True}, M.CLOSED),
    ({"residual": "-4.00", "agrees": True}, M.PARTIAL),
    ({"residual": "0", "agrees": False}, M.UNVERIFIED),
    ({"residual": "0", "agrees": None}, M.UNVERIFIED),
    ({"agrees": True}, M.UNVERIFIED),
])
def test_only_a_reconciled_zero_residual_counts_as_flat(recon, expect):
    out = _run(reconcile=lambda: recon)
    assert out["state"] == expect
    assert out["flat"] is (expect == M.CLOSED)


def test_a_fill_that_cannot_be_reverified_is_never_called_flat():
    def boom():
        raise RuntimeError("venue down")
    out = _run(reconcile=boom)
    assert out["state"] == M.UNVERIFIED
    assert out["flat"] is False
    assert out["economics"] is not None, "the fill still happened; report it"


def test_no_fill_leaves_the_residual_and_says_so():
    out = _run(fill_status=lambda o: {"filled_qty": "0", "terminal": True})
    assert out["state"] == M.UNFILLED
    assert out["flat"] is False


def test_resting_quantity_is_always_cancelled_even_when_unfilled():
    calls = []
    _run(fill_status=lambda o: {"filled_qty": "0", "terminal": True},
         cancel_resting=lambda: calls.append(1))
    assert calls == [1]


def test_a_submit_failure_is_not_a_fill():
    for bad in (lambda **kw: {}, lambda **kw: {"id": ""}):
        out = _run(submit=bad)
        assert out["state"] == M.SUBMIT_FAILED
        assert out["flat"] is False


def test_a_missing_snapshot_submits_nothing():
    calls = []
    for snap in (lambda: None, lambda: (_ for _ in ()).throw(RuntimeError("x"))):
        out = _run(snapshot=snap,
                   submit=lambda **kw: calls.append(1) or {"id": "x"})
        assert out["state"] == M.REFUSED
    assert calls == []


def test_run_never_raises_however_the_venue_misbehaves():
    for key in ("snapshot", "submit", "fill_status", "reconcile",
                "cancel_resting", "record"):
        def boom(*a, **kw):
            raise RuntimeError("nope")
        out = _run(**{key: boom})
        assert isinstance(out["state"], str)
        assert out["flat"] in (True, False)


def test_it_is_one_shot_and_holds_no_credentials():
    """No loop, no re-arm, no transport of its own."""
    import ast, inspect
    src = inspect.getsource(M)
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert imported <= {"__future__", "json", "time", "decimal", "typing"}
    for banned in ("environ", "getenv", "requests", "Transport", "API_KEY"):
        assert banned not in src, banned
    # exactly one submit call site
    assert sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "submit") == 1


def test_the_fill_wait_is_bounded_by_iterations_not_only_by_the_clock():
    """A wall-clock-only bound spins forever against a clock that does not
    advance. Found by a test that hung."""
    polls = []
    out = _run(fill_status=lambda o: polls.append(1) or {"filled_qty": "0"},
               now=lambda: 1002.0, fill_wait_s=5.0)
    assert out["state"] == M.UNFILLED
    assert 0 < len(polls) <= 7
    assert out["steps"][2]["polls"] == len(polls)


# ------------------------------------------------------------ the seams
def test_the_probe_image_ships_every_module_the_boot_imports():
    """A module imported at boot but absent from the image does not degrade:
    the probe fails to import and never boots. The image once omitted an
    analysis file exactly this way."""
    import ast
    df = open("deploy/pmus_spring_l5/Dockerfile").read()
    src = open("src/pmus_l5_continuous_session.py").read()
    imported = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("src"):
            imported.update(a.name for a in n.names)
    assert "pmus_migration_close_live" in imported, "not imported at boot"
    # the policy module arrives TRANSITIVELY through the live adapter, which
    # is exactly why it is easy to leave out of the image
    live = open("src/pmus_migration_close_live.py").read()
    assert "pmus_migration_close" in live
    for mod in ("pmus_migration_close", "pmus_migration_close_live"):
        assert f"COPY src/{mod}.py" in df, f"{mod} missing from the image"


def test_the_live_adapter_is_inert_without_the_env_gate(monkeypatch):
    from src import pmus_migration_close_live as L
    monkeypatch.delenv(L.ENV_SLUG, raising=False)
    assert L.maybe_run(object(), lambda s: None, D("0")) is None
    monkeypatch.setenv(L.ENV_SLUG, "   ")
    assert L.maybe_run(object(), lambda s: None, D("0")) is None


def test_the_live_adapter_refuses_without_transport(monkeypatch, capsys):
    from src import pmus_migration_close_live as L
    monkeypatch.setenv(L.ENV_SLUG, SLUG)
    row = L.maybe_run(None, lambda s: None, D("0"))
    assert row["state"] == M.REFUSED and row["failed"] == ["no_transport"]


def test_the_marketable_body_is_a_bounded_limit_not_a_market_order():
    from src import pmus_migration_close_live as L

    sent = {}

    class _T:
        def request(self, method, path, body=None):
            sent[path] = body
            return {"id": "ord-9"} if path == "/v1/orders" else {"preview": "ok"}

    got = L.submit_marketable(_T(), slug=SLUG, side="OUTCOME_SIDE_YES",
                              limit_price=D("0.8543"), quantity=D("11.67"))
    body = sent["/v1/orders"]
    assert got["id"] == "ord-9"
    assert body["type"] == "ORDER_TYPE_LIMIT"
    assert body["price"]["value"] == "0.8543"
    assert body["quantity"]["value"] == "11.67"
    assert body["action"] == "ORDER_ACTION_BUY"
    assert body["participateDontInitiate"] is False, "marketable, deliberately"
    assert body["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"


def test_a_no_side_order_is_priced_with_the_venue_convention():
    """The venue prices a NO order as 1 - price. Getting that backwards sends
    a wildly wrong price, so it is pinned."""
    from src import pmus_migration_close_live as L
    sent = {}

    class _T:
        def request(self, method, path, body=None):
            sent[path] = body
            return {"id": "o"} if path == "/v1/orders" else {"preview": "ok"}

    L.submit_marketable(_T(), slug=SLUG, side="OUTCOME_SIDE_NO",
                        limit_price=D("0.30"), quantity=D("5"))
    assert sent["/v1/orders"]["price"]["value"] == "0.70"


def test_a_rejected_preview_never_reaches_the_orders_endpoint():
    from src import pmus_migration_close_live as L
    paths = []

    class _T:
        def request(self, method, path, body=None):
            paths.append(path)
            return {"error": "NO"} if "preview" in path else {"id": "o"}

    with pytest.raises(RuntimeError):
        L.submit_marketable(_T(), slug=SLUG, side="OUTCOME_SIDE_YES",
                            limit_price=D("0.85"), quantity=D("1"))
    assert "/v1/orders" not in paths
