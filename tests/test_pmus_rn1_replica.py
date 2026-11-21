"""RN1 behavioural replica: the pure quoting rules, then the engine wiring."""
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

T = D("0.01")
CFG = {"qty": "5", "depth_ticks": 1, "px_min": "0.20", "px_max": "0.92",
       "pair_cap": "0.97", "residual_cap_usd": "3", "per_market_usd": "8",
       "extend_max_up": "0.02", "refill_cooldown_s": 60}
FLAT = {"YES": "0", "NO": "0"}


def qt(bid, ask, pos=FLAT, cost=FLAT, last=None, now=1000.0, **kw):
    return R.quotes(yes_bid=D(bid), yes_ask=D(ask), tick=T, pos=pos,
                    cost=cost, last_fill_t=last, now=now,
                    c=dict(CFG, **kw))


# ------------------------------------------------------------ pure rules
def test_flat_rests_maker_bids_on_both_outcomes_behind_the_touch():
    q = qt("0.48", "0.50")
    assert q["YES"]["px"] == D("0.47") and q["YES"]["role"] == "open"
    assert q["NO"]["px"] == D("0.49") and q["NO"]["role"] == "open"
    assert q["YES"]["qty"] == D("5") == q["NO"]["qty"]


def test_never_crosses_and_needs_a_two_sided_book():
    q = qt("0.48", "0.50", depth_ticks=0)
    assert q["YES"]["px"] == D("0.48")          # at the bid, not the ask
    assert R.quotes(yes_bid=D("0"), yes_ask=D("0.5"), tick=T, pos=FLAT,
                    cost=FLAT, c=CFG)["YES"] is None


def test_price_band_keeps_out_of_rn1_losing_cells():
    q = qt("0.10", "0.12", residual_cap_usd="10")   # YES 0.09 < 0.20
    assert q["YES"] is None and q["why_YES"] == "price_band"
    assert q["NO"]["px"] == D("0.87")               # NO side is in band
    q = qt("0.03", "0.05", residual_cap_usd="10")   # NO 0.94 > 0.92
    assert q["NO"] is None and q["why_NO"] == "price_band"


def test_a_single_opening_lot_must_fit_the_residual_cap():
    q = qt("0.10", "0.12")                          # NO 5 x 0.87 = 4.35 > 3
    assert q["NO"] is None and q["why_NO"] == "residual_cap"


def test_completion_only_at_a_profitable_pair_and_rests_deeper_until_then():
    # bought 5 YES at 0.60; NO touch is 0.39 -> 0.38 would make 0.98 > 0.97
    q = qt("0.60", "0.62", pos={"YES": "5", "NO": "0"},
           cost={"YES": "3.00", "NO": "0"})
    assert q["NO"]["role"] == "complete"
    assert q["NO"]["px"] == D("0.37")           # 0.60 + 0.37 = 0.97
    # market swings toward NO-cheap: completion follows the touch down
    q = qt("0.70", "0.72", pos={"YES": "5", "NO": "0"},
           cost={"YES": "3.00", "NO": "0"})
    assert q["NO"]["px"] == D("0.27")           # pair 0.87, under the cap


def test_completion_sized_to_the_gap_never_more_than_qty():
    q = qt("0.60", "0.62", pos={"YES": "12", "NO": "10"},
           cost={"YES": "7.2", "NO": "3.9"}, per_market_usd="20")
    assert q["NO"]["role"] == "complete" and q["NO"]["qty"] == D("2")


def test_extend_accumulates_bounded_by_residual_cap():
    # 5 YES @0.60 unpaired = $3.00 already at the cap: no extension
    q = qt("0.58", "0.60", pos={"YES": "5", "NO": "0"},
           cost={"YES": "3.00", "NO": "0"})
    assert q["YES"] is None and q["why_YES"] == "residual_cap"
    # 5 YES @0.30 unpaired = $1.50: one more 5-lot fits ($2.95 < $3)
    q = qt("0.30", "0.32", pos={"YES": "5", "NO": "0"},
           cost={"YES": "1.50", "NO": "0"}, residual_cap_usd="3")
    assert q["YES"]["role"] == "extend" and q["YES"]["px"] == D("0.29")


def test_extend_does_not_chase_the_price_up():
    q = qt("0.40", "0.42", pos={"YES": "5", "NO": "0"},
           cost={"YES": "1.50", "NO": "0"}, residual_cap_usd="10",
           per_market_usd="20")
    assert q["YES"]["px"] == D("0.32")          # avg 0.30 + 0.02


def test_replenish_waits_for_the_cooldown_after_a_fill():
    q = qt("0.30", "0.32", pos={"YES": "5", "NO": "0"},
           cost={"YES": "1.50", "NO": "0"}, last={"YES": 970.0}, now=1000.0)
    assert q["YES"] is None and q["why_YES"] == "refill_cooldown"
    assert q["NO"] is not None                   # completion never waits
    q = qt("0.30", "0.32", pos={"YES": "5", "NO": "0"},
           cost={"YES": "1.50", "NO": "0"}, last={"YES": 900.0}, now=1000.0)
    assert q["YES"] is not None


