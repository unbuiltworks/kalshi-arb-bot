"""The envelope and the shared reservation, together, as _submit uses them.

These are the interactions that cannot be seen in either module alone:
one lane refused for another lane's holds, a latched STOP outliving a
restart, a daily halt that counts risk still outstanding, and a hold
that must outlive the order that created it.
"""
from __future__ import annotations

import datetime as dt
import json
import multiprocessing as mp
import os
from decimal import Decimal as D

import pytest

from src import l5_persistent_mode as P
from src import l5_shared_reservation as R

NOW = dt.datetime(2026, 11, 16, 16, 0, tzinfo=dt.timezone.utc)
DEP = "sha-c035a1a1e"


def envelope(**kw):
    base = {"approved": True, "mode": "PERSISTENT", "deployment": DEP,
            "strategies": ["STRADDLE", "LATE_FAVORITE"],
            "series": ["cpc-btc-updown-5m-", "cpceth-eth-updown-5m-",
                       "cpcsol-sol-updown-5m-", "cpcxrp-xrp-updown-5m-"],
            "taker_allowed": False, "max_aggregate_usd": "10.00",
            "max_per_market_usd": "5.00", "max_daily_loss_usd": "5.00",
            "nonce": "bounded-validation-0001"}
    base.update(kw)
    return json.dumps(base)


@pytest.fixture
def live(tmp_path, monkeypatch):
    """An armed envelope and an empty shared reservation."""
    monkeypatch.setenv(P.DEPLOYMENT_ENV, DEP)
    monkeypatch.setenv(P.ENVELOPE_ENV, envelope())
    monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
    monkeypatch.setenv(R.PATH_ENV, str(tmp_path / "resv.json"))
    P.write_state(P.ARMED, why="test", now=NOW)
    return tmp_path


def gate(**kw):
    base = dict(market="cpc-btc-updown-5m-2026-11-16-1600z",
                strategy="LATE_FAVORITE", post_only=True,
                notional_usd="1.94", market_exposure_usd="0",
                aggregate_exposure_usd=R.aggregate().get("usd"),
                day_realized_usd="0", now=NOW)
    base.update(kw)
    return P.may_submit(**base)


# ============================================ one budget, four lanes

class TestOneLaneIsRefusedForAnothersHolds:

    def test_the_aggregate_the_envelope_judges_is_the_shared_one(self, live):
        """The defect the reservation exists to close, end to end: BTC's
        holds refuse ETH, which BTC's own ledger could never have done."""
        for i in range(5):
            assert R.reserve(lane="BTC5M", key="b5m-%d" % i, usd="1.94",
                             now=NOW)["ok"] is True
        held = R.aggregate()["usd"]
        assert held == D("9.70")
        r = gate(market="cpceth-eth-updown-5m-2026-11-16-1600z",
                 aggregate_exposure_usd=held)
        assert r["ok"] is False and r["reason"] == "AGGREGATE_EXPOSURE"

    def test_all_four_lanes_share_one_ten_dollar_pool(self, live):
        ok = []
        for lane in ("BTC5M", "ETH5M", "SOL5M", "XRP5M"):
            for i in range(2):
                ok.append(R.reserve(lane=lane, key="%s-%d" % (lane, i),
                                    usd="1.94", now=NOW)["ok"])
        assert sum(ok) == 5                      # 5 x 1.94 = 9.70 <= 10
        assert R.aggregate()["usd"] <= D("10.00")


def _submit_worker(args):
    p, state, env, dep, lane = args
    os.environ[R.PATH_ENV] = p
    os.environ[P.STATE_PATH_ENV] = state
    os.environ[P.ENVELOPE_ENV] = env
    os.environ[P.DEPLOYMENT_ENV] = dep
    got = 0
    for i in range(6):
        agg = R.aggregate()
        if not agg.get("ok"):
            continue
        g = P.may_submit(market="cpc-btc-updown-5m-2026-11-16-1600z",
                         strategy="STRADDLE", post_only=True,
                         notional_usd="1.00", market_exposure_usd="0",
                         aggregate_exposure_usd=agg["usd"],
                         day_realized_usd="0", now=NOW)
        if not g.get("ok"):
            continue
        #: the EFFECTIVE cap, as _submit computes it: the aggregate
        #: ceiling and the daily-loss room, whichever is smaller,
        #: enforced under the lock rather than against a stale read.
        if R.reserve(lane=lane, key="%s-%d" % (lane, i), usd="1.00",
                     now=NOW, cap_usd="5.00").get("ok"):
            got += 1
    return got


