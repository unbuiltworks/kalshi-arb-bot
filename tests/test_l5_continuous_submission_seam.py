"""The one seam that stopped the continuous candidate from ever submitting.

`l5_continuous_inventory` decides from the current book and the current
realized inventory, with no round and no stored plan. Until this seam
existed, those decisions could not reach the venue AT ALL: the claimable
identity was available only as `controller_action_key(market, round, leg)`,
so a decision taken without a round hit CONTROLLER_NO_PROBE_LEG at
`l5_live_execution` and was refused BEFORE submission. That -- not the
economics, and not the durable claim, which already has a non-round path in
`AUTH.claim_submission` -- is what made the candidate untestable against
real venue fills under any authorization.

These tests drive the REAL executor through `handle(controller=...)`, the
same path the live timer uses, and assert on what actually reached the
transport. Nothing is armed; `FakeTransport` is the wire.
"""
from decimal import Decimal as D

import pytest

from src import l5_continuous_inventory as CI
from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "CI-SEAM-1")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "CI-SEAM-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    for k, v in (("L5_LIVE_AUTH_MAX_OPEN", "40"),
                 ("L5_LIVE_AUTH_MAX_COMPLETE", "40"),
                 ("L5_LIVE_AUTH_MAX_SUBMISSIONS", "40")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 60.0)
    return H, E, monkeypatch, tmp_path


def book(**over):
    b = {"bid": "0.60", "ask": "0.61", "bid_shares": "900",
         "ask_shares": "5000", "stream_age_s": "0.05"}
    b.update(over)
    return b


def mk(H, E, tmp_path, orders=2, fill=False):
    t = H.FakeTransport(fill_all=fill)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path),
                            H.limits(max_live_orders=orders), transport=t,
                            now_fn=H.NOW, maker_treatment=True)
    ex.validation_gates = ValidationGates(
        max_cash_deployed_usd=D("8"), max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("10"),
        max_worst_case_deficit_usd=D("3"))
    return ex, t


def intent(E, bk, side=None, size="3", tick=D("0.01")):
    up = E.controller_provisional_price("UP", bk, tick)
    dn = E.controller_provisional_price("DOWN", bk, tick)
    if side is None:
        side, px = ("UP", up) if up <= dn else ("DOWN", dn)
    else:
        px = up if side == "UP" else dn
    return {"provenance": E.CONTROLLER_GENERATED, "maker_class": None,
            "decision": {"side": side, "price": px,
                         "l5_size": D(size), "relation": "open"}}


def tick(ex, E, bk=None, **kw):
    bk = bk if bk is not None else book()
    return ex.handle(None, bk, market="m", window="21:00",
                     controller=intent(E, bk, **kw))


# ========================================== the flag is off by default
def test_by_default_the_round_scoped_identity_is_unchanged(probe):
    H, E, mp, tmp = probe
    mp.delenv(E.CONTINUOUS_INVENTORY_ENV, raising=False)
    assert E.continuous_inventory_enabled() is False


@pytest.mark.parametrize("val", ["", "no", "0", "true", "1", "Y"])
def test_only_an_explicit_YES_enables_the_seam(probe, val):
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, val)
    assert E.continuous_inventory_enabled() is False


# ============================= with the flag ON a continuous decision submits
def test_with_the_seam_on_a_decision_with_no_round_reaches_the_transport(probe):
    """THE REGRESSION. With the seam off this refused CONTROLLER_NO_PROBE_LEG
    and nothing was ever sent, which is why the candidate could not be
    tested against real fills."""
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp)
    rec = tick(ex, E)
    assert rec["ORDER"]["submitted"] is True, rec["ORDER"].get("reason")
    assert len(t.sent) == 1


def test_the_identity_used_is_the_content_addressed_one(probe):
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp)
    rec = tick(ex, E)
    key = rec["action_key"]
    assert key.startswith(CI.KEY_PREFIX + "|m|")
    assert "|r0|" not in key and "|r1|" not in key


def test_the_key_matches_the_order_that_was_actually_sent(probe):
    """The key and the order must be one object, not two that can disagree."""
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp)
    rec = tick(ex, E)
    sent = t.sent[0]
    assert rec["action_key"] == CI.action_key(
        "m", str(sent["side"]), D(str(sent["price"])))


