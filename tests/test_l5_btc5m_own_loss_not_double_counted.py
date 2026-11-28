"""This lane's settled loss is not added to an account figure that already
contains it -- and the correction cannot hide a loss that is not there.

Live 2026-11-20 17:08Z: the account service had adopted, retired and
restated the 14:20Z window (-0.0114), and this lane added the same -0.0114
again: the day read -0.0228 against a true -0.0114.

THE FIRST FIX WAS UNSAFE AND IS PINNED HERE AS SUCH. It skipped any market
the account service LISTED as retired. A listed market is not a booked
loss: that service adopts only the NET position and first books it at a
$1-a-share bound, which is ZERO when the residual side wins, and it also
retires rows at zero. So the rule is the gap, per market:
add min(0, own - booked), and all of it when no amount is published.
"""
import datetime as dt
import random
from decimal import Decimal as D

import pytest

from src import l5_btc5m_runner as RUN
from tests.test_l5_btc5m_runner import NOW, risk_doc

DAY = NOW.strftime("%Y-%m-%d")
M1 = "cpc-btc-updown-5m-2026-11-20-1420z"
M2 = "cpc-btc-updown-5m-2026-11-20-1705z"
M3 = "cpc-btc-updown-5m-2026-11-20-1800z"


def pub(results, day=DAY):
    return {"utc_day": day, "results": dict(results)}


def read(own, account_day="-0.0114", account_life="-39.4794", **over):
    doc = risk_doc(utc_day=DAY, utc_day_realized_usd=account_day,
                   lifetime_realized_usd=account_life, daily_halt_usd="50",
                   cumulative_loss_ceiling_usd="75", **over)
    return RUN.account_risk(url="http://x", now=NOW, own_unaccounted=own,
                            fetch=lambda u: doc)


def own(**by):
    return RUN.OwnSettled.of({k: D(v) for k, v in by.items()})


# ============================================= the defect and its fix

def test_the_live_double_count_as_it_was():
    r = read(D("-0.0114"))
    assert r["utc_day_realized_usd"] == "-0.0228"
    assert r["lifetime_realized_usd"] == "-39.4908"


def test_a_loss_booked_at_its_true_amount_is_not_added_again():
    r = read(own(**{M1: "-0.0114"}),
             retired_results_utc_day=pub({M1: "-0.0114"}))
    assert r["ok"] is True
    assert D(r["utc_day_realized_usd"]) == D("-0.0114")
    assert D(r["lifetime_realized_usd"]) == D("-39.4794")
    assert D(r["btc5m_losses_not_yet_in_account_ledger_usd"]) == 0
    assert r["btc5m_shortfall_added_by_market"] == {M1: "0"}


def test_a_loss_booked_harsher_than_true_is_not_added_again():
    #: 17:05Z before restatement: booked -0.44 at the bound, true -0.0151
    r = read(own(**{M2: "-0.0151"}), account_day="-0.4514",
             retired_results_utc_day=pub({M2: "-0.44"}))
    assert D(r["utc_day_realized_usd"]) == D("-0.4514")


# ====================== it cannot suppress a loss that is not booked

def test_listed_at_zero_adds_the_whole_loss():
    """The case the first fix got wrong: the residual side won, the bound
    booked zero, and the matched pairs lost money here."""
    r = read(own(**{M2: "-0.05"}), account_day="0",
             retired_results_utc_day=pub({M2: "0"}), retired_markets=[M2])
    assert D(r["utc_day_realized_usd"]) == D("-0.05")
    assert r["btc5m_shortfall_added_by_market"] == {M2: "-0.05"}


def test_booked_at_less_than_the_true_loss_adds_exactly_the_gap():
    r = read(own(**{M2: "-0.05"}), account_day="-0.02",
             retired_results_utc_day=pub({M2: "-0.02"}))
    assert D(r["utc_day_realized_usd"]) == D("-0.05")


def test_booked_as_a_gain_while_this_lane_lost_adds_the_gap():
    r = read(own(**{M2: "-0.05"}), account_day="0.10",
             retired_results_utc_day=pub({M2: "0.10"}))
    assert D(r["utc_day_realized_usd"]) == D("-0.05")


def test_the_market_name_alone_is_never_enough():
    r = read(own(**{M1: "-0.0114"}), retired_markets=[M1])
    assert D(r["utc_day_realized_usd"]) == D("-0.0228")     # all added


