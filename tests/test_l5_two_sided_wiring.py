"""The two-sided probe reaches the transport as a PAIR, post-only, gated.

Drives the REAL FaithfulExecutor. The lesson from the queue measurement is
that implementing a constructor is not wiring it, so every assertion here is
about what actually reached the transport.
"""
from decimal import Decimal as D

import pytest

from src import l5_faithful_translation as FT
from src import l5_live_authorization as AUTH
from src import l5_order_body as OB
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "TS-E2E-1")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "TS-E2E-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    return H, E


def gates(cash="8", deficit="3", cum="10"):
    return ValidationGates(max_cash_deployed_usd=D(cash),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D(cum),
                           max_worst_case_deficit_usd=D(deficit))


_SEQ = [0]


def _row(H, **over):
    """A fresh L5 row per tick. Each live decision carries its own
    action_key, and the client order id is derived from it -- two identical
    rows are correctly deduplicated as the same order."""
    _SEQ[0] += 1
    kw = dict(bid="0.60", ask="0.61", l5px="0.60", side="DOWN",
              key="tick-%d" % _SEQ[0])
    kw.update(over)
    return H.row(**kw)


def _book(**over):
    b = {"bid": "0.60", "ask": "0.61", "bid_shares": "900",
         "ask_shares": "5000", "stream_age_s": "0.05"}
    b.update(over)
    return b


def mk(H, E, tmp_path, t=None, g=None):
    t = t or H.FakeTransport(fill_all=False)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path),
                            H.limits(max_live_orders=2), transport=t,
                            now_fn=H.NOW)
    ex.validation_gates = g if g is not None else gates()
    return ex, t


# ------------------------------------------------- both legs reach the wire

def test_both_legs_reach_the_transport_post_only_on_successive_ticks(
        probe, tmp_path):
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    r1 = ex.handle(_row(H), _book(), market="m", window="21:00")
    r2 = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 2, (r1.get("refused_reason"),
                              r2.get("refused_reason"))
    by = {s["side"]: s for s in t.sent}
    assert set(by) == {"UP", "DOWN"}
    # cheaper leg first: DOWN at 0.37 costs less than UP at 0.58
    assert t.sent[0]["side"] == "DOWN"
    assert D(by["UP"]["price"]) == D("0.58")
    assert D(by["DOWN"]["price"]) == D("0.37")
    for s in t.sent:
        # POST-ONLY IS CARRIED BY THE MODE. l5_order_body derives
        # participateDontInitiate from `mode == "MAKER"` and ignores any
        # post_only key, so the wire body is the only proof that counts.
        assert s["mode"] == FT.MAKER
        assert OB.from_intent(s, "m")["participateDontInitiate"] is True
    # and it replaced the ordinary L5 intent rather than adjusting it
    assert r1["TWO_SIDED_PROBE"]["pair_cost"] == "0.95"
    assert r1["TWO_SIDED_PROBE"]["leg_to_place"] == "DOWN"


def test_a_third_tick_places_nothing_because_both_claims_are_spent(
        probe, tmp_path):
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    for _ in range(4):
        ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 2


