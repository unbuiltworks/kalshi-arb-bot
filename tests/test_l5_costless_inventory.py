"""Quantity that arrived without a cost basis, and the four defects behind it.

LIVE, 2026-09-26, third continuous arm. Three windows traded. Two of them
settled against inventory that had cost real money and showed none.

  2045z  up 0 / dn 2.2500, up_cost 0 / dn_cost 0, settlement_pnl 0E-8,
         with yes_value 1.0000 so those DOWN shares were worthless.
  2100z  up 5.67 at 5.1627, dn 18.89 at 2.3244; matched 5.67, residual
         13.22 DOWN, settlement_pnl -1.81710. A coherent book, settled
         correctly, and the control case for everything here.
  2115z  our book held 3.25 under `...2115z|ORDER_SIDE_SELL` while the
         venue reported 2.5800 under `...2115z|DOWN`; the market settled
         reading all zeros and then re-emitted L5_LIVE_SETTLED every ~3
         seconds, 40+ times.

The account says what it cost. Flat before ($61.69765 at programme start,
$64.33535 before this arm) and flat after ($61.42595 at 21:35:58Z, zero
positions, zero open orders, zero unsettled): the books claimed +0.82060
against an actual -0.27170, overstated by exactly $1.09230 -- the cost of
5.50 DOWN shares that settled worthless off the ledger.

Four separate defects produced that, and each has its own section below.
"""
import collections
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_pmus_transport as T

M2045 = "cpc-btc-updown-15m-2026-09-26-2045z"
M2100 = "cpc-btc-updown-15m-2026-09-26-2100z"
M2115 = "cpc-btc-updown-15m-2026-09-26-2115z"


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LX.InventoryStore(os.path.join(d, "inv.json"))


def venue_position(market, *, bought, sold):
    """A venue position row in the shape position_row() demands."""
    return {"marketSlug": market, "qtyBoughtDecimal": str(bought),
            "qtySoldDecimal": str(sold),
            "netPositionDecimal": str(D(str(bought)) - D(str(sold)))}


# =====================================================================
# P0.1  the adopted side word          (2115z)
# =====================================================================

def test_the_venue_words_this_engine_has_actually_seen_all_map():
    """From authoritative /v1/order rows, which state the side three ways.

    ORDER_SIDE_BUY + OUTCOME_SIDE_YES + ORDER_INTENT_BUY_LONG is one fact;
    ORDER_SIDE_SELL + OUTCOME_SIDE_NO + ORDER_INTENT_BUY_SHORT is the other.
    The venue's `side` is about the YES token -- it expresses "buy NO" as
    "sell YES" -- which is why SELL is DOWN and not some third thing.
    """
    for word in ("ORDER_SIDE_BUY", "OUTCOME_SIDE_YES",
                 "ORDER_INTENT_BUY_LONG", "UP", "YES", "BUY", "LONG"):
        assert LX.canonical_side(word, doing="t") == LX.UP, word
    for word in ("ORDER_SIDE_SELL", "OUTCOME_SIDE_NO",
                 "ORDER_INTENT_BUY_SHORT", "DOWN", "NO", "SELL", "SHORT"):
        assert LX.canonical_side(word, doing="t") == LX.DOWN, word


def test_an_unmapped_side_word_never_reaches_the_ledger():
    """Fail closed. The alternative is 2115z: a word nothing recognised,
    carried into a ledger key that nothing else reads."""
    for bad in ("ORDER_SIDE_UNSPECIFIED", "", None, "sideways", "U"):
        with pytest.raises(LX.UnknownSide):
            LX.canonical_side(bad, doing="t")


def test_order_side_buy_can_no_longer_be_priced_as_a_short():
    """THE LATENT HALF of the 2115z defect, which never fired but would have.

    own_outcome_price used to ask `side.upper().startswith("U")`. That is
    False for ORDER_SIDE_SELL, which is why a sold leg happened to be
    priced right -- and it is equally False for ORDER_SIDE_BUY, so an
    adopted BUY would have been booked at `1 - p`: a 0.91 UP share
    recorded as costing 0.09, an order of magnitude under, straight into
    the leg cost that the $5 worst-case gate reads.
    """
    assert LX.own_outcome_price("ORDER_SIDE_BUY", D("0.91"),
                                basis=LX.WIRE) == D("0.91")
    assert LX.own_outcome_price("ORDER_SIDE_SELL", D("0.91"),
                                basis=LX.WIRE) == D("0.09")


