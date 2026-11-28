"""Runtime campaign authorization: the pure decision.

Owner 2026-11-23: "Expired, invalid, missing or revoked authorization must
prevent new exposure." "The most restrictive applicable limit must always
govern execution." "Remove the two-minute authorization grace." "It must
never restart with a fresh budget or resume after an emergency halt."
"""
import datetime as dt
import json

import pytest

from src import pmus_campaign_auth as CA

SECRET = "campaign-secret"
SERVICE = "pmus-spring-l5-probe"
BUILD = "e6e8b4e60a2c5c9c4732d001035aaa718949f226"
NOW = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)
CEIL = {"max_window_s": 43200, "max_markets": 4, "order_qty": 2,
        "campaign_spend_usd": "16", "campaign_unpaired_usd": "6",
        "per_market_spend_usd": "8", "per_market_unpaired_usd": "3",
        "competitions": ["atp", "nba", "nfl", "nhl", "sea", "epl", "lal"]}


def iso(t):
    return t.isoformat()


def doc(**kw):
    d = {"campaign_id": "c-1", "service": SERVICE, "build": BUILD[:9],
         "not_before": iso(NOW - dt.timedelta(minutes=5)),
         "expires": iso(NOW + dt.timedelta(hours=11)),
         "competitions": ["atp", "nba"], "max_markets": 3, "order_qty": 1,
         "campaign_spend_usd": "16", "campaign_unpaired_usd": "6",
         "per_market_spend_usd": "8", "per_market_unpaired_usd": "3",
         "slate": "both", "issued_utc": iso(NOW - dt.timedelta(minutes=10))}
    secret = kw.pop("_secret", SECRET)
    drop = kw.pop("_drop", ())
    resign = kw.pop("_sign", True)
    d.update(kw)
    for k in drop:
        d.pop(k, None)
    if resign:
        d["hmac"] = CA.sign(d, secret)
    return d


def raw(**kw):
    return json.dumps(doc(**kw)).encode()


def ev(r, **kw):
    a = dict(secret=SECRET, service=SERVICE, build=BUILD, ceilings=CEIL,
             now=NOW)
    a.update(kw)
    return CA.evaluate(r, **a)


def test_a_valid_file_inside_its_window_permits():
    v = ev(raw())
    assert v["permit"] is True and v["reason"] is None
    c = v["campaign"]
    assert c["campaign_id"] == "c-1" and c["competitions"] == ["atp", "nba"]
    assert c["max_markets"] == "3" and c["order_qty"] == "1"


@pytest.mark.parametrize("r,reason", [
    (None, "AUTH_FILE_EMPTY"), (b"", "AUTH_FILE_EMPTY"),
    (b"   ", "AUTH_FILE_EMPTY"), (b"not json", "AUTH_FILE_MALFORMED"),
    (b"[1,2]", "AUTH_FILE_MALFORMED"), (b'"x"', "AUTH_FILE_MALFORMED")])
def test_empty_or_malformed_files_refuse(r, reason):
    v = ev(r)
    assert v["permit"] is False and v["reason"] == reason


def test_no_secret_configured_refuses_everything():
    assert ev(raw(), secret="")["reason"] == "AUTH_SECRET_NOT_SET"
    assert ev(raw(), secret="   ")["reason"] == "AUTH_SECRET_NOT_SET"


def test_unsigned_and_wrongly_signed_files_refuse():
    assert ev(raw(_sign=False))["reason"] == "AUTH_SIGNATURE_INVALID"
    assert ev(raw(_secret="other"))["reason"] == "AUTH_SIGNATURE_INVALID"


def test_the_halt_markers_secret_cannot_sign_a_campaign():
    """Separate authority: a file signed with another key is just a
    wrongly signed file."""
    assert ev(raw(_secret="halt-marker-secret"))["permit"] is False


@pytest.mark.parametrize("field", list(CA.REQUIRED))
def test_every_signed_field_is_covered_by_the_signature(field):
    d = doc()
    d[field] = (["nfl"] if field == "competitions" else
                3 if field in ("max_markets", "order_qty") and d[field] != 3
                else 2 if field in ("max_markets", "order_qty") else
                "1" if field.endswith("_usd") else
                "live" if field == "slate" else str(d[field]) + "x")
    v = ev(json.dumps(d).encode())
    assert v["permit"] is False and v["reason"] == "AUTH_SIGNATURE_INVALID"