# ================================================= repeated pairing
def test_repeated_ticks_place_one_order_per_distinct_intent(probe):
    """Idempotence survives the change. Six identical ticks against an
    unchanged book yield ONE order per distinct (side, price) intent and
    never two of the same -- so a repeating trigger cannot stack orders.

    It is two intents, not one, because this is a TWO-SIDED engine: it rests
    the opening leg and then wants the complement, which is a pair rather
    than churn. What must never happen is the same intent going out twice,
    and that is what the content-addressed identity guarantees."""
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp)
    keys = []
    for _ in range(6):
        k = tick(ex, E).get("action_key")
        if k and k.startswith(CI.KEY_PREFIX):
            keys.append(k)
    distinct = set(keys)
    # one order on the wire for each distinct intent, and no intent twice
    assert len(t.sent) == len(distinct), (len(t.sent), distinct)
    coids = [o.get("client_order_id") for o in t.sent]
    assert len(coids) == len(set(coids))
    # and each distinct intent is one SIDE: no side was quoted twice
    assert len({str(o["side"]) for o in t.sent}) == len(t.sent)


def test_a_moved_book_is_a_new_identity_and_may_place_again(probe):
    """The intended behavioural change. The round-scoped key could not
    express 'a different order in the same market'."""
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp)
    a = tick(ex, E)
    b = tick(ex, E, bk=book(bid="0.40", ask="0.41"))
    assert a["action_key"] != b["action_key"]
    assert len(t.sent) == 2


def test_both_sides_can_be_worked_in_one_market(probe):
    """Repeated pairing needs UP and DOWN to be separately identifiable."""
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp)
    up = tick(ex, E, side="UP")
    dn = tick(ex, E, side="DOWN")
    assert up["action_key"] != dn["action_key"]
    assert {str(o["side"]) for o in t.sent} == {"UP", "DOWN"}


# ====================================== inventory reconciliation
def test_a_fill_is_booked_into_inventory_under_the_seam(probe):
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp, fill=True)
    rec = tick(ex, E)
    assert rec["ORDER"]["submitted"] is True
    side = str(t.sent[0]["side"])
    assert ex.store.position("m", side) > 0


def test_the_continuous_loop_reads_that_inventory_and_wants_the_complement(
        probe):
    """End to end: a fill under the seam becomes inventory, and the loop
    then asks for the OPPOSITE side -- which is what a REDUCE is."""
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp, fill=True)
    tick(ex, E)
    side = str(t.sent[0]["side"])
    up = ex.store.position("m", "UP")
    dn = ex.store.position("m", "DOWN")
    cost = D(str(t.sent[0]["price"])) * D(str(t.sent[0]["qty"]))
    nxt = CI.decide(market="m", bid=D("0.60"), ask=D("0.61"),
                    tick=D("0.01"), up_q=up, dn_q=dn,
                    up_cost=(cost if side == "UP" else D(0)),
                    dn_cost=(cost if side == "DOWN" else D(0)))
    if nxt.get("ok"):
        assert nxt["action"] in (CI.REDUCE, CI.REVERSE)
        assert nxt["side"] != side
    else:
        # refusing is acceptable; inventing a basis is not
        assert nxt["reason"] in ("COMPLETION_PAIR_ABOVE_CEILING",
                                 "HELD_COST_UNKNOWN", "CASH_CAP_WOULD_BIND")


def test_a_matched_pair_reads_as_flat_so_the_market_can_be_worked_again(probe):
    H, E, mp, tmp = probe
    st = H.store(tmp)
    st.apply_fill("m", "UP", D("2.52"), D("0.47"), basis="OWN")
    st.apply_fill("m", "DOWN", D("2.52"), D("0.48"), basis="OWN")
    assert st.residual("m") == D(0)
    r = CI.decide(market="m", bid=D("0.60"), ask=D("0.61"), tick=D("0.01"),
                  up_q=st.position("m", "UP"), dn_q=st.position("m", "DOWN"),
                  up_cost=D("1.1844"), dn_cost=D("1.2096"))
    assert r["ok"] and r["action"] == CI.OPEN


# ============================== nothing else was loosened by the seam
def test_the_seam_grants_no_transmit_allowance(probe):
    """The seam decides an IDENTITY, never a permission. An executor whose
    transport cannot transmit must still refuse, exactly as without it."""
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex = E.FaithfulExecutor(H.store(tmp), H.limits(), now_fn=H.NOW)
    rec = tick(ex, E)
    assert rec.get("ORDER") is None or rec["ORDER"]["submitted"] is False
    assert rec.get("refused_reason")


def test_the_order_concurrency_ceiling_still_binds_under_the_seam(probe):
    H, E, mp, tmp = probe
    mp.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    ex, t = mk(H, E, tmp, orders=1)
    tick(ex, E)
    r = tick(ex, E, bk=book(bid="0.40", ask="0.41"))
    assert r.get("ORDER") is None
    assert r["refused_by_layer"] == "SAFETY_OVERLAY"
    assert r["refused_reason"] == "MAX_LIVE_ORDERS"
    assert len(t.sent) == 1
