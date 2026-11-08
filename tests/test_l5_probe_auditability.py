"""Can a live probe produce an exact, durable, auditable settled P&L?

PRIORITY 3. The owner asked about a -$88.4258 "discrepancy". It is not a
discrepancy; it decomposes exactly, from the live heartbeat:

    -88.42580000  unknown_unattributed_realized
     -9.41580000  14 unattested post-baseline settlements
    -79.01000000  the ENTIRE pre-baseline realized total

The -79.01 is the whole account history before the L5 baseline, folded in
because its L5 share is unknown by construction. It is not newly
discovered loss. But `account_wide_realized` is L5-ledger-scoped, so the
two figures come from DIFFERENT populations -- and the record asserted
`account_wide = verified_l5 + manual + unknown_unattributed` with no
precondition, which reads as a balance that ought to tie out. It cannot,
and that reading is what looked like a missing $73.

THE PART THAT MATTERS FOR A PROBE is the other -9.4158, and specifically
HOW a settlement lands there: a leg whose cost basis the executor never
booked is priced at the $1/share upper bound. So a probe's settled P&L is
a MEASUREMENT only if every filled contract passed through the real
booking hooks. That is the property these tests establish.

No transport, no network, no order.
"""
from __future__ import annotations

import inspect
import os
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

SLUG = "cpc-btc-updown-15m-2026-11-08-1200z"


def store(tmp_path, name="inv.json"):
    return LX.InventoryStore(os.path.join(str(tmp_path), name))


def book_a_fill(st, coid, *, side, price, qty="1", market=SLUG):
    """Exactly the two hooks the executor calls, and nothing else."""
    st.record_submit(coid, {"market": market, "side": side,
                            "price": price, "qty": qty},
                     session_id="probe", mode="live")
    return st.apply_order_progress(
        coid, {"cum_qty": qty, "avg_px": price, "leaves_qty": "0",
               "terminal": True, "state": "ORDER_STATE_FILLED",
               "commission": "0", "venue_order_id": "V-" + coid},
        market=market, side=side)


# ============================================ the decomposition, as a test
def test_the_unattributed_bucket_decomposes_into_post_and_pre_baseline():
    """The arithmetic the owner's number came from, pinned so the framing
    cannot drift back. Values are the live ones from deployment a912f813."""
    post = [D("-0.16"), D("-0.65"), D("-0.7258"), D("-1.68"), D("-1.00"),
            D("0"), D("-0.40"), D("0"), D("0"), D("-1.00"), D("-0.90"),
            D("-1.50"), D("-0.40"), D("-1.00")]
    assert len(post) == 14
    assert sum(post) == D("-9.4158")
    assert sum(post) + D("-79.01") == D("-88.4258")


def test_most_of_the_actionable_set_is_not_an_L5_BTC15m_market():
    """Worth stating before anyone attributes it to this program: 6 of the
    14 rows are eth / sol / xrp / 1d markets from other lanes."""
    rows = {
        "cpc-btc-updown-15m-2026-10-16-0330z": D("-0.16"),
        "cpc-btc-updown-15m-2026-10-16-1230z": D("-0.65"),
        "cpc-btc-updown-15m-2026-10-30-0230z": D("-0.7258"),
        "cpc-btc-updown-15m-2026-11-02-2300z": D("-1.68"),
        "cpc-btc-updown-15m-2026-11-04-1445z": D("-1.00"),
        "cpc-btc-updown-15m-2026-11-05-2215z": D("0"),
        "cpc-btc-updown-15m-2026-11-05-2300z": D("-0.40"),
        "cpc-btc-updown-15m-2026-11-05-2315z": D("0"),
        "cpc-btc-updown-1d-2026-10-15-2100z": D("0"),
        "cpceth-eth-updown-15m-2026-10-16-0330z": D("-1.00"),
        "cpceth-eth-updown-15m-2026-10-16-1230z": D("-0.90"),
        "cpcsol-sol-updown-15m-2026-10-16-0330z": D("-1.50"),
        "cpcsol-sol-updown-15m-2026-10-16-1230z": D("-0.40"),
        "cpcxrp-xrp-updown-15m-2026-10-16-0330z": D("-1.00"),
    }
    btc15 = {k: v for k, v in rows.items()
             if k.startswith("cpc-btc-updown-15m-")}
    other = {k: v for k, v in rows.items() if k not in btc15}
    assert len(btc15) == 8 and sum(btc15.values()) == D("-4.6158")
    assert len(other) == 6 and sum(other.values()) == D("-4.80")


