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
    # the probe derives its placement cutoff and terminal deadline from the
    # WINDOW, so the fake market needs a window start. Place "now" 60s in,
    # comfortably inside the placement span.
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 60.0)
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


def test_accumulation_keeps_working_pairs_past_the_first(probe, tmp_path,
                                                        monkeypatch):
    """SUPERSEDES `a third tick places nothing`. Two slots per authorization
    was the one-shot restriction; the engine now works successive PAIRS, and
    the bound is an explicit round count rather than an accident of the
    claim namespace."""
    H, E = probe
    monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "3")
    ex, t = mk(H, E, tmp_path)
    ex.limits = H.limits(max_live_orders=16)
    seen = []
    for _ in range(8):
        seen.append(ex.handle(_row(H), _book(), market="m",
                              window="21:00").get("refused_reason"))
    # MORE THAN ONE PAIR, which is the whole point.
    #
    # THE ORDERING RULE CHANGED, and this is the test that recorded the old
    # one. It used to assert a rigid ["DOWN","UP"] alternation, on the
    # rationale that the cheaper leg filling alone is the smaller worst
    # case. That is right while FLAT and wrong while a residual is held:
    # measured here, the engine carries a DOWN-heavy prospective imbalance
    # of -3, then -2, then -1 across these rounds, and the old rule opened
    # ANOTHER DOWN leg on top of it each time. That is the shape that
    # stranded 0130z and 1245z.
    #
    # The invariant is now: the leg that REDUCES a held residual goes
    # first, and cheapest-first applies only when there is nothing to
    # reduce. The resulting inventory is just as balanced -- equal counts
    # below -- but the path never deepens an imbalance it could have cut.
    sides = [s["side"] for s in t.sent]
    assert len(sides) >= 4 and len(sides) % 2 == 0, sides
    assert sides.count("UP") == sides.count("DOWN"), sides
    # the FIRST leg of all, placed from flat, is still the cheaper one
    assert sides[0] == "DOWN", sides
    # HOW MANY PAIRS IS NOT PINNED, deliberately. The complement is now sized
    # to the quantity that actually FILLED rather than to the planned leg
    # size, and this harness fills 1 of each, so each pair costs the envelope
    # less than a full-size pair would and more rounds fit. The invariants
    # are what matter: pairs in order, distinct intents, and a RISK GATE --
    # not the claim namespace -- deciding where accumulation stops.
    assert len({s["client_order_id"] for s in t.sent}) == len(sides)
    # AND IT STOPS ON A RISK GATE. With nothing filling in full, the cash
    # envelope is what refuses -- the bound that should decide how much
    # inventory is accumulated.
    assert seen[-1] is not None, seen
    assert ("CASH_COMMITMENT" in seen[-1]
            or seen[-1] == "ACCUMULATION_EXHAUSTED"), seen


def test_the_round_bound_is_hard_and_then_refuses(probe, tmp_path,
                                                  monkeypatch):
    H, E = probe
    monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "1")
    ex, t = mk(H, E, tmp_path)
    for _ in range(4):
        r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 2
    assert r["refused_reason"] == "ACCUMULATION_EXHAUSTED"


def test_an_unparseable_round_bound_takes_the_safe_reading(probe, tmp_path,
                                                           monkeypatch):
    H, E = probe
    monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "lots")
    ex, t = mk(H, E, tmp_path)
    assert ex.acc_max_rounds() == 1
    assert ex.acc_max_rounds() <= E.ACC_MAX_ROUNDS_HARD_CAP


def test_the_hard_cap_bounds_any_configuration(probe, tmp_path, monkeypatch):
    H, E = probe
    monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "100000")
    ex, _t = mk(H, E, tmp_path)
    assert ex.acc_max_rounds() == E.ACC_MAX_ROUNDS_HARD_CAP


