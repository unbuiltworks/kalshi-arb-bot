"""Consecutive campaign windows on verified exposure, a per-window order
ceiling, and unsettled windows counted at their worst case.

2026-11-21: one busy window spent 47 of the campaign's 60 orders, and every
second window was sat out because the one before it had not settled.
"""
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_behaviour_probe as P
from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as RUN
from tests.test_l5_btc5m_hold_release import (          # noqa: F401
    T0, armed, live, rig, wander)
from tests.test_l5_btc5m_probe_campaign import (
    CAMP, M1, M2, M3, NOW, own, recon, risk)

UTC = dt.timezone.utc


def _reasons(steps):
    return {r for s in steps for r in s["refused"]}


# ------------------------------------------------ per-window order ceiling

def test_unset_there_is_no_per_window_ceiling(monkeypatch):
    monkeypatch.delenv(X.WINDOW_ORDER_CAP_ENV, raising=False)
    assert X.window_order_cap() == (None, None)


@pytest.mark.parametrize("raw", ["0", "-3", "many", "4.5"])
def test_a_ceiling_that_cannot_be_read_refuses(monkeypatch, raw):
    monkeypatch.setenv(X.WINDOW_ORDER_CAP_ENV, raw)
    assert X.window_order_cap() == (None, "WINDOW_ORDER_CAP_UNREADABLE")


def test_the_ceiling_stops_risk_adding_orders_at_its_count(rig, monkeypatch):
    monkeypatch.setenv(X.WINDOW_ORDER_CAP_ENV, "4")
    steps = wander(rig, 8)
    assert len(rig.venue.sent) == 4
    assert "WINDOW_ORDER_CAP" in _reasons(steps)


def test_without_the_ceiling_the_same_window_sends_more(rig, monkeypatch):
    monkeypatch.delenv(X.WINDOW_ORDER_CAP_ENV, raising=False)
    wander(rig, 8)
    assert len(rig.venue.sent) > 4


def test_an_unreadable_ceiling_sends_nothing_risk_adding(rig, monkeypatch):
    monkeypatch.setenv(X.WINDOW_ORDER_CAP_ENV, "lots")
    steps = wander(rig, 4)
    assert rig.venue.sent == []
    assert "WINDOW_ORDER_CAP_UNREADABLE" in _reasons(steps)


def test_a_reducing_order_is_never_refused_by_the_ceiling(rig, monkeypatch):
    """The ceiling is consulted only for an order that does NOT reduce
    unmatched inventory."""
    monkeypatch.setenv(X.WINDOW_ORDER_CAP_ENV, "1")
    wander(rig, 3)
    assert len(rig.venue.sent) == 1
    asked = []
    real = rig.ex._reduces_exposure
    monkeypatch.setattr(rig.ex, "_reduces_exposure",
                        lambda m, s, q: asked.append(1) or True)
    steps = wander(rig, 2, start=3)
    assert asked and "WINDOW_ORDER_CAP" not in _reasons(steps)
    assert real is not None


# ----------------------------- unsettled windows count at their worst case

class _Inv:
    """An executor reduced to what `pending_worst_case_usd` reads."""

    def __init__(self, book):
        self.book = book

    def pending_settlement(self):
        return list(self.book)

    def inventory(self, m):
        u, uc, d, dc = (D(x) for x in self.book[m])
        return {C.UP: {"qty": u, "cost": uc}, C.DOWN: {"qty": d, "cost": dc}}


def worst(book, exclude=None):
    return X.Btc5mExecutor.pending_worst_case_usd(_Inv(book), exclude)


def test_worst_case_is_cost_less_the_pairs_that_always_redeem():
    #: 16:30Z: UP 0.50 for 0.2159, DOWN 0.45 for 0.2537 -> 0.4696 - 0.45
    assert worst({M1: ("0.50", "0.2159", "0.45", "0.2537")}) == D("0.0196")
    #: 16:40Z: fully matched above 1.00 -> the locked loss
    assert worst({M1: ("0.31", "0.1918", "0.31", "0.1471")}) == D("0.0289")
    #: one-sided: the whole cost
    assert worst({M1: ("0.20", "0.12", "0", "0")}) == D("0.12")
    #: a pair bought under 1.00 cannot lose: never negative
    assert worst({M1: ("0.10", "0.04", "0.10", "0.05")}) == D("0")


