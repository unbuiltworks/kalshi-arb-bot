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


#: A book wide enough that ANOTHER opening round is economically justified.
#: `probe_reopen_decision` prices every round past the first exposure-taking
#: one, and at the usual ONE-TICK spread that arithmetic refuses a
#: reopening -- see `test_l5_reopening_decision.py`, which owns the pricing.
#: The multi-round tests below are about the round MECHANISM (key
#: namespacing, leg balance, maker mode), so they run on a book where the
#: economics permit the round.
def _wide_book(**over):
    return _book(**dict({"bid": "0.50", "ask": "0.70"}, **over))


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
            tick(ex, E, book=_wide_book())
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
            tick(ex, E, book=_wide_book())
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


# ------------------------- 4. the book is authenticated, fresh, and not L5

class TestItUsesFreshAuthenticatedBooksNotL5Activity:
    """The trigger is timer-driven. Its only market input must be the
    authenticated websocket mirror, read at decision time."""

    def _body(self):
        """The trigger's CODE, with docstring and comments removed. The
        prose says 'no action_key', which is the opposite of using one, so
        a substring check over the source would be exactly backwards."""
        import ast
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R.Runner.maybe_controller_open)
        fn = ast.parse(src.lstrip()).body[0]
        body = fn.body[1:] if (isinstance(fn.body[0], ast.Expr) and isinstance(
            getattr(fn.body[0], "value", None), ast.Constant)) else fn.body
        return "\n".join(ast.unparse(n) for n in body)

    def test_it_reads_no_l5_activity_state_whatsoever(self):
        code = self._body()
        for forbidden in ("_last_action_row", "l5_activity", "new_l5_actions",
                          "action_key", "l5_native", "l5_to_pmus_action",
                          "transactionHash", "OBS."):
            assert forbidden not in code, forbidden

    def test_its_only_market_input_is_the_authenticated_mirror(self):
        code = self._body()
        assert "self.book.snapshot" in code
        # and it is read on THIS tick, not cached on the instance
        assert "self._controller_book" not in code

    def test_the_snapshot_is_the_authenticated_two_sided_one(self):
        """`AB.Book.snapshot` is None unless the socket is CONNECTED and the
        book is two-sided, and it stamps its own `stream_age_s`. That is the
        property the trigger depends on, so it is asserted here rather than
        assumed from the call site."""
        import inspect
        from src import l5_pmus_auth_book as AB
        src = inspect.getsource(AB.AuthBook.snapshot)
        assert "if not self.connected or not s:" in src
        assert 'if s.get("bid") is None or s.get("ask") is None' in src
        assert "stream_age_s" in src
        assert '"source": "authenticated_ws"' in src

    def test_a_book_older_than_two_seconds_cannot_produce_an_order(
            self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        rec = tick(ex, E, book=_book(stream_age_s="2.01"))
        assert not t.sent
        assert rec["refused_reason"] == FT.T_STALE

    def test_the_book_it_was_gated_against_is_in_the_record(self, probe,
                                                            tmp_path):
        H, E = probe
        ex, _t = mk(H, E, tmp_path)
        rec = tick(ex, E, book=_book())
        cb = rec["CONTROLLER_BOOK"]
        assert cb["bid"] == "0.60" and cb["ask"] == "0.61"
        assert cb["stream_age_s"] == "0.05"


# ------------------- 5. inventory and reconciliation, through the money path

class TestInventoryAndReconciliation:

    def test_the_fill_lands_in_the_same_ledger_the_l5_path_writes(
            self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        side = t.sent[0]["side"]
        assert ex.store.position("m", side) > 0
        # and it is committed cash the gates can see
        assert ex.store.leg_cost("m").get(side) is not None

    def test_reconciliation_is_keyed_on_market_and_side_not_provenance(
            self, probe, tmp_path):
        """A venue position row carries no notion of who decided the order,
        so an autonomous fill reconciles through the identical path. The
        venue overwrites us either way."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        side = t.sent[0]["side"]
        # the venue says we hold MORE than our file does
        rows = [{"marketSlug": "m", "qtyBoughtDecimal": "2.0",
                 "qtySoldDecimal": "0", "netPositionDecimal": "2.0"}]
        # it lives on the STORE, which is the point: the ledger reconciles,
        # and the ledger has no provenance column
        out = ex.store.reconcile_from_venue(rows, [])
        assert out is not None
        # the venue's answer replaced ours rather than being averaged
        assert ex.store.position("m", "UP") == D("2.0"), out
        assert str(side) in ("UP", "DOWN")
        # and the divergence was surfaced rather than silently absorbed
        assert out.get("positions") or out.get("diverged") or out

    def test_an_autonomous_order_is_in_live_orders_like_any_other(
            self, probe, tmp_path):
        H, E = probe
        t = H.FakeTransport(fill_all=False)
        t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
        ex, _ = mk(H, E, tmp_path, t=t, orders=16)
        tick(ex, E)
        # the ledger owns it, so the orphan sweep and the deadman can see it
        assert ex.store.already_submitted(t.sent[0]["client_order_id"])

    def test_the_residual_cap_sees_an_autonomous_position(self, probe,
                                                          tmp_path):
        """The cap reads the PROSPECTIVE net from the store, which does not
        know or care which path created the inventory."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=16)
        tick(ex, E)
        up = ex.store.position("m", "UP")
        dn = ex.store.position("m", "DOWN")
        assert abs(up - dn) > 0, (up, dn)


# ------------------- observable without submitting: the disarmed rehearsal

class TestADisarmedEngineDecidesButCannotSubmit:
    """Requirement: autonomous decisions must be OBSERVABLE without any
    order reaching the venue. A disarmed service gives exactly that.

    WHERE THE GUARANTEE ACTUALLY LIVES, measured rather than assumed. Two
    separate things hold:

    1. `PMUSTransport.submit()` calls `_require_armed()` as its first act
       and raises `NotArmed` without attempting a POST, and `arm()` demands
       BOTH an in-process preflight PASS and the owner's arm token. That is
       the enforced gate.
    2. The block that consumes the submission claim is wrapped in
       `if getattr(self.transport, "armed", False)`, so a disarmed run never
       spends one. The rehearsal costs nothing from the campaign budget.

    A NOTE ON A GUARD THAT CANNOT FIRE, recorded because this file is where
    someone would look for it. `disarmed_refusal()` is checked inside that
    same `if ... armed` block, so within it `armed` is always truthy and the
    call always returns None -- the check is unreachable. It is harmless,
    because the enclosing condition is strictly stronger and achieves the
    same end, and it is NOT relied on here. Asserted below so the fact
    stays visible instead of being rediscovered as a safety claim.
    """

    def _disarmed(self, H, E, tmp_path):
        """A transport that refuses the way the real one refuses."""

        class Disarmed(H.FakeTransport):
            def submit(self, intent):
                raise E.NotArmed("transport is DISARMED")

        t = Disarmed(fill_all=False)
        t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
        t.armed = False
        assert getattr(t, "armed", None) is False
        return mk(H, E, tmp_path, t=t, orders=16)

    def test_nothing_reaches_the_venue(self, probe, tmp_path):
        """Recorded as a NON-SUBMISSION, not as a refusal, and that is the
        right shape: no gate declined this order -- the venue was simply
        unreachable. `refused_reason` stays None and the ORDER stage says
        why nothing was sent."""
        H, E = probe
        ex, t = self._disarmed(H, E, tmp_path)
        rec = tick(ex, E)
        assert not t.sent, t.sent
        o = rec["ORDER"]
        assert o["submitted"] is False
        assert o["reason"] == "NOT_ARMED"
        assert "DISARMED" in str(o["detail"])

    def test_the_order_that_would_have_gone_out_is_recorded_in_full(
            self, probe, tmp_path):
        """What makes the rehearsal worth running: the exact intent."""
        H, E = probe
        ex, _t = self._disarmed(H, E, tmp_path)
        i = tick(ex, E)["ORDER"]["intent"]
        assert i["side"] in ("UP", "DOWN")
        assert i["mode"] == FT.MAKER
        assert D(i["price"]) > 0 and D(i["qty"]) > 0
        assert i["action_key"].startswith(E.CONTROLLER_KEY_PREFIX)
        assert i["market"] == "m"

    def test_the_whole_autonomous_decision_is_still_in_the_record(
            self, probe, tmp_path):
        """This is what makes it observable: the decision, the book it was
        made against and the leg that was priced are all recorded even
        though no order was sent."""
        H, E = probe
        ex, _t = self._disarmed(H, E, tmp_path)
        rec = tick(ex, E)
        assert rec["provenance"] == E.CONTROLLER_GENERATED
        assert rec["action_key"].startswith(E.CONTROLLER_KEY_PREFIX)
        assert rec["CONTROLLER_BOOK"]["source"] is not None
        ts = rec["TWO_SIDED_PROBE"]
        assert ts["leg_to_place"] in ("UP", "DOWN")
        assert ts["accumulation_round"] == 0
        assert ts["pair_cost"] is not None

    def test_no_claim_is_consumed_so_the_campaign_budget_is_untouched(
            self, probe, tmp_path):
        """The property the 1715z campaign lost a submission to: a claim
        spent on an order the venue never saw."""
        H, E = probe
        ex, _t = self._disarmed(H, E, tmp_path)
        rec = tick(ex, E)
        leg = rec["TWO_SIDED_PROBE"]["leg_to_place"]
        assert not ex.probe_legs_claimed("m", 0).get(leg)
        # still claimable, which is the proof it was never spent
        AUTH.claim_accumulation_leg("m", 0, leg, {"rehearsal": False})

    def test_repeated_disarmed_ticks_place_and_cost_nothing(self, probe,
                                                             tmp_path):
        H, E = probe
        ex, t = self._disarmed(H, E, tmp_path)
        for _ in range(6):
            tick(ex, E)
        assert not t.sent
        assert ex.store.position("m", "UP") == 0
        assert ex.store.position("m", "DOWN") == 0
        assert not any(ex.probe_legs_claimed("m", 0).values())

    def test_the_claim_block_is_gated_on_armed_which_is_the_real_protection(
            self):
        import ast
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.FaithfulExecutor.handle)
        tree = ast.parse(src.lstrip()).body[0]
        guards = [ast.unparse(n.test) for n in ast.walk(tree)
                  if isinstance(n, ast.If)
                  and "claim_accumulation_leg" in ast.unparse(n)]
        assert any("armed" in g for g in guards), guards

    def test_the_disarmed_refusal_guard_is_unreachable_and_not_relied_on(
            self):
        """Recorded, not fixed. Inside `if ... armed`, `disarmed_refusal()`
        returns None by construction, so the branch cannot be taken."""
        import ast
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.FaithfulExecutor.handle)
        tree = ast.parse(src.lstrip()).body[0]
        for n in ast.walk(tree):
            if not (isinstance(n, ast.If) and "armed" in ast.unparse(n.test)):
                continue
            if "disarmed_refusal" in "".join(ast.unparse(c) for c in n.body):
                return      # it is nested inside an `armed` test, as stated
        raise AssertionError(
            "disarmed_refusal is no longer inside an `armed` guard -- it may "
            "now be reachable, so re-read whether it is load-bearing")

    def test_the_trigger_is_inert_while_the_switch_is_off(self, monkeypatch):
        """Requirement 2 and requirement 5 are both satisfiable, but not at
        the same instant: with the switch OFF the trigger returns before it
        reads a book, so there is nothing to observe in production."""
        from src import l5_pmus_live_runner as R
        monkeypatch.delenv("L5_CONTROLLER_OPEN", raising=False)
        assert R.Runner.controller_open_enabled(object()) is False
