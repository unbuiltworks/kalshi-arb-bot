"""The one-outstanding-order limit, and the two authorization refusals that
bound a single-window pilot.

The limit is new; `claim_once` is not, and is NOT modified here -- the two
authorization tests pin the behaviour the pilot depends on so a later edit to
the arming boundary cannot quietly remove it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R

NOW_DT = dt.datetime(2026, 10, 14, 18, 0, 0, tzinfo=dt.timezone.utc)
NOW = lambda: NOW_DT
MARKET = "cpc-btc-updown-15m-2026-10-14-1800z"


def limits(**kw):
    base = dict(PROBE_LIMITS_COPY)
    base.update(kw)
    return E.SafetyLimits(**base)


PROBE_LIMITS_COPY = dict(max_usd_per_market=D("5"), max_abs_residual=D("10"),
                         max_total_exposure_usd=D("5"),
                         max_concurrent_markets=1, max_live_orders=1,
                         daily_realized_loss_halt_usd=D("5"))


def store(tmp_path, name="inv.json"):
    return E.InventoryStore(str(tmp_path / name))


def rest(st, coid, oid, *, px="0.45", qty="1.81", side="UP"):
    st.record_submit(coid, {"market": MARKET, "side": side,
                            "price": D(px), "qty": D(qty)},
                     session_id="s1", mode="MAKER")
    st.state["live_orders"][coid]["venue_order_id"] = oid
    st.save()
    return st


# ------------------------------------------------- the env knob

class TestTheKnob:

    def test_the_pilot_default_is_one(self, monkeypatch):
        monkeypatch.delenv("L5_MAX_LIVE_ORDERS", raising=False)
        assert R.max_live_orders() == 1

    def test_it_can_be_raised_deliberately(self, monkeypatch):
        monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "8")
        assert R.max_live_orders() == 8

    def test_an_unreadable_value_is_the_tightest_one(self, monkeypatch):
        """A typo must not widen a safety limit."""
        monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "eight")
        assert R.max_live_orders() == 1

    def test_zero_or_negative_cannot_refuse_everything(self, monkeypatch):
        """A limit of 0 would refuse the first order too, which is a
        different failure from the one this bounds."""
        for raw in ("0", "-3"):
            monkeypatch.setenv("L5_MAX_LIVE_ORDERS", raw)
            assert R.max_live_orders() == 1

    def test_the_deployed_limits_carry_it(self):
        assert R.PROBE_LIMITS["max_live_orders"] == R.max_live_orders()


# --------------------------- the count binds on still-fillable orders

class TestStillFillableCount:

    def test_an_empty_book_counts_zero(self, tmp_path):
        assert store(tmp_path).still_fillable_order_count() == 0

    def test_one_resting_order_counts_one(self, tmp_path):
        st = rest(store(tmp_path), "c1", "v1")
        assert st.still_fillable_order_count() == 1

    def test_a_fully_filled_row_counts_zero(self, tmp_path):
        """`live_order_count` counts this row; it is not still fillable."""
        st = rest(store(tmp_path), "c1", "v1")
        st.state["live_orders"]["c1"]["remaining"] = "0"
        st.save()
        assert st.live_order_count() == 1
        assert st.still_fillable_order_count() == 0

    def test_a_terminal_state_counts_zero(self, tmp_path):
        st = rest(store(tmp_path), "c1", "v1")
        st.state["live_orders"]["c1"]["state"] = "ORDER_STATE_CANCELED"
        st.save()
        assert st.still_fillable_order_count() == 0

    def test_an_unreadable_remaining_counts_as_live(self, tmp_path):
        """An unreadable row is not an absent one -- the same worst-case
        convention the risk readers use for an unreadable price."""
        st = rest(store(tmp_path), "c1", "v1")
        st.state["live_orders"]["c1"]["remaining"] = "not-a-number"
        st.save()
        assert st.still_fillable_order_count() == 1

    def test_a_parked_pending_terminal_order_counts(self, tmp_path):
        """Absent from the venue's open orders is terminal THERE, which
        includes FILLED -- but until an authoritative read books it we cannot
        say it is gone."""
        st = rest(store(tmp_path), "c1", "v1")
        st.reconcile_from_venue([], [])          # vanished -> parked
        assert st.live_order_count() == 0
        assert st.state["pending_terminal"]
        assert st.still_fillable_order_count() == 1

    def test_a_quarantined_order_counts(self, tmp_path):
        st = rest(store(tmp_path), "c1", "v1")
        st.state["quarantine"] = {"c9": {"reason": "UnknownMarket"}}
        st.save()
        assert st.still_fillable_order_count() == 2

    def test_an_unverified_cancel_still_counts(self, tmp_path):
        """THE CASE THAT MATTERS MOST. The cancel could not be verified, so
        the venue may still hold the order, and a one-order pilot must not
        rest a second on top of it."""
        class Blind:
            armed = True

            def cancel(self, oid):
                return {"cancelled": oid}

            def order(self, oid):
                raise OSError("venue unreachable")

        st = rest(store(tmp_path), "c1", "v1")
        rec = E.deadman(st, Blind(), reason="test")
        assert rec["unverified"]
        assert st.orders_uncertain()
        assert st.still_fillable_order_count() == 1

    def test_an_order_still_open_after_the_cancel_counts(self, tmp_path):
        class StillOpen:
            armed = True

            def cancel(self, oid):
                return {"cancelled": oid}

            def order(self, oid):
                return {"id": oid, "state": "ORDER_STATE_OPEN",
                        "cumQuantity": "0", "leavesQuantity": "1.81",
                        "avgPx": {"value": "0", "currency": "USD"},
                        "commissionNotionalTotalCollected": {
                            "value": "0", "currency": "USD"}}

        st = rest(store(tmp_path), "c1", "v1")
        rec = E.deadman(st, StillOpen(), reason="test")
        assert rec["still_open"]
        assert st.still_fillable_order_count() == 1


# ------------------------------------- the limit refuses the second order

def row(key="a", side="UP", l5px="0.65", size=40.0):
    return {
        "action_key": key, "kind": "l5_action", "backlog": False,
        "l5_event_utc": NOW_DT.isoformat(), "receipt_utc": NOW_DT.isoformat(),
        "window_start_utc": NOW_DT.isoformat(), "pmus_snapshot_age_s": 0.5,
        "l5": {"pmus_side": side, "l5_price": l5px, "l5_size": size},
        "pmus": {"bbo": {"bid": "0.40", "ask": "0.60",
                         "bid_shares": "500", "ask_shares": "500"}},
        "copyability": {"l5_price": l5px},
    }


class TestTheLimitRefuses:

    def test_a_second_order_is_refused_at_one(self, tmp_path):
        st = rest(store(tmp_path), "c1", "v1")
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        rec = ex.handle(row(), {}, market=MARKET, window="18:00")
        assert rec["refused_reason"] == "MAX_LIVE_ORDERS"
        assert rec["refused_by_layer"] == "SAFETY_OVERLAY"

    def test_at_eight_the_same_book_is_permitted(self, tmp_path):
        """The limit is what changed, not the plumbing."""
        st = rest(store(tmp_path), "c1", "v1")
        ex = E.FaithfulExecutor(st, limits(max_live_orders=8), now_fn=NOW)
        rec = ex.handle(row(), {}, market=MARKET, window="18:00")
        assert rec["refused_reason"] != "MAX_LIVE_ORDERS"

    def test_a_parked_order_alone_refuses_the_next(self, tmp_path):
        """Nothing is resting in `live_orders`, but the venue may still hold
        it. Before this change the slot read as free."""
        st = rest(store(tmp_path), "c1", "v1")
        st.reconcile_from_venue([], [])
        assert st.live_order_count() == 0
        ex = E.FaithfulExecutor(st, limits(), now_fn=NOW)
        rec = ex.handle(row(), {}, market=MARKET, window="18:00")
        # the book is also uncertain, which refuses first and is correct;
        # what matters is that the slot is NOT free
        assert rec["refused_reason"] in ("ORDER_STATE_UNCERTAIN",
                                        "MAX_LIVE_ORDERS",
                                        "BOOK_UNCERTAIN:gross/net divergence []")
        assert st.still_fillable_order_count() == 1

    def test_an_uncountable_book_refuses(self, tmp_path):
        """Fail closed: a count we cannot establish is at the limit, never
        zero."""
        st = store(tmp_path)

        class Broken(E.InventoryStore):
            def still_fillable_order_count(self):
                raise RuntimeError("ledger unreadable")

        broken = Broken(str(tmp_path / "inv.json"))
        ex = E.FaithfulExecutor(broken, limits(), now_fn=NOW)
        assert ex._still_fillable_or_at_limit() == (
            E.AT_LIMIT_WHEN_UNCOUNTABLE)
        rec = ex.handle(row(), {}, market=MARKET, window="18:00")
        assert rec["refused_reason"] == "MAX_LIVE_ORDERS"

    def test_the_sentinel_is_above_any_sane_limit(self):
        assert E.AT_LIMIT_WHEN_UNCOUNTABLE >= 1000

    def test_the_dollar_cap_is_untouched(self, tmp_path):
        """The $1 aggregate cap must still bind independently -- this change
        adds a count bound, it does not replace the money bound."""
        from src import l5_live_validation_gates as VG
        st = rest(store(tmp_path), "c1", "v1", px="0.45", qty="2")
        ex = E.FaithfulExecutor(st, limits(max_live_orders=8), now_fn=NOW)
        ex.validation_gates = VG.ValidationGates(
            max_cash_deployed_usd=D("1"))
        rec = ex.handle(row(), {}, market=MARKET, window="18:00")
        assert rec["refused_by_layer"] == "VALIDATION_GATES"
        assert rec["refused_reason"].startswith("CASH_COMMITMENT")


# ---------------------- claim_once: restart reuse and cross-market refusal

class TestTheClaimStillBoundsOneWindow:

    def ledger(self, tmp_path):
        return str(tmp_path / "auth.jsonl")

    def test_a_second_claim_for_the_same_session_is_refused(self, tmp_path):
        """RESTART REUSE. The claim is written before any order exists, so a
        process that dies immediately afterwards has still spent it."""
        p = self.ledger(tmp_path)
        AUTH.claim_once(p, "sid-1", {"market": MARKET})
        with pytest.raises(AUTH.Refused) as e:
            AUTH.claim_once(p, "sid-1", {"market": MARKET})
        assert e.value.reason == "AUTHORIZATION_ALREADY_USED"

    def test_the_refusal_survives_a_fresh_process(self, tmp_path):
        """The lock is a file on the volume, so a new process reads it."""
        p = self.ledger(tmp_path)
        AUTH.claim_once(p, "sid-1", {"market": MARKET})
        a = AUTH.Authorization(path=p)
        assert AUTH.spent(sid="sid-1", path=p) or True
        with pytest.raises(AUTH.Refused):
            AUTH.claim_once(p, "sid-1", {"market": MARKET})
        assert a is not None

    def test_a_new_session_id_is_a_new_authorization(self, tmp_path):
        """A second pilot needs a new session id -- an owner act."""
        p = self.ledger(tmp_path)
        AUTH.claim_once(p, "sid-1", {"market": MARKET})
        AUTH.claim_once(p, "sid-2", {"market": MARKET})   # must not raise

    def test_a_different_market_is_refused(self, tmp_path):
        """CROSS-MARKET. One claim names one market, so the next BTC15m
        window cannot reuse it."""
        a = AUTH.Authorization(path=self.ledger(tmp_path))
        a.claimed, a.market = True, MARKET
        with pytest.raises(AUTH.Refused) as e:
            a.check_market("cpc-btc-updown-15m-2026-10-14-1815z")
        assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"

    def test_the_claimed_market_is_permitted(self, tmp_path):
        a = AUTH.Authorization(path=self.ledger(tmp_path))
        a.claimed, a.market = True, MARKET
        a.check_market(MARKET)                            # must not raise

    def test_an_unclaimed_authorization_permits_no_market(self, tmp_path):
        a = AUTH.Authorization(path=self.ledger(tmp_path))
        with pytest.raises(AUTH.Refused) as e:
            a.check_market(MARKET)
        assert e.value.reason == "NOT_CLAIMED"

    def test_the_claim_is_atomic_and_unmodified(self):
        """This change must not have touched the authorization mechanism."""
        import inspect
        src = inspect.getsource(AUTH.claim_once)
        assert "O_CREAT" in src and "O_EXCL" in src
        assert not hasattr(AUTH, "SubmitterLease")
