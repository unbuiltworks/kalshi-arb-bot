"""The SECOND settlement-evidence path: public final settlementPx + zero venue
position + no open order + coherent ledger, used ONLY when the activity walk
cannot reach a market's `since` (activity_walk_short_of_since).

LIVE since >= 2026-10-26: the venue's activity history stops at ~2026-11-04,
so 96 September markets stayed pending forever, the -17.26 unsettled floor
never cleared, and R5FB refused to arm on combined_loss_above_stop.

Offline: every venue read is a fake. Nothing here can reach a network.
"""
from __future__ import annotations

import inspect
import json
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_settlement_auditor as sa
from src import pmus_settlement_public_px as ppx

SHORT = ("activity_walk_short_of_since:ended=max_pages;"
         "oldest=2026-11-04T14:21:40.208;need=2026-09-14T10:00:00+00:00")


class HTTPErr(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class FakeT:
    """bbo per slug (dict, None => 404), positions/orders responses or
    exceptions. Records every call; refuses anything but GET."""

    def __init__(self, bbo=None, positions=None, orders=None):
        self.bbo = bbo or {}
        self.positions = {"positions": []} if positions is None else positions
        self.orders = {"orders": []} if orders is None else orders
        self.calls = []

    def request(self, method, path, body=None, base=None):
        assert method == "GET", "the evidence path must never mutate"
        self.calls.append(path)
        if path.endswith("/bbo"):
            slug = path.split("/")[3]
            v = self.bbo.get(slug)
            if v is None:
                raise HTTPErr(400)          # non-transient: no retry sleeps
            if isinstance(v, Exception):
                raise v
            return v
        if path == "/v1/portfolio/positions":
            if isinstance(self.positions, Exception):
                raise self.positions
            return self.positions
        if path == "/v1/orders/open":
            if isinstance(self.orders, Exception):
                raise self.orders
            return self.orders
        raise AssertionError(f"unexpected read {path}")


def bbo(slug, px, state="MARKET_STATE_EXPIRED"):
    md = {"marketSlug": slug, "state": state}
    if px is not ...:
        md["settlementPx"] = None if px is None else {"value": px,
                                                      "currency": "USD"}
    return {"marketData": md}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(cont.base.q1.base, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(sa.q1, "state_dir", lambda: tmp_path)
    ppx.reset_cache()
    yield tmp_path
    ppx.reset_cache()


def _rows(p, rows):
    with p.joinpath("ledger.jsonl").open("a") as f:
        for r in rows:
            f.write(json.dumps({"t": "2026-09-14T10:00:00+00:00", **r}) + "\n")


def fill(slug, side, px, qty, oid, stage="spring_open", **extra):
    return [{"kind": "order_terminal", "spring": True, "slug": slug,
             "order_id": oid, "filled_qty": qty, "price": px},
            {"kind": "filled", "spring": True, "stage": stage, "slug": slug,
             "side": side, "price": px, "qty": qty, "order_id": oid, **extra}]


def long_only(p, slug):        # 10 YES @ 0.40 -> cost 4.00
    _rows(p, fill(slug, "YES", "0.40", "10", f"{slug}-a"))


def short_only(p, slug):       # 5 NO @ 0.30 -> cost 1.50
    _rows(p, fill(slug, "NO", "0.30", "5", f"{slug}-b"))


def both(p, slug):             # 10 YES @ 0.40 + 6 NO @ 0.55 -> cost 7.30
    _rows(p, fill(slug, "YES", "0.40", "10", f"{slug}-a")
          + fill(slug, "NO", "0.55", "6", f"{slug}-b", stage="spring_complete"))


# --------------------------------------------------------- the P&L arithmetic

@pytest.mark.parametrize("builder,px,want", [
    (long_only, "1.0000", "6.00"),     # 10*1 - 4.00
    (long_only, "0.0000", "-4.00"),    # 0 - 4.00
    (short_only, "1", "-1.50"),        # 5*(1-1) - 1.50
    (short_only, "0", "3.50"),         # 5*(1-0) - 1.50
    (both, "1", "2.70"),               # 10 - 7.30
    (both, "0", "-1.30"),              # 6 - 7.30
])
def test_px_one_and_zero_long_short_both(state, builder, px, want):
    builder(state, "m")
    t = FakeT(bbo={"m": bbo("m", px)})
    got = ppx.settle_by_public_px(t, "m")
    assert got["settled"] is True, got
    assert got["provenance"] == ppx.PROVENANCE
    assert D(got["realized_derived_usd"]) == D(want)
    rows = [json.loads(l) for l in state.joinpath("ledger.jsonl").read_text()
            .splitlines()]
    derived = [r for r in rows if r.get("kind") == ppx.KIND]
    assert len(derived) == 1
    assert derived[0]["provenance"] == ppx.PROVENANCE
    assert derived[0]["venue_confirmed"] is False
    assert not any(r.get("kind") == "realized" for r in rows), \
        "never a venue-confirmed `realized` row"


def test_exited_early_uses_the_ledgers_proceeds(state):
    """Bought 10 YES @0.40, sold 4 @0.70 before settlement; YES lost.
    0.70*4 + 6*0 - 4.00 = -1.20, from the LEDGER, not the venue's zero."""
    _rows(state, fill("m", "YES", "0.40", "10", "a")
          + fill("m", "YES", "0.70", "4", "s", stage="spring_exit",
                 action="ORDER_INTENT_SELL_LONG"))
    # held_inventory_from_ledger treats every fill as a buy; the cross-check
    # is on gross, which is what it rebuilds
    got = ppx.settle_by_public_px(FakeT(bbo={"m": bbo("m", "0")}), "m")
    assert got["settled"], got
    assert D(got["realized_derived_usd"]) == D("-1.20")
    assert got["ledger"]["exited_early"] is True


def test_fully_exited_before_settlement(state):
    _rows(state, fill("m", "NO", "0.30", "5", "a")
          + fill("m", "NO", "0.50", "5", "s", stage="spring_exit",
                 action="SELL"))
    for px in ("0", "1"):
        got = ppx.settle_by_public_px(FakeT(bbo={"m": bbo("m", px)}), "m",
                                      write=False)
        assert D(got["realized_derived_usd"]) == D("1.00")   # 2.50 - 1.50


def test_incoherent_ledger_stays_pending(state):
    _rows(state, fill("m", "YES", "0.40", "3", "a")
          + fill("m", "YES", "0.70", "4", "s", stage="spring_exit",
                 action="SELL"))                        # sold more than bought
    got = ppx.settle_by_public_px(FakeT(bbo={"m": bbo("m", "1")}), "m")
    assert not got["settled"] and "sold_more_than_bought" in got["reason"]


def test_ledger_correction_makes_it_incoherent(state):
    long_only(state, "m")
    _rows(state, [{"kind": sa.RECONCILE_KIND, "slug": "m", "qty": "2"}])
    got = ppx.settle_by_public_px(FakeT(bbo={"m": bbo("m", "1")}), "m")
    assert not got["settled"] and "effective_qty_mismatch" in got["reason"]


def test_a_migration_close_that_may_have_filled_is_incoherent(state):
    long_only(state, "m")
    _rows(state, [{"kind": "migration_cleanup", "slug": "m",
                   "stage": "migration_close", "state": "CLOSED_FLAT"}])
    got = ppx.settle_by_public_px(FakeT(bbo={"m": bbo("m", "1")}), "m")
    assert not got["settled"] and "migration" in got["reason"]


def test_no_ledger_fills_stays_pending(state):
    got = ppx.settle_by_public_px(FakeT(bbo={"m": bbo("m", "1")}), "m")
    assert not got["settled"] and got["reason"].startswith("ledger:")


# ------------------------------------------------- (a) public price is final

@pytest.mark.parametrize("resp", [
    bbo("m", None), bbo("m", ...), bbo("m", "0.5"), bbo("m", "0.9999"),
    bbo("m", "1", state="MARKET_STATE_OPEN"), bbo("m", "0", state=None),
    None,                                    # 404 / error
    {"unexpected": True}, bbo("other-slug", "1"),
])
def test_px_not_final_stays_pending(state, resp):
    long_only(state, "m")
    t = FakeT(bbo={"m": resp} if resp is not None else {})
    got = ppx.settle_by_public_px(t, "m")
    assert got["settled"] is False and got["reason"].startswith("public:")
    assert not any(json.loads(l).get("kind") == ppx.KIND for l in
                   state.joinpath("ledger.jsonl").read_text().splitlines())


def test_404_is_pending(state):
    long_only(state, "m")
    t = FakeT(bbo={"m": HTTPErr(404)})
    import src.pmus_q1_executor as q
    got = ppx.settle_by_public_px(t, "m")
    assert not got["settled"] and "bbo_error" in got["reason"]
    assert q is not None


# ---------------------------------------- (b) venue position and open orders

@pytest.mark.parametrize("positions,reason", [
    ({"positions": [{"marketSlug": "m", "netPosition": "3"}]}, "nonzero"),
    ({"positions": {"m": {"netPosition": "-2"}}}, "nonzero"),
    ({"positions": [], "availablePositions": [{"marketSlug": "m",
                                               "netPosition": "1"}]}, "nonzero"),
    ({"positions": [{"marketSlug": "m", "netPosition": "abc"}]}, "unparseable"),
    ({"positions": [{"marketSlug": "m"}]}, "unparseable"),
    ({}, "positions_unreadable"),
    ({"positions": "nope"}, "malformed"),
    ({"positions": [{"marketSlug": "x", "netPosition": "1"}],
      "nextCursor": "abc"}, "paginated"),
    (RuntimeError("401"), "account_read_error"),
])
def test_venue_position_not_zero_or_unreadable_stays_pending(state, positions,
                                                            reason):
    long_only(state, "m")
    t = FakeT(bbo={"m": bbo("m", "1")}, positions=positions)
    got = ppx.settle_by_public_px(t, "m")
    assert got["settled"] is False and reason in got["reason"], got


def test_zero_row_and_other_markets_are_flat(state):
    long_only(state, "m")
    t = FakeT(bbo={"m": bbo("m", "1")},
              positions={"positions": [{"marketSlug": "m", "netPosition": "0"},
                                       {"marketSlug": "x", "netPosition": "4"}],
                         "eof": True})
    assert ppx.settle_by_public_px(t, "m")["settled"] is True


@pytest.mark.parametrize("orders,reason", [
    ({"orders": [{"marketSlug": "m", "id": "o1"}]}, "open_order_present"),
    ({"orders": [{"id": "o1"}]}, "unattributable"),
    ({"orders": "x"}, "malformed"),
    ([], "open_orders_unreadable"),
    (RuntimeError("500"), "account_read_error"),
])
def test_open_order_present_or_unreadable_stays_pending(state, orders, reason):
    long_only(state, "m")
    t = FakeT(bbo={"m": bbo("m", "0")}, orders=orders)
    got = ppx.settle_by_public_px(t, "m")
    assert got["settled"] is False and reason in got["reason"], got


def test_open_order_elsewhere_does_not_block(state):
    long_only(state, "m")
    t = FakeT(bbo={"m": bbo("m", "0")},
              orders={"orders": [{"marketSlug": "other", "id": "o"}]})
    assert ppx.settle_by_public_px(t, "m")["settled"] is True


def test_never_raises(state, monkeypatch):
    long_only(state, "m")
    monkeypatch.setattr(ppx, "ledger_position",
                        lambda s: (_ for _ in ()).throw(ValueError("x")))
    got = ppx.settle_by_public_px(FakeT(), "m")
    assert got["settled"] is False and got["reason"].startswith("raised:")


def test_idempotent_one_row_per_market(state):
    long_only(state, "m")
    t = FakeT(bbo={"m": bbo("m", "1")})
    assert ppx.settle_by_public_px(t, "m")["ledger_row_written"] is True
    assert ppx.settle_by_public_px(t, "m")["ledger_row_written"] is False


# ------------------------------------------------ wiring into the shutdown pass

def _shutdown_env(monkeypatch, covers=False):
    monkeypatch.setattr(cont.base.q1, "has_credentials", lambda: True)

    def walk(t, since=None, max_pages=None):
        return {"rows": [], "pages": 25, "ended": "max_pages",
                "cursor_param": "cursor",
                "oldest_time": "2026-11-04T14:21:40.208",
                "newest_time": "2026-11-13T00:00:00", "errors": [],
                "complete_to_since": covers,
                "truncated_by_page_cap": not covers,
                "truncated_by_error": False}
    monkeypatch.setattr(sa, "activity_pages", walk)
    # the pending-diagnostic public view is not under test here
    monkeypatch.setattr(sa, "public_market_view",
                        lambda slug, t=None: {"public_resolved": False})


def _september(state, n):
    slugs = [f"aec-r6-m{i:02d}-2026-09-{6 + i % 10:02d}" for i in range(n)]
    for i, s in enumerate(slugs):
        # each market: 10 YES @ 0.40 + 6 NO @ 0.55 -> floor 6 - 7.30 = -1.30
        both(state, s)
    return slugs


def test_production_row_shape_drains(state, monkeypatch):
    """Reproduces the LIVE row: 96 pending, every error
    activity_walk_short_of_since, settled_now == []. With the fallback the
    same pass drains them, SETTLE_AUDIT_BATCH per cycle, and the floor drops
    monotonically to zero."""
    _shutdown_env(monkeypatch)
    slugs = _september(state, 96)
    bbos = {s: bbo(s, "0" if i % 2 else "1") for i, s in enumerate(slugs)}

    # BEFORE (no public price yet): exactly the production shape
    out = cont.shutdown_settle_pass(t=FakeT(bbo={}))
    assert out["pending_total"] == 96 and out["settled_now"] == []
    assert len(out["errors"]) == cont.SETTLE_AUDIT_BATCH
    assert all("RuntimeError:activity_walk_short_of_since" in e
               for e in out["errors"])
    floor0 = D(out["unsettled_floor_usd"])
    assert floor0 == D("-1.30") * 96

    # AFTER: the public price is final and the account is flat
    t = FakeT(bbo=bbos)
    floors, cycles = [floor0], 0
    while cont.unsettled_slugs():
        ppx.reset_cache()
        out = cont.shutdown_settle_pass(t=t)
        cycles += 1
        assert len(out["batch"]) <= cont.SETTLE_AUDIT_BATCH
        assert len(out["settled_now"]) == len(out["batch"])
        for r in out["settled_now"]:
            assert r["settled_by"] == ppx.PROVENANCE
            assert r["realized_confirmed"] is False
            assert r["realized_usd"] is None, "never venue-confirmed cash"
            assert r["realized_derived_usd"] in ("2.70", "-1.30")
        floors.append(D(out["unsettled_floor_usd"]))
        assert cycles < 40
    assert cycles == -(-96 // cont.SETTLE_AUDIT_BATCH)
    assert all(b >= a for a, b in zip(floors, floors[1:])), floors
    assert floors[-1] == 0
    # floor left and realized_today took it in the SAME transition
    assert D(out["realized_today_usd"]) == D("2.70") * 48 + D("-1.30") * 48
    assert D(out["combined_loss_usd"]) == D(out["realized_today_usd"])


def test_bounded_per_cycle_and_reads_shared(state, monkeypatch):
    _shutdown_env(monkeypatch)
    slugs = _september(state, 12)
    t = FakeT(bbo={s: bbo(s, "1") for s in slugs})
    out = cont.shutdown_settle_pass(t=t)
    assert len(out["settled_now"]) == cont.SETTLE_AUDIT_BATCH
    bbo_reads = [c for c in t.calls if c.endswith("/bbo")]
    assert len(bbo_reads) == cont.SETTLE_AUDIT_BATCH
    # account-scoped reads are shared by the batch, not per market
    assert t.calls.count("/v1/orders/open") == 1


def test_durable_cursor_across_restart(state, monkeypatch):
    _shutdown_env(monkeypatch)
    slugs = _september(state, 12)
    # nothing public yet: cursor advances, nothing settles
    cont.shutdown_settle_pass(t=FakeT(bbo={}))
    assert cont.load_settle_cursor() == cont.SETTLE_AUDIT_BATCH
    # "restart": process memory gone, the cursor file is not
    ppx.reset_cache()
    t = FakeT(bbo={s: bbo(s, "0") for s in slugs})
    out = cont.shutdown_settle_pass(t=t)
    assert out["cursor"] == cont.SETTLE_AUDIT_BATCH
    assert [r["slug"] for r in out["settled_now"]] == \
        slugs[cont.SETTLE_AUDIT_BATCH:2 * cont.SETTLE_AUDIT_BATCH]


def test_failed_evidence_leaves_the_production_shape_unchanged(state,
                                                              monkeypatch):
    _shutdown_env(monkeypatch)
    _september(state, 7)
    # the activity audit itself reads positions fine (it gets as far as
    # activity_walk_short_of_since); the evidence path's open-orders read fails
    out = cont.shutdown_settle_pass(
        t=FakeT(bbo={s: bbo(s, "1") for s in cont.unsettled_slugs()},
                orders=RuntimeError("500")))
    assert out["settled_now"] == []
    assert all(r["status"] == "AUDIT_UNAVAILABLE" for r in out["still_pending"])
    assert all(r["public_px_fallback"]["settled"] is False
               for r in out["still_pending"])
    assert len(cont.unsettled_slugs()) == 7


def test_a_covering_walk_uses_the_original_path_unchanged(state, monkeypatch):
    """When the walk DOES cover `since`, the activity path decides, and the
    public-px path is never consulted -- even if it would have settled."""
    _shutdown_env(monkeypatch, covers=True)
    slugs = _september(state, 3)
    called = []
    monkeypatch.setattr(ppx, "settle_by_public_px",
                        lambda *a, **k: called.append(a) or {"settled": True})
    t = FakeT(bbo={s: bbo(s, "1") for s in slugs})
    out = cont.shutdown_settle_pass(t=t)
    assert called == []
    assert out["settled_now"] == []
    assert {r["status"] for r in out["still_pending"]} == {"PENDING_SETTLEMENT"}
    assert not any(c.endswith("/bbo") for c in t.calls)


def test_activity_path_settlement_is_not_touched(state, monkeypatch):
    _shutdown_env(monkeypatch)
    _september(state, 2)
    calls = []
    monkeypatch.setattr(cont, "settle_held", lambda t, s, **k: {
        "status": "REALIZED", "settled": True, "realized_confirmed": True,
        "realized_usd": "1.00"})
    monkeypatch.setattr(cont, "public_px_fallback",
                        lambda *a: calls.append(a) or {})
    out = cont.shutdown_settle_pass(t=FakeT())
    assert calls == [] and len(out["settled_now"]) == 2
    assert all(r["realized_usd"] == "1.00" for r in out["settled_now"])


def test_other_audit_errors_never_use_the_fallback(state, monkeypatch):
    _shutdown_env(monkeypatch)
    _september(state, 2)
    calls = []
    monkeypatch.setattr(cont, "settle_held", lambda t, s, **k: {
        "status": "AUDIT_UNAVAILABLE",
        "error": "RuntimeError:activity_walk_truncated:HTTP 429"})
    monkeypatch.setattr(cont, "public_px_fallback",
                        lambda *a: calls.append(a) or {})
    cont.shutdown_settle_pass(t=FakeT())
    assert calls == []


# ------------------------------------------------------- accounting invariants

def test_derived_row_closes_the_market_and_enters_realized_today(state):
    both(state, "m")
    assert cont.unsettled_slugs() == ["m"]
    before = cont.base.q1.realized_today()
    ppx.settle_by_public_px(FakeT(bbo={"m": bbo("m", "1")}), "m")
    assert cont.unsettled_slugs() == []
    assert cont.base.q1.realized_today() - before == D("2.70")


def test_derived_is_excluded_from_venue_confirmed_sums():
    rep = {"settlements": [{"status": ppx.PROVENANCE, "settled": True,
                            "realized_usd": None,
                            "realized_derived_usd": "5.00"}]}
    assert cont._sum_realized(rep) in (None, "0", "0.00")


def test_bare_settled_row_still_does_not_close(state):
    """The pre-existing invariant is untouched."""
    both(state, "m")
    _rows(state, [{"kind": "settled", "slug": "m"}])
    assert cont.unsettled_slugs() == ["m"]


# ------------------------------------------------- nothing about trading moved

def test_the_evidence_path_has_no_order_verb():
    src = inspect.getsource(ppx) + inspect.getsource(cont.public_px_fallback)
    for verb in ("place_order", "submit_order", "submit(", "post_order",
                 "cancel_order", "create_order", '"POST"', '"DELETE"', "/v1/orders\"",
                 "Transport(", "signed_headers", "PMUS_API_SECRET_KEY",
                 "SECRET_ENV", "KEY_ENV"):
        assert verb not in src, verb


def test_shutdown_settle_pass_still_has_no_order_verb():
    src = inspect.getsource(cont.shutdown_settle_pass)
    for verb in ("place_order", "submit", "post_order", "cancel_order",
                 "create_order"):
        assert verb not in src, verb


def test_only_the_shutdown_pass_uses_the_fallback():
    """The campaign loop and the continuation preflight keep the original,
    venue-confirmed-only path byte-for-byte."""
    src = inspect.getsource(cont)
    assert src.count("public_px_fallback(t, slug, rep)") == 1
    assert "public_px_fallback(t, slug, rep)" in \
        inspect.getsource(cont.shutdown_settle_pass)
    assert "public_px_fallback" not in inspect.getsource(cont.continuation_preflight)
    assert "public_px_fallback" not in inspect.getsource(cont.settle_held)


def test_stops_rungs_and_gates_unchanged():
    from src import pmus_spring_l5_session as sp
    assert "def combined_budget_tripped(" in inspect.getsource(sp)
    assert inspect.getsource(sp.combined_loss).count("unsettled_floor_total") == 1
    src = inspect.getsource(cont.boot_assertions)
    assert '_check("combined_loss_above_stop", lambda: _combined_now()[1], True)' in src
    assert "public_px" not in src
    assert "public_px" not in inspect.getsource(cont.shutdown_idle)
    assert "public_px" not in inspect.getsource(cont._combined_now)


def test_image_ships_the_module():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    docker = (root / "deploy/pmus_spring_l5/Dockerfile").read_text()
    assert "COPY src/pmus_settlement_public_px.py src/pmus_settlement_public_px.py" in docker