def test_the_identity_now_states_its_precondition():
    """It used to assert a balance that cannot tie when the pre-baseline
    total is non-zero, with `identity_holds: false` beside it unexplained.
    That is what read as a missing $73."""
    src = inspect.getsource(LX)
    assert "ONLY where the " in src
    assert "identity_precondition_met" in src
    assert "unknown_post_baseline_only" in src


# ================================= how a probe stays exactly auditable
def test_a_leg_booked_through_the_real_hooks_has_a_KNOWN_cost_basis(
        tmp_path):
    """THE WHOLE ANSWER TO PRIORITY 3. A settlement is priced at the
    $1/share upper bound only when the leg's basis is unknown. Booked
    through `record_submit` + `apply_order_progress`, it is known
    exactly."""
    st = store(tmp_path)
    book_a_fill(st, "l5f-p1", side="UP", price="0.62")
    assert st.position(SLUG, "UP") == D("1")
    assert st.leg_cost(SLUG)["UP"] == D("0.62")
    unknown = set(st.state.get("leg_cost_basis_unknown") or [])
    assert "%s|UP" % SLUG not in unknown, unknown
    assert unknown == set(), unknown


def test_a_position_that_never_passed_the_hooks_is_the_unknown_case(
        tmp_path):
    """The contrast, so the mechanism is not taken on faith: a venue NET
    position written without an order of ours is what lands in the
    placeholder set, and it reports no basis at all."""
    st = store(tmp_path)
    st.state.setdefault("positions", {})["%s|DOWN" % SLUG] = "1"
    st.state.setdefault("leg_cost_basis_unknown", []).append(
        "%s|DOWN" % SLUG)
    assert st.leg_cost(SLUG)["DOWN"] == D("0")
    assert "%s|DOWN" % SLUG in set(st.state["leg_cost_basis_unknown"])


def test_both_legs_of_a_completed_pair_are_exactly_priced(tmp_path):
    """What the probe must be able to say: the pair cost to the cent,
    from our own records, not an upper bound.

    OWN VERSUS WIRE, and this caught my test premise a fourth time. An
    order's `price` is the WIRE price. For UP, wire == own. For DOWN --
    a sell of YES resting on the ask -- own cost is `1 - wire`. So a DOWN
    leg whose OWN cost is 0.37 is submitted at WIRE 0.63, and the ledger
    correctly stores the own cost. Submitting DOWN at 0.37 would book an
    own cost of 0.63 and a pair of 1.25, not 0.99.
    """
    st = store(tmp_path)
    book_a_fill(st, "l5f-p1", side="UP", price="0.62")
    book_a_fill(st, "l5f-p2", side="DOWN", price="0.63")   # wire -> own 0.37
    legs = st.leg_cost(SLUG)
    assert legs["UP"] == D("0.62") and legs["DOWN"] == D("0.37")
    assert legs["UP"] + legs["DOWN"] == D("0.99")
    assert st.residual(SLUG) == 0
    assert not (st.state.get("leg_cost_basis_unknown") or [])


def test_a_partial_fill_is_priced_on_what_actually_filled(tmp_path):
    """A partial must not book the full intent's cost, or the pair price
    is wrong in the probe's favour."""
    st = store(tmp_path)
    st.record_submit("l5f-part", {"market": SLUG, "side": "UP",
                                  "price": "0.62", "qty": "2"},
                     session_id="probe", mode="live")
    st.apply_order_progress(
        "l5f-part", {"cum_qty": "1", "avg_px": "0.62", "leaves_qty": "1",
                     "terminal": False, "state": "ORDER_STATE_NEW",
                     "commission": "0", "venue_order_id": "V-1"},
        market=SLUG, side="UP")
    assert st.position(SLUG, "UP") == D("1")
    assert st.leg_cost(SLUG)["UP"] == D("0.62")


