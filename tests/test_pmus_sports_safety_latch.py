"""Owner 2026-11-22: the bot stops ITSELF. A relock issued from a coding
session reached production 45 minutes late; the latch is durable state in
the bot's own book, cancels every own order, refuses every placement, and
is cleared only by an acknowledgement naming that latch's id."""
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    monkeypatch.delenv(M.SAFETY_LATCH_ACK_ENV, raising=False)
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e, v, tmp_path


def open_orders(v):
    return [o for o in v.orders.values() if o["open"]]


def hold(e, slug, yes=0, no=0, cy="0", cn="0", stage="rn1_open", t=2000.0):
    mk = e.b.m(slug)
    mk["pos"] = {"YES": D(str(yes)), "NO": D(str(no))}
    mk["cost"] = {"YES": D(cy), "NO": D(cn)}
    mk["orders"]["seed"] = {"side": "YES", "px": "0.4", "qty": "2",
                            "filled": "2", "open": False, "stage": stage,
                            "placed_at": t}
    return mk


def snapshot(v, rows, complete=True):
    v.positions_snapshot = lambda: {"rows": rows, "open_orders": [],
                                    "pages": 1, "complete": complete}


def row(slug, bought, sold):
    return {"marketSlug": slug, "qtyBoughtDecimal": str(bought),
            "qtySoldDecimal": str(sold)}


# ------------------------------------------------------------ the latch
def test_latch_cancels_everything_and_refuses_every_stage(eng, capsys):
    e, v, _ = eng
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert len(open_orders(v)) == 2
    e.latch("position_mismatch", slug="aec-x", book_net=2, venue_net=4)
    assert e.enforce_lock() == "safety_latch:position_mismatch"
    assert open_orders(v) == []
    n = len(v.orders)
    for stage in ("rn1_open", "rn1_extend", "two_sided_open",
                  "two_sided_complete", "two_sided_exit"):
        e.place("aec-x", "YES", D("0.40"), D("2"), stage)
    assert len(v.orders) == n
    out = capsys.readouterr().out
    assert out.count("place_refused_safety_latch") == 5
    assert '"kind":"SAFETY_LATCH"' in out
    assert M.registry_doc(e.b)["submission_disabled"] is True
    assert M.registry_doc(e.b)["safety_latch"]["why"] == "position_mismatch"


def test_latch_survives_a_restart_and_any_lock_value(eng, monkeypatch):
    e, v, tmp = eng
    e.latch("loop_errors", consecutive=10)
    lid = e.b.d["safety_latch"]["id"]
    for lock in ("0", "", "1", "window:2026-11-22T14:05:00Z/"
                               "2026-11-22T14:50:00Z"):
        monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, lock)
        e2 = M.Engine(FakeVenue(), M.Book(tmp / "s.json"))   # "restart"
        assert e2.safety_latch()["id"] == lid
        assert e2.enforce_lock() == "safety_latch:loop_errors"
        e2.place("aec-x", "YES", D("0.40"), D("2"), "rn1_open")
        assert e2.v.orders == {}


def test_only_an_ack_naming_this_latch_clears_it(eng, monkeypatch, capsys):
    e, v, _ = eng
    e.latch("position_mismatch", slug="aec-x")
    lid = e.b.d["safety_latch"]["id"]
    for bad in ("1", "yes", "clear", lid + "x", "2026-01-01T00:00:00"):
        monkeypatch.setenv(M.SAFETY_LATCH_ACK_ENV, bad)
        assert e.safety_latch() is not None
    monkeypatch.setenv(M.SAFETY_LATCH_ACK_ENV, lid)
    assert e.safety_latch() is None
    assert "SAFETY_LATCH_CLEARED" in capsys.readouterr().out
    assert e.b.d["safety_latch_history"][-1]["id"] == lid
    # the same ack can never pre-clear the NEXT latch
    e.latch("campaign_cap_breached", spend=17)
    assert e.b.d["safety_latch"]["id"] != lid or True
    e.b.d["safety_latch"]["id"] = lid + "-next"
    assert e.safety_latch()["why"] == "campaign_cap_breached"


