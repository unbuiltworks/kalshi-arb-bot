"""One controller is authoritative over the discretionary cancels.

THE ARCHITECTURE THIS FIXES. Four mechanisms could each withdraw or
withhold a second leg on their own judgement:

  1. `completion_requote`          cancels to reprice        DISCRETIONARY
  2. `acc_stale_by_distance`       cancels on drift          DISCRETIONARY
  3. `complement_order` + boundary places the complement
  4. `acc_order_deadline` TTL      cancels on age            MANDATORY

0100z lost its window to two of them disagreeing about the same 0.9500 pair
inside 21 seconds: the requote cancelled it at 01:00:40 to chase a 1.0000
pair, and the distance rule cancelled its replacement at 01:00:59. PR #200
and PR #202 each made one of them pair-aware, but pair-awareness is not
authority -- they still judged alone.

The disposition controller now runs FIRST on each tick, on the same `now`,
and the two DISCRETIONARY mechanisms ask it before acting.

WHAT IS DELIBERATELY NOT GATED, and this is the whole safety argument: the
per-order age TTL, the terminal sweep, the halt latch and the independent
watchdog bound REAL exposure. A decision layer able to suppress those would
trade an economic problem for a risk one. They are untouched.

AND THE GATE CAN ONLY EVER REFUSE. An absent, stale or unreadable verdict
falls back to the pre-existing behaviour, which already carries both PRs'
ceilings -- so nothing here can enable a cancel, a submission or a crossing.
"""
from __future__ import annotations

import collections
import inspect
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS
from src import l5_unmatched_disposition as UD

SLUG = "cpc-btc-updown-15m-2026-11-07-0100z"
WS = 1794013200.0
NOW = 1794013240.1147778


# ===================================================== 1. THE VERDICT STORE

def _bare(**kw):
    """A runner carrying only what the verdict store and the fingerprint
    read. The fingerprint needs a store and a book, because a verdict is
    now tied to the STATE it was formed on, not just to a timestamp."""
    r = SimpleNamespace(stats=collections.Counter(), _disposition_last={},
                        store=_Store({}), book=SimpleNamespace(
                            snapshot=lambda s: {"bid": D("0.51"),
                                                "ask": D("0.53"),
                                                "stream_age_s": 0.05}))
    r.__dict__.update(kw)
    for n in ("controller_verdict", "controller_preserves"):
        setattr(r, n, getattr(R.Runner, n).__get__(r))
    r.DISPOSITION_AUTHORITY_MAX_AGE_S = (
        R.Runner.DISPOSITION_AUTHORITY_MAX_AGE_S)
    return r


def _write_verdict(runner, slug, selected, when):
    """Record a verdict exactly as `maybe_unmatched_disposition` does:
    action, clock, and the fingerprint of the state it was formed on."""
    runner._disposition_last[str(slug)] = (
        selected, float(when), R.disposition_fingerprint(runner, slug))


def test_a_fresh_preserve_verdict_binds():
    r = _bare()
    _write_verdict(r, SLUG, "PRESERVE", NOW)
    assert r.controller_verdict(SLUG, NOW) == "PRESERVE"
    assert r.controller_preserves(SLUG, NOW) is True


@pytest.mark.parametrize("sel", ["REPRICE", "COMPLETE_MAKER", "HOLD",
                                 "COMPLETE_TAKER", "REDUCE", None])
def test_only_preserve_binds_the_discretionary_cancels(sel):
    r = _bare()
    _write_verdict(r, SLUG, sel, NOW)
    assert r.controller_preserves(SLUG, NOW) is False


def test_a_stale_verdict_does_not_bind_anything():
    """Older than one tick is from a book we are no longer looking at."""
    r = _bare()
    _write_verdict(r, SLUG, "PRESERVE", NOW - 10.0)
    assert r.controller_verdict(SLUG, NOW) is None
    assert r.controller_preserves(SLUG, NOW) is False


