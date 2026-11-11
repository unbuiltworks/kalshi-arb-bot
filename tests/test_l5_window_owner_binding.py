"""The 2026-11-09 20:00Z window: armed, authorized, and unable to trade.

WHAT HAPPENED, from that window's own 61 decision lines on deployment
`ed6ace01` / commit `4aa0d9244`. The campaign approval named
`cpc-btc-updown-15m-2026-11-09-2000z`, the preflight PASSED, the executor
ARMED at 20:00:06 and claimed the one authorized window -- and then:

    34  SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER
    18  STRATEGY_EXCLUSIVE_TWO_SIDED
     9  VALIDATION_GATES:CASH_COMMITMENT   (1.12 / 1.25 / 1.35 > 0.90)
     0  orders

`two_sided` is NULL on all 61 lines, and the boot-time runtime config says
why: `ts_named_market: cpc-btc-updown-15m-2026-11-09-1530z`. The window was
exclusive to a two-sided engine still bound to a market five hours dead.

EVERY REFUSAL WAS CORRECT. Exclusivity barred the L5-following path,
maker-only barred its taker translations, the cash envelope barred its
unclipped sizes. No gate was wrong and none is loosened here. The defect is
that ARMING NEVER ASKED whether the window's owner could act, so an owner
authorization was spent on a configuration in which no order was possible.

THIS FILE, in the order PR #217's acceptance specification asks:

  A. the failure, reproduced through the real `FaithfulExecutor.handle`;
  B. the arming boundary refusing it BEFORE any claim or window is spent;
  C. the preflight naming it ahead of the window;
  D. the same limits with the binding corrected: two venue-acknowledged
     post-only maker orders resting at once inside the $0.90 envelope and
     the $1.00 exposure cap, a partial fill booked only from the venue,
     the second leg handled, cancellation by exact venue id,
     reconciliation, and settlement accounting.

NOT A VENUE SIMULATOR. Every fill is scripted by the test; nothing here is
evidence of profitability, and section D's last tests say so in numbers.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

import tests.test_l5_arming_market_mismatch as AM
import tests.test_l5_e2e_window as H
import tests.test_l5_release_candidate_lifecycle as RC

from src import l5_campaign_approval as CA
from src import l5_faithful_translation as FT
from src import l5_live_authorization as AUTH
from src import l5_live_execution as E
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates

SLUG = H.SLUG
#: what `L5_TS_MARKET` still held at 20:00Z.
STALE = "cpc-btc-updown-15m-2026-11-09-1530z"

# the deployed configuration and the arming fixtures, re-exported rather
# than retyped (a fixture object carries its own autouse flag).
production_configuration = RC.production_configuration
clock = AM.clock
camp_root = AM.camp_root


def gates_2000z() -> ValidationGates:
    """The envelope that window actually ran: $0.90, not the earlier $12."""
    return ValidationGates(max_cash_deployed_usd=D("0.90"),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D("20"),
                           cash_policy="NO_RECYCLING")


def window(tmp_path, *, maker_only=True, bid="0.45", ask="0.46"):
    """One window at the 20:00Z limits: $0.90 cash, $1.00 exposure, two
    live orders, maker-only. The 20:00:10 book was 0.45 / 0.46."""
    w = H.Window(tmp_path, limits=RC.deployed_limits(), gates=gates_2000z())
    w.ex.maker_only = maker_only
    w.book.set(bid=bid, ask=ask)
    return w


@pytest.fixture
def stale_binding(monkeypatch):
    monkeypatch.setenv(TS.MARKET_ENV, STALE)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")


@pytest.fixture
def bound(monkeypatch):
    monkeypatch.setenv(TS.MARKET_ENV, SLUG)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")


# =====================================================================
# A. THE FAILURE, THROUGH THE REAL EXECUTION PATH
# =====================================================================

def test_the_stale_binding_makes_the_owning_engine_inert(stale_binding):
    assert TS.active(SLUG) is False
    assert TS.active(STALE) is True
    assert TS.window_strategy() == "TWO_SIDED"


def test_a_taker_translation_is_refused_MAKER_ONLY(tmp_path, stale_binding):
    """34 of the 61. Correct, and still refused after this change."""
    w = window(tmp_path)
    r = w.tick(20.0, side="UP", l5px="0.65", size=1.0, bid="0.45", ask="0.46")
    assert (r.get("refused_by_layer"), w.reason(r)) == (
        "SAFETY_OVERLAY", "MAKER_ONLY_NO_TAKER")
    assert w.venue.sent == []


def test_the_l5_following_path_is_refused_STRATEGY_EXCLUSIVE(tmp_path,
                                                             stale_binding):
    """18 of the 61. The window names TWO_SIDED as its owner, so the other
    strategy may not submit in it -- which is the gate working."""
    w = window(tmp_path, maker_only=False)
    r = w.tick(20.0, side="UP", l5px="0.65", size=1.0, bid="0.45", ask="0.46")
    assert r.get("refused_by_layer") == "STRATEGY_EXCLUSIVE_TWO_SIDED"
    assert (r.get("ORDER") or {}).get("submitted") is False
    assert w.venue.sent == []


def test_an_unclipped_size_is_refused_CASH_COMMITMENT(tmp_path,
                                                      stale_binding):
    """9 of the 61 -- production logged 1.12, 1.25 and 1.35 against 0.90.
    The L5-following path sizes from L5's own action, not from the clip."""
    w = window(tmp_path, maker_only=False)
    r = w.tick(20.0, side="UP", l5px="0.65", size=3.0, bid="0.45", ask="0.46")
    assert r.get("refused_by_layer") == "VALIDATION_GATES"
    assert w.reason(r).startswith("CASH_COMMITMENT:")
    assert w.reason(r).endswith(">0.90")
    assert w.venue.sent == []


