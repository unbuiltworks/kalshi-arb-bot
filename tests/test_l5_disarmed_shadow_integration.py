"""Integration of the ledger repair, the settlement adjustment, the halt gate
and the validation gates into the DEPLOYED executor lineage.

The load-bearing tests are the ones proving the adjustment cannot be applied
twice and that the already-retired market's own entry is never touched."""
from __future__ import annotations

import json
import re
import tempfile
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as RUN

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "deploy" / "l5_pmus_live" / "Dockerfile"

Z0030 = "cpc-btc-updown-15m-2026-10-12-0030z"
Z1245 = "cpc-btc-updown-15m-2026-10-12-1245z"
NFL = "aec-nfl-bal-atl-2026-10-11"


class FakeStore:
    """Only what the repair and adjustment paths touch."""

    def __init__(self, state):
        self.state = state
        self.saves = 0
        self.reconstructed = []

    def save(self):
        self.saves += 1

    def leg_cost(self, market):
        return dict((self.state.get("leg_cost") or {}).get(market) or {})

    def reconstruct_cost_basis(self, market, side, *, qty, cost, source):
        self.reconstructed.append(
            {"market": market, "side": side, "qty": qty, "cost": cost,
             "source": source})
        return {"ok": True}


def retired_state(pnl="1.6800", realized="1.6800"):
    return {"retired_markets": {Z0030: {"t": "2026-10-12T00:45:00Z",
                                        "settlement_pnl": pnl}},
            "realized_attributed": realized,
            "realized_today": realized}


# -------------------------------------- the adjustment, and its idempotency

def test_adjustment_adds_exactly_two_cents():
    st = retired_state()
    s = FakeStore(st)
    res = RUN.apply_ledger_adjustments(s)
    assert res and len(res["applied"]) == 1
    assert D(st["realized_attributed"]) == D("1.7000")
    assert D(st["realized_today"]) == D("1.7000")


def test_adjustment_cannot_be_applied_twice():
    st = retired_state()
    s = FakeStore(st)
    RUN.apply_ledger_adjustments(s)
    first = st["realized_attributed"]
    assert RUN.apply_ledger_adjustments(s) is None
    assert st["realized_attributed"] == first == "1.7000"


def test_idempotency_survives_a_restart_because_it_is_in_the_state_file():
    st = retired_state()
    RUN.apply_ledger_adjustments(FakeStore(st))
    # a "restart": a brand new store object over the SAME persisted state
    assert RUN.apply_ledger_adjustments(FakeStore(st)) is None
    assert D(st["realized_attributed"]) == D("1.7000")
    assert "0030z-settlement-understated-by-two-cents" in \
        st["ledger_adjustments_applied"]


def test_the_retired_entry_is_never_modified():
    st = retired_state()
    before = json.dumps(st["retired_markets"], sort_keys=True)
    RUN.apply_ledger_adjustments(FakeStore(st))
    assert json.dumps(st["retired_markets"], sort_keys=True) == before


def test_already_retired_is_never_bypassed():
    """The adjustment must not call any settlement path at all."""
    src = Path(ROOT / "src" / "l5_pmus_live_runner.py").read_text()
    body = src.split("def apply_ledger_adjustments")[1].split(
        "\ndef ")[0]
    assert "retire_settled_market" not in body
    assert "retired_markets\"]" not in body.replace(
        'state.get("retired_markets")', "")


def test_adjustment_refuses_when_the_recorded_figure_differs():
    st = retired_state(pnl="1.2500")
    s = FakeStore(st)
    assert RUN.apply_ledger_adjustments(s) is None
    assert D(st["realized_attributed"]) == D("1.6800"), "nothing was written"


def test_adjustment_refuses_when_the_market_is_not_retired():
    st = {"retired_markets": {}, "realized_attributed": "0"}
    assert RUN.apply_ledger_adjustments(FakeStore(st)) is None
    assert D(st["realized_attributed"]) == 0


def test_adjustment_records_before_after_and_source_orders():
    st = retired_state()
    RUN.apply_ledger_adjustments(FakeStore(st))
    log = st["settlement_adjustments"]
    assert len(log) == 1
    e = log[0]
    assert e["recorded_pnl"] == "1.6800"
    assert e["authoritative_pnl"] == "1.7000"
    assert e["delta"] == "0.0200"
    assert e["realized_attributed_before"] == "1.6800"
    assert e["realized_attributed_after"] == "1.7000"
    assert e["evidence"]["orders"] == ["D1SCCX4F8Z84"]
    assert e["retired_entry_untouched"] is True


