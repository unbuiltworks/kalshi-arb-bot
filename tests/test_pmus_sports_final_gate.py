"""The final order gate: every hard limit checked again, independently,
immediately before an order is sent.

Owner 2026-11-24: "Every order should independently pass the following
checks immediately before submission ... A violation should prevent
submission, not trigger cancellation after an unauthorized order has
already been placed." "the pair-cost check must not introduce forced
pairing or prevent economically justified asymmetric accumulation."
"Include outstanding and in-flight orders when calculating remaining
exposure." "Fail closed on missing or stale risk information."

Each test calls Engine.place directly, as a faulty decision layer would.
"""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

S1, S2 = "aec-atp-a-b-2026-11-25", "aec-atp-c-d-2026-11-25"


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    for mod, name, val in (
            (R, "QTY", D("1")), (R, "PAIR_CAP", D("0.97")),
            (R, "PER_MARKET_USD", D("8")), (R, "RESIDUAL_CAP_USD", D("3")),
            (R, "TOTAL_SPEND_USD", D("16")),
            (R, "TOTAL_UNMATCHED_USD", D("6")),
            (M, "RN1_PREGAME_QTY", None), (M, "RN1_LIVE_QTY", None),
            (M, "TOTAL_CAP", D("50")), (M, "MIN_TOUCH_QTY", D("0"))):
        monkeypatch.setattr(mod, name, val)
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.since = None
    return e, v


def sent(v):
    return [(o["slug"], o["side"], str(o["px"]), str(o["qty"]))
            for o in v.orders.values()]


def refusals(capsys):
    pre = "PMUS_TWO_SIDED "
    return [json.loads(l[len(pre):])["reason"]
            for l in capsys.readouterr().out.splitlines()
            if l.startswith(pre) and '"place_refused_final_gate"' in l]


def test_gate_is_on_by_default_and_taker_is_off():
    assert M.FINAL_GATE is True and M.ALLOW_TAKER is False


def test_an_ordinary_order_is_sent(g):
    e, v = g
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == [(S1, "YES", "0.47", "1")]


# ------------------------------------------------------------------- size
def test_an_oversize_order_is_never_sent(g, capsys):
    e, v = g
    e.place(S1, "YES", D("0.47"), D("2"), "rn1_open")
    e.place(S1, "YES", D("0.47"), D("50"), "rn1_extend")
    e.place(S1, "NO", D("0.40"), D("2"), "two_sided_complete")
    assert sent(v) == []
    assert all(r.startswith("size_above_maximum") for r in refusals(capsys))


def test_unreadable_orders_are_refused(g, capsys):
    e, v = g
    for px, q in (("0", "1"), ("1", "1"), ("1.2", "1"), ("0.5", "0"),
                  ("0.5", "-1"), ("NaN", "1"), ("0.5", "NaN")):
        e.place(S1, "YES", D(px), D(q), "rn1_open")
    assert sent(v) == []
    assert set(refusals(capsys)) == {"order_unreadable"}


# ------------------------------------------------------------------ maker
def test_a_taker_order_is_never_sent(g, capsys):
    e, v = g
    e.place(S1, "YES", D("0.50"), D("1"), "rn1_open", post_only=False)
    e.place(S1, "NO", D("0.52"), D("1"), "two_sided_exit", post_only=False)
    e.place(S1, "NO", D("0.52"), D("1"), "two_sided_complete_taker",
            post_only=False)
    assert sent(v) == []
    assert set(refusals(capsys)) == {"taker_not_authorized"}


# --------------------------------------------------------------- ceilings
def test_per_market_unpaired_ceiling_counts_resting_orders(g, capsys,
                                                           monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("1.00"))
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    e.place(S1, "YES", D("0.46"), D("1"), "rn1_extend")
    assert len(sent(v)) == 2                       # 0.93 resting
    e.place(S1, "YES", D("0.45"), D("1"), "rn1_extend")   # would be 1.38
    assert len(sent(v)) == 2
    assert refusals(capsys)[-1].startswith("per_market_unpaired")


