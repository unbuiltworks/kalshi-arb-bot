"""EP013's crossed tick: a HOLD that costs one cycle, not a whole window.

RUN 35653449506 bound GEMI-BTC15M2609212115-UP at tte 874.0s, ran 55 clean
cycles, and at tte 332.4 read ONE crossed book -- yes bid 0.12 against a 0.05
ask. `state_from_venue` refused it (correctly), `plan()` turned the refusal
into a stop, and the driver withdrew the window 32 seconds before the frozen
surface's [0, 300] quoting window would have opened. Zero orders placed,
zero evidence produced, one authorized window spent.

WHAT IS NOT NEGOTIABLE, and every test below re-asserts it: an UNREADABLE,
crossed or incoherent authoritative snapshot may never be used to place an
order, to infer flat inventory, or to make any inventory-dependent decision.
The policy is not shown the snapshot at all.

WHAT CHANGED: the blast radius. A transient is now a bounded HOLD --
`UNREADABLE_MAX_CONSEC` cycles / `UNREADABLE_MAX_S` seconds -- and past that
bound terminal withdrawal is still the answer.
"""
import asyncio

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import position_ledger as PL
from src.gemini_execution import spec
from src.gemini_execution import ws_continuous as WC

BTC15M = "GEMI-BTC15M2609212115-UP"

#: the exact numbers run 35653449506 refused, and the ones it read the cycle
#: before. `identity_consistent` holds on BOTH: crossing both sides preserves
#: the complement identity by construction, which is why `crossed` exists.
CLEAN = {"readable": True, "identity_consistent": True,
         "yes": {"bid": "0.01", "ask": "0.06"},
         "no": {"bid": "0.94", "ask": "0.99"}}
CROSSED = {"readable": True, "identity_consistent": True,
           "yes": {"bid": "0.12", "ask": "0.05"},
           "no": {"bid": "0.95", "ask": "0.88"}}
#: a quotable book, so "no orders" in a test is never merely the TTE band
QUOTABLE = {"readable": True, "identity_consistent": True,
            "yes": {"bid": "0.35", "ask": "0.42"},
            "no": {"bid": "0.58", "ask": "0.65"}}

#: inside the frozen surface's quote_between_tte_s = [0, 300]
TTE = 250.0


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, dt=9.4):
        self.t += dt
        return self.t


def driver(tmp_path, clock, *, envelope=LE.LIVE_Q1_BTC15M_CAP4):
    d = WC.Driver("A", BTC15M, ledger_path=str(tmp_path / "l.jsonl"),
                  now_fn=clock, envelope=envelope)
    d.inv = CP.Inventory()
    d.guard = CP.WindowGuard(BTC15M, auth_id="A",
                             cohort_prefix=d._family_prefix)
    d.budget_consumed = 0
    d.state = WC.RUNNING
    return d


def rest(d, coid, leg, price, now, qty="1"):
    d.working.add(coid, leg=leg, price=price, qty=qty, now=now)
    return d.working.by_id[coid]


# ===================================================== the sanity baseline

def test_the_crossed_book_is_still_refused_as_unreadable():
    """The refusal itself is untouched. If this ever passes a crossed book
    the rest of the file is meaningless."""
    assert CP.crossed(CROSSED) is True
    assert CP.crossed(CLEAN) is False
    st = CP.state_from_venue(BTC15M, TTE, CP.Inventory(), now=1.0,
                             book=CROSSED)
    assert st["inventory_known"] is False
    assert st["state_id"] == "unreadable"
    assert "CROSSED" in st["unreadable_why"]


def test_a_quotable_book_really_does_produce_a_surface(tmp_path):
    """Otherwise 'no actions' below would prove nothing."""
    c = Clock()
    d = driver(tmp_path, c)
    out = d.decide(TTE, book=QUOTABLE)
    assert (out["turn"].get("actions") or []), out["turn"]
    assert out["plan"]["mutations"] > 0


# ========================================== clean -> one tick -> clean