def test_NO_decision_of_any_shape_can_place_an_order_in_that_window(
        tmp_path, stale_binding):
    """THE WINDOW, in one assertion. Whatever L5 did, nothing could be
    sent, and the two-sided plan is absent on every record -- exactly the
    `two_sided: null` on all 61 production lines."""
    w = window(tmp_path)
    shapes = [dict(side="UP", l5px="0.65", size=1.0),
              dict(side="DOWN", l5px="0.70", size=1.0),
              dict(side="UP", l5px="0.65", size=30.0),
              dict(side="DOWN", l5px="0.58", size=24.0),
              dict(side="UP", l5px="0.40", size=2.0),
              dict(side="DOWN", l5px="0.53", size=30.0)]
    for i, kw in enumerate(shapes):
        r = w.tick(20.0 + 10 * i, bid="0.45", ask="0.46", **kw)
        assert r.get("TWO_SIDED_PROBE") is None, kw
        assert w.reason(r) != "SUBMITTED", kw
    assert w.venue.sent == []
    assert w.store.still_fillable_order_count() == 0


# =====================================================================
# B. THE ARMING BOUNDARY REFUSES IT, AND IT COSTS THE OWNER NOTHING
# =====================================================================

def arm(market, ledger, campaign, **over):
    kw = dict(campaign=campaign, market=market,
              preflight={"preflight": "PASSED"}, store=AM.Store(),
              halted=False, halt_gate_present=True, reconciled=True,
              unresolved=(), limits=AM.LIMITS, shadow_locked=False,
              require_durable=False, window_bounds=AM.live_window(),
              nominal_window_s=900.0, transport_armable=True, path=ledger)
    kw.update(over)
    return AUTH.authorize_arming(**kw)


def approved_window(clock, monkeypatch):
    approved, _ = AM.two_windows(clock)
    doc = AM.doc_for(approved, clock)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    AM.owner_authorized(monkeypatch)
    return AM.slug_of(approved), doc


def test_arming_is_REFUSED_when_the_windows_owner_cannot_act(
        clock, camp_root, monkeypatch, tmp_path):
    """THE 20:00Z SHAPE AT THE BOUNDARY. The approval covers the market the
    executor is on -- there is no market mismatch for the campaign layer to
    catch -- and the two-sided engine that owns the window is bound
    elsewhere."""
    slug, doc = approved_window(clock, monkeypatch)
    monkeypatch.setenv(TS.MARKET_ENV, STALE)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")
    ledger = str(tmp_path / "auth.jsonl")
    t = AM.Transport(slug)
    campaign = CA.campaign_decision(slug, now=clock, reserve=False)

    with pytest.raises(AUTH.Refused) as ei:
        arm(slug, ledger, campaign)
    assert TS.WINDOW_OWNER_INACTIVE in str(ei.value), str(ei.value)

    # and the four things a refusal must not cost
    assert AM.claims_written(ledger) == 0, "an authorization claim was burned"
    assert AM.windows_spent(doc, camp_root) == 0, "an owner window was spent"
    assert t.arm_calls == 0 and t.armed is False
    assert t.submitted == []


