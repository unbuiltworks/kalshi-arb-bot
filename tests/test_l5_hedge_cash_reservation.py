"""A hedging DOWN order is reserved at what it can cost, not $1 a share.

OWNER DECISION, 2026-11-10: "correcting hedge cash reservations to use the
order's actual maximum executable limit-price cost, including fees, instead
of automatically reserving $1 per share. This approval is strictly for
risk-reducing hedges against venue-confirmed unmatched inventory. It does
not authorize exceeding the existing $0.90 cash cap ... Implement atomic
cash and exposure reservations across all resting orders and inventory,
including simultaneous-fill worst cases. Never double-count freed
reservations before venue-confirmed cancellation."

WHAT IS PINNED HERE

  * off unless `L5_HEDGE_CASH_AT_LIMIT` is exactly ON, and then identical;
  * only the quantity that offsets venue-confirmed unmatched UP inventory
    in the SAME market is repriced -- at limit cost plus the worst fee;
  * the unmatched quantity is one allowance shared by every DOWN order,
    resting and proposed, so two orders cannot both spend it -- which is
    what keeps a hedge whose cancellation is unconfirmed fully reserved;
  * the reservation is never below what every order filling at once would
    actually take, and the ceiling itself is untouched;
  * exposure was already priced at limit cost over both settlements.

The exposure bound (`LiveStore.risk_exposure_usd`) is not changed by this
commit: it already adds a resting DOWN order at `price * qty` and evaluates
the whole book under each settlement.
"""
import random
from decimal import Decimal as D

import pytest

import tests.test_l5_ladder_runner as LAD
from src import l5_live_execution as E
from src import l5_live_validation_gates as g

M = "cpc-btc-updown-15m-2026-11-10-2130z"
OTHER = "cpc-btc-updown-15m-2026-11-10-2145z"
SHORT = "ORDER_INTENT_BUY_SHORT"
LONG = "ORDER_INTENT_BUY_LONG"
CAP = D("0.90")


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")


@pytest.fixture()
def off(monkeypatch):
    monkeypatch.delenv(g.HEDGE_CASH_ENV, raising=False)


def gates(**kw):
    kw.setdefault("max_cash_deployed_usd", CAP)
    return g.ValidationGates(**kw)


def inv(up, dn, market=M):
    return {"market": market, "up_qty": D(str(up)), "dn_qty": D(str(dn)),
            "up_cost": D(str(up)) * D("0.5"), "dn_cost": D(str(dn)) * D("0.5")}


def row(side, qty, price, market=M, coid="c"):
    r = {"coid": coid, "qty": D(str(qty)), "price": D(str(price)),
         "intent": SHORT if side == "DOWN" else LONG}
    if market is not None:
        r["market"] = market
    return r


def filled(usd, market=M):
    return [{"market": market, "cash_out": D(str(usd)), "settled": False,
             "closing": False}]


def cash(gt, side, qty, price, *, held="0", outstanding=(), inventory=None):
    return gt.refuse(order_qty=D(str(qty)), order_price=D(str(price)),
                     order_intent=SHORT if side == "DOWN" else LONG,
                     filled_legs=filled(held), outstanding=list(outstanding),
                     inventory=inventory)


def reserve(gt, side, qty, price, *, outstanding=(), inventory=None):
    return gt.reserved_cash(
        outstanding=list(outstanding), order_qty=D(str(qty)),
        order_price=D(str(price)),
        order_intent=SHORT if side == "DOWN" else LONG, inventory=inventory)


# ======================================================================
# 1. OFF UNLESS THE OWNER'S SWITCH SAYS ON
# ======================================================================