def test_a_row_that_states_its_side_twice_must_agree():
    """A row saying OUTCOME_SIDE_YES and ORDER_SIDE_SELL at once is not a
    row this system understands. Picking whichever key we read first is the
    guess that 2115z was built on."""
    ok = {"side": "ORDER_SIDE_SELL", "outcomeSide": "OUTCOME_SIDE_NO",
          "intent": "ORDER_INTENT_BUY_SHORT"}
    assert LX.venue_row_side(ok, doing="t") == LX.DOWN
    with pytest.raises(LX.UnknownSide):
        LX.venue_row_side({"side": "ORDER_SIDE_SELL",
                           "outcomeSide": "OUTCOME_SIDE_YES"}, doing="t")
    with pytest.raises(LX.UnknownSide):
        LX.venue_row_side({"quantity": "1"}, doing="t")


def test_2115z_adoption_files_the_fill_under_down_not_the_venue_word(store):
    """THE DEFECT, END TO END, on the window that produced it.

    Four adopted orders, all ORDER_SIDE_SELL, filling 0.91 at 0.79, 0.53 at
    0.80, 1.72 at 0.80 and 0.09 at 0.79 -- 3.25 shares costing 0.6600 in
    own terms. Previously every one of those landed under
    `...|ORDER_SIDE_SELL`, which leg_cost(), risk_exposure_usd() and
    retire_settled_market() do not read.
    """
    fills = [("CR1HP84JJYBY", "0.91", "0.79"), ("CR1HQT5MMYCA", "0.53", "0.80"),
             ("CR1HP84X0YBY", "1.72", "0.80"), ("CR1HW5B1RYBE", "0.09", "0.79")]
    store.reconcile_from_venue([], [
        {"id": c, "client_order_id": c, "market": M2115,
         "side": "ORDER_SIDE_SELL", "outcomeSide": "OUTCOME_SIDE_NO",
         "price": {"value": px, "currency": "USD"}, "quantity": q,
         "remainingQuantity": q}
        for c, q, px in fills])

    for coid, qty, px in fills:
        o = store.live_orders()[coid]
        assert o["side"] == LX.DOWN                   # canonical in the book
        assert o["venue_side_raw"] == "ORDER_SIDE_SELL"   # raw kept for audit
        store.apply_order_progress(
            coid, {"cum_qty": D(qty), "avg_px": D(px), "terminal": True,
                   "state": "ORDER_STATE_FILLED", "commission": D(0)},
            market=M2115, side=o["side"])

    assert store.position(M2115, LX.DOWN) == D("3.25")
    assert store.leg_cost(M2115)[LX.DOWN] == D("0.6600")
    # and nothing is hiding under a key no reader visits
    assert not [k for k in store.state["positions"]
                if k.startswith(M2115) and not k.endswith(("|UP", "|DOWN"))]

    # UP won, so the DOWN shares are worthless: -0.6600, the real number.
    out = store.retire_settled_market(M2115, yes_value=D("1.0000"))
    assert out["retired"] is True
    assert D(out["dn_qty"]) == D("3.25")
    assert D(out["dn_cost"]) == D("0.6600")
    assert D(out["settlement_pnl"]) == D("-0.6600")


# =====================================================================
# P0.2  quantity with no cost          (2045z)
# =====================================================================

def test_2045z_quantity_from_the_venue_net_has_no_cost_and_fails_closed(store):
    """2.25 DOWN shares arrive from the venue's net after an order was
    released unbooked. per_share is 0/2.25 = 0, which is inside [0,1], so
    every existing check passed and the market settled at zero."""
    store.state["positions"]["%s|DOWN" % M2045] = "2.2500"
    store.save()

    bad = [v["violation"] for v in store.book_violations(M2045)]
    assert "QUANTITY_WITHOUT_COST" in bad

    # it must block RISK...
    with pytest.raises(LX.IncoherentBook):
        store.risk_exposure_usd(M2045)
    # ...and SETTLEMENT, which is the one that actually posted the zero.
    with pytest.raises(LX.IncoherentBook):
        store.retire_settled_market(M2045, yes_value=D("1.0000"))
    # ...and submissions, via the uncertainty the check sets.
    assert store.orders_uncertain() is not None