def test_the_refusal_says_what_it_read_so_it_can_be_fixed(
        clock, camp_root, monkeypatch, tmp_path):
    slug, _ = approved_window(clock, monkeypatch)
    monkeypatch.setenv(TS.MARKET_ENV, STALE)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")
    out = TS.window_owner_refusal(slug)
    assert out["reason"] == TS.WINDOW_OWNER_INACTIVE
    assert out["market"] == slug
    assert out["named_market"] == STALE
    assert out["env"]["market"] == TS.MARKET_ENV


def test_the_gate_precedes_the_shadow_lock_ONLY_in_being_cheap_not_in_rank(
        clock, camp_root, monkeypatch, tmp_path):
    """The structural lock is still named first when both apply: a locked
    service must go on saying it is locked."""
    slug, _ = approved_window(clock, monkeypatch)
    monkeypatch.setenv(TS.MARKET_ENV, STALE)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")
    campaign = CA.campaign_decision(slug, now=clock, reserve=False)
    with pytest.raises(AUTH.Refused) as ei:
        arm(slug, str(tmp_path / "a.jsonl"), campaign, shadow_locked=True)
    assert "SHADOW_ONLY_LOCK" in str(ei.value)


@pytest.mark.parametrize("owner,named,refused", [
    ("", STALE, False),                       # no owner named: unchanged
    ("L5_FOLLOWING", STALE, False),           # needs no market binding
    ("TWO_SIDED", "SAME", False),             # correctly bound
    ("TWO_SIDED", "cpc-btc-updown-15m-*", False),   # a covering pattern
    ("TWO_SIDED", STALE, True),               # 20:00Z
    ("TWO_SIDED", "", True),                  # owner named, nothing bound
    ("TWOSIDED", "SAME", True),               # a typo enforces nothing
])
def test_the_gate_refuses_ONLY_an_inert_or_unrecognised_owner(
        clock, camp_root, monkeypatch, tmp_path, owner, named, refused):
    slug, _ = approved_window(clock, monkeypatch)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, owner)
    monkeypatch.setenv(TS.MARKET_ENV, slug if named == "SAME" else named)
    campaign = CA.campaign_decision(slug, now=clock, reserve=False)
    try:
        arm(slug, str(tmp_path / "a.jsonl"), campaign)
        why = ""
    except AUTH.Refused as exc:
        why = str(exc)
    assert ("WINDOW_OWNER" in why) is refused, (owner, named, why)


def test_an_owner_that_cannot_be_CHECKED_is_refused_not_waved_through(
        monkeypatch):
    """Fail closed: an owner is named and the strategy will not evaluate."""
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")

    def boom(market):
        raise RuntimeError("strategy unavailable")

    monkeypatch.setattr(TS, "window_owner_refusal", boom)
    out = AUTH.window_owner_refusal(SLUG)
    assert out["reason"] == "WINDOW_OWNER_UNVERIFIABLE"


def test_with_NO_owner_named_an_unavailable_strategy_refuses_nothing(
        monkeypatch):
    """So an image that does not carry the strategy is unaffected."""
    monkeypatch.delenv(TS.WINDOW_STRATEGY_ENV, raising=False)

    def boom(market):
        raise RuntimeError("strategy unavailable")

    monkeypatch.setattr(TS, "window_owner_refusal", boom)
    assert AUTH.window_owner_refusal(SLUG) is None


def test_the_executor_and_the_boundary_read_the_SAME_variable():
    """One name for the owner. A second spelling is a second opinion."""
    import inspect
    assert TS.WINDOW_STRATEGY_ENV == "L5_WINDOW_STRATEGY"
    assert '"L5_WINDOW_STRATEGY"' in inspect.getsource(E)


# =====================================================================
# C. THE PREFLIGHT NAMES IT BEFORE THE WINDOW
# =====================================================================

def row(pf, name):
    got = [r for r in pf["rows"] if r["check"] == name]
    assert len(got) == 1, (name, [r["check"] for r in pf["rows"]])
    return got[0]