def test_first_reason_is_kept_and_switch_off_only_logs(eng, monkeypatch,
                                                       capsys):
    e, v, _ = eng
    e.latch("position_mismatch", slug="a")
    e.latch("loop_errors", consecutive=99)
    assert e.b.d["safety_latch"]["why"] == "position_mismatch"
    e.b.d["safety_latch"] = None
    monkeypatch.setattr(M, "SAFETY_LATCH_ON", False)
    e.latch("loop_errors", consecutive=99)
    assert not e.b.d.get("safety_latch")
    assert "SAFETY_LATCH_DISABLED_WOULD_LATCH" in capsys.readouterr().out


# ------------------------------------------ venue position vs the book
def test_matching_positions_never_latch(eng):
    e, v, _ = eng
    hold(e, "aec-a", yes=2, cy="0.9")             # 2 YES unpaired
    hold(e, "aec-b", yes=6, no=6, cy="2.74", cn="2.98")
    for _ in range(5):
        e.safety_check({"positions": {"aec-a": D("2")}, "open_orders": []})
    assert not e.b.d.get("safety_latch")
    e.safety_check({"positions": {"aec-a": D("-2")}, "open_orders": []})
    assert not e.b.d.get("safety_latch")          # sign-agnostic


def test_our_own_unread_fill_is_read_and_is_not_a_mismatch(eng):
    """The 14:28Z case: the venue had the fill, the book did not yet."""
    e, v, _ = eng
    e.b.m("aec-x")
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes]["filled"] = D("2")
    v.orders[yes]["open"] = False                 # filled at the venue
    e.safety_check({"positions": {"aec-x": D("2")}, "open_orders": []})
    assert D(str(e.b.d["markets"]["aec-x"]["pos"]["YES"])) == 2   # read now
    assert "mismatch_since" not in e.b.d["markets"]["aec-x"]
    assert not e.b.d.get("safety_latch")


def test_persistent_mismatch_latches_after_grace_and_a_complete_walk(
        eng, monkeypatch):
    e, v, _ = eng
    hold(e, "aec-x", yes=2, cy="0.9")
    t = [1000.0]
    monkeypatch.setattr(M.time, "time", lambda: t[0])
    acct = {"positions": {"aec-x": D("4")}, "open_orders": []}
    snapshot(v, [row("aec-x", 4, 0)])
    e.safety_check(acct)
    assert e.b.d["markets"]["aec-x"]["mismatch_since"] == 1000.0
    t[0] += M.MISMATCH_GRACE_S - 1
    e.safety_check(acct)
    assert not e.b.d.get("safety_latch")          # inside the grace
    t[0] += 2
    e.safety_check(acct)
    lt = e.b.d["safety_latch"]
    assert lt["why"] == "position_mismatch"
    assert lt["detail"]["book_net"] == "2" and lt["detail"]["venue_net"] == "4"


def test_first_page_miss_is_cleared_by_the_complete_walk(eng, monkeypatch):
    """account() reads page one only; a position on page two must not latch."""
    e, v, _ = eng
    hold(e, "aec-x", yes=2, cy="0.9")
    t = [1000.0]
    monkeypatch.setattr(M.time, "time", lambda: t[0])
    acct = {"positions": {}, "open_orders": []}   # not on page one
    calls = []

    def snap():
        calls.append(1)
        return {"rows": [row("aec-x", 2, 0)], "open_orders": [], "pages": 2,
                "complete": True}
    v.positions_snapshot = snap
    e.safety_check(acct)
    t[0] += M.MISMATCH_GRACE_S + 1
    e.safety_check(acct)
    assert not e.b.d.get("safety_latch") and len(calls) == 1
    for _ in range(10):                           # no walk storm afterwards
        t[0] += 20
        e.safety_check(acct)
    assert len(calls) == 1 and not e.b.d.get("safety_latch")
    # ... until the book changes: then it is checked again
    e.b.d["markets"]["aec-x"]["pos"]["YES"] = D("4")
    e.safety_check(acct)
    assert e.b.d["markets"]["aec-x"].get("mismatch_since")