def test_restart_recovery_cannot_replace_a_claimed_leg(probe, tmp_path):
    """The claim file is the durable record, so a fresh executor on the same
    volume re-places nothing."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 1
    ex2, t2 = mk(H, E, tmp_path)          # new process, same ledger dir
    ex2.handle(_row(H), _book(), market="m", window="21:00")
    assert [s["side"] for s in t2.sent] == ["UP"]     # only the unclaimed leg
    ex3, t3 = mk(H, E, tmp_path)
    ex3.handle(_row(H), _book(), market="m", window="21:00")
    assert t3.sent == []


# ------------------------------------------------------- the pair pre-check

def test_the_pair_is_sized_so_BOTH_legs_fit_the_cash_cap(probe, tmp_path):
    """ITEM 5. Leg one must never spend the headroom leg two needs, so the
    pair is sized DOWN until both fit -- it does not place a big first leg
    and then discover there is nothing left for the second."""
    H, E = probe
    ex, t = mk(H, E, tmp_path, g=gates(cash="3"))
    ex.handle(_row(H), _book(), market="m", window="21:00")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 2
    by = {x["side"]: x for x in t.sent}
    q = D(by["UP"]["qty"])
    assert D(by["DOWN"]["qty"]) == q
    # the gate's own basis: short at a dollar a share plus the long at price
    assert q * (D(1) + D(by["UP"]["price"])) <= D("3")


def test_an_envelope_below_the_venue_grid_places_NOTHING(probe, tmp_path):
    H, E = probe
    ex, t = mk(H, E, tmp_path, g=gates(cash="0.005", deficit="0.005"))
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] in ("NO_LEGAL_QUANTITY",
                                   "LOSS_CEILING_UNREACHABLE")


def test_a_tight_deficit_bound_shrinks_the_pair_rather_than_overshooting(
        probe, tmp_path):
    H, E = probe
    ex, t = mk(H, E, tmp_path, g=gates(deficit="1.00"))
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 1
    # the single resting leg's own worst case stays inside the bound
    s0 = t.sent[0]
    assert D(s0["qty"]) * D(s0["price"]) <= D("1.00")


def test_the_cumulative_ceiling_is_enforced_independently(probe, tmp_path):
    """A run-scoped ceiling already breached refuses regardless of sizing."""
    H, E = probe
    ex, t = mk(H, E, tmp_path, g=gates(cum="1"))
    ex.store.state["realized_today"] = "-5"   # already past the ceiling
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert "CUMULATIVE_LOSS_LIMIT" in r["refused_reason"]


# ------------------------------------------------------------ inertness

def test_inert_with_no_experiment_configured(probe, tmp_path, monkeypatch):
    H, E = probe
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    # the ordinary L5 path runs instead; whatever it does, it is NOT a probe
    for s in t.sent:
        assert D(str(s.get("price"))) != D("0.58")


def test_an_unauthorized_market_is_untouched(probe, tmp_path):
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="other", window="21:00")
    assert all(s.get("market") != "other"
               or D(str(s.get("price"))) != D("0.58") for s in t.sent)


# ------------------------------------------------- no fallback, fail closed

def test_a_book_the_probe_refuses_submits_NOTHING_not_an_L5_order(
        probe, tmp_path, monkeypatch):
    """At the touch the pair is 0.99, under the fee floor. The ordinary
    lambda intent must NOT be sent in its place."""
    H, E = probe
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "0")
    ex, t = mk(H, E, tmp_path / "fresh")
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "EDGE_BELOW_FEE_FLOOR"


def test_a_stale_book_refuses_both_legs(probe, tmp_path):
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    r = ex.handle(_row(H), _book(stream_age_s="9"), market="m",
                  window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "BOOK_STALE"


def test_a_persistence_failure_refuses_rather_than_submitting(probe,
                                                              tmp_path,
                                                              monkeypatch):
    H, E = probe
    ex, t = mk(H, E, tmp_path)

    def boom(*a, **k):
        raise AUTH.Refused("PROBE_CLAIM_UNAVAILABLE", {"note": "disk"})
    monkeypatch.setattr(AUTH, "claim_probe_leg", boom)
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "PROBE_CLAIM_UNAVAILABLE"


# ------------------------------------------------------------ the claims

def test_each_leg_has_its_own_one_shot_claim():
    assert AUTH.probe_leg_sid("e", "UP") != AUTH.probe_leg_sid("e", "DOWN")
    assert AUTH.PROBE_LEGS == ("UP", "DOWN")


def test_a_claim_without_an_experiment_refuses(monkeypatch):
    monkeypatch.delenv(AUTH.EXPERIMENT_ENV, raising=False)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_probe_leg("UP", {})
    assert e.value.reason == "PROBE_CLAIM_NO_EXPERIMENT"


def test_an_unknown_leg_refuses(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "x")
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "a.jsonl"))
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_probe_leg("SIDEWAYS", {})
    assert e.value.reason == "PROBE_CLAIM_BAD_LEG"


def test_an_unreadable_claim_state_is_treated_as_spent(probe, tmp_path,
                                                       monkeypatch):
    """Re-placing a leg we cannot rule out is the only unsafe reading."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    monkeypatch.setattr(AUTH, "claim_held",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("io")))
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "BOTH_LEGS_SPENT"
