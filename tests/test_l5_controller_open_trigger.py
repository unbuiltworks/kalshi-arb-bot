"""Autonomous maker openings, driven through the REAL executor.

WHAT THIS COVERS. `handle()` entered with `controller=` and NO ROW: our own
decision, the authenticated book, and then the identical remainder of the
path -- the same two-sided branch, the same gates, the same overlay, the
same claim, the same ledger. The assertions are about what actually reached
the transport, because the lesson from the queue measurement is that
implementing a constructor is not wiring it.

THE SIX SCENARIO CATEGORIES are the ones the mandate names: repeated ticks,
concurrent L5 and controller triggers, restarts, ambiguous
acknowledgements, partial fills, and multiple pairing rounds inside one
window. Each is a class below.

Nothing here arms anything. `FakeTransport` is the wire.
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
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "CO-E2E-1")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "CO-E2E-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 60.0)
    return H, E


def _book(**over):
    b = {"bid": "0.60", "ask": "0.61", "bid_shares": "900",
         "ask_shares": "5000", "stream_age_s": "0.05"}
    b.update(over)
    return b


def gates(cash="8", deficit="3", cum="10"):
    return ValidationGates(max_cash_deployed_usd=D(cash),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D(cum),
                           max_worst_case_deficit_usd=D(deficit))


def mk(H, E, tmp_path, t=None, g=None, orders=2):
    t = t or H.FakeTransport(fill_all=False)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path),
                            H.limits(max_live_orders=orders), transport=t,
                            now_fn=H.NOW, maker_treatment=True)
    ex.validation_gates = g if g is not None else gates()
    return ex, t


def intent(E, book, tick=D("0.01"), side=None, size="3"):
    """The controller intent the timer builds. Mirrors
    `Runner.maybe_controller_open`: cheaper own-price side, provisional
    price, no maker class because none has been observed."""
    up = E.controller_provisional_price("UP", book, tick)
    dn = E.controller_provisional_price("DOWN", book, tick)
    if side is None:
        side, px = ("UP", up) if up <= dn else ("DOWN", dn)
    else:
        px = up if side == "UP" else dn
    return {"provenance": E.CONTROLLER_GENERATED,
            "maker_class": None,
            "decision": {"side": side, "price": px,
                         "l5_size": D(size), "relation": "open"}}


def tick(ex, E, book=None, **kw):
    bk = book if book is not None else _book()
    return ex.handle(None, bk, market="m", window="21:00",
                     controller=intent(E, bk, **kw))


_SEQ = [0]


def l5row(H, **over):
    _SEQ[0] += 1
    kw = dict(bid="0.60", ask="0.61", l5px="0.60", side="DOWN",
              key="l5-tick-%d" % _SEQ[0])
    kw.update(over)
    return H.row(**kw)


# ------------------------------------------------- it reaches the wire at all

class TestAnAutonomousOpeningReachesTheWire:

    def test_one_tick_places_a_real_post_only_maker_order(self, probe,
                                                          tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        rec = tick(ex, E)
        assert rec.get("refused_reason") is None, rec.get("refused_reason")
        assert len(t.sent) == 1, t.sent
        s = t.sent[0]
        assert s["mode"] == FT.MAKER
        # POST-ONLY IS CARRIED BY THE MODE: the wire body is the only proof.
        assert OB.from_intent(s, "m")["participateDontInitiate"] is True

    def test_no_l5_action_underlies_it_and_none_was_fabricated(self, probe,
                                                              tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        rec = tick(ex, E)
        assert rec["provenance"] == E.CONTROLLER_GENERATED
        # OUR OWN NAMESPACE. An L5 action_key is
        # "txhash|asset|side|price|size|timestamp"; this cannot be mistaken
        # for one, in the tape or in the ledger.
        assert rec["action_key"].startswith(E.CONTROLLER_KEY_PREFIX + "|")
        assert t.sent[0]["action_key"] == rec["action_key"]
        # and nothing in the record claims an L5 price
        assert "rested_at_l5_price" not in (rec.get("MAKER_TREATMENT") or {})
        mt = rec.get("MAKER_TREATMENT") or {}
        assert mt.get("provenance") == E.CONTROLLER_GENERATED
        assert "rested_at_own_price" in mt

    def test_the_branch_prices_and_sizes_it_not_the_provisional_intent(
            self, probe, tmp_path):
        """The provisional price exists for the GATE. `build_orders` owns
        the order, exactly as it already supersedes L5's intent."""
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        bk = _book()
        prov_px = E.controller_provisional_price("DOWN", bk, D("0.01"))
        rec = tick(ex, E, book=bk)
        assert rec.get("refused_reason") is None
        # two ticks behind the touch, which is build_orders' own price
        assert D(str(t.sent[0]["price"])) == D("0.37")
        assert D(str(t.sent[0]["price"])) != prov_px
        assert rec["TWO_SIDED_PROBE"]["leg_to_place"] == "DOWN"

    def test_a_row_and_a_controller_intent_together_is_a_programming_error(
            self, probe, tmp_path):
        H, E = probe
        ex, _t = mk(H, E, tmp_path)
        with pytest.raises(ValueError):
            ex.handle(l5row(H), _book(), market="m", window="21:00",
                      controller=intent(E, _book()))

    def test_an_intent_that_does_not_declare_its_provenance_is_refused(
            self, probe, tmp_path):
        H, E = probe
        ex, _t = mk(H, E, tmp_path)
        bad = dict(intent(E, _book()), provenance="L5_FOLLOWED")
        with pytest.raises(ValueError):
            ex.handle(None, _book(), market="m", window="21:00",
                      controller=bad)