def test_an_absent_or_unreadable_verdict_is_not_a_veto():
    """NONE IS NOT A VETO: the fallback is the pre-existing behaviour, which
    already carries the PR #200 and #202 ceilings."""
    assert _bare().controller_preserves(SLUG, NOW) is False
    r = _bare()
    r._disposition_last = None
    assert r.controller_preserves(SLUG, NOW) is False

    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("gone")
    r2 = _bare()
    r2._disposition_last = _Boom()
    assert r2.controller_verdict(SLUG, NOW) is None


def test_another_markets_verdict_never_binds_this_one():
    r = _bare()
    _write_verdict(r, SLUG, "PRESERVE", NOW)
    r._disposition_last["cpc-btc-updown-15m-2026-11-07-0115z"] = (
        r._disposition_last.pop(SLUG))
    assert r.controller_preserves(SLUG, NOW) is False


# ============================ 2. THE REQUOTE DEFERS, AND CANCELS NOTHING

class _Store:
    def __init__(self, rows, *, resid=D("-1"), legs=None, uncertain=None):
        self._rows, self._resid = rows, resid
        # BOTH SIDES, always. `completion_ceiling` returns None when the
        # DEFICIENT side's cost is unreadable, which reads as
        # NO_COMPLETION_CEILING and masks whatever was being tested.
        self._legs = dict({"UP": D("0"), "DOWN": D("0")},
                          **(legs or {"DOWN": D("0.4900")}))
        self._uncertain = uncertain

    def live_orders(self):
        return dict(self._rows)

    def residual(self, slug):
        return self._resid

    def leg_cost(self, slug):
        return dict(self._legs)

    def position(self, slug, side):
        return {"DOWN": D("1"), "UP": D("0")}.get(side)

    def orders_uncertain(self):
        return self._uncertain


UP_ROW = {"market": SLUG, "side": "UP", "price": "0.4600", "qty": "1",
          "remaining": "1", "state": "LIVE", "venue_order_id": "DJHDVD5014ET",
          "t": "2026-11-07T01:00:00+00:00"}


#: A state the requote genuinely wants to move: basis 0.30 leaves the pair
#: ceiling room to target the touch, where the 0100z basis of 0.49 does not
#: -- there PR #200 caps the target at 0.47 against a 0.46 resting order.
MOVABLE = {"market": SLUG, "side": "UP", "price": "0.4000", "qty": "1",
           "remaining": "1", "state": "LIVE", "venue_order_id": "VM",
           "t": "2026-11-07T01:00:00+00:00"}


def _requote_runner(monkeypatch, *, bid=D("0.51"), ask=D("0.53"),
                    verdict=None, row=None, legs=None):
    """Drives the REAL `maybe_completion_requote`. `LX.deadman` is replaced
    by a tripwire: reaching it means an order was withdrawn."""
    fired = []
    monkeypatch.setattr(R.LX, "deadman",
                        lambda *a, **k: fired.append(k.get("only")) or {})
    monkeypatch.setattr(R, "completion_requote_enabled", lambda: True)
    # MARKET ACTIVATION IS NOT WHAT THIS TESTS. `TSP.active` reads the
    # two-sided activation env and has its own tests; here it would only
    # make every case return before the gate is reached.
    monkeypatch.setattr(TS, "active", lambda *a, **k: True)
    monkeypatch.setattr(R.AUTH, "class_budget_report",
                        lambda *a, **k: {"OPEN": {"remaining": 9}})
    monkeypatch.setattr(R.AUTH, "ledger_path", lambda *a, **k: "/dev/null")
    monkeypatch.setattr(R.AUTH, "experiment_id", lambda *a, **k: "t")
    r = SimpleNamespace(
        stats=collections.Counter(),
        _disposition_last={},
        transport=SimpleNamespace(armed=True, market_slug=SLUG,
                                  tick_size=D("0.01"),
                                  quantity_increment=D("1")),
        store=_Store({"c1": dict(row or UP_ROW)}, legs=legs),
        book=SimpleNamespace(snapshot=lambda s: {
            "bid": bid, "ask": ask, "stream_age_s": 0.061,
            "gap_suspected": False}),
        executor=SimpleNamespace(validation_gates=SimpleNamespace(
            max_worst_case_deficit_usd=D("5"))),
        _requote_queue_ahead_fn=lambda slug, side: (lambda px: D("100")),
        win_cancels={})
    for n in ("maybe_completion_requote", "controller_verdict",
              "controller_preserves"):
        setattr(r, n, getattr(R.Runner, n).__get__(r))
    r.DISPOSITION_AUTHORITY_MAX_AGE_S = (
        R.Runner.DISPOSITION_AUTHORITY_MAX_AGE_S)
    if verdict:
        _write_verdict(r, SLUG, verdict, NOW)
    return r, fired


