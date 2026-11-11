"""The pair-cost ceiling at the common submission boundary.

WHAT 1215z ACTUALLY DID, because I first reported it backwards. Two
exchange-confirmed fills in cpc-btc-updown-15m-2026-10-20-1215z settled as:

    up_qty 2.46  up_cost $2.2140  -> own price 0.90
    dn_qty 2.46  dn_cost $0.1476  -> own price 0.06
    PAIR COST 0.96 exactly, redeemed $2.46, settlement_pnl +$0.0984

That is a PROFIT and the first economically valid completed pair this
programme has produced -- and it came from the L5-following path.

THE READING ERROR, KEPT HERE BECAUSE IT IS THE WHOLE POINT OF THE CEILING'S
BASIS. The order records show avg_px 0.9400 and 0.9000, and those are WIRE
prices. A DOWN buy at wire 0.94 costs 0.06 in own terms, so summing wire
prices across sides (0.94 + 0.90 = 1.84) double-counts the YES side and
invents a loss that did not happen. The ceiling therefore has to be computed
in OWN-outcome terms off `leg_cost`, which is what these tests assert.

The ceiling still matters: a pair above 0.96 buys something that pays 1.00,
and the L5-following path had no pair discipline of any kind.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-10-20-1215z"


@pytest.fixture()
def ex(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "PAIR-CEILING")
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    t = H.FakeTransport(fill_all=True)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    x = E.FaithfulExecutor(H.store(tmp_path),
                           H.limits(max_live_orders=4), transport=t,
                           now_fn=H.NOW)
    return x, t, H, E


def test_the_REAL_1215z_pair_is_ALLOWED_at_exactly_the_ceiling(ex):
    """0.90 own + 0.06 own = 0.96, the spring target exactly. This is the
    trade that worked and the ceiling must not refuse it."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2.46"), D("0.90"), basis="OWN")
    assert x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                               {"price": "0.06", "qty": "2.46"}) is None
    # the settled economics, for the record
    cost = D("2.46") * D("0.90") + D("2.46") * D("0.06")
    assert cost == D("2.3616")
    assert D("2.46") - cost == D("0.0984")


def test_a_pair_genuinely_above_the_ceiling_in_OWN_terms_is_refused(ex):
    """The hypothetical the ceiling exists for -- NOT what 1215z did. A
    0.94 own UP plus a 0.90 own DOWN really would be a 1.84 pair and a
    certain -$2.0664 on 2.46 contracts."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2.46"), D("0.94"), basis="OWN")
    bad = x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                              {"price": "0.90", "qty": "2.46"})
    assert bad is not None
    assert bad["why"] == "PAIR_COST_ABOVE_CEILING"
    assert D(bad["effective_pair_cost"]) == D("1.84")
    assert D(bad["ceiling"]) == TS.MAX_PAIR_COST == D("0.96")
    assert (D(bad["matched_after"]) - D(bad["confirmed_cost_usd"])
            == D("-2.0664"))


def test_the_FIRST_leg_of_a_pair_is_not_constrained_by_it(ex):
    """There is nothing to match yet, so no pair cost exists. A ceiling that
    refused the first leg would refuse every pair."""
    x, t, H, E = ex
    assert x.pair_cost_refusal(SLUG, {"side": "UP"},
                               {"price": "0.94", "qty": "2.46"}) is None


def test_a_cheap_completing_leg_is_allowed(ex):
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2.46"), D("0.40"), basis="OWN")
    assert x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                               {"price": "0.54", "qty": "2.46"}) is None
    # ... and one cent too dear is not
    bad = x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                              {"price": "0.57", "qty": "2.46"})
    assert bad is not None and D(bad["effective_pair_cost"]) == D("0.97")


def test_a_leg_that_creates_no_new_matched_quantity_is_not_judged(ex):
    """Extending the side that is already ahead creates no pair, so there is
    no pair cost to bound -- the residual cap governs that, not this."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2.00"), D("0.94"), basis="OWN")
    assert x.pair_cost_refusal(SLUG, {"side": "UP"},
                               {"price": "0.94", "qty": "1.00"}) is None