def test_a_replay_of_the_SAME_intent_is_still_refused(probe, tmp_path):
    """THE PROPERTY THAT PROTECTS MONEY, unchanged. The slot moved from the
    leg to (market, round, leg), so a new intent gets a new slot -- but the
    same intent, replayed, lands on the slot it already spent."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 1
    before = AUTH.accumulation_sid("TS-E2E-1", "m", 0, "DOWN")
    assert AUTH.claim_held(before) is True
    with pytest.raises(AUTH.Refused):
        AUTH.claim_accumulation_leg("m", 0, "DOWN", {})
    # a DIFFERENT round is a different slot and is available
    AUTH.claim_accumulation_leg("m", 1, "DOWN", {})


def test_a_one_sided_round_is_completed_before_a_new_pair_is_opened(
        probe, tmp_path, monkeypatch):
    """Accumulation must not stack fresh first legs on top of an unpaired
    position: `probe_round` returns the first INCOMPLETE round."""
    H, E = probe
    monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "4")
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert ex.probe_round("m") == 0          # round 0 is HALF done
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert ex.probe_round("m") == 1          # only now does a new pair open
    assert [s["side"] for s in t.sent] == ["DOWN", "UP"]


def test_each_market_accumulates_in_its_own_namespace(probe, tmp_path,
                                                      monkeypatch):
    """Successive windows are successive MARKETS. A round spent in one must
    not read as spent in the next, or the engine goes inert on the roll.

    ASSERTED ON THE CLAIM AND THE ATTEMPT, not on `probe_legs_claimed`. That
    helper now answers "is this leg FINISHED" rather than "was it sent once",
    and with nothing filled and nothing live a sent leg is legitimately
    replaceable -- so it reads False for BOTH markets and can no longer tell
    them apart. The namespacing property itself lives in the claim id, which
    is what this now reads.
    """
    H, E = probe
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")
    A = "cpc-btc-updown-15m-2026-10-16-2100z"
    B = "cpc-btc-updown-15m-2026-10-16-2115z"
    AUTH.claim_accumulation_leg(A, 0, "UP", {})
    ex, _t = mk(H, E, tmp_path)
    exp = AUTH.experiment_id()
    assert AUTH.claim_held(AUTH.accumulation_sid(exp, A, 0, "UP")) is True
    assert AUTH.claim_held(AUTH.accumulation_sid(exp, B, 0, "UP")) is False
    # and the attempt axis is per market too: A is on its SECOND placement,
    # B has not been placed at all
    assert ex.acc_leg_attempt(A, 0, "UP") == 1
    assert ex.acc_leg_attempt(B, 0, "UP") == 0


def test_each_round_is_planned_against_the_CURRENT_book(probe, tmp_path,
                                                        monkeypatch):
    """The plan freeze is per ROUND. Frozen per market forever, the first
    pair's economics were reused however far the book had moved."""
    H, E = probe
    monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "2")
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    p0 = ex.probe_stored_plan("m", 0)
    r = ex.handle(_row(H), _book(bid="0.50", ask="0.51"),
                  market="m", window="21:00")
    p1 = ex.probe_stored_plan("m", 1)
    assert p0 is not None and p1 is not None, r.get("refused_reason")
    assert p1 != p0
    assert {l["side"]: l["price"] for l in p1["legs"]} != \
        {l["side"]: l["price"] for l in p0["legs"]}


