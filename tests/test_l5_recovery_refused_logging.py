"""Count the legs the recovery CAP alone refused.

`stale_by_distance: []` reads identically whether nothing drifted or the
per-market recovery budget was already gone, because the refusal carried only
`used` and `max_per_market`. So the number of legs that WOULD have qualified
for a second recovery -- the one quantity that could justify moving the cap
off 1 -- was unmeasurable.

These tests pin the enriched refusal and the reporting-only emit, and pin that
neither changes a decision: `recover` stays False, the recovery list stays
empty, and nothing is cancelled, re-priced or gated.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS

from tests.test_l5_stale_first_leg_recovery import LEG, at

#: drifted 26 ticks past a 4-tick tolerance, rested 60 s past a 15 s floor,
#: book 0.03 s old, zero filled -- qualifies on every test but the budget.
QUALIFIES = dict(bid="0.47", ask="0.48")


class TestTheRefusalCarriesWhatQualifiedIt:

    def test_a_spent_budget_refuses(self):
        v = at(**QUALIFIES, recoveries_used=1)
        assert v["recover"] is False
        assert v["reason"] == "RECOVERY_BUDGET_SPENT"

    def test_it_reports_the_cap_it_hit(self):
        v = at(**QUALIFIES, recoveries_used=1)
        assert v["used"] == 1
        assert v["max_per_market"] == TS.recover_max_per_market()

    def test_it_reports_the_drift_that_qualified(self):
        v = at(**QUALIFIES, recoveries_used=1)
        assert D(v["drift_ticks"]) > D(v["allowed_ticks"])

    def test_it_reports_rest_age_and_book_freshness(self):
        v = at(**QUALIFIES, recoveries_used=1)
        assert D(v["rest_s"]) >= D(str(TS.recover_min_rest_s()))
        assert D(v["book_age_s"]) <= D(str(TS.STALE_RECOVER_MAX_BOOK_AGE_S))

    def test_it_reports_the_side_and_both_prices(self):
        v = at(**QUALIFIES, recoveries_used=1)
        assert v["side"] == LEG["side"]
        assert v["own_px"] == "0.21"
        assert D(v["would_quote_now"]) > D(v["own_px"])

    def test_it_marks_the_leg_as_having_qualified(self):
        """The field that separates this from every other refusal."""
        assert at(**QUALIFIES, recoveries_used=1)["qualified"] is True

    @pytest.mark.parametrize("over,reason", [
        (dict(bid="0.77", ask="0.78"), "NOT_DRIFTED_AWAY"),
        (dict(bid="0.75", ask="0.76"), "WITHIN_TOLERANCE"),
        (dict(now=1005.0), "TOO_YOUNG"),
        (dict(book_age_s=99.0), "BOOK_STALE"),
        (dict(remaining="0.5"), "NOT_ZERO_FILL"),
    ])
    def test_a_leg_refused_for_any_other_cause_is_not_marked_qualified(
            self, over, reason):
        v = at(**{**QUALIFIES, **over}, recoveries_used=1)
        assert v["reason"] == reason
        assert "qualified" not in v


class TestTheEmitIsReportingOnly:
    """`acc_stale_by_distance` must log the refusal and recover nothing."""

    def _runner(self, monkeypatch, emitted):
        monkeypatch.setattr(R, "emit",
                            lambda tag, rec: emitted.append((tag, rec)))

        class Tx:
            tick_size = "0.01"
            market_slug = "cpc-btc-updown-15m-2026-10-27-0130z"

        class Book:
            def snapshot(self, _m=None):
                return {"bid": "0.47", "ask": "0.48", "stream_age_s": 0.03}

        class Ex:
            def probe_round(self, _m):
                return 2

        class Run:
            transport = Tx()
            book = Book()
            executor = Ex()
            stats = None

        return Run()

    def _live(self):
        return {"l5f-aaa": {"market": "cpc-btc-updown-15m-2026-10-27-0130z",
                            "side": "DOWN", "price": "0.21", "qty": "0.94",
                            "remaining": "0.94", "state": "LIVE",
                            "venue_order_id": "DBF9XYZ",
                            "t": "2026-10-27T01:31:00+00:00"}}

    def _call(self, monkeypatch, emitted, used):
        run = self._runner(monkeypatch, emitted)
        run.acc_recoveries = {"cpc-btc-updown-15m-2026-10-27-0130z": used}
        monkeypatch.setattr(R.AUTH, "class_budget_report",
                            lambda *_a, **_k: {"OPEN": {"remaining": 27}})
        return R.acc_stale_by_distance(
            run, 1793064720.0, "cpc-btc-updown-15m-2026-10-27-0130z",
            self._live())

    def test_it_emits_the_refusal_and_recovers_nothing(self, monkeypatch):
        emitted = []
        out = self._call(monkeypatch, emitted, used=1)

        assert out == []                      # NOTHING is recovered
        tags = [t for t, _ in emitted]
        assert tags == ["L5_ACC_RECOVER_REFUSED"]
        rec = emitted[0][1]
        assert rec["market"] == "cpc-btc-updown-15m-2026-10-27-0130z"
        assert rec["round"] == 2
        assert rec["coid"] == "l5f-aaa"
        assert rec["venue_order_id"] == "DBF9XYZ"
        assert rec["reason"] == "RECOVERY_BUDGET_SPENT"
        assert rec["qualified"] is True
        assert rec["recover"] is False

    def test_an_available_budget_recovers_and_does_not_emit_a_refusal(
            self, monkeypatch):
        emitted = []
        out = self._call(monkeypatch, emitted, used=0)

        assert out == ["l5f-aaa"]             # the ordinary path, unchanged
        assert [t for t, _ in emitted] == ["L5_ACC_RECOVER"]

    def test_no_credential_or_secret_reaches_the_record(self, monkeypatch):
        emitted = []
        self._call(monkeypatch, emitted, used=1)
        flat = repr(emitted[0][1]).lower()
        for banned in ("secret", "api_key", "apikey", "private", "token",
                       "password", "pmus_api"):
            assert banned not in flat

    def test_a_round_that_cannot_be_read_still_emits(self, monkeypatch):
        """Fail-open: the record is diagnostic, never a gate."""
        emitted = []
        run = self._runner(monkeypatch, emitted)
        run.acc_recoveries = {"cpc-btc-updown-15m-2026-10-27-0130z": 1}

        def boom(_m):
            raise RuntimeError("round unreadable")

        run.executor.probe_round = boom
        monkeypatch.setattr(R.AUTH, "class_budget_report",
                            lambda *_a, **_k: {"OPEN": {"remaining": 27}})

        out = R.acc_stale_by_distance(
            run, 1793064720.0, "cpc-btc-updown-15m-2026-10-27-0130z",
            self._live())

        assert out == []
        assert emitted[0][0] == "L5_ACC_RECOVER_REFUSED"
        assert emitted[0][1]["round"] is None
