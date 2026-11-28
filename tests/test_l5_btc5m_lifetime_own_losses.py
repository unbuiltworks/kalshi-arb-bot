"""Earlier days' own losses reach the cumulative ceiling.

Only TODAY's own losses were added to the account's lifetime figure. A
loss from an earlier UTC day that the account service never booked -- a
fully matched window bought above 1.00 is invisible to it -- was in
neither service's cumulative total.
"""
import datetime as dt
import random
from decimal import Decimal as D

import pytest

from src import l5_btc5m_runner as RUN
from tests.test_l5_btc5m_runner import NOW, risk_doc

DAY = NOW.strftime("%Y-%m-%d")
CASH_READ = NOW - dt.timedelta(days=4)
REBASED = CASH_READ + dt.timedelta(minutes=8)
OLD = "cpc-btc-updown-5m-pre-anchor"          # settled before the cash read
GAP = "cpc-btc-updown-5m-between"             # after the read, before rebase
PRIOR = "cpc-btc-updown-5m-yesterday"         # an earlier UTC day
TODAY = "cpc-btc-updown-5m-today"


def hist(**rows):
    when = {OLD: CASH_READ - dt.timedelta(hours=1),
            GAP: CASH_READ + dt.timedelta(minutes=3),
            PRIOR: NOW - dt.timedelta(days=1), TODAY: NOW}
    return {m: {"at": when[m], "pnl": D(v)} for m, v in rows.items()}


def own(history):
    today = {m: r["pnl"] for m, r in history.items()
             if r["at"].date() == NOW.date()}
    return RUN.OwnSettled.of(today, history)


def basis(results, life="-40", **over):
    b = {"cash_read_utc": CASH_READ.isoformat(),
         "rebased_at_utc": REBASED.isoformat(),
         "results_since_rebase": dict(results), "rows": len(results),
         "complete": True, "reconciled": True, "lifetime_realized_usd": life}
    b.update(over)
    return b


def read(o, life="-40", day="0", **over):
    kw = dict(utc_day=DAY, utc_day_realized_usd=day,
              lifetime_realized_usd=life, daily_halt_usd="50",
              cumulative_loss_ceiling_usd="75")
    kw.update(over)
    doc = risk_doc(**kw)
    return RUN.account_risk(url="http://x", now=NOW, own_unaccounted=o,
                            fetch=lambda u: doc)


def life_of(r):
    return D(r["lifetime_realized_usd"])


# ================================================= the gap and its fix

def test_the_gap_as_it_was_a_prior_day_loss_never_reached_the_ceiling():
    #: no history supplied: only today's (none) is added
    r = read(RUN.OwnSettled.of({}))
    assert life_of(r) == D("-40")


def test_a_prior_day_loss_the_account_service_never_booked_is_added():
    r = read(own(hist(**{PRIOR: "-0.30"})), lifetime_basis=basis({}))
    assert life_of(r) == D("-40.30")
    assert r["btc5m_lifetime_basis"]["markets"]["in_full"] == 1
    assert D(r["utc_day_realized_usd"]) == 0          # the DAY is untouched


def test_a_prior_day_loss_booked_at_its_true_amount_is_not_added_again():
    r = read(own(hist(**{PRIOR: "-0.30"})),
             lifetime_basis=basis({PRIOR: "-0.30"}))
    assert life_of(r) == D("-40")
    assert r["btc5m_lifetime_basis"]["markets"]["gap_to_booked"] == 1


def test_booked_at_zero_adds_the_whole_loss():
    r = read(own(hist(**{PRIOR: "-0.30"})),
             lifetime_basis=basis({PRIOR: "0"}))
    assert life_of(r) == D("-40.30")


def test_a_result_inside_the_cash_anchor_is_not_added():
    r = read(own(hist(**{OLD: "-5"})), lifetime_basis=basis({}))
    assert life_of(r) == D("-40")
    assert r["btc5m_lifetime_basis"]["markets"]["inside_cash_anchor"] == 1


def test_settled_after_the_cash_read_but_before_the_rebase_is_added():
    """Not in the anchor's cash, and any row for it was replaced by the
    re-anchor: it is in neither part of the lifetime figure."""
    r = read(own(hist(**{GAP: "-0.20"})), lifetime_basis=basis({}))
    assert life_of(r) == D("-40.20")


