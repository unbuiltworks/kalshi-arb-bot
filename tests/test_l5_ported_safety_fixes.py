"""The three safety fixes, on the DEPLOYED lineage.

Ported from `claude/l5-shadow-mode-deployment-epkz1i`, where they were
developed and verified, onto `claude/gemini-btc-economic-viability-h3u7zl`,
which is what `l5-pmus-live` actually runs. This lineage's own exclusivity
and authorization machinery -- `authorize_arming`, `HALT_GATE_ABSENT`,
`claim_once` -- is preserved untouched and is NOT duplicated here: no
SubmitterLease, no second boundary.

Each fix was confirmed to be genuinely absent from this lineage before being
ported, and each test below fails against `57d02628`.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_halt_gate as HG
from src import l5_live_execution as E
from src import l5_live_validation_gates as VG

NOW_DT = dt.datetime(2026, 10, 14, 14, 0, 0, tzinfo=dt.timezone.utc)
NOW = lambda: NOW_DT
MARKET = "m"
SESSION = "s1"


def limits(**kw):
    base = dict(max_usd_per_market=D("100"), max_abs_residual=D("100"),
                max_total_exposure_usd=D("100"), max_concurrent_markets=5,
                daily_realized_loss_halt_usd=D("100"))
    base.update(kw)
    return E.SafetyLimits(**base)


def row(key="a", side="UP", l5px="0.65", size=10.0):
    return {
        "action_key": key, "kind": "l5_action", "backlog": False,
        "l5_event_utc": NOW_DT.isoformat(), "receipt_utc": NOW_DT.isoformat(),
        "window_start_utc": NOW_DT.isoformat(), "pmus_snapshot_age_s": 0.5,
        "l5": {"pmus_side": side, "l5_price": l5px, "l5_size": size},
        "pmus": {"bbo": {"bid": "0.40", "ask": "0.60",
                         "bid_shares": "500", "ask_shares": "500"}},
        "copyability": {"l5_price": l5px},
    }


def rest(store, coid, oid, *, px="0.45", qty="2", side="UP"):
    store.record_submit(coid, {"market": MARKET, "side": side,
                               "price": D(px), "qty": D(qty)},
                        session_id=SESSION, mode="MAKER")
    store.state["live_orders"][coid]["venue_order_id"] = oid
    store.save()
    return store


def venue_row(coid, oid, px="0.45", q="2", manual=False):
    return {"id": oid, "clientOrderId": coid, "marketSlug": MARKET,
            "outcomeSide": "OUTCOME_SIDE_YES", "state": "ORDER_STATE_OPEN",
            "price": {"value": px, "currency": "USD"},
            "quantity": q, "leavesQuantity": q, "remainingQuantity": q,
            "manualOrderIndicator": ("MANUAL_ORDER_INDICATOR_MANUAL" if manual
                                     else "MANUAL_ORDER_INDICATOR_AUTOMATIC")}


# ============================ FIX 1 — aggregate, account-wide cash cap

class TestTheCashCapIsAggregate:

    def gates(self):
        return VG.ValidationGates(max_cash_deployed_usd=D("1"))

    def test_a_series_of_clips_cannot_each_spend_the_dollar(self):
        """THE DEFECT. `vg.refuse` was called with neither `filled_legs` nor
        `outstanding`, both defaulting to `()`, so the committed figure was
        the proposed order's notional alone. Six $0.45 clips all passed."""
        gt = self.gates()
        outstanding = []
        verdicts = []
        for _ in range(4):
            r = gt.refuse(order_qty=D(1), order_price=D("0.45"),
                          order_intent="ORDER_INTENT_BUY_LONG",
                          outstanding=list(outstanding))
            verdicts.append(r)
            if r is None:
                outstanding.append({"qty": D(1), "price": D("0.45"),
                                    "intent": "ORDER_INTENT_BUY_LONG"})
        assert verdicts[0] is None and verdicts[1] is None
        assert verdicts[2] == "CASH_COMMITMENT:1.35>1"

    def test_the_executor_passes_the_book_to_the_gate(self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        ex.validation_gates = self.gates()
        assert ex.outstanding_for_gates() == [
            {"coid": "c1", "qty": D("2"), "price": D("0.45"),
             "intent": "ORDER_INTENT_BUY_LONG"}]
        rec = ex.handle(row(), {}, market=MARKET, window="16:15")
        assert rec["refused_by_layer"] == "VALIDATION_GATES"
        assert rec["refused_reason"].startswith("CASH_COMMITMENT:")
        # THE PROPERTY, not a hardcoded total: the figure the gate refused on
        # EXCEEDS the proposed order's own notional by the $0.90 already
        # resting. Before the port it would have been the order alone.
        total = D(rec["refused_reason"].split(":")[1].split(">")[0])
        proposed = D(str(rec["PMUS_TRANSLATION"]["price"])) * D(
            str(rec["PMUS_TRANSLATION"]["qty"]))
        assert total - proposed == D("0.90"), (total, proposed)

    def test_filled_notional_counts_as_unreleased_cash(self, tmp_path):
        st = E.InventoryStore(str(tmp_path / "inv.json"))
        st.state["committed"][MARKET] = "0.80"
        st.save()
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        assert ex.cash_legs_for_gates() == [
            {"market": MARKET, "cash_out": D("0.80"), "settled": False,
             "closing": False}]
        assert self.gates().refuse(
            order_qty=D(1), order_price=D("0.45"),
            order_intent="ORDER_INTENT_BUY_LONG",
            filled_legs=ex.cash_legs_for_gates()
        ) == "CASH_COMMITMENT:1.25>1"

    def test_it_binds_across_a_process_restart(self, tmp_path):
        inv = tmp_path / "inv.json"
        rest(E.InventoryStore(str(inv)), "c1", "v1")
        ex = E.FaithfulExecutor(E.InventoryStore(str(inv)), limits(),
                                now_fn=NOW)
        ex.validation_gates = self.gates()
        assert ex.validation_gates.refuse(
            order_qty=D(1), order_price=D("0.45"),
            order_intent="ORDER_INTENT_BUY_LONG",
            filled_legs=ex.cash_legs_for_gates(),
            outstanding=ex.outstanding_for_gates()
        ) == "CASH_COMMITMENT:1.35>1"

    def test_an_unreadable_price_is_charged_at_worst_case(self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        st.state["live_orders"]["c1"]["price"] = "not-a-price"
        st.save()
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        assert ex.outstanding_for_gates()[0]["price"] == (
            E.UNKNOWN_PRICE_WORST_CASE)

    def test_a_resting_short_is_charged_a_dollar_a_share(self):
        assert self.gates().pending_obligation(
            [{"qty": D(2), "price": D("0.05"),
              "intent": "ORDER_INTENT_BUY_SHORT"}]) == D(2)

    # ---- account-wide: the shared account ----------------------------

    def test_exposure_adopted_from_the_venue_counts(self, tmp_path):
        """The account is SHARED and the venue answers for all of it.
        `reconcile_from_venue` adopts what it reports, and the dollar counts
        it -- which makes the gate refuse EARLIER. Excluding it would let an
        automated dollar sit on top of exposure we did not create, against a
        venue that NETS the two outcome tokens of a market."""
        st = E.InventoryStore(str(tmp_path / "inv.json"))
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        ex.validation_gates = self.gates()
        st.reconcile_from_venue(
            [], [venue_row("owner-app-1", "v-manual", "0.90", "5",
                           manual=True)])
        assert ex.validation_gates.refuse(
            order_qty=D(1), order_price=D("0.45"),
            order_intent="ORDER_INTENT_BUY_LONG",
            filled_legs=ex.cash_legs_for_gates(),
            outstanding=ex.outstanding_for_gates()
        ) == "CASH_COMMITMENT:4.95>1"

    # ---- fail closed on an unconfirmed book --------------------------

    def test_an_uncertain_book_refuses_before_any_cash_figure(self):
        """Every number this gate reads comes out of the ledger, so a ledger
        the venue has not confirmed makes all of them unauthoritative. The
        deployed ORDER_STATE_UNCERTAIN refusal is one layer DOWN, after this
        gate has already computed and passed a total from the same untrusted
        book."""
        r = self.gates().refuse(
            order_qty=D(1), order_price=D("0.01"),
            order_intent="ORDER_INTENT_BUY_LONG",
            book_uncertain="gross/net divergence ['m']")
        assert r.startswith("BOOK_UNCERTAIN:")

    def test_the_executor_refuses_on_an_uncertain_book_at_this_layer(
            self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        st.reconcile_from_venue([], [])          # order vanished -> parked
        assert st.orders_uncertain()
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        ex.validation_gates = self.gates()
        rec = ex.handle(row(), {}, market=MARKET, window="16:15")
        assert rec["refused_by_layer"] == "VALIDATION_GATES"
        assert rec["refused_reason"].startswith("BOOK_UNCERTAIN:")

    def test_a_clean_book_still_passes(self, tmp_path):
        st = E.InventoryStore(str(tmp_path / "inv.json"))
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        ex.validation_gates = VG.ValidationGates(
            max_cash_deployed_usd=D("10"))
        rec = ex.handle(row(), {}, market=MARKET, window="16:15")
        assert rec["refused_by_layer"] != "VALIDATION_GATES", rec
        assert rec.get("VALIDATION_GATES") is None

    def test_the_gate_is_still_additive_and_refuse_only(self):
        """A loose ceiling cannot raise the deployed envelope."""
        gt = VG.ValidationGates(max_cash_deployed_usd=D("1000"))
        assert gt.refuse(order_qty=D(1), order_price=D("0.95"),
                         order_intent="ORDER_INTENT_BUY_LONG") is None
        assert limits(max_total_exposure_usd=D("0")).refuse(
            price=D("0.95"), qty=D(1), market=MARKET,
            committed_by_market={}, residual_after=D(0),
            realized_today=D(0), open_markets=[MARKET],
            outstanding_by_market={}, live_order_count=0,
            outstanding_residual=D(0), orders_uncertain=None,
            risk_by_market={MARKET: D("0.95")})

    def test_the_basis_is_stated_in_as_dict(self):
        d = VG.ValidationGates().as_dict()
        assert "ACCOUNT-WIDE" in d["cash_basis"]
        assert d["fails_closed_on"] == "an unconfirmed or disputed ledger"


# =============== FIX 2 — independent halt -> exact-order cancellation

class Armed:
    armed = True
    name = "FAKE"

    def __init__(self):
        self.disarmed = None
        self.cancelled = []
        self.retired = set()

    def disarm(self, reason):
        self.armed = False
        self.disarmed = reason

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        self.retired.add(order_id)
        return {"cancelled": order_id}

    def order(self, order_id):
        if order_id not in self.retired:
            raise KeyError(order_id)
        return {"id": order_id, "state": "ORDER_STATE_CANCELED",
                "cumQuantity": "0", "leavesQuantity": "0",
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}


class _RunnerStub:
    HALT_SWEEP_EVERY_S = 10.0

    def __init__(self, store, transport, gate):
        self.store = store
        self.transport = transport
        self.executor = type("Ex", (), {"halt_gate": gate})()
        self.last_halt_sweep_t = 0.0


def sweep(stub, now=1000.0):
    from src import l5_pmus_live_runner as R
    return R.Runner.maybe_halt_deadman(stub, now)


class Raising:
    def get(self, key):
        raise HG.HaltReadUnavailable("partitioned")

    def put(self, key, body):               # pragma: no cover
        raise AssertionError("the executor must never write the latch")


class TestALatchedHaltCancels:

    def test_a_latched_halt_cancels_exactly_the_resting_order(self, tmp_path):
        """HALT_GATE_ABSENT stops this process ARMING without a halt, and the
        gate in `handle` stops the NEXT order. Neither touches an order
        already resting -- and the watchdog cannot, by design."""
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        halt = HG.FileStore(str(tmp_path / "halt"))
        HG.HaltAsserter(halt, who="l5-halt-watchdog").assert_halt(
            reason="EXECUTOR_REPORT_STALE", evidence={"age_s": 301})
        t = Armed()
        rec = sweep(_RunnerStub(st, t, HG.HaltGate(halt)))
        assert t.cancelled == ["v1"]
        assert [c["venue_order_id"] for c in rec["cancelled"]] == ["v1"]
        assert t.armed is False
        assert t.disarmed.startswith("independent_halt:halt_asserted")

    def test_an_unreachable_latch_also_cancels(self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        t = Armed()
        rec = sweep(_RunnerStub(st, t, HG.HaltGate(Raising())))
        assert t.cancelled == ["v1"]
        assert t.disarmed == "independent_halt:halt_state_unavailable"
        assert rec is not None

    def test_a_stale_attestation_also_cancels(self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        halt = HG.FileStore(str(tmp_path / "halt"))
        HG.HaltAsserter(halt, who="w").heartbeat_no_halt()
        t = Armed()
        rec = sweep(_RunnerStub(st, t, HG.HaltGate(halt, max_age_s=-1)))
        assert rec is not None
        assert t.disarmed == "independent_halt:halt_record_stale"

    def test_a_gate_that_raises_also_cancels(self, tmp_path):
        class Exploding:
            def state(self):
                raise RuntimeError("boom")

        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        t = Armed()
        assert sweep(_RunnerStub(st, t, Exploding())) is not None
        assert t.disarmed == (
            "independent_halt:HALT_GATE_UNREADABLE:RuntimeError")

    def test_a_fresh_no_halt_cancels_nothing(self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        halt = HG.FileStore(str(tmp_path / "halt"))
        HG.HaltAsserter(halt, who="w").heartbeat_no_halt()
        t = Armed()
        assert sweep(_RunnerStub(st, t, HG.HaltGate(halt))) is None
        assert t.cancelled == [] and t.armed is True

    def test_nothing_resting_means_the_latch_is_not_read(self, tmp_path):
        """An idle session must not put an HTTP read in front of every tick.
        The submission path is gated independently."""
        st = E.InventoryStore(str(tmp_path / "inv.json"))
        t = Armed()
        assert sweep(_RunnerStub(st, t, HG.HaltGate(Raising()))) is None
        assert t.armed is True

    def test_an_unarmed_transport_is_left_alone(self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        t = Armed()
        t.armed = False
        assert sweep(_RunnerStub(st, t, HG.HaltGate(Raising()))) is None

    def test_no_gate_configured_changes_nothing(self, tmp_path):
        """L5_HALT_URL unset: this sweep is inert, and `authorize_arming`'s
        HALT_GATE_ABSENT is what refuses to arm."""
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        t = Armed()
        assert sweep(_RunnerStub(st, t, None)) is None
        assert t.cancelled == []

    def test_the_sweep_is_rate_limited_to_one_read(self, tmp_path):
        class Counting:
            def __init__(self):
                self.n = 0

            def get(self, key):
                self.n += 1
                raise HG.HaltReadUnavailable("partitioned")

            def put(self, key, body):       # pragma: no cover
                raise AssertionError("never")

        class Blind(Armed):
            def order(self, order_id):
                raise OSError("venue unreachable")

        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        t = Blind()
        c = Counting()
        stub = _RunnerStub(st, t, HG.HaltGate(c))
        assert sweep(stub, now=1000.0) is not None
        assert c.n == 1                      # one read, not two
        t.armed = True
        assert sweep(stub, now=1005.0) is None
        assert c.n == 1
        assert sweep(stub, now=1011.0) is not None
        assert c.n == 2

    # ---- restart, and an uncertain acknowledgement -------------------

    def test_the_latch_survives_a_restart_and_the_restart_cancels(self,
                                                                  tmp_path):
        inv = tmp_path / "inv.json"
        halt = HG.FileStore(str(tmp_path / "halt"))
        rest(E.InventoryStore(str(inv)), "c1", "v1")
        HG.HaltAsserter(halt, who="w").assert_halt(reason="r",
                                                   evidence={"e": 1})
        # a NEW store over the same bytes: the only thing carried across is
        # the file, which is the only honest model of a restart
        st = E.InventoryStore(str(inv))
        assert st.live_order_count() == 1
        assert st.reconcile_from_venue([], [venue_row("c1", "v1")]
                                       )["reconciled"] is True
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        ex.halt_gate = HG.HaltGate(halt)
        assert ex.handle(row(), {}, market=MARKET, window="16:15"
                         )["refused_by_layer"] == "HALT_GATE"
        t = Armed()
        rec = sweep(_RunnerStub(st, t, HG.HaltGate(halt)))
        assert [c["venue_order_id"] for c in rec["cancelled"]] == ["v1"]

    def test_an_unconfirmed_cancel_marks_the_book_uncertain(self, tmp_path):
        class StillOpen(Armed):
            def order(self, order_id):
                return {"id": order_id, "state": "ORDER_STATE_OPEN",
                        "cumQuantity": "0", "leavesQuantity": "2",
                        "avgPx": {"value": "0", "currency": "USD"},
                        "commissionNotionalTotalCollected": {
                            "value": "0", "currency": "USD"}}

        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        rec = sweep(_RunnerStub(st, StillOpen(), HG.HaltGate(Raising())))
        assert rec["cancelled"] == []
        assert [o["venue_order_id"] for o in rec["still_open"]] == ["v1"]
        assert st.orders_uncertain()
        # and the uncertainty is in the FILE, so it survives the restart
        assert E.InventoryStore(str(tmp_path / "inv.json")).orders_uncertain()

    def test_a_reread_that_raises_leaves_the_book_uncertain(self, tmp_path):
        class Blind(Armed):
            def order(self, order_id):
                raise OSError("venue unreachable")

        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        rec = sweep(_RunnerStub(st, Blind(), HG.HaltGate(Raising())))
        assert rec["cancelled"] == []
        assert rec["unverified"] or rec["errors"]
        assert st.orders_uncertain()

    def test_an_uncertain_book_then_refuses_the_next_order(self, tmp_path):
        st = rest(E.InventoryStore(str(tmp_path / "inv.json")), "c1", "v1")
        st.mark_uncertain("cancel unconfirmed on v1")
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        ex.validation_gates = VG.ValidationGates(
            max_cash_deployed_usd=D("10"))
        rec = ex.handle(row(), {}, market=MARKET, window="16:15")
        assert rec["refused_reason"].startswith("BOOK_UNCERTAIN:")


# =================== FIX 3 — the latch is immutable under a bad read

class Torn:
    def __init__(self, body=b"{not json"):
        self.body = body
        self.puts = []

    def get(self, key):
        return self.body

    def put(self, key, body):
        self.puts.append(body)
        self.body = body
        return {"ok": True}


class TestTheLatchCannotBeClearedByABadRead:

    def test_a_failed_read_does_not_let_the_watchdog_attest_no_halt(self):
        """THE HOLE. `heartbeat_no_halt` read through a helper returning None
        both for "no object" and for "I could not find out", so a GET failure
        in front of a LATCHED halt looked like a clean store."""
        class Dead:
            def __init__(self):
                self.puts = []

            def get(self, key):
                raise HG.HaltReadUnavailable("500")

            def put(self, key, body):
                self.puts.append(body)
                return {"ok": True}

        d = Dead()
        with pytest.raises(PermissionError):
            HG.HaltAsserter(d, who="w").heartbeat_no_halt()
        assert d.puts == []

    def test_a_torn_record_does_not_let_the_watchdog_attest(self):
        t = Torn()
        with pytest.raises(PermissionError):
            HG.HaltAsserter(t, who="w").heartbeat_no_halt()
        assert t.puts == []

    def test_a_non_object_record_does_not_let_the_watchdog_attest(self):
        t = Torn(body=b'["halt", false]')
        with pytest.raises(PermissionError):
            HG.HaltAsserter(t, who="w").heartbeat_no_halt()
        assert t.puts == []

    def test_a_malformed_halt_value_is_still_a_latch_on_both_sides(self):
        t = Torn(body=json.dumps(
            {"halt": "yes", "t": NOW_DT.isoformat(), "who": "w"}).encode())
        assert HG.HaltGate(t).submissions_permitted() is False
        with pytest.raises(PermissionError):
            HG.HaltAsserter(t, who="w").heartbeat_no_halt()

    def test_the_watchdog_still_cannot_clear_its_own_latch(self, tmp_path):
        s = HG.FileStore(str(tmp_path / "halt"))
        a = HG.HaltAsserter(s, who="w")
        a.assert_halt(reason="r", evidence={"e": 1})
        with pytest.raises(PermissionError):
            a.heartbeat_no_halt()
        assert HG.HaltGate(s).submissions_permitted() is False

    def test_an_absent_record_still_permits_the_first_heartbeat(self,
                                                               tmp_path):
        """The fix must not wedge a fresh deployment: ABSENT is not
        UNREADABLE, and a store with no object yet is attestable."""
        s = HG.FileStore(str(tmp_path / "halt"))
        assert HG._classify(s)[0] == HG.ABSENT
        assert HG.HaltAsserter(s, who="w").heartbeat_no_halt()["halt"] is False
        assert HG.HaltGate(s).submissions_permitted() is True

    def test_the_classification_separates_absent_from_unreadable(self,
                                                                 tmp_path):
        assert HG._classify(HG.FileStore(str(tmp_path / "nope")))[0] == (
            HG.ABSENT)
        assert HG._classify(Raising())[0] == HG.UNREADABLE
        assert HG._classify(Torn())[0] == HG.UNREADABLE
        d = tmp_path / "halt2" / HG.HALT_KEY
        d.mkdir(parents=True)
        assert HG._classify(HG.FileStore(str(tmp_path / "halt2"))
                            )[0] == HG.UNREADABLE

    def test_the_gate_still_treats_absent_and_unreadable_alike(self,
                                                              tmp_path):
        """The distinction is for the asserter only. A gate reading ABSENT as
        permission would be the original hole, inverted."""
        assert HG.HaltGate(HG.FileStore(str(tmp_path / "nope"))
                           ).submissions_permitted() is False
        assert HG.HaltGate(Raising()).submissions_permitted() is False

    def test_the_http_store_raises_rather_than_reading_absent(self):
        s = HG.HttpHaltStore(
            "http://halt.internal",
            opener=lambda u, t: (_ for _ in ()).throw(OSError("500")))
        assert HG._classify(s)[0] == HG.UNREADABLE
        with pytest.raises(HG.HaltReadUnavailable):
            s.get(HG.HALT_KEY)

    def test_the_executor_cannot_write_the_latch_that_governs_it(self):
        with pytest.raises(PermissionError):
            HG.HttpHaltStore("http://halt.internal").put(HG.HALT_KEY, b"{}")


# ============ this lineage's own machinery is preserved, not duplicated

class TestTheExistingAuthorizationIsUntouched:

    def test_no_submitter_lease_was_ported(self):
        from src import l5_live_authorization as AUTH
        assert not hasattr(AUTH, "SubmitterLease")
        assert not hasattr(AUTH, "LeaseLost")

    def test_authorize_arming_is_still_the_boundary(self):
        from src import l5_live_authorization as AUTH
        assert callable(AUTH.authorize_arming)
        assert callable(AUTH.claim_once)

    def test_halt_gate_absent_is_still_an_arming_refusal(self):
        import inspect
        from src import l5_live_authorization as AUTH
        assert "HALT_GATE_ABSENT" in inspect.getsource(AUTH.authorize_arming)

    def test_the_claim_is_still_atomic(self):
        import inspect
        from src import l5_live_authorization as AUTH
        src = inspect.getsource(AUTH.claim_once)
        assert "O_EXCL" in src and "O_CREAT" in src

    def test_every_arm_site_still_sits_behind_the_boundary(self):
        import ast
        import pathlib as _p
        sites = {}
        for f in (list(_p.Path("src").glob("*.py"))
                  + list(_p.Path("scripts").glob("*.py"))):
            try:
                t = ast.parse(f.read_text())
            except SyntaxError:                        # pragma: no cover
                continue
            for n in ast.walk(t):
                if (isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "arm" and not n.args):
                    sites.setdefault(str(f), []).append(n.lineno)
        assert set(sites) == {"src/l5_pmus_live_runner.py",
                              "scripts/l5_pmus_live_oneshot.py"}, sites
        for f in sites:
            assert "authorize_arming" in _p.Path(f).read_text(), f