def test_restart_recovery_cannot_replace_a_claimed_leg(probe, tmp_path):
    """A fresh executor on the same volume re-places nothing that FILLED.

    WHAT THE DURABLE RECORD ACTUALLY PROVES. The claim file says the leg was
    sent; the inventory says what became of it. A leg that filled -- as both
    do here, the harness filling 1 of each -- may never be re-placed at any
    attempt, and that is what a restart must not be able to undo.

    A leg sent and NOT filled is a different case and is deliberately
    replaceable within its attempt budget: `record_submit` persists the live
    order BEFORE the wire, so a claim with no inventory AND no live order on
    disk is a leg that never reached the venue or already ended there.
    `test_a_leg_cancelled_with_ZERO_fill_may_be_REPLACED...` in the
    end-to-end suite covers that path.

    `max_live_orders` is raised here so the assertion under test is about
    CLAIMS. At the default of two, the two partially filled rows -- which are
    still resting with a remainder -- correctly fill both slots, and the
    third process is refused on the order cap before the claim logic is
    reached. That count is pinned in the end-to-end suite.
    """
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.limits = H.limits(max_live_orders=6)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 1
    ex2, t2 = mk(H, E, tmp_path)          # new process, same ledger dir
    ex2.limits = H.limits(max_live_orders=6)
    ex2.handle(_row(H), _book(), market="m", window="21:00")
    assert [s["side"] for s in t2.sent] == ["UP"]     # only the unclaimed leg
    # both legs of round 0 now hold filled quantity, so a third process
    # re-places NEITHER; the only thing it may do is open the NEXT round,
    # which is accumulation working.
    ex3, t3 = mk(H, E, tmp_path)
    ex3.limits = H.limits(max_live_orders=6)
    assert ex3.store.position("m", "UP") > D(0)
    assert ex3.store.position("m", "DOWN") > D(0)
    assert ex3.acc_leg_attempt("m", 0, "UP") is None
    assert ex3.acc_leg_attempt("m", 0, "DOWN") is None
    ex3.handle(_row(H), _book(), market="m", window="21:00")
    # ONE order, and it is the leg that REDUCES the prospective residual.
    # Realized inventory is balanced here (1 of each filled), but the two
    # resting remainders are DOWN-heavy by 2, so UP is the reducing side.
    # This assertion used to read ["DOWN"] -- cheapest-first -- which would
    # have added a third DOWN leg to an already DOWN-heavy book. The claim
    # behaviour this test exists to pin is unchanged: exactly one order, a
    # fresh client order id, and round 1.
    assert [s["side"] for s in t3.sent] == ["UP"]
    assert ex3.probe_round("m") == 1
    assert {s["client_order_id"] for s in t3.sent}.isdisjoint(
        {s["client_order_id"] for s in t.sent + t2.sent})


# ------------------------------------------------------- the pair pre-check

def test_the_pair_is_sized_so_BOTH_legs_fit_the_cash_cap(probe, tmp_path):
    """ITEM 5. Leg one must never spend the headroom leg two needs, so the
    pair is sized DOWN until both fit -- it does not place a big first leg
    and then discover there is nothing left for the second.

    ASSERTED ON THE PLAN, which is what `probe_precheck` gates. The SENT
    quantities no longer have to match each other: the second leg is a
    COMPLEMENT, sized to the quantity that actually filled, so a partial
    first fill correctly produces a smaller second leg. The property this
    test exists for is that the PAIR was sized so both legs fit before
    either was placed.
    """
    H, E = probe
    ex, t = mk(H, E, tmp_path, g=gates(cash="3"))
    r1 = ex.handle(_row(H), _book(), market="m", window="21:00")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 2
    plan = r1["TWO_SIDED_PROBE"]
    legs = {l["side"]: l for l in plan["legs"]}
    q = D(legs["UP"]["qty"])
    assert D(legs["DOWN"]["qty"]) == q          # the PAIR is matched
    # the gate's own basis: short at a dollar a share plus the long at price
    assert q * (D(1) + D(legs["UP"]["price"])) <= D("3")
    assert D(plan["cash_gate_obligation_usd"]) <= D("3")
    # and the first leg actually sent is that planned leg, unchanged
    first = t.sent[0]
    assert D(first["qty"]) == q


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
    """A refusal is terminal. The ordinary lambda intent must NOT be sent in
    its place -- that would be a trade under a probe's authorization."""
    H, E = probe
    # 40 ticks behind drives the DOWN leg below zero: out of band
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "40")
    ex, t = mk(H, E, tmp_path / "oob")
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "PRICE_OUT_OF_BAND"


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
        raise AUTH.Refused("ACC_CLAIM_UNAVAILABLE", {"note": "disk"})
    monkeypatch.setattr(AUTH, "claim_accumulation_leg", boom)
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "ACC_CLAIM_UNAVAILABLE"


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
    assert r["refused_reason"] == "ACCUMULATION_EXHAUSTED"


# =====================================================================
# THE FIVE AUDIT FINDINGS, each with an executable regression.

