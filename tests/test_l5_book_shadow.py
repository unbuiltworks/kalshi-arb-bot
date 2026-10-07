"""Tests for the two-token book-state shadow collector.

Offline by construction: no test here touches the network. Every venue read
is injected.

What is proven:
  * the boot gate FAILS when an order-capable module is importable;
  * the writer is append-only and never rewrites a part;
  * BOTH tokens are recorded per market, and a single-token tape FAILS;
  * every row carries its provenance envelope;
  * the pacer respects its configured interval and backs off on 429;
  * a credential VALUE never reaches a log line, a row or the gate output.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from src import l5_book_shadow as bs


# --------------------------------------------------------------- boot gate

def test_boot_gate_fails_when_order_capable_module_importable():
    # the monorepo itself carries the order path, which is exactly the
    # condition the gate must refuse.
    res = bs.boot_gate(["src.l5_pmus_transport"])
    assert res["ok"] is False
    assert "mutation_incapable" in res["blocking_failures"]
    assert res["gates"]["mutation_incapable"]["detail"][
        "importable_order_modules"] == ["src.l5_pmus_transport"]


def test_boot_gate_passes_when_order_modules_absent():
    res = bs.boot_gate(["src.definitely_not_a_module_zzz"])
    assert res["ok"] is True
    assert res["blocking_failures"] == []
    assert res["gates"]["role_is_observation_only"]["detail"][
        "orders_submitted"] == 0


def test_boot_gate_detects_a_module_already_in_sys_modules(monkeypatch):
    name = "src.pmus_q1_executor_stub_zzz"
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    assert bs.module_importable(name) is True
    assert bs.boot_gate([name])["ok"] is False


def test_main_refuses_to_start_when_gate_fails(capsys):
    # default FORBIDDEN_MODULES includes the real order path, present here
    assert bs.main(["--gate-only"]) == 2
    out = capsys.readouterr().out
    assert "BOOT GATE FAILED" in out


# ------------------------------------------------------------ append-only

def test_writer_is_append_only_and_never_rewrites(tmp_path):
    w = bs.TapeWriter(tmp_path)
    assert bs.TapeWriter.MODE == "a"
    first = w.write({"kind": "book", "n": 1})
    w.write({"kind": "book", "n": 2})
    assert first.read_text().count("\n") == 2
    before = first.read_text()
    w.write({"kind": "book", "n": 3})
    after = first.read_text()
    assert after.startswith(before)          # history preserved verbatim
    assert len(after.splitlines()) == 3
    assert w.rows_written == 3


def test_writer_source_contains_no_truncating_open_mode():
    import inspect
    opens = [ln for ln in inspect.getsource(bs.TapeWriter).splitlines()
             if "open(" in ln]
    assert opens
    for line in opens:
        assert "self.MODE" in line or '"r"' in line
    assert bs.TapeWriter.MODE == "a"


def test_derived_view_is_a_separate_file_and_leaves_raw_alone(tmp_path):
    w = bs.TapeWriter(tmp_path)
    raw = w.write({"kind": "book", "venue": "polymarket", "market": "m",
                   "token": "A"})
    before = raw.read_text()
    out = w.write_derived("token_coverage.json", {"markets": 1})
    assert out.parent.name == "derived"
    assert raw.parent.name == "raw"
    assert raw.read_text() == before


# --------------------------------------------------------- both-token rule

def _book_row(market, token, outcome):
    row = bs.envelope(venue=bs.VENUE_POLY, market=market, token=token,
                      outcome=outcome, feed="clob_rest_book",
                      selector=bs.SELECTOR_L5_ACTIVE, kind="book")
    row["book"] = {"bids": [{"price": "0.47", "size": "100"}],
                   "asks": [{"price": "0.49", "size": "100"}]}
    return row


def test_single_token_tape_fails_the_both_token_audit():
    rows = [_book_row("btc-updown-15m-1", "TOKEN_UP", "Up") for _ in range(50)]
    audit = bs.verify_both_tokens(rows)
    assert audit["both_tokens_ok"] is False
    assert audit["defective_markets"] == ["polymarket/btc-updown-15m-1"]
    assert bs.defective_markets(rows) == ["polymarket/btc-updown-15m-1"]


def test_two_token_tape_passes_the_both_token_audit():
    rows = [_book_row("btc-updown-15m-1", "TOKEN_UP", "Up"),
            _book_row("btc-updown-15m-1", "TOKEN_DOWN", "Down")]
    audit = bs.verify_both_tokens(rows)
    assert audit["both_tokens_ok"] is True
    cov = audit["coverage"]["polymarket/btc-updown-15m-1"]
    assert cov["token_count"] == 2 and cov["book_rows"] == 2


class _FakePoly:
    """Injected venue: no network. Returns a distinct book per token."""

    book_feed = bs.PolymarketVenue.book_feed
    activity_feed = bs.PolymarketVenue.activity_feed

    def __init__(self):
        self.calls = []

    def l5_activity(self, limit=100):
        return [{"slug": "btc-updown-15m-1", "asset": "TOKEN_UP",
                 "timestamp": 1760000000, "size": 50, "price": 0.47,
                 "side": "BUY", "outcome": "Up",
                 "transactionHash": "0xabc", "conditionId": "0xcond"}]

    def book(self, token):
        self.calls.append(token)
        return {"asset_id": token,
                "bids": [{"price": "0.47", "size": "100"}],
                "asks": [{"price": "0.49", "size": "100"}]}


def _collector(tmp_path, **kw):
    kw.setdefault("max_rpm", 0)
    c = bs.BookShadowCollector(tmp_path, **kw)
    c.pacer.interval_s = 0.0
    c.pacer.base_interval_s = 0.0
    return c


def test_poll_records_both_tokens_of_every_market(tmp_path):
    c = _collector(tmp_path)
    c.poly = _FakePoly()
    c.selections = [bs.Selection(
        bs.VENUE_POLY, "btc-updown-15m-1",
        [{"token": "TOKEN_UP", "outcome": "Up", "outcome_index": 0},
         {"token": "TOKEN_DOWN", "outcome": "Down", "outcome_index": 1}],
        bs.SELECTOR_L5_ACTIVE, "fixture")]
    assert c.poll_books() == 2
    rows = c.tape.read_all()
    assert bs.verify_both_tokens(rows)["both_tokens_ok"] is True
    # the sibling identity is on each row, so a reader can tell that the
    # other token's book was supposed to exist even if it is missing
    for r in rows:
        assert r["sibling_tokens"] == ["TOKEN_UP", "TOKEN_DOWN"]
        assert r["complement_derived"] is False


def test_book_fetch_failure_is_taped_not_silent(tmp_path):
    c = _collector(tmp_path)

    class Boom(_FakePoly):
        def book(self, token):
            raise OSError("connection reset")

    c.poly = Boom()
    c.selections = [bs.Selection(
        bs.VENUE_POLY, "m", [{"token": "A", "outcome": "Up"},
                             {"token": "B", "outcome": "Down"}],
        bs.SELECTOR_L5_ACTIVE, "fixture")]
    c.poll_books()
    rows = c.tape.read_all()
    assert [r["kind"] for r in rows] == ["book_error", "book_error"]
    assert "OSError" in rows[0]["error"]
    # an error tape must NOT read as evidence of both-token coverage
    assert bs.verify_both_tokens(rows)["both_tokens_ok"] is False


def test_pmus_single_market_event_is_flagged_complement_derived():
    sel = bs.Selection(bs.VENUE_PMUS, "evt",
                       [{"token": "evt-yes", "outcome": "YES"}],
                       bs.SELECTOR_SERIES, "fixture",
                       complement_derived=True)
    assert sel.as_row()["complement_derived"] is True
    assert sel.as_row()["token_count"] == 1


# ------------------------------------------------------------- provenance

def test_every_emitted_row_carries_provenance(tmp_path):
    c = _collector(tmp_path, selectors=(bs.SELECTOR_L5_ACTIVE,))
    c.poly = _FakePoly()
    c.selections = [bs.Selection(
        bs.VENUE_POLY, "btc-updown-15m-1",
        [{"token": "TOKEN_UP", "outcome": "Up"},
         {"token": "TOKEN_DOWN", "outcome": "Down"}],
        bs.SELECTOR_L5_ACTIVE, "l5 public activity row")]
    c.poll_books()
    c._emit_activity(c.poly.l5_activity())
    c.heartbeat()
    rows = c.tape.read_all()
    assert rows
    for r in rows:
        assert bs.has_provenance(r), r
        assert r["schema_version"] == bs.SCHEMA_VERSION
        assert r["feed"]
        assert r["selector"]
    kinds = {r["kind"] for r in rows}
    assert {"book", "l5_activity", "heartbeat"} <= kinds


def test_activity_rows_are_deduped_and_keep_venue_event_time(tmp_path):
    c = _collector(tmp_path)
    c.poly = _FakePoly()
    c._emit_activity(c.poly.l5_activity())
    c._emit_activity(c.poly.l5_activity())
    acts = [r for r in c.tape.read_all() if r["kind"] == "l5_activity"]
    assert len(acts) == 1
    assert acts[0]["venue_event_time"] == 1760000000
    assert acts[0]["token"] == "TOKEN_UP"


def test_monotonic_clock_is_recorded_for_time_ordering(tmp_path):
    c = _collector(tmp_path)
    c.poly = _FakePoly()
    c.selections = [bs.Selection(
        bs.VENUE_POLY, "m", [{"token": "A", "outcome": "Up"},
                             {"token": "B", "outcome": "Down"}],
        bs.SELECTOR_L5_ACTIVE, "fixture")]
    c.poll_books()
    ns = [r["recv_monotonic_ns"] for r in c.tape.read_all()]
    assert len(ns) == 2 and ns[0] <= ns[1]
    assert all(isinstance(v, int) for v in ns)


def test_heartbeat_shows_rising_per_stream_counts(tmp_path):
    c = _collector(tmp_path)
    c.poly = _FakePoly()
    c.selections = [bs.Selection(
        bs.VENUE_POLY, "m", [{"token": "A", "outcome": "Up"},
                             {"token": "B", "outcome": "Down"}],
        bs.SELECTOR_L5_ACTIVE, "fixture")]
    c.poll_books()
    hb1 = c.heartbeat()
    c.poll_books()
    hb2 = c.heartbeat()
    feed = bs.PolymarketVenue.book_feed
    assert hb2["rows_per_stream"][feed] > hb1["rows_per_stream"][feed]
    assert hb2["tokens_tracked"] == 2
    assert hb2["single_token_markets"] == []
    assert hb2["orders_submitted"] == 0


def test_discovery_failure_keeps_the_previous_slate(tmp_path):
    c = _collector(tmp_path)
    c.poly = _FakePoly()
    c.selections = [bs.Selection(
        bs.VENUE_POLY, "m", [{"token": "A", "outcome": "Up"},
                             {"token": "B", "outcome": "Down"}],
        bs.SELECTOR_L5_ACTIVE, "fixture")]

    def boom(*_a, **_k):
        raise OSError("discovery down")

    c.poly.l5_activity = boom
    kept = c.discover()
    assert [s.market for s in kept] == ["m"]
    row = [r for r in c.tape.read_all() if r["kind"] == "discovery"][-1]
    assert row["kept_previous_slate"] is True
    assert row["errors"] and row["errors"][0].startswith("l5_active:")


def test_run_survives_a_raising_cycle_and_tapes_it(tmp_path):
    c = _collector(tmp_path, discovery_interval_s=1e9, heartbeat_s=1e9)
    c.poly = _FakePoly()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return 0

    c.poll_books = flaky
    c.run(minutes=0.0001, sleep=lambda _s: None)
    kinds = [r["kind"] for r in c.tape.read_all()]
    assert "cycle_error" in kinds
    assert calls["n"] >= 2        # it did not exit on the transient error


# ------------------------------------------------------------------ pacing

def test_pacer_respects_its_configured_interval():
    slept = []
    clock = {"t": 0.0}

    def sleep(s):
        slept.append(s)
        clock["t"] += s

    p = bs.Pacer(max_rpm=60.0, sleep=sleep, clock=lambda: clock["t"])
    assert p.interval_s == pytest.approx(1.0)
    p.wait()                       # first request is free
    p.wait()
    p.wait()
    assert slept == [pytest.approx(1.0), pytest.approx(1.0)]
    assert p.worst_case_rpm() == pytest.approx(60.0)


def test_pacer_does_not_sleep_when_caller_was_already_slow():
    clock = {"t": 0.0}
    slept = []
    p = bs.Pacer(max_rpm=60.0, sleep=lambda s: slept.append(s),
                 clock=lambda: clock["t"])
    p.wait()
    clock["t"] = 5.0
    p.wait()
    assert slept == []


def test_pacer_backs_off_on_429_and_relaxes_back():
    p = bs.Pacer(max_rpm=240.0, sleep=lambda _s: None)
    base = p.interval_s
    assert p.penalize() == pytest.approx(base * 2)
    assert p.penalties == 1
    for _ in range(200):
        p.relax()
    assert p.interval_s == pytest.approx(base)


def test_fetcher_penalizes_the_shared_pacer_on_http_429():
    import urllib.error

    p = bs.Pacer(max_rpm=240.0, sleep=lambda _s: None)

    def opener(req, **_kw):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                     {}, None)

    f = bs.ReadOnlyFetcher(p, opener=opener)
    with pytest.raises(urllib.error.HTTPError):
        f.get("https://example.invalid/x")
    assert f.rate_limited == 1
    assert p.penalties == 1


def test_fetcher_is_get_only():
    import inspect
    src = inspect.getsource(bs.ReadOnlyFetcher)
    assert 'method="GET"' in src
    code = [ln for ln in src.splitlines() if "method=" in ln
            or "urlopen" in ln]
    for line in code:
        for verb in ("POST", "PUT", "DELETE", "PATCH"):
            assert verb not in line
    assert not hasattr(bs.ReadOnlyFetcher, "post")


def test_worst_case_rate_is_a_constant_ceiling(tmp_path):
    c = _collector(tmp_path)
    c.pacer = bs.Pacer(max_rpm=240.0, sleep=lambda _s: None)
    c.selections = [
        bs.Selection(bs.VENUE_POLY, "m%d" % i,
                     [{"token": "A%d" % i, "outcome": "Up"},
                      {"token": "B%d" % i, "outcome": "Down"}],
                     bs.SELECTOR_SERIES, "fixture")
        for i in range(40)]
    # the schedule asks for far more than the ceiling; the ceiling holds.
    assert c.scheduled_rpm() > 240.0
    assert c.pacer.worst_case_rpm() == pytest.approx(240.0)


def test_outbound_ws_frames_are_guarded():
    bs.guard_outbound({"type": "subscribe", "assets_ids": ["A", "B"]})
    bs.guard_outbound({"type": "unsubscribe"})
    with pytest.raises(PermissionError):
        bs.guard_outbound({"type": "order"})
    with pytest.raises(PermissionError):
        bs.guard_outbound({"order": {"price": "0.5"}})


# ----------------------------------------------------------- credentials

SECRET = "SUPER-SECRET-VALUE-9f8e7d6c"


def test_credential_value_never_reaches_a_log_line_a_row_or_the_gate(
        tmp_path, monkeypatch):
    for name in bs.CREDENTIAL_ENVS:
        monkeypatch.setenv(name, SECRET)
    presence = bs.credential_presence()
    assert set(presence.values()) == {True}
    assert SECRET not in json.dumps(presence)

    gate = bs.boot_gate(["src.definitely_not_a_module_zzz"])
    assert SECRET not in json.dumps(gate)

    logged: list = []
    c = bs.BookShadowCollector(tmp_path, max_rpm=0, log=logged.append)
    c.pacer.interval_s = 0.0
    c.poly = _FakePoly()
    c.selections = [bs.Selection(
        bs.VENUE_POLY, "m", [{"token": "A", "outcome": "Up"},
                             {"token": "B", "outcome": "Down"}],
        bs.SELECTOR_L5_ACTIVE, "fixture")]
    c.poll_books()
    c.discover()
    c.heartbeat()
    assert logged
    assert SECRET not in "\n".join(logged)
    tape = "\n".join(json.dumps(r, sort_keys=True) for r in c.tape.read_all())
    assert SECRET not in tape
    hb = [r for r in c.tape.read_all() if r["kind"] == "heartbeat"][-1]
    assert hb["credentials_present"]["PMUS_API_KEY_ID"] is True


def test_module_source_never_reads_a_credential_value():
    import inspect
    src = inspect.getsource(bs)
    # presence only: the credential envs are consulted through
    # credential_presence(), which coerces to bool at the point of read.
    assert "bool(os.environ.get(name))" in src
    assert "os.environ[\"PMUS_API_SECRET_KEY\"]" not in src


def test_no_trading_claim_is_in_the_docstring():
    doc = bs.__doc__ or ""
    assert "NO TRADING, NOW OR LATER" in doc
    assert "OBSERVATION ARTIFACT" in doc.upper()
    assert "WORST-CASE REQUEST RATE" in doc


# -------------------------------------------------------------- selection

def test_selector_and_reason_land_on_every_book_row(tmp_path):
    c = _collector(tmp_path)
    c.poly = _FakePoly()
    c.selections = [bs.Selection(
        bs.VENUE_PMUS, "aec-itfwo-x-y",
        [{"token": "aec-itfwo-x-y-a", "outcome": "A"},
         {"token": "aec-itfwo-x-y-b", "outcome": "B"}],
        bs.SELECTOR_SERIES, "pmus live event in configured category 'tennis'")]
    c.pmus = type("P", (), {"book": staticmethod(lambda s: {"bids": []})})()
    c.poll_books()
    rows = [r for r in c.tape.read_all() if r["kind"] == "book"]
    assert rows and all(r["selector"] == bs.SELECTOR_SERIES for r in rows)
    assert all("tennis" in r["selector_why"] for r in rows)


def test_l5_active_selection_resolves_both_tokens_from_clob_token_ids():
    class Poly(_FakePoly):
        def market_by_slug(self, slug):
            return {"slug": slug,
                    "clobTokenIds": json.dumps(["TOKEN_UP", "TOKEN_DOWN"]),
                    "outcomes": json.dumps(["Up", "Down"])}

    sels, actions = bs.select_l5_active(Poly())
    assert len(sels) == 1
    sel = sels[0]
    assert [t["token"] for t in sel.tokens] == ["TOKEN_UP", "TOKEN_DOWN"]
    assert [t["outcome"] for t in sel.tokens] == ["Up", "Down"]
    assert sel.selector == bs.SELECTOR_L5_ACTIVE
    assert "l5 public activity" in sel.why
    assert sel.complement_derived is False
    assert actions


def test_series_selector_defaults_to_tennis_and_soccer():
    assert bs.DEFAULT_SERIES == ("tennis", "soccer")


def test_series_selection_on_pmus_records_both_outcome_markets():
    class Pmus:
        @staticmethod
        def live_events(name, limit=200):
            return [{"slug": "evt-%s" % name,
                     "markets": [{"slug": "evt-a", "outcome": "A"},
                                 {"slug": "evt-b", "outcome": "B"}]}]

    sels = bs.select_series(None, Pmus(), ("tennis",), max_markets=8)
    assert len(sels) == 1
    assert [t["token"] for t in sels[0].tokens] == ["evt-a", "evt-b"]
    assert sels[0].complement_derived is False
    assert "tennis" in sels[0].why


def test_poly_tokens_never_pads_a_missing_second_token():
    toks = bs.poly_tokens({"clobTokenIds": json.dumps(["ONLY"]),
                           "outcomes": json.dumps(["Up"])})
    assert len(toks) == 1 and toks[0]["token"] == "ONLY"


def test_from_env_reads_every_documented_name(monkeypatch, tmp_path):
    monkeypatch.setenv(bs.ENV_ROOT, str(tmp_path))
    monkeypatch.setenv(bs.ENV_VENUES, "polymarket,pmus")
    monkeypatch.setenv(bs.ENV_SELECTORS, "l5_active,series")
    monkeypatch.setenv(bs.ENV_SERIES, "tennis,soccer")
    monkeypatch.setenv(bs.ENV_POLL_S, "2.5")
    monkeypatch.setenv(bs.ENV_ACTIVITY_S, "3")
    monkeypatch.setenv(bs.ENV_DISCOVERY_S, "90")
    monkeypatch.setenv(bs.ENV_HEARTBEAT_S, "30")
    monkeypatch.setenv(bs.ENV_MAX_MARKETS, "12")
    monkeypatch.setenv(bs.ENV_MAX_RPM, "120")
    monkeypatch.setenv(bs.ENV_L5_ADDRESS, "l5zn1bwom8etsk")
    c = bs.from_env()
    assert c.venues == ("polymarket", "pmus")
    assert c.selectors == ("l5_active", "series")
    assert c.series == ("tennis", "soccer")
    assert (c.poll_interval_s, c.activity_interval_s) == (2.5, 3.0)
    assert (c.discovery_interval_s, c.heartbeat_s) == (90.0, 30.0)
    assert c.max_markets == 12
    assert c.pacer.worst_case_rpm() == pytest.approx(120.0)
    assert c.poly is not None and c.pmus is not None


# ---------------- activity-failure observability and the BOOK_ONLY window

def test_describe_http_failure_exposes_status_endpoint_and_body():
    import io
    import urllib.error
    exc = urllib.error.HTTPError(
        "https://data-api.polymarket.com/activity?user=abc&limit=100",
        404, "Not Found",
        {"Content-Type": "application/json", "Set-Cookie": "s=secret"},
        io.BytesIO(b'{"error":"user not found"}'))
    d = bs.describe_http_failure(exc)
    assert d["status"] == 404
    assert d["error_class"] == "HTTPError"
    assert "user not found" in d["body"]
    # endpoint keeps the path and the PARAM NAMES, never the values
    assert d["endpoint"].startswith(
        "https://data-api.polymarket.com/activity?")
    assert "limit=<v>" in d["endpoint"] and "user=<v>" in d["endpoint"]
    assert "abc" not in d["endpoint"]
    # headers are allowlisted, so a cookie cannot ride along
    assert "set-cookie" not in d["headers_safe"]
    assert d["headers_safe"]["content-type"] == "application/json"


def test_describe_http_failure_handles_a_non_http_error():
    d = bs.describe_http_failure(OSError("tunnel refused"))
    assert d["error_class"] == "OSError"
    assert d["status"] is None and d["body"] is None


def test_a_book_only_window_is_marked_at_both_edges(tmp_path):
    col = _collector(tmp_path)
    col._activity_down_since = None
    col._emit_activity_window("OPEN")
    col._activity_down_since = 1_700_000_000.0
    col._emit_activity_window("CLOSE")
    rows = [r for r in col.tape.read_all()
            if r.get("kind") == "activity_availability"]
    assert [r["edge"] for r in rows] == ["OPEN", "CLOSE"]
    assert rows[0]["state"] == "BOOK_ONLY_ACTIVITY_UNAVAILABLE"
    assert rows[1]["state"] == "ACTIVITY_RECOVERED"
    # the whole point: the gap may never read as a zero-fill observation
    for r in rows:
        assert "NOT a zero-fill observation" in r["MEANING"]


def test_the_boot_inventory_is_read_only_and_sees_prior_parts(tmp_path):
    first = _collector(tmp_path)
    first.emit(bs.envelope(venue="polymarket", market="m", token="t",
                           outcome="up", feed="clob_rest_book",
                           selector="series", kind="book"))
    before = sorted(p.name for p in (tmp_path / "raw").glob("*.jsonl"))
    bytes_before = sum((tmp_path / "raw" / n).stat().st_size for n in before)
    assert before, "a part must exist before the simulated restart"

    second = _collector(tmp_path)          # the "restart"
    inv = second.emit_boot_inventory()
    assert inv["raw_parts_present_at_boot"] == before
    assert inv["raw_bytes_at_boot"] == bytes_before
    # read-only: the inventory must not add, remove or shrink anything
    after = sorted(p.name for p in (tmp_path / "raw").glob("*.jsonl"))
    assert after == before
    assert sum((tmp_path / "raw" / n).stat().st_size
               for n in after) == bytes_before
