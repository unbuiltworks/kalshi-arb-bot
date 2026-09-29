"""The R4 sequence, end to end, through the REAL composition.

R4 fixture, from `analysis/l5/rung_a/R4_TRADE_LEDGER_AND_SETTLEMENT.md`:

    intended first leg   BUY 10 UP  @ 0.26   -> FILLED 10 @ 0.26
    intended complement  BUY 10 DOWN @ 0.73  -> REJECTED, 0 filled
    intended pair cost   0.99
    actual outcome       naked 10 UP, because the native lifecycle ingested
                         neither the fill nor the terminal rejection
    settled result       -$2.58

The strategy is NOT changed to make this pass. What changed is that the
native path now resolves every nonterminal order against `/v1/order/{id}`
before the engine is allowed another decision, using the same classifier set
that has traded live (`pmus_q1_executor` via `pmus_order_lifecycle`).

Composition here is real: MANDATE.guard(PmusTransport) and the real
AuthenticatedVenue. Only the HTTP layer is faked.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_pmus_transport as T
from src import pmus_auth as Q1
from src import pmus_l5_native_engine as E
from src import pmus_live_mandate as MANDATE
from src import pmus_native_money_path as NMP
from src import pmus_native_venue as NV
from src import pmus_order_lifecycle as LC

SLUG = "cpc-btc-updown-15m-2026-09-29-0045z"
ORDERS, PREVIEW = "/v1/orders", "/v1/order/preview"

UP_PX, DOWN_PX = D("0.26"), D("0.73")          # own terms; 0.99 pair
CLIP = D(10)


class Venue:
    """The venue as R4 met it. Per-order scripted outcomes, keyed by id."""

    def __init__(self, *, outcomes):
        self.outcomes = list(outcomes)          # consumed per POST, in order
        self.orders = {}
        self.bodies = []
        self.calls = []
        self.n = 0

    # ---- HTTP ----
    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if method == "POST" and path == PREVIEW:
            sent = dict((body or {}).get("request") or {})
            return {"order": dict(sent, cumQuantity="0")}
        if method == "POST" and path == ORDERS:
            self.bodies.append(dict(body or {}))
            self.n += 1
            oid = "R4-%d" % self.n
            plan = self.outcomes.pop(0) if self.outcomes else {"state": "OPEN"}
            wire = (body.get("price") or {}).get("value")
            self.orders[oid] = {
                "state": plan["state"],
                "quantity": body.get("quantity"),
                "cumQuantity": plan.get("cum", "0"),
                "leavesQuantity": plan.get("leaves", "0"),
                "avgPx": {"value": plan.get("avg", wire)},
                "commissionNotionalTotalCollected": {
                    "value": plan.get("commission", "0.0000")},
                "outcomeSide": body.get("outcomeSide"),
                "lastTransactTime": "2026-09-29T00:46:46.376683Z"}
            return {"id": oid, "filled_qty": plan.get("cum", "0")}
        if method == "GET" and path.startswith("/v1/order/"):
            oid = path.rsplit("/", 1)[-1]
            if oid not in self.orders:
                raise RuntimeError("404 no such order")
            return {"order": dict(self.orders[oid])}
        if path == "/v1/orders/open":
            return {"orders": [{"orderId": k} for k, v in self.orders.items()
                               if v["state"] == "OPEN"]}
        if path == "/v1/portfolio/positions":
            return {"positions": self.positions()}
        raise AssertionError(("unexpected", method, path))

    def positions(self):
        """What the venue would report, derived from the scripted fills."""
        net = {"OUTCOME_SIDE_YES": D(0), "OUTCOME_SIDE_NO": D(0)}
        for v in self.orders.values():
            net[v["outcomeSide"]] += D(str(v["cumQuantity"] or 0))
        out = []
        for wire, qty in net.items():
            if qty > 0:
                out.append({"market": SLUG,
                            "side": "UP" if wire.endswith("YES") else "DOWN",
                            "qty": str(qty)})
        return out

    def posts(self):
        return [c for c in self.calls if c == ("POST", ORDERS)]


FILL_10_AT_26 = {"state": "ORDER_STATE_FILLED", "cum": "10", "leaves": "0",
                 "avg": "0.2600", "commission": "-0.0200"}
REJECT = {"state": "ORDER_STATE_REJECTED", "cum": "0", "leaves": "0",
          "avg": "0.0000", "commission": "0.0000"}
#: the complement fills: venue serves the LONG price, so DOWN at 0.73 own
#: terms comes back as avgPx 0.27.
FILL_10_AT_73 = {"state": "ORDER_STATE_FILLED", "cum": "10", "leaves": "0",
                 "avg": "0.2700", "commission": "-0.0200"}


@pytest.fixture()
def armed(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "sess-r4")
    monkeypatch.setenv(Q1.ARM_ENV, "sess-r4")


def mandate(tmp_path, auth_id="OWNER-TEST-R4"):
    m, err = MANDATE.parse_mandate({
        "authorization_id": auth_id,
        "authorized_at": "2026-09-29T00:34:00Z",
        "service": "l5-live-probe-oneshot",
        "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
        "strategy_version": "test", "max_markets": 1, "max_total_markets": 1,
        "max_spend_per_market": "81.00", "max_total_spend": "81.00",
        "max_residual": "20.00", "max_daily_realized_loss": "20.00",
        "expiry": "2099-01-01T00:00:00Z"})
    assert err is None, err
    return m


def stack(tmp_path, venue_http):
    tp = T.PmusTransport(market_slug=SLUG, http=venue_http)
    tp.limiter = T.RateLimiter(rps=1e6, burst=1e6)
    tp.preflight_report = {"preflight": "PASSED"}
    tp.arm()
    guarded = MANDATE.guard(tp, mandate(tmp_path), tmp_path)
    av = NV.AuthenticatedVenue(guarded, market=SLUG)
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=av,
                   journal=None, clip=CLIP)
    return tp, guarded, av, mp


def up_rest():
    return E.Intent(E.REST, side="up", price=UP_PX, qty=CLIP, mode="MAKER")


def down_rest():
    return E.Intent(E.REST, side="down", price=DOWN_PX, qty=CLIP, mode="MAKER")


# ------------------------------------------------- the seam must be present

def test_the_production_venue_exposes_the_lifecycle_seam():
    """`unbooked_fills` asked `tp.fills()`; PmusTransport has no such method,
    so production booked nothing while every double did. This is the
    structural guard against that regression returning."""
    assert hasattr(NV.AuthenticatedVenue, "order_status")
    assert not hasattr(T.PmusTransport, "fills")


def test_the_engine_cannot_decide_before_order_state_is_incorporated():
    """Order of operations inside on_book_state: resolve, THEN decide."""
    import inspect
    src = inspect.getsource(NMP.NativeMoneyPath.on_book_state)
    assert src.index("resolve_open_orders(") < src.index("self.eng.on_book(")
    assert "ORDER_STATE_UNKNOWN" in src


# --------------------------------------------------------- THE R4 SEQUENCE

def test_lifecycle_fill_then_reject_is_handled(armed, tmp_path):
    """LIFECYCLE CORRECTNESS, not the R4 event order.

    R4's real sequence was UP submitted -> DOWN submitted 234ms later -> DOWN
    REJECTED 94ms after that -> UP FILLED 10.7s later, i.e. the rejection
    PRECEDED the fill by 10.373s (analysis/l5/rung_a/R4_CHRONOLOGY.md). This
    fixture drives fill-then-reject, which is a different and also necessary
    ordering; the exact chronology is the next test.
    """
    v = Venue(outcomes=[FILL_10_AT_26, REJECT])
    tp, guarded, av, mp = stack(tmp_path, v)

    # 1. UP submitted
    rec = mp._post(up_rest())
    assert rec["oid"] == "R4-1"
    assert len(v.posts()) == 1

    # 2. authoritative state incorporated: FILLED 10 @ 0.26
    r = mp.resolve_open_orders()
    assert r["unknown"] == []
    assert len(r["fills_booked"]) == 1
    f = r["fills_booked"][0]
    assert (f["side"], f["qty"], f["price_own"]) == ("up", "10", "0.2600")
    assert D(f["cost"]) == D("2.60")
    assert f["commission"] == "-0.0200"

    # 3. INVENTORY: UP = 10 with a $2.60 cost basis. No phantom flat state.
    bk = mp.eng.bk
    assert bk.q["up"] == D(10) and bk.q["down"] == D(0)
    assert bk.c["up"] == D("2.60")
    assert bk.avg("up") == D("0.26")
    assert bk.residual() == D(10)

    # 4. the complement goes out at 0.73 own terms -> 0.27 on the wire
    mp._post(down_rest())
    posted = [c for c in v.calls if c == ("POST", ORDERS)]
    assert len(posted) == 2
    down_body = [b for b in v.bodies
                 if b["outcomeSide"] == "OUTCOME_SIDE_NO"][0]
    assert D(str(down_body["price"]["value"])) == D("0.27")
    assert D(str(down_body["quantity"])) == CLIP

    # 5. REJECTED: terminal immediately, zero filled
    r2 = mp.resolve_open_orders()
    assert r2["fills_booked"] == []
    assert len(r2["rejected"]) == 1
    rej = r2["rejected"][0]
    assert rej["class"] == LC.REJECTED_NO_FILL
    assert rej["state"] == "ORDER_STATE_REJECTED"
    assert rej["filled"] == "0"
    assert mp.rejections and mp.rejections[0]["oid"] == "R4-2"

    # 6. only the UNFILLED reservation released: the DOWN leg's $7.30 is gone,
    #    the UP leg's filled $2.60 is permanent.
    ex = mp.registry.as_dict()
    assert D(ex["filled_commitment"]) == D("2.60")
    assert D(ex["open_reserved"]) == D(0)
    assert D(ex["exposure"]) == D("2.60")
    assert ex["nonterminal"] == 0

    # 7. the residual REMAINS and the engine knows it
    assert mp.eng.bk.residual() == D(10)

    # 8. the side is clear again, so the engine may retry the complement --
    #    R4 could not, because both orders stayed nonterminal.
    assert mp._ensure_side_clear("down")["clear"] is True
    assert mp._ensure_side_clear("up")["clear"] is True

    # 9. our book agrees with the venue
    assert [p for p in v.positions()] == [
        {"market": SLUG, "side": "UP", "qty": "10"}]


def test_the_r4_settled_result_is_reproduced_from_the_booked_basis(armed,
                                                                  tmp_path):
    """-$2.58: settlement 0 for UP, cost 2.60, commission -0.0200."""
    v = Venue(outcomes=[FILL_10_AT_26, REJECT])
    tp, guarded, av, mp = stack(tmp_path, v)
    mp._post(up_rest())
    mp.resolve_open_orders()
    mp._post(down_rest())
    mp.resolve_open_orders()
    bk = mp.eng.bk
    proceeds = D(0) * bk.q["up"]                 # settlement_px 0.0000
    realized = proceeds - bk.c["up"] - D("-0.0200")
    assert realized == D("-2.5800")


# ------------------------------------------------ the 99c pair that COMPLETES

def test_a_successful_ninety_nine_cent_pair(armed, tmp_path):
    v = Venue(outcomes=[FILL_10_AT_26, FILL_10_AT_73])
    tp, guarded, av, mp = stack(tmp_path, v)

    mp._post(up_rest())
    mp.resolve_open_orders()
    mp._post(down_rest())
    r = mp.resolve_open_orders()

    assert len(r["fills_booked"]) == 1
    assert r["fills_booked"][0]["price_own"] == "0.7300"
    assert r["rejected"] == []

    bk = mp.eng.bk
    assert bk.q["up"] == D(10) and bk.q["down"] == D(10)
    matched = min(bk.q["up"], bk.q["down"])
    assert matched == D(10)
    pair_cost = bk.avg("up") + bk.avg("down")
    assert pair_cost == D("0.99")               # before fees
    assert bk.residual() == D(0)

    ex = mp.registry.as_dict()
    assert D(ex["open_reserved"]) == D(0) and ex["nonterminal"] == 0
    assert D(ex["filled_commitment"]) == D("9.90")   # 2.60 + 7.30
    assert v.positions() == [
        {"market": SLUG, "side": "UP", "qty": "10"},
        {"market": SLUG, "side": "DOWN", "qty": "10"}]


# ----------------------------------------------- UNKNOWN halts, never assumes

def test_an_unreadable_order_halts_instead_of_reading_as_unfilled(armed,
                                                                 tmp_path):
    class Blind(Venue):
        def request(self, method, path, body=None, base=None):
            if method == "GET" and path.startswith("/v1/order/"):
                raise RuntimeError("503 upstream")
            return super().request(method, path, body, base)

    v = Blind(outcomes=[FILL_10_AT_26])
    tp, guarded, av, mp = stack(tmp_path, v)
    mp._post(up_rest())
    r = mp.resolve_open_orders()
    assert r["fills_booked"] == []
    assert len(r["unknown"]) == 1
    # charged in FULL, and still nonterminal
    ex = mp.registry.as_dict()
    assert D(ex["open_reserved"]) == D("2.60") and ex["nonterminal"] == 1

    row = {"status": "USABLE", "px": None, "t": 0.0,
           "bid": "0.25", "ask": "0.27", "bid_qty": "100", "ask_qty": "100"}
    out = mp.on_book_state(row)
    assert out["acted"] is False
    assert out["refused"] == "ORDER_STATE_UNKNOWN" and out["halted"] is True


def test_an_already_terminal_order_is_not_a_failed_cancel(armed, tmp_path):
    """R4 recorded CANCEL_FAILED against a FILLED order and a REJECTED order
    and left both nonterminal: $9.90 stayed reserved for thirteen minutes
    after the venue had finished with them."""
    class NoCancel(Venue):
        def request(self, method, path, body=None, base=None):
            if method == "POST" and "/cancel" in path:
                raise RuntimeError("400 order is not cancellable")
            return super().request(method, path, body, base)

    v = NoCancel(outcomes=[FILL_10_AT_26])
    tp, guarded, av, mp = stack(tmp_path, v)
    mp._post(up_rest())
    got = mp.cancel_side("up")
    assert got["state"] == "ALREADY_TERMINAL"
    assert got["classification"] == LC.FILLED
    assert got["filled_qty"] == "10"
    assert mp.registry.as_dict()["nonterminal"] == 0
    assert mp.eng.bk.q["up"] == D(10)


# ----------------------------------------------------------- nothing moved

def test_the_frozen_parameters_are_untouched():
    assert (str(E.THETA), E.VARIANT, E.HOLD_S, str(E.CLIP),
            str(E.RESIDUAL_CAP_K), str(E.PROFIT_LOCK_USD),
            E.FLOOR_GUARD, E.COMPLEMENT_BIAS_C) == \
        ("0.96", "B", 60.0, "50", "2", "5", False, None)


def test_r4_exact_chronology_two_sided_quote_then_late_fill(armed, tmp_path):
    """THE ACTUAL R4 SEQUENCE.

    UP accepted and RESTING -> DOWN submitted -> DOWN terminal REJECTED while
    the book is still FLAT -> UP still live -> later UP FILLED -> authoritative
    polling books UP=10 -> LOCK5 sees residual UP=10 on its next decision.

    Both legs are the frozen policy's two-sided acquisition at our touch, 234ms
    apart; the rejection is not a failed completion of a held residual.
    """
    v = Venue(outcomes=[{"state": "OPEN", "cum": "0"}, REJECT])
    tp, guarded, av, mp = stack(tmp_path, v)

    # 1. UP submitted and ACCEPTED, resting
    up = mp._post(up_rest())
    assert up["oid"] == "R4-1"
    assert v.orders["R4-1"]["state"] == "OPEN"

    # 2. DOWN submitted 234ms later -- while FLAT
    assert mp.eng.bk.q["up"] == D(0) and mp.eng.bk.q["down"] == D(0)
    dn = mp._post(down_rest())
    assert dn["oid"] == "R4-2"

    # 3. first pass: DOWN is terminal REJECTED, UP is still WORKING
    r1 = mp.resolve_open_orders()
    assert [x["oid"] for x in r1["rejected"]] == ["R4-2"]
    assert [x["oid"] for x in r1["working"]] == ["R4-1"]
    assert r1["fills_booked"] == []
    # the rejection released its whole reservation; UP's is still held
    ex = mp.registry.as_dict()
    assert D(ex["filled_commitment"]) == D(0)
    assert D(ex["open_reserved"]) == D("2.60")      # UP only; DOWN's 7.30 gone
    assert ex["nonterminal"] == 1
    # and the book is STILL FLAT: a rejection moves no inventory
    assert mp.eng.bk.residual() == D(0)

    # 4. 10.7s later the UP maker quote fills
    v.orders["R4-1"].update({"state": "ORDER_STATE_FILLED", "cumQuantity": "10",
                             "leavesQuantity": "0",
                             "avgPx": {"value": "0.2600"},
                             "commissionNotionalTotalCollected":
                                 {"value": "-0.0200"}})

    # 5. the next pass incorporates it BEFORE any decision
    r2 = mp.resolve_open_orders()
    assert len(r2["fills_booked"]) == 1
    assert r2["fills_booked"][0]["price_own"] == "0.2600"
    assert [x["oid"] for x in r2["terminated"]] == ["R4-1"]

    # 6. LOCK5 now sees residual UP=10 with a real basis
    bk = mp.eng.bk
    assert bk.q["up"] == D(10) and bk.q["down"] == D(0)
    assert bk.residual() == D(10)
    assert bk.avg("up") == D("0.26")
    ex = mp.registry.as_dict()
    assert D(ex["filled_commitment"]) == D("2.60")
    assert D(ex["open_reserved"]) == D(0) and ex["nonterminal"] == 0

    # 7. and the next decision is taken against CURRENT inventory and the
    #    CURRENT book -- not against the state at submission time.
    row = {"status": "USABLE", "t": 0.0, "bid": "0.25", "ask": "0.27",
           "bid_qty": "100", "ask_qty": "100"}
    out = mp.on_book_state(row)
    assert out.get("refused") != "ORDER_STATE_UNKNOWN"
    # whether the engine ACTS on this synthetic row is the frozen policy's
    # business and is not asserted here. What is asserted is that it was not
    # blocked by unresolved state and that it decided against the CURRENT
    # inventory -- residual 10, booked from the venue, not the flat book it
    # had when it submitted.
    assert mp.eng.bk.residual() == D(10)
    assert out.get("fills_booked") == []      # already booked on the pass above