@pytest.mark.parametrize("published", [
    None, "x", 7, [M1], {}, {"utc_day": DAY}, {"results": {M1: "-0.0114"}},
    {"utc_day": DAY, "results": [M1]},
    {"utc_day": "2026-11-11", "results": {M1: "-0.0114"}},   # another day
])
def test_an_absent_stale_or_malformed_publication_adds_everything(published):
    over = {} if published is None else {"retired_results_utc_day": published}
    r = read(own(**{M1: "-0.0114"}), **over)
    assert D(r["utc_day_realized_usd"]) == D("-0.0228")
    assert r["btc5m_shortfall_added_by_market"] == {}
    assert r["btc5m_markets_added_in_full"] == [M1]


@pytest.mark.parametrize("amount", ["?", "", None, "nan", "inf", [1]])
def test_an_unreadable_amount_for_one_market_adds_that_market_in_full(amount):
    r = read(own(**{M1: "-0.0114", M2: "-0.0151"}), account_day="-0.0151",
             retired_results_utc_day=pub({M1: amount, M2: "-0.0151"}))
    assert D(r["utc_day_realized_usd"]) == D("-0.0151") + D("-0.0114")
    assert r["btc5m_markets_added_in_full"] == [M1]


def test_a_market_the_account_service_has_not_reached_is_added_in_full():
    """An INCOMPLETE ledger: this lane settled first."""
    r = read(own(**{M1: "-0.0114", M3: "-0.03"}),
             retired_results_utc_day=pub({M1: "-0.0114"}))
    assert D(r["utc_day_realized_usd"]) == D("-0.0114") + D("-0.03")
    assert r["btc5m_markets_added_in_full"] == [M3]


def test_a_total_without_a_breakdown_is_added_whatever_is_published():
    r = read(D("-0.0114"), retired_results_utc_day=pub({M1: "-0.0114"}))
    assert D(r["utc_day_realized_usd"]) == D("-0.0228")


def test_an_unavailable_account_record_still_refuses_outright():
    def boom(_u):
        raise TimeoutError()
    r = RUN.account_risk(url="http://x", now=NOW,
                         own_unaccounted=own(**{M1: "-0.0114"}), fetch=boom)
    assert r["ok"] is False and "UNREADABLE" in r["reason"]


def test_a_stale_account_record_still_refuses_outright():
    doc = risk_doc(utc_day=DAY, retired_results_utc_day=pub({M1: "-9"}))
    doc["as_of_utc"] = (NOW - dt.timedelta(seconds=31)).isoformat()
    r = RUN.account_risk(url="http://x", now=NOW,
                         own_unaccounted=own(**{M1: "-0.0114"}),
                         fetch=lambda u: doc)
    assert r["ok"] is False and r["reason"] == "ACCOUNT_RISK_STALE"


def test_a_gain_is_never_added_and_never_offsets_another_markets_loss():
    r = read(own(**{M1: "0.50", M2: "-0.0151"}), account_day="0",
             retired_results_utc_day=pub({}))
    assert D(r["utc_day_realized_usd"]) == D("-0.0151")
    r = read(own(**{M1: "0.50", M2: "-0.0151"}), account_day="0.50",
             retired_results_utc_day=pub({M1: "0.50"}))
    assert D(r["utc_day_realized_usd"]) == D("0.50") + D("-0.0151")


# ============================================ the guarantee, searched

