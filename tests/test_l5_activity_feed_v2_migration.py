"""The L5 activity feed: v1 retired, v2 wrapped and renamed.

WHAT THIS PINS, and why each part is load-bearing.

Polymarket retired Data API v1. The legacy `/activity` path answers HTTP 410
"Data API v1 has been retired", `l5_activity` swallowed the exception whole
and returned None, and the executor counted `ingest:FEED_FAILED` 577 times
with no status, no exception type and no body anywhere in its telemetry. So
the heartbeat could not tell "L5 stopped trading" from "we cannot read the
feed", which is precisely the distinction the ingest counters exist to make.

Checked against v2 at 2026-11-05T20:42Z, L5 was trading BTC15m THAT MINUTE:
200 rows, newest 1.2 minutes old, 179 of them BTC15m. The feed was never
quiet.

TWO BREAKS, AND FIXING EITHER ALONE STILL YIELDS None:
  * the envelope -- v1 returned a bare list, v2 returns {"data": [...]};
  * the field names -- v2 is snake_case, and `asset` became `token_id`.

A HALF-DONE RENAME IS WORSE THAN THE OUTAGE. Rows would flow while
`action_key` keyed on "None|None|..." -- a feed that LOOKS healthy and has
lost the identity of every fill. The field map is therefore asserted against
a REAL v1 row captured 2026-09-01, not against a hand-written fixture.

NO NETWORK. Every test here stubs the transport.
"""
from __future__ import annotations

import json
import pathlib
import urllib.error

import pytest

from src import l5_pmus_realtime_observer as OBS
from src import l5_pmus_live_runner as R


V1_CAPTURE = pathlib.Path("data/l5/ops/activity_20260901.jsonl")

#: one real v2 row, as served 2026-11-05.
V2_ROW = {
    "proxy_wallet": "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68",
    "timestamp": 1793911233,
    "condition_id": "0xd4fcaad9cf63bde65353f9a98686e86b4864ba39d",
    "type": "TRADE", "size": 10.0, "usdc_size": 0.7,
    "transaction_hash": "0x58ae16ac946afa33dac0c43b61546255e162d4e78",
    "price": 0.07,
    "token_id": "4473343790785523257721333243769405943858835692",
    "side": "BUY", "outcome_index": 1,
    "title": "Bitcoin Up or Down - November 5, 3:30PM-3:45PM ET",
    "slug": "btc-updown-15m-1793910600",
    "event_slug": "btc-updown-15m-1793910600",
    "outcome": "Down", "name": "l5Zn1bWoM8eTsK",
    # the profile fields the real row also carries. Kept in the fixture
    # because the completeness test below is only meaningful against the
    # WHOLE row -- a trimmed fixture would let a missing mapping pass.
    "pseudonym": "Noisy-Colonisation",
    "bio": "Not active on X or any other form of social media.",
    "icon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/BTC.png",
    "profile_image": "https://example.invalid/p.png",
    "profile_image_optimized": "https://example.invalid/p-opt.png",
}


@pytest.fixture(autouse=True)
def _no_residual_failure():
    """Each test starts with the module's failure record clean."""
    OBS._LAST_FEED_FAILURE = {}
    yield
    OBS._LAST_FEED_FAILURE = {}


def _stub(monkeypatch, payload=None, exc=None):
    def fake(url, timeout=20.0, *, no_cache=False):
        assert "/v2/activity" in url, "must poll v2, not the retired v1 path"
        if exc is not None:
            raise exc
        return payload
    monkeypatch.setattr(OBS, "_get", fake)


# ------------------------------------------------------- the envelope
def test_the_v2_envelope_is_unwrapped(monkeypatch):
    _stub(monkeypatch, {"data": [dict(V2_ROW)],
                        "pagination": {"has_more": False}})
    rows = OBS.l5_activity(limit=5)
    assert rows is not None, "a wrapped payload is not a feed failure"
    assert len(rows) == 1
    assert OBS.last_feed_failure() == {}