def test_a_fill_that_really_was_free_is_not_a_violation(store):
    """The distinction the check turns on. An UP fill at a wire price of
    0.00 costs nothing and is a real thing that can happen; quantity with
    no fill behind it at all is a cost basis we LOST. `fill_count` is what
    tells them apart, so this is not 'zero cost is always wrong'."""
    store.apply_fill(M2045, LX.UP, D("3"), D("0.00"), basis=LX.WIRE)
    assert store.position(M2045, LX.UP) == D("3")
    assert store.leg_cost(M2045)[LX.UP] == D("0")
    assert store.cost_attested(M2045, LX.UP) is True
    assert store.book_violations(M2045) == []
    store.require_coherent_book(M2045, doing="test")


def test_the_lost_cost_basis_can_be_reconstructed_from_named_evidence(store):
    """The way OUT of the refusal: quantity and cost written together, from
    a stated source. The 1815z defect was writing them separately."""
    store.state["positions"]["%s|DOWN" % M2045] = "2.2500"
    store.save()
    assert store.book_violations(M2045)

    store.reconstruct_cost_basis(
        M2045, "ORDER_SIDE_SELL", qty=D("2.25"), cost=D("0.43230"),
        source="venue /v1/order rows for CR157F2WJYC3")
    assert store.book_violations(M2045) == []
    assert store.position(M2045, LX.DOWN) == D("2.25")
    assert store.leg_cost(M2045)[LX.DOWN] == D("0.43230")
    assert store.state["cost_reconstructions"][-1]["source"].startswith("venue")

    with pytest.raises(ValueError):
        store.reconstruct_cost_basis(M2045, "DOWN", qty=D("1"), cost=D("1"),
                                     source="")
    with pytest.raises(ValueError):          # more than $1 a share
        store.reconstruct_cost_basis(M2045, "DOWN", qty=D("1"), cost=D("2"),
                                     source="x")


def test_cost_without_quantity_still_fails_too(store):
    """The 1815z mirror, unchanged, so the new rule did not displace it."""
    store.state["leg_cost"] = {"%s|DOWN" % M2045: "4.2494"}
    store.save()
    assert "COST_WITHOUT_QUANTITY" in [v["violation"]
                                       for v in store.book_violations(M2045)]


# =====================================================================
# P0.3  the order that vanished, and the 429 that made it vanish
# =====================================================================

def test_an_order_absent_from_the_open_list_is_not_released_unbooked(store):
    """2045z: CR157F2WJYC3 filled, left /v1/orders/open, its /v1/order read
    raised, and reconcile released it. The shares came back from the venue's
    net with no cost. It is now parked, not dropped."""
    store.record_submit("CR157F2WJYC3",
                        {"market": M2045, "side": "DOWN",
                         "price": D("0.20"), "qty": D("2.25")})
    store.attach_venue_id("CR157F2WJYC3", "CR157F2WJYC3")

    out = store.reconcile_from_venue([], [])
    assert out["live_orders_pending_terminal"] == ["CR157F2WJYC3"]
    assert store.live_order_count() == 0            # off the envelope
    assert "CR157F2WJYC3" in store.pending_terminal()
    assert out["uncertainty_cleared"] is False
    assert store.orders_uncertain() is not None

    # and the market cannot settle while its last fill is unbooked
    refused = store.retire_settled_market(M2045, yes_value=D("1.0000"))
    assert refused == {"retired": False, "market": M2045,
                       "reason": "UNBOOKED_TERMINAL_ORDERS",
                       "pending_terminal": ["CR157F2WJYC3"]}

    # the venue's own row resolves it, and the cost lands where it belongs
    res = store.resolve_pending_terminal("CR157F2WJYC3", {
        "cum_qty": D("2.25"), "avg_px": D("0.8079"), "terminal": True,
        "state": "ORDER_STATE_FILLED", "commission": D(0)})
    assert res["resolved"] is True
    assert store.pending_terminal() == {}
    assert store.position(M2045, LX.DOWN) == D("2.25")
    assert store.leg_cost(M2045)[LX.DOWN] == D("2.25") * D("0.1921")
    assert store.book_violations(M2045) == []


def test_resolving_the_same_order_twice_books_it_once(store):
    store.record_submit("c1", {"market": M2045, "side": "DOWN",
                               "price": D("0.2"), "qty": D("2.25")})
    store.attach_venue_id("c1", "V1")
    store.reconcile_from_venue([], [])
    prog = {"cum_qty": D("2.25"), "avg_px": D("0.80"), "terminal": True,
            "state": "ORDER_STATE_FILLED", "commission": D(0)}
    store.resolve_pending_terminal("c1", prog)
    again = store.resolve_pending_terminal("c1", prog)
    assert again["reason"] == "NOT_PENDING"
    assert store.position(M2045, LX.DOWN) == D("2.25")