def test_per_market_spend_ceiling(g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "PER_MARKET_USD", D("1.00"))
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert len(sent(v)) == 1          # 0.47 + reserve to complete 0.50
    e.place(S1, "YES", D("0.46"), D("1"), "rn1_extend")
    assert len(sent(v)) == 1
    assert refusals(capsys)[-1].startswith("per_market_spend")


def _campaign(e, slug):
    mk = e.b.m(slug)
    mk["orders"]["seed" + slug] = {"side": "YES", "px": "0.5", "qty": "1",
                                   "filled": "1", "open": False,
                                   "stage": "rn1_open",
                                   "placed_at": time.time()}
    return mk


def test_campaign_unpaired_ceiling_spans_markets(g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("1.00"))
    mk = _campaign(e, S2)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")   # held, unpaired
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")          # 0.60 + 0.47
    assert sent(v) == []
    assert refusals(capsys)[-1].startswith("campaign_unpaired")
    e.place(S1, "YES", D("0.39"), D("1"), "rn1_open")          # 0.99: fits
    assert len(sent(v)) == 1


def test_campaign_spend_ceiling_spans_markets(g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "TOTAL_SPEND_USD", D("2.00"))
    mk = _campaign(e, S2)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("1.20")
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert refusals(capsys)[-1].startswith("campaign_spend")


def test_two_orders_that_each_fit_cannot_both_be_sent(g, capsys,
                                                     monkeypatch):
    """Sent back to back in one pass, as the controller sends both sides
    and several markets: the second sees the first."""
    e, v = g
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("0.80"))
    _campaign(e, S1)
    _campaign(e, S2)
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    e.place(S2, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == [(S1, "YES", "0.47", "1")]
    assert refusals(capsys)[-1].startswith("campaign_unpaired")


def test_concurrent_submissions_cannot_exceed_a_ceiling_together(
        g, monkeypatch):
    """Eight threads each try one $0.47 order in a different market at the
    same moment; the campaign allows $1.00 unpaired. However they
    interleave, at most two can be sent."""
    import threading
    e, v = g
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("1.00"))
    slugs = ["aec-atp-m%d-x-2026-11-25" % i for i in range(8)]
    for s_ in slugs:
        _campaign(e, s_)
    lock = threading.Lock()
    real = e.place

    def one(s_):
        with lock:                      # the engine sends from ONE thread;
            real(s_, "YES", D("0.47"), D("1"), "rn1_open")   # see the note
    th = [threading.Thread(target=one, args=(s_,)) for s_ in slugs]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert len(sent(v)) == 2
    assert e.rn1_campaign_exposure()["unmatched"] <= D("1.00")


def test_partial_fills_are_counted_at_what_is_left(g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "QTY", D("2"))
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("1.00"))
    e.place(S1, "YES", D("0.47"), D("2"), "rn1_open")          # 0.94
    assert len(sent(v)) == 1
    mk = e.b.d["markets"][S1]
    oid = next(iter(mk["orders"]))
    mk["orders"][oid]["filled"] = "1"                 # one filled, one left
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.47")
    e.place(S1, "YES", D("0.05"), D("1"), "rn1_extend")   # 0.47+0.47+0.05
    assert len(sent(v)) == 2
    e.place(S1, "YES", D("0.05"), D("1"), "rn1_extend")   # 1.04 > 1.00
    assert len(sent(v)) == 2
    assert refusals(capsys)[-1].startswith("per_market_unpaired")


# -------------------------------------------------------------- pair cost
def test_a_completion_above_the_pair_cap_is_never_sent(g, capsys):
    e, v = g
    mk = e.b.m(S1)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")
    e.place(S1, "NO", D("0.38"), D("1"), "two_sided_complete")   # 0.98
    assert sent(v) == []
    assert refusals(capsys)[-1].startswith("pair_cost_above_cap")
    e.place(S1, "NO", D("0.37"), D("1"), "two_sided_complete")   # 0.97
    assert sent(v) == [(S1, "NO", "0.37", "1")]