def test_every_adjustment_has_a_unique_id():
    ids = [a["id"] for a in RUN.LEDGER_ADJUSTMENTS]
    assert len(ids) == len(set(ids)) and all(ids)


# ----------------------------------------------- the repair entries

def test_the_three_repair_entries_exist_with_attested_values():
    by = {r["id"]: r for r in RUN.LEDGER_REPAIRS}
    a = by["1245z-manual-yes-fill-no-cost-basis"]
    assert a["market"] == Z1245 and a["side"] == "UP"
    assert a["qty"] == D("2") and a["cost"] == D("1.6800")
    assert a["evidence"]["orders"] == ["D23N485QAZ89"]
    b = by["nfl-bal-atl-manual-yes-legs-undercounted"]
    assert b["qty"] == D("4") and b["cost"] == D("1.7700")
    c = by["nfl-bal-atl-manual-no-legs-no-cost-basis"]
    assert c["qty"] == D("4") and c["cost"] == D("2.1700")


def test_repair_ids_are_unique():
    ids = [r["id"] for r in RUN.LEDGER_REPAIRS]
    assert len(ids) == len(set(ids))


def test_no_repair_entry_targets_the_already_retired_market():
    assert all(r["market"] != Z0030 for r in RUN.LEDGER_REPAIRS), \
        "0030z must be corrected by adjustment, never by re-settling"


def test_a_repair_refuses_when_the_ledger_holds_something_else():
    st = {"positions": {"%s|UP" % Z1245: "9.0000"},
          "leg_cost": {Z1245: {"UP": D("0")}}}
    s = FakeStore(st)
    RUN.repair_ledger_from_venue(s)
    assert not any(r["market"] == Z1245 for r in s.reconstructed)


def test_the_1245z_repair_books_the_real_cost():
    st = {"positions": {"%s|UP" % Z1245: "2.0000"},
          "leg_cost": {Z1245: {"UP": D("0")}}}
    s = FakeStore(st)
    RUN.repair_ledger_from_venue(s)
    mine = [r for r in s.reconstructed if r["market"] == Z1245]
    assert len(mine) == 1
    assert mine[0]["cost"] == D("1.6800") and mine[0]["qty"] == D("2")


# --------------------------------------------------- halt gate wiring

class _Halt:
    def __init__(self, permitted, reason=None):
        self._p, self._r = permitted, reason

    def submissions_permitted(self):
        return self._p

    def refuse_reason(self):
        return self._r


def test_the_halt_gate_is_read_before_translation():
    """It must sit above UNREADABLE_ROW, or a halt depends on a good book."""
    src = (ROOT / "src" / "l5_live_execution.py").read_text()
    body = src.split("    def handle(self, row")[1]
    assert body.index("HALT_GATE") < body.index("UNREADABLE_ROW")


def test_an_unreadable_halt_gate_refuses_rather_than_failing_open():
    class Boom:
        def submissions_permitted(self):
            raise RuntimeError("store unreachable")

        def refuse_reason(self):
            return None
    src = (ROOT / "src" / "l5_live_execution.py").read_text()
    assert "HALT_GATE_UNREADABLE" in src
    assert "permitted = False" in src


def test_the_executor_declares_its_hooks_and_defaults_them_to_absent():
    """Declared on the class, not attached from outside -- and None means
    absent, never permitted."""
    from src import l5_live_execution as EX
    import pathlib as _p
    st = EX.InventoryStore(str(_p.Path(tempfile.mkdtemp()) / "s.json"))
    ex = EX.FaithfulExecutor(st, EX.SafetyLimits())
    assert ex.halt_gate is None
    assert ex.validation_gates is None
    assert ex.available_cash_usd is None


# ---------------------------------------------- validation gate wiring

def test_validation_gates_are_consulted_after_the_existing_limits():
    src = (ROOT / "src" / "l5_live_execution.py").read_text()
    body = src.split("    def handle(self, row")[1]
    assert body.index("IMBALANCE_INCREASE_SUPPRESSED") < \
        body.index("VALIDATION_GATES")


