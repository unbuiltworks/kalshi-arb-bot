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
    assert '"locked_edge_usd":"0.0000"' in hb                          # rewards never in P&L


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


# ---------------------------------------------------------------- realtime WS
# OWNER 2026-11-14: react to price changes in real time (PMUS_TWO_SIDED_WS=1).
import json  # noqa: E402
from src import pmus_two_sided_ws as W  # noqa: E402


def frame(slug, bid, ask):
    return {"market_data": {"marketSlug": slug,
                            "bids": [{"px": {"value": bid}, "qty": "5"}],
                            "offers": [{"px": {"value": ask}, "qty": "5"}]}}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class WsVenue(FakeVenue):
    """Gateway poll stays at the FakeVenue bid/ask; the socket can differ."""

    def __init__(self, clock):
        super().__init__()
        self.ws = W.TwoSidedWs(clock=clock, log=M.log)

    def bbo(self, slug):
        return self.ws.fresh_bbo(slug) or {"bid": self.bid, "ask": self.ask}


@pytest.fixture
def weng(tmp_path):
    clk = Clock()
    v = WsVenue(clk)
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e, v, clk


def _open(v):
    return {o["side"]: o["px"] for o in v.orders.values() if o["open"]}


def test_ws_wire_format_matches_the_recorder(monkeypatch):
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from src import pmus_ws_evidence_recorder as rec
    raw = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    secret = base64.b64encode(raw).decode()
    monkeypatch.setenv("PMUS_API_KEY_ID", "kid")
    monkeypatch.setenv("PMUS_API_SECRET_KEY", secret)
    assert W.ws_headers(now_ms=123) == rec.auth_headers("kid", secret, now_ms=123)
    assert (W.WS_URL, W.WS_PATH) == (rec.WS_URL, rec.WS_PATH)
    mine = W.subscribe_frame({"b", "a"})["subscribe"]
    theirs = rec.subscribe_frames(["a", "b"])[0]["subscribe"]
    assert set(mine) == set(theirs) and mine["market_slugs"] == ["a", "b"]
    assert mine["subscription_type"] == theirs["subscription_type"] == rec.SUB_MARKET_DATA
    W.guard_outbound(W.subscribe_frame(["a"]))
    with pytest.raises(RuntimeError):
        W.guard_outbound({"order": {}})
    for msg in (frame("m-1", "0.45", "0.47"),
                {"marketData": {"marketSlug": "m-2", "bestBid": {"value": "0.30"},
                                "bestAsk": {"value": "0.33"}}}):
        st = {}
        rec.apply_events(st, msg, 1.0)
        got = W.parse_tops(msg)
        assert got == [(s, st[s]["bid"], st[s]["ask"]) for s in st]


def test_fresh_ws_price_used_over_gateway_and_stale_falls_back():
    from src import pmus_q1_executor_v4 as q1
    clk = Clock()
    v = M.Venue.__new__(M.Venue)
    v.q1 = q1
    v.gget = lambda p: {"marketData": {"bestBid": "0.30", "bestAsk": "0.32"}}
    v.ws = W.TwoSidedWs(clock=clk, log=lambda *a, **k: None)
    v.ws.ingest(frame("aec-x", "0.44", "0.46"))
    clk.t += 2.5
    assert v.bbo("aec-x") == {"bid": D("0.44"), "ask": D("0.46")}      # fresh WS
    clk.t += 1.0                                                     # 3.5s, socket down
    assert v.bbo("aec-x") == {"bid": D("0.30"), "ask": D("0.32")}      # gateway
    assert v.bbo("aec-unknown") == {"bid": D("0.30"), "ask": D("0.32")}
    # a book unchanged on the LIVE socket (frames still arriving) is current
    v.ws.connected = True
    v.ws.last_frame_t = clk.t - 0.5
    assert v.bbo("aec-x") == {"bid": D("0.44"), "ask": D("0.46")}
    v.ws.conn_id += 1                                                # not across reconnects
    assert v.bbo("aec-x") == {"bid": D("0.30"), "ask": D("0.32")}