def test_one_crossed_tick_holds_and_the_next_clean_read_resumes(tmp_path):
    """THE EP013 SEQUENCE, and the whole point of the change."""
    c = Clock()
    d = driver(tmp_path, c)

    c.tick()
    assert d.decide(TTE, book=QUOTABLE)["state"] == WC.RUNNING

    c.tick()
    held = d.decide(TTE, book=CROSSED)
    assert d.state == WC.RUNNING, "one tick may not end the window"
    assert held["turn"] is None, "the policy was not shown the snapshot"
    assert held["plan"]["mutations"] == 0
    assert held["plan"]["place"] == [] and held["plan"]["cancel"] == []
    assert held["unreadable_hold"]["verdict"] == "HOLD"
    assert held["unreadable_hold"]["consecutive"] == 1

    c.tick()
    back = d.decide(TTE, book=QUOTABLE)
    assert d.state == WC.RUNNING
    assert back["unreadable_hold"] is None if "unreadable_hold" in back \
        else True
    assert back["turn"] is not None, "the surface is live again"
    assert d._unreadable_n == 0
    assert any(e.get("event") == "UNREADABLE_RECOVERED" for e in d.log)


def test_the_run_counter_resets_so_holds_do_not_accumulate(tmp_path):
    """Two separated single ticks are two transients, not a run of two."""
    c = Clock()
    d = driver(tmp_path, c)
    for book in (CROSSED, QUOTABLE, CROSSED, QUOTABLE, CROSSED):
        c.tick()
        d.decide(TTE, book=book)
    assert d.state == WC.RUNNING
    assert d._unreadable_n == 1


# ================================= repeated unreadable beyond the bound

def test_repeated_unreadable_past_the_bound_withdraws(tmp_path):
    c = Clock()
    d = driver(tmp_path, c)
    for i in range(WC.UNREADABLE_MAX_CONSEC):
        c.tick()
        out = d.decide(TTE, book=CROSSED)
        assert out["unreadable_hold"]["verdict"] == "HOLD", i
        assert d.state == WC.RUNNING, i

    c.tick()
    out = d.decide(TTE, book=CROSSED)
    assert d.state == WC.WITHDRAWING
    assert out["unreadable_hold"]["verdict"] == "BOUND_EXCEEDED"
    assert str(WC.UNREADABLE_MAX_CONSEC) in out["why"]
    assert "unreadable" in out["why"]


def test_the_wall_clock_half_of_the_bound_also_binds(tmp_path):
    """A loop that stalls could sit inside five cycles for a long time."""
    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    d.decide(TTE, book=CROSSED)
    c.tick(WC.UNREADABLE_MAX_S + 1.0)
    out = d.decide(TTE, book=CROSSED)
    assert d.state == WC.WITHDRAWING
    assert out["unreadable_hold"]["consecutive"] == 2
    assert out["unreadable_hold"]["verdict"] == "BOUND_EXCEEDED"


def test_the_bound_exceeded_plan_cancels_every_resting_order(tmp_path):
    """Withdrawal protections stay available exactly as before."""
    c = Clock()
    d = driver(tmp_path, c)
    rest(d, "coid-a", "YES", "0.19", c.t)
    rest(d, "coid-b", "NO", "0.72", c.t)
    for _ in range(WC.UNREADABLE_MAX_CONSEC + 1):
        c.tick()
        out = d.decide(TTE, book=CROSSED)
    assert d.state == WC.WITHDRAWING
    assert {x["order"]["client_order_id"] for x in out["plan"]["cancel"]} == \
        {"coid-a", "coid-b"}
    assert all(x.get("safety") == "TERMINAL_WITHDRAWAL"
               for x in out["plan"]["cancel"])


# ============================================ unreadable while flat

def test_unreadable_while_flat_mutates_nothing(tmp_path):
    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    out = d.decide(TTE, book=CROSSED)
    assert out["plan"]["mutations"] == 0
    assert out["plan"]["place"] == []
    assert out["plan"]["cancel"] == []
    assert out["plan"]["retain"] == []
    assert d.budget_left() == d.max_actions, "no slot may be spent on a hold"


# =========================== unreadable with resting orders: no churn