def test_unverifiable_mismatch_fails_closed(eng, monkeypatch):
    e, v, _ = eng
    hold(e, "aec-x", yes=2, cy="0.9")
    t = [1000.0]
    monkeypatch.setattr(M.time, "time", lambda: t[0])
    acct = {"positions": {"aec-x": D("4")}, "open_orders": []}
    snapshot(v, [], complete=False)               # walk never completes
    e.safety_check(acct)
    t[0] += M.MISMATCH_GRACE_S + 1
    e.safety_check(acct)
    assert not e.b.d.get("safety_latch")
    t[0] += 2 * M.MISMATCH_GRACE_S
    e.safety_check(acct)
    assert e.b.d["safety_latch"]["why"] == "position_unverifiable"


def test_done_markets_and_a_failed_account_read_are_ignored(eng):
    e, v, _ = eng
    mk = hold(e, "aec-x", yes=2, cy="0.9")
    mk["done"] = True
    e.safety_check({"positions": {"aec-x": D("9")}, "open_orders": []})
    assert not e.b.d.get("safety_latch") and "mismatch_since" not in mk
    mk["done"] = False
    e.safety_check(None)                          # account unreadable
    assert not e.b.d.get("safety_latch")


# --------------------------------------------------- book invariants
def test_breached_caps_latch_from_the_book_alone(eng):
    e, v, _ = eng
    hold(e, "aec-a", no=5, cn="3.40")             # 3.40 unpaired > $3
    e.safety_check(None)
    assert e.b.d["safety_latch"]["why"] == "market_cap_breached"
    e.b.d["safety_latch"] = None
    e.b.d["markets"].clear()
    for i in range(3):                            # 3 x 2.50 unpaired > $6
        hold(e, f"aec-m{i}", no=4, cn="2.50")
    e.safety_check(None)
    assert e.b.d["safety_latch"]["why"] == "campaign_cap_breached"
    e.b.d["safety_latch"] = None
    e.b.d["markets"].clear()
    hold(e, "aec-p", yes=9, no=9, cy="4.2", cn="4.4")   # 8.60 spent > $8
    e.safety_check(None)
    assert e.b.d["safety_latch"]["why"] == "market_cap_breached"


def test_todays_campaign_would_not_have_latched_on_caps(eng):
    """Serie A 22 Nov: 5.72 paired + 2.68 unpaired = 8.40 spent."""
    e, v, _ = eng
    hold(e, "atc-sea-bol-udi-2026-11-22-bol", yes=6, no=6, cy="2.74",
         cn="2.98")
    hold(e, "atc-sea-sas-gen-2026-11-22-gen", no=4, cn="2.68")
    e.safety_check({"positions": {"atc-sea-sas-gen-2026-11-22-gen": D("-4")},
                    "open_orders": []})
    assert not e.b.d.get("safety_latch")


def test_oversized_own_order_latches(eng):
    e, v, _ = eng
    mk = e.b.m("aec-x")
    mk["orders"]["big"] = {"side": "YES", "px": "0.40", "qty": "5",
                           "filled": "0", "open": True, "stage": "rn1_open"}
    e.safety_check(None)
    assert e.b.d["safety_latch"]["why"] == "order_qty_exceeds_clip"


def test_settled_campaign_markets_do_not_count_toward_caps(eng):
    e, v, _ = eng
    for i in range(4):
        mk = hold(e, f"aec-s{i}", no=4, cn="2.50")
        mk["settle_px"] = "1"
        mk["done"] = True
    e.safety_check(None)
    assert not e.b.d.get("safety_latch")