def test_an_unacked_order_is_released_because_nothing_can_ask_about_it(store):
    """The 1700z phantoms. No venue id means /v1/order has nothing to take,
    so parking them would wedge the session on an unanswerable question.
    They are released -- and if one of them HAD filled, the shares arrive
    with no cost basis and QUANTITY_WITHOUT_COST catches it."""
    for i in range(8):
        store.record_submit("phantom%d" % i,
                            {"market": M2045, "side": "UP",
                             "price": D("0.5"), "qty": D("1")})
    out = store.reconcile_from_venue([], [])
    assert sorted(out["live_orders_released_unacked"]) == [
        "phantom%d" % i for i in range(8)]
    assert out["live_orders_pending_terminal"] == []
    assert store.live_order_count() == 0
    assert store.pending_terminal() == {}
    assert store.orders_uncertain() is None


# ---------------------------------------------------------------- the 429

class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept += s
        self.t += s


def limiter(**kw):
    c = Clock()
    kw.setdefault("rps", 2.0)
    kw.setdefault("burst", 2.0)
    return T.RateLimiter(now_fn=c.now, sleep_fn=c.sleep, **kw), c


def test_the_token_bucket_paces_the_steady_state():
    """The tick was 3s and polled every live order on every tick: with a
    full envelope that is 2.7 GETs a second from one path alone."""
    lim, c = limiter(rps=2.0, burst=2.0)
    for _ in range(2):
        lim.before(write=False)               # burst, no wait
    assert c.slept == 0.0
    lim.before(write=False)                   # third in the same instant
    assert c.slept == pytest.approx(0.5)      # 1 token at 2/s
    assert lim.metrics()["throttled_waits"] == 1


def test_a_429_stops_everything_for_a_shared_cooloff_that_doubles():
    """Shared, because a per-caller backoff is no backoff when six callers
    take turns; doubling, because a fixed one never lets the venue recover."""
    lim, c = limiter()
    first = lim.on_429()
    with pytest.raises(T.VenueThrottled):
        lim.before(write=False)
    with pytest.raises(T.VenueThrottled):
        lim.before(write=True)                # writes are NOT exempt
    c.t += first + 0.01
    lim.before(write=False)                   # cool-off elapsed
    second = lim.on_429()
    third = lim.on_429()
    assert third > second > first
    assert lim.metrics()["http_429"] == 3


def test_the_backoff_is_capped_so_it_cannot_run_away():
    lim, c = limiter(backoff_s=2.0, backoff_max_s=60.0)
    for _ in range(20):
        span = lim.on_429()
    assert span <= 60.0 * 1.25


def test_recovery_time_is_measured_so_we_can_see_it_working():
    lim, c = limiter()
    lim.on_429()
    c.t += 30.0
    lim.on_success()
    m = lim.metrics()
    assert m["time_to_authoritative_recovery_s"] == 30.0
    assert m["consecutive_429"] == 0 and m["in_cooloff"] is False
    assert m["recoveries"] == 1


def test_a_429_is_recognised_however_the_stdlib_wrapped_it():
    import urllib.error
    assert T.is_rate_limited(urllib.error.HTTPError(
        "u", 429, "Too Many Requests", {}, None)) is True
    assert T.is_rate_limited(RuntimeError(
        "HTTP Error 429: Too Many Requests")) is True
    assert T.is_rate_limited(RuntimeError("HTTP Error 500")) is False


def test_the_cooloff_raises_without_sending_anything():
    """The point of raising rather than sleeping: every caller in this
    system already fails closed on an exception from the venue, so none of
    them has to learn what a rate limit is."""
    sent = []

    class Http:
        def request(self, m, p, b, base):
            sent.append(p)
            return {}

    tr = T.PmusTransport(market_slug=M2115, http=Http())
    tr.limiter = T.RateLimiter(rps=99, burst=99)
    tr.limiter.on_429()
    with pytest.raises(T.VenueThrottled):
        tr._get("/v1/orders/open")
    assert sent == []


