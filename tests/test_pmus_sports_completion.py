"""Sports integration of the SHARED stranded-leg policy + its replay."""
import json
import os
from decimal import Decimal as D

import pytest

os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
from src import l5_completion_policy as CP  # noqa: E402
from src import pmus_sports_completion as SC  # noqa: E402
from src import pmus_sports_completion_replay as RP  # noqa: E402
from src import pmus_sports_two_sided as M  # noqa: E402
from tests.test_pmus_sports_two_sided import FakeVenue  # noqa: E402

# lead YES @0.40; YES book 0.40/0.42 -> NO (complement) 0.58/0.60
BASE = dict(lead="YES", lead_px=D("0.40"), need=D("5"), yes_bid=D("0.40"),
            yes_ask=D("0.42"), tick=D("0.01"))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(CP.POLICY_ENV, "ON")


# ------------------------------------------------------------- adapter
def test_effective_ceiling_is_stricter_only_and_never_above_par():
    s0 = CP.stage_for({"progress": None, "certain": False})
    # shared default (9c338374f) is the authorized 0.97: a looser sports
    # cap can never widen it
    assert s0["pair_ceiling"] == CP.PATIENT_CEILING == D("0.97")
    assert SC.effective_ceiling(s0, D("0.97")) == D("0.97")
    assert SC.effective_ceiling(s0, D("0.99")) == D("0.97")   # never looser
    # the comparator only runs when passed explicitly as patient_ceiling
    s99 = CP.stage_for({"progress": None, "certain": False},
                       patient_ceiling=CP.COMPARATOR_PATIENT_CEILING)
    assert SC.effective_ceiling(s99, D("0.99")) == D("0.99")
    assert SC.effective_ceiling(s99, D("1.05")) == D("0.99")  # never looser
    late = CP.stage_for({"progress": None, "certain": False},
                        event_late=True)
    assert SC.effective_ceiling(late, D("0.97")) == D("1.00")
    # even a hypothetical certain FINAL stage is clamped to par on sports
    fin = CP.stage_for({"progress": D("0.99"), "certain": True})
    assert SC.effective_ceiling(fin, D("0.97")) == D("1.00")


def test_off_by_default_holds(monkeypatch):
    monkeypatch.delenv(CP.POLICY_ENV, raising=False)
    d = SC.decide(**BASE, late=False)
    assert d["action"] == "HOLD" and d["policy"]["why"] == "POLICY_OFF"


def test_rests_at_the_cap_and_declares_p_win(on):
    d = SC.decide(**BASE, late=False, sports_cap=D("0.97"))
    assert d["action"] == "COMPLETE_MAKER" and d["px"] == D("0.57")
    assert d["policy"]["p_win_source"] == "HELD_SIDE_EXECUTABLE_MID"
    d99 = SC.decide(**BASE, late=False, sports_cap=D("0.99"))
    # cap allows 0.59 but the complement mid is 0.59: strictly under it
    assert d99["px"] == D("0.58")


def test_late_reaches_par_never_above(on):
    far = dict(BASE, yes_bid=D("0.30"), yes_ask=D("0.32"))   # NO .68/.70
    d = SC.decide(**far, late=True)
    assert d["effective_ceiling"] == "1.00"
    assert d["px"] is None or D("0.40") + d["px"] <= D("1.00")


def test_no_stage_pays_above_par_and_records_not_an_economic_gate(on):
    """Shared 9c338374f: FINAL is par; the ceiling is never the gate."""
    assert CP.FINAL_CEILING == D("1.00")
    assert max(c for _u, c, _n in CP.STAGES) == D("1.00")
    for cap in (D("0.97"), D("0.99"), D("1.03")):
        for late in (False, True):
            d = SC.decide(**BASE, late=late, sports_cap=cap)
            assert D(d["effective_ceiling"]) <= D("1.00")
            if d["px"] is not None:
                assert BASE["lead_px"] + d["px"] <= D("1.00")
                assert d["policy"]["ceiling_is_not_an_economic_gate"] is True


def test_comparator_cap_really_runs_at_099(on):
    d97 = SC.decide(**BASE, late=False, sports_cap=D("0.97"))
    d99 = SC.decide(**BASE, late=False, sports_cap=D("0.99"))
    assert d97["effective_ceiling"] == "0.97"
    assert d99["effective_ceiling"] == "0.99"
    assert d99["px"] > d97["px"]