def test_unreadable_with_resting_orders_replaces_and_duplicates_nothing(
        tmp_path):
    """The dangerous shape: a live order, a book we cannot read, and a
    lifecycle that would happily re-price or duplicate it."""
    c = Clock()
    d = driver(tmp_path, c)
    a = rest(d, "coid-a", "YES", "0.19", c.t)
    b = rest(d, "coid-b", "NO", "0.72", c.t)
    before = [dict(o) for o in d.working.rungs()]

    # age them PAST the rest floor, so nothing is protected by youth: a
    # cancel here would be legal in any ordinary cycle.
    c.tick(CP.STALE_ORDER_S + 5.0)
    out = d.decide(TTE, book=CROSSED)

    assert out["plan"]["mutations"] == 0
    assert out["plan"]["place"] == [], "no replacement on an unread book"
    assert out["plan"]["cancel"] == [], "no cancel nothing asked for"
    assert {x["order"]["client_order_id"] for x in out["plan"]["retain"]} == \
        {"coid-a", "coid-b"}
    assert all(x["held_by"] == WC.HELD_UNREADABLE
               for x in out["plan"]["retain"])
    # and the orders themselves are untouched -- not forgotten, not re-priced
    assert [dict(o) for o in d.working.rungs()] == before
    assert out["unreadable_hold"]["resting"] == 2
    assert a["price"] == "0.19" and b["price"] == "0.72"


# ================================ inventory is never assumed flat

def test_a_held_cycle_never_assumes_flat_inventory(tmp_path):
    c = Clock()
    d = driver(tmp_path, c)
    d.inv.apply_fill("YES", "1", "0.19", source="test")
    held_report = d.inv.report()
    c.tick()
    out = d.decide(TTE, book=CROSSED)
    assert out["unreadable_hold"]["inventory"] == held_report
    assert out["unreadable_hold"]["inventory"]["yes_qty"] == "1"
    assert d.inv.report() == held_report, "the hold did not rewrite inventory"


def test_the_last_coherent_state_is_provenance_and_not_authority(tmp_path):
    """It is recorded. It is not re-used, and it cannot end a hold."""
    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    good = d.decide(TTE, book=QUOTABLE)
    sid = good["market_state"]["state_id"]

    c.tick()
    out = d.decide(TTE, book=CROSSED)
    assert out["unreadable_hold"]["last_coherent"]["state_id"] == sid
    # the state the DECISION was about is the unreadable one, not the memory
    assert out["market_state"]["state_id"] == "unreadable"
    assert out["turn"] is None
    assert out["plan"]["mutations"] == 0


# ============================ recovery requires a FRESH coherent read

def test_a_stale_coherent_snapshot_does_not_end_a_hold(tmp_path):
    """Resume takes a read at least as new as the last refusal. A replayed or
    cached object is a memory, not a recovery."""
    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    d.decide(TTE, book=CROSSED)
    assert d._unreadable_n == 1

    # what a replayed snapshot looks like from the driver's side: a coherent
    # book stamped with a clock OLDER than the refusal it is meant to undo.
    c.t -= 60.0
    out = d.decide(TTE, book=QUOTABLE)
    assert out["turn"] is None, "a stale snapshot may not reach the policy"
    assert out["unreadable_hold"]["verdict"] == "HOLD"
    assert d._unreadable_n == 2
    assert "stale read" in out["unreadable_hold"]["why"]

    # and a genuinely fresh one does end it
    c.t += 120.0
    assert d.decide(TTE, book=QUOTABLE)["turn"] is not None
    assert d._unreadable_n == 0


def test_resume_requires_the_snapshot_to_pass_every_existing_check(tmp_path):
    """An identity-inconsistent book is coherent-looking and still refused,
    so it cannot end a hold either."""
    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    d.decide(TTE, book=CROSSED)
    bad = {"readable": True, "identity_consistent": False,
           "yes": {"bid": "0.35", "ask": "0.42"},
           "no": {"bid": "0.10", "ask": "0.20"}}
    c.tick()
    out = d.decide(TTE, book=bad)
    assert out["turn"] is None
    assert d._unreadable_n == 2
    assert "identity" in out["unreadable_hold"]["why"]


# ================================= the position re-read during a hold

class _Pos:
    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    async def __call__(self):
        self.calls += 1
        return {"rows": self.rows}