def test_a_429_on_the_wire_extends_the_cooloff_and_propagates():
    import urllib.error

    class Http:
        def request(self, m, p, b, base):
            raise urllib.error.HTTPError("u", 429, "Too Many Requests",
                                         {}, None)

    tr = T.PmusTransport(market_slug=M2115, http=Http())
    tr.limiter = T.RateLimiter(rps=99, burst=99)
    with pytest.raises(urllib.error.HTTPError):
        tr._get("/v1/orders/open")
    assert tr.limiter.metrics()["http_429"] == 1
    assert tr.limiter.metrics()["in_cooloff"] is True


# =====================================================================
# P0.4  settling the same market forever
# =====================================================================

def test_a_retired_market_is_never_retired_again(store):
    """2115z re-emitted L5_LIVE_SETTLED every ~3 seconds, 40+ times. Each
    pass would have posted another P&L into realized and moved the loss
    halt with it; only the all-zero book kept it harmless."""
    store.apply_fill(M2100, LX.UP, D("2"), D("0.50"), basis=LX.WIRE)
    first = store.retire_settled_market(M2100, yes_value=D("1"))
    assert first["retired"] is True
    assert D(first["settlement_pnl"]) == D("1.00")
    before = store.realized_today()

    again = store.retire_settled_market(M2100, yes_value=D("1"))
    assert again["retired"] is False
    assert again["reason"] == "ALREADY_RETIRED"
    assert store.realized_today() == before
    assert M2100 not in store.markets_with_exposure()


def test_retirement_survives_a_restart(store):
    """Persisted, not in-process: a crash loop would otherwise re-settle
    every market in the book on every boot."""
    store.apply_fill(M2100, LX.UP, D("2"), D("0.50"), basis=LX.WIRE)
    store.retire_settled_market(M2100, yes_value=D("1"))
    reopened = LX.InventoryStore(store.path)
    assert reopened.retire_settled_market(
        M2100, yes_value=D("1"))["reason"] == "ALREADY_RETIRED"
    assert M2100 not in reopened.markets_with_exposure()


def test_retirement_clears_every_key_of_the_market_not_just_up_and_down(store):
    """THE MECHANISM of the 2115z loop. Retirement popped `|UP` and `|DOWN`;
    the market's real inventory was under `|ORDER_SIDE_SELL`, so
    markets_with_exposure kept naming it and the sweep kept settling it."""
    store.apply_fill(M2115, LX.UP, D("1"), D("0.50"), basis=LX.WIRE)
    store.state["positions"]["%s|ORDER_SIDE_SELL" % M2115] = "3.25"
    store.state["leg_cost"]["%s|ORDER_SIDE_SELL" % M2115] = "0.66"
    store.save()
    store.retire_settled_market(M2115, yes_value=D("1"))
    assert not [k for k in store.state["positions"] if k.startswith(M2115)]
    assert not [k for k in store.state["leg_cost"] if k.startswith(M2115)]
    assert M2115 not in store.markets_with_exposure()


# =====================================================================
# 2100z: the control. A window that was always right must stay right.
# =====================================================================

def test_2100z_still_settles_to_exactly_what_it_settled_to(store):
    """up 5.67 at 5.1627 and dn 18.89 at 2.3244, settled at yes_value
    1.0000: matched 5.67 at 1.0336 a pair, residual 13.22 DOWN worthless,
    settlement_pnl -1.81710. None of the four fixes may move this number."""
    store.apply_fill(M2100, "ORDER_SIDE_BUY", D("5.67"),
                     D("5.1627") / D("5.67"), basis=LX.OWN)
    store.apply_fill(M2100, "ORDER_SIDE_SELL", D("18.89"),
                     D(1) - D("2.3244") / D("18.89"), basis=LX.WIRE)
    assert store.leg_cost(M2100)[LX.UP] == D("5.1627")
    assert store.leg_cost(M2100)[LX.DOWN] == D("2.3244")

    out = store.retire_settled_market(M2100, yes_value=D("1.0000"))
    assert D(out["up_qty"]) == D("5.67") and D(out["dn_qty"]) == D("18.89")
    assert D(out["matched_qty"]) == D("5.67")
    assert D(out["residual_qty"]) == D("13.22")
    assert out["residual_side"] == "DOWN"
    assert D(out["settlement_pnl"]) == D("-1.81710")
    # matched and residual stay APART and are not summed
    assert D(out["matched_pnl"]) + D(out["residual_pnl"]) == D("-1.81710")
    assert D(out["matched_cost"]) / D("5.67") > D(1)      # bought above par


