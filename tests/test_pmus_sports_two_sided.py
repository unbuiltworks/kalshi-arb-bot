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

    def place(self, slug, side, px, qty, stage, post_only=True):
        self.taker = getattr(self, "taker", []) + ([oid_px := (side, px)] if not post_only else [])
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


def test_flat_market_out_of_band_frees_its_slot(eng, monkeypatch):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    v.bid, v.ask = D("0.90"), D("0.92")
    e.step_market("aec-x")                      # pulls both legs
    assert not [o for o in v.orders.values() if o["open"]]
    e.b.d["markets"]["aec-x"]["idle_since"] = 0
    e.step_market("aec-x")
    assert e.active() == []


def test_registry_doc_lists_only_open_own_orders(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    doc = M.registry_doc(e.b)
    assert doc["readable"] is True and doc["manages_own_resting"] is True
    assert {r["side"] for r in doc["orders"]} == {"YES", "NO"}
    assert all(r["market"] == "aec-x" for r in doc["orders"])
    assert doc["as_of_utc"]


def test_registry_http(tmp_path):
    import json, socket, urllib.request
    b = M.Book(tmp_path / "s.json")
    b.m("aec-z")["orders"]["OID1"] = {"side": "YES", "px": "0.4", "qty": "1", "filled": "0", "open": True}
    s = socket.socket(); s.bind(("", 0)); port = s.getsockname()[1]; s.close()
    M.serve_registry(b, port)
    d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/{M.REGISTRY_KEY}").read())
    assert d["orders"][0]["venue_order_id"] == "OID1"


def test_stale_open_pair_is_resubmitted_closer(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")                                  # 0.42 / 0.52 (depth 2)
    e.b.d["markets"]["aec-x"]["pair_set_at"] -= 61
    e.step_market("aec-x")
    live = {o["side"]: o["px"] for o in v.orders.values() if o["open"]}
    assert live == {"YES": D("0.43"), "NO": D("0.53")}       # depth 1, pair 0.96
    e.b.d["markets"]["aec-x"]["pair_set_at"] -= 61
    e.step_market("aec-x")
    live = {o["side"]: o["px"] for o in v.orders.values() if o["open"]}
    assert live == {"YES": D("0.44"), "NO": D("0.54")}       # at the touch, pair 0.98


def test_join_touch_never_breaks_pair_cap(eng):
    e, v = eng
    v.bid, v.ask = D("0.44"), D("0.45")                       # 1c spread
    e.select(v.account())
    e.step_market("aec-x")
    e.b.d["markets"]["aec-x"]["pair_set_at"] -= 600
    e.step_market("aec-x")
    live = {o["side"]: o["px"] for o in v.orders.values() if o["open"]}
    assert sum(live.values()) <= M.PAIR_CAP and len(live) == 2


def test_stale_completion_steps_up_one_tick_to_cap(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)          # YES @0.42
    e.step_market("aec-x")
    comp = next(o for o in e.b.d["markets"]["aec-x"]["orders"].values() if o["open"])
    assert comp["px"] == "0.52"
    comp["placed_at"] -= 61
    e.step_market("aec-x")                                    # cancel
    e.step_market("aec-x")                                    # resubmit
    live = [o for o in v.orders.values() if o["open"]]
    assert len(live) == 1 and live[0]["px"] == D("0.53")
    for _ in range(5):
        c = next(o for o in e.b.d["markets"]["aec-x"]["orders"].values() if o["open"])
        c["placed_at"] -= 61
        e.step_market("aec-x"); e.step_market("aec-x")
    live = [o for o in v.orders.values() if o["open"]]
    assert live[0]["px"] == D("0.54")                         # its bid; pair 0.96 <= cap


# ---------------------------------------------------------------- reward mode
# OWNER 2026-11-14: prefer liquidity-rewarded markets, quote ~10 there.
from src import pmus_sports_rewards as R  # noqa: E402

INCENTIVES = {"programs": [
    {"marketSlug": "aec-rw", "instrumentState": "OPEN",
     "eventStartTime": "2026-11-14T10:00:00Z",
     "timePeriods": [
         {"programType": "liquidityProgram", "programId": "LP1",
          "rewardPool": "50", "period": "daily", "discountFactor": "0.5",
          "targetSize": "100", "maxSpread": "0.03", "status": "active",
          "start": "2026-01-01T00:00:00Z"},
         {"programType": "rebateProgram", "programId": "RB",
          "rewardPool": "9", "period": "daily", "discountFactor": "1",
          "targetSize": "1", "status": "active"}]},
    {"marketSlug": "aec-ended", "timePeriods": [
        {"programType": "liquidityProgram", "programId": "LP2",
         "rewardPool": "80", "period": "daily", "discountFactor": "0.5",
         "targetSize": "50", "maxSpread": "0.03", "status": "ended"}]},
]}
BOOK = {"marketData": {"bids": [{"px": {"value": "0.44"}, "qty": "60"},
                                {"px": {"value": "0.43"}, "qty": "200"}],
                       "offers": [{"px": {"value": "0.46"}, "qty": "40"},
                                  {"px": {"value": "0.47"}, "qty": "300"}]}}


class RewardVenue(FakeVenue):
    def __init__(self):
        super().__init__()
        self.gets = []
        self.targets = [
            {"slug": "aec-x", "market_meta": {"bestBidQuote": "0.44", "bestAskQuote": "0.46"}},
            {"slug": "aec-rw", "market_meta": {"bestBidQuote": "0.44", "bestAskQuote": "0.48"}},
            {"slug": "aec-ended", "market_meta": {"bestBidQuote": "0.44", "bestAskQuote": "0.45"}},
        ]

    def gget(self, path):
        self.gets.append(path)
        if path.startswith("/v1/incentives?"):
            return INCENTIVES
        if path.endswith("/book"):
            return BOOK
        return {}


@pytest.fixture
def reng(tmp_path, monkeypatch):
    monkeypatch.setenv("PMUS_TWO_SIDED_REWARDS", "1")
    monkeypatch.setattr(M, "TOTAL_CAP", D("12.00"))           # live value
    v = RewardVenue()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._rget = R.SpacedGet(v.gget, gap_s=0.0)
    return e, v


def test_reward_discovery_parses_fake_incentives_payload():
    found, diag = R.fetch_programs(lambda p: INCENTIVES, ["aec-rw", "aec-ended", "aec-x"])
    assert diag["requests"] == 1 and set(found) == {"aec-rw", "aec-ended"}
    pm = R.build_program_map(found)
    per = pm["aec-rw"]["periods"]
    assert len(per) == 1 and per[0]["programId"] == "LP1"     # rebate period ignored
    assert per[0]["pool"] == 50.0 and per[0]["target"] == 100.0 and per[0]["maxSpread"] == 0.03
    pp = R.paying_period(pm["aec-rw"], now=1_790_000_000.0)
    assert pp["programId"] == "LP1" and pp["period_s"] == 86400.0
    assert R.paying_period(pm["aec-ended"], now=1_790_000_000.0) is None   # not active


def test_symbols_batches_and_spacing():
    calls, slept = [], []
    g = R.SpacedGet(lambda p: calls.append(p) or {"programs": []},
                    gap_s=0.5, sleep=slept.append, clock=lambda: 0.0)
    R.fetch_programs(g, [f"aec-{i}" for i in range(130)])
    assert len(calls) == 3 and all(c.count("symbols=") <= 60 for c in calls)
    assert len(slept) == 2 and all(s == 0.5 for s in slept)   # <= 2 rps


def test_ranking_puts_rewarded_first(reng):
    e, v = reng
    e.select(v.account())
    act = e.active()
    assert act[0] == "aec-rw"                       # rewarded first despite wider spread
    assert e.b.m("aec-rw")["reward"]["programId"] == "LP1"
    assert all(not e.b.m(s).get("reward") for s in act[1:])   # ended program = not rewarded


def test_rewarded_market_quotes_ten_at_touch_within_max_spread(reng):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.48")
    e.select(v.account())
    e.step_market("aec-rw")
    live = {o["side"]: (o["px"], o["qty"]) for o in v.orders.values() if o["open"]}
    assert live == {"YES": (D("0.44"), D("10")), "NO": (D("0.52"), D("10"))}
    mid = (D("0.44") + D("0.48")) / 2
    assert abs(D("0.44") - mid) <= D("0.03") and abs((1 - D("0.52")) - mid) <= D("0.03")
    assert sum(p for p, _ in live.values()) <= M.PAIR_CAP


def test_rewarded_pair_cap_respected_with_one_tick_spread(reng):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.45")             # touch pair = 0.99 > 0.98
    p, d = M.reward_open_prices(v.bid, v.ask, T, 0.03)
    assert d == 1 and p == {"YES": D("0.43"), "NO": D("0.54")}
    assert p["YES"] + p["NO"] <= M.PAIR_CAP
    e.select(v.account())
    e.step_market("aec-rw")
    live = {o["side"]: o["px"] for o in v.orders.values() if o["open"]}
    assert live == {"YES": D("0.43"), "NO": D("0.54")}


def test_touch_outside_max_spread_falls_back_to_normal_quote(reng):
    e, v = reng
    v.bid, v.ask = D("0.40"), D("0.46")             # half-spread 0.03 ok at touch
    assert M.reward_open_prices(v.bid, v.ask, T, 0.03) is not None
    assert M.reward_open_prices(v.bid, v.ask, T, 0.02) is None
    e.select(v.account())
    e.b.m("aec-rw")["reward"]["maxSpread"] = 0.02
    e.step_market("aec-rw")
    live = {o["side"]: (o["px"], o["qty"]) for o in v.orders.values() if o["open"]}
    assert live == {"YES": (D("0.38"), D("1")), "NO": (D("0.52"), D("1"))}


def test_total_cap_precheck_blocks_new_ten_lot_pair(reng, monkeypatch):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.48")
    monkeypatch.setattr(M, "TOTAL_CAP", D("5.00"))  # worst 10-lot leg 0.52*10
    e.select(v.account())
    e.step_market("aec-rw")
    assert not [o for o in v.orders.values() if o["open"]]
    monkeypatch.setattr(M, "TOTAL_CAP", D("12.00"))
    e.step_market("aec-rw")
    assert len([o for o in v.orders.values() if o["open"]]) == 2
    # a second market cannot open while aec-rw's resting 10-lot pair could
    # leave a $5.20 lone leg and the cap is $5.50
    monkeypatch.setattr(M, "TOTAL_CAP", D("5.50"))
    e.step_market("aec-x")
    assert {o["slug"] for o in v.orders.values() if o["open"]} == {"aec-rw"}


def test_rewarded_per_market_cap_on_lone_leg(reng, monkeypatch):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.48")
    monkeypatch.setattr(M, "REWARD_PER_MARKET_CAP", D("5.00"))
    e.select(v.account())
    e.step_market("aec-rw")                          # NO leg 0.52*10 = 5.20 > 5.00
    assert not [o for o in v.orders.values() if o["open"]]


def test_rewarded_partial_fill_completion_resizes(reng):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.48")
    e.select(v.account())
    e.step_market("aec-rw")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES" and o["open"])
    v.orders[yes].update(filled=D("4"))             # partial, still open
    for _ in range(3):                               # cancel YES rest, cancel NO x10, re-place NO x4
        e.step_market("aec-rw")
    live = [o for o in v.orders.values() if o["open"]]
    assert len(live) == 1 and live[0]["side"] == "NO" and live[0]["qty"] == D("4")
    assert live[0]["px"] + D("0.44") <= M.PAIR_CAP
    assert e.b.unmatched_cost("aec-rw") == D("1.76")


def test_reward_program_log_is_theoretical(reng, capsys):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.46")
    e.select(v.account())
    e.step_market("aec-rw")
    out = capsys.readouterr().out
    rows = [ln for ln in out.splitlines() if '"kind":"reward_program"' in ln]
    assert rows and all("THEORETICAL" in ln for ln in rows)
    assert any('"est_share"' in ln and '"qualifies":true' in ln for ln in rows)
    e.report()
    hb = [ln for ln in capsys.readouterr().out.splitlines() if '"heartbeat"' in ln][-1]
    assert '"rewarded_active":1' in hb
    assert '"locked_edge_usd":0' in hb                          # rewards never in P&L


def test_default_off_never_touches_incentives(tmp_path, monkeypatch):
    monkeypatch.delenv("PMUS_TWO_SIDED_REWARDS", raising=False)
    v = RewardVenue()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.select(v.account())
    for s in e.active():
        e.step_market(s)
    assert v.gets == []
    assert all(o["qty"] == D("1") for o in v.orders.values())
    assert not any(e.b.m(s).get("reward") for s in e.active())



def test_lone_leg_exits_by_taking_complement_when_cheap(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")                                  # 0.42 / 0.52
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")                                  # lone YES @0.42
    e.b.d["markets"]["aec-x"]["lone_since"] -= M.EXIT_S + 1
    v.bid, v.ask = D("0.40"), D("0.42")                     # NO ask = 0.60 -> pair 1.02
    e.step_market("aec-x")                                  # pulls resting maker
    e.step_market("aec-x")                                  # takes
    assert getattr(v, "taker", []) == [("NO", D("0.60"))]


def test_lone_leg_not_exited_when_too_expensive(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")
    e.b.d["markets"]["aec-x"]["lone_since"] -= M.EXIT_S + 1
    v.bid, v.ask = D("0.25"), D("0.27")                     # NO ask 0.75 -> pair 1.17
    e.step_market("aec-x"); e.step_market("aec-x")
    assert getattr(v, "taker", []) == []
    assert [o for o in v.orders.values() if o["open"] and o["side"] == "NO"]
