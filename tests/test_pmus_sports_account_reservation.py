"""The account limit holds across a burst of orders.

LIVE 2026-11-24 18:27-18:50Z. The account-limit verdict is cached for ten
seconds and its capacity counted only the bids resting when it was
computed. Every new bid was checked against that same capacity, so several
bids inside one cache period each passed: the account figure plus resting
opening bids read 3.06 to 3.36 against a limit of 3.00, the next reading
refused, every opening bid was pulled, and the cycle began again about
every 25 seconds.

Owner 2026-11-24: "Reserve capital for each submitted order immediately,
including in-flight and uncertain submissions. Prevent subsequent orders
from spending the same cached available capacity. Release reservations
only after confirmed cancellation, rejection or reconciliation.
Regression-test simultaneous submissions, concurrent fills, partial fills
and stale account readings. Eliminate the repeated place-and-cancel
cycle."

Every test runs with the PRODUCTION cache period (10 s). The earlier tests
of this limit set it to zero, which is why they could not see the defect.
"""
import datetime as dt
import json
import threading
import time
from decimal import Decimal as D

import pytest

from src import l5_account_risk as AR
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

S1, S2, S3, S4 = ("aec-atp-a-b-2026-11-25", "aec-atp-c-d-2026-11-25",
                  "aec-atp-e-f-2026-11-25", "aec-nba-g-h-2026-11-25")
LIMIT = D("3.00")


def _doc(used, mx="3.00"):
    now = dt.datetime.now(dt.timezone.utc)
    return {"as_of_utc": now.isoformat(),
            "account_risk": {
                "readable": True, "utc_day": now.strftime("%Y-%m-%d"),
                "utc_day_realized_usd": "0", "daily_halt_usd": "50",
                "lifetime_realized_usd": "-40",
                "cumulative_loss_ceiling_usd": "75", "retired_markets": [],
                "portfolio_drawdown": {"state": "ON", "drawdown_usd": used,
                                       "max_drawdown_usd": mx}}}