def test_the_preflight_FAILS_on_the_stale_binding(monkeypatch):
    """At 20:00Z the heartbeat's verdict read ok with fail [] while no
    order was possible. It cannot now."""
    monkeypatch.setenv(TS.MARKET_ENV, STALE)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "x")
    pf = AUTH.authorization_preflight("cpc-btc-updown-15m-2026-11-09-2000z")
    r = row(pf, "window_owner_active")
    assert r["verdict"] == "FAIL"
    assert r["detail"]["named_market"] == STALE
    assert "window_owner_active" in pf["fail"]
    assert pf["ok"] is False


def test_the_preflight_PASSES_the_row_when_the_owner_is_bound(monkeypatch):
    m = "cpc-btc-updown-15m-2026-11-09-2000z"
    monkeypatch.setenv(TS.MARKET_ENV, m)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "x")
    pf = AUTH.authorization_preflight(m)
    assert row(pf, "window_owner_active")["verdict"] == "PASS"
    assert "window_owner_active" not in pf["fail"]


def test_without_a_market_the_row_is_NOT_EVALUATED_never_a_guess(monkeypatch):
    """The startup line has no slug, and an exact binding either matches a
    window or does not. Neither PASS nor FAIL is honest there."""
    monkeypatch.setenv(TS.MARKET_ENV, STALE)
    monkeypatch.setenv(TS.WINDOW_STRATEGY_ENV, "TWO_SIDED")
    pf = AUTH.authorization_preflight("")
    assert row(pf, "window_owner_active")["verdict"] == "NOT_EVALUATED"
    assert "window_owner_active" not in pf["fail"]
    assert "window_owner_active" in pf["not_evaluated"]


def test_the_row_is_reported_even_when_no_owner_is_named(monkeypatch):
    """A check that appears only on failure cannot be confirmed as run."""
    monkeypatch.delenv(TS.WINDOW_STRATEGY_ENV, raising=False)
    pf = AUTH.authorization_preflight("cpc-btc-updown-15m-2026-11-09-2000z")
    assert row(pf, "window_owner_active")["verdict"] == "PASS"


# =====================================================================
# D. THE SAME LIMITS, CORRECTLY BOUND: TWO ACKNOWLEDGED MAKER ORDERS
# =====================================================================

def oid_of(w, i):
    coid = w.venue.sent[i]["client_order_id"]
    return next(o for o, x in w.venue.by_oid.items()
                if x["client_order_id"] == coid)


def test_both_legs_are_ACKNOWLEDGED_and_rest_at_once(tmp_path, bound):
    """SPEC ITEM 3. Two distinct venue ids, both live at the same moment,
    inside the two-order cap -- the thing 20:00Z never reached."""
    w = window(tmp_path)
    r1, r2 = w.tick(30.0), w.tick(45.0)
    assert (w.reason(r1), w.reason(r2)) == ("SUBMITTED", "SUBMITTED")
    assert sorted(s["side"] for s in w.venue.sent) == ["DOWN", "UP"]
    ids = [(r["ORDER"] or {}).get("venue_order_id") for r in (r1, r2)]
    assert all(ids) and len(set(ids)) == 2, ids
    assert w.store.still_fillable_order_count() == 2
    assert w.venue.cancelled == []


def test_both_are_post_only_makers_and_neither_crosses(tmp_path, bound):
    """SPEC ITEM 1. Maker-only is not relaxed to get here: each leg rests
    BEHIND its own touch."""
    w = window(tmp_path)
    r1 = w.tick(30.0)
    w.tick(45.0)
    assert [s["mode"] for s in w.venue.sent] == [FT.MAKER, FT.MAKER]
    legs = {l["side"]: l for l in r1["TWO_SIDED_PROBE"]["legs"]}
    assert all(l["post_only"] is True for l in legs.values())
    assert D(legs["UP"]["price"]) < D("0.45")              # behind the bid
    assert D(legs["DOWN"]["price"]) < D("1") - D("0.46")   # behind its bid