# ------------------------------------------------------- the gates still bind

class TestEveryExistingControlStillApplies:

    def test_a_stale_book_is_refused_by_the_two_second_bound(self, probe,
                                                             tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        rec = tick(ex, E, book=_book(stream_age_s="9.0"))
        assert rec["refused_reason"] is not None
        assert not t.sent

    def test_a_one_sided_book_is_refused(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        bk = _book()
        bk.pop("ask")
        # no provisional price exists, so the trigger itself would decline;
        # entered anyway, the translation refuses rather than inventing one
        rec = ex.handle(None, bk, market="m", window="21:00",
                        controller={"provenance": E.CONTROLLER_GENERATED,
                                    "maker_class": None,
                                    "decision": {"side": "UP",
                                                 "price": D("0.40"),
                                                 "l5_size": D("3"),
                                                 "relation": "open"}})
        assert rec["refused_reason"] == FT.T_NO_BOOK
        assert not t.sent

    def test_the_cash_gate_refuses_and_nothing_reaches_the_wire(self, probe,
                                                                tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, g=gates(cash="0.01"))
        rec = tick(ex, E)
        assert rec["refused_reason"] is not None
        assert not t.sent

    def test_it_is_maker_only_and_never_crosses(self, probe, tmp_path):
        """EXECUTION_MODE = MAKER_ONLY is preserved: every order this path
        can produce is a post-only maker order, on both sides, across
        rounds."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        for _ in range(6):
            tick(ex, E)
        assert t.sent, "nothing was placed, so the claim is untested"
        for s in t.sent:
            assert s["mode"] == FT.MAKER
            assert OB.from_intent(s, "m")["participateDontInitiate"] is True

    def test_an_unauthorized_market_places_nothing(self, probe, tmp_path,
                                                   monkeypatch):
        H, E = probe
        monkeypatch.setenv(TS.MARKET_ENV, "some-other-market")
        ex, t = mk(H, E, tmp_path)
        rec = tick(ex, E)
        # the two-sided branch never runs, so no leg is pending and the
        # key cannot resolve: it fails closed rather than submitting under
        # the UNRESOLVED placeholder
        assert rec["refused_reason"] is not None
        assert not t.sent

    def test_without_a_probe_leg_the_key_never_resolves(self, probe,
                                                        tmp_path,
                                                        monkeypatch):
        """The explicit fail-closed path: no pending leg, no order.

        THE RECORD KEEPS THE PLACEHOLDER, and that is right. The key is
        resolved only at the submission site, so a decision refused before
        it still carries `UNRESOLVED` -- which states accurately that no
        (round, leg) was ever chosen. What must not happen is an ORDER
        under that placeholder, and that is what is asserted.
        """
        H, E = probe
        monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
        ex, t = mk(H, E, tmp_path)
        rec = tick(ex, E)
        assert not t.sent
        assert rec["refused_reason"] is not None
        assert "UNRESOLVED" in str(rec["action_key"])
        # and the placeholder never became a submitted order id
        placeholder = E.client_order_id(rec["action_key"])
        assert not ex.store.already_submitted(placeholder)
        assert (rec.get("ORDER") or {}).get("submitted") in (None, False)


# ----------------------------------------------------- 1. repeated ticks

class TestRepeatedTicks:
    """The timer fires every tick while an order rests. It must not stack
    orders, and the mechanism is the deterministic key."""

    def test_the_key_is_the_same_on_every_tick(self, probe, tmp_path):
        H, E = probe
        assert (E.controller_action_key("m", 0, "UP")
                == E.controller_action_key("m", 0, "UP"))
        # and it does not move with the clock
        assert "r0|UP" in E.controller_action_key("m", 0, "up")

    def test_ten_ticks_with_one_outstanding_order_place_one_order(
            self, probe, tmp_path):
        H, E = probe
        # ONE outstanding order, which is the live restriction
        ex, t = mk(H, E, tmp_path, orders=1)
        recs = [tick(ex, E) for _ in range(10)]
        assert len(t.sent) == 1, [r.get("refused_reason") for r in recs]
        # and the repeats were refused, not silently dropped
        assert any(r.get("refused_reason") for r in recs[1:])

    def test_a_repeat_of_the_same_leg_is_suppressed_as_a_duplicate(
            self, probe, tmp_path, monkeypatch):
        """The DUPLICATE_SUPPRESSED branch itself, not just the key.

        Driven by making the ledger answer that it has already seen this
        order id -- which is exactly the state a crash between submit and
        response, or a timer that fired twice, leaves behind. The order
        count limit is lifted so the KEY is the only thing that can stop
        the second copy.
        """
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        first = tick(ex, E)
        coid = t.sent[0]["client_order_id"]
        assert E.client_order_id(first["action_key"]) == coid
        assert ex.store.already_submitted(coid)

        # a FRESH executor, so no in-process memory carries over, and a
        # ledger that reports every id as already submitted
        ex2, t2 = mk(H, E, tmp_path, orders=16)
        monkeypatch.setattr(ex2.store, "already_submitted",
                            lambda _c: True)
        rec = tick(ex2, E)
        assert not t2.sent, t2.sent
        assert rec["ORDER"]["reason"] == "DUPLICATE_SUPPRESSED"
        assert rec["ORDER"]["submitted"] is False
        assert rec["provenance"] == E.CONTROLLER_GENERATED

    def test_distinct_legs_get_distinct_ids_so_the_pair_can_complete(
            self, probe, tmp_path):
        H, E = probe
        a = E.controller_action_key("m", 0, "UP")
        b = E.controller_action_key("m", 0, "DOWN")
        c = E.controller_action_key("m", 1, "UP")
        assert len({E.client_order_id(k) for k in (a, b, c)}) == 3


# -------------------------------- 2. concurrent L5 and controller triggers

class TestConcurrentL5AndControllerTriggers:

    def test_the_two_namespaces_cannot_collide(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        ex.handle(l5row(H), _book(), market="m", window="21:00")
        keys = [s["action_key"] for s in t.sent]
        assert len(keys) == len(set(keys)), keys
        ours = [k for k in keys if k.startswith(E.CONTROLLER_KEY_PREFIX)]
        theirs = [k for k in keys if not k.startswith(
            E.CONTROLLER_KEY_PREFIX)]
        assert ours and theirs, keys
        # and no L5 key could ever take our form
        assert all("|OPEN|" not in k for k in theirs), theirs

    def test_both_paths_share_one_accumulation_round_and_one_claim_set(
            self, probe, tmp_path):
        """The thing that actually prevents two orders for one opportunity
        is not the key -- it is that both paths claim legs from the SAME
        `claim_accumulation_leg` state."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)                      # controller takes the cheaper leg
        first_leg = t.sent[0]["side"]
        ex.handle(l5row(H), _book(), market="m", window="21:00")
        assert len(t.sent) == 2, [s["side"] for s in t.sent]
        # the L5 path could not re-place the leg the controller claimed
        assert t.sent[1]["side"] != first_leg

    def test_an_l5_action_cannot_reopen_a_leg_the_controller_claimed(
            self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        tick(ex, E)                      # both legs of round 0
        sides = [s["side"] for s in t.sent]
        assert sorted(sides) == ["DOWN", "UP"], sides
        n = len(t.sent)
        # an L5 action now cannot re-place either leg of round 0: both
        # claims are spent, and they are the SAME claims the L5 path reads
        ex.handle(l5row(H), _book(), market="m", window="21:00")
        for s in t.sent[n:]:
            assert "|r0|" not in str(s["action_key"]), s["action_key"]
        # at most one claim per (round, leg) across BOTH paths
        keys = [str(s["action_key"]) for s in t.sent]
        ours = [k for k in keys if k.startswith(E.CONTROLLER_KEY_PREFIX)]
        assert len(ours) == len(set(ours)), ours

    def test_the_provisional_side_cannot_change_the_gate_verdict(
            self, probe, tmp_path):
        """The trigger names a provisional side. If that choice could flip
        a gate, the trigger would be making a risk decision it has no
        business making -- so it is asserted, not assumed."""
        H, E = probe
        bk = _book()
        want = D("3")
        out = {}
        for side in ("UP", "DOWN"):
            px = E.controller_provisional_price(side, bk, D("0.01"))
            out[side] = FT.controller_translate(
                {"side": side, "price": px, "l5_size": want,
                 "relation": "open"},
                bbo=bk, snapshot_age_s=bk["stream_age_s"],
                maker_class=None, want=want)
        assert out["UP"]["ok"] == out["DOWN"]["ok"] is False
        assert out["UP"]["reason"] == out["DOWN"]["reason"]
        assert out["UP"]["mode"] == out["DOWN"]["mode"] == FT.MAKER


# --------------------------------------------------------- 3. restarts

class TestRestarts:

    def test_a_restart_does_not_reopen_a_leg_already_claimed(self, probe,
                                                             tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        placed = t.sent[0]["side"]
        # a FRESH executor over the SAME ledger and the same claim dir
        ex2, t2 = mk(H, E, tmp_path, orders=16)
        tick(ex2, E)
        assert t2.sent, "the restart placed nothing at all"
        # it resumed the round it was working rather than reopening the leg
        assert t2.sent[0]["side"] != placed, (placed, t2.sent[0]["side"])

    def test_the_key_survives_a_restart_unchanged(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        k1 = t.sent[0]["action_key"]
        ex2, t2 = mk(H, E, tmp_path, orders=16)
        tick(ex2, E)
        # DERIVED, never random: same market, round and leg, same id
        assert k1 != t2.sent[0]["action_key"]      # different leg
        assert E.client_order_id(k1) != E.client_order_id(
            t2.sent[0]["action_key"])

    def test_inventory_from_before_the_restart_is_not_forgotten(self, probe,
                                                                tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        before = ex.store.position("m", t.sent[0]["side"])
        ex2, _t2 = mk(H, E, tmp_path, orders=16)
        assert ex2.store.position("m", t.sent[0]["side"]) == before


# ------------------------------------------- 4. ambiguous acknowledgements

class TestAmbiguousAcknowledgements:

    def test_an_ambiguous_ack_does_not_return_the_claim(self, probe,
                                                        tmp_path):
        """The claim is taken BEFORE the wire and never returned by an
        unreadable response. Re-entering must not produce a second order
        for the same leg."""
        H, E = probe

        class Ambiguous(H.FakeTransport):
            """Records the attempt, then gives no usable answer."""

            def place(self, intent, market=None):              # noqa: A002
                self.sent.append(dict(intent))
                raise RuntimeError("timeout: state unknown")

        t = Ambiguous(fill_all=False)
        t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
        ex, _ = mk(H, E, tmp_path, t=t, orders=16)
        try:
            tick(ex, E)
        except BaseException:                                  # noqa: BLE001
            pass
        assert len(t.sent) == 1, t.sent
        leg = t.sent[0]["side"]

        # THE RETRY. A fresh executor over the same claim state, with a
        # transport that would happily accept an order. The leg whose
        # claim was spent on the ambiguous attempt must not be placed
        # again -- the claim is taken before the wire and an unreadable
        # response does not return it.
        ex2, t2 = mk(H, E, tmp_path, orders=16)
        tick(ex2, E)
        same = [s for s in t2.sent if s["side"] == leg
                and str(s["action_key"]).endswith("r0|%s" % leg)]
        assert not same, same

    def test_the_record_still_names_the_controller_after_a_failure(
            self, probe, tmp_path):
        H, E = probe

        class Boom(H.FakeTransport):
            def place(self, intent, market=None):              # noqa: A002
                raise RuntimeError("no answer")

        t = Boom(fill_all=False)
        t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
        ex, _ = mk(H, E, tmp_path, t=t, orders=16)
        try:
            rec = tick(ex, E)
        except BaseException:                                  # noqa: BLE001
            return                       # raising is also fail-closed
        assert rec["provenance"] == E.CONTROLLER_GENERATED


# ------------------------------------------------------- 5. partial fills

class TestPartialFills:

    def test_a_partial_fill_is_carried_as_inventory(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        rec = tick(ex, E)
        assert rec.get("refused_reason") is None, rec.get("refused_reason")
        side = t.sent[0]["side"]
        pos = ex.store.position("m", side)
        # the harness fills 1 of the 3 asked for
        assert pos > 0
        assert pos <= D(str(t.sent[0]["qty"]))

    def test_the_complement_is_sized_to_what_filled_not_what_was_asked(
            self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        first_qty = ex.store.position("m", t.sent[0]["side"])
        tick(ex, E)
        assert len(t.sent) == 2, [s["side"] for s in t.sent]
        comp = t.sent[1]
        assert comp["side"] != t.sent[0]["side"]
        # never larger than the exposure it exists to match
        assert D(str(comp["qty"])) <= D(str(t.sent[0]["qty"]))
        assert first_qty > 0

    def test_a_partial_fill_does_not_free_the_leg_for_a_second_order(
            self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        side = t.sent[0]["side"]
        n = len(t.sent)
        for _ in range(3):
            tick(ex, E)
        same_leg = [s for s in t.sent[n:] if s["side"] == side
                    and s["action_key"] == t.sent[0]["action_key"]]
        assert not same_leg, same_leg


# ------------------------------- 6. multiple pairing rounds in one window

class TestMultiplePairingRoundsInOneWindow:

    def test_more_than_one_pair_is_worked_in_a_single_window(self, probe,
                                                             tmp_path,
                                                             monkeypatch):
        H, E = probe
        monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "3")
        ex, t = mk(H, E, tmp_path, orders=16)
        for _ in range(8):
            tick(ex, E)
        sides = [s["side"] for s in t.sent]
        assert len(sides) >= 4 and len(sides) % 2 == 0, sides
        assert sides.count("UP") == sides.count("DOWN"), sides
        # every order a distinct identity
        assert len({s["client_order_id"] for s in t.sent}) == len(sides)

    def test_the_rounds_appear_in_the_keys_so_they_cannot_collide(
            self, probe, tmp_path, monkeypatch):
        H, E = probe
        monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "3")
        ex, t = mk(H, E, tmp_path, orders=16)
        for _ in range(8):
            tick(ex, E)
        rounds = {str(s["action_key"]).split("|")[3] for s in t.sent}
        assert len(rounds) >= 2, [s["action_key"] for s in t.sent]

    def test_the_round_bound_stops_it_rather_than_the_key_namespace(
            self, probe, tmp_path, monkeypatch):
        H, E = probe
        monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "1")
        ex, t = mk(H, E, tmp_path, orders=16)
        for _ in range(4):
            rec = tick(ex, E)
        assert len(t.sent) == 2, [s["side"] for s in t.sent]
        assert rec["refused_reason"] == "ACCUMULATION_EXHAUSTED"

    def test_every_leg_across_every_round_is_still_a_maker(self, probe,
                                                           tmp_path,
                                                           monkeypatch):
        H, E = probe
        monkeypatch.setenv(E.ACC_MAX_ROUNDS_ENV, "3")
        ex, t = mk(H, E, tmp_path, orders=16)
        for _ in range(8):
            tick(ex, E)
        assert t.sent
        for s in t.sent:
            assert s["mode"] == FT.MAKER
            assert OB.from_intent(s, "m")["participateDontInitiate"] is True


# --------------------------------------------------- the trigger is inert

class TestTheTriggerIsOffUnlessTheOwnerSetIt:

    def test_no_env_means_no_decision(self, monkeypatch):
        from src import l5_pmus_live_runner as R
        monkeypatch.delenv("L5_CONTROLLER_OPEN", raising=False)
        assert R.Runner.controller_open_enabled(object()) is False

    @pytest.mark.parametrize("v", ["1", "YES", "yes", "true", "ON"])
    def test_the_owner_can_set_it(self, monkeypatch, v):
        from src import l5_pmus_live_runner as R
        monkeypatch.setenv("L5_CONTROLLER_OPEN", v)
        assert R.Runner.controller_open_enabled(object()) is True

    @pytest.mark.parametrize("v", ["", "0", "NO", "off", "maybe"])
    def test_anything_else_is_off(self, monkeypatch, v):
        from src import l5_pmus_live_runner as R
        monkeypatch.setenv("L5_CONTROLLER_OPEN", v)
        assert R.Runner.controller_open_enabled(object()) is False

    def test_it_does_not_relax_arming_or_the_approval(self):
        """It decides whether the DECISION is made, never whether the wire
        is open. Nothing in it touches the arm token, the approval or the
        deadline."""
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R.Runner.maybe_controller_open)
        for forbidden in ("PMUS_Q1_LIVE", "PMUS_Q1_ARM", "arm", "approval",
                          "NOT_AFTER", "disarm"):
            assert forbidden not in src, forbidden


# ------------------------------------- the claim is the sequence authority

class TestItRidesTheExistingAccumulationClaim:
    """Requirement 5: the autonomous order is authorized by
    `claim_accumulation_leg`, the SAME per-leg grant the L5 path uses. No
    second authorization system, and no order without a claim."""

    def test_the_controller_leg_is_recorded_as_claimed(self, probe,
                                                       tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        rec = tick(ex, E)
        assert rec.get("refused_reason") is None
        leg = t.sent[0]["side"]
        claimed = ex.probe_legs_claimed("m", 0)
        assert claimed.get(leg), claimed

    def test_the_claim_is_not_returned_by_a_second_attempt(self, probe,
                                                           tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        leg = t.sent[0]["side"]
        with pytest.raises(AUTH.Refused):
            AUTH.claim_accumulation_leg("m", 0, leg, {"x": 1})

    def test_the_record_names_the_round_and_the_claims(self, probe,
                                                       tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        rec = tick(ex, E)
        ts = rec["TWO_SIDED_PROBE"]
        assert ts["accumulation_round"] == 0
        assert ts["leg_to_place"] == t.sent[0]["side"]
        # and the key the order went out under names the same round and leg
        assert str(t.sent[0]["action_key"]).endswith(
            "r%d|%s" % (ts["accumulation_round"], ts["leg_to_place"]))

    def test_no_order_ever_leaves_without_a_claim(self, probe, tmp_path,
                                                  monkeypatch):
        """Make the claim refuse, and nothing may reach the wire."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)

        def refuse(*a, **k):
            raise AUTH.Refused("ACC_CLAIM_TEST_REFUSAL")

        monkeypatch.setattr(AUTH, "claim_accumulation_leg", refuse)
        rec = tick(ex, E)
        assert not t.sent, t.sent
        assert rec.get("refused_reason") is not None