def test_2100z_cash_deployed_is_reported_beside_an_unchanged_gate(store):
    """$7.4871 of cash went out under a worst case of $1.8171. The gate is
    worst-case loss and is NOT redefined here; the outlay is reported so
    the difference is visible rather than implicit."""
    store.apply_fill(M2100, "UP", D("5.67"), D("5.1627") / D("5.67"),
                     basis=LX.OWN)
    store.apply_fill(M2100, "DOWN", D("18.89"), D("2.3244") / D("18.89"),
                     basis=LX.OWN)
    cash = store.cash_deployed_usd(M2100)
    assert D(cash["cash_deployed_usd"]) == D("7.48710")
    assert D(cash["leg_cost_total"]) == D("7.48710")
    assert store.risk_exposure_usd(M2100) == D("1.81710")
    # the gate itself is untouched
    assert LX.SafetyLimits(**R.PROBE_LIMITS).max_usd_per_market == D("5")


# =====================================================================
# the accounting repair
# =====================================================================

def test_the_contaminated_total_is_rebased_to_the_authoritative_cash(store):
    """+0.82060 was overstated by exactly the cost of the shares that
    settled off the ledger. The repair is decomposed, never 'set realized
    to the balance', and the part we cannot attribute stays named."""
    store.state["realized_attributed"] = "3.01460"
    store.state["realized_unattributed"] = "-2.19400"
    store.save()
    assert store.realized_today() == D("0.82060")

    out = R.rebase_contaminated_accounting(store)
    assert out is not None
    assert store.realized_today() == D("-0.27170")     # = 61.42595 - 61.69765
    lines = store.realized_lines()
    assert D(lines["realized_attributed"]) == D("2.35460")
    assert D(lines["realized_unattributed"]) == D("-2.62630")
    # the unattributed line moved by the 2045z cost and NOTHING else
    assert D(lines["realized_unattributed"]) - D("-2.19400") == D("-0.43230")
    # and it is not relabelled
    assert "NOT a trading result" in lines["labels"]
    assert R.rebase_contaminated_accounting(store) is None   # once only


def test_the_2115z_line_is_the_sum_of_its_venue_rows():
    """0.6600 is not a plug: it is four fills at prices the venue stated."""
    rows = [("0.91", "0.79"), ("0.53", "0.80"),
            ("1.72", "0.80"), ("0.09", "0.79")]
    qty = sum(D(q) for q, _ in rows)
    cost = sum((D(1) - D(p)) * D(q) for q, p in rows)
    assert qty == D("3.25") and cost == D("0.6600")
    spec = [s for s in R.ACCOUNTING_REBASES
            if s["id"] == "costless-venue-net-2045z-2115z"][0]
    assert spec["attributed"] == D("4.8317") - D("1.81710") - cost
    assert spec["cash_delta"] == D("61.42595") - D("61.69765")


def test_no_rebase_claims_a_rebate_or_a_fee_it_did_not_observe():
    spec = [s for s in R.ACCOUNTING_REBASES
            if s["id"] == "costless-venue-net-2045z-2115z"][0]
    assert "not a rebate" in spec["anchor"]["rebates_confirmed"]
    assert spec["anchor"]["commissions_observed"] == "0.0000 on every 2115z row"


# =====================================================================
# the book the defects left behind, on the live volume
# =====================================================================

def test_a_raw_side_key_is_merged_not_dropped(store):
    """MIGRATION. The live volume holds `...2115z|ORDER_SIDE_SELL` with 3.25
    shares and 0.66 of cost. Under the canonical rules that key is now
    uncanonical: counted apart in the gross, skipped in the net, and
    therefore a permanent gross/net disagreement -- a book that is uncertain
    forever and never submits again.

    Merging is right and dropping is not: the quantity and the cost are
    real, and they move together into the key apply_fill should have used.
    """
    store.state["positions"] = {"%s|ORDER_SIDE_SELL" % M2115: "3.25",
                                "%s|DOWN" % M2115: "1.00"}
    store.state["leg_cost"] = {"%s|ORDER_SIDE_SELL" % M2115: "0.66",
                               "%s|DOWN" % M2115: "0.20"}
    store.state["fill_count"] = {"%s|ORDER_SIDE_SELL" % M2115: 4}
    store.save()

    out = store.canonicalize_position_keys()
    assert out["unmapped"] == []
    assert store.position(M2115, LX.DOWN) == D("4.25")
    assert store.leg_cost(M2115)[LX.DOWN] == D("0.86")
    assert store.fill_count(M2115, LX.DOWN) == 4
    assert not [k for k in store.state["positions"]
                if k.endswith("ORDER_SIDE_SELL")]
    assert "ORDER_SIDE_SELL" in store.state["venue_side_raw"][
        "%s|DOWN" % M2115]
    assert store.book_violations(M2115) == []
    # idempotent
    assert store.canonicalize_position_keys()["moved"] == []