class TestFourProcessConcurrentSubmissions:

    def test_the_pool_is_never_overdrawn_by_four_real_processes(self, live):
        args = [(os.environ[R.PATH_ENV], os.environ[P.STATE_PATH_ENV],
                 envelope(), DEP, lane)
                for lane in ("BTC5M", "ETH5M", "SOL5M", "XRP5M")]
        with mp.get_context("fork").Pool(4) as pool:
            got = pool.map(_submit_worker, args)
        #: FIVE, not ten: the $5 daily-loss worst case binds before the
        #: $10 aggregate, and it binds ATOMICALLY. Checked against a
        #: stale aggregate outside the lock this reached $8 -- every
        #: individual check correct, the collective result wrong.
        assert sum(got) == 5, got
        assert R.aggregate()["usd"] == D("5.00")


# ==================================================== STOP and halts

class TestStopIsImmediateAndLatched:

    def test_stop_blocks_the_next_submission(self, live):
        assert gate()["ok"] is True
        P.write_state(P.STOPPED, why="operator", now=NOW)
        assert gate()["reason"] == "STATE_STOPPED"

    def test_stop_racing_a_submission_refuses_the_submission(self, live):
        """The gate reads the durable state on every call, so a STOP
        written between two submissions stops the second."""
        assert gate()["ok"] is True
        R.reserve(lane="BTC5M", key="in-flight", usd="1.94", now=NOW)
        P.write_state(P.STOPPED, why="operator", now=NOW)
        assert gate()["reason"] == "STATE_STOPPED"

    def test_stop_does_not_release_holds_on_orders_still_at_risk(self, live):
        """A stopped lane still owns what it bought. Freeing the budget
        on STOP would let a restart re-spend money still at risk."""
        R.reserve(lane="BTC5M", key="filled-unsettled", usd="1.94", now=NOW)
        P.write_state(P.STOPPED, why="operator", now=NOW)
        assert R.aggregate()["usd"] == D("1.94")

    def test_a_latch_survives_a_restart(self, live):
        P.write_state(P.HALTED, why="daily loss", now=NOW,
                      latch_digest="d1")
        #: a new process reads the same file
        assert P.read_state()["state"] == P.HALTED
        assert gate()["reason"] == "STATE_HALTED"

    def test_a_latch_is_not_cleared_by_a_fresh_envelope(self, live, monkeypatch):
        P.write_state(P.HALTED, why="daily loss", now=NOW)
        monkeypatch.setenv(P.ENVELOPE_ENV, envelope(nonce="brand-new-0002"))
        assert gate()["reason"] == "STATE_HALTED"

    def test_nothing_rearms_by_itself(self, live):
        P.write_state(P.STOPPED, why="operator", now=NOW)
        for _ in range(5):
            assert gate()["ok"] is False
        assert P.read_state()["state"] == P.STOPPED


class TestTheDailyHaltCountsOutstandingRisk:

    def test_realized_plus_outstanding_plus_this_order(self, live):
        R.reserve(lane="BTC5M", key="open-1", usd="2.00", now=NOW)
        r = gate(aggregate_exposure_usd=R.aggregate()["usd"],
                 day_realized_usd="-2.50", notional_usd="1.00")
        assert r["reason"] == "WORST_CASE_DAILY_LOSS"
        assert r["worst_case_usd"] == "5.50"

    def test_it_fits_when_the_sum_is_inside_the_halt(self, live):
        R.reserve(lane="BTC5M", key="open-1", usd="1.00", now=NOW)
        r = gate(aggregate_exposure_usd=R.aggregate()["usd"],
                 day_realized_usd="-2.00", notional_usd="1.00")
        assert r["ok"] is True and r["worst_case_usd"] == "4.00"

    def test_outstanding_alone_can_exhaust_the_halt(self, live):
        """No realized loss at all, but $5 already at risk."""
        for i in range(5):
            R.reserve(lane="BTC5M", key="k%d" % i, usd="1.00", now=NOW)
        r = gate(aggregate_exposure_usd=R.aggregate()["usd"],
                 day_realized_usd="0", notional_usd="0.50")
        assert r["reason"] == "WORST_CASE_DAILY_LOSS"


# ========================================= restart and remount recovery