def test_the_combined_figure_is_never_better_than_this_lanes_own_result():
    """Searched over 4,000 random ledgers.

    Model: the account's day figure is `other` plus what it ACTUALLY booked
    for each lane market -- the truth, a harsher bound, zero, half, a gain,
    or nothing at all. What it PUBLISHES for a market is that amount, an
    unreadable value, or nothing; and the whole publication may be absent,
    for another day, or only a list of names.

    THREE THINGS ARE ASSERTED ON EVERY CASE:
      1. the lane only ever ADDS loss to the account's figure;
      2. a market with a readable published amount counts for at most the
         lane's own result -- min(booked, own) -- so nothing it lost there
         can vanish;
      3. everything else adds every loss in full, market by market, and
         no gain: a market without a readable amount, and every market
         when there is no usable publication at all.
    """
    rng = random.Random(20261120)
    cents = lambda lo, hi: D(rng.randint(lo, hi)) / D(1000)   # noqa: E731
    seen = set()
    for _ in range(4000):
        markets = ["m%d" % i for i in range(rng.randint(1, 5))]
        o = {m: cents(-600, 600) for m in markets}
        booked, published = {}, {}
        for m in markets:
            kind = rng.choice(["true", "bound", "zero", "gain", "absent",
                               "garbage", "partial"])
            if kind == "absent":
                continue                     # not retired there at all
            booked[m] = {"true": o[m], "bound": -abs(cents(0, 900)),
                         "zero": D(0), "gain": abs(cents(0, 600)),
                         "garbage": cents(-600, 600),
                         "partial": o[m] / 2}[kind]
            if kind == "garbage" and rng.random() < 0.5:
                published[m] = rng.choice(["?", None, "nan"])
            else:
                published[m] = str(booked[m])
        other = cents(-2000, 2000)
        account_day = other + sum(booked.values(), D(0))
        over = {}
        mode = rng.choice(["ok", "ok", "ok", "none", "wrong_day", "list"])
        if mode == "ok":
            over["retired_results_utc_day"] = pub(published)
        elif mode == "wrong_day":
            over["retired_results_utc_day"] = pub(published, "2026-01-01")
        elif mode == "list":
            over["retired_markets"] = sorted(published)
        r = read(RUN.OwnSettled.of(o), account_day=str(account_day),
                 account_life="-10", **over)
        combined = D(r["utc_day_realized_usd"])
        seen.add(mode)
        assert combined <= account_day                           # 1
        if mode != "ok":
            assert combined == account_day + sum(                # 3
                (min(D(0), v) for v in o.values()), D(0)), mode
            continue
        expect = other
        for m in markets:
            b = booked.get(m)
            readable = (m in published
                        and RUN._dec(published[m]) is not None)
            if readable:
                assert RUN._dec(published[m]) == b
                expect += min(b, o[m])                           # 2
                assert min(b, o[m]) <= o[m]
            else:
                expect += (b or D(0)) + min(D(0), o[m])          # 3
        assert combined == expect, (o, booked, published)
    assert seen == {"ok", "none", "wrong_day", "list"}


def test_the_addition_is_exact_when_everything_is_published_truthfully():
    rng = random.Random(7)
    for _ in range(500):
        o = {"m%d" % i: D(rng.randint(-500, 500)) / D(1000)
             for i in range(rng.randint(1, 4))}
        account_day = D("-1") + sum(o.values(), D(0))
        r = read(RUN.OwnSettled.of(o), account_day=str(account_day),
                 retired_results_utc_day=pub({m: str(v)
                                              for m, v in o.items()}))
        assert D(r["utc_day_realized_usd"]) == account_day


# ================================================== plumbing

def test_own_settled_is_still_the_decimal_total():
    v = own(**{M1: "-0.0114", M2: "-0.0151"})
    assert v == D("-0.0265") and isinstance(v, D) and str(v) == "-0.0265"


def test_the_executor_breaks_the_day_down_by_market(tmp_path):
    from src import l5_btc5m_execution as X
    ex = X.Btc5mExecutor(transport=None,
                         ledger_path=str(tmp_path / "ledger.json"))
    at = dt.datetime(2026, 11, 20, 17, 8, tzinfo=dt.timezone.utc)
    ex._ledger["settled"] = {
        M1: {"settled_at": "2026-11-20T14:26:14+00:00",
             "settlement_pnl": "-0.0114"},
        "old": {"settled_at": "2026-11-19T10:00:00+00:00",
                "settlement_pnl": "-9"}}
    assert ex.day_settled_by_market(at) == {M1: D("-0.0114")}
    assert sum(ex.day_settled_by_market(at).values()) \
        == ex.day_settled_pnl(at)
    ex._ledger["settled"]["bad"] = {"settled_at": "?", "settlement_pnl": "1"}
    assert ex.day_settled_by_market(at) is None


def test_without_a_publication_a_gain_no_longer_cancels_a_loss():
    """The hole in the deployed fallback. A fully matched window bought
    above 1.00 lost 0.03 here and is invisible to the account service; a
    second window's winning residual was booked there as +0.05. The old
    fallback added min(0, -0.03 + 0.05) = 0 and the loss vanished."""
    lane = own(**{M1: "-0.03", M2: "0.05"})
    old = read(D("0.02"), account_day="0.05")          # total, no breakdown
    assert D(old["utc_day_realized_usd"]) == D("0.05")           # as it was
    new = read(lane, account_day="0.05")
    assert D(new["utc_day_realized_usd"]) == D("0.02")           # the truth
    both = read(lane, account_day="0.05",
                retired_results_utc_day=pub({M2: "0.05"}))
    assert D(both["utc_day_realized_usd"]) == D("0.02")