def test_revokes_is_covered_by_the_signature():
    d = doc()
    d["revokes"] = "c-0"
    assert ev(json.dumps(d).encode())["reason"] == "AUTH_SIGNATURE_INVALID"


def test_an_unknown_field_invalidates_the_whole_file():
    """A typo cannot be silently ignored, and no limit that is not ours to
    set can ride in under a new name."""
    for extra in ("account_limit_usd", "loss_stop_usd", "daily_halt_usd",
                  "pair_cap", "halt_reset", "taker", "max_market"):
        d = doc()
        d[extra] = "100"
        v = ev(json.dumps(d).encode())
        assert v["reason"] == "AUTH_UNKNOWN_FIELD" and extra in v["fields"]


@pytest.mark.parametrize("field", list(CA.REQUIRED))
def test_a_missing_field_refuses(field):
    v = ev(raw(_drop=(field,)))
    assert v["permit"] is False
    assert v["reason"] in ("AUTH_MISSING_FIELD", "AUTH_SIGNATURE_INVALID")


def test_wrong_service_and_wrong_build_refuse():
    assert ev(raw(service="l5-pmus-live"))["reason"] == "AUTH_WRONG_SERVICE"
    assert ev(raw(build="0ebb4a14a"))["reason"] == "AUTH_WRONG_BUILD"
    assert ev(raw(build="e6e8"))["reason"] == "AUTH_WRONG_BUILD"   # too short
    assert ev(raw(), build=None)["reason"] == "AUTH_RUNNING_BUILD_UNKNOWN"
    assert ev(raw(), build="")["reason"] == "AUTH_RUNNING_BUILD_UNKNOWN"


def test_before_the_window_and_after_it_do_not_permit():
    early = ev(raw(not_before=iso(NOW + dt.timedelta(minutes=1))))
    assert early["permit"] is False and early["reason"] == "AUTH_NOT_STARTED"
    assert early["campaign"]["campaign_id"] == "c-1"
    late = ev(raw(), now=NOW + dt.timedelta(hours=11))
    assert late["permit"] is False and late["reason"] == "AUTH_EXPIRED"
    just = ev(raw(), now=NOW + dt.timedelta(hours=11) - dt.timedelta(seconds=1))
    assert just["permit"] is True


def test_times_must_carry_a_zone_and_make_a_window():
    assert ev(raw(expires="2026-11-25T23:00:00"))["reason"] == \
        "AUTH_TIME_UNREADABLE"
    assert ev(raw(not_before="soon"))["reason"] == "AUTH_TIME_UNREADABLE"
    assert ev(raw(expires=iso(NOW - dt.timedelta(hours=1)),
                  not_before=iso(NOW)))["reason"] == "AUTH_WINDOW_EMPTY"


def test_a_window_longer_than_the_ceiling_refuses_the_file():
    v = ev(raw(not_before=iso(NOW - dt.timedelta(minutes=5)),
               expires=iso(NOW + dt.timedelta(hours=12))))
    assert v["reason"] == "AUTH_ABOVE_CEILING" and v["field"] == "window"
    ok = ev(raw(not_before=iso(NOW), expires=iso(NOW + dt.timedelta(hours=12))))
    assert ok["reason"] == "AUTH_NOT_STARTED" or ok["permit"] is True


def test_a_file_dated_in_the_future_refuses():
    assert ev(raw(issued_utc=iso(NOW + dt.timedelta(minutes=10))))[
        "reason"] == "AUTH_FUTURE_DATED"


@pytest.mark.parametrize("field,over", [
    ("max_markets", 5), ("order_qty", 3), ("campaign_spend_usd", "16.01"),
    ("campaign_unpaired_usd", "6.01"), ("per_market_spend_usd", "8.01"),
    ("per_market_unpaired_usd", "3.01")])
def test_a_value_above_its_ceiling_refuses_the_whole_file(field, over):
    v = ev(raw(**{field: over}))
    assert v["permit"] is False and v["reason"] == "AUTH_ABOVE_CEILING"
    assert v["field"] == field and v["campaign"] is None      # not clamped