def test_a_side_word_that_maps_to_nothing_is_named_not_guessed(store):
    store.state["positions"] = {"%s|SIDEWAYS" % M2115: "1"}
    store.save()
    out = store.canonicalize_position_keys()
    assert out["moved"] == []
    assert out["unmapped"] == ["%s|SIDEWAYS" % M2115]
    assert store.state["positions"] == {"%s|SIDEWAYS" % M2115: "1"}


def test_an_authoritative_flat_read_closes_the_book_without_posting_pnl(store):
    """The 21:35:58Z read: zero positions, zero open orders, zero unsettled,
    balance 61.42595. Every window in the book is expired and every one of
    them is already in the attributed/unattributed lines, so closing them
    must post NOTHING -- settling them a second time is the double count
    this exists to prevent."""
    store.apply_fill(M2115, LX.DOWN, D("3.25"), D("0.80"), basis=LX.WIRE)
    store.record_submit("stale", {"market": M2045, "side": "UP",
                                  "price": D("0.5"), "qty": D("1")})
    before = store.realized_today()

    out = store.adopt_flat_account(
        anchor={"utc": "2026-09-26T21:35:58Z", "balance": "61.42595",
                "open_orders": 0, "positions": {}, "unsettled_funds": 0},
        reason="test")
    assert sorted(out["closed"]) == sorted([M2045, M2115])
    assert store.realized_today() == before           # NOTHING posted
    assert store.markets_with_exposure() == []
    assert store.live_orders() == {}
    assert store.pending_terminal() == {}
    assert store.orders_uncertain() is None
    assert store.state["flat_adoptions"][-1]["anchor"]["balance"] == "61.42595"
    # and they cannot come back
    assert store.retire_settled_market(
        M2115, yes_value=D("1"))["reason"] == "ALREADY_RETIRED"


def test_the_live_volume_migrates_end_to_end_in_one_pass():
    """The whole repair, on the shape the deployed state file is actually
    in: contaminated total, a raw-side key, and a stale live order."""
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["realized_attributed"] = "3.01460"
        s.state["realized_unattributed"] = "-2.19400"
        s.state["positions"] = {"%s|ORDER_SIDE_SELL" % M2115: "3.25"}
        s.state["leg_cost"] = {"%s|ORDER_SIDE_SELL" % M2115: "0.66"}
        s.state["committed"] = {M2115: "0.66"}
        s.save()
        assert s.realized_today() == D("0.82060")

        assert R.rebase_contaminated_accounting(s) is not None

        assert s.realized_today() == D("-0.27170")
        assert s.markets_with_exposure() == []
        assert s.orders_uncertain() is None
        assert s.book_violations(M2115) == []
        # a fresh process reads the same repaired file and repeats nothing
        again = LX.InventoryStore(os.path.join(d, "inv.json"))
        assert R.rebase_contaminated_accounting(again) is None
        assert again.realized_today() == D("-0.27170")
        assert len(again.state["flat_adoptions"]) == 1


def test_an_order_that_comes_back_stops_being_pending(store):
    """The venue can drop an order from its open list and list it again on
    the next read. A parked entry left behind for an order that is live
    again would refuse its market's settlement for ever -- and double
    booking is not the risk, because apply_order_progress is idempotent on
    the cumulative."""
    row = {"id": "V1", "client_order_id": "V1", "market": M2045,
           "side": "ORDER_SIDE_SELL", "outcomeSide": "OUTCOME_SIDE_NO",
           "price": {"value": "0.80", "currency": "USD"},
           "quantity": "2", "remainingQuantity": "2"}
    store.reconcile_from_venue([], [row])
    store.reconcile_from_venue([], [])                 # vanished
    assert "V1" in store.pending_terminal()

    out = store.reconcile_from_venue([], [row])        # and back again
    assert out["pending_terminal_returned"] == ["V1"]
    assert store.pending_terminal() == {}
    assert "V1" in store.live_orders()
    assert store.orders_uncertain() is None
