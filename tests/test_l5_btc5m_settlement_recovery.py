"""A window whose process died never recorded its settlement.

`settle()` runs at the END of a window, so a redeploy, a crash or an OOM in
the middle of one leaves that market out of the ledger's `settled` block --
and `_own_exposure` skips ONLY settled markets, so the dead window's filled
cost is charged against the account cap forever.

Observed live 2026-11-12: $4.97 of own exposure against an $8.00 cap on
markets the venue reported holding zero contracts for, which refused the
0.09 and 0.10 rungs of the straddle ladder on every window of the evening.

`recover_settlements` is bookkeeping. It must free that exposure using the
venue's own settlementPx and nothing else, and it must REFUSE to guess.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from decimal import Decimal as D

from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as R

MARKET = "cpc-btc-updown-5m-2026-11-12-2310z"
OTHER = "cpc-btc-updown-5m-2026-11-12-2315z"
T0 = dt.datetime(2026, 11, 12, 23, 45, tzinfo=dt.timezone.utc)


class Venue:
    """Reads nothing; the recovery never touches the order path."""

    def open_orders(self):
        return []

    def positions(self):
        return []          # the venue holds nothing -- the live observation


def executor(tmp_path, venue=None):
    return X.Btc5mExecutor(venue or Venue(),
                           str(tmp_path / "btc5m" / "ledger.json"),
                           now_fn=lambda: T0)


def ghost(ex, market=MARKET, side=None, qty="5", price="0.08"):
    """The state a killed window leaves: a filled order, no settlement.

    The coid must carry `COID_PREFIX`: `_validated` rejects the WHOLE
    ledger over a row it does not recognise as ours, which is why a
    hand-rolled id made the ghost silently vanish at restart instead.
    """
    side = side or X.C.UP
    coid = "%sghost-%s-%s" % (X.COID_PREFIX, market[-5:], side)
    ex._ledger["orders"][coid] = {
        "client_order_id": coid, "market": market, "side": side,
        "price": price, "qty": qty, "venue_order_id": "V" + coid,
        "state": X.FILLED, "filled_qty": qty,
        "filled_cost": str(D(price) * D(qty)),
        "created_at": T0.isoformat(), "reason": "STRADDLE_LEG",
        "slot": 0, "klass": "OPEN", "claim_sid": "s1"}
    #: inventory too, or `_validated` rejects the whole ledger at restart
    #: with "inventory disagrees with booked fills" -- the real fill path
    #: writes both, so a faithful ghost must as well.
    leg = ex._ledger["inventory"].setdefault(market, {}).setdefault(
        side, {"qty": "0", "cost": "0"})
    leg["qty"] = str(D(leg["qty"]) + D(qty))
    leg["cost"] = str(D(leg["cost"]) + D(price) * D(qty))
    ex._save_or_halt()
    return coid


class Settles:
    """A venue that reports one terminal, binary settlement per market."""

    def __init__(self, table):
        self.table = table
        self.asked = []

    def settlement(self, slug):
        self.asked.append(slug)
        px = self.table.get(slug)
        if px is None:
            return {"market": slug, "state": "MARKET_STATE_OPEN",
                    "settlement_px": None,
                    "not_terminal_reason": "STATE_NOT_TERMINAL"}
        return {"market": slug, "state": "MARKET_STATE_EXPIRED",
                "settlement_px": D(px), "not_terminal_reason": None}


def transport_for(venue):
    return lambda slug: venue


# ------------------------------------------------------- the ghost itself

class TestTheGhostIsReal:

    def test_a_dead_window_is_charged_against_the_cap(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        assert ex.account_exposure()["own_usd"] == D("0.40")

    def test_and_it_survives_a_restart(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        again = executor(tmp_path)
        assert again.account_exposure()["own_usd"] == D("0.40")

    def test_while_the_venue_holds_nothing(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        acct = ex.account_exposure()
        assert acct["other_position_contracts"] == D(0)
        assert acct["other_usd"] == D(0)


# ------------------------------------------------------------- recovering

class TestRecovery:

    def test_frees_the_exposure(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        v = Settles({MARKET: 1})
        out = R.recover_settlements(ex, transport_for(v))
        assert out["error"] is None
        assert out["exposure_before_usd"] == "0.40"
        assert out["exposure_after_usd"] == "0"
        assert out["freed_usd"] == "0.40"
        assert ex.account_exposure()["own_usd"] == D(0)

    def test_records_the_venues_outcome_not_a_guess(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        out = R.recover_settlements(ex, transport_for(Settles({MARKET: 0})))
        assert out["recovered"][0]["up_won"] is False
        assert ex._ledger["settled"][MARKET]["up_won"] is False

    def test_up_won_when_the_venue_says_one(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        R.recover_settlements(ex, transport_for(Settles({MARKET: 1})))
        assert ex._ledger["settled"][MARKET]["up_won"] is True

    def test_survives_a_restart(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        R.recover_settlements(ex, transport_for(Settles({MARKET: 1})))
        assert executor(tmp_path).account_exposure()["own_usd"] == D(0)

    def test_several_markets_at_once(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex, MARKET)
        ghost(ex, OTHER)
        assert ex.account_exposure()["own_usd"] == D("0.80")
        out = R.recover_settlements(
            ex, transport_for(Settles({MARKET: 1, OTHER: 0})))
        assert len(out["recovered"]) == 2
        assert ex.account_exposure()["own_usd"] == D(0)

    def test_is_idempotent(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        v = Settles({MARKET: 1})
        R.recover_settlements(ex, transport_for(v))
        out = R.recover_settlements(ex, transport_for(v))
        #: nothing left to consider, so nothing is asked again
        assert out["considered"] == []
        assert out["recovered"] == []
        assert ex.account_exposure()["own_usd"] == D(0)


# ------------------------------------------- what it must REFUSE to do

class TestRefusals:

    def test_leaves_a_market_the_venue_has_not_settled(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        out = R.recover_settlements(ex, transport_for(Settles({})))
        assert out["recovered"] == []
        assert out["left"][0]["why"] == "STATE_NOT_TERMINAL"
        #: STILL charged -- an unsettled window is real exposure
        assert ex.account_exposure()["own_usd"] == D("0.40")

    def test_refuses_a_non_binary_settlement(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)

        class Half:
            def settlement(self, slug):
                #: the expired book's last price is NOT a settlement
                return {"market": slug, "state": "MARKET_STATE_EXPIRED",
                        "settlement_px": D("0.01"),
                        "not_terminal_reason": None}

        out = R.recover_settlements(ex, transport_for(Half()))
        assert out["left"][0]["why"] == "SETTLEMENT_NOT_BINARY"
        assert ex.account_exposure()["own_usd"] == D("0.40")

    def test_refuses_a_missing_price_even_when_terminal(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)

        class NoPx:
            def settlement(self, slug):
                return {"market": slug, "state": "MARKET_STATE_EXPIRED",
                        "settlement_px": None, "not_terminal_reason": None}

        out = R.recover_settlements(ex, transport_for(NoPx()))
        assert out["left"][0]["why"] == "SETTLEMENT_NOT_BINARY"
        assert ex.account_exposure()["own_usd"] == D("0.40")

    def test_a_read_failure_leaves_the_market_charged(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)

        class Broken:
            def settlement(self, slug):
                raise OSError("boom")

        out = R.recover_settlements(ex, transport_for(Broken()))
        assert out["left"][0]["why"] == "READ_FAILED:OSError"
        assert out["recovered"] == []
        assert ex.account_exposure()["own_usd"] == D("0.40")

    def test_never_reverses_a_settlement_already_recorded(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)
        R.recover_settlements(ex, transport_for(Settles({MARKET: 1})))
        assert ex._ledger["settled"][MARKET]["up_won"] is True
        #: the ledger no longer lists it, so a contradicting venue is not
        #: even consulted -- and the recorded outcome stands.
        out = R.recover_settlements(ex, transport_for(Settles({MARKET: 0})))
        assert out["considered"] == []
        assert ex._ledger["settled"][MARKET]["up_won"] is True

    def test_one_bad_market_does_not_stop_the_others(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex, MARKET)
        ghost(ex, OTHER)

        class Mixed:
            def settlement(self, slug):
                if slug == MARKET:
                    raise OSError("boom")
                return {"market": slug, "state": "MARKET_STATE_EXPIRED",
                        "settlement_px": D(1), "not_terminal_reason": None}

        out = R.recover_settlements(ex, transport_for(Mixed()))
        assert [r["market"] for r in out["recovered"]] == [OTHER]
        assert [l["market"] for l in out["left"]] == [MARKET]


# -------------------------------------------------- it is not a trader

class TestItMovesNoMoney:

    def test_sends_no_order(self, tmp_path):
        sent = []

        class Loud(Venue):
            def submit(self, intent):
                sent.append(intent)
                raise AssertionError("recovery must not submit")

            def cancel(self, *a, **kw):
                raise AssertionError("recovery must not cancel")

        ex = executor(tmp_path, Loud())
        ghost(ex)
        R.recover_settlements(ex, transport_for(Settles({MARKET: 1})))
        assert sent == []

    def test_asks_the_venue_only_about_unsettled_markets(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex, MARKET)
        v = Settles({MARKET: 1})
        R.recover_settlements(ex, transport_for(v))
        assert v.asked == [MARKET]

    def test_a_clean_ledger_is_a_no_op(self, tmp_path):
        ex = executor(tmp_path)
        v = Settles({})
        out = R.recover_settlements(ex, transport_for(v))
        assert out["considered"] == [] and out["recovered"] == []
        assert v.asked == []
        assert out["error"] is None

    def test_never_raises_out_of_the_entrypoint(self, tmp_path):
        ex = executor(tmp_path)
        ghost(ex)

        def explode(slug):
            raise RuntimeError("transport factory is broken")

        out = R.recover_settlements(ex, explode)
        #: a bookkeeping sweep must not stop the service from trading
        assert out["left"][0]["why"] == "READ_FAILED:RuntimeError"
        assert out["error"] is None