def test_stale_reasons_and_ticks():
    q = {"bid": "0.44", "ask": "0.46"}
    assert M.stale_reasons(q, {"bid": D("0.44"), "ask": D("0.46")}, T) == []
    assert M.stale_reasons(q, {"bid": D("0.45"), "ask": D("0.46")}, T) == [
        "yes_touch_moved", "no_opposite_toward"]
    assert M.stale_reasons(q, {"bid": D("0.44"), "ask": D("0.45")}, T) == [
        "no_touch_moved", "yes_opposite_toward"]
    assert M.ticks_moved({"bid": D("0.44"), "ask": D("0.46")},
                         {"bid": D("0.46"), "ask": D("0.47")}, T) == 2


def test_touch_move_pulls_both_legs_and_replaces_at_new_price(weng, capsys):
    e, v, clk = weng
    e.select(v.account())
    v.ws.ingest(frame("aec-x", "0.44", "0.46"))
    e.step_market("aec-x")
    assert _open(v) == {"YES": D("0.42"), "NO": D("0.52")}
    assert not e._wake.is_set()
    v.ws.ingest(frame("aec-x", "0.43", "0.45"))          # 1-tick drop on both sides
    assert e._wake.is_set()                              # main loop woken at once
    e.drain_ws_events()
    assert "aec-x" in v.ws.wanted()                      # subscription follows active set
    assert set(v.ws.wanted()) == set(e.active())
    assert _open(v) == {"YES": D("0.41"), "NO": D("0.53")}   # gateway still says 0.44/0.46
    assert v.n == 4 and e.reactive_pulls == 1
    out = capsys.readouterr().out
    row = [ln for ln in out.splitlines() if '"reactive_pull"' in ln][0]
    assert '"slug":"aec-x"' in row and "yes_touch_moved" in row
    assert 0 <= json.loads(row.split(" ", 1)[1])["latency_ms"] < 1000


def test_two_tick_jump_pauses_reopen(weng, capsys):
    e, v, clk = weng
    e.select(v.account())
    v.ws.ingest(frame("aec-x", "0.44", "0.46"))
    e.step_market("aec-x")
    v.ws.ingest(frame("aec-x", "0.46", "0.48"))          # 2-tick jump
    e.drain_ws_events()
    assert _open(v) == {} and v.n == 2
    for _ in range(3):
        e.step_market("aec-x")
    assert _open(v) == {} and v.n == 2                   # no re-open inside the window
    assert '"jump_pause"' in capsys.readouterr().out
    e.b.d["markets"]["aec-x"]["jump_pause_until"] -= M.JUMP_PAUSE_S + 1
    e.step_market("aec-x")
    assert _open(v) == {"YES": D("0.44"), "NO": D("0.50")}


def test_completion_leg_not_pulled_by_jump(weng, capsys):
    e, v, clk = weng
    e.select(v.account())
    v.ws.ingest(frame("aec-x", "0.44", "0.46"))
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")                               # lone YES; NO completes
    v.ws.ingest(frame("aec-x", "0.40", "0.42"))          # 4-tick jump
    e.drain_ws_events()
    assert _open(v) == {"NO": D("0.52")}
    v.ws.ingest(frame("aec-x", "0.39", "0.41"))          # 1-tick touch move
    e.drain_ws_events()
    assert _open(v) == {"NO": D("0.52")} and e.reactive_pulls == 0
    assert '"completion_kept":true' in capsys.readouterr().out


def test_main_loop_wakes_on_ws_event(weng):
    import threading as th
    import time as _t
    e, v, clk = weng
    e.select(v.account())
    v.ws.ingest(frame("aec-x", "0.44", "0.46"))
    e.step_market("aec-x")
    pulled_at = []
    real_cancel = v.cancel
    v.cancel = lambda oid: pulled_at.append(_t.monotonic()) or real_cancel(oid)
    t = th.Timer(0.05, lambda: v.ws.ingest(frame("aec-x", "0.45", "0.47")))
    t0 = _t.monotonic()
    t.start()
    e._sleep(1.0)
    assert pulled_at and pulled_at[0] - t0 < 0.5         # reacted mid-sleep
    assert _open(v) == {"YES": D("0.43"), "NO": D("0.51")}


