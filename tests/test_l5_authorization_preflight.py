"""The consolidated authorization preflight: all blockers at once, read-only."""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_live_authorization as A

NOW = dt.datetime(2026, 10, 23, 17, 45, tzinfo=dt.timezone.utc)
MKT = "cpc-btc-updown-15m-2026-10-23-1745z"


def _pf(**env):
    return A.authorization_preflight(MKT, now=NOW)


def row(out, name):
    return next(r for r in out["rows"] if r["check"] == name)


class TestItIsReadOnly:

    def test_it_arms_nothing_and_reserves_nothing(self):
        out = A.authorization_preflight(MKT, now=NOW)
        assert "READ-ONLY" in out["labels"]
        assert "reserves nothing" in out["labels"]

    def test_the_campaign_check_reserves_no_window(self):
        """reserve=False, so asking does not spend one -- the property added
        after verification consumed the 1600z window."""
        import inspect
        src = inspect.getsource(A.authorization_preflight)
        assert "reserve=False" in src
        assert "reserve=True" not in src

    def test_it_calls_the_real_accessors_not_copies(self):
        """A second implementation would drift from what enforces."""
        import inspect
        src = inspect.getsource(A.authorization_preflight)
        for real in ("not_after_status(", "series_prefix()", "role_refusal()",
                     "max_windows_env()", "session_id()",
                     "campaign_decision("):
            assert real in src, real


class TestTheDeadline:
    """The step-26 refusal that cost three redeploys on 2026-10-23."""

    def test_an_expired_deadline_fails_and_names_the_step(self, monkeypatch):
        monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER",
                           "2026-10-23T05:55:27+00:00")
        r = row(A.authorization_preflight(MKT, now=NOW), "absolute_deadline")
        assert r["verdict"] == "FAIL"
        assert r["state"] == "EXPIRED"
        assert "AUTHORIZATION_EXPIRED" in r["blocks"]
        assert r["expires_in_s"] < 0

    def test_a_future_deadline_passes(self, monkeypatch):
        monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER",
                           "2026-10-23T23:00:00+00:00")
        r = row(A.authorization_preflight(MKT, now=NOW), "absolute_deadline")
        assert r["verdict"] == "PASS" and r["state"] == "OPEN"

    def test_an_unreadable_deadline_fails(self, monkeypatch):
        monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER", "not a timestamp")
        r = row(A.authorization_preflight(MKT, now=NOW), "absolute_deadline")
        assert r["verdict"] == "FAIL" and r["state"] == "UNREADABLE"

    def test_the_configured_deadline_is_never_emitted(self, monkeypatch):
        """A preflight that leaked what it checks would be worse than the
        problem it solves."""
        monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER",
                           "2026-10-23T05:55:27+00:00")
        out = A.authorization_preflight(MKT, now=NOW)
        assert "05:55:27" not in str(out)

    def test_unset_is_not_unconditionally_pass(self):
        """Step 35 refuses MANDATE_HAS_NO_DEADLINE for a multi-window
        mandate with no valid campaign, so the preflight must encode the
        condition rather than treating UNSET as fine."""
        import inspect
        src = inspect.getsource(A.authorization_preflight)
        assert "MANDATE_HAS_NO_DEADLINE" in src
        assert "_mw_now > 1 and not _camp_valid" in src


class TestHonestVerdicts:

    def test_unknown_and_not_evaluated_are_never_pass(self):
        out = A.authorization_preflight(MKT, now=NOW)
        assert "never" in out["labels"]
        for r in out["rows"]:
            assert r["verdict"] in ("PASS", "FAIL", "UNKNOWN",
                                    "NOT_EVALUATED")

    def test_the_sequential_claim_steps_are_declared_not_evaluated(self):
        out = A.authorization_preflight(MKT, now=NOW)
        assert set(out["not_evaluated"]) == {
            "window_bounds", "order_state_and_reconciliation",
            "claim_held_and_scope_digest"}

    def test_ok_requires_no_fail_and_no_unknown(self):
        out = A.authorization_preflight(MKT, now=NOW)
        assert out["ok"] == (not out["fail"] and not out["unknown"])

    def test_a_missing_series_fails_and_names_its_step(self, monkeypatch):
        monkeypatch.setenv("L5_LIVE_AUTH_MARKET_SERIES", "")
        r = row(A.authorization_preflight(MKT, now=NOW), "market_series")
        assert r["verdict"] == "FAIL"
        assert "MANDATE_SERIES_NOT_DECLARED" in r["blocks"]

    def test_a_series_that_does_not_cover_the_market_fails(self, monkeypatch):
        monkeypatch.setenv("L5_LIVE_AUTH_MARKET_SERIES", "cpc-eth-updown-15m-")
        r = row(A.authorization_preflight(MKT, now=NOW), "market_series")
        assert r["verdict"] == "FAIL" and r["covers_market"] is False

    def test_every_row_that_can_block_names_the_refusal(self):
        out = A.authorization_preflight(MKT, now=NOW)
        for r in out["rows"]:
            if r["verdict"] == "FAIL" and r["check"] != "session_id":
                assert r.get("blocks") or r.get("reason") or r.get("note"), \
                    r["check"]