def test_an_unreadable_position_holds_and_is_never_read_as_flat(tmp_path):
    c = Clock()
    d = driver(tmp_path, c)
    d.inv.apply_fill("YES", "1", "0.19", source="test")
    c.tick()
    d.decide(TTE, book=CROSSED)
    rec = asyncio.run(d._recheck_position(_Pos(None)))
    assert rec["verdict"] == PL.UNREADABLE
    assert rec["terminal"] is False
    assert d.state == WC.RUNNING, "unreadable is a hold, not a funeral"
    assert d.inv.report()["yes_qty"] == "1", "and never becomes flat"


def test_a_position_disagreement_during_a_hold_is_terminal(tmp_path):
    c = Clock()
    d = driver(tmp_path, c)
    d.inv.apply_fill("YES", "1", "0.19", source="test")
    c.tick()
    d.decide(TTE, book=CROSSED)
    rec = asyncio.run(d._recheck_position(_Pos([])))   # venue flat, we hold 1
    assert rec["verdict"] == PL.DISAGREE
    assert rec["terminal"] is True
    assert d.state == WC.DO_NOT_TRADE
    assert "position disagreement" in d.why


def test_a_raising_position_reader_is_unreadable_not_flat(tmp_path):
    async def boom():
        raise RuntimeError("socket died")

    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    d.decide(TTE, book=CROSSED)
    rec = asyncio.run(d._recheck_position(boom))
    assert rec["verdict"] == PL.UNREADABLE
    assert "socket died" in rec["why"]
    assert d.state == WC.RUNNING


# ============================= the two EP013 reporting defects

def test_the_real_stop_reason_survives_the_withdrawal(tmp_path):
    """DEFECT 1. Every turn-stop used to be overwritten with one sentence
    about contract rollover, so EP013's summary said the contract rolled."""
    c = Clock()
    d = driver(tmp_path, c)
    for _ in range(WC.UNREADABLE_MAX_CONSEC + 1):
        c.tick()
        out = d.decide(TTE, book=CROSSED)
    assert d.state == WC.WITHDRAWING
    # what run_window will record, by the same expression it now uses
    stop_why = out.get("why")
    assert "unreadable" in stop_why
    assert "one contract only" not in stop_why


def test_n_rows_for_symbol_counts_rows_for_the_symbol():
    """DEFECT 2. EP013 reported '2 rows for this instrument' beside
    FLAT_NO_ROWS for a contract that was never traded; the 2 were another
    market's."""
    rows = [{"instrumentSymbol": "GEMI-NFL-SOMETHING-UP", "quantity": "1"},
            {"instrumentSymbol": "GEMI-NFL-OTHER-UP", "quantity": "1"}]
    assert PL.rows_for(rows, BTC15M) == []
    assert len(PL.rows_for(rows, "GEMI-NFL-OTHER-UP")) == 1
    assert PL.rows_for(None, BTC15M) == []


# ============================== the bound is the one the census measured

def test_the_bound_matches_the_measured_recovery_distribution():
    """5 cycles / 60s. scripts/gemini_unreadable_census.py: of 40 unreadable
    runs across 1,543 recorded cycles, every run that recovered did so within
    5 cycles (41.9s at the longest) and no run past 5 cycles ever came back.
    A bound that moves without that census moving is a guess."""
    assert WC.UNREADABLE_MAX_CONSEC == 5
    assert WC.UNREADABLE_MAX_S == 60.0
    assert WC.UNREADABLE_MAX_S >= WC.UNREADABLE_MAX_CONSEC * 9.4


# ================== what stays terminal, and is not softened by any of this

def test_a_contract_transition_is_still_terminal(tmp_path):
    """The window phase, not the book: WINDOW_WITHDRAWN still ends it."""
    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    out = d.decide(0.5, book=QUOTABLE)     # past the frozen window
    assert d.state == WC.WITHDRAWING
    assert out.get("unreadable_hold") is None


def test_the_deadman_and_the_risk_gates_are_not_holds(tmp_path):
    """Only an UNREADABLE SNAPSHOT is recoverable. A stale-but-readable state
    and a breached bound are policy stops and stay terminal."""
    c = Clock()
    d = driver(tmp_path, c)
    st = CP.state_from_venue(BTC15M, TTE, d.inv, now=c.t, book=QUOTABLE)
    old = dict(st, state_age_s=CP.DEADMAN_S + 10.0)
    turn = CP.plan(old, d.inv, action_size="1", now=c.t)
    assert turn["stop"] and "DEADMAN" in turn["stop"]
    assert old["inventory_known"] is True, \
        "a DEADMAN state is READABLE -- it must not reach the hold path"