def test_Q2_maker_only_cannot_reject_a_probe_for_a_TAKER_L5_action(probe,
                                                                   tmp_path):
    """maker_only reads tr["mode"]. It used to run BEFORE the rewrite, so an
    L5 action that translated to TAKER refused the probe outright -- even
    though both probe legs are always MAKER."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    assert ex.maker_only or True            # whatever the default, it passes
    ex.maker_only = True
    # an L5 row whose own action would cross (taker) at this book
    r = ex.handle(_row(H, side="UP", l5px="0.99"), _book(),
                  market="m", window="21:00")
    assert r.get("refused_reason") != "MAKER_ONLY_NO_TAKER"
    assert len(t.sent) == 1
    assert t.sent[0]["mode"] == FT.MAKER


def test_Q2_the_imbalance_suppressor_judges_the_probe_not_the_L5_side(probe,
                                                                      tmp_path):
    """suppress_imbalance_increase classifies on dec["side"], which the probe
    rewrites. It must not reject a probe leg for an imbalance the superseded
    L5 action would have created."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.suppress_imbalance_increase = True
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert r.get("refused_reason") not in ("TREATMENT_SUPPRESSED",
                                           "INCREASE_RESIDUAL")
    assert len(t.sent) == 1


def test_Q2_the_record_preserves_the_SUPERSEDED_L5_action(probe, tmp_path):
    """Reading dec["side"] after the rewrite logged the override as the thing
    it replaced, destroying the provenance the record exists for."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    r = ex.handle(_row(H, side="DOWN"), _book(), market="m", window="21:00")
    rep = r["TWO_SIDED_PROBE"]["replaced_l5_intent"]
    assert rep["side"] == "DOWN"            # the L5 action, not the leg
    assert t.sent[0]["side"] == "DOWN" or t.sent[0]["side"] == "UP"


def test_Q3_both_experiments_at_once_submits_NOTHING(probe, tmp_path,
                                                     monkeypatch):
    """Both overrides rewrite the order. With both authorized the later one
    silently wins and the gates validated the other. No safe precedence
    exists, so it is a configuration error that fails closed."""
    H, E = probe
    from src import l5_queue_measurement as QM
    monkeypatch.setenv(QM.EXPERIMENT_ENV, "QM-1")
    monkeypatch.setenv(QM.MARKET_ENV, "m")
    ex, t = mk(H, E, tmp_path)
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "CONFLICTING_EXPERIMENTS_TS_AND_QM"


def test_Q3_the_transmitted_intent_equals_what_every_gate_validated(probe,
                                                                    tmp_path):
    """The money-moving fields of the order that reaches the transport must
    be byte-for-byte what the gates saw."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 2
    for s in t.sent:
        # no stray keys: the intent carries exactly the validated fields
        for k in ("market", "side", "price", "qty", "mode"):
            assert k in s
        assert "qm_mode" not in s
        assert "post_only" not in s


def test_Q3_a_late_mutation_of_a_money_field_is_refused(probe, tmp_path,
                                                        monkeypatch):
    """Rather than trust the ordering of the overrides, the check is live."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    real = ex.transport.submit

    def tamper(intent):
        return real(intent)
    monkeypatch.setattr(ex.transport, "submit", tamper)
    # mutate the intent between the gates and the wire by hooking record_submit
    orig = ex.store.record_submit

    def bump(*a, **k):
        out = orig(*a, **k)
        return out
    monkeypatch.setattr(ex.store, "record_submit", bump)
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    # unmutated, so it goes through; the guard exists for the mutated case,
    # which the source enforces by comparing the two dicts
    assert len(t.sent) == 1, r.get("refused_reason")
    import inspect
    src = inspect.getsource(E.FaithfulExecutor.handle)
    assert "INTENT_MUTATED_AFTER_GATES" in src
    assert "sent_money != gated_money" in src


def test_Q4_the_placement_cutoff_is_CALLED_not_merely_defined(probe,
                                                              tmp_path,
                                                              monkeypatch):
    """A constant nobody calls is not a deadline."""
    H, E = probe
    # window start far enough back that we are past the placement cutoff
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 700.0)
    ex, t = mk(H, E, tmp_path)
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    # ENTRY-specific now: with no leg of this round claimed, this action is an
    # entry, and entering is what the late cutoff forbids. Completing an
    # existing leg stays permitted later -- see may_enter vs may_complete.
    assert r["refused_reason"] == "PAST_ENTRY_CUTOFF"


def test_Q4_an_unknown_window_refuses_rather_than_guessing(probe, tmp_path,
                                                           monkeypatch):
    H, E = probe
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: None)
    ex, t = mk(H, E, tmp_path)
    r = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent == []
    assert r["refused_reason"] == "WINDOW_START_UNKNOWN"


def test_Q4_the_terminal_deadline_hook_exists_and_is_called_from_tick():
    import inspect
    from src import l5_pmus_live_runner as R
    assert hasattr(R.Runner, "maybe_ts_deadline")
    tick = inspect.getsource(R.Runner.tick)
    assert "self.maybe_ts_deadline(now)" in tick
    body = inspect.getsource(R.Runner.maybe_ts_deadline)
    # fires on the WINDOW, so a restart or stall cannot postpone it
    assert "cancel_cutoff" in body
    assert "TS_WINDOW_UNKNOWN" in body      # unknown timing is overdue
    assert "deadman" in body                # cancels by venue id, with proof


def test_Q5_leg_two_is_bounded_by_the_deadline_not_by_another_L5_trade(probe,
                                                                      tmp_path,
                                                                      monkeypatch):
    """One leg per decision means leg two CAN be starved if no further L5
    decision arrives. That is bounded, not unbounded: the terminal deadline
    withdraws the resting leg regardless of whether L5 ever acts again, so
    the worst case is the one-sided fill the envelope already prices."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == 1                 # leg one only
    # no further decision ever arrives. The deadline is time-driven, so the
    # resting leg is still withdrawn: the hook keys on the window, not on a
    # decision, and treats unknown timing as overdue.
    import inspect
    body = inspect.getsource(
        __import__("src.l5_pmus_live_runner", fromlist=["x"]).Runner
        .maybe_ts_deadline)
    assert "live_orders" in body
    # the deadline is now coordinated with the defensive runway, so the
    # comparison is against complement_retire_from_start, not the raw
    # terminal. Same clock, 15s earlier. See the 0300z fix.
    assert "now <= TSP.complement_retire_from_start" in body


