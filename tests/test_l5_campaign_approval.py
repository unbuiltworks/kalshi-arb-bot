"""The campaign approval gate: can it widen anything, and does it fail closed?

Two properties carry everything:

  * a document can never raise a limit, only ask for less;
  * anything but an explicit, in-date, in-scope approval refuses.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA

NOW = dt.datetime(2026, 10, 20, 1, 0, tzinfo=dt.timezone.utc)


def _doc(**over):
    d = {"approved": True, "nonce": "campaign-nonce-0001",
         "series": "cpc-btc-updown-15m-",
         "issued_utc": (NOW - dt.timedelta(minutes=5)).isoformat(),
         "expires_utc": (NOW + dt.timedelta(minutes=90)).isoformat(),
         "max_windows": 4, "max_total_exposure_usd": "5",
         "max_daily_loss_usd": "5"}
    d.update(over)
    return d


class _Store:
    def __init__(self, body):
        self.body = body

    def get(self, key):
        if key != CA.APPROVAL_KEY:
            raise CA.ApprovalReadUnavailable("only one key is served here")
        if self.body is CA.ApprovalReadUnavailable:
            raise CA.ApprovalReadUnavailable("endpoint down")
        return self.body


def _gate(body, **kw):
    if isinstance(body, dict):
        body = json.dumps(body).encode("utf-8")
    return CA.ApprovalGate(_Store(body), now_fn=lambda: NOW, **kw)


def test_a_well_formed_in_scope_document_is_approved():
    st = _gate(_doc()).state()
    assert st["approved"] is True, st
    s = st["scope"]
    assert s["max_windows"] == 4
    assert s["max_total_exposure_usd"] == "5"
    assert s["max_daily_loss_usd"] == "5"
    assert s["nonce_sid"] == "camp1.campaign-nonce-0001"
    assert all(v is False for v in s["clamped"].values())


# ------------------------------------------------- it can never widen a limit

@pytest.mark.parametrize("field,asked,expected", [
    ("max_windows", 40, 4),
    ("max_total_exposure_usd", "500", "5"),
    ("max_daily_loss_usd", "250", "5"),
])
def test_a_document_asking_for_more_is_clamped_not_honoured(field, asked,
                                                            expected):
    st = _gate(_doc(**{field: asked})).state()
    assert st["approved"] is True
    assert st["scope"][field] == expected
    assert st["scope"]["clamped"][field] is True


def test_a_long_campaign_is_cut_to_the_code_ceiling():
    st = _gate(_doc(expires_utc=(NOW + dt.timedelta(days=7)).isoformat())).state()
    assert st["approved"] is True
    end = dt.datetime.fromisoformat(st["scope"]["expires_utc"])
    issued = dt.datetime.fromisoformat(st["scope"]["issued_utc"])
    assert (end - issued).total_seconds() == CA.MAX_CAMPAIGN_S
    assert st["scope"]["clamped"]["expires_utc"] is True


def test_asking_for_less_is_honoured_exactly():
    st = _gate(_doc(max_windows=2, max_total_exposure_usd="1.50")).state()
    assert st["scope"]["max_windows"] == 2
    assert st["scope"]["max_total_exposure_usd"] == "1.50"
    assert st["scope"]["clamped"]["max_windows"] is False


# ----------------------------------------------------------- fail closed

@pytest.mark.parametrize("body,why", [
    (None, "approval_absent"),
    (CA.ApprovalReadUnavailable, "approval_unreadable"),
    (b"not json", "approval_unparseable"),
    (b'["a","list"]', "approval_not_an_object"),
])
def test_an_unusable_object_refuses(body, why):
    st = _gate(body).state()
    assert st["approved"] is False and st["why"] == why


@pytest.mark.parametrize("over,why", [
    ({"approved": False}, "approval_not_asserted"),
    ({"approved": "yes"}, "approval_not_asserted"),
    ({"approved": 1}, "approval_not_asserted"),
    ({"nonce": "short"}, "approval_nonce_too_short"),
    ({"series": "cpceth-eth-updown-15m-"}, "approval_series_not_allowed"),
    ({"series": "*"}, "approval_series_not_allowed"),
    ({"max_windows": 0}, "approval_max_windows_below_one"),
    ({"max_windows": "lots"}, "approval_max_windows_unusable"),
    ({"max_total_exposure_usd": "0"}, "approval_limits_not_positive"),
    ({"max_total_exposure_usd": "abc"}, "approval_limits_unusable"),
])
def test_a_document_outside_scope_refuses(over, why):
    st = _gate(_doc(**over)).state()
    assert st["approved"] is False and st["why"] == why, st


def test_a_missing_field_refuses_and_names_it():
    d = _doc()
    del d["expires_utc"]
    st = _gate(d).state()
    assert st["approved"] is False
    assert st["why"] == "approval_incomplete"
    assert "expires_utc" in st["missing"]


@pytest.mark.parametrize("over,why", [
    ({"issued_utc": "2026-10-20T01:00:00"}, "approval_issued_time_unusable"),
    ({"expires_utc": "2026-10-20T02:00:00"}, "approval_expiry_unusable"),
    ({"issued_utc": (NOW + dt.timedelta(minutes=5)).isoformat()},
     "approval_issued_in_the_future"),
    ({"issued_utc": (NOW - dt.timedelta(hours=5)).isoformat()},
     "approval_stale"),
    ({"expires_utc": (NOW - dt.timedelta(minutes=1)).isoformat()},
     "approval_expired"),
])
def test_every_time_problem_refuses(over, why):
    st = _gate(_doc(**over)).state()
    assert st["approved"] is False and st["why"] == why, st


def test_a_naive_timestamp_is_never_read_as_utc():
    """A naive time is ambiguous, and an ambiguous expiry is not an expiry."""
    assert CA._ts("2026-10-20T02:00:00") is None
    assert CA._ts("2026-10-20T02:00:00Z") is not None


# -------------------------------------------------------- it cannot approve

def test_the_gate_exposes_no_mutating_verb():
    """If the executor could write the document that authorizes it, the
    document would authorize nothing."""
    g = _gate(_doc())
    for banned in ("put", "write", "set", "issue"):
        assert not hasattr(g, banned), banned


def test_the_store_the_runner_holds_refuses_writes():
    """Mirrors HttpHaltStore: read-only by construction, not by convention."""
    from src import l5_halt_gate as HG
    s = HG.HttpHaltStore("https://example.invalid")
    with pytest.raises(PermissionError):
        s.put("anything", b"{}")


def test_telemetry_names_the_ceilings_and_never_the_document():
    g = _gate(_doc(nonce="campaign-nonce-distinctive"))
    g.state()
    t = g.telemetry()
    assert t["ceilings"]["max_windows"] == CA.MAX_WINDOWS
    assert t["ceilings"]["max_total_exposure_usd"] == "5"
    assert t["sha256"] and len(t["sha256"]) == 64
    assert "distinctive" not in json.dumps(t)


def test_the_ceilings_are_the_authorized_envelope():
    assert CA.MAX_TOTAL_EXPOSURE_USD == D("5")
    assert CA.MAX_DAILY_LOSS_USD == D("5")
    assert CA.MAX_WINDOWS == 4
    assert CA.ALLOWED_SERIES == ("cpc-btc-updown-15m-",)