# =========================================== THE WHOLE LOOP, end to end

AUTH = "TEST-UNREADABLE-RECOVERY"


class FakeSession:
    def __init__(self):
        self.sent = []

    async def request(self, method, params, timeout=5.0):
        self.sent.append({"method": method, "params": params})
        return {"status": 200, "result": {"orderId": len(self.sent)}}


class Positions:
    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return {"rows": []}


def _readers(books):
    """`books` is consumed one per read, then repeats its last entry."""
    def outcome_book(_symbol):
        return books.pop(0) if len(books) > 1 else books[0]
    return {"terms_for": lambda s: {"quantityMinimum": "0.01",
                                    "quantityIncrement": "0.01",
                                    "priceMinimum": "0.01",
                                    "priceIncrement": "0.01",
                                    "contract_status": "active",
                                    "market_state": "open"},
            "terms_status": lambda: {"hasAcceptedLatest": True,
                                     "acceptedVersion": 1},
            "outcome_book": outcome_book}


def _loop(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
    ).run_until_complete(coro)


@pytest.fixture
def _armed(monkeypatch):
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized(), WC.authorization_state()["why_not"]


def _window(tmp_path, monkeypatch, books, *, cycles):
    """One real run_window over a scripted book sequence."""
    from src.gemini_execution import metadata as MD
    c = Clock()
    seq = list(books)
    monkeypatch.setattr(MD, "outcome_book",
                        lambda s: seq.pop(0) if len(seq) > 1 else seq[0])

    async def refresh():
        # THE VENUE ECHOES BACK WHAT IT IS HOLDING. Returning [] here would
        # make every order we just placed an unexplained disappearance, which
        # is its own (correct) terminal stop and would hide the thing under
        # test.
        c.tick()
        return {"fills": [],
                "open_orders": [{"clientOrderId": o["client_order_id"],
                                 spec.ORDER_ID_FIELD: o.get("venue_order_id"),
                                 "outcome": o["leg"], "price": o["price"],
                                 "quantity": o["qty"],
                                 "resting_since": o.get("resting_since")}
                                for o in d.working.rungs()],
                "orders_seen": [], "trades": []}

    d = WC.Driver(AUTH, BTC15M, ledger_path=str(tmp_path / "m.jsonl"),
                  now_fn=c, envelope=LE.LIVE_Q1_BTC15M_CAP4)
    pos, sess = Positions(), FakeSession()
    rep = _loop(d.run_window(
        sess, tte_of=lambda: TTE, venue_fills=[], venue_open=[],
        venue_orders=[], readers=_readers(list(books)), refresh_fn=refresh,
        position_fn=pos, interval_s=0.0, max_cycles=cycles))
    return d, rep, sess, pos


def test_the_whole_loop_survives_one_crossed_tick(tmp_path, monkeypatch,
                                                  _armed):
    """END TO END, the EP013 shape with orders actually resting.

    cycle 1  clean    -> the frozen surface opens two maker orders
    cycle 2  CROSSED  -> HOLD: no place, no cancel, no replacement, and the
                        two orders are still resting when it is over
    cycle 3  clean    -> the surface is back and KEEPS both of them
    """
    d, rep, sess, pos = _window(
        tmp_path, monkeypatch,
        [QUOTABLE, CROSSED] + [QUOTABLE] * 6, cycles=4)

    plans = [t["plan"] for t in rep["turns"]]
    assert [t.get("unreadable_hold") for t in rep["turns"]] == \
        [None, "HOLD", None, None]
    assert plans[0]["place"] == 2, "cycle 1 must actually put orders on"
    assert plans[1] == {"keep": 0, "place": 0, "cancel": 0, "mutations": 0}
    assert plans[2]["keep"] == 2 and plans[2]["mutations"] == 0, \
        "the two orders survived the hold and were not re-placed"

    holds = rep.get("unreadable_holds") or []
    assert len(holds) == 1 and holds[0]["verdict"] == "HOLD"
    assert holds[0]["consecutive"] == 1
    assert holds[0]["resting"] == 2, "the orders were accounted for, not lost"
    assert holds[0]["position_recheck"]["verdict"] == "AGREE"
    assert pos.calls == 1, "the position is re-read while held, and only then"

    assert rep["unreadable"] == {
        "holds": 1, "bound": {"max_consecutive": WC.UNREADABLE_MAX_CONSEC,
                              "max_s": WC.UNREADABLE_MAX_S},
        "longest_run": 1, "bound_exceeded": False, "recoveries": 1,
        "note": rep["unreadable"]["note"]}

    # ONE place per order, and both of them before the hold
    places = [m for m in sess.sent if m["method"] == "order.place"]
    assert len(places) == 2
    assert sorted(m["params"]["price"] for m in places) == ["0.35", "0.58"]
    assert rep["cycles"] == 4, "the window ran on; the tick cost one cycle"
    assert rep["budget_consumed"] == 2, "a hold spends no mutation slot"


