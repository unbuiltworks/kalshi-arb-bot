"""Standing authorization and durable RUN/STOP state.

Each refusal here is a case where the permissive answer would let a
money-moving service trade on something nobody authorized, or forget
why it had stopped.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_persistent_mode as P

NOW = dt.datetime(2026, 11, 16, 13, 0, tzinfo=dt.timezone.utc)
DEP = "sha-abc123"


def doc(**kw):
    base = {"approved": True, "mode": "PERSISTENT", "deployment": DEP,
            "strategies": ["STRADDLE", "LATE_FAVORITE"],
            "series": ["cpc-btc-updown-5m-", "cpceth-eth-updown-5m-"],
            "taker_allowed": False, "max_aggregate_usd": "10.00",
            "max_per_market_usd": "5.00", "max_daily_loss_usd": "5.00",
            "nonce": "persist-0001-abcdef"}
    base.update(kw)
    return json.dumps(base)


@pytest.fixture
def armed(tmp_path, monkeypatch):
    p = str(tmp_path / "state.json")
    monkeypatch.setenv(P.STATE_PATH_ENV, p)
    monkeypatch.setenv(P.DEPLOYMENT_ENV, DEP)
    monkeypatch.setenv(P.ENVELOPE_ENV, doc())
    assert P.write_state(P.ARMED, why="test", now=NOW)["ok"] is True
    return p


def gate(**kw):
    base = dict(market="cpc-btc-updown-5m-2026-11-16-1300z",
                strategy="STRADDLE", post_only=True, notional_usd="1.00",
                market_exposure_usd="0", aggregate_exposure_usd="0",
                day_realized_usd="0", now=NOW)
    base.update(kw)
    return P.may_submit(**base)


# ================================================================ envelope

class TestTheEnvelopeAuthorizesOnlyWhatItNames:

    def test_a_good_envelope_parses(self):
        r = P.parse_envelope(doc(), deployment=DEP, now=NOW)
        assert r["ok"] is True
        s = r["scope"]
        assert s["strategies"] == ("STRADDLE", "LATE_FAVORITE")
        assert s["taker_allowed"] is False
        assert s["max_aggregate_usd"] == D("10.00")

    @pytest.mark.parametrize("raw,reason", [
        (None, "ENVELOPE_ABSENT"),
        ("", "ENVELOPE_ABSENT"),
        ("   ", "ENVELOPE_ABSENT"),
        ("{not json", "ENVELOPE_UNPARSEABLE"),
        ("[]", "ENVELOPE_NOT_AN_OBJECT"),
        ('"a string"', "ENVELOPE_NOT_AN_OBJECT"),
    ])
    def test_a_missing_or_malformed_document_authorizes_nothing(self, raw, reason):
        assert P.parse_envelope(raw, deployment=DEP, now=NOW)["reason"] == reason

    def test_an_unapproved_document_authorizes_nothing(self):
        assert P.parse_envelope(doc(approved=False), deployment=DEP,
                                now=NOW)["reason"] == "ENVELOPE_NOT_APPROVED"

    def test_a_bounded_campaign_document_is_not_a_standing_one(self):
        """The two paths coexist, so the mode is named IN the document and
        a bounded approval can never be read as a standing envelope."""
        assert P.parse_envelope(doc(mode="BOUNDED"), deployment=DEP,
                                now=NOW)["reason"] == "ENVELOPE_NOT_PERSISTENT"
        assert P.parse_envelope(doc(mode=None), deployment=DEP,
                                now=NOW)["reason"] == "ENVELOPE_NOT_PERSISTENT"

    def test_the_nonce_must_be_substantial(self):
        assert P.parse_envelope(doc(nonce="x"), deployment=DEP,
                                now=NOW)["reason"] == "ENVELOPE_NONCE_TOO_SHORT"


class TestDeploymentIdentity:

    def test_another_deployment_may_not_inherit_it(self):
        r = P.parse_envelope(doc(), deployment="sha-different", now=NOW)
        assert r["reason"] == "ENVELOPE_IS_FOR_ANOTHER_DEPLOYMENT"

    def test_an_unknown_deployment_refuses(self):
        """A process that cannot say which image it is cannot be the one
        the owner authorized."""
        for d in (None, "", "  "):
            assert P.parse_envelope(doc(), deployment=d,
                                    now=NOW)["reason"] == "DEPLOYMENT_UNKNOWN"

    def test_an_envelope_naming_no_deployment_refuses(self):
        assert P.parse_envelope(doc(deployment=""), deployment=DEP,
                                now=NOW)["reason"] == "ENVELOPE_NAMES_NO_DEPLOYMENT"


class TestExpiry:

    def test_no_expiry_is_allowed_and_does_not_expire(self):
        r = P.parse_envelope(doc(), deployment=DEP,
                             now=NOW + dt.timedelta(days=365))
        assert r["ok"] is True and r["scope"]["expires_utc"] is None

    def test_an_expiry_in_the_past_refuses(self):
        r = P.parse_envelope(doc(expires_utc="2026-11-16T12:00:00Z"),
                             deployment=DEP, now=NOW)
        assert r["reason"] == "ENVELOPE_EXPIRED"

    def test_the_expiry_boundary_is_exclusive(self):
        at = "2026-11-16T13:00:00Z"
        assert P.parse_envelope(doc(expires_utc=at), deployment=DEP,
                                now=NOW)["reason"] == "ENVELOPE_EXPIRED"

    def test_an_unreadable_expiry_is_not_an_absent_one(self):
        r = P.parse_envelope(doc(expires_utc="next tuesday"),
                             deployment=DEP, now=NOW)
        assert r["reason"] == "ENVELOPE_EXPIRY_UNREADABLE"


class TestScopeAndCeilings:

    @pytest.mark.parametrize("kw,reason", [
        ({"strategies": []}, "ENVELOPE_NAMES_NO_STRATEGIES"),
        ({"strategies": "STRADDLE"}, "ENVELOPE_NAMES_NO_STRATEGIES"),
        ({"strategies": ["STRADDLE", "SCALP"]}, "ENVELOPE_UNKNOWN_STRATEGY"),
        ({"series": []}, "ENVELOPE_NAMES_NO_SERIES"),
        ({"series": ["cpc-doge-updown-5m-"]}, "ENVELOPE_UNKNOWN_SERIES"),
        ({"taker_allowed": None}, "ENVELOPE_TAKER_PERMISSION_UNSTATED"),
        ({"taker_allowed": "no"}, "ENVELOPE_TAKER_PERMISSION_UNSTATED"),
        ({"max_aggregate_usd": None}, "ENVELOPE_LIMITS_UNREADABLE"),
        ({"max_aggregate_usd": "0"}, "ENVELOPE_LIMITS_NOT_POSITIVE"),
        ({"max_aggregate_usd": "-1"}, "ENVELOPE_LIMITS_NOT_POSITIVE"),
    ])
    def test_scope_must_be_stated_and_known(self, kw, reason):
        assert P.parse_envelope(doc(**kw), deployment=DEP,
                                now=NOW)["reason"] == reason

    @pytest.mark.parametrize("kw", [
        {"max_aggregate_usd": "10.01"},
        {"max_per_market_usd": "5.01"},
        {"max_daily_loss_usd": "5.01"},
        {"max_aggregate_usd": "1000"},
    ])
    def test_a_document_may_ask_for_less_and_never_for_more(self, kw):
        """A forged or fat-fingered envelope cannot widen the system past
        what was reviewed in code."""
        r = P.parse_envelope(doc(**kw), deployment=DEP, now=NOW)
        assert r["reason"] == "ENVELOPE_EXCEEDS_CODE_CEILING"

    def test_asking_for_less_is_honoured(self):
        r = P.parse_envelope(doc(max_aggregate_usd="4.00",
                                 max_per_market_usd="2.00",
                                 max_daily_loss_usd="1.00"),
                             deployment=DEP, now=NOW)
        assert r["ok"] is True
        assert r["scope"]["max_aggregate_usd"] == D("4.00")

    def test_a_per_market_cap_above_the_aggregate_is_a_contradiction(self):
        r = P.parse_envelope(doc(max_aggregate_usd="2.00",
                                 max_per_market_usd="5.00"),
                             deployment=DEP, now=NOW)
        assert r["reason"] == "ENVELOPE_PER_MARKET_ABOVE_AGGREGATE"


# =========================================================== durable state

class TestTheStateIsDurableAndLatches:

    def test_a_fresh_volume_is_born_in_monitor(self, tmp_path, monkeypatch):
        """Never traded here: MONITOR is the only safe birth state."""
        monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "none.json"))
        r = P.read_state()
        assert r["ok"] is True and r["state"] == P.MONITOR and r["born"] is True

    def test_a_transition_survives_a_restart(self, tmp_path, monkeypatch):
        p = str(tmp_path / "s.json")
        monkeypatch.setenv(P.STATE_PATH_ENV, p)
        P.write_state(P.ARMED, why="owner armed", now=NOW)
        assert P.read_state()["state"] == P.ARMED
        P.write_state(P.HALTED, why="daily loss", now=NOW,
                      latch_digest="abc")
        again = P.read_state()                       # as a new process sees it
        assert again["state"] == P.HALTED
        assert again["detail"]["why"] == "daily loss"
        assert again["detail"]["latch_digest"] == "abc"

    @pytest.mark.parametrize("latched", [P.HALTED, P.STOPPED])
    def test_a_latched_state_refuses_every_submission(self, armed, latched):
        P.write_state(latched, why="because", now=NOW)
        r = gate()
        assert r["ok"] is False and r["reason"] == "STATE_%s" % latched
        assert "latched" in r["note"]

    @pytest.mark.parametrize("latched", [P.HALTED, P.STOPPED])
    def test_a_latch_does_not_clear_on_a_new_day_or_a_restart(self, armed, latched):
        P.write_state(latched, why="because", now=NOW)
        later = NOW + dt.timedelta(days=3)
        assert gate(now=later)["reason"] == "STATE_%s" % latched
        assert P.read_state()["state"] == latched

    def test_monitor_decides_but_submits_nothing(self, armed):
        P.write_state(P.MONITOR, why="stood down", now=NOW)
        r = gate()
        assert r["ok"] is False and r["reason"] == "STATE_NOT_ARMED"
        assert r["state"] == P.MONITOR

    @pytest.mark.parametrize("text,reason", [
        ("not json", "STATE_CORRUPT"),
        ("[]", "STATE_CORRUPT"),
        ('{"state": "SPICY"}', "STATE_UNKNOWN_VALUE"),
        ('{"nope": 1}', "STATE_UNKNOWN_VALUE"),
    ])
    def test_an_unreadable_state_is_not_monitor(self, tmp_path, monkeypatch,
                                                text, reason):
        """The one thing a corrupt state file could be hiding is a latch,
        so it refuses rather than defaulting to the resting state."""
        p = tmp_path / "s.json"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setenv(P.STATE_PATH_ENV, str(p))
        assert P.read_state()["reason"] == reason
        assert P.read_state()["state"] is None

    def test_an_unset_state_path_refuses(self, monkeypatch):
        monkeypatch.delenv(P.STATE_PATH_ENV, raising=False)
        assert P.read_state()["reason"] == "STATE_PATH_UNSET"
        assert P.write_state(P.ARMED, why="x", now=NOW)["reason"] \
            == "STATE_PATH_UNSET"

    def test_an_unknown_state_cannot_be_written(self, armed):
        assert P.write_state("SPICY", why="x", now=NOW)["reason"] \
            == "STATE_UNKNOWN_VALUE"
        assert P.read_state()["state"] == P.ARMED      # unchanged


# ================================================================ the gate

class TestTheGateRejectsWhatWasNotAuthorized:

    def test_an_authorized_order_passes(self, armed):
        r = gate()
        assert r["ok"] is True and r["state"] == P.ARMED

    def test_an_unauthorized_strategy_is_rejected(self, armed):
        assert gate(strategy="SCALP")["reason"] == "STRATEGY_NOT_AUTHORIZED"

    def test_a_strategy_left_out_of_the_envelope_is_rejected(self, armed,
                                                             monkeypatch):
        monkeypatch.setenv(P.ENVELOPE_ENV, doc(strategies=["STRADDLE"]))
        assert gate(strategy="LATE_FAVORITE")["reason"] \
            == "STRATEGY_NOT_AUTHORIZED"
        assert gate(strategy="STRADDLE")["ok"] is True

    def test_an_unauthorized_market_is_rejected(self, armed):
        assert gate(market="cpcsol-sol-updown-5m-2026-11-16-1300z")["reason"] \
            == "MARKET_NOT_AUTHORIZED"
        assert gate(market="cpc-btc-updown-15m-2026-11-16-1300z")["reason"] \
            == "MARKET_NOT_AUTHORIZED"
        assert gate(market="")["reason"] == "MARKET_NOT_AUTHORIZED"

    def test_an_authorized_sibling_series_passes(self, armed):
        assert gate(market="cpceth-eth-updown-5m-2026-11-16-1300z")["ok"] is True

    def test_a_taker_order_is_rejected_under_maker_only(self, armed):
        for po in (False, None, "yes", 1):
            assert gate(post_only=po)["reason"] == "MAKER_ONLY"

    def test_taker_passes_only_when_the_envelope_permits_it(self, armed,
                                                            monkeypatch):
        monkeypatch.setenv(P.ENVELOPE_ENV, doc(taker_allowed=True))
        assert gate(post_only=False)["ok"] is True


class TestSharedExposureIsNotDoubleSpent:

    def test_the_per_market_cap_binds(self, armed):
        assert gate(notional_usd="1.00", market_exposure_usd="4.00")["ok"] is True
        r = gate(notional_usd="1.01", market_exposure_usd="4.00")
        assert r["reason"] == "PER_MARKET_EXPOSURE"

    def test_the_aggregate_cap_binds_across_markets(self, armed):
        """A second market's order is refused by the AGGREGATE even with
        its own per-market room free -- which is what stops two
        strategies, or two markets, each spending the same budget."""
        r = gate(market="cpceth-eth-updown-5m-2026-11-16-1300z",
                 notional_usd="1.00", market_exposure_usd="0",
                 aggregate_exposure_usd="10.00")
        assert r["reason"] == "AGGREGATE_EXPOSURE"

    def test_exposure_already_held_by_the_OTHER_strategy_counts(self, armed):
        """The caller passes ONE aggregate figure covering both
        strategies and every resting order, so LATE_FAVORITE cannot
        spend what STRADDLE has already reserved."""
        assert gate(strategy="LATE_FAVORITE", notional_usd="1.00",
                    aggregate_exposure_usd="9.50")["reason"] \
            == "AGGREGATE_EXPOSURE"

    @pytest.mark.parametrize("kw,reason", [
        ({"notional_usd": None}, "EXPOSURE_UNREADABLE"),
        ({"aggregate_exposure_usd": None}, "EXPOSURE_UNREADABLE"),
        ({"day_realized_usd": None}, "EXPOSURE_UNREADABLE"),
        ({"notional_usd": "0"}, "NOTIONAL_NOT_POSITIVE"),
        ({"notional_usd": "-1"}, "NOTIONAL_NOT_POSITIVE"),
        ({"aggregate_exposure_usd": "-1"}, "EXPOSURE_NEGATIVE"),
    ])
    def test_an_unreadable_exposure_refuses(self, armed, kw, reason):
        assert gate(**kw)["reason"] == reason


class TestTheWorstCaseDailyLossBudget:

    def test_it_counts_losses_already_realized_today(self, armed):
        """$4 lost plus $1 at risk is the $5 halt, so one more cent is
        refused even though nothing is near the exposure caps."""
        assert gate(notional_usd="1.00", aggregate_exposure_usd="0",
                    day_realized_usd="-4.00")["ok"] is True
        r = gate(notional_usd="1.01", aggregate_exposure_usd="0",
                 day_realized_usd="-4.00")
        assert r["reason"] == "WORST_CASE_DAILY_LOSS"

    def test_it_counts_positions_already_open(self, armed):
        """A halt that only counted settled losses would let open
        positions carry the account straight through it."""
        r = gate(notional_usd="1.00", aggregate_exposure_usd="4.50",
                 day_realized_usd="0")
        assert r["reason"] == "WORST_CASE_DAILY_LOSS"
        assert r["worst_case_usd"] == "5.50"

    def test_profit_today_does_not_buy_extra_room(self, armed):
        """A good morning is not a larger loss budget."""
        r = gate(notional_usd="1.00", aggregate_exposure_usd="4.50",
                 day_realized_usd="+20.00")
        assert r["reason"] == "WORST_CASE_DAILY_LOSS"

    def test_the_reported_worst_case_is_the_sum_it_used(self, armed):
        r = gate(notional_usd="0.50", aggregate_exposure_usd="1.00",
                 day_realized_usd="-2.00")
        assert r["ok"] is True and r["worst_case_usd"] == "3.50"


class TestNoAutomaticRearming:

    def test_nothing_in_this_module_leaves_a_latched_state(self, armed):
        """Only an explicit write moves out of HALTED, and this module
        offers no path that does it by itself."""
        P.write_state(P.HALTED, why="loss", now=NOW, latch_digest="d1")
        for _ in range(3):
            assert gate()["reason"] == "STATE_HALTED"
        assert P.read_state()["state"] == P.HALTED

    def test_a_new_envelope_does_not_clear_a_latch(self, armed, monkeypatch):
        """Re-issuing authorization is not the same as clearing the
        latch; the state is a separate durable fact."""
        P.write_state(P.STOPPED, why="operator stop", now=NOW)
        monkeypatch.setenv(P.ENVELOPE_ENV, doc(nonce="persist-0002-abcdef"))
        assert gate()["reason"] == "STATE_STOPPED"
