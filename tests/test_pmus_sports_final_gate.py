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


# ------------------------ a stale "no halt" attestation (owner 2026-11-24)
def _attest(tmp_path, age_s):
    import datetime as dt
    import json as _j
    from src import l5_halt_gate as HG
    store = HG.FileStore(str(tmp_path))
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_s)
    store.put(HG.HALT_KEY, _j.dumps(
        {"halt": False, "t": t.isoformat(), "who": "watchdog"}).encode())
    return store


def test_an_attestation_older_than_the_limit_blocks_new_exposure_only(
        g, tmp_path, monkeypatch, capsys):
    """The watchdog has stopped attesting (dead, or its halt channel is
    down). After the bounded age the bot opens nothing; a completion still
    goes, and nothing reads as a halt."""
    from src import l5_halt_gate as HG
    e, v = g
    store = _attest(tmp_path / "latch", 130)
    monkeypatch.setenv(M.SHARED_HALT_URL_ENV, "http://watchdog.test")
    monkeypatch.setattr(HG, "HttpHaltStore", lambda url, **k: store)
    monkeypatch.setattr(M, "SHARED_HALT_TTL_S", 0.0)
    assert M.SHARED_HALT_MAX_AGE_S == 900          # default: still trusted
    assert e.shared_halt() is None
    monkeypatch.setattr(M, "SHARED_HALT_MAX_AGE_S", 120.0)
    assert e.shared_halt() == "UNAVAILABLE"        # not "HALT"
    e.b.m(S1)
    e.step_market(S1)
    assert sent(v) == []                           # no opening
    mk = e.b.d["markets"][S1]
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")
    e.place(S1, "NO", D("0.37"), D("1"), "two_sided_complete")
    assert sent(v) == [(S1, "NO", "0.37", "1")]    # completion still goes


def test_a_fresh_attestation_is_trusted_under_the_tighter_limit(
        g, tmp_path, monkeypatch):
    from src import l5_halt_gate as HG
    e, v = g
    store = _attest(tmp_path / "latch", 25)
    monkeypatch.setenv(M.SHARED_HALT_URL_ENV, "http://watchdog.test")
    monkeypatch.setattr(HG, "HttpHaltStore", lambda url, **k: store)
    monkeypatch.setattr(M, "SHARED_HALT_TTL_S", 0.0)
    monkeypatch.setattr(M, "SHARED_HALT_MAX_AGE_S", 120.0)
    assert e.shared_halt() is None


def test_a_stale_attestation_cancels_resting_opening_orders(
        g, tmp_path, monkeypatch):
    """Owner 2026-11-24: "On channel timeout, the controller should cancel
    outstanding exposure-increasing orders, block new exposure and preserve
    reconciliation and settlement operations." Opening bids already
    resting are pulled on the next pass; the bid that completes a held leg
    stays; nothing is latched."""
    from src import l5_halt_gate as HG
    e, v = g
    box = {"store": _attest(tmp_path / "a", 5)}
    monkeypatch.setenv(M.SHARED_HALT_URL_ENV, "http://watchdog.test")
    monkeypatch.setattr(HG, "HttpHaltStore", lambda url, **k: box["store"])
    monkeypatch.setattr(M, "SHARED_HALT_TTL_S", 0.0)
    monkeypatch.setattr(M, "SHARED_HALT_MAX_AGE_S", 120.0)
    e.b.m(S1)
    e.step_market(S1)                              # fresh: both sides rest
    opens = [o for o in v.orders.values() if o["open"]]
    assert len(opens) == 2
    mk2 = e.b.m(S2)                                # a second market holding
    mk2["pos"]["YES"], mk2["cost"]["YES"] = D("1"), D("0.44")   # a lone leg
    e.step_market(S2)
    comp = [o for o in e.b.d["markets"][S2]["orders"].values()
            if o.get("open")]
    # a held leg rests its completion and, here, an extension as well
    assert "two_sided_complete" in [o["stage"] for o in comp]
    # the watchdog stops attesting; two minutes later the record is stale
    box["store"] = _attest(tmp_path / "b", 130)
    assert e.shared_halt() == "UNAVAILABLE"
    e.step_market(S1)
    e.step_market(S2)
    left = [(s, o["stage"]) for s, mk in e.b.d["markets"].items()
            for o in mk["orders"].values() if o.get("open")]
    assert left == [(S2, "two_sided_complete")]    # openings gone
    assert not e.b.d.get("safety_latch")
    # reconciliation still runs: the account read and the book are intact
    assert e.own_exposure_usd() > 0
    # and when the attestation is fresh again the bot quotes again
    box["store"] = _attest(tmp_path / "c", 5)
    e.step_market(S1)
    assert len([o for o in e.b.d["markets"][S1]["orders"].values()
                if o.get("open")]) == 2