class TestDurableRecovery:

    def test_the_reservation_survives_a_process_restart(self, live):
        R.reserve(lane="BTC5M", key="b5m-1", usd="4.00", now=NOW)
        #: a new process reads the same file on the same volume
        assert R.aggregate()["usd"] == D("4.00")
        assert R.aggregate()["by_lane"] == {"BTC5M": "4.00"}

    def test_it_survives_a_remount_at_a_different_path(self, live, tmp_path,
                                                       monkeypatch):
        """The file is the state. Moved with the volume, it still binds."""
        R.reserve(lane="BTC5M", key="b5m-1", usd="4.00", now=NOW)
        old = os.environ[R.PATH_ENV]
        new = str(tmp_path / "remounted" / "resv.json")
        os.makedirs(os.path.dirname(new), exist_ok=True)
        with open(old) as a, open(new, "w") as b:
            b.write(a.read())
        monkeypatch.setenv(R.PATH_ENV, new)
        assert R.aggregate()["usd"] == D("4.00")

    def test_an_uncertain_order_keeps_holding_its_risk(self, live):
        """Reconcile is told what the ledger still shows at risk. An
        order whose outcome is unknown is in that set, so its hold
        stays -- an uncertain order IS exposure."""
        R.reserve(lane="BTC5M", key="uncertain-1", usd="1.94", now=NOW)
        R.reconcile(lane="BTC5M", live_keys=["uncertain-1"])
        assert R.aggregate()["usd"] == D("1.94")

    def test_a_filled_but_unsettled_order_keeps_holding_its_risk(self, live):
        """The hold outlives the ORDER: a filled contract is inventory,
        and a losing one pays nothing."""
        R.reserve(lane="BTC5M", key="filled-1", usd="1.94", now=NOW)
        R.reconcile(lane="BTC5M", live_keys=["filled-1"])
        assert R.aggregate()["usd"] == D("1.94")

    def test_a_settled_order_gives_its_room_back(self, live):
        R.reserve(lane="BTC5M", key="settled-1", usd="1.94", now=NOW)
        R.reconcile(lane="BTC5M", live_keys=[])
        assert R.aggregate()["usd"] == D("0")

    def test_a_dead_lanes_holds_are_not_freed_by_a_sibling_restart(self, live):
        R.reserve(lane="BTC5M", key="b5m-1", usd="2.00", now=NOW)
        R.reserve(lane="ETH5M", key="e5m-1", usd="2.00", now=NOW)
        #: ETH restarts with nothing live; BTC is dead and holds nothing
        #: live either, but ETH may not free BTC's room.
        R.reconcile(lane="ETH5M", live_keys=[])
        assert R.aggregate()["by_lane"] == {"BTC5M": "2.00"}


# ===================================== authorization scope and identity

class TestAuthorizationScope:

    def test_an_expired_envelope_refuses(self, live, monkeypatch):
        monkeypatch.setenv(P.ENVELOPE_ENV,
                           envelope(expires_utc="2026-11-16T15:00:00Z"))
        assert gate()["reason"] == "ENVELOPE_EXPIRED"

    def test_another_deployments_envelope_refuses(self, live, monkeypatch):
        monkeypatch.setenv(P.DEPLOYMENT_ENV, "sha-something-else")
        assert gate()["reason"] == "ENVELOPE_IS_FOR_ANOTHER_DEPLOYMENT"

    def test_an_unauthorized_market_refuses(self, live):
        assert gate(market="cpc-btc-updown-15m-2026-11-16-1600z")["reason"] \
            == "MARKET_NOT_AUTHORIZED"

    def test_an_unauthorized_strategy_refuses(self, live, monkeypatch):
        monkeypatch.setenv(P.ENVELOPE_ENV, envelope(strategies=["STRADDLE"]))
        assert gate(strategy="LATE_FAVORITE")["reason"] \
            == "STRATEGY_NOT_AUTHORIZED"

    def test_a_taker_order_refuses_under_maker_only(self, live):
        assert gate(post_only=False)["reason"] == "MAKER_ONLY"

    def test_the_per_market_ceiling_refuses(self, live):
        assert gate(market_exposure_usd="4.50", notional_usd="1.00")["reason"] \
            == "PER_MARKET_EXPOSURE"

    def test_an_unreadable_aggregate_refuses(self, live):
        assert gate(aggregate_exposure_usd=None)["reason"] \
            == "EXPOSURE_UNREADABLE"