class TestOffUnlessSet:

    def test_unset_reserves_a_dollar_a_share(self, off):
        r = reserve(gates(), "DOWN", "0.3", "0.44", inventory=inv("0.9", "0.6"))
        assert r["this_order"] == D("0.3") and r["basis"] == "DOLLAR_A_SHARE"

    @pytest.mark.parametrize("value", ["", "on", "1", "true", "YES", "ON "
                                       "X", "OFF"])
    def test_only_the_exact_token_turns_it_on(self, monkeypatch, value):
        monkeypatch.setenv(g.HEDGE_CASH_ENV, value)
        r = reserve(gates(), "DOWN", "0.3", "0.44", inventory=inv("0.9", "0.6"))
        assert r["this_order"] == D("0.3")

    def test_off_and_on_agree_whenever_nothing_is_hedged(self, monkeypatch):
        rnd = random.Random(7)
        for _ in range(200):
            q = D(rnd.randrange(1, 90)) / 100
            p = D(rnd.randrange(2, 98)) / 100
            side = rnd.choice(["UP", "DOWN"])
            rest = [row(rnd.choice(["UP", "DOWN"]),
                        D(rnd.randrange(1, 60)) / 100,
                        D(rnd.randrange(2, 98)) / 100, coid=str(i))
                    for i in range(rnd.randrange(0, 4))]
            n = D(rnd.randrange(0, 90)) / 100
            book = inv(n, n + D(rnd.randrange(0, 30)) / 100)   # never long UP
            monkeypatch.delenv(g.HEDGE_CASH_ENV, raising=False)
            a = reserve(gates(), side, q, p, outstanding=rest, inventory=book)
            monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")
            b = reserve(gates(), side, q, p, outstanding=rest, inventory=book)
            assert (a["resting"], a["this_order"]) == (
                b["resting"], b["this_order"])


# ======================================================================
# 2. A HEDGE IS RESERVED AT ITS LIMIT COST PLUS THE WORST FEE
# ======================================================================

class TestAHedgeCostsWhatItCanCost:

    def test_limit_cost_plus_the_fee_rounded_up(self, on):
        gt = gates()
        r = reserve(gt, "DOWN", "0.3", "0.44", inventory=inv("0.9", "0.6"))
        fee = gt.fee_worst(D("0.3"), D("0.44"))
        # 0.0051 rounded UP to a cent, plus the per-order rounding allowance
        assert fee == D("0.01") + gt.fee_extra_per_order_usd == D("0.02")
        assert r["this_order"] == D("0.3") * D("0.44") + fee == D("0.152")
        assert r["hedged_this"] == D("0.3")
        assert r["basis"] == "HEDGE_AT_LIMIT_PLUS_FEE"

    def test_a_hedge_the_dollar_reading_refused_is_now_allowed(
            self, monkeypatch):
        """Three clips of UP and two of DOWN are held for $0.714; the third
        DOWN clip is the hedge. At $1 a share it was $1.014 of 'cash'."""
        book = inv("0.9", "0.6")
        monkeypatch.delenv(g.HEDGE_CASH_ENV, raising=False)
        assert cash(gates(), "DOWN", "0.3", "0.44", held="0.714",
                    inventory=book) == "CASH_COMMITMENT:1.014>0.90"
        monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")
        assert cash(gates(), "DOWN", "0.3", "0.44", held="0.714",
                    inventory=book) is None

    def test_the_ceiling_still_refuses_a_hedge_that_does_not_fit(self, on):
        assert cash(gates(), "DOWN", "0.3", "0.44", held="0.80",
                    inventory=inv("0.9", "0.6")
                    ) == "CASH_COMMITMENT:0.952>0.90"

    def test_only_the_hedged_quantity_is_repriced(self, on):
        gt = gates()
        r = reserve(gt, "DOWN", "0.5", "0.44", inventory=inv("0.9", "0.6"))
        hedged = D("0.3") * D("0.44") + gt.fee_worst(D("0.3"), D("0.44"))
        assert r["this_order"] == hedged + D("0.2")     # the rest at $1
        assert r["hedged_this"] == D("0.3")

    def test_a_down_order_with_nothing_unmatched_is_not_a_hedge(self, on):
        r = reserve(gates(), "DOWN", "0.3", "0.44", inventory=inv("0.6", "0.6"))
        assert r["this_order"] == D("0.3")
        assert r["basis"].endswith("NOTHING_UNMATCHED_TO_HEDGE")

    def test_adding_to_the_side_already_long_is_not_a_hedge(self, on):
        r = reserve(gates(), "DOWN", "0.3", "0.44", inventory=inv("0.3", "0.6"))
        assert r["this_order"] == D("0.3")

    def test_an_up_order_is_reserved_as_it_always_was(self, on, monkeypatch):
        book = inv("0.3", "0.6")                        # UP is the hedge side
        a = reserve(gates(), "UP", "0.3", "0.55", inventory=book)
        monkeypatch.delenv(g.HEDGE_CASH_ENV)
        b = reserve(gates(), "UP", "0.3", "0.55", inventory=book)
        assert a["this_order"] == b["this_order"] == D("0.165")

    def test_at_a_price_where_the_fee_outweighs_the_discount(self, on):
        """Actual maximum cost, not 'whichever is smaller': at 0.97 the fee
        reservation is more than the three cents a share the limit saves."""
        gt = gates()
        r = reserve(gt, "DOWN", "0.3", "0.97", inventory=inv("0.9", "0.6"))
        assert r["this_order"] == D("0.291") + gt.fee_worst(
            D("0.3"), D("0.97")) > D("0.3")