def test_today_and_earlier_days_together():
    h = hist(**{OLD: "-5", PRIOR: "-0.30", TODAY: "-0.05"})
    r = read(own(h), day="-0.05", life="-40.05",
             lifetime_basis=basis({TODAY: "-0.05"}, life="-40.05"),
             retired_results_utc_day={"utc_day": DAY,
                                      "results": {TODAY: "-0.05"}})
    assert life_of(r) == D("-40.35")                  # only PRIOR is missing
    assert D(r["utc_day_realized_usd"]) == D("-0.05")


# ============================ incomplete reconciliation fails closed

ALL = {OLD: "-5", GAP: "-0.20", PRIOR: "-0.30", TODAY: "-0.05"}
EVERYTHING = D("-40") + D("-5.55")


@pytest.mark.parametrize("b,why", [
    (None, "BASIS_NOT_PUBLISHED"),
    ("x", "BASIS_NOT_PUBLISHED"),
    (basis({PRIOR: "-0.30"}, reconciled=False), "BASIS_NOT_RECONCILED"),
    (basis({PRIOR: "-0.30"}, reconciled="yes"), "BASIS_NOT_RECONCILED"),
    (basis({PRIOR: "-0.30"}, life="-39"), "BASIS_FOR_ANOTHER_FIGURE"),
    (basis({PRIOR: "-0.30"}, lifetime_realized_usd=None),
     "BASIS_FOR_ANOTHER_FIGURE"),
    (basis({PRIOR: "-0.30"}, results_since_rebase=[PRIOR]),
     "BASIS_UNREADABLE"),
    (basis({PRIOR: "-0.30"}, cash_read_utc="yesterday"), "BASIS_UNREADABLE"),
    (basis({PRIOR: "-0.30"}, cash_read_utc="2026-11-08T00:00:00"),
     "BASIS_UNREADABLE"),
])
def test_a_basis_that_does_not_reconcile_treats_nothing_as_booked(b, why):
    over = {} if b is None else {"lifetime_basis": b}
    r = read(own(hist(**ALL)), **over)
    assert life_of(r) == EVERYTHING                   # every loss, ever
    assert r["btc5m_lifetime_basis"]["basis_refusal"] == why
    assert r["btc5m_lifetime_basis"]["basis_used"] is False


def test_an_incomplete_map_books_only_what_it_names():
    r = read(own(hist(**{PRIOR: "-0.30", TODAY: "-0.05"})),
             lifetime_basis=basis({TODAY: "-0.05"}, complete=False))
    assert life_of(r) == D("-40.30")
    assert r["btc5m_lifetime_basis"]["basis_complete"] is False


@pytest.mark.parametrize("amount", ["?", None, "nan", [1]])
def test_an_unreadable_amount_adds_that_market_in_full(amount):
    r = read(own(hist(**{PRIOR: "-0.30"})),
             lifetime_basis=basis({PRIOR: amount}))
    assert life_of(r) == D("-40.30")


def test_with_no_reanchor_on_record_nothing_is_inside_an_anchor():
    r = read(own(hist(**{OLD: "-5"})),
             lifetime_basis=basis({}, cash_read_utc=None,
                                  rebased_at_utc=None))
    assert life_of(r) == D("-45")


def test_a_gain_on_an_earlier_day_never_offsets_a_loss():
    h = {"g": {"at": NOW - dt.timedelta(days=2), "pnl": D("3")},
         PRIOR: {"at": NOW - dt.timedelta(days=1), "pnl": D("-0.30")}}
    r = read(own(h), lifetime_basis=basis({}))
    assert life_of(r) == D("-40.30")


def test_the_larger_lifetime_loss_can_now_trip_the_cumulative_ceiling():
    h = hist(**{PRIOR: "-36"})
    r = read(own(h), lifetime_basis=basis({}))
    assert r["ok"] is False and "CUMULATIVE" in r["reason"]


def test_an_unreadable_own_history_refuses(tmp_path):
    from src import l5_btc5m_execution as X
    ex = X.Btc5mExecutor(transport=None,
                         ledger_path=str(tmp_path / "ledger.json"))
    ex._ledger["settled"] = {
        "a": {"settled_at": "2026-11-19T10:00:00+00:00",
              "settlement_pnl": "-1"}}
    assert ex.settled_history()["a"]["pnl"] == D("-1")
    ex._ledger["settled"]["b"] = {"settled_at": "?", "settlement_pnl": "1"}
    assert ex.settled_history() is None
    src = open(RUN.__file__, encoding="utf-8").read()
    i = src.index("    def _own_unaccounted(self)")
    body = src[i:src.index("\n    def ", i + 10)]
    assert "own settled history unreadable" in body
    assert "OWN_SETTLED_RESULT_UNREADABLE" in src