@pytest.fixture
def a(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    for mod, name, val in (
            (R, "QTY", D("1")), (R, "PAIR_CAP", D("0.97")),
            (R, "PER_MARKET_USD", D("8")), (R, "RESIDUAL_CAP_USD", D("3")),
            (R, "TOTAL_SPEND_USD", D("16")),
            (R, "TOTAL_UNMATCHED_USD", D("6")),
            (M, "RN1_PREGAME_QTY", None), (M, "RN1_LIVE_QTY", None),
            (M, "TOTAL_CAP", D("50")), (M, "MIN_TOUCH_QTY", D("0")),
            (M, "ACCOUNT_RISK_ON", True),
            (M, "ACCOUNT_RISK_TTL_S", 10.0),           # as in production
            (M, "ACCOUNT_RISK_RESTING_IN_LEDGER", False)):
        monkeypatch.setattr(mod, name, val)
    monkeypatch.setenv(AR.RISK_URL_ENV, "http://risk/l5/live_orders.json")
    monkeypatch.setenv(AR.ACCOUNT_CAP_ENV, "40")
    monkeypatch.setenv(AR.ACCOUNT_LANES_ENV, "2")
    box = {"doc": _doc("2.2644")}                 # the figure at 18:34 UTC
    monkeypatch.setattr(AR, "fetch_json", lambda url, timeout=3.0: box["doc"])
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.since = None
    return e, v, box


def sent(v):
    return [(o["slug"], o["side"], str(o["px"])) for o in v.orders.values()]


def kinds(capsys, kind):
    pre = "PMUS_TWO_SIDED "
    out = []
    for l in capsys.readouterr().out.splitlines():
        if l.startswith(pre) and '"%s"' % kind in l:
            out.append(json.loads(l[len(pre):]))
    return out


def worst(e, box):
    """Account figure + unpaired fills the ledger has not seen + every
    resting or reserved bid: what the account would show if all filled."""
    used = D(box["doc"]["account_risk"]["portfolio_drawdown"]["drawdown_usd"])
    return used + e.own_resting_worst()


def fill(e, slug, side, px, qty="1"):
    """The venue filled this resting bid in full."""
    mk = e.b.d["markets"][slug]
    for o in mk["orders"].values():
        if o["open"] and o["side"] == side and D(o["px"]) == D(px):
            o["filled"], o["open"] = o["qty"], False
            mk["pos"][side] = D(str(mk["pos"][side])) + D(qty)
            mk["cost"][side] = D(str(mk["cost"][side])) + D(px) * D(qty)
            return
    raise AssertionError("no such resting bid")


# ---------------------------------------------------------------- the defect
def test_the_live_burst_of_24_nov_is_refused(a, capsys):
    """18:34:15-18:34:21 UTC: figure 2.2644, four opening bids in six
    seconds on one cached reading. The second market's bid took the worst
    case to 3.3344."""
    e, v, box = a
    assert e.account_risk()["drawdown"]["capacity_usd"] == "0.7356"
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    e.place(S1, "NO", D("0.42"), D("1"), "rn1_open")    # same market: no more
    e.place(S2, "YES", D("0.52"), D("1"), "rn1_open")   # this one crossed 3.00
    e.place(S2, "NO", D("0.45"), D("1"), "rn1_open")
    assert sent(v) == [(S1, "YES", "0.55"), (S1, "NO", "0.42")]
    assert worst(e, box) <= LIMIT
    ref = kinds(capsys, "place_refused_account_risk")
    assert [(r["slug"], r["side"], r["reason"]) for r in ref] == [
        (S2, "YES", "ACCOUNT_RISK_CAPACITY"),
        (S2, "NO", "ACCOUNT_RISK_CAPACITY")]


def test_every_burst_seen_live_stays_inside_the_limit(a):
    """The other bursts of that half hour, each from the reading that
    preceded it."""
    for used, bids in (
            ("1.6544", [(S1, "YES", "0.55"), (S2, "YES", "0.53"),
                        (S2, "NO", "0.44"), (S3, "YES", "0.33")]),   # 3.0644
            ("1.6544", [(S1, "YES", "0.55"), (S2, "YES", "0.53"),
                        (S2, "NO", "0.44"), (S3, "NO", "0.63")]),    # 3.3644
            ("1.6544", [(S3, "YES", "0.44"), (S1, "YES", "0.55"),
                        (S2, "YES", "0.53"), (S2, "NO", "0.44")]),   # 3.1744
            ("2.2644", [(S3, "YES", "0.44"), (S4, "YES", "0.53")])):  # 3.2344
        e, v, box = a
        for mk in e.b.d.get("markets", {}).values():
            mk["orders"].clear()
        e._acct_risk = None
        box["doc"] = _doc(used)
        e.account_risk()                         # the cached reading
        for slug, side, px in bids:
            e.place(slug, side, D(px), D("1"), "rn1_open")
        assert worst(e, box) <= LIMIT, (used, bids)


def test_many_markets_cannot_each_spend_the_same_room(a):
    e, v, box = a
    box["doc"] = _doc("1.00")                    # 2.00 of room
    e.account_risk()
    for i in range(12):
        e.place("aec-atp-m%d-x-2026-11-25" % i, "YES", D("0.50"), D("1"),
                "rn1_open")
    assert len(sent(v)) == 3                     # 1.50; a fourth needs 2.01
    assert worst(e, box) <= LIMIT


def test_both_sides_of_one_market_are_not_counted_twice(a):
    """If both fill they are a pair; the worst case is the dearer side."""
    e, v, box = a
    box["doc"] = _doc("2.40")                    # 0.60 of room
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    e.place(S1, "NO", D("0.42"), D("1"), "rn1_open")
    assert len(sent(v)) == 2
    assert e.own_resting_worst() == D("0.55")


# -------------------------------------------------- the place-and-cancel loop
def test_a_bid_that_exactly_fits_is_refused(a, capsys):
    """It would put the reading AT the limit, and a reading at the limit
    pulls every opening bid."""
    e, v, box = a
    box["doc"] = _doc("2.45")                    # 0.55 of room
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    assert sent(v) == []
    e.place(S1, "YES", D("0.54"), D("1"), "rn1_open")
    assert sent(v) == [(S1, "YES", "0.54")]


def test_resting_bids_never_put_the_reading_over_the_limit(a, monkeypatch):
    """Twenty rounds of the live loop: read, place whatever is wanted,
    read again. No reading refuses, so nothing is ever pulled, and nothing
    is placed twice."""
    e, v, box = a
    clock = {"t": 1000.0}
    monkeypatch.setattr(M.time, "monotonic", lambda: clock["t"])
    want = [(S1, "YES", "0.55"), (S1, "NO", "0.42"),
            (S2, "YES", "0.52"), (S2, "NO", "0.45")]
    for _ in range(20):
        assert e.account_risk_blocks() is None       # what pulls the bids
        have = set(sent(v))
        for slug, side, px in want:
            if (slug, side, px) not in have:
                e.place(slug, side, D(px), D("1"), "rn1_open")
        clock["t"] += 12.0                           # past the cache period
        v_ = e.account_risk()
        assert v_["ok"] and D(v_["drawdown"]["capacity_usd"]) > 0
    assert sent(v) == [(S1, "YES", "0.55"), (S1, "NO", "0.42")]


def test_a_refused_bid_does_not_disturb_the_bids_that_rest(a):
    e, v, box = a
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    for _ in range(5):
        e.place(S2, "YES", D("0.52"), D("1"), "rn1_open")
    assert e.account_risk(fresh=True)["ok"]
    assert e.account_risk_blocks() is None
    assert [o["open"] for o in v.orders.values()] == [True]


# ------------------------------------------------- in-flight and uncertain
def test_an_order_is_reserved_while_it_is_being_sent(a):
    """A second order raised while the first is still on the wire sees the
    first one's capital as taken."""
    e, v, box = a
    real, seen = v.place, {}

    def slow(slug, side, px, qty, stage, post_only=True):
        if slug == S1 and "inner" not in seen:
            seen["inner"] = True
            seen["reserved"] = e.own_resting_worst()
            e.place(S2, "YES", D("0.52"), D("1"), "rn1_open")
        return real(slug, side, px, qty, stage, post_only)
    v.place = slow
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    assert seen["reserved"] == D("0.55")
    assert sent(v) == [(S1, "YES", "0.55")]
    assert e.reserved_orders() == []             # the book holds it now
    assert worst(e, box) <= LIMIT


def test_simultaneous_submissions_cannot_exceed_the_limit_together(a):
    """Eight threads, eight markets, one cached reading with room for one
    bid. The engine sends from one thread, so the sends are serialised
    here as they are there."""
    e, v, box = a
    e.account_risk()
    gate = threading.Lock()

    def one(s_):
        with gate:
            e.place(s_, "YES", D("0.50"), D("1"), "rn1_open")
    th = [threading.Thread(target=one,
                           args=("aec-atp-t%d-x-2026-11-25" % i,))
          for i in range(8)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert len(sent(v)) == 1
    assert worst(e, box) <= LIMIT


def test_a_send_that_fails_without_an_answer_keeps_its_reservation(
        a, capsys):
    e, v, box = a
    real = v.place

    def lost(slug, side, px, qty, stage, post_only=True):
        if slug == S1:
            raise TimeoutError("no answer")
        return real(slug, side, px, qty, stage, post_only)
    v.place = lost
    with pytest.raises(TimeoutError):
        e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    assert [r["state"] for r in e.reserved_orders()] == ["uncertain"]
    assert e.own_resting_worst() == D("0.55")
    assert e.own_exposure_usd() == D("0.55")
    e.place(S2, "YES", D("0.52"), D("1"), "rn1_open")    # its room is taken
    assert sent(v) == []
    assert kinds(capsys, "ORDER_RESERVATION_HELD")[0]["slug"] == S1


def test_an_uncertain_reservation_is_released_only_by_a_newer_reconcile(
        a, capsys):
    e, v, box = a
    v.place = lambda *a_, **k: (_ for _ in ()).throw(TimeoutError("lost"))
    with pytest.raises(TimeoutError):
        e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    made = e.reserved_orders()[0]["mono"]
    assert e.release_reconciled_reservations(None) == 0
    assert e.release_reconciled_reservations(made - 30) == 0   # older read
    assert e.release_reconciled_reservations(made + 1) == 0    # too close
    assert e.own_resting_worst() == D("0.55")
    assert e.release_reconciled_reservations(
        made + M.RESERVATION_HOLD_S + 0.1) == 1
    assert e.reserved_orders() == [] and e.own_resting_worst() == D("0")
    assert kinds(capsys, "ORDER_RESERVATION_RELEASED")[0]["why"] == \
        "reconciled"


def test_a_failed_orphan_sweep_releases_nothing(a):
    e, v, box = a
    v.place = lambda *a_, **k: (_ for _ in ()).throw(TimeoutError("lost"))
    with pytest.raises(TimeoutError):
        e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")

    class T:
        @staticmethod
        def request(*a_, **k):
            raise OSError("venue down")
    v.t = T()
    e.orphan_sweep(None)
    assert e._orphan_sweep_failed is True        # the loop then holds it
    assert len(e.reserved_orders()) == 1


def test_a_definite_rejection_releases_at_once(a):
    e, v, box = a

    class RejectedNoFill(Exception):
        pass
    v.place = lambda *a_, **k: (_ for _ in ()).throw(RejectedNoFill("no"))
    with pytest.raises(RejectedNoFill):
        e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    assert e.reserved_orders() == []


def test_an_uncertain_reservation_survives_a_restart(a, tmp_path):
    e, v, box = a
    v.place = lambda *a_, **k: (_ for _ in ()).throw(TimeoutError("lost"))
    with pytest.raises(TimeoutError):
        e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    v2 = FakeVenue()
    v2.states = {}
    e2 = M.Engine(v2, M.Book(tmp_path / "s.json"))
    e2.since = None
    assert e2.own_resting_worst() == D("0.55")
    e2.place(S2, "YES", D("0.52"), D("1"), "rn1_open")
    assert v2.orders == {}
    born = e2.reserved_orders()[0]["mono"]       # as old as the new process
    assert e2.release_reconciled_reservations(
        born + M.RESERVATION_HOLD_S + 0.1) == 1


def test_a_cancelled_order_frees_its_room_only_when_the_book_says_closed(a):
    e, v, box = a
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    e.place(S2, "YES", D("0.52"), D("1"), "rn1_open")
    assert len(sent(v)) == 1                     # no room while it rests
    mk = e.b.d["markets"][S1]
    next(iter(mk["orders"].values()))["open"] = False   # confirmed cancel
    e.place(S2, "YES", D("0.52"), D("1"), "rn1_open")
    assert len(sent(v)) == 2
    assert worst(e, box) <= LIMIT


# ----------------------------------------------------------- fills
def test_concurrent_fills_cannot_breach_the_limit(a):
    """Everything resting fills at once, before the ledger has seen any of
    it. The account figure the ledger will then publish is inside the
    limit, and nothing more is accepted meanwhile."""
    e, v, box = a
    box["doc"] = _doc("1.2144")                  # 1.7856 of room
    e.account_risk()
    for slug, side, px in ((S1, "YES", "0.55"), (S2, "YES", "0.52"),
                           (S3, "NO", "0.61"), (S4, "YES", "0.53")):
        e.place(slug, side, D(px), D("1"), "rn1_open")
    assert sent(v) == [(S1, "YES", "0.55"), (S2, "YES", "0.52"),
                       (S3, "NO", "0.61")]       # 1.68; the fourth is 2.21
    for slug, side, px in ((S1, "YES", "0.55"), (S2, "YES", "0.52"),
                           (S3, "NO", "0.61")):
        fill(e, slug, side, px)
    unpaired = D(e.b.total_unmatched())
    assert D("1.2144") + unpaired == D("2.8944") <= LIMIT
    # the ledger still says 1.2144: the fills are counted from the book
    e.place(S4, "YES", D("0.53"), D("1"), "rn1_open")
    assert len(sent(v)) == 3
    d = e.account_risk(fresh=True)["drawdown"]
    assert D(d["own_added_since_snapshot_usd"]) == D("1.68")
    # ... and once the ledger has caught up, the room is what is left
    box["doc"] = _doc("2.8944")
    e._expo_hist = None
    assert D(e.account_risk(fresh=True)["drawdown"]["capacity_usd"]) == \
        D("0.1056")
    e.place(S4, "YES", D("0.53"), D("1"), "rn1_open")
    assert len(sent(v)) == 3


def test_a_fill_between_two_placements_is_seen_by_the_second(a):
    """The cached reading predates the fill; the placement does not use
    it."""
    e, v, box = a
    box["doc"] = _doc("1.90")                    # 1.10 of room
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    fill(e, S1, "YES", "0.55")                   # now unpaired inventory
    e.place(S2, "YES", D("0.52"), D("1"), "rn1_open")    # 0.55 + 0.52 fits
    e.place(S3, "YES", D("0.10"), D("1"), "rn1_open")    # 1.17 does not
    assert sent(v) == [(S1, "YES", "0.55"), (S2, "YES", "0.52")]


def test_partial_fills_count_what_filled_and_what_still_rests(a,
                                                             monkeypatch):
    e, v, box = a
    monkeypatch.setattr(R, "QTY", D("2"))
    box["doc"] = _doc("1.90")                    # 1.10 of room
    e.place(S1, "YES", D("0.40"), D("2"), "rn1_open")    # 0.80
    mk = e.b.d["markets"][S1]
    o = next(iter(mk["orders"].values()))
    o["filled"] = "1"                            # one filled, one resting
    mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D("0.40")
    d = e.account_risk(fresh=True)["drawdown"]
    assert D(d["own_added_since_snapshot_usd"]) == D("0.40")
    assert D(d["own_resting_orders_usd"]) == D("0.40")
    assert D(d["capacity_usd"]) == D("0.30")
    e.place(S2, "YES", D("0.30"), D("1"), "rn1_open")    # exactly fits: no
    e.place(S2, "YES", D("0.29"), D("1"), "rn1_open")
    assert sent(v)[-1] == (S2, "YES", "0.29") and len(sent(v)) == 2


def test_a_completion_is_never_refused_by_the_account_limit(a):
    e, v, box = a
    box["doc"] = _doc("2.40")
    e.place(S1, "YES", D("0.55"), D("1"), "rn1_open")
    fill(e, S1, "YES", "0.55")
    box["doc"] = _doc("2.95")                    # the ledger has caught up
    e.place(S1, "NO", D("0.40"), D("1"), "two_sided_complete")
    assert sent(v)[-1] == (S1, "NO", "0.40")


# ------------------------------------------------ stale and unreadable
class _Reader:
    def __init__(self, doc, age):
        self.doc, self.age, self.err = doc, age, None

    def snapshot(self):
        return {"doc": self.doc, "age_s": self.age, "err": self.err}


def test_a_stale_account_reading_refuses_every_new_bid(a, capsys):
    e, v, box = a
    e._risk_reader = _Reader(_doc("1.00"), M.ACCOUNT_RISK_SNAPSHOT_MAX_S + 1)
    e.place(S1, "YES", D("0.10"), D("1"), "rn1_open")
    assert sent(v) == []
    assert kinds(capsys, "place_refused_account_risk")[0]["reason"] == \
        "ACCOUNT_RISK_SNAPSHOT_STALE"


def test_a_reading_that_goes_stale_inside_the_cache_period_refuses(a,
                                                                   capsys):
    """The cached verdict was good; the snapshot behind it has aged out.
    A placement looks at the snapshot as it is now."""
    e, v, box = a
    r = _Reader(_doc("1.00"), 1.0)
    e._risk_reader = r
    assert e.account_risk()["ok"]
    e.place(S1, "YES", D("0.10"), D("1"), "rn1_open")
    assert len(sent(v)) == 1
    r.age = M.ACCOUNT_RISK_SNAPSHOT_MAX_S + 1
    e.place(S2, "YES", D("0.10"), D("1"), "rn1_open")
    assert len(sent(v)) == 1


def test_no_reading_yet_refuses(a):
    e, v, box = a
    e._risk_reader = _Reader(None, None)
    e.place(S1, "YES", D("0.10"), D("1"), "rn1_open")
    assert sent(v) == []


def test_an_unreadable_drawdown_figure_refuses(a):
    e, v, box = a
    d = _doc("1.00")
    d["account_risk"]["portfolio_drawdown"]["drawdown_usd"] = None
    box["doc"] = d
    e.place(S1, "YES", D("0.10"), D("1"), "rn1_open")
    assert sent(v) == []


def test_unreadable_reservations_refuse(a):
    e, v, box = a
    e.b.d["acct_reservations"] = {"x": {"slug": S1, "side": "YES",
                                        "px": "junk", "qty": "1",
                                        "state": "uncertain", "mono": 0}}
    assert e.own_resting_worst() == D("Infinity")
    e.place(S2, "YES", D("0.10"), D("1"), "rn1_open")
    assert sent(v) == []


# ------------------------------------------------------------ unchanged
def test_the_other_ceilings_still_apply(a, monkeypatch, capsys):
    e, v, box = a
    box["doc"] = _doc("0.10")
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("0.80"))
    e.place(S1, "YES", D("0.47"), D("1"), "rn1_open")
    e.place(S2, "YES", D("0.47"), D("1"), "rn1_open")
    assert len(sent(v)) == 1
    assert kinds(capsys, "place_refused_final_gate")[0]["reason"].startswith(
        "campaign_unpaired")
    assert e.reserved_orders() == []             # nothing sent, none held


def test_with_the_account_limit_off_nothing_changes(a, monkeypatch):
    e, v, box = a
    monkeypatch.setattr(M, "ACCOUNT_RISK_ON", False)
    for s_ in (S1, S2, S3, S4):
        e.place(s_, "YES", D("0.55"), D("1"), "rn1_open")
    assert len(sent(v)) == 4 and e.reserved_orders() == []