# ======================================================================
# 3. ONLY VENUE-CONFIRMED INVENTORY IN THE SAME MARKET COUNTS
# ======================================================================

class TestWhatCountsAsAHedge:

    @pytest.mark.parametrize("book", [
        None, {}, "x",
        {"up_qty": D("0.9"), "dn_qty": D("0.6")},                 # no market
        {"market": "", "up_qty": D("0.9"), "dn_qty": D("0.6")},
        {"market": M, "up_qty": None, "dn_qty": D("0.6")},
        {"market": M, "up_qty": D("0.9")},
        {"market": M, "up_qty": D("NaN"), "dn_qty": D("0.6")},
        {"market": M, "up_qty": D("Infinity"), "dn_qty": D("0.6")},
        {"market": M, "up_qty": D("-0.9"), "dn_qty": D("0.6")},
        {"market": M, "up_qty": "abc", "dn_qty": D("0.6")},
    ])
    def test_an_unreadable_ledger_keeps_the_dollar(self, on, book):
        r = reserve(gates(), "DOWN", "0.3", "0.44", inventory=book)
        assert r["this_order"] == D("0.3")
        assert r["hedged_this"] == 0

    def test_a_resting_order_in_another_market_is_not_a_hedge(self, on):
        rest = [row("DOWN", "0.3", "0.44", market=OTHER)]
        r = reserve(gates(), "UP", "0.1", "0.5", outstanding=rest,
                    inventory=inv("0.9", "0.6"))
        assert r["resting"] == D("0.3") and r["hedged_resting"] == 0

    def test_a_resting_order_that_names_no_market_is_not_a_hedge(self, on):
        rest = [row("DOWN", "0.3", "0.44", market=None)]
        r = reserve(gates(), "UP", "0.1", "0.5", outstanding=rest,
                    inventory=inv("0.9", "0.6"))
        assert r["resting"] == D("0.3")

    def test_and_it_does_not_use_up_the_allowance(self, on):
        rest = [row("DOWN", "0.3", "0.44", market=OTHER)]
        r = reserve(gates(), "DOWN", "0.3", "0.44", outstanding=rest,
                    inventory=inv("0.9", "0.6"))
        assert r["hedged_this"] == D("0.3")

    @pytest.mark.parametrize("price", ["1", "1.2", "0", "-0.1", "NaN"])
    def test_an_unreadable_price_keeps_the_dollar(self, on, price):
        rest = [row("DOWN", "0.3", price)]
        r = reserve(gates(), "UP", "0.1", "0.5", outstanding=rest,
                    inventory=inv("0.9", "0.6"))
        assert r["hedged_resting"] == 0
        assert r["resting"] == D("0.3")


# ======================================================================
# 4. ONE ALLOWANCE, SPENT ONCE -- ACROSS RESTING AND PROPOSED ORDERS
# ======================================================================