def test_per_market_cap_bounds_total_spend():
    q = qt("0.48", "0.50", pos={"YES": "10", "NO": "10"},
           cost={"YES": "4.7", "NO": "3.0"}, per_market_usd="8")
    assert q["YES"] is None and q["why_YES"] == "per_market_cap"


def test_never_a_sell_or_a_taker():
    for b, a in (("0.48", "0.50"), ("0.30", "0.31"), ("0.85", "0.86")):
        for pos, cost in ((FLAT, FLAT), ({"YES": "5", "NO": "0"},
                                         {"YES": "2", "NO": "0"})):
            q = qt(b, a, pos=pos, cost=cost)
            for s in ("YES", "NO"):
                if q[s]:
                    touch = R.side_bid(s, D(b), D(a))
                    assert q[s]["px"] <= touch and q[s]["qty"] > 0


def test_off_by_default():
    assert R.ON is False


# ------------------------------------------------------------ engine wiring
@pytest.fixture
def rn1(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    for k, val in CFG.items():
        monkeypatch.setattr(R, {"qty": "QTY", "depth_ticks": "DEPTH_TICKS",
                                "px_min": "PX_MIN", "px_max": "PX_MAX",
                                "pair_cap": "PAIR_CAP",
                                "residual_cap_usd": "RESIDUAL_CAP_USD",
                                "per_market_usd": "PER_MARKET_USD",
                                "extend_max_up": "EXTEND_MAX_UP",
                                "refill_cooldown_s": "REFILL_COOLDOWN_S"}[k],
                            val if isinstance(val, (int, float)) else D(val))
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.b.m("aec-x")
    return e, v


def live(v):
    return {o["side"]: (o["px"], o["qty"]) for o in v.orders.values()
            if o["open"]}


def test_engine_rests_both_sides_then_completes_profitably(rn1):
    e, v = rn1
    e.step_market("aec-x")
    assert live(v) == {"YES": (D("0.47"), D("5")), "NO": (D("0.49"), D("5"))}
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("5"), open=False)          # YES 5 @0.47
    e.step_market("aec-x")
    e.step_market("aec-x")
    lv = live(v)
    # NO is now the completion: 0.47 + 0.49 = 0.96 <= 0.97 -> keeps 0.49;
    # YES extension waits for the refill cool-down
    assert lv == {"NO": (D("0.49"), D("5"))}
    no = next(k for k, o in v.orders.items() if o["side"] == "NO" and o["open"])
    # the NO bid placed while flat is kept (already at the completion target)
    assert e.b.m("aec-x")["orders"][no]["stage"] == "rn1_open"
    v.orders[no].update(filled=D("5"), open=False)
    e.step_market("aec-x")
    assert e.b.d["pairs"][-1]["pair_cost"] == "0.96"


def test_engine_reprices_with_the_game_and_keeps_in_place_bids(rn1):
    e, v = rn1
    e.step_market("aec-x")
    n0 = v.n
    e.step_market("aec-x")                       # unchanged book: no churn
    assert v.n == n0
    v.bid, v.ask = D("0.55"), D("0.57")
    e.step_market("aec-x")
    assert live(v) == {"YES": (D("0.54"), D("5")), "NO": (D("0.42"), D("5"))}


def test_engine_submission_lock_refuses_every_rn1_order(rn1, monkeypatch,
                                                         capsys):
    monkeypatch.setenv("PMUS_TWO_SIDED_SUBMISSION_LOCK", "1")
    e, v = rn1
    e.step_market("aec-x")
    assert v.orders == {}
    assert "place_refused_submission_lock" in capsys.readouterr().out


def test_engine_new_exposure_gate_blocks_openings_not_completions(rn1,
                                                                  monkeypatch):
    e, v = rn1
    mk = e.b.m("aec-x")
    mk["pos"], mk["cost"] = {"YES": "5", "NO": "0"}, {"YES": "2.35", "NO": "0"}
    monkeypatch.setattr(e, "daily_stopped", lambda: True)
    e.step_market("aec-x")
    assert live(v) == {"NO": (D("0.49"), D("5"))}           # completion only


def test_engine_disabled_places_nothing_new(rn1, monkeypatch):
    monkeypatch.setenv(M.ENABLED_ENV, "NO")
    e, v = rn1
    e.step_market("aec-x")
    assert v.orders == {}


def test_pending_cancel_state_stays_open_so_a_racing_fill_is_recorded():
    st = M.Venue.status_from(type("X", (), {"micro": type("Y", (), {
        "authoritative_fill": staticmethod(lambda o, q, accept_partial: None)
    })()})(), {"state": "ORDER_STATE_PENDING_CANCEL", "quantity": "5"})
    assert st["open"] is True
    st = M.Venue.status_from(type("X", (), {"micro": type("Y", (), {
        "authoritative_fill": staticmethod(lambda o, q, accept_partial: None)
    })()})(), {"state": "ORDER_STATE_CANCELED", "quantity": "5"})
    assert st["open"] is False