def test_a_SUBMITTED_order_does_not_count_as_a_hedge(ex):
    """The basis is `leg_cost`, written from venue-confirmed fills. A resting
    DOWN order must not make a dear UP pair look matched."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2.46"), D("0.94"), basis="OWN")
    x.store.record_submit("c-resting", {"market": SLUG, "side": "DOWN",
                                        "price": "0.90", "qty": "2.46"})
    # the resting order changes nothing: the ceiling still refuses
    bad = x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                              {"price": "0.90", "qty": "2.46"})
    assert bad is not None
    assert D(bad["held_confirmed"]["DOWN"]) == D("0")


@pytest.mark.parametrize("dec,tr", [
    ({"side": "SIDEWAYS"}, {"price": "0.5", "qty": "1"}),
    ({"side": "UP"}, {"price": "0", "qty": "1"}),
    ({"side": "UP"}, {"price": "0.5", "qty": "0"}),
    ({"side": "UP"}, {"price": None, "qty": "1"}),
])
def test_unreadable_inputs_REFUSE_rather_than_assume_the_pair_is_fine(
        ex, dec, tr):
    x, t, H, E = ex
    out = x.pair_cost_refusal(SLUG, dec, tr)
    assert out is not None and out["why"] != "PAIR_COST_ABOVE_CEILING"


def test_unreadable_STATE_refuses(ex):
    x, t, H, E = ex

    class Blind:
        def leg_cost(self, market):
            raise OSError("volume gone")
    x.store = Blind()
    out = x.pair_cost_refusal(SLUG, {"side": "UP"},
                              {"price": "0.5", "qty": "1"})
    assert out is not None and out["why"].startswith("STATE_UNREADABLE")


# ================================================ strategy mutual exclusion

def test_the_exclusivity_gate_and_the_pair_ceiling_both_precede_the_CLAIM(
        ex):
    """ONE STRATEGY PER AUTHORIZED WINDOW, and the ceiling before the wire.

    In 1215z both strategies ran: the L5-following path spent $4.5264 of the
    $4.65 envelope on its two fills and the two-sided engine then refused
    CASH_COMMITMENT:6.99>4.65 -- the cash gate working correctly on an
    envelope another strategy had already emptied.

    Asserted on the ORDER of the real `handle` source, because what matters
    is that neither gate can be reached after a claim has been spent or an
    order sent. Driving it end to end instead would depend on the maker
    translation configuration, which is not what these two gates are about.
    """
    import inspect

    from src import l5_live_execution as E
    body = inspect.getsource(E.FaithfulExecutor.handle)
    i_pair = body.index("self.pair_cost_refusal(")
    i_excl = body.index("STRATEGY_EXCLUSIVE_TWO_SIDED")
    i_claim_sub = body.index("AUTH.claim_submission(")
    i_claim_acc = body.index("AUTH.claim_accumulation_leg(")
    i_submit = body.index("self.transport.submit(")
    for name, i in (("pair ceiling", i_pair), ("exclusivity", i_excl)):
        assert i < i_claim_sub, name
        assert i < i_claim_acc, name
        assert i < i_submit, name
    # and the exclusivity gate keys on a two-sided leg actually being
    # pending, not merely on the market being authorized
    call = body[i_excl - 400:i_excl]
    assert "_probe_leg_pending" in call


def test_the_ceiling_is_computed_in_OWN_terms_not_WIRE_terms(ex):
    """THE READING ERROR, PINNED. Wire prices cannot be summed across sides.
    A DOWN leg whose avg_px reads 0.94 on the wire costs 0.06, and the pair
    with a 0.90 own UP is 0.96 -- not the 1.84 that summing wires suggests."""
    x, t, H, E = ex
    assert TS.wire_price("DOWN", "0.06") == D("0.94")
    assert D("0.06") + TS.wire_price("DOWN", "0.06") == D(1)
    x.store.apply_fill(SLUG, "UP", D("2.46"), D("0.90"), basis="OWN")
    # own 0.06 -> allowed; the wire number 0.94 would not be
    assert x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                               {"price": "0.06", "qty": "2.46"}) is None
    assert x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                               {"price": "0.94", "qty": "2.46"}) is not None