class TestOneAllowance:

    def test_a_hedge_still_resting_keeps_its_share(self, on):
        """THE REPLACEMENT CASE. The old hedge's cancellation has been sent
        and not confirmed, so it is still in the live orders. A replacement
        beside it finds nothing left to hedge and is reserved at $1."""
        gt = gates()
        old = [row("DOWN", "0.3", "0.50", coid="old")]
        r = reserve(gt, "DOWN", "0.3", "0.44", outstanding=old,
                    inventory=inv("0.9", "0.6"))
        assert r["hedged_resting"] == D("0.3") and r["hedged_this"] == 0
        assert r["this_order"] == D("0.3")
        assert r["resting"] == D("0.15") + gt.fee_worst(D("0.3"), D("0.50"))

    def test_so_the_replacement_is_refused_until_the_venue_confirms(self, on):
        book = inv("0.9", "0.6")
        old = [row("DOWN", "0.3", "0.50", coid="old")]
        assert str(cash(gates(), "DOWN", "0.3", "0.44", held="0.714",
                        outstanding=old, inventory=book)
                   ).startswith("CASH_COMMITMENT:")
        # the venue confirms the cancel: the row leaves the live orders
        assert cash(gates(), "DOWN", "0.3", "0.44", held="0.714",
                    outstanding=[], inventory=book) is None

    def test_the_dearest_order_takes_the_allowance(self, on):
        """Whichever order the imbalance absorbs, the total is the larger
        of the two possibilities -- the allocation that saves least."""
        gt = gates()
        book = inv("0.9", "0.6")
        a = reserve(gt, "DOWN", "0.3", "0.70",
                    outstanding=[row("DOWN", "0.3", "0.40")], inventory=book)
        b = reserve(gt, "DOWN", "0.3", "0.40",
                    outstanding=[row("DOWN", "0.3", "0.70")], inventory=book)
        total_a = a["resting"] + a["this_order"]
        total_b = b["resting"] + b["this_order"]
        dear = D("0.21") + gt.fee_worst(D("0.3"), D("0.70")) + D("0.3")
        cheap = D("0.12") + gt.fee_worst(D("0.3"), D("0.40")) + D("0.3")
        assert total_a == total_b == dear > cheap

    def test_the_allowance_is_split_when_no_order_covers_it(self, on):
        gt = gates()
        rest = [row("DOWN", "0.2", "0.50", coid="a")]
        r = reserve(gt, "DOWN", "0.3", "0.44", outstanding=rest,
                    inventory=inv("0.9", "0.5"))            # 0.4 unmatched
        assert r["hedged_resting"] == D("0.2")
        assert r["hedged_this"] == D("0.2")
        assert r["this_order"] == (D("0.2") * D("0.44")
                                   + gt.fee_worst(D("0.2"), D("0.44"))
                                   + D("0.1"))

    def test_a_fill_of_the_hedge_shrinks_both_together(self, on):
        """A partial fill moves quantity from the order to the inventory:
        the remainder is still exactly the unmatched amount."""
        r = reserve(gates(), "UP", "0.01", "0.5",
                    outstanding=[row("DOWN", "0.2", "0.44")],
                    inventory=inv("0.9", "0.7"))
        assert r["hedged_resting"] == D("0.2")

    def test_the_planned_other_leg_of_a_pair_counts_first(self, on):
        """`probe_precheck` gates each leg with the other counted as if it
        were resting. The UP leg adds no allowance: only inventory does."""
        gt = gates()
        r = reserve(gt, "UP", "0.3", "0.25",
                    outstanding=[row("DOWN", "0.3", "0.70")],
                    inventory=inv("0.6", "0.3"))
        assert r["this_order"] == D("0.075")
        assert r["resting"] == D("0.21") + gt.fee_worst(D("0.3"), D("0.70"))


# ======================================================================
# 5. EVERYTHING FILLING AT ONCE NEVER COSTS MORE THAN WAS RESERVED
# ======================================================================

