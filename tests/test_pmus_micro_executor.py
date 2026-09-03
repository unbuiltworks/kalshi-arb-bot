"""MICRO-LIVE decimal-sizing executor regressions (issue #167 comments
5519953967/5519959374, live shapes from 5520088122/5520140034). Nothing
here arms anything; the q=1 default path must remain untouched."""
from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import pmus_micro_executor as micro
from src import pmus_q1_executor as q1


SLUG = "aec-mlb-nyy-laa-2026-09-02"


class Fake:
    """Scripted transport; records every call. Never signs anything."""

    def __init__(self, meta=None, bbo=("0.4800", "0.4850"),
                 fills=None, reject_create=False):
        self.calls: list[tuple] = []
        self.meta = meta
        self.bbo = bbo
        self.fills = list(fills or [])
        self.reject_create = reject_create
        self.created = 0

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path, body))
        if path.startswith("/v1/market/slug/"):
            if self.meta is None:
                return {"market": {"name": "x"}}          # keys missing
            return {"market": {"minimumTradeQty": self.meta[0],
                               "orderPriceMinTickSize": self.meta[1],
                               "sportsMarketTypeV2": "MONEYLINE"}}
        if path.endswith("/bbo"):
            return {"marketData": {"bestBid": {"value": self.bbo[0]},
                                   "bestAsk": {"value": self.bbo[1]}}}
        if path == "/v1/account/balances":
            return {"balances": [{"currency": "USD",
                                  "buyingPower": {"value": "100.57"}}]}
        if path == "/v1/portfolio/positions":
            return {"positions": {}}
        if path == "/v1/orders/open":
            return {"orders": []}
        if path == "/v1/order/preview":
            return {"order": dict(body["request"])}
        if path == "/v1/orders":
            self.created += 1
            if self.reject_create:
                return {}
            return {"id": f"MICRO{self.created}"}
        if path.startswith("/v1/order/") and path.endswith("/cancel"):
            return {}
        if path.startswith("/v1/order/"):
            if self.fills:
                return self.fills.pop(0)
            return {"order": {"state": "ORDER_STATE_OPEN",
                              "cumQuantity": "0"}}
        if path == "/v1/orders/open/cancel":
            return {}
        raise AssertionError((method, path))

    def posts(self):
        return [(p, b) for m, p, b in self.calls
                if m == "POST" and p == "/v1/orders"]


def _arm(monkeypatch, tmp_path, slug=SLUG):
    monkeypatch.setenv(micro.LIVE_ENV, "YES")
    monkeypatch.setenv(micro.SID_ENV, "sid1")
    monkeypatch.setenv(micro.ARM_ENV, "sid1")
    monkeypatch.setenv(micro.MARKET_ENV, slug)
    monkeypatch.setenv(micro.GATE_ENV, slug)
    monkeypatch.setenv(micro.STATE_ENV, str(tmp_path))
    monkeypatch.setenv(q1.KEY_ENV, "k")
    monkeypatch.setenv(q1.SECRET_ENV, "s")


def _fill(state, cum, px):
    return {"order": {"state": state, "cumQuantity": cum, "avgPx": px,
                      "marketSlug": SLUG}}


# ------------------------------ sizing math ------------------------------

def test_micro_qty_live_examples_never_exceed_one_cent():
    # NYY/LAA live proof: 0.48, min 0.01 -> qty 0.02 = $0.0096
    r = micro.micro_qty(D("0.48"), D("0.01"))
    assert (r["qty"], r["notional"]) == (D("0.02"), D("0.0096"))
    # DET/MIN price shape 0.42 -> qty 0.02 = $0.0084
    r = micro.micro_qty(D("0.42"), D("0.01"))
    assert (r["qty"], r["notional"]) == (D("0.02"), D("0.0084"))
    # 0.70 -> qty 0.01 = $0.0070; STL/LAD prop 0.64 -> 0.01 = $0.0064
    assert micro.micro_qty(D("0.70"), D("0.01"))["qty"] == D("0.01")
    assert micro.micro_qty(D("0.64"), D("0.01"))["notional"] == D("0.0064")
    for price in ("0.21", "0.33", "0.50", "0.79"):
        r = micro.micro_qty(D(price), D("0.01"))
        assert r["skip"] is None and r["notional"] <= D("0.01")


def test_venue_minimum_one_contract_is_skipped_not_fallen_back():
    r = micro.micro_qty(D("0.42"), D("1"))
    assert r["qty"] is None
    assert r["skip"].startswith("venue_min_exceeds_cap")


def test_venue_minimum_between_one_and_two_cents_is_allowed():
    r = micro.micro_qty(D("0.50"), D("0.03"))     # min notional 0.015
    assert r["qty"] == D("0.03") and r["notional"] == D("0.015")
    assert r.get("venue_min_exception") is True
    r2 = micro.micro_qty(D("0.80"), D("0.03"))    # 0.024 > 2c cap
    assert r2["qty"] is None


# ------------------------------ fail-closed ------------------------------

def test_disarmed_makes_zero_network_calls_even_if_q1_is_armed(
        monkeypatch, tmp_path):
    # arming the q1 keys must NOT arm micro — isolation of arming keys
    monkeypatch.setenv(q1.LIVE_ENV, "YES")
    monkeypatch.setenv(q1.SID_ENV, "x")
    monkeypatch.setenv(q1.ARM_ENV, "x")
    monkeypatch.setenv(micro.STATE_ENV, str(tmp_path))
    fake = Fake()
    rep = micro.run_campaign(fake)
    assert rep["status"] == "DISARMED"
    assert fake.calls == []