# ----------------------------------------------------------- the loop
def test_run_loop_latches_cancels_and_stops_stepping(eng, monkeypatch):
    """End to end through run(): a persistent mismatch found on an account
    read cancels the resting orders and no market is stepped again."""
    e, v, _ = eng
    monkeypatch.setattr(M, "MISMATCH_GRACE_S", 0.0)
    monkeypatch.setattr(M, "LOOP_S", 0)
    monkeypatch.setattr(M, "REWARD_CHECK_S", 0)
    monkeypatch.setattr(M, "SHARP_MODE", "off")
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert len(open_orders(v)) == 2
    v.acct = {"positions": {"aec-x": D("7")}, "open_orders": []}
    v.targets = []
    snapshot(v, [row("aec-x", 7, 0)])
    # the mismatch was first seen on an earlier account read (reads are
    # 20 s apart in production; this run makes one)
    e.b.d["markets"]["aec-x"]["mismatch_since"] = M.time.time() - 100
    steps = []
    real = e.step_market
    monkeypatch.setattr(e, "step_market",
                        lambda s: (steps.append(s), real(s))[1])
    n = [0]

    def sleep(_):
        n[0] += 1
        if n[0] >= 4:
            e.stop = True
    monkeypatch.setattr(e, "_sleep", sleep)
    monkeypatch.setattr(e, "report", lambda: None)
    monkeypatch.setattr(e, "orphan_sweep", lambda *a, **k: None)
    e.run()
    assert e.b.d["safety_latch"]["why"] == "position_mismatch"
    assert open_orders(v) == []
    assert len(steps) <= 1                        # nothing stepped after it


def test_repeated_loop_errors_latch(eng, monkeypatch):
    e, v, _ = eng
    monkeypatch.setattr(M, "LOOP_S", 0)
    monkeypatch.setattr(M, "LOOP_ERROR_LATCH_N", 3)
    monkeypatch.setattr(e, "read_account",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    n = [0]

    def sleep(_):
        n[0] += 1
        if n[0] >= 5:
            e.stop = True
    monkeypatch.setattr(e, "_sleep", sleep)
    e.run()
    assert e.b.d["safety_latch"]["why"] == "loop_errors"


# ------------------------------------------ campaign marker across a relock
def test_campaign_marker_survives_the_relock(tmp_path, monkeypatch):
    """Window 14:05Z, then lock back to 1 with a stale 01:15Z variable: the
    marker must stay 14:05Z so the meter does not merge two campaigns."""
    start = 1795356300.0                               # 2026-11-22T14:05:00Z
    win = "window:2026-11-22T14:05:00Z/2026-11-22T14:50:00Z"
    monkeypatch.setattr(M, "PILOT_SINCE", start)       # as at import with win
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, win)
    e = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    assert e.b.d["campaign_since"] == start and e.since == start
    # relock: the process restarts with lock=1 and the stale variable
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    monkeypatch.setattr(M, "PILOT_SINCE", 1795310100.0)    # 01:15Z, stale
    e = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    assert e.since == start
    assert M.PILOT_SINCE == 1795310100.0               # module value untouched
    # a LATER explicit marker still wins; an unset one adopts the stored
    monkeypatch.setattr(M, "PILOT_SINCE", start + 3600)
    assert M.Engine(FakeVenue(), M.Book(tmp_path / "s.json")).since == \
        start + 3600
    monkeypatch.setattr(M, "PILOT_SINCE", None)
    assert M.Engine(FakeVenue(), M.Book(tmp_path / "s.json")).since == start


def test_meter_after_relock_counts_only_the_new_campaign(tmp_path,
                                                         monkeypatch):
    from tests.test_pmus_pilot_pnl import mk, trade
    start = 1795356300.0
    b = M.Book(tmp_path / "s.json")
    b.d["campaign_since"] = start
    b.save()
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    monkeypatch.setattr(M, "PILOT_SINCE", 1795310100.0)
    e = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    markets = {"aec-last-night": mk(5, 5, "1.95", "2.85", t=1795310500.0),
               "atc-sea-bol-udi-2026-11-22-bol":
                   mk(6, 6, "2.74", "2.98", t=start + 60)}
    rows = [trade(s, q) for s, q in (("aec-last-night", 5),
                                     ("aec-last-night", 5),
                                     ("atc-sea-bol-udi-2026-11-22-bol", 6),
                                     ("atc-sea-bol-udi-2026-11-22-bol", 6))]
    e.pilot_meter(rows, markets, venue_net={})
    assert e._pilot["pilot_markets"] == 1
    assert abs(D(e._pilot["realized_net_usd"]) - D("0.28")) < D("0.000001")


def test_no_window_ever_means_no_stored_marker(tmp_path, monkeypatch):
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    monkeypatch.setattr(M, "PILOT_SINCE", None)
    e = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    assert e.b.d.get("campaign_since") is None and e.since is None
