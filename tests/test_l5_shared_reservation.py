"""One account-wide reservation shared by four separate lane processes.

The four lanes have separate ledgers by design, so no lane can see
another's exposure. Without this file four lanes each checking a $10 cap
against their own books commit $40 between them. The decisive test here
is the concurrent one: real processes, real lock.
"""
from __future__ import annotations

import datetime as dt
import json
import multiprocessing as mp
import os
from decimal import Decimal as D

import pytest

from src import l5_shared_reservation as R

NOW = dt.datetime(2026, 11, 16, 15, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def f(tmp_path):
    return str(tmp_path / "resv.json")


class TestOneHoldAtATime:

    def test_a_hold_is_taken_and_counted(self, f):
        r = R.reserve(lane="BTC5M", key="b5m-1", usd="4.00", now=NOW,
                      file_path=f)
        assert r["ok"] is True and r["aggregate_usd"] == "4.00"
        assert R.aggregate(f)["usd"] == D("4.00")

    def test_the_same_order_asking_twice_is_one_risk(self, f):
        """Idempotent, so a retry after an uncertain write cannot
        double-count."""
        R.reserve(lane="BTC5M", key="b5m-1", usd="4.00", now=NOW, file_path=f)
        again = R.reserve(lane="BTC5M", key="b5m-1", usd="4.00", now=NOW,
                          file_path=f)
        assert again["ok"] is True and again["already_held"] is True
        assert R.aggregate(f)["usd"] == D("4.00")

    def test_releasing_frees_the_room(self, f):
        R.reserve(lane="BTC5M", key="b5m-1", usd="9.00", now=NOW, file_path=f)
        assert R.reserve(lane="ETH5M", key="e5m-1", usd="2.00", now=NOW,
                         file_path=f)["reason"] \
            == "RESERVATION_WOULD_EXCEED_CAP"
        assert R.release(lane="BTC5M", key="b5m-1", file_path=f)["released"]
        assert R.reserve(lane="ETH5M", key="e5m-1", usd="2.00", now=NOW,
                         file_path=f)["ok"] is True


class TestTheCapIsAccountWideNotPerLane:

    def test_four_lanes_cannot_each_take_the_whole_cap(self):
        """The defect this file exists to close."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            got = [R.reserve(lane=l, key=l + "-1", usd="4.00", now=NOW,
                             file_path=p)
                   for l in ("BTC5M", "ETH5M", "SOL5M", "XRP5M")]
            assert [g["ok"] for g in got] == [True, True, False, False]
            assert R.aggregate(p)["usd"] == D("8.00")

    def test_the_total_never_exceeds_the_cap(self, f):
        for i in range(40):
            R.reserve(lane="BTC5M", key="k%d" % i, usd="0.97", now=NOW,
                      file_path=f)
        assert R.aggregate(f)["usd"] <= R.MAX_AGGREGATE_USD

    def test_a_caller_may_ask_for_less_and_never_for_more(self, f):
        assert R.reserve(lane="BTC5M", key="a", usd="3.00", now=NOW,
                         cap_usd="4.00", file_path=f)["ok"] is True
        assert R.reserve(lane="BTC5M", key="b", usd="3.00", now=NOW,
                         cap_usd="4.00", file_path=f)["reason"] \
            == "RESERVATION_WOULD_EXCEED_CAP"
        assert R.reserve(lane="BTC5M", key="c", usd="1.00", now=NOW,
                         cap_usd="99.00", file_path=f)["reason"] \
            == "RESERVATION_CAP_ABOVE_CEILING"


def _worker(args):
    p, lane, n = args
    ok = 0
    for i in range(n):
        r = R.reserve(lane=lane, key="%s-%d" % (lane, i), usd="1.00",
                      now=NOW, file_path=p)
        if r.get("ok"):
            ok += 1
    return ok


class TestConcurrentLanes:
    """Read, decide and write happen inside ONE lock hold, so two lanes
    cannot both be told there is room only one can have."""

    def test_real_processes_never_overdraw_the_cap(self, tmp_path):
        p = str(tmp_path / "race.json")
        lanes = ["BTC5M", "ETH5M", "SOL5M", "XRP5M"]
        with mp.get_context("fork").Pool(4) as pool:
            granted = pool.map(_worker, [(p, l, 8) for l in lanes])
        total = R.aggregate(p)
        #: 32 attempts of $1 against a $10 cap: exactly 10 may succeed
        assert sum(granted) == 10, (granted, total)
        assert total["usd"] == D("10.00")
        assert total["holds"] == 10

    def test_every_grant_is_recorded_exactly_once(self, tmp_path):
        p = str(tmp_path / "race2.json")
        with mp.get_context("fork").Pool(4) as pool:
            pool.map(_worker, [(p, l, 5) for l in ("A", "B", "C", "D")])
        doc = json.loads(open(p).read())
        keys = list(doc["holds"])
        assert len(keys) == len(set(keys))
        assert sum(D(h["usd"]) for h in doc["holds"].values()) <= D("10.00")


class TestALaneMayTouchOnlyItsOwn:

    def test_one_lane_cannot_release_anothers_hold(self, f):
        """The one way this file could silently grant someone else's room
        away."""
        R.reserve(lane="BTC5M", key="b5m-1", usd="4.00", now=NOW, file_path=f)
        r = R.release(lane="ETH5M", key="b5m-1", file_path=f)
        assert r["ok"] is False
        assert r["reason"] == "RESERVATION_BELONGS_TO_ANOTHER_LANE"
        assert R.aggregate(f)["usd"] == D("4.00")

    def test_reconcile_releases_only_this_lanes_dead_orders(self, f):
        R.reserve(lane="BTC5M", key="b5m-live", usd="1.00", now=NOW, file_path=f)
        R.reserve(lane="BTC5M", key="b5m-dead", usd="1.00", now=NOW, file_path=f)
        R.reserve(lane="ETH5M", key="e5m-dead", usd="1.00", now=NOW, file_path=f)
        got = R.reconcile(lane="BTC5M", live_keys=["b5m-live"], file_path=f)
        assert got["released"] == ["b5m-dead"]
        #: the sibling's hold is untouched even though it is not live here
        assert R.aggregate(f)["usd"] == D("2.00")

    def test_reconcile_with_nothing_live_frees_only_its_own(self, f):
        R.reserve(lane="BTC5M", key="b5m-1", usd="1.00", now=NOW, file_path=f)
        R.reserve(lane="ETH5M", key="e5m-1", usd="1.00", now=NOW, file_path=f)
        R.reconcile(lane="BTC5M", live_keys=[], file_path=f)
        assert R.aggregate(f)["by_lane"] == {"ETH5M": "1.00"}


class TestLeaksBlockRatherThanOverdraw:

    def test_nothing_expires_on_a_timer(self, f):
        """A stale hold refuses trades; an expired one would permit them."""
        R.reserve(lane="BTC5M", key="old", usd="10.00",
                  now=NOW - dt.timedelta(days=30), file_path=f)
        assert R.aggregate(f)["usd"] == D("10.00")
        assert R.reserve(lane="ETH5M", key="new", usd="0.01", now=NOW,
                         file_path=f)["reason"] \
            == "RESERVATION_WOULD_EXCEED_CAP"


class TestItFailsClosed:

    @pytest.mark.parametrize("kw,reason", [
        ({"lane": ""}, "RESERVATION_KEY_MISSING"),
        ({"key": ""}, "RESERVATION_KEY_MISSING"),
        ({"usd": None}, "RESERVATION_AMOUNT_UNREADABLE"),
        ({"usd": "0"}, "RESERVATION_AMOUNT_UNREADABLE"),
        ({"usd": "-1"}, "RESERVATION_AMOUNT_UNREADABLE"),
        ({"usd": "nope"}, "RESERVATION_AMOUNT_UNREADABLE"),
        ({"cap_usd": None}, "RESERVATION_CAP_UNREADABLE"),
    ])
    def test_an_unusable_request_reserves_nothing(self, f, kw, reason):
        base = dict(lane="BTC5M", key="k", usd="1.00", now=NOW, file_path=f)
        base.update(kw)
        assert R.reserve(**base)["reason"] == reason

    def test_an_unset_path_refuses_every_operation(self, monkeypatch):
        monkeypatch.delenv(R.PATH_ENV, raising=False)
        assert R.reserve(lane="A", key="k", usd="1", now=NOW)["reason"] \
            == "RESERVATION_PATH_UNSET"
        assert R.release(lane="A", key="k")["reason"] == "RESERVATION_PATH_UNSET"
        assert R.aggregate()["reason"] == "RESERVATION_PATH_UNSET"
        assert R.reconcile(lane="A", live_keys=[])["reason"] \
            == "RESERVATION_PATH_UNSET"

    @pytest.mark.parametrize("text", ["not json", "[]", '{"holds": []}',
                                      '{"nope": 1}'])
    def test_a_corrupt_file_is_not_an_empty_budget(self, tmp_path, text):
        p = tmp_path / "bad.json"
        p.write_text(text, encoding="utf-8")
        assert R.reserve(lane="A", key="k", usd="1", now=NOW,
                         file_path=str(p))["reason"] \
            == "RESERVATION_FILE_UNREADABLE"
        assert R.aggregate(str(p))["usd"] is None

    def test_an_unreadable_hold_amount_refuses(self, tmp_path):
        p = tmp_path / "bad2.json"
        p.write_text(json.dumps({"holds": {"k": {"lane": "A", "usd": "x"}}}),
                     encoding="utf-8")
        assert R.aggregate(str(p))["usd"] is None
        assert R.reserve(lane="A", key="j", usd="1", now=NOW,
                         file_path=str(p))["reason"] \
            == "RESERVATION_FILE_UNREADABLE"

    def test_an_empty_file_is_an_empty_budget(self, tmp_path):
        """A file that exists but was never written is genuinely zero --
        distinct from one that cannot be parsed."""
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        assert R.aggregate(str(p))["usd"] == D("0")
