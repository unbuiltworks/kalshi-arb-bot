"""Same-market EXTEND while stranded (owner 2026-11-15: "work every market
until it's profitable"): OPEN -> EXTEND -> COMPLETE -> new pair, bounded so
it can never become a martingale."""
import os
from decimal import Decimal as D

import pytest

os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
from src import pmus_sports_two_sided as M  # noqa: E402
from tests.test_pmus_sports_intl_guard import guard, move  # noqa: E402
from tests.test_pmus_sports_two_sided import FakeVenue  # noqa: E402


@pytest.fixture
def ext(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RECOVERY_ON", True)
    monkeypatch.setattr(M, "EXTEND_ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("12"))
    v = FakeVenue()
    return M.Engine(v, M.Book(tmp_path / "s.json")), v


def strand(e, v):
    e.select(v.account())
    e.step_market("aec-x")                          # pair 0.42 / 0.52
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")                          # lone YES @0.42
    return yes


def resting(v, stage=None):
    return [o for k, o in v.orders.items() if o["open"] and
            (stage is None or v.stage.get(k) == stage)]


def completion(e):
    """The resting NO-side order of a long-YES market: the original opening
    partner kept by the recovery controller, or a re-placed completion."""
    return [o for o in e.b.m("aec-x")["orders"].values()
            if o.get("open") and o["side"] == "NO"]


def stages(e):
    mk = e.b.m("aec-x")
    return {oid: o.get("stage") for oid, o in mk["orders"].items()
            if o.get("open")}


def test_stranded_market_rests_completion_and_a_bounded_extend(ext):
    e, v = ext
    strand(e, v)
    assert completion(e)
    assert "two_sided_extend" in stages(e).values()
    ext_o = next(o for oid, o in e.b.m("aec-x")["orders"].items()
                 if o.get("open") and o["stage"] == "two_sided_extend")
    # below the 0.42 average by >= 3c, and a maker under the YES bid
    assert ext_o["side"] == "YES" and D(ext_o["px"]) <= D("0.39")
    assert D(ext_o["qty"]) == D("1")


def test_extend_fill_lowers_average_and_completion_resizes(ext):
    e, v = ext
    strand(e, v)
    mk = e.b.m("aec-x")
    eid = next(oid for oid, o in mk["orders"].items()
               if o.get("open") and o["stage"] == "two_sided_extend")
    v.orders[eid].update(filled=D("1"), open=False)
    for _ in range(4):
        e.step_market("aec-x")
    assert D(mk["pos"]["YES"]) == 2
    assert D(mk["cost"]["YES"]) / 2 < D("0.42")          # average came down
    comp = completion(e)
    assert comp and D(comp[0]["qty"]) - D(comp[0].get("filled") or 0) == 2
    # imbalance 2 is the cap: no third same-side add
    assert not any(o.get("open") and o["stage"] == "two_sided_extend"
                   for o in mk["orders"].values())


def test_completion_returns_market_to_new_pairs(ext):
    e, v = ext
    strand(e, v)
    mk = e.b.m("aec-x")
    cid = next(oid for oid, o in mk["orders"].items()
               if o.get("open") and o["side"] == "NO")
    v.orders[cid].update(filled=D("1"), open=False)
    for _ in range(3):
        e.step_market("aec-x")
    assert mk["pos"]["YES"] == mk["pos"]["NO"]
    opens = [o for o in mk["orders"].values() if o.get("open")]
    assert sorted(o["side"] for o in opens) == ["NO", "YES"]
    assert all(o["stage"] == "two_sided_open" for o in opens)  # new pair


def test_off_by_default_keeps_todays_behaviour(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RECOVERY_ON", True)
    assert M.EXTEND_ON is False
    v = FakeVenue()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    strand(e, v)
    assert "two_sided_extend" not in stages(e).values()


def test_extend_respects_per_market_dollar_cap(ext, monkeypatch):
    e, v = ext
    monkeypatch.setattr(M, "EXTEND_MAX_USD", D("0.50"))  # 0.42 + 0.39 > 0.50
    strand(e, v)
    assert "two_sided_extend" not in stages(e).values()


def test_extend_respects_daily_loss_stop_and_halts(ext, monkeypatch):
    e, v = ext
    e.select(v.account())
    e.step_market("aec-x")                          # pair posted
    monkeypatch.setattr(e, "daily_stopped", lambda: True)   # then stop trips
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")
    assert "two_sided_extend" not in stages(e).values()
    assert completion(e)                            # recovery still works


def test_extend_is_pulled_when_the_market_falls_through_it(ext):
    e, v = ext
    strand(e, v)
    v.bid, v.ask = D("0.30"), D("0.32")      # target drops to 0.28
    for _ in range(3):
        e.step_market("aec-x")
    ext_o = [o for o in e.b.m("aec-x")["orders"].values()
             if o.get("open") and o["stage"] == "two_sided_extend"]
    # re-set lower (or pulled) -- never left resting above the new target
    assert all(D(o["px"]) <= D("0.28") for o in ext_o)


def test_intl_guard_blocks_and_pulls_extend(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RECOVERY_ON", True)
    monkeypatch.setattr(M, "EXTEND_ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("12"))
    g = guard(0.45)
    v = FakeVenue()
    v.guard = g
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    strand(e, v)
    assert "two_sided_extend" in stages(e).values()
    move(g, 0.36)                            # intl: YES now worth ~0.36
    e._on_intl_change("aec-x")
    e.drain_intl_events()
    assert "two_sided_extend" not in stages(e).values()
    assert completion(e)                                 # completion kept
    e.step_market("aec-x")                   # and not re-placed while unsafe
    assert "two_sided_extend" not in stages(e).values()