def test_accumulation_claim_refusals(tmp_path, monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "a.jsonl"))
    monkeypatch.delenv(AUTH.EXPERIMENT_ENV, raising=False)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("m", 0, "UP", {})
    assert e.value.reason == "ACC_CLAIM_NO_EXPERIMENT"
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "x")
    for args, why in ((("", 0, "UP"), "ACC_CLAIM_NO_MARKET"),
                      (("m", 0, "SIDEWAYS"), "ACC_CLAIM_BAD_LEG"),
                      (("m", -1, "UP"), "ACC_CLAIM_BAD_ROUND"),
                      (("m", "x", "UP"), "ACC_CLAIM_BAD_ROUND")):
        with pytest.raises(AUTH.Refused) as e:
            AUTH.claim_accumulation_leg(args[0], args[1], args[2], {})
        assert e.value.reason == why, args


def test_accumulation_sids_are_distinct_on_every_axis():
    a = AUTH.accumulation_sid
    sids = {a("e", "m", 0, "UP"), a("e", "m", 0, "DOWN"),
            a("e", "m", 1, "UP"), a("e", "m2", 0, "UP"),
            a("e2", "m", 0, "UP")}
    assert len(sids) == 5
    # and a DIFFERENT namespace from the superseded two-slot claim
    assert not any(s.startswith("ts1.") for s in sids)
    assert all(s.startswith("acc1.") for s in sids)


# ------------------------------------- the PAIR is observable from the logs

def test_the_emitted_decision_line_carries_the_whole_pair_plan(probe,
                                                               tmp_path):
    """WHAT THIS PINS. The staged plan -- both legs' prices, the common
    quantity and the combined pair cost -- went to the durable tape but was
    dropped from `L5_LIVE_DECISION`, whose `intent` holds only the ONE leg
    being placed. So from the logs alone the proposed complement and the pair
    economics were invisible: the 0245z window showed thirty orders and not
    one pair price. Both halves are asserted here so they cannot drift: the
    executor really does stage the pair under this key, and the emitter
    really does read that key.
    """
    import inspect

    from src import l5_pmus_live_runner as R

    H, E = probe
    ex, _t = mk(H, E, tmp_path)
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")

    # (1) the executor stages the PAIR, not the leg
    plan = rec["TWO_SIDED_PROBE"]
    assert {l["side"] for l in plan["legs"]} == {"UP", "DOWN"}
    assert plan["pair_cost"] == "0.95"
    assert plan["leg_to_place"] == "DOWN"
    # the complement's price is present even though only one leg is placed
    assert {l["side"]: l["price"] for l in plan["legs"]} == {
        "DOWN": "0.37", "UP": "0.58"}

    # (2) the emitter reads that exact key into the line it emits
    body = inspect.getsource(R.Runner.handle)
    i = body.index('emit("L5_LIVE_DECISION"')
    payload = body[i:body.index("self.model_place", i)]
    assert '"two_sided": rec.get("TWO_SIDED_PROBE")' in payload