def test_the_window_being_traded_is_excluded_and_others_add_up():
    book = {M1: ("0.20", "0.12", "0", "0"), M2: ("0", "0", "0.10", "0.06")}
    assert worst(book) == D("0.18")
    assert worst(book, exclude=M2) == D("0.12")


def test_an_unsettled_loss_comes_off_the_campaign_room(rig, monkeypatch):
    """With the room nearly gone, an earlier unsettled window's worst case
    is enough to refuse."""
    monkeypatch.setattr(type(rig.ex), "pending_worst_case_usd",
                        lambda self, exclude=None: D("0.70"))
    steps = wander(rig, 3)
    assert rig.venue.sent == []
    assert ("CAMPAIGN_LOSS_HALT:UNSETTLED_WORST_CASE_EXHAUSTS_ROOM"
            in _reasons(steps))


def test_a_small_unsettled_loss_only_lowers_the_cap(rig, monkeypatch):
    monkeypatch.setattr(type(rig.ex), "pending_worst_case_usd",
                        lambda self, exclude=None: D("0.02"))
    assert sum(s["placed"] for s in wander(rig, 3)) > 0


# --------------------------------------------- the switch, and the deadline

def test_overlap_is_off_unless_the_exact_token(monkeypatch):
    for raw in ("", "ON", "1", "one_verified_unsettled_window"):
        monkeypatch.setenv(RUN.CAMPAIGN_OVERLAP_ENV, raw)
        assert RUN.campaign_overlap() is False
    monkeypatch.setenv(RUN.CAMPAIGN_OVERLAP_ENV, RUN.CAMPAIGN_OVERLAP_TOKEN)
    assert RUN.campaign_overlap() is True


@pytest.fixture
def overlap(monkeypatch):
    monkeypatch.setenv(RUN.CAMPAIGN_OVERLAP_ENV, RUN.CAMPAIGN_OVERLAP_TOKEN)
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")


def test_off_there_is_still_no_grace(monkeypatch):
    monkeypatch.delenv(RUN.CAMPAIGN_OVERLAP_ENV, raising=False)
    r = recon(own({M3: "-0.04"}, {M3: 30}), risk({}))
    assert r["ok"] is False and r["grace_s"] == 0


def test_on_a_fresh_settlement_is_given_until_the_deadline(overlap):
    o = own({M3: "-0.0408"}, {M3: 120})
    r = recon(o, risk({M3: "-0.02"}))           # still at the first booking
    assert r["ok"] is True and r["within_grace"] == [M3]
    assert r["grace_s"] == RUN.CAMPAIGN_RECON_DEADLINE_S == 600


def test_on_past_the_deadline_an_unreconciled_window_refuses(overlap):
    for booked in ({M3: "-0.02"}, {}):
        r = recon(own({M3: "-0.0408"}, {M3: 601}), risk(booked))
        assert r["ok"] is False and M3 in r["failed"]


def test_on_reconciled_windows_pass_whatever_their_age(overlap):
    r = recon(own({M1: "0.0195", M3: "-0.0408"}, {M1: 9000, M3: 700}),
              risk({M1: "0.0195", M3: "-0.0408"}))
    assert r["ok"] is True


# ------------------------------------------- the overlap gate in a session