def test_metadata_missing_skips_with_no_mutation(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    fake = Fake(meta=None)
    rep = micro.run_campaign(fake, clock=lambda: 0.0, sleep=lambda s: None)
    assert rep["status"] == "MICRO_SKIP_METADATA_UNVERIFIED"
    assert fake.posts() == []


def test_tick_mismatch_skips_with_no_mutation(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    fake = Fake(meta=("0.01", "0.007"), bbo=("0.4800", "0.4850"))
    rep = micro.run_campaign(fake, clock=lambda: 0.0, sleep=lambda s: None)
    assert rep["status"] == "MICRO_SKIP_TICK_MISMATCH"
    assert fake.posts() == []


def test_venue_min_whole_contract_skips_never_q1(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    fake = Fake(meta=("1", "0.005"))
    rep = micro.run_campaign(fake, clock=lambda: 0.0, sleep=lambda s: None)
    assert rep["status"] == "MICRO_SKIP_SIZE"
    assert fake.posts() == []


# ------------------------------ campaigns ------------------------------

def _clock():
    t = {"v": 0.0}

    def clock():
        t["v"] += 0.5
        return t["v"]
    return clock


def test_full_micro_match_decimal_bodies_and_ledger(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    fills = [_fill("ORDER_STATE_FILLED", "0.02", "0.4800"),
             _fill("ORDER_STATE_FILLED", "0.02", "0.5000")]
    fake = Fake(meta=("0.01", "0.005"), bbo=("0.4800", "0.4850"),
                fills=fills)
    rep = micro.run_campaign(fake, clock=_clock(), sleep=lambda s: None)
    assert rep["status"] == "MICRO_MATCHED", rep
    posts = fake.posts()
    assert len(posts) == 2
    first_body = posts[0][1]
    assert first_body["quantity"] == 0.02
    assert first_body["participateDontInitiate"] is True
    assert posts[1][1]["quantity"] == 0.02        # complement = filled qty
    assert D(rep["first_leg"]["qty"]) == D("0.02")
    assert D(rep["first_leg"]["px"]) == D("0.48")
    assert D(rep["first_leg"]["notional"]) == D("0.0096")
    assert D(rep["residual_qty"]) == 0
    assert rep["realized_pnl"] is None
    rows = [json.loads(x) for x in
            (tmp_path / "micro_ledger.jsonl").read_text().splitlines()]
    submitted = [r for r in rows if r["kind"] == "submitted"]
    assert all("qty" in r and "notional" in r for r in submitted)
    assert D(submitted[0]["qty"]) == D("0.02")
    assert D(submitted[0]["notional"]) == D("0.0096")


def test_partial_first_fill_is_real_inventory_and_sizes_complement(
        monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setenv(micro.FIRST_WAIT_ENV, "1")
    fills = [
        _fill("ORDER_STATE_OPEN", "0.01", "0.4800"),   # partial while open
        _fill("ORDER_STATE_OPEN", "0.01", "0.4800"),   # cancel_exact status
        _fill("ORDER_STATE_CANCELED", "0.01", "0.4800"),
        _fill("ORDER_STATE_CANCELED", "0.01", "0.4800"),  # late look
        _fill("ORDER_STATE_FILLED", "0.01", "0.5000"),    # complement
    ]
    fake = Fake(meta=("0.01", "0.005"), fills=fills)
    rep = micro.run_campaign(fake, clock=_clock(), sleep=lambda s: None)
    assert rep["status"] == "MICRO_MATCHED", rep
    assert rep["first_leg"]["qty"] == "0.01"          # never NO_FILL
    posts = fake.posts()
    assert posts[1][1]["quantity"] == 0.01            # complement = actual

def test_partial_complement_leaves_exact_decimal_residual(
        monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setenv(micro.DEADLINE_ENV, "6")
    fills = [_fill("ORDER_STATE_FILLED", "0.02", "0.4800")]
    # every complement status read reports a 0.01 partial, then cancels
    fills += [_fill("ORDER_STATE_CANCELED", "0.01", "0.5000")] * 12
    fake = Fake(meta=("0.01", "0.005"), fills=fills)
    rep = micro.run_campaign(fake, clock=_clock(), sleep=lambda s: None)
    assert rep["status"] == "MICRO_PARTIAL_RESIDUAL_HOLD", rep
    assert D(rep["residual_qty"]) == D("0.01")        # decimal-exact
    assert D(rep["residual_notional"]) == D("0.0048")
    assert D(rep["complement"]["qty"]) == D("0.01")


def test_q1_default_path_is_untouched():
    assert q1.QTY == 1
    for mod_path in ("src/pmus_q1_executor.py", "src/pmus_q1_executor_v4.py",
                     "src/pmus_aws_supervisor.py"):
        src = (Path(__file__).parent.parent / mod_path).read_text()
        assert "pmus_micro_executor" not in src


def test_micro_eligible_is_get_only(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    fake = Fake(meta=("0.01", "0.005"), bbo=("0.6400", "0.6700"))
    row = micro.micro_eligible(fake, "tsc-mlb-stl-lad-2026-09-02-8pt5")
    assert row["skip_reason"] is None
    assert D(row["qty"]) == D("0.01")
    assert D(row["intended_notional"]) == D("0.0064")
    assert row["market_type"] == "MONEYLINE"
    assert all(m == "GET" for m, p, b in fake.calls)