@pytest.mark.parametrize("field,bad", [
    ("max_markets", 0), ("max_markets", -1), ("max_markets", "x"),
    ("max_markets", 2.5), ("order_qty", 0), ("order_qty", True),
    ("campaign_spend_usd", "0"), ("campaign_spend_usd", "NaN"),
    ("campaign_unpaired_usd", "Infinity"), ("slate", "everything")])
def test_invalid_values_refuse(field, bad):
    v = ev(raw(**{field: bad}))
    assert v["permit"] is False
    assert v["reason"] in ("AUTH_VALUE_INVALID", "AUTH_ABOVE_CEILING")


def test_values_at_the_ceiling_are_allowed():
    v = ev(raw(max_markets=4, order_qty=2))
    assert v["permit"] is True


def test_a_competition_outside_the_deployed_set_refuses_the_file():
    v = ev(raw(competitions=["atp", "cricket"]))
    assert v["reason"] == "AUTH_ABOVE_CEILING"
    assert v["field"] == "competitions" and v["outside"] == ["cricket"]
    assert ev(raw(competitions=[]))["reason"] == "AUTH_COMPETITIONS_INVALID"
    assert ev(raw(competitions="atp"))["reason"] == \
        "AUTH_COMPETITIONS_INVALID"


def test_an_unreadable_ceiling_refuses():
    bad = dict(CEIL, order_qty=None)
    assert ev(raw(), ceilings=bad)["reason"] == "AUTH_CEILING_UNREADABLE"
    assert ev(raw(), ceilings=dict(CEIL, max_window_s=0))["reason"] == \
        "AUTH_CEILING_UNREADABLE"


# ------------------------------------------------ once, and restart-safe
def test_an_ended_campaign_is_never_accepted_again():
    v = ev(raw(), ended_ids=["c-1"])
    assert v["permit"] is False and v["reason"] == "AUTH_CAMPAIGN_ENDED"


def test_the_same_file_resumes_after_a_restart_with_its_original_window():
    r = raw()
    first = ev(r)
    again = ev(r, active={"id": "c-1", "digest": first["campaign"]["digest"]},
               now=NOW + dt.timedelta(hours=3))
    assert again["permit"] is True
    assert again["campaign"]["start"] == first["campaign"]["start"]
    assert again["campaign"]["end"] == first["campaign"]["end"]


def test_the_same_id_with_different_contents_is_refused():
    """No fresh budget and no longer window under an old name."""
    first = ev(raw())
    longer = raw(expires=iso(NOW + dt.timedelta(hours=11, minutes=30)))
    v = ev(longer, active={"id": "c-1",
                           "digest": first["campaign"]["digest"]})
    assert v["permit"] is False
    assert v["reason"] == "AUTH_CAMPAIGN_CONTENT_CHANGED"


def test_a_second_campaign_needs_to_revoke_the_one_in_force():
    act = {"id": "c-1", "digest": "x"}
    v = ev(raw(campaign_id="c-2"), active=act)
    assert v["permit"] is False
    assert v["reason"] == "AUTH_ANOTHER_CAMPAIGN_ACTIVE"
    ok = ev(raw(campaign_id="c-2", revokes="c-1"), active=act)
    assert ok["permit"] is True and ok["campaign"]["revokes"] == "c-1"


# -------------------------------------------------------------- no grace
def snap(**kw):
    s = {"raw": raw(), "age_s": 1.0, "err": None, "missing": False}
    s.update(kw)
    return s


def dec(s, **kw):
    a = dict(max_age_s=45.0, secret=SECRET, service=SERVICE, build=BUILD,
             ceilings=CEIL, now=NOW)
    a.update(kw)
    return CA.decide(s, **a)


def test_a_fresh_successful_fetch_permits():
    assert dec(snap())["permit"] is True


def test_a_failed_fetch_blocks_at_once_even_with_a_valid_file_in_hand():
    """Owner: no grace. The last good file is not honoured for a second
    after a refresh fails."""
    v = dec(snap(err="URLError", age_s=0.1))
    assert v["permit"] is False and v["reason"] == "AUTH_UNREACHABLE"