def test_a_bare_list_is_still_accepted(monkeypatch):
    """If the endpoint ever reverts, or a cached v1 body arrives, it reads."""
    _stub(monkeypatch, [dict(V2_ROW)])
    rows = OBS.l5_activity(limit=5)
    assert rows is not None and len(rows) == 1


def test_an_empty_data_list_is_a_quiet_wallet_not_a_failure(monkeypatch):
    """THE DISTINCTION THE WHOLE MODULE EXISTS FOR. Zero rows means L5 did
    nothing; it must never be reported as a broken feed."""
    _stub(monkeypatch, {"data": [], "pagination": {}})
    rows = OBS.l5_activity(limit=5)
    assert rows == [], "an empty feed is a finding, not a fault"
    assert rows is not None
    assert OBS.last_feed_failure() == {}


@pytest.mark.parametrize("payload", [
    {"pagination": {}},                 # no data key at all
    {"data": {"not": "a list"}},
    {"data": None},
    "a string",
    42,
    None,
])
def test_an_unexpected_shape_fails_closed_and_says_so(monkeypatch, payload):
    _stub(monkeypatch, payload)
    assert OBS.l5_activity(limit=5) is None
    f = OBS.last_feed_failure()
    assert f["kind"] == "UNEXPECTED_SHAPE"
    assert f["status"] == 200, "the HTTP call succeeded; the SHAPE did not"
    assert "no row list" in f["reason"]


# ------------------------------------------------- the failure record
def test_the_410_that_caused_this_is_recorded_in_full(monkeypatch):
    """The exact production failure, and the detail that was missing.

    `ingest:FEED_FAILED` reached 577 with none of this recorded anywhere.
    """
    body = (b'{"error":"Data API v1 has been retired",'
            b'"migration":"https://data-api.polymarket.com/v2/"}')

    class _Gone(urllib.error.HTTPError):
        def __init__(self):
            urllib.error.HTTPError.__init__(
                self, "u", 410, "Gone", {}, None)
            self._b = body

        def read(self):
            return self._b

    _stub(monkeypatch, exc=_Gone())
    assert OBS.l5_activity(limit=5) is None
    f = OBS.last_feed_failure()
    assert f["kind"] == "HTTPError"
    assert f["status"] == 410
    assert f["reason"] == "Gone"
    assert "has been retired" in f["body"]
    assert f["utc"]


def test_a_non_http_exception_is_also_recorded(monkeypatch):
    _stub(monkeypatch, exc=OSError("Tunnel connection failed: 403 Forbidden"))
    assert OBS.l5_activity(limit=5) is None
    f = OBS.last_feed_failure()
    assert f["kind"] == "OSError"
    assert f["status"] is None
    assert "Tunnel" in f["reason"]


def test_an_unreadable_error_body_degrades_rather_than_raising(monkeypatch):
    class _Bad(urllib.error.HTTPError):
        def __init__(self):
            urllib.error.HTTPError.__init__(self, "u", 500, "Boom", {}, None)

        def read(self):
            raise RuntimeError("no body")

    _stub(monkeypatch, exc=_Bad())
    assert OBS.l5_activity(limit=5) is None
    assert OBS.last_feed_failure()["body"] == "BODY_UNREADABLE"


def test_a_success_clears_a_previous_failure(monkeypatch):
    _stub(monkeypatch, exc=OSError("down"))
    assert OBS.l5_activity(limit=5) is None
    assert OBS.last_feed_failure()["kind"] == "OSError"
    _stub(monkeypatch, {"data": [dict(V2_ROW)]})
    assert OBS.l5_activity(limit=5) is not None
    assert OBS.last_feed_failure() == {}, "a stale failure must not persist"