class _T:
    def __init__(self, rows=None, terminal=False, fail=False):
        self.rows, self.terminal, self.fail = rows, terminal, fail
        self.reads = 0

    def positions(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("venue")
        return list(self.rows or [])

    def settlement(self, m):
        return {"terminal": self.terminal, "settlement_px": "1"}


class _Ex:
    def __init__(self, pending, legs=("0.31", "0.31"), live=()):
        self._pending, self._legs, self._live = list(pending), legs, live

    def pending_settlement(self):
        return list(self._pending)

    def live_orders(self):
        return {"c%d" % i: {"market": m} for i, m in enumerate(self._live)}

    def inventory(self, m):
        return {C.UP: {"qty": D(self._legs[0]), "cost": D("0.19")},
                C.DOWN: {"qty": D(self._legs[1]), "cost": D("0.14")}}


def row(m, up="0.3100", dn="0.3100"):
    return {"marketSlug": m, "qtyBoughtDecimal": up, "qtySoldDecimal": dn,
            "netPositionDecimal": str(D(up) - D(dn))}


def session(ex, t, market=M2):
    s = RUN.Session.__new__(RUN.Session)
    s.ex, s.t, s.market = ex, t, market
    s.halt_fn = lambda: {"ok": True}
    s._own_unaccounted = lambda: RUN.OwnSettled.of({}, {})
    s._now = lambda: NOW
    s.risk_fn = lambda now, o: {"ok": True, "reason": None,
                                "loss_room_usd": "0.95"}
    s.exposure_ceiling = lambda: D("0.60")
    s._log = lambda k, d: None
    s.clock = lambda: 1000.0
    return s


def test_a_verified_unsettled_window_no_longer_stops_the_next(overlap):
    t = _T([row(M1)])
    s = session(_Ex([M1]), t)
    assert s.gates()["ok"] is True
    assert s.gates()["ok"] is True
    assert t.reads == 1                      # verified once per window


def test_off_the_same_situation_still_waits(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    monkeypatch.delenv(RUN.CAMPAIGN_OVERLAP_ENV, raising=False)
    g = session(_Ex([M1]), _T([row(M1)])).gates()
    assert g["reason"] == "CAMPAIGN_PREVIOUS_WINDOW_NOT_SETTLED"


@pytest.mark.parametrize("ex,t,reason", [
    (_Ex([M1, M3]), _T([row(M1)]), "CAMPAIGN_TOO_MANY_UNSETTLED_WINDOWS"),
    (_Ex([M1], live=[M1]), _T([row(M1)]),
     "CAMPAIGN_PREVIOUS_WINDOW_HAS_OPEN_ORDERS"),
    (_Ex([M1]), _T([row(M1, up="0.3000")]),
     "CAMPAIGN_PREVIOUS_WINDOW_DISAGREES_WITH_VENUE"),
    (_Ex([M1]), _T([row(M1, dn="0.3900")]),
     "CAMPAIGN_PREVIOUS_WINDOW_DISAGREES_WITH_VENUE"),
    (_Ex([M1]), _T([{"marketSlug": M1}]),
     "CAMPAIGN_PREVIOUS_WINDOW_VENUE_ROW_UNREADABLE"),
    (_Ex([M1]), _T([]), "CAMPAIGN_PREVIOUS_WINDOW_NOT_VERIFIABLE"),
    (_Ex([M1]), _T(fail=True), "CAMPAIGN_PREVIOUS_WINDOW_READ_FAILED"),
])
def test_each_failed_verification_sits_the_window_out(overlap, ex, t, reason):
    s = session(ex, t)
    assert s.gates()["reason"] == reason
    ex._pending, ex._live = [], ()           # it clears a moment later
    assert s.gates() == {"ok": False, "reason": "CAMPAIGN_WINDOW_SAT_OUT",
                         "first_refusal": reason}


def test_no_venue_row_is_accepted_only_if_the_venue_says_settled(overlap):
    assert session(_Ex([M1]), _T([], terminal=True)).gates()["ok"] is True


def test_the_market_being_traded_does_not_need_verifying(overlap):
    t = _T(fail=True)
    assert session(_Ex([M2]), t).gates()["ok"] is True
    assert t.reads == 0


# ------------------------------------------------ mid-window settlement

class _SettleEx(_Ex):
    def __init__(self, pending):
        super().__init__(pending)
        self.settled, self.synced = [], 0

    def settle(self, m, up_won):
        self.settled.append((m, up_won))
        self._pending.remove(m)
        return {"settlement_pnl": "-0.0289"}

    def sync_reservation(self):
        self.synced += 1
        return {"ok": True}


def test_an_earlier_window_is_settled_during_the_next_one(overlap):
    ex, t = _SettleEx([M1, M2]), _T(terminal=True)
    s = session(ex, t)
    #: the venue settles the earlier market; the current one is not final
    t.settlement = lambda m: {"terminal": m == M1, "settlement_px": "1"}
    out = s.settle_earlier_windows(1000.0)
    assert ex.settled == [(M1, True)] and ex.synced == 1
    assert out["settled"][0]["market"] == M1
    #: paced: nothing again inside the interval, and nothing left to do after
    assert s.settle_earlier_windows(1005.0) is None
    assert s.settle_earlier_windows(1000.0 + RUN.MIDWINDOW_SETTLE_S) is None


def test_mid_window_settlement_is_off_without_the_switch(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    monkeypatch.delenv(RUN.CAMPAIGN_OVERLAP_ENV, raising=False)
    ex = _SettleEx([M1, M2])
    assert session(ex, _T(terminal=True)).settle_earlier_windows(1.0) is None
    assert ex.settled == []


def test_a_failing_settlement_read_never_raises(overlap):
    ex = _SettleEx([M1, M2])
    ex.pending_settlement = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    assert session(ex, _T()).settle_earlier_windows(1.0) == {
        "error": "RuntimeError"}


# -------------- the venue dropped its row before reporting the settlement

@pytest.fixture(autouse=True)
def _clean_close_record():
    RUN.CLOSE_VERIFIED.clear()
    yield
    RUN.CLOSE_VERIFIED.clear()


def legs(up="0.31", dn="0.31"):
    return {C.UP: {"qty": D(up), "cost": D("0.19")},
            C.DOWN: {"qty": D(dn), "cost": D("0.14")}}


def close_recon(up="0.3100", dn="0.3100", agrees=True, read=True, rows=None):
    return {"read": read, "agrees": agrees,
            "venue_rows": [row(M1, up, dn)] if rows is None else rows}


def test_18_40z_the_row_is_gone_but_this_process_verified_it_at_close(
        overlap):
    assert RUN.note_close_verification(M1, close_recon(), legs(), 990.0)
    s = session(_Ex([M1]), _T([], terminal=False))
    assert s.gates()["ok"] is True


def test_without_a_close_record_the_same_state_still_sits_out(overlap):
    g = session(_Ex([M1]), _T([], terminal=False)).gates()
    assert g["reason"] == "CAMPAIGN_PREVIOUS_WINDOW_NOT_VERIFIABLE"


@pytest.mark.parametrize("recon_,lg", [
    (close_recon(agrees=False), legs()),
    (close_recon(read=False), legs()),
    (close_recon(up="0.3000"), legs()),            # a leg differed at close
    (close_recon(dn="0.3900"), legs()),
    (close_recon(rows=[]), legs()),                 # no row while holding
    (close_recon(rows=[row(M1), row(M1)]), legs()),
    (close_recon(rows=[{"marketSlug": M1}]), legs()),
    (None, legs()),
])
def test_only_an_agreement_on_both_legs_is_recorded(overlap, recon_, lg):
    assert RUN.note_close_verification(M1, recon_, lg, 990.0) is None
    assert M1 not in RUN.CLOSE_VERIFIED
    g = session(_Ex([M1]), _T([], terminal=False)).gates()
    assert g["reason"] == "CAMPAIGN_PREVIOUS_WINDOW_NOT_VERIFIABLE"


def test_a_close_record_older_than_its_limit_is_not_accepted(overlap):
    RUN.note_close_verification(
        M1, close_recon(), legs(),
        1000.0 - RUN.CLOSE_VERIFIED_MAX_AGE_S - 1)
    g = session(_Ex([M1]), _T([], terminal=False)).gates()
    assert g["reason"] == "CAMPAIGN_PREVIOUS_WINDOW_NOT_VERIFIABLE"


def test_a_ledger_that_changed_since_the_close_is_not_accepted(overlap):
    RUN.note_close_verification(M1, close_recon(), legs(), 990.0)
    ex = _Ex([M1], legs=("0.36", "0.31"))           # a late fill arrived
    g = session(ex, _T([], terminal=False)).gates()
    assert g["reason"] == "CAMPAIGN_PREVIOUS_WINDOW_NOT_VERIFIABLE"


def test_a_row_that_is_still_there_is_always_compared(overlap):
    """The close record never overrides a venue row that can be read."""
    RUN.note_close_verification(M1, close_recon(), legs(), 990.0)
    g = session(_Ex([M1]), _T([row(M1, up="0.3000")])).gates()
    assert g["reason"] == "CAMPAIGN_PREVIOUS_WINDOW_DISAGREES_WITH_VENUE"


def test_a_flat_window_verified_flat_is_recorded():
    assert RUN.note_close_verification(
        M1, {"read": True, "agrees": True, "venue_rows": []},
        legs("0", "0"), 1.0)
