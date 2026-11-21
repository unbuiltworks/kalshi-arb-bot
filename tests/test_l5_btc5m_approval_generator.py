"""The approval generator must not bless a document the venue path refuses.

OBSERVED LIVE 2026-11-12. A ROLLING approval (bounded by `last_open_utc`
instead of enumerating each window) passed the generator's self-check,
armed the service, planned eight windows and chose OPEN_FAVORITE on every
step with EV_AND_ROOM_OK -- and submitted NOTHING, because every order was
refused at the per-order claim with BTC5M_WINDOWS_NOT_ENUMERATED. The
document was valid and useless, and the generator had printed YES.

The cause was that the generator verified against ONE of the two gates.
`campaign_decision` plans the campaign; `claim_btc5m_submission` authorizes
each order, and it is stricter: for this series "an interval is not an
explicit approval". Passing the first says nothing about the second.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH

GEN = os.path.join("analysis", "l5", "btc5m_accumulation", "make_approval.py")


def run(*args):
    return subprocess.run([sys.executable, "-I", GEN, *args],
                          capture_output=True, text=True, timeout=180)


class TestTheGeneratorAgreesWithTheClaimLayer:

    def test_the_claim_layer_really_does_require_enumeration(self):
        """The premise. If this ever stops being true the generator's
        extra gate is obsolete rather than merely strict, and this test
        says so instead of the generator silently over-refusing."""
        src = __import__("inspect").getsource(AUTH)
        assert "BTC5M_WINDOWS_NOT_ENUMERATED" in src
        i = src.index("BTC5M_WINDOWS_NOT_ENUMERATED")
        assert 'scope.get("window_starts")' in src[max(0, i - 400):i], \
            "the refusal is no longer keyed on window_starts"

    def test_a_rolling_document_is_refused(self):
        r = run("2", "--rolling")
        assert r.returncode == 1, r.stdout
        assert "per-order claim accepted : NO" in r.stdout
        assert "NOT USABLE FOR BTC5m" in r.stdout
        assert "BTC5M_WINDOWS_NOT_ENUMERATED" in r.stdout

    def test_a_rolling_document_still_passes_the_campaign_planner(self):
        """The point of the fix: the first gate was never the problem, so
        a test that only checked the planner would have passed throughout
        the live failure."""
        r = run("2", "--rolling")
        assert "campaign planner accepted: YES" in r.stdout

    def test_an_enumerated_document_is_accepted(self):
        r = run("2")
        assert r.returncode == 0, r.stdout
        assert "campaign planner accepted: YES" in r.stdout
        assert "per-order claim accepted : YES" in r.stdout
        assert "NOT USABLE" not in r.stdout

    def test_the_accepted_document_enumerates_its_windows(self):
        r = run("3")
        line = [x for x in r.stdout.splitlines()
                if x.startswith('{"approved"')]
        assert line, r.stdout
        import json
        doc = json.loads(line[0])
        assert len(doc["window_starts"]) == 3
        assert "last_open_utc" not in doc
        assert doc["series"] == CA.BTC5M_SERIES

    @pytest.mark.parametrize("n", ["0", "49"])
    def test_window_count_stays_inside_the_code_ceiling(self, n):
        """Below 1, and above BTC5m's OWN ceiling, are both refused.

        The out-of-range case must be stated in terms of the SERIES
        ceiling -- `max_windows_for(BTC5M_SERIES)` = 48 -- and not the
        general `MAX_WINDOWS` = 8. Nine windows is now INSIDE the
        5-minute ceiling, so asserting nine is refused would assert
        nothing at all.
        """
        assert run(n).returncode == 2

    def test_the_generator_uses_the_SERIES_ceiling_not_the_general_one(self):
        """Pins both numbers so the generator and claim layer cannot drift.

        BTC5m has its own wider count; BTC15m and anything unrecognized
        keep the general one. If a later change moves either, this fails
        rather than silently widening what the generator will emit.
        """
        assert CA.max_windows_for(CA.BTC5M_SERIES) == 48
        assert CA.max_windows_for(CA.BTC15M_SERIES) == CA.MAX_WINDOWS == 8
        assert CA.max_windows_for(None) == 8
        assert CA.max_windows_for("") == 8
        assert CA.max_windows_for("cpc-not-a-series-") == 8
        # and one past the series ceiling is refused by the generator itself
        assert run(str(CA.max_windows_for(CA.BTC5M_SERIES) + 1)).returncode == 2