def test_the_requote_stands_down_when_the_controller_preserves(monkeypatch):
    """A requote the ceiling WOULD permit, declined because the controller
    chose to keep the resting quote. Nothing is withdrawn."""
    # touch far enough away that `completion_requote` itself permits a move
    r, fired = _requote_runner(monkeypatch, bid=D("0.60"), ask=D("0.62"),
                               verdict="PRESERVE", row=MOVABLE,
                               legs={"DOWN": D("0.30")})
    assert r.maybe_completion_requote(NOW) is None
    assert fired == [], "an order was cancelled under a PRESERVE verdict"
    assert r.stats["requote_refused:CONTROLLER_SELECTED_PRESERVE"] == 1


def test_the_requote_proceeds_when_the_controller_chose_to_reprice(
        monkeypatch):
    """Proves the gate is authority, not paralysis."""
    r, fired = _requote_runner(monkeypatch, bid=D("0.60"), ask=D("0.62"),
                               verdict="REPRICE", row=MOVABLE,
                               legs={"DOWN": D("0.30")})
    r.maybe_completion_requote(NOW)
    assert fired == [["c1"]], fired
    assert r.stats["requote_cancelled"] == 1


def test_with_no_verdict_the_requote_behaves_exactly_as_before(monkeypatch):
    r, fired = _requote_runner(monkeypatch, bid=D("0.60"), ask=D("0.62"),
                               row=MOVABLE, legs={"DOWN": D("0.30")})
    r.maybe_completion_requote(NOW)
    assert fired == [["c1"]]


def test_the_pr200_ceiling_still_refuses_first(monkeypatch):
    """The gate is the LAST word, not the only one: the 0100z touch is
    still refused by the pair ceiling before authority is consulted."""
    r, fired = _requote_runner(monkeypatch, verdict="REPRICE")
    assert r.maybe_completion_requote(NOW) is None
    assert fired == []
    assert r.stats["requote_refused:IMPROVEMENT_BELOW_MIN_TICKS"] == 1


# ================== 3. THE RECOVERY DEFERS; THE MANDATORY TTL DOES NOT

def test_the_distance_recovery_is_behind_the_gate():
    src = inspect.getsource(R.Runner.maybe_acc_deadline)
    i = src.find("controller_preserves")
    j = src.find("acc_stale_by_distance(self, now, slug, live")
    assert i != -1 and j != -1 and i < j, "recovery is not gated"