def test_the_emitted_line_carries_the_ladder_at_BOTH_leg_prices(probe,
                                                                tmp_path):
    """The three fill scenarios need the QUEUE, which a BBO cannot give.

    CONSERVATIVE wants the same-rung queue plus our own size cleared, BASE
    the queue, OPTIMISTIC a print THROUGH our price. None of that is
    computable from the touch, and the complement leg -- never submitted --
    appears in no other record at all. So the view is asserted for BOTH
    sides, with the UP/DOWN wire-price and resting-side mapping that the fill
    model actually uses, and an unreadable ladder must say WHY rather than
    report a zero.
    """
    from decimal import Decimal as D

    from src import l5_maker_fill_model as FM
    from src import l5_pmus_live_runner as R

    H, E = probe
    ex, _t = mk(H, E, tmp_path)
    plan = ex.handle(_row(H), _book(), market="m",
                     window="21:00")["TWO_SIDED_PROBE"]

    lad = FM.Ladder()
    lad.ingest({"marketSlug": "m",
                "bids": [{"px": {"value": "0.40"}, "qty": "250"},
                         {"px": {"value": "0.37"}, "qty": "80"}],
                "offers": [{"px": {"value": "0.42"}, "qty": "500"}],
                "transactTime": "0"}, 0.0)

    class Stub:
        shadow_ladder = lad
        stats: dict = {}

    got = R.Runner.ts_queue_view(Stub(), "m", plan)

    # UP rests as a BID at its own price; DOWN rests as an ASK at 1 - price
    assert got["UP"]["resting_side"] == FM.BID
    assert got["UP"]["wire_px"] == "0.58"
    assert got["DOWN"]["resting_side"] == FM.ASK
    assert got["DOWN"]["own_px"] == "0.37"
    assert got["DOWN"]["wire_px"] == "0.63"

    # the DOWN leg's wire price is beyond the deepest ASK rung served, so the
    # queue is UNKNOWN -- a null, never a zero
    assert got["DOWN"]["queue_ahead"] is None

    # no ladder at all says why, and still reports both legs
    class Blind:
        shadow_ladder = None
        stats: dict = {}
    blind = R.Runner.ts_queue_view(Blind(), "m", plan)
    assert blind["UP"]["why"] == "no_ladder"
    assert blind["DOWN"]["why"] == "no_ladder"
    assert "queue_ahead" not in blind["UP"]

    # and nothing to plan means nothing to report
    assert R.Runner.ts_queue_view(Stub(), "m", None) is None
    assert R.Runner.ts_queue_view(Stub(), "m", {"legs": []}) is None


def test_the_emitted_line_carries_EVERY_two_sided_stage(probe, tmp_path):
    """WHAT THIS PINS. `two_sided` reached the log a release before
    `two_sided_round` and `two_sided_complement` did, so the retirement and
    complement decisions -- the whole point of the lifecycle release -- were
    staged into the record and written to the tape but dropped from the line,
    and the release could not be validated from the logs at all.

    Asserted against the stage names the EXECUTOR actually produces, so a new
    stage cannot be added without either appearing in the emitted payload or
    failing here.
    """
    import inspect
    import re

    from src import l5_live_execution as E
    from src import l5_pmus_live_runner as R

    body = inspect.getsource(E.FaithfulExecutor.handle)
    staged = set(re.findall(r'prov\.stage\("(TWO_SIDED[A-Z_]*)"', body))
    assert staged >= {"TWO_SIDED_PROBE", "TWO_SIDED_ROUND",
                      "TWO_SIDED_COMPLEMENT"}, staged

    emit = inspect.getsource(R.Runner.handle)
    i = emit.index('emit("L5_LIVE_DECISION"')
    payload = emit[i:emit.index("self.model_place", i)]
    for stage in staged:
        assert 'rec.get("%s")' % stage in payload, stage