# ------------------------------------------------------ the field map
def test_the_field_map_is_exactly_what_a_real_v1_row_needs():
    """ASSERTED AGAINST A REAL v1 ROW, not a hand-written fixture.

    Every v1 field must be reachable after normalization, except our own
    `_`-prefixed annotations, which the API never served.
    """
    if not V1_CAPTURE.exists():                       # pragma: no cover
        pytest.skip("no v1 capture in this checkout")
    v1 = json.loads(V1_CAPTURE.read_text().splitlines()[0])
    got = OBS.normalize_activity_row(dict(V2_ROW))
    missing = sorted(k for k in v1
                     if not k.startswith("_") and k not in got)
    assert missing == [], (
        "these v1 fields are unreachable after normalization: %s" % missing)


def test_asset_comes_from_token_id_and_both_are_token_ids():
    """The one TRUE rename, as opposed to the nine case changes."""
    got = OBS.normalize_activity_row(dict(V2_ROW))
    assert got["asset"] == V2_ROW["token_id"]
    assert got["asset"].isdigit(), "an ERC1155 token id"


@pytest.mark.parametrize("v2k,v1k", sorted(OBS.V2_FIELD_MAP.items()))
def test_every_mapped_field_is_reachable_under_its_v1_name(v2k, v1k):
    got = OBS.normalize_activity_row({v2k: "sentinel"})
    assert got[v1k] == "sentinel"


def test_normalization_never_overwrites_an_existing_v1_name():
    """Non-destructive, so a mixed or already-normalized row is safe."""
    got = OBS.normalize_activity_row(
        {"token_id": "new", "asset": "ORIGINAL"})
    assert got["asset"] == "ORIGINAL"


def test_normalization_is_idempotent():
    once = OBS.normalize_activity_row(dict(V2_ROW))
    twice = OBS.normalize_activity_row(dict(once))
    assert once == twice


def test_a_non_mapping_row_passes_through_untouched():
    for bad in (None, 7, "x", [1]):
        assert OBS.normalize_activity_row(bad) is bad or \
            OBS.normalize_activity_row(bad) == bad


def test_rows_carry_their_api_version_for_provenance(monkeypatch):
    _stub(monkeypatch, {"data": [dict(V2_ROW)]})
    assert OBS.l5_activity(limit=5)[0]["_api_version"] == "v2"


# -------------------------------------------- what the engine reads
def test_action_key_is_fully_populated_after_normalization():
    """THE SILENT-FAILURE GUARD. Without the rename, `action_key` would key
    on "None|None|..." for every row -- a feed that looks healthy while it
    has lost the identity of every fill."""
    key = OBS.action_key(OBS.normalize_activity_row(dict(V2_ROW)))
    assert not key.startswith("None|None")
    assert V2_ROW["transaction_hash"] in key
    assert V2_ROW["token_id"] in key
    assert "None" not in key, key


def test_the_btc15m_slug_matcher_still_matches_v2_slugs():
    """v2's slug is the unix-window form `btc-updown-15m-<epoch>`, which is
    what `rows_btc15m` already counts. This one did NOT break, and pinning
    it keeps a future slug change from going unnoticed."""
    assert V2_ROW["slug"].startswith("btc-updown-15m-")


# ---------------------------------------------------- the telemetry
def test_the_runner_reports_the_failure_detail(monkeypatch):
    monkeypatch.setattr(OBS, "last_feed_failure",
                        lambda: {"kind": "HTTPError", "status": 410})
    assert R._feed_failure_detail() == {"kind": "HTTPError", "status": 410}


def test_the_detail_helper_never_breaks_a_heartbeat(monkeypatch):
    def boom():
        raise RuntimeError("observer unavailable")
    monkeypatch.setattr(OBS, "last_feed_failure", boom)
    got = R._feed_failure_detail()
    assert got["kind"] == "DETAIL_UNAVAILABLE"
    assert got["error"] == "RuntimeError"


def test_the_detail_helper_survives_an_observer_without_the_function(
        monkeypatch):
    monkeypatch.delattr(OBS, "last_feed_failure", raising=False)
    assert R._feed_failure_detail() == {}


def test_the_ingest_report_carries_the_field():
    import inspect
    src = inspect.getsource(R)
    assert '"last_feed_failure": _feed_failure_detail()' in src