# ============================================ the guarantee, searched

def test_the_lifetime_figure_is_never_better_than_the_truth():
    """4,000 random histories. The account's lifetime figure is the cash
    anchor (which holds everything settled before its cash read, this
    lane's results included) plus what it booked since. TRUTH replaces
    each booking of a lane market with the lane's own result and adds the
    lane markets it never booked.

    With every booking published the combined figure is never above the
    truth, whatever was booked, and is the truth exactly when every lane
    market is inside the anchor or booked at its own result. With bookings
    withheld or the basis broken it is still never above the truth under
    one stated assumption, checked in the loop.
    """
    rng = random.Random(1120)
    c = lambda lo, hi: D(rng.randint(lo, hi)) / D(1000)        # noqa: E731
    modes = set()
    for _ in range(4000):
        anchor_cash = c(-50000, 0)
        other = c(-3000, 3000)
        h, booked, truth_extra = {}, {}, D(0)
        for i in range(rng.randint(1, 6)):
            m, o = "m%d" % i, c(-900, 900)
            where = rng.choice(["old", "gap", "prior", "today"])
            at = {"old": CASH_READ - dt.timedelta(hours=rng.randint(1, 99)),
                  "gap": CASH_READ + dt.timedelta(minutes=3),
                  "prior": NOW - dt.timedelta(days=rng.randint(1, 3)),
                  "today": NOW}[where]
            h[m] = {"at": at, "pnl": o}
            if where == "old":
                continue                   # inside anchor_cash already
            truth_extra += o
            if where != "gap":
                kind = rng.choice(["true", "bound", "zero", "gain",
                                   "absent", "half"])
                if kind != "absent":
                    booked[m] = {"true": o, "bound": -abs(c(0, 900)),
                                 "zero": D(0), "gain": abs(c(0, 600)),
                                 "half": o / 2}[kind]
        life = anchor_cash + other + sum(booked.values(), D(0))
        truth = anchor_cash + other + truth_extra
        mode = rng.choice(["ok", "ok", "ok", "none", "unreconciled",
                           "other_figure", "partial"])
        modes.add(mode)
        pubd = {m: str(v) for m, v in booked.items()}
        over = {}
        if mode == "ok":
            over["lifetime_basis"] = basis(pubd, life=str(life))
        elif mode == "unreconciled":
            over["lifetime_basis"] = basis(pubd, life=str(life),
                                           reconciled=False)
        elif mode == "other_figure":
            over["lifetime_basis"] = basis(pubd, life=str(life - 1))
        elif mode == "partial":
            keep = {m: v for m, v in pubd.items() if rng.random() < 0.5}
            over["lifetime_basis"] = basis(keep, life=str(life),
                                           complete=False)
        r = read(own(h), life=str(life), cumulative_loss_ceiling_usd="1000000000",
                 daily_halt_usd="1000000000", **over)
        got = life_of(r)
        assert got <= life                          # only ever adds loss
        seen = (set(over.get("lifetime_basis", {}).get(
            "results_since_rebase", {})) if mode in ("ok", "partial")
            else set())
        #: THE ONE ASSUMPTION, MADE EXPLICIT. A booking the lane is SHOWN
        #: needs none: the market counts for min(booked, own). A booking it
        #: is NOT shown is assumed to be no better than the larger of zero
        #: and the lane's own result -- true of both ways that service
        #: books (the $1 bound is never above zero; a restatement from
        #: venue cost is the result itself). `unseen_gain` is how far a
        #: case breaks that assumption; the figure is never above the
        #: truth by more than that, and by nothing when it holds.
        unseen_gain = sum(
            (max(D(0), booked[m] - max(D(0), h[m]["pnl"]))
             for m in booked if m not in seen), D(0))
        assert got <= truth + unseen_gain, (h, booked, mode)
        if mode not in ("ok", "partial"):
            assert got == life + sum(
                (min(D(0), r_["pnl"]) for r_ in h.values()), D(0))
        if mode == "ok":
            assert unseen_gain == 0 and got <= truth
            if all(booked.get(m) == h[m]["pnl"] for m in h
                   if h[m]["at"] > CASH_READ):
                assert got == truth
    assert modes == {"ok", "none", "unreconciled", "other_figure", "partial"}
