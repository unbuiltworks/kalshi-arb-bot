"""The queue measurement: timing, construction, and recovery.

Every test here pins a property the owner enumerated. Nothing widens an
existing bound: the $1 cap, the contract cap, the shadow lock, the halt latch
and the watchdog are untouched, and this module is INERT unless an experiment
and its market are both named.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_queue_measurement as QM


T_START = 1_792_098_000.0          # 21:00:00Z
T_END = T_START + 900              # 21:15:00Z
MKT = "cpc-btc-updown-15m-2026-10-15-2100z"

OK = dict(market=MKT, bid="0.60", ask="0.61", bid_depth="1000",
          tick="0.01", min_qty="0.01", qty_increment="0.01",
          book_age_s="0.05")


# ------------------------------------------------------------ inertness

def test_inert_unless_an_experiment_and_a_market_are_both_named(monkeypatch):
    monkeypatch.delenv(QM.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(QM.MARKET_ENV, raising=False)
    assert QM.active() is False
    monkeypatch.setenv(QM.EXPERIMENT_ENV, "QM-1")
    assert QM.active() is False               # market still unnamed
    monkeypatch.setenv(QM.MARKET_ENV, MKT)
    assert QM.active() is True
    assert QM.active("some-other-market") is False


# -------------------------------------------------------- deadline timing

def test_the_single_place_cutoff_is_the_earlier_of_the_two_bounds():
    assert QM.cancel_cutoff(T_END) == T_END - 180
    assert QM.place_cutoff(T_START, T_END) == T_START + 480     # 21:08:00Z
    # and it always leaves at least the minimum rest
    assert (QM.cancel_cutoff(T_END) - QM.place_cutoff(T_START, T_END)
            >= QM.MIN_REST_S)


def test_place_cutoff_takes_the_window_bound_when_the_window_is_short():
    short_end = T_START + 400          # a window shorter than the offset
    assert QM.place_cutoff(T_START, short_end) == short_end - 180 - 60


def test_t_cancel_is_measured_from_the_venue_acknowledgement():
    ack = T_START + 240
    assert QM.t_cancel(ack, T_END) == ack + 120                 # 21:06:00Z
    # a late ack is clamped by the window bound, never past it
    late = T_END - 200
    assert QM.t_cancel(late, T_END) == T_END - 180


def test_placement_is_refused_at_and_after_the_cutoff():
    cut = QM.place_cutoff(T_START, T_END)
    assert QM.may_place(cut - 1, T_START, T_END) is None
    assert QM.may_place(cut, T_START, T_END) == "AFTER_PLACE_CUTOFF"
    assert QM.may_place(cut + 300, T_START, T_END) == "AFTER_PLACE_CUTOFF"
    assert QM.may_place(T_START, T_START, T_END) == "BEFORE_PLACE_WINDOW"


def test_a_cancel_is_due_at_the_deadline_and_unknown_timing_is_overdue():
    rec = {"t_cancel": T_START + 360}
    assert QM.due(T_START + 359, rec) is False
    assert QM.due(T_START + 360, rec) is True
    assert QM.due(T_START, {"t_cancel": None}) is True          # unreadable
    assert QM.due(T_START, {"t_cancel": "nonsense"}) is True
    assert QM.due(T_START, None) is False                       # nothing acked
    assert QM.due(1e12, {"t_cancel": 1, "cancel_confirmed": True}) is False


# ------------------------------------------------- order construction

def test_one_maker_buy_up_at_the_best_bid():
    out = QM.build_order(**OK)
    assert out["ok"] is True
    i = out["intent"]
    assert (i["side"], i["action"]) == ("UP", "BUY")
    assert i["price"] == "0.60"                 # joins, never improves
    assert i["post_only"] is True
    assert i["mode"] == "MAKER_JOIN_BEST_BID"


def test_it_never_crosses_a_locked_or_crossed_book():
    for bid, ask in (("0.60", "0.60"), ("0.61", "0.60")):
        out = QM.build_order(**dict(OK, bid=bid, ask=ask))
        assert out["reason"] == "BOOK_CROSSED_OR_LOCKED"


@pytest.mark.parametrize("bid", ["0.19", "0.91", "0.01", "0.99"])
def test_the_price_band_is_enforced(bid):
    assert QM.build_order(**dict(OK, bid=bid, ask="0.995"))["reason"] \
        == "PRICE_OUT_OF_BAND"


def test_a_price_off_the_venue_tick_is_refused():
    out = QM.build_order(**dict(OK, bid="0.605", ask="0.61"))
    assert out["reason"] in ("PRICE_NOT_ON_TICK", "BOOK_CROSSED_OR_LOCKED")
    out = QM.build_order(**dict(OK, bid="0.605", ask="0.62"))
    assert out["reason"] == "PRICE_NOT_ON_TICK"


def test_fee_inclusive_sizing_never_exceeds_one_dollar_all_in():
    for cents in range(20, 91):
        p = D(cents) / 100
        out = QM.build_order(**dict(OK, bid=str(p),
                                    ask=str(p + D("0.01"))))
        assert out["ok"] is True, (p, out)
        qty = D(out["intent"]["qty"])
        allin = D(out["intent"]["all_in_usd"])
        assert allin <= QM.ALL_IN_CAP, (p, allin)
        assert qty <= QM.MAX_CONTRACTS
        # and it is strictly tighter than cash-only sizing
        assert qty <= (QM.ALL_IN_CAP / p)


def test_the_worst_case_taker_fee_is_charged_and_the_rebate_never_assumed():
    out = QM.build_order(**OK)
    i = out["intent"]
    fee = D(i["worst_case_fee_usd"])
    assert fee > 0                              # a rebate would be negative
    q, p = D(i["qty"]), D(i["price"])
    assert fee == QM.TAKER_THETA * q * p * (D(1) - p)


def test_the_venue_minimum_quantity_is_respected_and_refused_when_too_large():
    assert QM.build_order(**dict(OK, min_qty="1"))["ok"] is True
    out = QM.build_order(**dict(OK, bid="0.90", ask="0.91", min_qty="2"))
    assert out["reason"] == "NO_LEGAL_QUANTITY"
    assert "no fallback" in out["note"]


def test_insufficient_displayed_depth_is_refused():
    assert QM.build_order(**dict(OK, bid_depth="0"))["reason"] \
        == "NO_DISPLAYED_DEPTH"
    assert QM.build_order(**dict(OK, bid_depth=None))["reason"] \
        == "NO_DISPLAYED_DEPTH"
    assert QM.build_order(**dict(OK, bid_depth="0.005",
                                 min_qty="0.01"))["reason"] \
        == "DEPTH_BELOW_MIN_QTY"


def test_depth_caps_the_quantity_rather_than_being_ignored():
    out = QM.build_order(**dict(OK, bid="0.20", ask="0.21", bid_depth="1.5"))
    assert out["ok"] is True
    assert D(out["intent"]["qty"]) <= D("1.5")


def test_a_stale_or_untimed_book_is_refused():
    assert QM.build_order(**dict(OK, book_age_s="5"))["reason"] == "BOOK_STALE"
    assert QM.build_order(**dict(OK, book_age_s=None))["reason"] == "BOOK_STALE"


def test_a_suspected_reconnect_gap_is_refused():
    assert QM.build_order(**dict(OK, gap_suspected=True))["reason"] \
        == "GAP_SUSPECTED"


def test_a_one_sided_book_is_refused():
    assert QM.build_order(**dict(OK, ask=None))["reason"] \
        == "BOOK_NOT_TWO_SIDED"


def test_unknown_tick_or_minimum_is_refused_never_defaulted():
    assert QM.build_order(**dict(OK, tick=None))["reason"] == "TICK_UNKNOWN"
    assert QM.build_order(**dict(OK, min_qty=None))["reason"] \
        == "MIN_QTY_UNKNOWN"


# ------------------------------------------- durable ack and recovery

def test_the_ack_is_persisted_before_the_deadline_is_usable(tmp_path):
    s = QM.DeadlineStore(str(tmp_path / "qm.json"))
    ack = T_START + 240
    rec = s.record_ack(experiment="QM-1", market=MKT, venue_order_id="V1",
                       t_ack=ack, t_end=T_END, price="0.60", qty="1.62")
    assert rec["t_cancel"] == ack + 120
    # durable: a brand-new store on the same path sees it
    assert QM.DeadlineStore(str(tmp_path / "qm.json")).order()["venue_order_id"] \
        == "V1"


def test_an_ack_without_a_venue_order_id_or_time_is_refused(tmp_path):
    s = QM.DeadlineStore(str(tmp_path / "qm.json"))
    with pytest.raises(ValueError):
        s.record_ack(experiment="e", market=MKT, venue_order_id="  ",
                     t_ack=T_START, t_end=T_END, price="0.6", qty="1")
    with pytest.raises(ValueError):
        s.record_ack(experiment="e", market=MKT, venue_order_id="V1",
                     t_ack=0, t_end=T_END, price="0.6", qty="1")
    assert s.order() is None


def test_restart_recovers_the_deadline_and_demands_reconciliation(tmp_path):
    p = str(tmp_path / "qm.json")
    s = QM.DeadlineStore(p)
    s.record_ack(experiment="QM-1", market=MKT, venue_order_id="V1",
                 t_ack=T_START + 240, t_end=T_END, price="0.60", qty="1.62")
    fresh = QM.DeadlineStore(p)                  # the restarted process
    out = QM.recovery_refusal(fresh)
    assert out["halt"] is False
    assert out["reason"] == "RECOVERED_PENDING_CANCEL"
    assert out["venue_order_id"] == "V1"
    assert out["t_cancel"] == T_START + 360
    assert "proved by re-read" in out["note"]


def test_restart_with_nothing_acknowledged_is_clean(tmp_path):
    assert QM.recovery_refusal(QM.DeadlineStore(str(tmp_path / "x.json"))) \
        is None


def test_an_unreadable_record_halts_rather_than_reading_as_absent(tmp_path):
    p = tmp_path / "qm.json"
    p.write_text("{not json")
    s = QM.DeadlineStore(str(p))
    assert s.unreadable() is True
    out = QM.recovery_refusal(s)
    assert out["halt"] is True
    assert out["reason"] == "DEADLINE_RECORD_UNREADABLE"


def test_an_ambiguous_identity_or_time_halts_after_restart(tmp_path):
    for bad, reason in (({"venue_order_id": "", "t_ack": 1.0},
                         "ACK_IDENTITY_UNCERTAIN"),
                        ({"venue_order_id": "V1", "t_ack": 0},
                         "ACK_TIME_UNCERTAIN")):
        s = QM.DeadlineStore(str(tmp_path / ("q%s.json" % reason)))
        s.state = {"order": dict(bad, cancel_confirmed=False)}
        out = QM.recovery_refusal(s)
        assert out["halt"] is True and out["reason"] == reason


# ------------------------------------------- cancellation confirmation

def test_only_a_terminal_state_with_zero_remaining_confirms(tmp_path):
    s = QM.DeadlineStore(str(tmp_path / "qm.json"))
    s.record_ack(experiment="QM-1", market=MKT, venue_order_id="V1",
                 t_ack=T_START + 240, t_end=T_END, price="0.60", qty="1.62")
    assert s.confirm_cancel(terminal=True, remaining="0.5") is False   # partial
    assert s.confirm_cancel(terminal=False, remaining="0") is False    # working
    assert s.confirm_cancel(terminal=True, remaining="junk") is False  # unknown
    assert s.order()["cancel_confirmed"] is False
    assert QM.due(1e12, s.order()) is True          # still owed a cancel
    assert s.confirm_cancel(terminal=True, remaining="0") is True
    assert s.order()["cancel_confirmed"] is True
    assert QM.due(1e12, s.order()) is False


def test_a_partial_fill_leaves_the_cancel_owed_and_survives_restart(tmp_path):
    p = str(tmp_path / "qm.json")
    s = QM.DeadlineStore(p)
    s.record_ack(experiment="QM-1", market=MKT, venue_order_id="V1",
                 t_ack=T_START + 240, t_end=T_END, price="0.60", qty="1.62")
    s.confirm_cancel(terminal=False, remaining="1.12")     # 0.50 filled
    out = QM.recovery_refusal(QM.DeadlineStore(p))
    assert out["halt"] is False
    assert out["reason"] == "RECOVERED_PENDING_CANCEL"


def test_a_cancellation_failure_never_retires_the_order(tmp_path):
    """An accepted cancel request is not a cancelled order."""
    s = QM.DeadlineStore(str(tmp_path / "qm.json"))
    s.record_ack(experiment="QM-1", market=MKT, venue_order_id="V1",
                 t_ack=T_START + 240, t_end=T_END, price="0.60", qty="1.62")
    # the venue said 200 to the cancel, then the re-read still shows working
    assert s.confirm_cancel(terminal=False, remaining="1.62") is False
    assert QM.due(1e12, s.order()) is True
    assert QM.recovery_refusal(QM.DeadlineStore(
        str(tmp_path / "qm.json")))["reason"] == "RECOVERED_PENDING_CANCEL"


# --------------------------------------- wiring: inert, shipped, guarded

def test_the_runner_hook_is_inert_unless_the_experiment_is_named(monkeypatch,
                                                                 tmp_path):
    """The shadow-locked service running today must not execute a line of
    the measurement. The hook returns before touching anything."""
    from src import l5_pmus_live_runner as RUN
    monkeypatch.delenv(QM.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(QM.MARKET_ENV, raising=False)

    class _T:
        market_slug = MKT
        armed = False

    class _R:
        maybe_qm_deadline = RUN.Runner.maybe_qm_deadline
        qm_store = RUN.Runner.qm_store
        root = str(tmp_path)
        transport = _T()

        def poll_orders(self):                      # must never be called
            raise AssertionError("the inert hook polled the venue")

    assert _R().maybe_qm_deadline(1e12) is None
    # and no record file was created
    assert not (tmp_path / "queue_measurement.json").exists()


def test_the_module_ships_in_the_executor_image():
    """A module the submit path needs but the image lacks crash-loops the
    executor. That has happened once; this is the guard."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    df = (root / "deploy" / "l5_pmus_live" / "Dockerfile").read_text()
    assert "COPY src/l5_queue_measurement.py" in df


def test_the_deadline_hook_is_called_from_the_tick():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.tick)
    assert "maybe_qm_deadline" in src
