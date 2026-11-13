"""Offline tests for the THEORETICAL PMUS liquidity-incentive recorder.

No network: every venue read is a fake. Covers the scoring arithmetic against
the venue doc's worked example, the Target Size walk, maxSpread, the touch
fill rule, markout sign, settlement capture, fail-soft wiring into the live
collector loop, and the collector image's import closure.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import pmus_incentive_recorder as pir
from src import pmus_sports_collector as col
from src import pmus_sports_live_collector as live

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/pmus_sports_collector/Dockerfile"

SLUG = "aec-atp-aaa-bbb-2026-11-13"
SIB = "tsc-atp-aaa-bbb-2026-11-13-set1"
EVENT = "atp-aaa-bbb-2026-11-13"


def lv(*pairs):
    return [{"px": {"value": "%.4f" % p}, "qty": str(q)} for p, q in pairs]


# ------------------------------------------------------------ scoring ---

def test_docs_worked_example_df_030():
    levels = [(0.50, 1000), (0.49, 1000), (0.48, 1000), (0.47, 1000)]
    scored = pir.score_levels(levels, 0.01, 0.30)
    assert [round(s, 6) for *_, s in scored] == [1000, 300, 90, 27]
    total = sum(s for *_, s in scored)
    assert total == pytest.approx(1417)
    assert 1000 / total == pytest.approx(0.706, abs=5e-4)
    assert 27 / total == pytest.approx(0.019, abs=5e-4)
    # the same book through the target walk (target met by all four levels)
    w = pir.walk_side(levels, 0.01, 0.30, target=4000, our_qty=0)
    assert w["qualifies"] and w["others_score"] == pytest.approx(1417)
    # our Q joined at the back of the best level scores DF^0 * Q
    w = pir.walk_side(levels, 0.01, 0.30, target=4000, our_qty=10)
    assert w["share"] == pytest.approx(10 / 1427)


def test_target_walk_excludes_orders_beyond_target():
    bids = [(0.50, 600), (0.49, 600), (0.48, 5000)]
    w = pir.walk_side(bids, 0.01, 0.30, target=1000, our_qty=10)
    # 10 + 600 < 1000 -> include 0.49 (cum 1210 >= 1000) -> stop before 0.48
    assert w["levels_in_range"] == 2
    assert w["others_score"] == pytest.approx(600 + 0.3 * 600)
    assert w["share"] == pytest.approx(10 / (10 + 780))


def test_target_reached_at_best_level_zeroes_the_next_level():
    """Docs: Target 20,000 with 25,000 at best -> second-best scores 0."""
    w = pir.walk_side([(0.50, 25000), (0.49, 100000)], 0.01, 0.3,
                      target=20000, our_qty=0)
    assert w["levels_in_range"] == 1
    assert w["others_score"] == pytest.approx(25000)


def test_target_not_met_scores_zero():
    w = pir.walk_side([(0.50, 100), (0.49, 100)], 0.01, 0.3,
                      target=5000, our_qty=10)
    assert not w["qualifies"] and w["share"] == 0.0


def test_max_spread_disqualifies_both_sides():
    bids = [(0.50, 10), (0.40, 5000)]           # size-adjusted ~0.40
    asks = [(0.52, 6000)]
    plain = pir.quote_shares(bids, asks, 0.01, 0.3, 1000, 10, None)
    assert plain["bid"]["share"] > 0 and plain["ask"]["share"] > 0
    gated = pir.quote_shares(bids, asks, 0.01, 0.3, 1000, 10, 0.03)
    assert gated["max_spread_ok"] is False
    assert gated["bid"]["share"] == 0 and gated["ask"]["share"] == 0
    # a tight two-sided book inside maxSpread keeps both sides paid
    ok = pir.quote_shares([(0.50, 2000)], [(0.52, 2000)], 0.01, 0.3,
                          1000, 10, 0.03)
    assert ok["max_spread_ok"] is True
    assert ok["bid"]["share"] == pytest.approx(10 / 2010)
    # one empty side -> no mid -> neither side under maxSpread
    none = pir.quote_shares([(0.50, 2000)], [], 0.01, 0.3, 1000, 10, 0.03)
    assert none["bid"]["share"] == 0 and none["max_spread_ok"] is False


def test_book_parsing_offers_authoritative_and_tick_inference():
    bids, asks = pir.book_sides({"bids": lv((0.48, 5), (0.50, 7)),
                                 "offers": lv((0.53, 2), (0.52, 3)),
                                 "asks": lv((0.10, 1))})
    assert bids[0] == (0.50, 7) and asks[0] == (0.52, 3) and len(asks) == 2
    assert pir.infer_tick({}, bids, asks) == (0.01, "inferred_from_levels")
    assert pir.infer_tick({}, [(0.505, 1)], [])[0] == 0.005
    assert pir.infer_tick({"orderPriceMinTickSize": "0.001"}, bids)[0] \
        == 0.001


# -------------------------------------------------------- fills/markout ---

def test_fill_rule_bid():
    f = pir.detect_fill
    assert f("bid", 0.50, 0.49, 0.49, None, False) == "trade_through"
    assert f("bid", 0.50, 0.50, 0.50, None, False) is None   # locked: queue
    assert f("bid", 0.50, 0.50, 0.52, 0.50, True) is None    # still at touch
    assert f("bid", 0.50, 0.49, 0.51, 0.50, True) == \
        "level_vanished_with_trade"
    assert f("bid", 0.50, 0.49, 0.51, 0.50, False) is None   # cancel, no trade
    assert f("bid", 0.50, 0.49, 0.51, 0.51, True) is None    # traded above us
    assert f("bid", 0.50, 0.51, 0.52, 0.51, True) is None    # touch moved up


def test_fill_rule_ask():
    f = pir.detect_fill
    assert f("ask", 0.52, 0.53, 0.53, None, False) == "trade_through"
    assert f("ask", 0.52, 0.52, 0.52, None, False) is None
    assert f("ask", 0.52, 0.50, 0.53, 0.52, True) == \
        "level_vanished_with_trade"
    assert f("ask", 0.52, 0.50, None, 0.53, True) == \
        "level_vanished_with_trade"
    assert f("ask", 0.52, 0.50, 0.53, 0.51, True) is None


def test_markout_sign():
    assert pir.markout("bid", 0.50, 0.45) == pytest.approx(-0.05)  # adverse
    assert pir.markout("bid", 0.50, 0.55) == pytest.approx(0.05)
    assert pir.markout("ask", 0.50, 0.55) == pytest.approx(-0.05)  # adverse
    assert pir.markout("ask", 0.50, 0.45) == pytest.approx(0.05)


# ------------------------------------------------------ recorder (e2e) ---

def _programs_payload(slugs):
    progs = []
    for s in slugs:
        progs.append({"marketSlug": s, "instrumentState":
                      "INSTRUMENT_STATE_OPEN", "eventStartTime": "",
                      "timePeriods": [{
                          "programId": "atp_live_x", "programType":
                          "liquidityProgram", "start": "2026-11-01T00:00:00Z",
                          "rewardPool": 120, "status": "active",
                          "discountFactor": 0.3, "targetSize": 1000,
                          "period": "live"}]})
    return {"programs": progs}


class FakeGateway:
    def __init__(self):
        self.paths = []
        self.bbo = {}

    def __call__(self, path):
        self.paths.append(path)
        if path.startswith("/v1/incentives?"):
            slugs = re.findall(r"symbols=([^&]+)", path)
            return _programs_payload([s for s in slugs if s in (SLUG, SIB)])
        if path.startswith("/v1/markets/") and path.endswith("/bbo"):
            return {"marketData": self.bbo.get(path.split("/")[3], {})}
        raise AssertionError("unexpected path " + path)


def _target(slug=SLUG):
    return {"slug": slug, "event_slug": EVENT, "why": "v1_live_event_market",
            "market_meta": {}}


def _rec(tmp_path, gw, printed=None):
    T0 = datetime(2026, 11, 13, 12, tzinfo=timezone.utc).timestamp()
    rec = pir.IncentiveRecorder(
        tmp_path, get_json=gw, clock=lambda: T0, sleep=lambda s: None,
        printer=(lambda *a, **k: printed.append(a[0])) if printed is not None
        else (lambda *a, **k: None))
    return rec, T0


def _rows(tmp_path):
    out = []
    for p in sorted((tmp_path / "incentive").glob("pmus_incentive_*.jsonl")):
        out += [json.loads(x) for x in p.read_text().splitlines()]
    return out


def test_program_map_uses_symbol_batches_and_counts_event_markets(tmp_path):
    gw = FakeGateway()
    rec, T0 = _rec(tmp_path, gw)
    universe = [{"slug": SLUG, "event_slug": EVENT},
                {"slug": SIB, "event_slug": EVENT},
                {"slug": "other", "event_slug": "ev2"}]
    rec.refresh_map([_target()], universe, T0)
    inc = [p for p in gw.paths if p.startswith("/v1/incentives?")]
    assert inc and all("pageToken" not in p for p in inc)
    assert any("symbols=" + SIB in p for p in inc)       # sibling queried
    per = rec.map[SLUG]["periods"][0]
    assert per["M_event"] == 2 and per["M_program"] == 2
    assert SIB not in rec.map                           # not polled
    kinds = [r["kind"] for r in _rows(tmp_path)]
    assert kinds == ["program_map"]
    assert all(r["label"] == "THEORETICAL_NOT_REALIZED"
               for r in _rows(tmp_path))


def test_fetch_programs_splits_capped_batches():
    calls = []

    def gw(path):
        slugs = re.findall(r"symbols=([^&]+)", path)
        calls.append(len(slugs))
        n = 100 if len(slugs) > 1 else 1
        return {"programs": [{"marketSlug": slugs[i % len(slugs)] + str(i)}
                             for i in range(n)]}
    found, diag = pir.fetch_programs(gw, ["a", "b", "c", "d"], batch=4,
                                     sleep=lambda s: None)
    assert diag["split"] >= 1 and calls[0] == 4 and 1 in calls


def _bbo(ltp=None, cum=None, state="MARKET_STATE_OPEN", **kw):
    d = {"state": state, "sharesTraded": cum}
    if ltp is not None:
        d["lastTradePx"] = {"value": str(ltp)}
    d.update(kw)
    return d


def test_end_to_end_share_fill_markout_status(tmp_path):
    gw = FakeGateway()
    printed = []
    rec, T0 = _rec(tmp_path, gw, printed)
    rec.share_row_s = 0.0
    rec.refresh_map([_target()], [{"slug": SLUG, "event_slug": EVENT},
                                  {"slug": SIB, "event_slug": EVENT}], T0)
    book = {"bids": lv((0.50, 990), (0.49, 1000)),
            "offers": lv((0.52, 990), (0.53, 1000))}
    rec.observe(SLUG, _target(), _bbo(0.51, 100), book, T0)
    rec.observe(SLUG, _target(), _bbo(0.51, 100), book, T0 + 10)
    # offers trade through our 0.50 bid -> bid fill
    through = {"bids": lv((0.48, 2000)), "offers": lv((0.49, 2000))}
    rec.observe(SLUG, _target(), _bbo(0.49, 300), through, T0 + 20)
    for dt, mid_bid in ((50, 0.46), (140, 0.44), (620, 0.40)):
        b = {"bids": lv((mid_bid, 2000)), "offers": lv((mid_bid + 0.02, 2000))}
        rec.observe(SLUG, _target(), _bbo(mid_bid, 300), b, T0 + dt)
    rows = _rows(tmp_path)
    kinds = [r["kind"] for r in rows]
    assert "incentive_share" in kinds and "touch_fill" in kinds
    fill = next(r for r in rows if r["kind"] == "touch_fill"
                and r["side"] == "bid")
    assert fill["px"] == 0.50 and fill["rule"] == "trade_through"
    mo = next(r for r in rows if r["kind"] == "markout"
              and r["side"] == "bid")
    # mid 30s after a 0.50 bid fill was 0.47 -> -0.03 (adverse)
    assert mo["markout_30s"] == pytest.approx(-0.03)
    assert mo["markout_120s"] == pytest.approx(-0.05)
    assert mo["markout_600s"] == pytest.approx(-0.09)
    share = next(r for r in rows if r["kind"] == "incentive_share")
    q10 = share["periods"][0]["q"]["10"]
    # our 10 + 990 at best reaches target 1000 AT the best level, so the
    # 0.49 level is outside the walk: others = 990
    assert q10["bid_share"] == pytest.approx(10 / 1000, rel=1e-4)
    assert q10["bid_others_score"] == pytest.approx(990)
    pe = q10["usd_per_day"]
    assert pe["PER_MARKET"] == pytest.approx(2 * pe["PER_EVENT"], rel=1e-6)
    rec.emit_status(T0 + 700)
    line = printed[-1]
    assert line.startswith("PMUS_INCENTIVE_STATUS ")
    p = json.loads(line.split(" ", 1)[1])
    assert p["label"] == "THEORETICAL_NOT_REALIZED"
    m = p["markets"][0]
    assert m["slug"] == SLUG and len(m["q10"]) == 3 and len(m["q50"]) == 3
    assert m["fills_hr"]["bid"] > 0 and m["mo30"] == pytest.approx(-0.03)
    assert m["net_ev_q10_day"] is not None
    assert rec.errors == 0


def test_settlement_from_poll_and_from_queue(tmp_path):
    gw = FakeGateway()
    rec, T0 = _rec(tmp_path, gw)
    rec.refresh_map([_target()], [{"slug": SLUG, "event_slug": EVENT}], T0)
    book = {"bids": lv((0.50, 2000)), "offers": lv((0.52, 2000))}
    rec.observe(SLUG, _target(), _bbo(0.51, 1), book, T0)
    # market drops off the poll; after SETTLE_STALE_S it is queued and read
    gw.bbo[SLUG] = {"state": "MARKET_STATE_EXPIRED",
                    "settlementPx": {"value": "1.0000"}}
    rec.check_settlements(T0 + pir.SETTLE_STALE_S + 1)
    st = [r for r in _rows(tmp_path) if r["kind"] == "settlement"]
    assert len(st) == 1 and st[0]["settlement_px"] == 1.0 and st[0]["binary"]
    assert SLUG in rec.settled and SLUG not in rec.settle_pending
    # persisted, and not written twice
    rec2, _ = _rec(tmp_path, gw)
    assert SLUG in rec2.settled
    rec2.map = dict(rec.map)
    rec2.observe(SLUG, _target(), {"state": "MARKET_STATE_EXPIRED",
                                   "settlementPx": "1.0000"}, {}, T0 + 2000)
    assert len([r for r in _rows(tmp_path) if r["kind"] == "settlement"]) == 1


def test_recorder_public_methods_never_raise(tmp_path):
    def boom(path):
        raise RuntimeError("gateway down")
    rec, T0 = _rec(tmp_path, boom)
    rec.after_cycle([_target()], [], T0)
    rec.map = {SLUG: {"event": EVENT, "periods": [{"bad": 1}]}}
    rec.observe(SLUG, _target(), {"state": 7}, {"bids": "garbage"}, T0)
    rec.check_settlements(T0)
    assert rec.errors >= 1          # counted, not raised


# ------------------------------------------- collector loop integration ---

class _Stop(Exception):
    pass


def test_recorder_exceptions_never_stop_the_collector(tmp_path, monkeypatch):
    monkeypatch.setenv(col.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(col.STATUS_EVERY_ENV, "0")
    monkeypatch.delenv(col.ROLE_ENV, raising=False)
    monkeypatch.setenv(live.INCENTIVE_RECORDER_ENV, "1")
    ev = {"slug": EVENT, "live": True, "markets": [{"slug": SLUG}]}

    def fake_get(path):
        if path.startswith("/v1/incentives"):
            return {"programs": []}
        if path.startswith("/v1/events"):
            return {"events": [ev]}
        if path.endswith("/bbo"):
            return {"marketData": {"bestBid": {"value": "0.50"},
                                   "bestAsk": {"value": "0.52"}}}
        if path.endswith("/book"):
            return {"marketData": {"bids": lv((0.50, 5)),
                                   "offers": lv((0.52, 5))}}
        raise AssertionError(path)
    monkeypatch.setattr(col, "http_get_json", fake_get)

    def explode(self, *a, **k):
        raise RuntimeError("recorder defect")
    monkeypatch.setattr(pir.IncentiveRecorder, "observe", explode)
    monkeypatch.setattr(pir.IncentiveRecorder, "after_cycle", explode)
    calls = {"n": 0}

    def fake_sleep(s):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _Stop()
    monkeypatch.setattr(live.time, "sleep", fake_sleep)
    live._recorder_failures["count"] = 0
    with pytest.raises(_Stop):
        live.run()
    raw = [json.loads(x) for p in (tmp_path / "raw").glob("*.jsonl")
           for x in p.read_text().splitlines()]
    assert sum(1 for r in raw if r["kind"] == "bbo_book") == 2   # 2 cycles
    errs = [r for r in raw if r.get("stage") == "incentive_recorder"]
    assert errs and live._recorder_failures["count"] == 4
    assert json.loads((tmp_path / "state.json").read_text())["cycles"] == 2


def test_recorder_flag_off_and_init_failure_are_soft(tmp_path, monkeypatch):
    monkeypatch.setenv(live.INCENTIVE_RECORDER_ENV, "0")
    assert live.make_incentive_recorder(tmp_path) is None
    monkeypatch.setenv(live.INCENTIVE_RECORDER_ENV, "1")

    def bad_init(self, *a, **k):
        raise OSError("read-only fs")
    monkeypatch.setattr(pir.IncentiveRecorder, "__init__", bad_init)
    assert live.make_incentive_recorder(tmp_path) is None
    live.recorder_call(None, "observe")               # no recorder: no-op


def test_recorder_is_structurally_read_only():
    src = inspect.getsource(pir)
    hosts = set(re.findall(r"https://([a-z0-9.-]+)", src))
    assert hosts <= {"gateway.polymarket.us"}
    for forbidden in ("X-PM-Access-Key", "X-PM-Signature", "websocket",
                      "wss://", '"POST"', "'POST'", "method=", "/v1/orders",
                      "place_order", "urlopen", "data=", "api.polymarket.us"):
        assert forbidden not in src, forbidden
    # still selection-only vocabulary in the live runner
    lsrc = inspect.getsource(live)
    for banned in ("armed", "complement", "pmus_spring"):
        assert banned not in lsrc, banned


# ------------------------------------------------- image import closure ---

def _copy_specs():
    out = []
    for line in DOCKERFILE.read_text().splitlines():
        s = line.strip()
        if s.startswith("COPY "):
            parts = s.split()[1:]
            assert len(parts) == 2, s
            out.append((parts[0], parts[1]))
    return out


def _src_imports(path: Path) -> set:
    """Every `src.X` module a file imports, at any depth (lazy included)."""
    tree = ast.parse(path.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "src":
                mods |= {"src." + a.name for a in node.names}
            elif node.module.startswith("src."):
                mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods |= {a.name for a in node.names if a.name.startswith("src.")}
    return mods


#: lazily imported ONLY by an offline build tool, never on the runtime path:
#: pmus_l5_persistence_v2.build_artifact() rebuilds the v2 model JSON (which
#: IS copied) from the canonical sequence. Pre-existing; named so a new
#: runtime import cannot hide behind it.
OFFLINE_ONLY = {"src.polymarket_l5.action_model_v2"}


def _static_closure(entry: str) -> set:
    seen, todo = set(), [entry]
    while todo:
        m = todo.pop()
        if m in seen or m in OFFLINE_ONLY:
            continue
        seen.add(m)
        p = ROOT / (m.replace(".", "/") + ".py")
        if not p.exists():
            p = ROOT / m.replace(".", "/") / "__init__.py"
        if p.exists():
            todo += sorted(_src_imports(p) - seen)
    return seen


def test_dockerfile_copies_every_module_the_collector_imports():
    copied = {dst for _, dst in _copy_specs()}
    closure = _static_closure("src.pmus_sports_live_collector")
    assert "src.pmus_incentive_recorder" in closure
    missing = []
    for m in sorted(closure):
        f = m.replace(".", "/") + ".py"
        if (ROOT / f).exists() and f not in copied:
            missing.append(f)
    assert not missing, "not COPY'd into the collector image: %s" % missing


PROBE = r"""
import importlib, sys
sys.path.insert(0, ".")
for m in ("src.pmus_sports_live_collector", "src.pmus_incentive_recorder",
          "src.pmus_status_report", "src.pmus_sports_l5_decision"):
    importlib.import_module(m)
print("CLOSURE_OK")
"""


def _materialise(tree: Path, skip: str = "") -> None:
    for src, dst in _copy_specs():
        if dst == skip:
            continue
        out = tree / dst
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / src, out)
    (tree / "src" / "__init__.py").touch()


def _probe(tree: Path) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONNOUSERSITE": "1"}
    return subprocess.run([sys.executable, "-c", PROBE], cwd=tree, env=env,
                          capture_output=True, text=True, timeout=60)


def test_image_tree_imports_the_recorder_and_fails_without_it(tmp_path):
    good = tmp_path / "good"
    _materialise(good)
    r = _probe(good)
    assert r.returncode == 0 and "CLOSURE_OK" in r.stdout, r.stderr
    bad = tmp_path / "bad"
    _materialise(bad, skip="src/pmus_incentive_recorder.py")
    r = _probe(bad)
    assert r.returncode != 0 and "pmus_incentive_recorder" in r.stderr