def test_a_stale_snapshot_blocks():
    v = dec(snap(age_s=46.0))
    assert v["permit"] is False and v["reason"] == "AUTH_SNAPSHOT_STALE"
    assert dec(snap(age_s=None))["reason"] == "AUTH_NOT_READ_YET"


def test_an_absent_file_blocks():
    v = dec(snap(missing=True, raw=None))
    assert v["permit"] is False and v["reason"] == "AUTH_FILE_ABSENT"


# ------------------------------------------------------------- the reader
class Clk:
    t = 100.0

    def __call__(self):
        return self.t


def test_reader_keeps_only_the_last_attempt():
    box = {"v": b"file-1"}

    def fetch():
        if isinstance(box["v"], Exception):
            raise box["v"]
        return box["v"]
    c = Clk()
    r = CA.FileReader("https://x/y", fetch=fetch, clock=c)
    assert r.snapshot()["age_s"] is None             # nothing read yet
    r.refresh_once()
    s = r.snapshot()
    assert s["raw"] == b"file-1" and s["err"] is None and not s["missing"]
    box["v"] = OSError("down")
    r.refresh_once()
    s = r.snapshot()
    assert s["err"] == "OSError" and s["raw"] is None      # bytes dropped
    assert dec(dict(s))["reason"] == "AUTH_UNREACHABLE"
    box["v"] = None                                   # host: no such file
    r.refresh_once()
    s = r.snapshot()
    assert s["missing"] is True and s["err"] is None
    assert dec(dict(s))["reason"] == "AUTH_FILE_ABSENT"


def test_reader_without_a_url_never_permits():
    r = CA.FileReader("", fetch=lambda: raw())
    r.refresh_once()
    assert r.snapshot()["err"] == "AUTH_URL_NOT_SET"
    assert dec(r.snapshot())["permit"] is False


def test_reader_treats_404_as_absent_and_other_errors_as_failures(
        monkeypatch):
    import io
    import urllib.error
    import urllib.request
    r = CA.FileReader("https://x/y", bearer="tok", accept="application/x")
    seen = {}

    def gone(req, timeout=None):
        seen.update({k.lower(): v for k, v in req.header_items()})
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO())
    monkeypatch.setattr(urllib.request, "urlopen", gone)
    r.refresh_once()
    assert r.snapshot()["missing"] is True
    assert seen["authorization"] == "Bearer tok"
    assert seen["accept"] == "application/x"

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "x", {}, io.BytesIO())
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    r.refresh_once()
    assert r.snapshot()["err"] == "HTTPError"


# ------------------------------------------------------- the signing tool
def _sign(monkeypatch, capsys, *extra, secret=SECRET):
    from scripts import pmus_campaign_auth_sign as S
    if secret is None:
        monkeypatch.delenv(S.SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(S.SECRET_ENV, secret)
    args = ["--campaign-id", "c-9", "--build", BUILD[:9],
            "--start", iso(NOW), "--hours", "12",
            "--competitions", "atp,nba", "--max-markets", "4",
            "--order-qty", "1"] + list(extra)
    rc = S.main(args, now=NOW - dt.timedelta(minutes=1))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_the_tool_emits_a_file_the_bot_accepts(monkeypatch, capsys):
    rc, out, err = _sign(monkeypatch, capsys)
    assert rc == 0 and SECRET not in out and SECRET not in err
    v = ev(out.encode(), now=NOW + dt.timedelta(minutes=1))
    assert v["permit"] is True and v["campaign"]["campaign_id"] == "c-9"


def test_the_tool_refuses_without_a_secret(monkeypatch, capsys):
    rc, out, err = _sign(monkeypatch, capsys, secret=None)
    assert rc == 2 and out == ""


@pytest.mark.parametrize("extra", [
    ("--max-markets", "5"), ("--order-qty", "3"), ("--hours", "13"),
    ("--competitions", "atp,cricket"), ("--campaign-unpaired-usd", "7")])
def test_the_tool_refuses_values_above_the_ceilings(monkeypatch, capsys,
                                                    extra):
    rc, out, err = _sign(monkeypatch, capsys, *extra)
    assert rc == 3 and out == "" and "AUTH_ABOVE_CEILING" in err
