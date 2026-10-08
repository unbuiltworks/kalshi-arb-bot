"""THE DURABLE TERMINAL CACHE, AND THE 429 IT EXISTS TO SURVIVE.

On 2026-10-08 the corrected startup reconciliation reached the venue and
was rate-limited instead:

    20:48:49Z  ORDER_STATE_UNREADABLE:/v1/order/CZGW5Q0DJZ8E:HTTPError:429
    20:51:51Z  ORDER_STATE_UNREADABLE:/v1/order/CZGW6T1G8Z84:HTTPError:429

Forty known ids, a cold cache, one GET each, and the venue answered 429
after roughly fifteen of them about a second in. Backoff cannot fix it: the
whole read has to land inside the 5.0s freshness bound, so retrying twenty
ids would trade a rate-limit refusal for a staleness refusal.

"TERMINAL IS FINAL, SO IT IS READ ONCE" was already the rule; it was just
not durable. These tests pin the properties that make persisting it safe:

  * only venue-confirmed TERMINAL rows are ever written;
  * the file is atomic, versioned, digest-checked, and scoped to an
    account and a session -- anything unverifiable loads as {};
  * an id the OPEN-ORDERS list reports as resting is ALWAYS re-read, so
    the cache can shrink work but never change an answer;
  * the 5.0s freshness bound and every exposure check are untouched, and
    a 429 on a read we still NEED is still a refusal.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort.test_e1_outstanding_orders import (UP, DOWN,
                                                              order)
from analysis.l5.e1_cohort.test_e1_startup_history import (ALL_IDS,
                                                           CANCELLED_IDS,
                                                           FILLED_IDS, NOW,
                                                           S1215, S1230,
                                                           S_LIVE,
                                                           history_rows)

SESSION = "E1-COHORT-20261008T005816Z-051888d8"
SCOPE = "acct-scope-abc"


def rate_limited(rows, *, allow=15, positions=None):
    """A venue that answers HTTP 429 after `allow` per-id GETs -- which is
    what the real one did, twice."""
    by_id = {str(r["id"]): r for r in rows}
    state = {"gets": 0}

    class _429(Exception):
        def __init__(self):
            super().__init__("Too Many Requests")
            self.code = 429

    def _read(path):
        if path == OS.OPEN_PATH:
            return {"orders": []}
        if path == OS.POSITIONS_PATH:
            return (positions if positions is not None
                    else {"positions": {}, "eof": True})
        if path.startswith("/v1/order/"):
            state["gets"] += 1
            if state["gets"] > allow:
                raise _429()
            oid = path.rsplit("/", 1)[-1]
            if oid in by_id:
                return {"order": by_id[oid]}
            raise AssertionError("unexpected id %s" % oid)
        raise AssertionError("unexpected path %s" % path)

    _read.gets = state                                         # type: ignore
    return _read


def warm_cache_from(rows):
    """The cache the previous process would have persisted."""
    return {str(r["id"]): {"terminal": True, "raw": dict(r)}
            for r in rows if str(r["state"]) in OS.TERMINAL_STATES}


def read(reader, *, cache=None, known=None, now=NOW, outcome_up=None,
         **kw):
    """The armed startup call: no slug scope, no window pin, all 40 ids.

    `outcome_up` is None at startup, exactly as production passes it. A
    test that needs a LIVE order to be priceable (rather than refused as
    unpriceable) passes the pin explicitly.
    """
    return OS.read_order_state(reader, slugs=[], outcome_up=outcome_up,
                               now=now, known_ids=list(known or ALL_IDS),
                               terminal_cache=cache, **kw)


# ===================== 6a. COLD START WITH ALL 40 IDS ===================

def test_cold_start_with_forty_ids_is_RATE_LIMITED_as_it_was_live():
    """The exact live failure, reproduced: no cache, forty ids, 429."""
    r = rate_limited(history_rows(), allow=15)
    view = read(r, cache={})
    assert view["ok"] is False
    assert view["reason"].startswith("ORDER_STATE_UNREADABLE:/v1/order/")
    assert view["reason"].endswith(":429"), view["reason"]
    assert OS.reconcile_at_startup(view)["may_submit"] is False
    assert OS.reconcile_at_startup(view)["FAILS_CLOSED"] is True


def test_a_WARM_cache_survives_the_same_rate_limit():
    """THE FIX. The same venue, the same limit, the same forty ids -- but
    the terminal rows come off the volume, so almost nothing is asked."""
    rows = history_rows()
    r = rate_limited(rows, allow=15)
    view = read(r, cache=warm_cache_from(rows))
    assert view["ok"] is True, view.get("reason")
    assert view["n_known"] == 40
    assert len(view["ids_served_from_terminal_cache"]) == 40
    assert view["per_id_gets_this_read"] == 0
    assert r.gets["gets"] == 0, "a warm cache must ask the venue nothing"
    # and the startup gate now passes
    rec = OS.reconcile_at_startup(view)
    assert rec["may_submit"] is True, rec
    assert view["n_resolved_history"] == 20
    assert D(view["historical_resolved_filled_qty"]) == D("2.60")


def test_a_warm_cache_still_reads_the_ids_it_does_not_have():
    """Partially warm: 20 cached, 20 asked. The live ones are still read."""
    rows = history_rows()
    partial = warm_cache_from([r for r in rows if r["id"] in FILLED_IDS])
    r = rate_limited(rows, allow=25)
    view = read(r, cache=partial)
    assert view["ok"] is True, view.get("reason")
    assert len(view["ids_served_from_terminal_cache"]) == 20
    assert view["per_id_gets_this_read"] == 20
    assert r.gets["gets"] == 20


# ============== 6b. THE OPEN-ORDER CONFLICT: NEVER TRUSTED ==============

def test_a_cached_id_the_venue_reports_RESTING_is_re_read():
    """The one case a durable cache could have changed an answer. The
    open-orders list wins, the id is re-read, and it is published."""
    rows = history_rows()
    resting = order(FILLED_IDS[0], state="ORDER_STATE_NEW", qty="0.13",
                    cum="0", leaves="0.13", wire="0.49", side=UP,
                    slug=S_LIVE)
    by_id = {str(r["id"]): r for r in rows}
    by_id[str(resting["id"])] = resting

    def _read(path):
        if path == OS.OPEN_PATH:
            return {"orders": [resting]}
        if path == OS.POSITIONS_PATH:
            return {"positions": {}, "eof": True}
        oid = path.rsplit("/", 1)[-1]
        return {"order": by_id[oid]}

    # the cache LIES: it claims that id is a filled, finished order.
    # A pin is passed so the re-read order PRICES -- without one the view
    # refuses as UNPRICEABLE_EXPOSURE, which is also safe but hides the
    # conflict bookkeeping this test is about.
    view = read(_read, cache=warm_cache_from(rows), outcome_up=UP)
    assert view["cached_but_resting_re_read"] == [FILLED_IDS[0]]
    assert view["n_cached_but_resting_re_read"] == 1
    assert FILLED_IDS[0] not in view["ids_served_from_terminal_cache"]
    assert view["a_cached_id_the_venue_calls_resting_is_never_trusted"] \
        is True
    # and the re-read makes it outstanding, which blocks startup
    assert FILLED_IDS[0] in view.get("outstanding_ids", [])
    assert OS.reconcile_at_startup(view)["may_submit"] is False


def test_an_entry_whose_state_is_not_terminal_is_ignored_at_use():
    rows = history_rows()
    bad = warm_cache_from(rows)
    bad[FILLED_IDS[1]] = {"terminal": True,
                          "raw": dict(order(FILLED_IDS[1],
                                            state="ORDER_STATE_NEW",
                                            slug=S1215))}
    r = rate_limited(rows, allow=40)
    view = read(r, cache=bad)
    assert FILLED_IDS[1] not in view["ids_served_from_terminal_cache"]
    assert r.gets["gets"] == 1, "the bad entry must be asked about"


def test_an_entry_whose_id_does_not_match_its_key_is_ignored():
    rows = history_rows()
    bad = warm_cache_from(rows)
    bad[FILLED_IDS[2]] = {"terminal": True,
                          "raw": dict(bad[FILLED_IDS[3]]["raw"])}
    r = rate_limited(rows, allow=40)
    view = read(r, cache=bad)
    assert FILLED_IDS[2] not in view["ids_served_from_terminal_cache"]
    assert r.gets["gets"] == 1


# ================= 6c. PERSISTENCE: ATOMIC AND VERIFIED ================

def test_a_round_trip_preserves_every_terminal_row(tmp_path):
    rows = history_rows()
    cache = warm_cache_from(rows)
    rep = OS.save_terminal_cache(cache, str(tmp_path), session_id=SESSION,
                                 scope=SCOPE)
    assert rep["saved"] is True and rep["entries"] == 40
    assert rep["atomic"] == "os.replace"
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert back["report"]["trusted"] is True
    assert back["report"]["loaded"] == 40
    assert set(back["cache"]) == set(ALL_IDS)
    for oid, ent in back["cache"].items():
        assert ent["terminal"] is True
        assert str(ent["raw"]["id"]) == oid
        assert str(ent["raw"]["state"]) in OS.TERMINAL_STATES


def test_only_TERMINAL_rows_are_ever_written(tmp_path):
    """Active orders, ambiguous states and pending intents must never be
    persisted as terminal."""
    cache = {
        "CZTERM0001": {"terminal": True,
                       "raw": dict(order("CZTERM0001",
                                         state="ORDER_STATE_CANCELED",
                                         slug=S1230))},
        "CZLIVE0001": {"terminal": True,
                       "raw": dict(order("CZLIVE0001",
                                         state="ORDER_STATE_NEW",
                                         slug=S_LIVE))},
        "CZPART0001": {"terminal": True,
                       "raw": dict(order(
                           "CZPART0001",
                           state="ORDER_STATE_PARTIALLY_FILLED",
                           cum="0.05", leaves="0.08", slug=S_LIVE))},
        "CZNOSLUG01": {"terminal": True,
                       "raw": dict(order("CZNOSLUG01",
                                         state="ORDER_STATE_FILLED",
                                         slug=None))},
        OS.LOCAL_INTENT + "w3:x:UP:0.49": {
            "terminal": True,
            "raw": {"id": OS.LOCAL_INTENT + "w3:x:UP:0.49",
                    "state": "ORDER_STATE_FILLED", "marketSlug": S_LIVE}},
        "CZNOTTERM1": {"terminal": False,
                       "raw": dict(order("CZNOTTERM1",
                                         state="ORDER_STATE_FILLED",
                                         slug=S1215))},
    }
    rep = OS.save_terminal_cache(cache, str(tmp_path), session_id=SESSION,
                                 scope=SCOPE)
    assert rep["entries"] == 1
    assert rep["skipped_non_terminal"] == 5
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert set(back["cache"]) == {"CZTERM0001"}


def test_no_credential_is_written_to_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PMUS_API_KEY_ID", "KEY-ID-SECRETISH")
    monkeypatch.setenv("PMUS_API_SECRET_KEY", "SUPER-SECRET-VALUE")
    OS.save_terminal_cache(warm_cache_from(history_rows()), str(tmp_path),
                           session_id=SESSION)
    blob = open(OS.terminal_cache_path(str(tmp_path)), encoding="utf8").read()
    assert "SUPER-SECRET-VALUE" not in blob
    assert "KEY-ID-SECRETISH" not in blob
    # the scope is a digest, stable for the account and reversible to nothing
    assert OS.account_scope() == OS.account_scope()
    assert len(OS.account_scope()) == 16


# ============ 6d. CORRUPT, MISSING AND MISMATCHED NEVER TRUSTED ========

def test_a_missing_cache_loads_as_empty(tmp_path):
    back = OS.load_terminal_cache(str(tmp_path / "nope"),
                                  session_id=SESSION, scope=SCOPE)
    assert back["cache"] == {}
    assert back["report"]["why"] == "NO_CACHE_FILE"
    assert back["report"]["trusted"] is False


@pytest.mark.parametrize("blob", ["", "{", "null", "[]", '"a string"',
                                  '{"version": 1}', "not json at all"])
def test_a_CORRUPT_cache_loads_as_empty(tmp_path, blob):
    os.makedirs(str(tmp_path), exist_ok=True)
    open(OS.terminal_cache_path(str(tmp_path)), "w",
         encoding="utf8").write(blob)
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert back["cache"] == {}
    assert back["report"]["trusted"] is False


def test_a_TAMPERED_cache_fails_its_digest(tmp_path):
    OS.save_terminal_cache(warm_cache_from(history_rows()), str(tmp_path),
                           session_id=SESSION, scope=SCOPE)
    p = OS.terminal_cache_path(str(tmp_path))
    doc = json.load(open(p, encoding="utf8"))
    # flip a cached order from CANCELED to FILLED without touching the digest
    doc["entries"][CANCELLED_IDS[0]]["raw"]["cumQuantity"] = "0.13"
    json.dump(doc, open(p, "w", encoding="utf8"), sort_keys=True)
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert back["cache"] == {}
    assert back["report"]["why"] == "DIGEST_MISMATCH"


def test_a_cache_from_ANOTHER_SESSION_is_not_trusted(tmp_path):
    OS.save_terminal_cache(warm_cache_from(history_rows()), str(tmp_path),
                           session_id="SOME-OTHER-SESSION", scope=SCOPE)
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert back["cache"] == {}
    assert back["report"]["why"] == "SESSION_MISMATCH"


def test_a_cache_from_ANOTHER_ACCOUNT_is_not_trusted(tmp_path):
    OS.save_terminal_cache(warm_cache_from(history_rows()), str(tmp_path),
                           session_id=SESSION, scope="a-different-account")
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert back["cache"] == {}
    assert back["report"]["why"] == "ACCOUNT_SCOPE_MISMATCH"


def test_a_WRONG_VERSION_cache_is_not_trusted(tmp_path):
    OS.save_terminal_cache(warm_cache_from(history_rows()), str(tmp_path),
                           session_id=SESSION, scope=SCOPE)
    p = OS.terminal_cache_path(str(tmp_path))
    doc = json.load(open(p, encoding="utf8"))
    doc["version"] = OS.CACHE_VERSION + 1
    json.dump(doc, open(p, "w", encoding="utf8"), sort_keys=True)
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert back["cache"] == {}
    assert back["report"]["why"] == "WRONG_VERSION"


def test_an_entry_with_a_non_terminal_state_is_rejected_at_LOAD(tmp_path):
    cache = warm_cache_from(history_rows())
    OS.save_terminal_cache(cache, str(tmp_path), session_id=SESSION,
                           scope=SCOPE)
    p = OS.terminal_cache_path(str(tmp_path))
    doc = json.load(open(p, encoding="utf8"))
    doc["entries"][FILLED_IDS[0]]["raw"]["state"] = "ORDER_STATE_NEW"
    # re-digest so ONLY the per-entry validation can catch it
    import hashlib
    blob = json.dumps(doc["entries"], sort_keys=True, separators=(",", ":"))
    doc["digest"] = hashlib.sha256(blob.encode()).hexdigest()
    json.dump(doc, open(p, "w", encoding="utf8"), sort_keys=True)
    back = OS.load_terminal_cache(str(tmp_path), session_id=SESSION,
                                  scope=SCOPE)
    assert FILLED_IDS[0] not in back["cache"]
    assert back["report"]["rejected"] == 1
    assert back["report"]["loaded"] == 39


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    OS.save_terminal_cache(warm_cache_from(history_rows()), str(tmp_path),
                           session_id=SESSION, scope=SCOPE)
    names = sorted(os.listdir(str(tmp_path)))
    assert names == [OS.CACHE_FILE], names


# ====== 6e. FRESH SNAPSHOTS, FRESHNESS BOUND AND EXPOSURE INTACT =======

def test_open_orders_and_positions_are_ALWAYS_read_even_when_fully_warm():
    rows = history_rows()
    seen = []

    def _read(path):
        seen.append(path)
        if path == OS.OPEN_PATH:
            return {"orders": []}
        if path == OS.POSITIONS_PATH:
            return {"positions": {}, "eof": True}
        raise AssertionError("no per-id GET expected: %s" % path)

    view = read(_read, cache=warm_cache_from(rows))
    assert view["ok"] is True
    assert OS.OPEN_PATH in seen
    assert OS.POSITIONS_PATH in seen


def test_the_five_second_freshness_bound_is_unchanged():
    """The bound is untouched -- and the cache is what keeps us inside it.

    Same slow venue both times: 0.2s per per-id GET. Cold, the forty reads
    blow the 5.0s bound and the view REFUSES. Warm, nothing is asked and
    the view is fresh. The cache shrinks the work; it does not relax the
    check.
    """
    assert OS.MAX_ORDER_STATE_AGE_S == 5.0
    rows = history_rows()

    def slow_venue():
        clock = {"t": NOW}
        inner = rate_limited(rows, allow=40)

        def _read(path):
            if path.startswith("/v1/order/"):
                clock["t"] += 0.2
            return inner(path)
        return _read, (lambda: clock["t"])

    reader, now_fn = slow_venue()
    cold = read(reader, cache={}, now_fn=now_fn)
    assert cold["ok"] is False
    assert cold["reason"].startswith("ORDER_STATE_STALE:"), cold["reason"]
    assert cold["age_s"] > 5.0
    assert OS.reconcile_at_startup(cold)["may_submit"] is False

    reader, now_fn = slow_venue()
    warm = read(reader, cache=warm_cache_from(rows), now_fn=now_fn)
    assert warm["ok"] is True, warm.get("reason")
    assert warm["age_s"] <= 5.0


def test_a_429_on_a_READ_WE_STILL_NEED_is_still_a_refusal():
    """Suppression is not the fix. A live order we must read, that the
    venue will not serve, still fails closed."""
    rows = history_rows()
    live = order("CZLIVE0009", state="ORDER_STATE_NEW", qty="0.13",
                 cum="0", leaves="0.13", wire="0.49", side=UP, slug=S_LIVE)

    class _429(Exception):
        def __init__(self):
            super().__init__("429")
            self.code = 429

    def _read(path):
        if path == OS.OPEN_PATH:
            return {"orders": [live]}
        if path == OS.POSITIONS_PATH:
            return {"positions": {}, "eof": True}
        raise _429()

    view = read(_read, cache=warm_cache_from(rows),
                known=ALL_IDS + ["CZLIVE0009"])
    assert view["ok"] is False
    assert "429" in view["reason"]
    assert OS.reconcile_at_startup(view)["may_submit"] is False


def test_unresolved_inventory_still_blocks_with_a_warm_cache():
    """A warm cache must not turn a held position into history."""
    rows = history_rows()
    held = {"positions": {S1215: {"netPositionDecimal": "2.60"}},
            "eof": True}
    r = rate_limited(rows, allow=40, positions=held)
    view = read(r, cache=warm_cache_from(rows))
    assert view["ok"] is False
    assert view["reason"] == "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"
    assert OS.reconcile_at_startup(view)["may_submit"] is False


def test_a_partial_fill_is_never_served_from_the_cache():
    rows = history_rows()
    part = order("CZPART0002", state="ORDER_STATE_PARTIALLY_FILLED",
                 qty="0.13", cum="0.05", leaves="0.08", wire="0.49",
                 side=UP, slug=S_LIVE)
    cache = warm_cache_from(rows)
    # even if something tried to cache it, it is not terminal
    cache["CZPART0002"] = {"terminal": True, "raw": dict(part)}
    by_id = {str(r["id"]): r for r in rows + [part]}

    def _read(path):
        if path == OS.OPEN_PATH:
            return {"orders": [part]}
        if path == OS.POSITIONS_PATH:
            return {"positions": {}, "eof": True}
        return {"order": by_id[path.rsplit("/", 1)[-1]]}

    view = read(_read, cache=cache, known=ALL_IDS + ["CZPART0002"],
                outcome_up=UP)
    assert "CZPART0002" not in view.get(
        "ids_served_from_terminal_cache", [])
    assert "CZPART0002" in view["cached_but_resting_re_read"]


# ===================== 6f. THE PROVIDER WIRING =========================

def test_the_provider_loads_and_saves_the_cache(tmp_path, monkeypatch):
    from analysis.l5.e1_cohort import pmus_provider as PV

    rows = history_rows()
    OS.save_terminal_cache(warm_cache_from(rows), str(tmp_path),
                           session_id=SESSION, scope=OS.account_scope())

    class _Prog:
        @staticmethod
        def windows_used():
            return 3

    p = PV.PmusWindowProvider(
        progress=_Prog(), session_auth=None,
        feed=object(), order_state_reader=rate_limited(rows, allow=0),
        known_order_ids=list(ALL_IDS),
        cache_state_dir=str(tmp_path), cache_session_id=SESSION,
        now_fn=lambda: NOW)
    assert p.terminal_cache_load["trusted"] is True
    assert p.terminal_cache_load["loaded"] == 40
    assert len(p.terminal_order_cache) == 40
    # and the pin-free startup read now succeeds against a venue that
    # would refuse every per-id GET
    view = p.order_state({})
    assert view["ok"] is True, view.get("reason")
    assert OS.reconcile_at_startup(view)["may_submit"] is True
    assert p.terminal_cache_save["saved"] is True


def test_the_provider_survives_a_corrupt_cache_on_the_volume(tmp_path):
    from analysis.l5.e1_cohort import pmus_provider as PV

    os.makedirs(str(tmp_path), exist_ok=True)
    open(OS.terminal_cache_path(str(tmp_path)), "w",
         encoding="utf8").write("{{{ not json")

    class _Prog:
        @staticmethod
        def windows_used():
            return 3

    p = PV.PmusWindowProvider(
        progress=_Prog(), session_auth=None, feed=object(),
        order_state_reader=rate_limited(history_rows(), allow=40),
        known_order_ids=list(ALL_IDS),
        cache_state_dir=str(tmp_path), cache_session_id=SESSION,
        now_fn=lambda: NOW)
    assert p.terminal_order_cache == {}
    assert p.terminal_cache_load["trusted"] is False
    # it simply asks the venue instead
    view = p.order_state({})
    assert view["ok"] is True, view.get("reason")
    assert view["per_id_gets_this_read"] == 40


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