def test_the_pair_is_SIZED_DOWN_to_fit_the_cash_envelope(tmp_path, bound):
    """SPEC ITEM 2. At $0.90 a one-contract pair does not fit, and the
    planner sizes BOTH legs down together rather than placing one leg the
    gate would then orphan. The commitment is counted the way the gate
    counts it: UP at its own price, DOWN at a dollar a share."""
    w = window(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    up = next(s for s in w.venue.sent if s["side"] == "UP")
    dn = next(s for s in w.venue.sent if s["side"] == "DOWN")
    assert D(str(up["qty"])) == D(str(dn["qty"])), "legs sized apart"
    q = D(str(up["qty"]))
    assert D("0") < q < D("1"), "sized down from the one-contract clip"
    committed = q * D(str(up["price"])) + q * D("1")
    assert committed <= D("0.90"), committed
    # and on the venue's quantity increment, not an arbitrary fraction
    assert (q / w.venue.quantity_increment) == (
        q / w.venue.quantity_increment).to_integral_value()


def test_worst_case_exposure_stays_inside_the_one_dollar_cap(tmp_path, bound):
    w = window(tmp_path)
    w.tick(30.0)
    r2 = w.tick(45.0)
    so = r2["SAFETY_OVERLAY"]
    assert so["permitted"] is True
    assert D(so["limits"]["max_total_exposure_usd"]) == D("1.00")
    assert D(so["risk_exposure_usd"]) <= D("1.00")


def test_a_third_order_is_refused_and_the_envelope_binds_FIRST(tmp_path,
                                                              bound):
    """A CORRECTION of this test's premise. I asserted MAX_LIVE_ORDERS, the
    bound that refuses a third order at the $12 envelope. At $0.90 the cash
    gate refuses EARLIER -- the pair already resting commits $0.8866 of it,
    so a further pair cannot fit -- and the order cap is never consulted.
    Two independent bounds, and the tighter one speaks. The property is
    that nothing more is sent."""
    w = window(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    r3 = w.tick(60.0)
    assert w.reason(r3) != "SUBMITTED"
    assert "CASH_COMMITMENT" in w.reason(r3), w.reason(r3)
    assert w.reason(r3).endswith(">0.90")
    assert len(w.venue.sent) == 2
    assert w.store.still_fillable_order_count() == 2


def test_at_the_wider_envelope_the_ORDER_CAP_is_what_refuses_the_third(
        tmp_path, bound):
    """The other bound, shown binding where the first does not."""
    w = H.Window(tmp_path, limits=RC.deployed_limits(),
                 gates=RC.deployed_gates())
    w.ex.maker_only = True
    w.book.set(bid="0.45", ask="0.46")
    w.tick(30.0)
    w.tick(45.0)
    assert w.reason(w.tick(60.0)) == "MAX_LIVE_ORDERS"
    assert len(w.venue.sent) == 2


def test_the_L5_following_path_STILL_cannot_submit_beside_the_pair(
        tmp_path, bound):
    """Exclusivity is preserved, not bypassed: with the engine bound, the
    orders that go out are the engine's own."""
    w = window(tmp_path)
    r1 = w.tick(30.0)
    assert r1["TWO_SIDED_PROBE"]["ok"] is True
    assert r1.get("refused_by_layer") is None


# ------------------------------------------------------------ fills
def test_a_PARTIAL_fill_is_booked_only_from_the_venue(tmp_path, bound):
    """SPEC ITEM 4. Inventory moves when the venue says so and by exactly
    what it says: 0.40 of the first leg, not the leg."""
    w = window(tmp_path)
    w.venue.fill_next("0.40")
    w.tick(30.0)
    first = w.venue.sent[0]
    assert w.store.position(SLUG, first["side"]) == D("0.40")
    other = "DOWN" if first["side"] == "UP" else "UP"
    assert w.store.position(SLUG, other) == D("0")


def test_a_RESTING_complement_is_never_counted_as_a_filled_hedge(tmp_path,
                                                                 bound):
    """SPEC ITEM 4, the false-pair guard. One leg fills, the other rests:
    the residual is the whole filled quantity until the venue fills more."""
    w = window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.tick(45.0)
    first, second = w.venue.sent[0], w.venue.sent[1]
    assert second["side"] != first["side"]
    assert w.store.position(SLUG, second["side"]) == D("0")
    assert abs(w.store.residual(SLUG)) == D(str(first["qty"]))
    assert w.store.still_fillable_order_count() == 1


def test_both_legs_filling_is_a_matched_pair_with_no_residual(tmp_path,
                                                              bound):
    w = window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.venue.fill_next()
    w.tick(45.0)
    up, dn = w.store.position(SLUG, "UP"), w.store.position(SLUG, "DOWN")
    assert up == dn and up > 0
    assert w.store.residual(SLUG) == D("0")
    assert w.store.still_fillable_order_count() == 0


# ----------------------------------------------- cancellation, reconcile
def test_unfilled_legs_are_cancelled_by_EXACT_venue_id(tmp_path, bound):
    """Nothing is cancelled that we did not place, and everything we placed
    and left unfilled is."""
    w = window(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    ids = {oid_of(w, 0), oid_of(w, 1)}
    w.tick(300.0)                    # past the 120 s order TTL
    assert set(w.venue.cancelled) == ids
    assert w.store.still_fillable_order_count() == 0


def test_a_FILLED_leg_is_never_cancelled(tmp_path, bound):
    w = window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.tick(45.0)
    filled_id, resting_id = oid_of(w, 0), oid_of(w, 1)
    w.tick(300.0)
    assert filled_id not in w.venue.cancelled
    assert resting_id in w.venue.cancelled


def test_the_ledger_and_the_venue_agree_after_the_window(tmp_path, bound):
    """Reconciliation: what we hold is what the venue filled, to the
    contract, and nothing is left working."""
    w = window(tmp_path)
    w.venue.fill_next("0.40")
    w.tick(30.0)
    w.tick(45.0)
    w.tick(300.0)
    venue_filled = {}
    for oid, intent in w.venue.by_oid.items():
        venue_filled[intent["side"]] = venue_filled.get(
            intent["side"], D("0")) + D(str(w.venue.acked.get(oid) or "0"))
    for side in ("UP", "DOWN"):
        assert w.store.position(SLUG, side) == venue_filled.get(side, D("0"))
    assert w.store.still_fillable_order_count() == 0
    assert not w.store.orders_uncertain()


def test_no_duplicate_client_order_id_ever_reaches_the_venue(tmp_path,
                                                             bound):
    w = window(tmp_path)
    for t in (30.0, 45.0, 60.0, 300.0, 330.0):
        w.tick(t)
    coids = [s["client_order_id"] for s in w.venue.sent]
    assert len(coids) == len(set(coids))


# ------------------------------------------------------------ settlement
def fills_as_records(w):
    """Settlement input built from the VENUE's record of what filled."""
    out = []
    for oid, intent in w.venue.by_oid.items():
        got = D(str(w.venue.acked.get(oid) or "0"))
        out.append({"L5_NATIVE": {"side": intent["side"]},
                    "FILL": ({"filled": True, "filled_qty": str(got),
                              "fill_price": str(intent["price"]),
                              "fee_usd": "0", "maker_rebate_usd": "0"}
                             if got > 0 else {"filled": False})})
    return out


def test_SETTLEMENT_a_matched_pair_redeems_a_dollar_a_share(tmp_path, bound):
    """SPEC ITEM 6. Realized net comes from settlement, on either outcome,
    from the prices actually filled."""
    w = window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.venue.fill_next()
    w.tick(45.0)
    recs = fills_as_records(w)
    q = w.store.position(SLUG, "UP")
    pair = sum(D(str(s["price"])) for s in w.venue.sent)
    for yes in (D("0"), D("1")):
        out = E.settle(recs, market=SLUG, yes_value=yes)
        assert D(out["matched"]) == q
        assert D(out["residual"]) == D("0")
        assert D(out["realized_net_usd"]) == (D("1") - pair) * q


def test_SETTLEMENT_a_stranded_leg_is_a_directional_result_not_a_pair(
        tmp_path, bound):
    """And the honest half. One leg fills, the complement is cancelled
    unfilled: the outcome is the leg's cost lost or its payout won. This is
    the 15:30Z shape, and passing these tests does not make it profitable."""
    w = window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.tick(45.0)
    w.tick(300.0)
    recs = fills_as_records(w)
    first = w.venue.sent[0]
    q, px = D(str(first["qty"])), D(str(first["price"]))
    wins = D("1") if first["side"] == "UP" else D("0")
    lost = E.settle(recs, market=SLUG, yes_value=D("1") - wins)
    won = E.settle(recs, market=SLUG, yes_value=wins)
    assert D(lost["matched"]) == D("0")
    assert D(lost["realized_net_usd"]) == -(q * px)
    assert D(won["realized_net_usd"]) == q * (D("1") - px)


def test_an_UNFILLED_window_settles_to_exactly_zero(tmp_path, bound):
    """Submitted and acknowledged is not filled, and not filled is not
    money: two resting orders that never trade realize nothing."""
    w = window(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(300.0)
    out = E.settle(fills_as_records(w), market=SLUG, yes_value=D("1"))
    assert D(out["matched"]) == D("0")
    assert D(out["realized_net_usd"]) == D("0")