class TestWindowBudget:

    def test_a_stale_single_window_env_is_surfaced(self, monkeypatch):
        """An env value can only tighten, so a stale 1 silently caps a
        four-window campaign."""
        monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "1")
        r = row(A.authorization_preflight(MKT, now=NOW), "window_budget_env")
        assert r["max_windows_env"] == 1
        assert "silently caps" in r["note"]


class TestNoMarketYetDoesNotFakeASeriesMismatch:
    """Emitted in production 2026-10-23T19:37: a valid approval -- state
    "approved", exposure 2.00, 4 windows, daily loss 5 -- was reported FAIL
    with CAMPAIGN_SERIES_MISMATCH purely because the transport had no slug
    yet and `"".startswith("cpc-btc-updown-15m-")` is False."""

    def _doc(self):
        import datetime as dt, json
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        return json.dumps({
            "approved": True, "nonce": "camp-20261023-4w-c2",
            "series": "cpc-btc-updown-15m-",
            "issued_utc": (now - dt.timedelta(minutes=2)).isoformat(),
            "expires_utc": (now + dt.timedelta(minutes=73)).isoformat(),
            # OPENING BOUND, 2026-11-04. Set equal to the expiry so this
            # fixture reproduces the pre-bound semantics EXACTLY -- windows
            # may open until expiry -- and keeps testing what it tested.
            # The bound itself is covered in test_l5_opening_bound.py.
            "last_open_utc": (now + dt.timedelta(minutes=58)).isoformat(),
            "max_windows": 4, "max_total_exposure_usd": "2.00",
            "max_daily_loss_usd": "5"})

    def test_a_valid_approval_passes_with_no_market(self, monkeypatch):
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", self._doc())
        r = row(A.authorization_preflight("", now=None), "campaign_approval")
        assert r["verdict"] == "PASS", r
        assert r["reason"] != "CAMPAIGN_SERIES_MISMATCH"

    def test_coverage_is_reported_not_evaluated_rather_than_failed(
            self, monkeypatch):
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", self._doc())
        r = row(A.authorization_preflight(""), "campaign_approval")
        assert "NOT_EVALUATED" in str(r["covers_market"])

    def test_the_approved_scope_is_still_reported(self, monkeypatch):
        """The operator must still see what was approved."""
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", self._doc())
        r = row(A.authorization_preflight(""), "campaign_approval")
        assert r["max_windows"] == 4
        assert r["approved_exposure"] == "2.00"
        assert r["approved_daily_loss"] == "5"
        assert r["document_sha256"]

    def test_a_real_series_mismatch_still_fails_when_a_market_is_known(
            self, monkeypatch):
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", self._doc())
        r = row(A.authorization_preflight("cpc-eth-updown-15m-2026-10-23-2000z"),
                "campaign_approval")
        assert r["verdict"] == "FAIL"
        assert r["reason"] == "CAMPAIGN_SERIES_MISMATCH"

    def test_a_matching_market_passes_and_asserts_coverage(self, monkeypatch):
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", self._doc())
        r = row(A.authorization_preflight("cpc-btc-updown-15m-2026-10-23-2000z"),
                "campaign_approval")
        assert r["verdict"] == "PASS" and r["covers_market"] is True

    def test_an_absent_document_still_fails_with_no_market(self, monkeypatch):
        monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
        r = row(A.authorization_preflight(""), "campaign_approval")
        assert r["verdict"] == "FAIL"
