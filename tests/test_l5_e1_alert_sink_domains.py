"""The alert sink must never be able to become a venue path.

The monitor's module docstring has always promised this. It was not true: the
check tested only `AUTH.API`'s hostname -- `api.polymarket.us` -- so
`https://gateway.polymarket.us/...` was ACCEPTED as an alert sink, and the
gateway is the host the collector and every public book read actually use.
"""
from __future__ import annotations

import importlib

import pytest

M = importlib.import_module("scripts.l5_e1_safety_monitor")


@pytest.mark.parametrize("url", [
    "https://gateway.polymarket.us/hook",          # the one that got through
    "https://api.polymarket.us/hook",
    "https://polymarket.us/hook",
    "https://docs.polymarket.us/hook",
    "https://api.elections.kalshi.com/hook",
    "https://exchange.gemini.com/hook",
])
def test_every_venue_host_is_refused(url):
    with pytest.raises(SystemExit, match="venue domain"):
        M.AlertSink(url)


@pytest.mark.parametrize("url", [
    "https://hooks.slack.com/services/T/B/x",
    "https://notpolymarket.us/hook",   # must not collide with polymarket.us
])
def test_a_real_sink_still_works(url):
    sink = M.AlertSink(url)
    assert sink.report()["webhook_configured"] is True


def test_the_venue_domain_list_tracks_auth_api():
    """So a venue move cannot silently reopen the hole."""
    from src import pmus_auth as AUTH
    host = AUTH.API.split("//")[-1].split("/")[0].lower()
    assert ".".join(host.split(".")[-2:]) in M.VENUE_DOMAINS


@pytest.mark.parametrize("url", ["http://example.com/h", "ftp://x/y",
                                 "example.com/h"])
def test_non_https_is_still_refused(url):
    with pytest.raises(SystemExit, match="https"):
        M.AlertSink(url)


def test_unset_is_log_only_and_says_so():
    r = M.AlertSink(None).report()
    assert r["webhook_configured"] is False
    assert "LOG ONLY" in r["MISSING_SETUP"]
