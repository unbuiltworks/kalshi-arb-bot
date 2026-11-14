"""Owner 2026-11-14 two-sided resting bids: pricing, completion, caps, isolation."""
import os
from decimal import Decimal as D

import pytest

os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
from src import pmus_sports_two_sided as M  # noqa: E402

T = D("0.01")


def test_open_prices_two_ticks_under_each_touch():
    p = M.open_prices(D("0.44"), D("0.46"), T)
    assert p == {"YES": D("0.42"), "NO": D("0.52")}
    assert p["YES"] + p["NO"] == D("0.94")


def test_open_refused_outside_band_or_wide():
    assert M.open_prices(D("0.10"), D("0.12"), T) is None
    assert M.open_prices(D("0.40"), D("0.60"), T) is None


def test_completion_never_lowers_and_respects_pair_cap():
    assert M.completion_price("NO", D("0.52"), D("0.42"), D("0.44"), D("0.46"), T) == D("0.54")
    assert M.completion_price("NO", D("0.52"), D("0.42"), D("0.30"), D("0.70"), T) == D("0.52")
    assert M.completion_price("NO", D("0.50"), D("0.47"), D("0.40"), D("0.42"), T) == D("0.51")


class FakeVenue:
    def __init__(self):
        self.n = 0
        self.orders = {}
        self.bid, self.ask = D("0.44"), D("0.46")
        self.acct = {"positions": {}, "open_orders": []}
        self.targets = [{"slug": "aec-x", "market_meta": {"bestBidQuote": "0.44", "bestAskQuote": "0.46"}},
                        {"slug": "aec-owner", "market_meta": {"bestBidQuote": "0.50", "bestAskQuote": "0.51"}},
                        {"slug": "cpc-btc-updown-5m-x", "market_meta": {"bestBidQuote": "0.5", "bestAskQuote": "0.51"}}]
        from src import pmus_spring_gateway_candidates as gw
        from src import pmus_q1_executor_v4 as q1
        self.gw, self.q1 = gw, q1

    def live_sports(self):
        return [t for t in self.targets if t["slug"].startswith(M.SPORTS_PREFIXES)]

    def bbo(self, slug):
        return {"bid": self.bid, "ask": self.ask}

    def spec(self, slug):
        return {"tick": T, "min_qty": D("1")}

    def account(self):
        return self.acct

    def place(self, slug, side, px, qty, stage):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {"slug": slug, "side": side, "px": px, "qty": qty, "filled": D(0), "open": True}
        return oid

    def status(self, oid):
        o = self.orders[oid]
        return {"qty": o["filled"], "px": o["px"] if o["filled"] else None, "open": o["open"], "state": ""}

    def cancel(self, oid):
        self.orders[oid]["open"] = False
        return True


@pytest.fixture
def eng(tmp_path):
    v = FakeVenue()
    v.acct["positions"] = {"aec-owner": D("5")}
    return M.Engine(v, M.Book(tmp_path / "s.json")), v


def test_select_skips_owner_and_btc(eng):
    e, v = eng
    e.select(v.account())
    assert e.active() == ["aec-x"]


def test_full_pair_cycle(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    opened = {o["side"]: o["px"] for o in v.orders.values() if o["open"]}
    assert opened == {"YES": D("0.42"), "NO": D("0.52")}
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    v.bid, v.ask = D("0.40"), D("0.42")      # market moves: partner is NOT touched
    e.step_market("aec-x")
    e.step_market("aec-x")
    comp = [o for o in v.orders.values() if o["open"]]
    assert len(comp) == 1 and comp[0]["side"] == "NO" and comp[0]["px"] == D("0.52")
    assert v.n == 2
    cid = next(k for k, o in v.orders.items() if o["open"])
    v.orders[cid].update(filled=D("1"), open=False)
    e.step_market("aec-x")
    assert e.b.d["pairs"][0]["pair_cost"] == "0.94"
    assert len([o for o in v.orders.values() if o["open"]]) == 2   # next pair set together


def test_requote_moves_both_legs_together(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    v.bid, v.ask = D("0.47"), D("0.49")
    e.step_market("aec-x")
    live = {o["side"]: o["px"] for o in v.orders.values() if o["open"]}
    assert live == {"YES": D("0.45"), "NO": D("0.49")}


def test_total_cap_blocks_new_pairs(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "TOTAL_CAP", D("0.10"))
    e.select(v.account())
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")
    assert e.b.total_unmatched() == D("0.42")
    e.b.d["markets"]["aec-x"]["done"] = True
    v.targets.append({"slug": "aec-y", "market_meta": {"bestBidQuote": "0.44", "bestAskQuote": "0.46"}})
    e.select(v.account())
    assert e.active() == []