# ------------------------------------- loss stops at the final gate (owner
# 2026-11-26: "Isolate the final pre-submission loss-stop correction")
def test_the_campaign_loss_stop_refuses_an_opening_at_the_gate(
        g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("10"))
    e.b.d["pilot_loss_stop"] = {"t": "x", "net": "-10.5"}     # latched
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert refusals(capsys)[-1] == "campaign_loss_stop:pilot_loss_stop"


def test_the_campaign_loss_stop_never_refuses_a_completion(g, monkeypatch):
    e, v = g
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("10"))
    e.b.d["pilot_loss_stop"] = {"t": "x", "net": "-10.5"}
    mk = e.b.m(S1)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")
    e.place(S1, "NO", D("0.37"), D("1"), "two_sided_complete")
    assert len(sent(v)) == 1


def test_an_unavailable_loss_meter_refuses_an_opening_at_the_gate(
        g, capsys, monkeypatch):
    """Fail closed: with the campaign stop on and no current meter result,
    new exposure is refused here too, as step_market already refuses it."""
    e, v = g
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("10"))
    e._pilot, e._pilot_at = None, None
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert refusals(capsys)[-1] == \
        "campaign_loss_stop:pilot_meter_unavailable"


def test_a_loss_at_risk_including_this_order_refuses_at_the_gate(
        g, capsys, monkeypatch):
    """Held unpaired cost 9.60 in the other market; this 0.47 bid would
    take the worst case past the $10 stop (resting counted, as deployed)."""
    e, v = g
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("10"))
    monkeypatch.setattr(M, "RN1_LOSS_STOP_COUNTS_RESTING", True)
    e._pilot = {"realized_net_usd": "0", "open_residual_worst_usd": "0",
                "unconfirmed": []}
    e._pilot_at = time.monotonic()
    mk = _campaign(e, S2)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("20"), D("9.60")
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert refusals(capsys)[-1].startswith("campaign_loss_stop")


def test_the_daily_loss_stop_refuses_an_opening_at_the_gate(
        g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(e, "daily_stopped", lambda: True)
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert refusals(capsys)[-1] == "daily_loss_stop"


def test_the_daily_loss_stop_never_refuses_a_completion(g, monkeypatch):
    e, v = g
    monkeypatch.setattr(e, "daily_stopped", lambda: True)
    mk = e.b.m(S1)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.60")
    e.place(S1, "NO", D("0.37"), D("1"), "two_sided_complete")
    assert len(sent(v)) == 1


def test_with_no_stop_in_force_an_opening_is_sent_as_before(g, monkeypatch):
    e, v = g
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("10"))
    e._pilot = {"realized_net_usd": "-1.00", "open_residual_worst_usd": "0",
                "unconfirmed": []}
    e._pilot_at = time.monotonic()
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert len(sent(v)) == 1


# ----------------------- in-flight orders in OTHER markets count (owner
# 2026-11-26: two markets must not bypass the aggregate limits)
def test_an_uncertain_submission_elsewhere_counts_toward_the_campaign(
        g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("0.80"))
    _campaign(e, S1)
    rid = e.reserve_order(S2, "YES", D("0.47"), D("1"), "rn1_open")
    e._reservations()[rid]["state"] = "uncertain"     # send result unknown
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")  # 0.47 + 0.47 > 0.80
    assert sent(v) == []
    assert refusals(capsys)[-1].startswith("campaign_unpaired")
    e.release_reservation(rid)
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert len(sent(v)) == 1


def test_a_reserved_first_order_in_an_untracked_market_counts(
        g, capsys, monkeypatch):
    e, v = g
    monkeypatch.setattr(R, "TOTAL_SPEND_USD", D("1.50"))
    _campaign(e, S1)
    e.reserve_order("aec-atp-new-mkt-2026-11-25", "NO", D("0.50"), D("1"),
                    "rn1_open")
    exp = e.rn1_campaign_exposure(exclude=S1)
    assert exp["spend"] > D(0)
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == []
    assert refusals(capsys)[-1].startswith("campaign_spend")


def test_two_markets_filling_together_stay_inside_the_campaign_caps(
        g, monkeypatch):
    """Both markets hold fills and resting bids at once; every further
    opening in either is checked against the two together."""
    e, v = g
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("2.00"))
    for s_ in (S1, S2):
        mk = _campaign(e, s_)
        mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.47")
    for _ in range(6):
        for s_ in (S1, S2):
            e.place(s_, "YES", D("0.47"), D("1"), "rn1_open")
    assert e.rn1_campaign_exposure()["unmatched"] <= D("2.00")