def test_keep_and_reprice(on):
    d = SC.decide(**BASE, late=False, resting_px=D("0.57"))
    assert d["action"] == "KEEP"
    d = SC.decide(**BASE, late=False, resting_px=D("0.55"))
    assert d["action"] == "REPRICE_COMPLETION" and d["px"] == D("0.57")
    assert SC.rest_price(D("0.99"), D("0.40"), D("0.60"), D("0.01"),
                         D("0.59")) == D("0.58")


def test_a_winning_leg_locks_profit_without_overpaying(on):
    # we hold YES @0.40 and YES now trades 0.70/0.72 (NO 0.28/0.30, mid
    # 0.29): the rest price follows the complement under its mid (0.28),
    # never the 0.97-cap price 0.57 -> pair 0.68, better than holding
    d = SC.decide(**dict(BASE, yes_bid=D("0.70"), yes_ask=D("0.72")),
                  late=False)
    assert d["action"] == "COMPLETE_MAKER" and d["px"] == D("0.28")


def test_holds_when_the_cap_leaves_no_price(on):
    # held leg bought at 0.97: a 0.97 cap leaves no completion price
    d = SC.decide(**dict(BASE, lead_px=D("0.97")), late=False,
                  sports_cap=D("0.97"))
    assert d["action"] == "HOLD" and d["why"] == "REST_PRICE_BELOW_ONE_TICK"


# ------------------------------------------------------------- engine
@pytest.fixture
def eng(tmp_path, monkeypatch, on):
    monkeypatch.setattr(M, "RECOVERY_ON", True)
    monkeypatch.setattr(M, "COMPLETION_POLICY_ON", True)
    monkeypatch.setattr(M, "OPEN_POLICY", "two_sided")
    v = FakeVenue()                                      # YES 0.44/0.46
    return M.Engine(v, M.Book(tmp_path / "s.json")), v


def test_engine_routes_strands_to_the_shared_policy(eng):
    e, v = eng
    assert M.completion_policy_live() is True
    e.select(v.account())
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["open"] and
               o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)      # YES @0.42
    for _ in range(3):
        e.step_market("aec-x")
    comp = [o for o in v.orders.values() if o["open"] and o["side"] == "NO"]
    assert comp and all(D(str(o["px"])) <= D("0.55") for o in comp)


def test_default_engine_path_unchanged(monkeypatch):
    monkeypatch.delenv(CP.POLICY_ENV, raising=False)
    assert M.COMPLETION_POLICY_ON is False and M.completion_policy_live() \
        is False
    assert M.MAX_STRANDED == 0


def test_ev_gate_preserved():
    assert hasattr(M, "OPEN_EV_GATE")


def test_max_stranded_blocks_new_first_legs(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "MAX_STRANDED", 1)
    e.b.m("aec-other")["lone_since"] = 1.0
    assert e.halt_new_exposure() is True


# ------------------------------------------------------------- replay
def _tape(tmp_path):
    L = []

    def book(t, slug, b, a):
        L.append({"k": "book", "t": t, "slug": slug,
                  "bids": [{"px": {"value": str(b)}, "qty": "10"}],
                  "asks": [{"px": {"value": str(a)}, "qty": "10"}]})

    def ev(t, kind, **kw):
        L.append({"k": "ev", "t": t, "kind": kind, **kw})
    # strand A: YES @0.42 x5 at t=100, production paired at NO 0.60 (1.02)
    ev(100, "RECOVERY_DECISION", slug="aec-a", lead="YES", lead_px="0.42",
       need="5", late=False)
    for t, b, a in ((100, .44, .46), (160, .44, .46), (220, .46, .48),
                    (300, .40, .42)):
        book(t, "aec-a", b, a)                # NO ask = 1 - YES bid
    ev(400, "RECOVERY_RESOLVED", slug="aec-a", outcome="paired", age_s=300,
       lead_px="0.42", completion_px="0.60", pair_cost="1.02")
    # strand B: NO @0.30 x5, production held; YES rallies, never pairs
    ev(1000, "RECOVERY_DECISION", slug="aec-b", lead="NO", lead_px="0.30",
       need="5", late=False)
    for t in (1000, 1100, 1200):
        book(t, "aec-b", .80, .82)
    ev(1300, "RECOVERY_RESOLVED", slug="aec-b", outcome="held", age_s=300,
       lead_px="0.30")
    return [json.dumps(r) for r in L]