def test_pair_cost_uses_the_average_of_partial_fills(g):
    e, v = g
    mk = e.b.m(S1)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("1.10")   # avg 0.55
    e.place(S1, "NO", D("0.43"), D("1"), "two_sided_complete")   # 0.98
    assert sent(v) == []
    e.place(S1, "NO", D("0.42"), D("1"), "two_sided_complete")   # 0.97
    assert len(sent(v)) == 1


def test_a_bid_cannot_pair_above_the_cap_with_a_resting_opposite_bid(
        g, capsys):
    e, v = g
    e.place(S1, "YES", D("0.50"), D("1"), "rn1_open")
    e.place(S1, "NO", D("0.48"), D("1"), "rn1_open")          # 0.98 if both
    assert sent(v) == [(S1, "YES", "0.50", "1")]
    assert refusals(capsys)[-1].startswith("pair_cost_above_cap")
    e.place(S1, "NO", D("0.47"), D("1"), "rn1_open")          # 0.97
    assert len(sent(v)) == 2


def test_the_pair_check_never_blocks_adding_to_the_heavier_side(g):
    """Asymmetric accumulation: holding YES at 0.60, another YES bid at any
    price is not a pair and the pair cap has nothing to say about it."""
    e, v = g
    mk = e.b.m(S1)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")
    e.place(S1, "YES", D("0.62"), D("1"), "rn1_extend")
    e.place(S1, "YES", D("0.70"), D("1"), "rn1_extend")
    assert [x[1] for x in sent(v)] == ["YES", "YES"]


def test_the_pair_check_never_forces_a_pair(g):
    """With a leg held and no acceptable completion price, nothing is sent
    on the other side and nothing else happens: no order appears."""
    e, v = g
    mk = e.b.m(S1)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")
    e.place(S1, "NO", D("0.45"), D("1"), "two_sided_complete")
    assert sent(v) == []


def test_a_completion_is_not_refused_by_the_exposure_ceilings(g, monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("0.10"))
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("0.10"))
    mk = e.b.m(S1)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")
    e.place(S1, "NO", D("0.37"), D("1"), "two_sided_complete")
    assert len(sent(v)) == 1


# ---------------------------------------------------------- fail closed
def test_an_error_inside_the_gate_refuses(g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(e, "rn1_campaign_exposure",
                        lambda exclude=None: 1 / 0)
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert refusals(capsys)[-1] == "gate_error:ZeroDivisionError"


def test_unreadable_book_figures_refuse(g, capsys):
    e, v = g
    mk = e.b.m(S1)
    mk["cost"]["YES"] = "not-a-number"
    # an earlier check may already stop it (by refusing or by raising,
    # which the loop records as a market error); either way nothing goes
    try:
        e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    except Exception:
        pass
    assert sent(v) == []
    # and the gate on its own refuses rather than passes
    assert str(e.final_gate(S1, "YES", D("0.47"), D("1"),
                            "rn1_open")).startswith("gate_error")


def test_a_stale_account_read_still_refuses_new_exposure(g, capsys):
    e, v = g
    e.require_fresh_account = True
    e._acct_ok_at = None
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert "place_refused_account_stale" in capsys.readouterr().out


def test_lock_halt_and_scope_still_refuse_before_the_gate(g, monkeypatch,
                                                          capsys):
    e, v = g
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV)
    monkeypatch.setattr(M.Engine, "shared_halt", lambda self: "HALT")
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    e.place(S1, "NO", D("0.37"), D("1"), "two_sided_complete")
    monkeypatch.setattr(M.Engine, "shared_halt", lambda self: None)
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    out = capsys.readouterr().out
    for k in ("place_refused_submission_lock", "place_refused_shared_halt",
              "place_refused_scope"):
        assert k in out


def test_the_gate_can_be_switched_off_only_explicitly(g, monkeypatch):
    e, v = g
    monkeypatch.setattr(M, "FINAL_GATE", False)
    e.place(S1, "YES", D("0.47"), D("2"), "rn1_open")
    assert len(sent(v)) == 1
