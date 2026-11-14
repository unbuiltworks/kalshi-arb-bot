"""2026-11-14 sports-live changes: lane isolation, probe-local gateway
candidate feed, optional maker depth. Each is default-safe."""
from __future__ import annotations

import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import pmus_spring_l5_session as spring
from src import pmus_l5_continuous_session as camp
from src import pmus_micro_executor as micro
from src import pmus_spring_gateway_candidates as gw
from src import pmus_spring_l5_probe_entry as entry

q1 = micro.q1
ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_spring_session_fixtures", ROOT / "tests" / "test_pmus_spring_l5_session.py")
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

OWN = "aec-atp-own-mkt-2026-11-14"
BTC = "cpc-btc-updown-5m-2026-11-14-1415z"
FRITZ = "aec-atp-tayfri-xxxyyy-2026-11-14"


class Venue:
    def __init__(self, positions=None, orders=None):
        self.positions = positions or {}
        self.orders = orders or []

    def request(self, method, path, body=None, base=None):
        if path == "/v1/orders/open":
            return {"orders": list(self.orders)}
        if path == "/v1/portfolio/positions":
            return {"positions": {s: {"netPosition": v}
                                  for s, v in self.positions.items()}}
        raise AssertionError(path)


def _ledger(tmp_path, monkeypatch, rows):
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    with (tmp_path / "ledger.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setenv(spring.LANE_ISOLATION_ENV, "1")
    spring._LANE_LAST.clear()
    _ledger(tmp_path, monkeypatch, [
        {"kind": "submitted", "stage": "spring_open_1", "slug": OWN,
         "order_id": "OWN1"}])
    return tmp_path


# ---------------------------------------------------------- 1. isolation

def test_image_turns_lane_isolation_on_and_code_default_is_off(monkeypatch):
    df = (ROOT / "deploy/pmus_spring_l5/Dockerfile").read_text()
    assert "    PMUS_SPRING_LANE_ISOLATION=1 \\\n" in df
    monkeypatch.delenv(spring.LANE_ISOLATION_ENV, raising=False)
    assert spring.lane_isolation_enabled() is False


def test_foreign_btc5m_position_and_orders_are_not_resumed(iso):
    v = Venue(positions={BTC: "5"},
              orders=[{"id": "B1", "marketSlug": BTC},
                      {"id": "B2", "marketSlug": BTC}])
    assert camp.held_market(v) is None
    assert camp.held_markets(v) == []
    assert spring.lane_account_block_reason(v) is None
    assert spring.foreign_exposure(v) == {BTC: ["open_orders", "position"]}


def test_off_keeps_the_account_wide_lock(monkeypatch, tmp_path):
    monkeypatch.delenv(spring.LANE_ISOLATION_ENV, raising=False)
    _ledger(tmp_path, monkeypatch, [])
    v = Venue(positions={BTC: "5"})
    assert camp.held_market(v) == BTC                 # the old behaviour
    v = Venue(orders=[{"id": "B1", "marketSlug": BTC}])
    assert spring.lane_account_block_reason(v) == "open_orders:1"


def test_own_ledger_market_is_resumed_as_before(iso):
    v = Venue(positions={OWN: "0.01", BTC: "5"},
              orders=[{"id": "B1", "marketSlug": BTC}])
    assert camp.held_market(v) == OWN
    assert camp.held_markets(v) == [OWN]
    # resume preflight on the own market: foreign lanes do not block it
    assert spring.resume_block_reason(v, OWN) is None
    # an own resting order (by id) still blocks, fail closed
    v.orders.append({"id": "OWN1", "marketSlug": OWN})
    assert spring.resume_block_reason(v, OWN) == "open_orders:1"


def test_owner_manual_position_is_not_selected_nor_resumed(iso):
    v = Venue(positions={FRITZ: "3"})
    assert camp.held_markets(v) == []
    # preflight on the target market refuses
    assert spring.lane_account_block_reason(v, target=FRITZ) == \
        f"foreign_position:{FRITZ}"
    v2 = Venue(orders=[{"id": "M1", "marketSlug": FRITZ}])
    assert spring.lane_account_block_reason(v2, target=FRITZ) == \
        f"foreign_open_orders:{FRITZ}"
    # selection refuses it with a named reason
    status = {"t": 0, "spring_watch": [{"slug": FRITZ, "bid": "0.5"}]}
    rk = camp.rank_candidates(status, 0.0, None,
                              foreign=spring.foreign_exposure(v))
    assert rk["admitted"] == []
    assert rk["refused"][0]["reason"].startswith("foreign_account_exposure")


def test_boot_assertions_ignore_foreign_orders_under_isolation(iso):
    v = Venue(positions={BTC: "5"},
              orders=[{"id": "B1", "marketSlug": BTC}])
    sweep = {"seen": 1, "cancelled": [], "foreign": [
        {"order_id": "B1", "slug": BTC}], "errors": []}
    boot = camp.boot_assertions(v, sweep)
    by = {c["check"]: c for c in boot["checks"]}
    assert by["orphan_sweep_clean"]["ok"]
    assert by["no_open_orders_fresh_read"]["ok"]
    assert by["flat_held_state"]["ok"]
    assert camp.foreign_orders_stop(sweep) is False


def test_foreign_orders_still_stop_without_isolation(monkeypatch):
    monkeypatch.delenv(spring.LANE_ISOLATION_ENV, raising=False)
    assert camp.foreign_orders_stop({"foreign": [{"order_id": "x"}]})


# ---------------------------------------------------------- 2. gateway feed

LIVE = "aec-atp-aaa-bbb-2026-11-14"


class Gateway:
    """Fake public gateway: one live tennis event, a moving tape."""

    def __init__(self):
        self.calls = []
        self.vol = 100
        self.flip = False

    def __call__(self, path):
        self.calls.append(path)
        if path.startswith("/v1/events"):
            return {"events": [{
                "slug": "aec-atp-aaa-bbb", "live": True, "ended": False,
                "markets": [{"slug": LIVE,
                             "bestBidQuote": {"value": "0.48"}},
                            {"slug": "aec-atp-far-out",
                             "bestBidQuote": {"value": "0.97"}}]}]}
        if path.endswith("/bbo"):
            self.vol += 10
            self.flip = not self.flip
            # alternate prints at the ask and at the bid: two-sided flow
            last = "0.5000" if self.flip else "0.4800"
            return {"marketData": {
                "bestBid": {"value": "0.4800"}, "bestAsk": {"value": "0.5000"},
                "lastTradePx": {"value": last}, "sharesTraded": self.vol}}
        if path.endswith("/book"):
            return {"marketData": {"bids": [{"px": "0.48", "qty": "50"}],
                                   "offers": [{"px": "0.50", "qty": "40"}]}}
        if path.startswith("/v1/market/slug/"):
            return {"market": {"active": True, "archived": False,
                               "closed": False,
                               "status": "MARKET_STATUS_OPEN",
                               "endDate": "2099-01-01T00:00:00Z"}}
        raise AssertionError(path)


def _feed(monkeypatch, n=14):
    gw.reset()
    monkeypatch.setenv(gw.CANDIDATES_ENV, "gateway")
    monkeypatch.setattr(gw, "_probe_entry_action", lambda b, a: {
        "decision": "quote", "relation": "open", "side": "YES",
        "px": str(b)})
    g = Gateway()
    slept = []
    t0 = 1_800_000_000.0
    doc = None
    for i in range(n):
        doc = gw.build_status(now=t0 + 30 * i, get=g, sleep=slept.append,
                              clock=lambda: 0.0)
    return g, doc, t0 + 30 * (n - 1), slept


def test_gateway_default_off_is_unchanged(monkeypatch):
    monkeypatch.delenv(gw.CANDIDATES_ENV, raising=False)
    monkeypatch.delenv(entry.EVIDENCE_STATUS_URL_ENV, raising=False)
    monkeypatch.delenv(entry.EVIDENCE_STATUS_PATH_ENV, raising=False)
    assert entry.load_status_document() == (None, "unset")


def test_gateway_rows_are_recorder_shaped_and_rate_bounded(monkeypatch):
    g, doc, now, slept = _feed(monkeypatch)
    assert doc["evidence_provenance"] == "inferred"
    slugs = [r["slug"] for r in doc["spring_watch"]]
    assert slugs == [LIVE]                      # 0.97 screened out
    row = doc["spring_watch"][0]
    assert row["both_sides_served"] is True
    assert row["market_state"]["status"] == "MARKET_STATUS_OPEN"
    assert row["two_sided_trades"] is True and row["trades_1h"] >= 10
    # <= 2 rps: every request after the first waited >= 0.5 s
    assert slept and min(slept) >= 0.5 - 1e-9
    assert doc["gateway_diag"]["requests"] <= gw.DEFAULT_MAX_REQ


def test_gateway_rows_admitted_only_with_explicit_provenance_opt_in(
        monkeypatch):
    g, doc, now, _ = _feed(monkeypatch)
    monkeypatch.setattr(camp, "pmus_shadow_yield", lambda now=None: {})
    monkeypatch.delenv(gw.ACCEPT_INFERRED_ENV, raising=False)
    rk = camp.rank_candidates(doc, now, None)
    assert rk["admitted"] == []
    assert rk["refused"][0]["reason"] == \
        "strict_evidence_provenance_not_authenticated:inferred"
    monkeypatch.setenv(gw.ACCEPT_INFERRED_ENV, "1")
    rk = camp.rank_candidates(doc, now, None)
    assert [x["slug"] for x in rk["admitted"]] == [LIVE], rk["refused"]


def test_load_status_document_serves_gateway_when_switched(monkeypatch):
    _feed(monkeypatch, n=1)
    monkeypatch.setattr(gw, "build_status", lambda: {"t": 1,
                                                     "spring_watch": []})
    status, source = entry.load_status_document()
    assert source == "gateway:public" and status["spring_watch"] == []


def test_image_copies_gateway_modules():
    df = (ROOT / "deploy/pmus_spring_l5/Dockerfile").read_text()
    m = "pmus_spring_gateway_candidates"
    assert f"COPY src/{m}.py src/{m}.py" in df
    assert "COPY src/pmus_sports_collector.py" not in df


# ---------------------------------------------------------- 3. maker depth

def _first_open_px(monkeypatch, tmp_path, depth):
    fx._arm(monkeypatch, tmp_path)
    fx._script_proposals(monkeypatch, ["open", "complete"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")
    if depth is None:
        monkeypatch.delenv(spring.DEPTH_TICKS_ENV, raising=False)
    else:
        monkeypatch.setenv(spring.DEPTH_TICKS_ENV, str(depth))
    fake = fx.Fake(bid="0.4800", ask="0.5000")
    rep = spring.run_session(fake, clock=fx._ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    creates = [b for p, b in fake.posts if p == "/v1/orders"]
    assert creates, rep
    return D(str(creates[0]["price"]["value"])), creates[0], rep


def test_depth_default_zero_joins_the_touch(monkeypatch, tmp_path):
    px, body, rep = _first_open_px(monkeypatch, tmp_path, None)
    assert px == D("0.48")
    assert all("maker_depth" not in d for d in rep.get("decisions") or [])


def test_depth_two_rests_two_ticks_under_the_bid(monkeypatch, tmp_path):
    px, body, rep = _first_open_px(monkeypatch, tmp_path, 2)
    assert px == D("0.48") - 2 * D("0.005")
    md = [d["maker_depth"] for d in rep["decisions"] if "maker_depth" in d]
    assert md and md[0]["ticks"] == 2


def test_depth_never_applies_to_complete_and_floors_at_a_tick(monkeypatch):
    monkeypatch.setenv(spring.DEPTH_TICKS_ENV, "2")
    tick = D("0.01")
    assert spring.depth_rest_px(D("0.50"), tick, "complete") == D("0.50")
    assert spring.depth_rest_px(D("0.50"), tick, "extend") == D("0.48")
    assert spring.depth_rest_px(D("0.01"), tick, "open") == D("0.01")
    monkeypatch.setenv(spring.DEPTH_TICKS_ENV, "garbage")
    assert spring.depth_rest_px(D("0.50"), tick, "open") == D("0.50")