def test_replay_separates_recorded_and_modelled():
    books, events = RP.load(_tape(None))
    res = RP.run(books, events, {"aec-a": D("1"), "aec-b": D("1")})
    prod = res["production_RECORDED"]
    assert prod["strands"] == 2 and prod["paired"] == 1
    # A: 5*(1-1.02)=-0.10 ; B: NO held, YES settled 1 -> 5*(0-0.30)=-1.50
    assert D(prod["net_usd"]) == D("-1.60")
    p97 = res["policy"]["cap_0.97_touch"]
    # A: NO book 0.54/0.56 (mid 0.55): rests at 0.54 (under the mid);
    # NO ask = 1 - YES bid reaches 0.54 at t=220 -> MODELLED fill, 0.96
    assert p97["paired_MODELLED"] == 1
    assert p97["completion_pair_cost"]["max"] == "0.96"
    assert p97["held_to_settlement"] == 1
    assert "MODELLED" in res["labels"] and "RECORDED" in res["labels"]
    assert set(p97["stage_distribution"]) <= {"PATIENT", "BREAKEVEN"}
    p99 = res["policy"]["cap_0.99_touch"]
    assert D(p99["completion_pair_cost"]["max"]) <= D("0.99")


def test_replay_never_completes_above_par_even_late():
    L = _tape(None)
    L.append(json.dumps({"k": "ev", "t": 1050, "kind": "RECOVERY_DECISION",
                         "slug": "aec-b", "lead": "NO", "lead_px": "0.30",
                         "need": "5", "late": True}))
    books, events = RP.load(L)
    res = RP.run(books, events, {})
    for k, v in res["policy"].items():
        pc = v["completion_pair_cost"]
        assert pc.get("above_1", 0) == 0, k


def test_replay_does_not_leave_the_policy_enabled(monkeypatch):
    monkeypatch.delenv(CP.POLICY_ENV, raising=False)
    books, events = RP.load(_tape(None))
    RP.run(books, events, {})
    assert CP.enabled() is False


# ------------------------------------------------------------- runner
def test_runner_off_by_default_and_refuses_when_live(monkeypatch, on):
    monkeypatch.delenv(M.COMPLETION_REPLAY_ENV, raising=False)
    assert M.start_completion_replay(object()) is None
    monkeypatch.setenv(M.COMPLETION_REPLAY_ENV, "1")
    monkeypatch.setattr(M, "COMPLETION_POLICY_ON", True)
    logs = []
    monkeypatch.setattr(M, "log", lambda k, **kw: logs.append((k, kw)))
    assert M.start_completion_replay(object()) is None
    assert logs[-1][1]["why"] == "live_policy_on_refused"


def test_runner_reads_tape_and_logs_without_any_order_path(monkeypatch,
                                                           tmp_path):
    monkeypatch.setenv(M.COMPLETION_REPLAY_ENV, "1")
    monkeypatch.setattr(M, "COMPLETION_POLICY_ON", False)
    monkeypatch.setattr(M, "STATE_DIR", tmp_path)
    (tmp_path / "tape").mkdir()
    (tmp_path / "tape" / "tape_20261117.jsonl").write_text(
        "\n".join(_tape(None)) + "\n")
    logs = []
    monkeypatch.setattr(M, "log", lambda k, **kw: logs.append((k, kw)))

    class V:
        def gget(self, path):
            return {"marketData": {"state": "MARKET_STATE_EXPIRED",
                                   "settlementPx": {"value": "1.0000"}}}

        def place(self, *a, **k):               # must never be reached
            raise AssertionError("replay placed an order")
    th = M.start_completion_replay(V())
    th.join(10)
    rep = [kw for k, kw in logs if k == "COMPLETION_REPLAY"]
    assert rep and rep[0]["ok"] and rep[0]["strands"] == 2
    assert rep[0]["settled_markets"] == 2
    assert any(k == "COMPLETION_REPLAY_ROWS" for k, _ in logs)