def test_a_validation_gate_error_refuses_rather_than_failing_open():
    src = (ROOT / "src" / "l5_live_execution.py").read_text()
    assert "VALIDATION_GATE_ERROR" in src


def test_the_gates_module_is_importable_and_defaults_to_no_recycling():
    from src import l5_live_validation_gates as G
    assert G.ValidationGates().cash_policy == G.NO_RECYCLING
    assert G.ValidationGates().max_unmatched_contracts == D("5")
    assert G.ValidationGates().max_cumulative_loss_usd == D("20")


# ------------------------------------------- image packaging and arming

def test_the_image_packages_all_three_new_modules():
    body = DOCKERFILE.read_text()
    for m in ("l5_halt_gate", "l5_live_validation_gates"):
        assert re.search(r"^COPY\s+src/%s\.py\s" % m, body, re.M), m
    # NOT shipped: nothing in the image imports it, and the graft test
    # refuses any module outside the runner's closure.
    assert not re.search(r"^COPY\s+src/l5_authoritative_ledger_repair",
                         body, re.M)


def test_every_module_the_executor_imports_is_packaged():
    body = DOCKERFILE.read_text()
    src = (ROOT / "src" / "l5_live_execution.py").read_text()
    missing = [m for m in set(re.findall(r"^\s*from src import (\w+)",
                                         src, re.M))
               if not re.search(r"^COPY\s+src/%s\.py\s" % re.escape(m),
                                body, re.M)]
    assert not missing, "not COPYed into the executor image: %s" % missing


def test_the_image_sets_no_arming_variable():
    """Arming needs three env values that agree; the image must set none."""
    body = DOCKERFILE.read_text()
    assert not re.search(r"^(ENV|ARG)\b.*[Aa][Rr][Mm]", body, re.M)


def test_arming_requires_three_agreeing_env_values_and_is_off_here(
        monkeypatch):
    from src import pmus_q1_executor as Q1
    for e in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(e, raising=False)
    assert Q1.armed() is False, "no token -> disarmed"
    # a live flag alone is not enough
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    assert Q1.armed() is False
    # a session id without a matching arm token is not enough
    monkeypatch.setenv(Q1.SID_ENV, "sid-123")
    assert Q1.armed() is False
    # a MISMATCHED arm token is not enough
    monkeypatch.setenv(Q1.ARM_ENV, "sid-999")
    assert Q1.armed() is False
    # only an exact match arms, and nothing in this change supplies one
    monkeypatch.setenv(Q1.ARM_ENV, "sid-123")
    assert Q1.armed() is True


def test_nothing_in_this_integration_sets_an_arm_variable():
    for f in ("src/l5_live_validation_gates.py", "src/l5_halt_gate.py"):
        body = (ROOT / f).read_text()
        from src import pmus_q1_executor as Q1
        for e in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
            assert e not in body, "%s references %s" % (f, e)


# ------------------------------------------------- runner-side wiring

def test_validation_gates_are_on_by_default_with_the_mandate_limits():
    g = RUN.validation_gates()
    assert g is not None
    d = g.as_dict()
    assert d["max_cash_deployed_usd"] == "10"
    assert d["max_unmatched_contracts"] == "5"
    assert d["max_cumulative_loss_usd"] == "20"
    assert d["cash_policy"] == "NO_RECYCLING"


def test_validation_gates_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv(RUN.GATES_ENV, "0")
    assert RUN.validation_gates() is None


def test_gate_limits_are_env_overridable_but_default_to_the_mandate(
        monkeypatch):
    monkeypatch.setenv("L5_MAX_UNMATCHED_CONTRACTS", "3")
    assert RUN.validation_gates().max_unmatched_contracts == D("3")


def test_the_halt_gate_is_inert_until_an_endpoint_is_named(monkeypatch):
    monkeypatch.delenv(RUN.HALT_URL_ENV, raising=False)
    assert RUN.halt_gate() is None, \
        "this deployment must change nothing until the guard URL is set"


def test_a_named_halt_endpoint_builds_a_gate(monkeypatch):
    monkeypatch.setenv(RUN.HALT_URL_ENV, "http://guard.railway.internal:8080")
    g = RUN.halt_gate()
    assert g is not None and hasattr(g, "submissions_permitted")