class TestSimultaneousFills:

    def test_the_reservation_bounds_every_order_filling_together(self, on):
        rnd = random.Random(11)
        gt = gates()
        for _ in range(500):
            up = D(rnd.randrange(0, 120)) / 100
            dn = D(rnd.randrange(0, 120)) / 100
            rest = [row(rnd.choice(["UP", "DOWN"]),
                        D(rnd.randrange(1, 60)) / 100,
                        D(rnd.randrange(2, 99)) / 100,
                        market=rnd.choice([M, M, OTHER]), coid=str(i))
                    for i in range(rnd.randrange(0, 4))]
            side = rnd.choice(["UP", "DOWN"])
            q = D(rnd.randrange(1, 60)) / 100
            p = D(rnd.randrange(2, 99)) / 100
            r = reserve(gt, side, q, p, outstanding=rest, inventory=inv(up, dn))
            real = sum(o["qty"] * o["price"] for o in rest) + q * p
            assert r["resting"] + r["this_order"] >= real, (up, dn, rest)
            # and never below the old figure by more than the hedged quantity
            old = gt.pending_obligation(rest) + (q if side == "DOWN" else q * p)
            assert old - (r["resting"] + r["this_order"]) <= (
                r["hedged_resting"] + r["hedged_this"])
            assert r["hedged_resting"] + r["hedged_this"] <= max(
                D(0), up - dn)

    def test_an_order_the_gate_allows_cannot_breach_the_ceiling(self, on):
        """Take every order the corrected gate allows, fill ALL of it, and
        the principal out of the account is still inside $0.90."""
        rnd = random.Random(23)
        gt = gates()
        allowed = 0
        for _ in range(800):
            up = D(rnd.randrange(0, 100)) / 100
            dn = D(rnd.randrange(0, 100)) / 100
            held = up * D("0.5") + dn * D("0.45")
            rest = [row(rnd.choice(["UP", "DOWN"]),
                        D(rnd.randrange(1, 40)) / 100,
                        D(rnd.randrange(5, 95)) / 100, coid=str(i))
                    for i in range(rnd.randrange(0, 3))]
            side = rnd.choice(["UP", "DOWN"])
            q = D(rnd.randrange(1, 40)) / 100
            p = D(rnd.randrange(5, 95)) / 100
            why = cash(gt, side, q, p, held=held, outstanding=rest,
                       inventory=inv(up, dn))
            if why is None:
                allowed += 1
                out = held + sum(o["qty"] * o["price"] for o in rest) + q * p
                assert out <= CAP
        assert allowed > 50

    def test_the_ceiling_itself_is_not_touched(self, on):
        assert gates().max_cash_deployed_usd == CAP


# ======================================================================
# 6. THE 21:30Z WINDOW, AS THE VENUE CONFIRMED IT
# ======================================================================

class TestTheStrandedClipOf2130z:
    """DOWN 0.31 @ 0.53, UP 0.31 @ 0.47, UP 0.31 @ 0.44: $0.4464 held, 0.31
    UP unmatched. After the second UP fill the market fell and a DOWN hedge
    at the touch would have cost 0.56 to 0.80."""
    HELD = "0.4464"
    BOOK = staticmethod(lambda: inv("0.62", "0.31"))

    @pytest.mark.parametrize("price", ["0.50", "0.56", "0.60", "0.70", "0.80"])
    def test_the_hedge_passed_the_cash_gate_before_and_passes_it_now(
            self, monkeypatch, price):
        monkeypatch.delenv(g.HEDGE_CASH_ENV, raising=False)
        assert cash(gates(), "DOWN", "0.31", price, held=self.HELD,
                    inventory=self.BOOK()) is None          # 0.7564 <= 0.90
        monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")
        assert cash(gates(), "DOWN", "0.31", price, held=self.HELD,
                    inventory=self.BOOK()) is None

    def test_what_the_reservation_was_and_is(self, monkeypatch):
        monkeypatch.delenv(g.HEDGE_CASH_ENV, raising=False)
        before = reserve(gates(), "DOWN", "0.31", "0.60",
                         inventory=self.BOOK())["this_order"]
        monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")
        after = reserve(gates(), "DOWN", "0.31", "0.60",
                        inventory=self.BOOK())["this_order"]
        assert before == D("0.31") and after == D("0.206")
        assert D(self.HELD) + before == D("0.7564")
        assert D(self.HELD) + after == D("0.6524")

    def test_the_refusals_cash_did_make_were_a_new_pair_beside_the_hedge(
            self, monkeypatch):
        """21:35:01Z, `PAIR_UP:CASH_COMMITMENT:0.999400>0.90`. The hedge was
        RESTING (DOWN 0.31 @ 0.50, reserved $0.31) and the engine proposed a
        further pair of 0.18 beside it. The hedge is now reserved $0.175.
        The new pair's own DOWN leg is not a hedge of anything -- the
        allowance is the stranded clip's, and it is spent once -- so that
        pair is still short of fitting, by the fee reservations."""
        hedge = row("DOWN", "0.31", "0.50", coid="hedge")
        pair_dn = row("DOWN", "0.18", "0.61", coid="planned")
        monkeypatch.delenv(g.HEDGE_CASH_ENV, raising=False)
        assert cash(gates(), "UP", "0.18", "0.35", held=self.HELD,
                    outstanding=[hedge, pair_dn], inventory=self.BOOK()
                    ) == "CASH_COMMITMENT:0.9994>0.90"
        monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")
        gt = gates()
        r = reserve(gt, "UP", "0.18", "0.35", outstanding=[hedge, pair_dn],
                    inventory=self.BOOK())
        assert r["hedged_resting"] == D("0.31")           # and not 0.49
        assert D(self.HELD) + r["resting"] + r["this_order"] == D("0.9042")
        assert cash(gt, "UP", "0.18", "0.35", held=self.HELD,
                    outstanding=[hedge, pair_dn], inventory=self.BOOK()
                    ) == "CASH_COMMITMENT:0.9042>0.90"


