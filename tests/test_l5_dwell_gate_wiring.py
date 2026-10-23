"""The dwell gate, wired: a persisted stranding clock and a coordinated deadline.

WHY THE GATE WAS NOT WIRED. `l5_defensive_completion.decide` has taken
`stranded_since_t` since 13a7e203, and both live call sites share one `_dkw`
that never carried it, because nothing in `InventoryStore` recorded WHEN a
market went off flat. The gate was complete and unreachable: no anchor, no
dwell. That is what this fixes.

TWO INDEPENDENT FLAGS, on purpose. `L5_DEFENSIVE_COMPLETION` arms the exit;
`L5_DEFENSIVE_DWELL` arms its TIMING. With the dwell flag off the executor
passes `stranded_since_t=None` and `decide` uses the arm window, so live
timing is byte-for-byte what it was and turning the policy on is one explicit
authorization rather than a side effect of unlocking submission.

THE 0300z COORDINATION DEFECT. `maybe_ts_deadline` retired a resting leg AT the
binding terminal (T+600 deployed / T+720 at the default margin). The defensive
sequence needs `DEFENSIVE_MIN_EXECUTION_S` BEFORE that terminal to cancel,
re-read and submit. So a complement still resting at the terminal was retired
at the instant the exit's own window had already shut. The deadline is now
terminal - runway, which is a TIGHTENING.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_defensive_completion as DEF
from src import l5_live_execution as LX
from src import l5_two_sided_probe as TSP

MKT = "cpc-btc-updown-15m-2026-10-23-0230z"
OTHER = "cpc-btc-updown-15m-2026-10-23-0245z"


class Clock:
    def __init__(self, t0: float = 1_792_000_000.0) -> None:
        self.t = t0

    def __call__(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.t, dt.timezone.utc)


@pytest.fixture
def store(tmp_path):
    clk = Clock()
    s = LX.InventoryStore(str(tmp_path / "inv.json"), now_fn=clk)
    s._clk = clk                      # the test's handle on the clock
    return s


def fill(s, market, side, qty, px="0.50"):
    s.apply_fill(market, side, D(str(qty)), D(px), basis="OWN")


class TestTheStampIsTakenFromAnAuthoritativeFill:

    def test_the_first_unmatched_fill_sets_it(self, store):
        assert store.stranded_since(MKT) is None
        fill(store, MKT, "UP", 1.17)
        assert store.stranded_since(MKT) == store._clk.t

    def test_an_extend_does_NOT_restart_the_clock(self, store):
        """The dwell measures from when the imbalance opened. If adding to the
        same side reset it, an accumulating position would never mature."""
        fill(store, MKT, "UP", 1.17)
        t0 = store.stranded_since(MKT)
        store._clk.t += 120.0
        fill(store, MKT, "UP", 1.00)
        assert store.stranded_since(MKT) == t0

    def test_returning_to_flat_clears_it(self, store):
        fill(store, MKT, "UP", 1.17)
        assert store.stranded_since(MKT) is not None
        fill(store, MKT, "DOWN", 1.17)
        assert store.residual(MKT) == 0
        assert store.stranded_since(MKT) is None

    def test_a_new_imbalance_after_flat_gets_a_NEW_stamp(self, store):
        fill(store, MKT, "UP", 1.00)
        first = store.stranded_since(MKT)
        fill(store, MKT, "DOWN", 1.00)
        store._clk.t += 300.0
        fill(store, MKT, "UP", 1.00)
        assert store.stranded_since(MKT) == first + 300.0

    def test_crossing_through_flat_restamps(self, store):
        """A complement big enough to reverse the sign is a new imbalance."""
        fill(store, MKT, "UP", 1.00)
        store._clk.t += 60.0
        fill(store, MKT, "DOWN", 3.00)
        assert store.residual(MKT) == D("-2.00")
        assert store.stranded_since(MKT) is not None

    def test_markets_are_independent(self, store):
        fill(store, MKT, "UP", 1.00)
        store._clk.t += 50.0
        fill(store, OTHER, "DOWN", 1.00)
        assert store.stranded_since(OTHER) - store.stranded_since(MKT) == 50.0


class TestItSurvivesARestart:

    def test_the_stamp_is_persisted_and_reloaded(self, store, tmp_path):
        fill(store, MKT, "UP", 1.17)
        t0 = store.stranded_since(MKT)
        store.save()
        again = LX.InventoryStore(str(tmp_path / "inv.json"))
        assert again.stranded_since(MKT) == t0

    def test_a_restart_does_not_reset_the_dwell(self, store, tmp_path):
        """The failure this prevents: a restart mid-window re-arming the
        dwell and making the exit wait another 30s it does not have."""
        fill(store, MKT, "UP", 1.17)
        store.save()
        later = Clock(store._clk.t + 600.0)
        again = LX.InventoryStore(str(tmp_path / "inv.json"), now_fn=later)
        assert again.stranded_since(MKT) == store.stranded_since(MKT)
        assert later.t - again.stranded_since(MKT) == 600.0


class TestItFailsClosed:

    def test_inventory_with_no_stamp_reads_None(self, store):
        """A store written before this field, or a position adopted from the
        venue, has inventory and no stamp. That must read as None -- which
        `decide` treats as 'use the arm window' -- never as an invented time."""
        store.state["positions"]["%s|UP" % MKT] = "1.17"
        assert store.state.get("stranded_since") == {}
        assert store.stranded_since(MKT) is None

    def test_a_flat_market_with_a_leftover_stamp_reads_None(self, store):
        store.state["stranded_since"][MKT] = 1.0
        assert store.residual(MKT) == 0
        assert store.stranded_since(MKT) is None

    def test_an_unreadable_stamp_reads_None(self, store):
        fill(store, MKT, "UP", 1.17)
        for bad in ("not-a-time", None, "", 0, -5):
            store.state["stranded_since"][MKT] = bad
            assert store.stranded_since(MKT) is None

    def test_an_unreadable_store_reads_None(self, store):
        fill(store, MKT, "UP", 1.17)
        store.state["stranded_since"] = "not-a-dict"
        assert store.stranded_since(MKT) is None


class TestReconciliation:

    def test_it_clears_stamps_the_ledger_says_are_flat(self, store):
        store.state["stranded_since"][OTHER] = 1.0
        out = store.reconcile_stranded()
        assert OTHER in out["cleared"]
        assert OTHER not in store.state["stranded_since"]

    def test_it_reports_inventory_without_a_stamp_rather_than_inventing_one(
            self, store):
        store.state["positions"]["%s|UP" % MKT] = "1.17"
        out = store.reconcile_stranded()
        assert MKT in out["stranded_without_stamp"]
        assert MKT not in store.state["stranded_since"]
        assert store.stranded_since(MKT) is None

    def test_it_keeps_a_live_stamp(self, store):
        fill(store, MKT, "UP", 1.17)
        t0 = store.stranded_since(MKT)
        out = store.reconcile_stranded()
        assert MKT in out["kept"]
        assert store.stranded_since(MKT) == t0

    def test_it_never_raises(self, store):
        store.state["positions"] = "not-a-dict"
        assert isinstance(store.reconcile_stranded(), dict)


class TestTheFlagGatesTheTimingAndNothingElse:

    def test_the_dwell_flag_defaults_off(self, monkeypatch):
        monkeypatch.delenv(DEF.DEFENSIVE_DWELL_ENV, raising=False)
        assert DEF.dwell_enabled() is False

    @pytest.mark.parametrize("v", ["1", "yes", "true", "on", "ON", " true "])
    def test_only_the_exact_strings_arm_it(self, monkeypatch, v):
        monkeypatch.setenv(DEF.DEFENSIVE_DWELL_ENV, v)
        assert DEF.dwell_enabled() is True

    @pytest.mark.parametrize("v", ["", "0", "no", "ture", "enabled", "Y"])
    def test_a_typo_cannot_half_arm_it(self, monkeypatch, v):
        monkeypatch.setenv(DEF.DEFENSIVE_DWELL_ENV, v)
        assert DEF.dwell_enabled() is False

    def test_it_is_a_SEPARATE_flag_from_the_exit_itself(self):
        assert DEF.DEFENSIVE_DWELL_ENV != DEF.DEFENSIVE_ENABLED_ENV

    def test_the_executor_passes_the_anchor_only_when_armed(self):
        import inspect
        src = inspect.getsource(LX)
        assert "stranded_since_t=(self.store.stranded_since(market)" in src
        assert "if DEF.dwell_enabled() else None)" in src


class TestTheRetirementDeadlineLeavesRunway:

    WS = 1_792_094_400.0

    def test_the_deadline_is_the_terminal_less_the_runway(self):
        terminal = TSP.cancel_cutoff_from_start(self.WS)
        got = TSP.complement_retire_from_start(
            self.WS, DEF.DEFENSIVE_MIN_EXECUTION_S)
        assert got == terminal - DEF.DEFENSIVE_MIN_EXECUTION_S
        assert got < terminal

    def test_0300z_cannot_recur_the_leg_is_retired_with_runway_left(self):
        """The defect: retired AT the terminal, when the exit needed 15s
        before it. Now there is always at least that much left."""
        terminal = TSP.cancel_cutoff_from_start(self.WS)
        got = TSP.complement_retire_from_start(
            self.WS, DEF.DEFENSIVE_MIN_EXECUTION_S)
        assert terminal - got >= DEF.DEFENSIVE_MIN_EXECUTION_S

    @pytest.mark.parametrize("bad", [None, "x", "", float("nan")])
    def test_an_unreadable_runway_is_zero_never_a_later_deadline(self, bad):
        terminal = TSP.cancel_cutoff_from_start(self.WS)
        assert TSP.complement_retire_from_start(self.WS, bad) <= terminal

    def test_a_negative_runway_cannot_push_the_deadline_out(self):
        terminal = TSP.cancel_cutoff_from_start(self.WS)
        assert TSP.complement_retire_from_start(self.WS, -500.0) == terminal

    def test_the_runner_uses_it(self):
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R.Runner.maybe_ts_deadline)
        assert "complement_retire_from_start" in src
        assert "DEFENSIVE_MIN_EXECUTION_S" in src


class TestTheKeyConventionCannotSilentlyMisread:
    """`apply_fill` writes positions under `canonical_market(...)`, and the
    live call site passes `handle`'s own `market`. If those two ever disagreed,
    the lookup must fail CLOSED rather than return a stale or wrong stamp --
    and it does, because `stranded_since` gates on `self.residual(market)`
    computed from the SAME key it then reads. A key that finds no position
    reads zero residual and returns None, which is the arm window."""

    def test_a_key_with_no_position_reads_None_even_with_a_stamp(self, store):
        fill(store, MKT, "UP", 1.17)
        assert store.stranded_since(MKT) is not None
        # a differently-spelled key: stamped, but no position under it
        store.state["stranded_since"]["NOT-A-REAL-MARKET"] = 1.0
        assert store.stranded_since("NOT-A-REAL-MARKET") is None

    def test_the_gate_and_the_lookup_use_one_key(self):
        import inspect
        src = inspect.getsource(LX.InventoryStore.stranded_since)
        assert "self.residual(market)" in src
        assert "(self.state.get(\"stranded_since\") or {}).get(str(market))" in src

    def test_the_writer_canonicalises(self):
        import inspect
        src = inspect.getsource(LX.InventoryStore.apply_fill)
        assert 'own_market = canonical_market(market' in src
        assert "self._note_stranding(own_market" in src
