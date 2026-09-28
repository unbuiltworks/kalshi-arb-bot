"""No explicit PMUS_LIVE_MANDATE = no live authority. Period.

THE RULE (owner, 2026-09-28). For the Rung-A executor there is no fallback to a
repository file, a historical mandate, a previous or cached authorization, or a
default monetary envelope.

WHY IT IS A RULE. `analysis/l5/authorization/live_mandate.json` was git-tracked and
held the EXPIRED OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT authorization --
$5 per market, $15 total, 10 markets, service `l5-r1-probe`.
`pmus_live_mandate.load_mandate()` falls back to that path, and `parse_mandate`
accepted it WITHOUT error, because expiry and service are checked at `admit()` time
rather than at load. A probe with no authorization in its environment would have
loaded $15 of authority out of the repository, armed, reported it, and failed only
at its first order.

`admit()` would have refused that order, so no money could move. But "a mandate
loaded" is not "authority exists", and that is the
"we could not read today's authorization, so we used yesterday's" failure which
`pmus_live_mandate`'s own docstring names as the entire 2026-09-16 incident.

Every test below writes a real expired mandate file into a real fallback location
first, so the absence of authority is proven WITH the hazard present rather than
merely with it absent.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as EP
from src import pmus_live_mandate as MANDATE

SLUG = "cpc-btc-updown-15m-2026-09-28-0600z"
SEED = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

#: the real thing that was in the repository, verbatim
EXPIRED_CP1 = {
    "authorization_id": "OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT",
    "authorized_at": "2026-09-17T12:41:20Z",
    "service": "l5-r1-probe",
    "strategy": "pmus-l5-continuous-v1",
    "strategy_version": "CP1",
    "max_markets": 1, "max_total_markets": 10,
    "max_spend_per_market": "5.00", "max_total_spend": "15.00",
    "max_residual": "3.00", "max_daily_realized_loss": "6.00",
    "expiry": "2026-09-18T00:41:20Z",
}

VALID = {
    "authorization_id": "OWNER-NOFALLBACK-TEST",
    "authorized_at": "2026-09-28T00:00:00Z",
    "service": EP.RUNG_A_SERVICE,
    "strategy": EP.RUNG_A_STRATEGY,
    "strategy_version": "nofallback-test",
    "max_markets": 1, "max_total_markets": 1,
    "max_spend_per_market": "1.00", "max_total_spend": "1.00",
    "max_residual": "1.00", "max_daily_realized_loss": "1.00",
    "expiry": "2099-01-01T00:00:00Z",
}


class Venue:
    name = "VENUE_DOUBLE"

    def __init__(self):
        self.posts = []

    def request(self, method, path, body=None, **kw):
        if str(method).upper() == "POST":
            self.posts.append((path, body))
        return {}

    def order(self, vid):
        return {}


class Feed:
    def start(self):
        pass

    def wait_ready(self, sleep=None):
        return True

    def books(self):
        return []

    def health(self):
        return {"connected": True, "frames": 1, "fresh": True,
                "source": "TEST", "errors": [], "stop_reason": "test"}

    def stop(self):
        pass


def ticking(start_at, step=1.0):
    box = {"t": float(start_at)}

    def now():
        box["t"] += step
        return box["t"]
    return now


#: opt out of conftest's autouse $1,000,000 "test-suite" mandate. These tests are
#: ABOUT mandate resolution, so a fixture supplying one would decide the result.
#: conftest's own docstring makes the point: if that fixture keeps a spending test
#: green, the test is measuring the fixture.
pytestmark = pytest.mark.real_mandate_env


@pytest.fixture()
def hazard(tmp_path, monkeypatch):
    """An armed owner, and an EXPIRED mandate file sitting in a fallback path."""
    stale = tmp_path / "live_mandate.json"
    stale.write_text(json.dumps(EXPIRED_CP1))
    monkeypatch.setenv(MANDATE.MANDATE_FILE_ENV, str(stale))
    monkeypatch.setenv("PMUS_API_KEY_ID", "key-aaaaaaaaaaaa")
    monkeypatch.setenv("PMUS_API_SECRET_KEY", SEED)
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "nofallback-session")
    monkeypatch.setenv("PMUS_Q1_ARM", "nofallback-session")
    monkeypatch.setenv(EP.AUTH.MARKET_ENV, SLUG)
    # sanity: the hazard is REAL -- load_mandate does pick the stale file up
    loaded, err = MANDATE.load_mandate()
    assert loaded is not None and err is None, (
        "the fallback hazard is not reproduced, so these tests prove nothing")
    assert loaded.max_total_spend == D("15.00")
    return tmp_path


def run_probe(tmp_path, venue):
    return EP.run(clock=ticking(EP.window_start(SLUG) + EP.PREFLIGHT_AT_S),
                  sleep=lambda *_a, **_k: None,
                  transport_factory=lambda slug: venue,
                  state_path=str(tmp_path / "inv.json"),
                  journal_path=str(tmp_path / "journal.jsonl"),
                  ledger_path=str(tmp_path / "ledger.jsonl"),
                  book_feed=Feed())


def assert_no_authority(rep, venue):
    assert rep["refused"] == "NO_MONETARY_AUTHORITY", rep
    assert rep["armed"] is False
    assert rep["effective_limits_usd"] == "0"
    assert rep["monetary_authority"] == "NONE_NO_MANDATE"
    assert rep["submitted"] == 0
    assert venue.posts == [], "an order went out with no live authority"


# ---------------------------------------------- the five ways to have no authority

def test_missing_mandate_with_an_expired_file_present(hazard, monkeypatch):
    """THE REGRESSION. Env unset, stale $15 file present -> $0 and NOT_ARMED."""
    monkeypatch.delenv(MANDATE.MANDATE_ENV, raising=False)
    venue = Venue()
    rep = run_probe(hazard, venue)
    assert_no_authority(rep, venue)
    assert "mandate_env_required" in str(rep["detail"])
    # and emphatically NOT the stale file's numbers
    assert "15.00" not in json.dumps(rep)
    assert "l5-r1-probe" not in json.dumps(rep)


def test_an_empty_mandate_env_is_not_a_mandate(hazard, monkeypatch):
    monkeypatch.setenv(MANDATE.MANDATE_ENV, "   ")
    venue = Venue()
    assert_no_authority(run_probe(hazard, venue), venue)


def test_an_expired_mandate_fails_before_arming(hazard, monkeypatch):
    monkeypatch.setenv(MANDATE.MANDATE_ENV, json.dumps(EXPIRED_CP1))
    venue = Venue()
    rep = run_probe(hazard, venue)
    assert_no_authority(rep, venue)
    assert "mandate_expired" in str(rep["detail"])


def test_a_wrong_service_mandate_fails_before_arming(hazard, monkeypatch):
    raw = dict(VALID, service="l5-r1-probe")
    monkeypatch.setenv(MANDATE.MANDATE_ENV, json.dumps(raw))
    venue = Venue()
    rep = run_probe(hazard, venue)
    assert_no_authority(rep, venue)
    assert "mandate_service_mismatch" in str(rep["detail"])


def test_a_wrong_strategy_mandate_fails_before_arming(hazard, monkeypatch):
    raw = dict(VALID, strategy="pmus-l5-continuous-v1")
    monkeypatch.setenv(MANDATE.MANDATE_ENV, json.dumps(raw))
    venue = Venue()
    rep = run_probe(hazard, venue)
    assert_no_authority(rep, venue)
    assert "mandate_strategy_mismatch" in str(rep["detail"])


@pytest.mark.parametrize("bad,why", [
    ("{not json", "unparseable"),
    (json.dumps({"authorization_id": "X"}), "missing fields"),
    (json.dumps(dict(VALID, max_spend_per_market="")), "empty money field"),
    (json.dumps(dict(VALID, max_total_spend="-1")), "negative money field"),
])
def test_a_malformed_mandate_fails_before_arming(hazard, monkeypatch, bad, why):
    """Parametrised, not looped: one authorization trades one market, so a loop
    spends the ledger on its first iteration and the rest measure
    AUTHORIZATION_ALREADY_USED instead of the mandate."""
    monkeypatch.setenv(MANDATE.MANDATE_ENV, bad)
    venue = Venue()
    assert_no_authority(run_probe(hazard, venue), venue)


# ------------------------------------------------------- and a valid one works

def test_a_valid_rung_a_mandate_is_accepted(hazard, monkeypatch):
    monkeypatch.setenv(MANDATE.MANDATE_ENV, json.dumps(VALID))
    m, why = EP.rung_a_mandate()
    assert m is not None, why
    assert m.max_spend_per_market == D("1.00")
    assert m.service == EP.RUNG_A_SERVICE


# ------------------------------------------- the repository holds no live mandate

def test_the_repository_contains_no_loadable_live_mandate():
    """The tracked file is gone from every executable role.

    Restoring either the name or the location re-creates the fallback, so this
    asserts on both.
    """
    import os
    assert not os.path.exists(MANDATE.DEFAULT_MANDATE_FILE), (
        "%s is back; load_mandate() falls back to it"
        % MANDATE.DEFAULT_MANDATE_FILE)
    # the provenance record is preserved, and is not loadable as authority
    rec = ("analysis/l5/authorization/historical/"
           "live_mandate_2026-09-17_CP1.expired.json.record")
    assert os.path.exists(rec), "the historical record was lost, not relocated"
    assert not rec.endswith(".json")


def test_run_never_reaches_the_fallback_loader():
    """Structural: rung_a_mandate, never load_mandate, from run()'s graph."""
    import ast
    tree = ast.parse(open(EP.__file__, encoding="utf-8").read())
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def reached(name, seen=None):
        seen = seen or set()
        if name in seen or name not in funcs:
            return set()
        seen.add(name)
        out = set()
        for n in ast.walk(funcs[name]):
            if isinstance(n, ast.Call):
                f = n.func
                a = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else None)
                if a:
                    out.add(a)
                    out |= reached(a, seen)
        return out

    g = reached("run")
    assert "rung_a_mandate" in g
    assert "load_mandate" not in g