def test_an_unbuildable_halt_gate_is_announced_not_skipped_silently(
        monkeypatch, capsys):
    monkeypatch.setenv(RUN.HALT_URL_ENV, "http://x")

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no")
    import src.l5_halt_gate as HG
    monkeypatch.setattr(HG, "HttpHaltStore", Boom)
    assert RUN.halt_gate() is None
    assert "halt_gate_unavailable" in capsys.readouterr().out


def test_the_runner_never_sets_available_cash_to_a_number_by_default():
    """None must mean 'no reading', never 'unlimited'."""
    src = (ROOT / "src" / "l5_pmus_live_runner.py").read_text()
    assert "self.executor.available_cash_usd = None" in src


# ------------------- the disarmed state as a STRUCTURAL invariant

class _Inner:
    """A transport that would happily place an order."""
    armed = True

    def __init__(self):
        self.submitted = []

    def submit(self, intent):
        self.submitted.append(intent)
        return {"orderId": "SHOULD-NEVER-EXIST"}

    def cancel(self, oid):
        return {"cancelled": oid}

    def positions(self):
        return ["read ok"]


def test_the_lock_is_on_by_default():
    assert RUN.shadow_only() is True


def test_the_lock_is_released_only_by_an_explicit_zero(monkeypatch):
    monkeypatch.setenv(RUN.SHADOW_ONLY_ENV, "0")
    assert RUN.shadow_only() is False
    for v in ("1", "", "no", "false", "YES"):
        monkeypatch.setenv(RUN.SHADOW_ONLY_ENV, v)
        assert RUN.shadow_only() is True, v


def test_submit_is_impossible_through_the_lock():
    inner = _Inner()
    t = RUN.ShadowOnlyTransport(inner)
    with pytest.raises(LX.NotArmed):
        t.submit({"side": "BUY"})
    assert inner.submitted == [], "nothing reached the inner transport"


def test_submit_stays_impossible_even_with_every_arming_variable_set(
        monkeypatch):
    """THE invariant. Arming is three env values that agree; this proves the
    lock does not depend on them."""
    from src import pmus_q1_executor as Q1
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "sid-abc")
    monkeypatch.setenv(Q1.ARM_ENV, "sid-abc")
    assert Q1.armed() is True, "the account is armed in this scenario"

    inner = _Inner()
    t = RUN.ShadowOnlyTransport(inner)
    with pytest.raises(LX.NotArmed):
        t.submit({"side": "BUY"})
    assert inner.submitted == []


def test_the_lock_raises_the_same_exception_the_executor_already_handles():
    """So no new code path runs on the money side."""
    src = (ROOT / "src" / "l5_live_execution.py").read_text()
    assert "except NotArmed as exc:" in src
    t = RUN.ShadowOnlyTransport(_Inner())
    with pytest.raises(LX.NotArmed):
        t.submit({})


def test_cancel_is_not_blocked_because_cancelling_only_reduces_risk():
    t = RUN.ShadowOnlyTransport(_Inner())
    assert t.cancel("OID1") == {"cancelled": "OID1"}


def test_reads_and_attributes_pass_through():
    t = RUN.ShadowOnlyTransport(_Inner())
    assert t.positions() == ["read ok"]
    assert t.armed is True


def test_submit_is_defined_on_the_class_so_getattr_cannot_reach_the_inner():
    assert "submit" in RUN.ShadowOnlyTransport.__dict__
    assert "submit" in RUN.ShadowOnlyTransport.BLOCKED


def test_the_runner_wraps_before_the_executor_is_constructed():
    """There must be no window where the executor holds a submitting
    transport."""
    src = (ROOT / "src" / "l5_pmus_live_runner.py").read_text()
    assert src.index("ShadowOnlyTransport(self.transport)") < \
        src.index("self.executor = LX.FaithfulExecutor(")


def test_the_lock_does_not_touch_the_owners_arming_variables():
    src = (ROOT / "src" / "l5_pmus_live_runner.py").read_text()
    body = src.split("class ShadowOnlyTransport")[1].split("\ndef halt_gate")[0]
    from src import pmus_q1_executor as Q1
    for e in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        assert e not in body