def test_the_whole_loop_withdraws_when_the_bound_is_exceeded(
        tmp_path, monkeypatch, _armed):
    n = WC.UNREADABLE_MAX_CONSEC + 1
    d, rep, s, pos = _window(tmp_path, monkeypatch, [CROSSED], cycles=n + 3)

    holds = rep.get("unreadable_holds") or []
    assert len(holds) == n
    assert [h["verdict"] for h in holds] == ["HOLD"] * (n - 1) + \
        ["BOUND_EXCEEDED"]
    assert rep["unreadable"]["bound_exceeded"] is True
    assert rep["unreadable"]["longest_run"] == n
    assert rep["state"] == WC.STOPPED_FLAT_ORDERS
    # DEFECT 1, end to end: the report says WHY, not "one contract only"
    assert "unreadable" in rep["stop_why"]
    assert "one contract only" not in rep["stop_why"]
    # nothing was ever placed on an unreadable book
    assert [m["method"] for m in s.sent if m["method"] == "order.place"] == []
    assert rep["budget_consumed"] in (None, 0)
    assert rep["orders_live_after_stop"] == 0


def test_unreadable_ORDERS_stay_terminal_even_while_the_book_is_held(
        tmp_path, monkeypatch, _armed):
    """Inability to know or control live orders is NOT the recoverable case.

    The book being unreadable is a transient. The venue's open-order list
    being unreadable is the one state from which withdrawal cannot be
    planned, and it stops the window exactly as it did before -- crossed
    book or not.
    """
    from src.gemini_execution import metadata as MD
    c = Clock()
    monkeypatch.setattr(MD, "outcome_book", lambda s: CROSSED)

    async def refresh():
        c.tick()
        return {"fills": [], "open_orders": None, "orders_seen": [],
                "trades": []}

    d = WC.Driver(AUTH, BTC15M, ledger_path=str(tmp_path / "m.jsonl"),
                  now_fn=c, envelope=LE.LIVE_Q1_BTC15M_CAP4)
    rep = _loop(d.run_window(
        FakeSession(), tte_of=lambda: TTE, venue_fills=[], venue_open=[],
        venue_orders=[], readers=_readers([CROSSED]), refresh_fn=refresh,
        position_fn=Positions(), interval_s=0.0, max_cycles=8))

    assert rep["stop_state"] == WC.DO_NOT_TRADE
    assert "unread venue is never an empty one" in rep["stop_why"]
    assert (rep.get("unreadable_holds") or []) == [], \
        "the order read fails first; no hold is entered"


def test_a_replayed_snapshot_cannot_be_laundered_by_a_second_replay(tmp_path):
    """The freshness watermark only ever rises.

    A stale snapshot re-enters the hold path, so a naive implementation would
    let its older clock overwrite the watermark -- and the NEXT replay of the
    original would then look fresh.
    """
    c = Clock()
    d = driver(tmp_path, c)
    c.tick()
    d.decide(TTE, book=CROSSED)
    high = d._unreadable_last_t

    c.t -= 60.0
    assert d.decide(TTE, book=QUOTABLE)["turn"] is None
    assert d._unreadable_last_t == high, "the watermark did not regress"

    c.t = high - 1.0
    assert d.decide(TTE, book=QUOTABLE)["turn"] is None, \
        "still older than the refusal, so still not a recovery"
    assert d._unreadable_n == 3