def test_ws_thread_subscribes_streams_and_reconnects(monkeypatch):
    """Real socket thread against a local server: subscribe frame for the
    wanted slugs, book frames land in the latest-top map, a dropped
    connection reconnects with backoff and resubscribes."""
    import asyncio
    import threading as th
    import time as _t
    websockets = pytest.importorskip("websockets")
    from websockets.asyncio.server import serve
    got, conns = [], []

    async def handler(ws):
        conns.append(ws)
        sub = json.loads(await ws.recv())
        got.append(sub)
        slug = sub["subscribe"]["market_slugs"][0]
        await ws.send(json.dumps(frame(slug, "0.44", "0.46")))
        await ws.send(json.dumps(frame(slug, "0.45", "0.47")))
        if len(conns) == 1:
            await ws.close()                      # force one reconnect
            return
        await asyncio.sleep(5)

    ready, port = th.Event(), []

    def server():
        async def main():
            async with serve(handler, "127.0.0.1", 0) as srv:
                port.append(srv.sockets[0].getsockname()[1])
                ready.set()
                await asyncio.sleep(8)
        asyncio.run(main())
    th.Thread(target=server, daemon=True).start()
    assert ready.wait(5)
    monkeypatch.setattr(W, "WS_URL", f"ws://127.0.0.1:{port[0]}")
    monkeypatch.setattr(W, "ws_headers", lambda now_ms=None: {"X-PM-Access-Key": "test"})
    logs = []
    ws = W.TwoSidedWs(log=lambda kind, **kw: logs.append(kind))
    changes = []
    ws.on_change = lambda *a: changes.append(a)
    ws.set_slugs(["aec-x"])
    ws.start()
    deadline = _t.monotonic() + 6
    while _t.monotonic() < deadline and logs.count("ws_connected") < 2:
        _t.sleep(0.05)
    _t.sleep(0.2)
    ws.stop()
    assert logs.count("ws_connected") >= 2 and "ws_reconnect" in logs
    assert got[0]["subscribe"]["market_slugs"] == ["aec-x"]
    assert got[0]["subscribe"]["subscription_type"] == W.SUB_MARKET_DATA
    assert ws.fresh_bbo("aec-x") == {"bid": D("0.45"), "ask": D("0.47")}
    assert changes and changes[0][0] == "aec-x"


def test_ws_default_off_unchanged(eng, monkeypatch):
    monkeypatch.delenv("PMUS_TWO_SIDED_WS", raising=False)
    assert not M.ws_on()
    e, v = eng
    assert e.ws is None
    e.select(v.account())
    e.step_market("aec-x")
    e.drain_ws_events()                                  # no-op
    assert "quoted_bbo" not in e.b.d["markets"]["aec-x"]
    assert M.Venue.ws is None


def test_locked_edge_counts_latest_cumulative_row_per_market(eng):
    e, v = eng
    e.b.d["pairs"] = [{"slug": "a", "qty": "1", "pair_cost": "0.96"},
                      {"slug": "a", "qty": "2", "pair_cost": "0.97"},
                      {"slug": "b", "qty": "1", "pair_cost": "0.98"}]
    last = e._latest_pairs()
    total = sum((M.ONE - D(p["pair_cost"])) * D(p["qty"]) for p in last.values())
    assert total == D("0.08")


def test_jump_threshold_is_also_a_price_floor():
    assert M.JUMP_MIN_PX == D("0.02")
    # 2 ticks of 0.005 = 1c < 2c floor -> not a jump; 4 ticks = 2c -> jump
    assert not (2 >= M.JUMP_TICKS and 2 * D("0.005") >= M.JUMP_MIN_PX)
    assert 4 >= M.JUMP_TICKS and 4 * D("0.005") >= M.JUMP_MIN_PX


def test_cfb_exits_sooner_and_accepts_more():
    assert M.exit_rule("aec-cfb-x-y-2026-11-14") == (60.0, D("1.05"))
    assert M.exit_rule("aec-atp-x-y-2026-11-14") == (M.EXIT_S, M.EXIT_MAX_PAIR)