# ======================================================================
# 7. THROUGH THE EXECUTOR: WHAT THE GATE IS ACTUALLY HANDED
# ======================================================================

class TestThroughTheExecutor:

    def test_every_outstanding_row_names_its_market(self, tmp_path,
                                                    monkeypatch):
        h = LAD.build(LAD.harness(tmp_path, monkeypatch, **LAD.LADDER))
        rows = h.r.executor.outstanding_for_gates()
        assert len(rows) >= 2
        assert {r["market"] for r in rows} == {LAD.SLUG}

    def test_a_resting_complement_is_the_hedge_of_its_filled_leg(
            self, tmp_path, monkeypatch):
        """One UP order fills at the venue; the DOWN orders still resting
        are reserved at limit cost for exactly the unmatched quantity and
        at $1 a share for the rest."""
        monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")
        h = LAD.build(LAD.harness(tmp_path, monkeypatch, **LAD.LADDER))
        ex = h.r.executor
        h.venue.fill(LAD.resting_ids(h, "UP")[0])
        LAD.book_fills(h, 12)
        book = ex.inventory_for_gates(LAD.SLUG)
        unmatched = book["up_qty"] - book["dn_qty"]
        assert unmatched > 0
        rows = ex.outstanding_for_gates()
        down = [r for r in rows if r["intent"].endswith("BUY_SHORT")]
        assert sum(r["qty"] for r in down) > unmatched     # more than one clip
        r = ex.validation_gates.reserved_cash(
            outstanding=rows, order_qty=D("0.01"), order_price=D("0.5"),
            order_intent=LONG, inventory=book)
        assert r["hedged_resting"] == unmatched
        dollar = ex.validation_gates.pending_obligation(rows)
        assert dollar - unmatched < r["resting"] < dollar

    def test_an_order_awaiting_cancel_confirmation_is_still_reserved(
            self, tmp_path, monkeypatch):
        """The executor's outstanding rows are its live orders. A cancel
        that has been requested removes nothing until the venue's terminal
        state is booked."""
        monkeypatch.setenv(g.HEDGE_CASH_ENV, "ON")
        h = LAD.build(LAD.harness(tmp_path, monkeypatch, **LAD.LADDER))
        ex = h.r.executor
        before = {r["coid"]: r["qty"] for r in ex.outstanding_for_gates()}
        live = h.r.store.state["live_orders"]
        for o in live.values():
            o["cancel_requested"] = True          # sent, not yet confirmed
        after = {r["coid"]: r["qty"] for r in ex.outstanding_for_gates()}
        assert after == before and after

    def test_the_exposure_bound_already_priced_orders_at_their_limit(
            self, tmp_path, monkeypatch):
        """Unchanged by this commit, and pinned so it stays that way: a
        resting DOWN order adds `price * qty` of cost and its quantity of
        DOWN redemption, evaluated under both settlements."""
        h = LAD.build(LAD.harness(tmp_path, monkeypatch, **LAD.LADDER))
        st = h.r.store
        up = [(D(str(o["remaining"])), D(str(o["price"])))
              for o in st.live_orders().values() if o["side"] == "UP"]
        dn = [(D(str(o["remaining"])), D(str(o["price"])))
              for o in st.live_orders().values() if o["side"] == "DOWN"]
        up_q, up_c = sum(q for q, _ in up), sum(q * p for q, p in up)
        dn_q, dn_c = sum(q for q, _ in dn), sum(q * p for q, p in dn)
        worst = max(up_c + dn_c - dn_q, up_c + dn_c - up_q, D(0))
        assert st.risk_exposure_usd(LAD.SLUG) == worst
        for flag in ("", "ON"):
            monkeypatch.setenv(g.HEDGE_CASH_ENV, flag)
            assert st.risk_exposure_usd(LAD.SLUG) == worst