def test_polling_the_same_fill_twice_books_it_once(tmp_path):
    """Durability requires idempotence: a probe that double-books its own
    fill reports a pair cost twice the truth."""
    st = store(tmp_path)
    st.record_submit("l5f-idem", {"market": SLUG, "side": "UP",
                                  "price": "0.62", "qty": "1"},
                     session_id="probe", mode="live")
    prog = {"cum_qty": "1", "avg_px": "0.62", "leaves_qty": "0",
            "terminal": True, "state": "ORDER_STATE_FILLED",
            "commission": "0", "venue_order_id": "V-1"}
    for _ in range(3):
        st.apply_order_progress("l5f-idem", dict(prog), market=SLUG,
                                side="UP")
    assert st.position(SLUG, "UP") == D("1")
    assert st.leg_cost(SLUG)["UP"] == D("0.62")


def test_the_basis_survives_a_restart_on_the_same_file(tmp_path):
    """Durable, not just exact. A probe whose basis dies with the process
    cannot be audited after settlement."""
    st = store(tmp_path)
    book_a_fill(st, "l5f-dur", side="UP", price="0.62")
    st2 = store(tmp_path)
    assert st2.position(SLUG, "UP") == D("1")
    assert st2.leg_cost(SLUG)["UP"] == D("0.62")
    assert not (st2.state.get("leg_cost_basis_unknown") or [])


def test_the_probe_market_is_scoped_separately_from_the_legacy_set(
        tmp_path):
    """Score the probe on ITS OWN market. The eight legacy BTC15m markets
    and the six other-asset rows must not reach its number."""
    st = store(tmp_path)
    book_a_fill(st, "l5f-new", side="UP", price="0.62")
    legacy = "cpc-btc-updown-15m-2026-10-15-1915z|DOWN"
    st.state.setdefault("leg_cost_basis_unknown", []).append(legacy)
    unknown = set(st.state["leg_cost_basis_unknown"])
    assert legacy in unknown
    assert "%s|UP" % SLUG not in unknown
    assert st.leg_cost(SLUG)["UP"] == D("0.62")


# ================================ the pre-registered acceptance condition
def test_the_acceptance_condition_is_checkable_from_the_ledger_alone(
        tmp_path):
    """Stated as code so it cannot soften later: after settlement the
    probe's market must carry a known basis on every filled leg and be
    absent from the placeholder set. Anything else and the probe is
    UNINTERPRETABLE, not merely disappointing."""
    st = store(tmp_path)
    book_a_fill(st, "l5f-a", side="UP", price="0.62")
    book_a_fill(st, "l5f-b", side="DOWN", price="0.37")

    def probe_is_auditable(store_, market):
        unknown = set(store_.state.get("leg_cost_basis_unknown") or [])
        if any(k.startswith(market + "|") for k in unknown):
            return False
        legs = store_.leg_cost(market)
        held = {s for s in ("UP", "DOWN") if store_.position(market, s) > 0}
        return all(legs[s] > 0 for s in held)

    assert probe_is_auditable(st, SLUG) is True
    st.state.setdefault("leg_cost_basis_unknown", []).append(
        "%s|UP" % SLUG)
    assert probe_is_auditable(st, SLUG) is False


def test_the_ledger_stores_OWN_cost_for_a_DOWN_leg_not_the_wire_price():
    """Pinned because it has misled me four times, and because a probe
    that reports wire as own would misprice every pair it completes."""
    import tempfile
    st = LX.InventoryStore(os.path.join(tempfile.mkdtemp(), "inv.json"))
    book_a_fill(st, "l5f-dn", side="DOWN", price="0.63")
    assert st.leg_cost(SLUG)["DOWN"] == D("0.37")
    assert st.leg_cost(SLUG)["DOWN"] == D(1) - D("0.63")