def test_the_mandatory_age_deadline_is_not_gated():
    """The TTL bounds REAL exposure. A decision layer able to suppress it
    would trade an economic problem for a risk one."""
    src = inspect.getsource(R.Runner.maybe_acc_deadline)
    code = [ln for ln in src.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    body = "\n".join(code)
    # the overdue computation and its cancel are reached regardless
    assert "if not overdue:" in body
    gate = body.find("controller_preserves")
    assert body.find("overdue") < gate, "the TTL now sits behind the gate"
    # and the one cancel route is still scoped by exact id
    assert "only=" in body


def test_the_watchdog_and_halt_paths_are_untouched_by_authority():
    for fn in (R.Runner.maybe_halt_deadman, R.Runner.maybe_cancel_orphans):
        assert "controller_preserves" not in inspect.getsource(fn)


# ====== 4. THE INVARIANT: A 0.95 PAIR IS NOT REPLACED BY A WORSE ONE

RECORDED = [
    # slug, held side, held basis, resting own px, touch (bid, ask)
    ("0100z", "DOWN", D("0.4900"), D("0.4600"), D("0.51"), D("0.53")),
    ("1215z", "UP", D("0.3693"), D("0.4700"), D("0.47"), D("0.48")),
    ("1245z", "UP", D("0.3200"), D("0.6300"), D("0.63"), D("0.64")),
]


@pytest.mark.parametrize("name,held,basis,rest,bid,ask", RECORDED)
def test_no_mechanism_replaces_a_keepable_pair_with_a_dearer_one(
        name, held, basis, rest, bid, ask):
    """All three authenticated cases, against every mechanism that could
    withdraw the quote. The resting pair is inside the 0.96 cap in each.

    NO FILL IS ASSERTED. These are controller verdicts on recorded book
    states; whether the preserved quote would have filled is unknown.
    """
    cap = TS.max_pair_cost()
    pair_rest = basis + rest
    assert pair_rest <= cap, (name, pair_rest)

    comp = "DOWN" if held == "UP" else "UP"
    # 1. the controller keeps it
    rec = UD.decide(
        market=name, held_side=held, held_qty=D("1"), held_cost_usd=basis,
        resting_own_px=rest, resting_qty=D("1"),
        bid=bid, ask=ask, tick=D("0.01"), min_qty=D("1"),
        now_s=WS + 40.0, window_start_s=WS, window_end_s=WS + 900.0,
        book_age_s=0.05, residual_budget_usd=D("5"))
    assert rec["selected"] == UD.PRESERVE, (name, rec["selected"])
    # THE SELECTED ACTION never buys a dearer pair than the one already
    # resting. Dearer alternatives may still be LISTED and priced -- that
    # is the point of the record, and 1215z has an executable REPRICE at
    # 0.8893 against a resting 0.8393. What matters is that it lost.
    sel = next(a for a in rec["actions"] if a["action"] == rec["selected"])
    assert D(sel["pair_own_cost"]) == pair_rest, (name, sel)
    dearer = [a["action"] for a in rec["actions"]
              if a["executable_now"] and a["pair_own_cost"] is not None
              and D(a["pair_own_cost"]) > pair_rest]
    assert rec["selected"] not in dearer, (name, dearer)
    for d in dearer:
        assert {x["action"]: x["why"] for x in rec["rejected"]}[d] == (
            "RANKED_BELOW_SELECTED"), (name, d)

    # 2. THE RAW REQUOTE IS NOT THE INVARIANT, AND 1215z PROVES WHY. Left
    #    to itself `completion_requote` would move own 0.4700 to 0.5200 --
    #    a 0.8393 pair to 0.8893 -- which PR #200's ceiling permits because
    #    it bounds the PAIR CAP, not the resting pair. Both are profitable,
    #    but giving up $0.05 of certain edge buys only queue position, and
    #    `l5_queue_depletion` establishes that no fill probability exists to
    #    justify that trade. So the invariant is enforced one level up: the
    #    controller selected PRESERVE, and the authority gate is what makes
    #    that stick.
    rq = TS.completion_requote(
        side=comp, resting_own_px=rest, resting_qty=D("1"),
        resting_filled_qty=D("0"), bid=bid, ask=ask, tick=D("0.01"),
        min_qty=D("1"), held_qty=D("1"), held_cost_usd=basis,
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        residual_budget_usd=D("5"), now_s=WS + 40.0, window_start_s=WS,
        window_end_s=WS + 900.0, book_age_s=0.05,
        queue_ahead_fn=lambda px: D("100"), require_queue_evidence=True)
    if rq.get("act"):
        # never past the CAP, whatever the controller then decides
        assert (basis + D(rq["to_price"])) <= cap, (name, rq)
        # and the gate stands it down, because the controller said PRESERVE
        gated = _bare()
        _write_verdict(gated, name, UD.PRESERVE, WS + 40.0)
        assert gated.controller_preserves(name, WS + 40.0) is True

    # 3. the distance recovery does not strand it
    rc = TS.recover_stale_leg(
        side=comp, own_px=rest, qty=D("1"), remaining=D("1"), state="LIVE",
        submitted_t=WS, now=WS + 40.0, bid=bid, ask=ask, tick=D("0.01"),
        depth_ticks=TS.depth_ticks(), book_age_s=0.05, recoveries_used=0,
        held_qty=D("1"), held_cost_usd=basis)
    if rc.get("recover"):
        repl = TS.quote_own_price(comp, bid=bid, ask=ask, tick=D("0.01"),
                                  depth_ticks=TS.depth_ticks())
        assert repl is not None and (basis + repl) <= cap, (name, rc)


def test_a_dearer_pair_needs_an_authorized_risk_reduction(monkeypatch):
    """The ONLY way the 0.95 pair is given up for a dearer completion is an
    explicitly authorized, economically justified risk reduction -- and
    with the route off there is no such path."""
    base = dict(market=SLUG, held_side="DOWN", held_qty=D("1"),
                held_cost_usd=D("0.4900"), resting_own_px=D("0.4600"),
                resting_qty=D("1"), bid=D("0.51"), ask=D("0.53"),
                tick=D("0.01"), min_qty=D("1"), now_s=NOW,
                window_start_s=WS, window_end_s=WS + 900.0,
                book_age_s=0.061, residual_budget_usd=D("5"))
    off = UD.decide(**base)
    assert off["selected"] == UD.PRESERVE
    assert off["taker_route_enabled"] is False
    # with the owner's route on, a CERTAIN loss-capping action can win --
    # and it is a risk reduction, priced, not a cheaper pair
    on = UD.decide(**dict(base, taker_enabled=True))
    assert on["selected"] in UD.CROSSING
    sel = next(a for a in on["actions"] if a["action"] == on["selected"])
    assert sel["worst_case_usd"] == sel["best_case_usd"]      # certain
    assert D(sel["worst_case_usd"]) > D("-0.4900")            # justified


# =============== 5. A DUCK-TYPED STUB MUST NOT BE GATED INTO SILENCE
#
# I built the first version of this gate as `self.controller_preserves(...)`
# and it broke 39 tests across three suites, because every lightweight
# runner stub grafts `maybe_completion_requote` and `maybe_acc_deadline`
# onto an object carrying only what those methods touched. The warning was
# already written in `acc_stale_by_distance`'s own docstring. The gate is
# now a module-level function reading `runner` through getattr.

def test_the_gate_is_a_module_function_not_a_method_call():
    src = inspect.getsource(R.Runner.maybe_completion_requote)
    assert "controller_preserves(self, slug, now)" in src
    assert "self.controller_preserves(" not in src
    src2 = inspect.getsource(R.Runner.maybe_acc_deadline)
    assert "controller_preserves(self, slug, now)" in src2
    assert "self.controller_preserves(" not in src2


def test_an_object_without_the_accessor_is_not_gated():
    """Absent is not a veto: the fallback is the pre-existing behaviour."""
    assert R.controller_preserves(SimpleNamespace(), SLUG, NOW) is False
    assert R.controller_preserves(object(), SLUG, NOW) is False
    assert R.controller_preserves(None, SLUG, NOW) is False


def test_an_accessor_that_raises_is_not_a_veto_either():
    def boom(*a, **k):
        raise RuntimeError("gone")
    assert R.controller_preserves(
        SimpleNamespace(controller_verdict=boom), SLUG, NOW) is False


def test_a_stub_runner_still_reaches_its_requote(monkeypatch):
    """The regression in one assertion: a bare stub must still cancel."""
    r, fired = _requote_runner(monkeypatch, bid=D("0.60"), ask=D("0.62"),
                               row=MOVABLE, legs={"DOWN": D("0.30")})
    del r.controller_verdict
    del r.controller_preserves
    r.maybe_completion_requote(NOW)
    assert fired == [["c1"]], "a stub without the accessor was gated"


# ========== 6. REVIEW ITEM 2: A FUTURE-DATED VERDICT MUST NOT VETO
#
# `age > MAX` alone let a future verdict through, because a negative age is
# not greater than 3.0. A clock rollback, an NTP step or a bad write could
# therefore veto EVERY discretionary cancellation indefinitely -- the one
# failure mode of this gate that is a risk problem rather than an economic
# one. The test is now two-sided.

@pytest.mark.parametrize("ahead_s", [0.001, 1.0, 5.0, 3600.0, 86400.0])
def test_a_future_dated_verdict_never_binds(ahead_s):
    r = _bare()
    _write_verdict(r, SLUG, "PRESERVE", NOW + ahead_s)
    assert r.controller_verdict(SLUG, NOW) is None, ahead_s
    assert r.controller_preserves(SLUG, NOW) is False, ahead_s


def test_a_clock_rollback_cannot_freeze_the_discretionary_cancels(
        monkeypatch):
    """End to end: the requote must still act when `now` jumps backwards."""
    r, fired = _requote_runner(monkeypatch, bid=D("0.60"), ask=D("0.62"),
                               verdict="PRESERVE", row=MOVABLE,
                               legs={"DOWN": D("0.30")})
    assert r.maybe_completion_requote(NOW) is None       # binds normally
    assert fired == []
    r.maybe_completion_requote(NOW - 10.0)               # clock rolled back
    assert fired == [["c1"]], "a future verdict froze the cancel"


@pytest.mark.parametrize("age", [0.0, 1.5, 2.999])
def test_the_freshness_window_is_inclusive_of_the_present(age):
    r = _bare()
    _write_verdict(r, SLUG, "PRESERVE", NOW - age)
    assert r.controller_preserves(SLUG, NOW) is True, age


@pytest.mark.parametrize("age", [3.001, 10.0, 900.0])
def test_past_the_window_it_stops_binding(age):
    r = _bare()
    _write_verdict(r, SLUG, "PRESERVE", NOW - age)
    assert r.controller_preserves(SLUG, NOW) is False, age


# ===== 7. REVIEW ITEM 2b/4: THE VERDICT IS TIED TO THE STATE IT JUDGED

def test_the_fingerprint_covers_inventory_orders_and_the_book():
    """Named explicitly, so a future change cannot silently drop one."""
    r = _bare(store=_Store({"c1": dict(UP_ROW)}))
    fp = R.disposition_fingerprint(r, SLUG)
    assert fp is not None
    for piece in ("-1", "0.4900", "0.51", "0.53", "c1", "0.4600", "LIVE"):
        assert piece in fp, piece


@pytest.mark.parametrize("mutate,why", [
    (lambda r: setattr(r.store, "_resid", D("-2")), "residual changed"),
    (lambda r: r.store._legs.update({"DOWN": D("0.4000")}), "basis changed"),
    (lambda r: setattr(r.store, "_uncertain", "UNVERIFIED_SUBMIT"),
     "orders became uncertain"),
    (lambda r: r.store._rows.clear(), "the resting order went terminal"),
    (lambda r: r.store._rows["c1"].update({"remaining": "0.4"}),
     "a partial fill landed"),
    (lambda r: r.store._rows["c1"].update({"price": "0.5000"}),
     "the order was re-priced"),
    (lambda r: setattr(r, "book", SimpleNamespace(
        snapshot=lambda s: {"bid": D("0.60"), "ask": D("0.62"),
                            "stream_age_s": 0.05})), "the book moved"),
])
def test_any_state_change_between_decisions_stops_the_veto(mutate, why):
    """REVIEW ITEM 4, and it does not depend on tick order: a verdict formed
    on inventory that has since changed must not veto a cancellation."""
    r = _bare(store=_Store({"c1": dict(UP_ROW)}))
    _write_verdict(r, SLUG, "PRESERVE", NOW)
    assert r.controller_preserves(SLUG, NOW) is True, why
    mutate(r)
    assert r.controller_preserves(SLUG, NOW) is False, why
    assert r.stats["disposition:STATE_CHANGED_SINCE_VERDICT"] == 1, why


def test_an_unreadable_state_cannot_be_matched_and_so_cannot_veto():
    r = _bare(store=_Store({"c1": dict(UP_ROW)}))
    _write_verdict(r, SLUG, "PRESERVE", NOW)

    def boom(_s):
        raise RuntimeError("feed gone")
    r.book = SimpleNamespace(snapshot=boom)
    assert R.disposition_fingerprint(r, SLUG) is None
    assert r.controller_preserves(SLUG, NOW) is False


def test_a_verdict_with_no_fingerprint_is_refused_not_trusted():
    """The legacy 2-tuple shape, e.g. a verdict written by older code."""
    r = _bare()
    r._disposition_last[SLUG] = ("PRESERVE", NOW)
    assert r.controller_verdict(SLUG, NOW) == "PRESERVE"
    assert r.controller_preserves(SLUG, NOW) is False
    assert r.stats["disposition:VERDICT_WITHOUT_FINGERPRINT"] == 1


def test_a_racing_fill_breaks_the_veto_so_the_cancel_route_proceeds(
        monkeypatch):
    """The cancellation race, end to end. The deadline route polls the
    venue BEFORE it cancels; that reconciliation is exactly what changes
    the state and releases the gate."""
    r, fired = _requote_runner(monkeypatch, bid=D("0.60"), ask=D("0.62"),
                               verdict="PRESERVE", row=MOVABLE,
                               legs={"DOWN": D("0.30")})
    assert r.maybe_completion_requote(NOW) is None
    assert fired == []
    assert r.stats["requote_refused:CONTROLLER_SELECTED_PRESERVE"] == 1

    # THE VENUE BOOKS A PARTIAL FILL on our resting completion.
    r.store._rows["c1"]["remaining"] = "0.4"
    r.store._legs["UP"] = D("0.24")

    # The veto is released immediately -- the verdict no longer describes
    # this inventory. Asserted on the gate itself, because after a partial
    # fill `completion_requote` also refuses on its OWN terms and returns
    # before the gate is consulted; that is correct, and it means the
    # end-to-end call cannot observe the release.
    assert R.controller_preserves(r, SLUG, NOW) is False
    assert r.stats["disposition:STATE_CHANGED_SINCE_VERDICT"] >= 1

    # and nothing was cancelled while the veto was in force
    assert fired == []


# ============ 8. REVIEW ITEM 3: ORDER OF THE SCHEDULE, PROVEN

def test_the_controller_runs_before_BOTH_discretionary_paths():
    import re
    src = inspect.getsource(R.Runner.tick)
    head = src[:src.index("self.maybe_preflight(now)")]
    order = [m.group(1) for m in
             re.finditer(r'_sc\.mark\("([a-z_]+)"\)', head)]
    i = order.index("maybe_unmatched_disposition")
    assert i < order.index("maybe_acc_deadline"), order
    assert i < order.index("maybe_completion_requote"), order


def test_the_mandatory_paths_still_run_before_the_controller():
    """The halt deadman and the orphan sweep bound real exposure and must
    not wait on a decision layer."""
    import re
    src = inspect.getsource(R.Runner.tick)
    head = src[:src.index("self.maybe_preflight(now)")]
    order = [m.group(1) for m in
             re.finditer(r'_sc\.mark\("([a-z_]+)"\)', head)]
    i = order.index("maybe_unmatched_disposition")
    assert order.index("maybe_halt_deadman") < i, order
    assert order.index("maybe_cancel_orphans") < i, order
