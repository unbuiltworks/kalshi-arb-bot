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
    # an OPEN lone leg at/over the cap blocks selecting another market
    v.targets.append({"slug": "aec-y", "market_meta": {"bestBidQuote": "0.44", "bestAskQuote": "0.46"}})
    e.select(v.account())
    assert e.active() == ["aec-x"]


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
    monkeypatch.setattr(M, "REWARD_MIN_DEPTH_PX", D("0"))     # at-touch mode
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


def test_total_cap_precheck_shrinks_ten_lot_reward_pair_to_fit(reng, monkeypatch):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.48")
    monkeypatch.setattr(M, "TOTAL_CAP", D("5.00"))  # worst 10-lot leg 0.52*10
    e.select(v.account())
    e.step_market("aec-rw")
    live = [o for o in v.orders.values() if o["open"]]
    # 5.00 / 0.52 -> 9 contracts per leg: worst lone leg 4.68 <= cap
    assert len(live) == 2 and all(o["qty"] == D("9") for o in live)
    e.step_market("aec-rw")                        # no churn while it fits
    assert len([o for o in v.orders.values() if o["open"]]) == 2
    assert len(v.orders) == 2
    monkeypatch.setattr(M, "TOTAL_CAP", D("12.00"))
    e.step_market("aec-rw"); e.step_market("aec-rw")
    live = [o for o in v.orders.values() if o["open"]]
    assert len(live) == 2 and all(o["qty"] == D("10") for o in live)
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



def test_lone_leg_exits_by_taking_complement_when_cheap(eng, monkeypatch):
    # pre-2026-11-15 fee-blind behaviour (PMUS_TWO_SIDED_EXIT_FEE_AWARE=0)
    monkeypatch.setattr(M, "EXIT_FEE_AWARE", False)
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
    assert '"slug":"aec-x"' in row and "yes_closer_than_2t" in row
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
    assert M.exit_rule("aec-cfb-x-y-2026-11-14") == (20.0, D("1.05"))
    assert M.EXIT_S == 30.0
    assert M.exit_rule("aec-atp-x-y-2026-11-14") == (M.EXIT_S, M.EXIT_MAX_PAIR)


def test_reward_check_logs_only_credited_as_money(eng, monkeypatch, capsys):
    e, v = eng
    v.t = object()
    e.b.m("aec-cs2-x")
    from src import pmus_incentive_audit as IA
    monkeypatch.setattr(IA, "earnings", lambda t: {
        "ok": True, "endpoint": "/v1/incentives/earnings", "rows": 2,
        "credited_usd": "0", "accrued_not_credited_usd": "1.25",
        "by_status": {"PENDING": 2}, "by_program": {"liquidityProgram": 2},
        "rows_detail": [{"marketSlug": "aec-cs2-x", "status": "PENDING"},
                        {"marketSlug": "other", "status": "PENDING"}]})
    e.reward_check()
    out = capsys.readouterr().out
    assert '"kind":"REWARD_CHECK"' in out and '"rows_on_our_markets":1' in out
    assert '"credited_usd":"0"' in out


@pytest.mark.parametrize("st,late", [
    ({"type": "football", "period": "Q4", "elapsed": "9:37"}, "Q4"),
    ({"type": "football", "period": "Q3", "elapsed": "2:00"}, None),
    ({"type": "basketball", "period": "Q4", "elapsed": "07:08"}, "Q4"),
    ({"type": "college-basketball", "period": "H2", "elapsed": "20:00"}, None),
    ({"type": "college-basketball", "period": "H2", "elapsed": "08:00"}, "H2_last10"),
    ({"type": "hockey", "period": "P3", "elapsed": "12:37"}, "P3"),
    ({"type": "soccer", "period": "2H", "elapsed": "74'"}, "2H_70+"),
    ({"type": "soccer", "period": "1H", "elapsed": "10'"}, None),
    ({"type": "tennis", "period": "S3"}, "set_S3"),
    ({"type": "tennis", "period": "S2"}, None),
    ({"type": "table-tennis", "period": "S4"}, "set_S4"),
    ({"type": "ufc", "period": "R3", "ufcState": {"rounds": 3}}, "final_round"),
    ({"type": "football", "period": "OT"}, "overtime:OT"),
    (None, None), ({"type": "cs2"}, None)])
def test_is_late(st, late):
    assert M.is_late(st) == late


def test_late_game_stops_new_pairs_but_completion_continues(eng):
    e, v = eng
    v.states = {}
    e.select(v.account())
    e.step_market("aec-x")
    assert len([o for o in v.orders.values() if o["open"]]) == 2
    v.states = {"aec-x": {"type": "football", "period": "Q4"}}
    e.step_market("aec-x")                                   # pulls the opening pair
    assert not [o for o in v.orders.values() if o["open"]]
    e.b.d["markets"]["aec-x"]["pos"] = {"YES": "1", "NO": "0"}
    e.b.d["markets"]["aec-x"]["cost"] = {"YES": "0.42", "NO": "0"}
    e.step_market("aec-x")                                   # completion still placed
    assert [o for o in v.orders.values() if o["open"] and o["side"] == "NO"]


def test_late_game_not_selected(eng):
    e, v = eng
    v.states = {"aec-x": {"type": "hockey", "period": "P3"}}
    e.select(v.account())
    assert e.active() == []


def test_account_audit_sums_venue_realized_per_market_and_sport(eng, monkeypatch, capsys):
    e, v = eng
    v.t = object()
    e.b.m("aec-cfb-a-b-2026-11-14"); e.b.m("aec-atp-c-d-2026-11-14")
    from src import pmus_settlement_auditor as SA
    rows = [{"type": "ACTIVITY_TYPE_TRADE", "trade": {"marketSlug": "aec-cfb-a-b-2026-11-14", "realizedPnl": {"value": "-0.40"}}},
            # the venue's real shape: settlement P&L is afterPosition.realized
            {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "positionResolution": {"marketSlug": "aec-atp-c-d-2026-11-14", "beforePosition": {"realized": "0"}, "afterPosition": {"realized": "0.05"}}},
            {"type": "ACTIVITY_TYPE_TRADE", "trade": {"marketSlug": "cpc-btc-x", "realizedPnl": "9"}}]
    monkeypatch.setattr(SA, "activity_pages", lambda t, since=None, max_pages=0: {"rows": rows, "ended": "eof"})
    e.account_audit()
    out = capsys.readouterr().out
    body = out.split("ACCOUNT_AUDIT")[1].split("label")[0]
    assert '"7_settled_realized_usd":"0.05"' in body and '"7_settled_markets":1' in body
    assert '"unsettled_trade_realized_usd":"-0.40"' in body
    assert '"settled_by_sport":{"atp":"0.05"}' in body and "cpc-btc" not in body


def test_tennis_pair_cap_is_tighter():
    assert M.pair_cap("aec-atp-x-y-2026-11-14") == D("0.96")
    assert M.pair_cap("aec-itfme-x-y-2026-11-14") == D("0.96")
    assert M.pair_cap("aec-cfb-x-y-2026-11-14") == M.PAIR_CAP
    assert M.open_prices(D("0.48"), D("0.50"), T, depth=1, cap=D("0.96")) is not None   # 0.47+0.49
    assert M.open_prices(D("0.48"), D("0.49"), T, depth=0, cap=D("0.96")) is None       # 0.48+0.51


# ------------------------------------------------- fee-aware exit (2026-11-15)
def test_taker_fee_rounds_up_to_the_cent_per_order():
    assert M.EXIT_FEE_AWARE is True and M.EXIT_MAX_TAKER_QTY == D("0")
    assert M.taker_fee(D("0.60"), D("1")) == D("0.02")      # 0.0144 -> 0.02
    assert M.taker_fee(D("0.60"), D("2")) == D("0.03")      # 0.0288 -> 0.03
    assert M.taker_fee(D("0.50"), D("10")) == D("0.15")     # exact
    assert M.taker_fee(D("0.99"), D("1")) == D("0.01")      # 0.000594 -> 0.01


def _lone_yes(e, v):
    e.select(v.account())
    e.step_market("aec-x")                                  # 0.42 / 0.52
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")                                  # lone YES @0.42
    e.b.d["markets"]["aec-x"]["lone_since"] -= M.EXIT_S + 1


def test_fee_pushes_exit_over_cap_blocks_taker(eng, capsys):
    e, v = eng
    _lone_yes(e, v)
    v.bid, v.ask = D("0.40"), D("0.42")     # 0.42 + 0.60 = 1.02, + 0.02 fee = 1.04
    e.step_market("aec-x"); e.step_market("aec-x")
    assert getattr(v, "taker", []) == []
    assert [o for o in v.orders.values() if o["open"] and o["side"] == "NO"]
    out = capsys.readouterr().out
    row = [ln for ln in out.splitlines() if '"exit_skipped_fee"' in ln]
    assert len(row) == 1 and '"pair_cost_with_fee":"1.04"' in row[0]


def test_fee_under_cap_allows_exit_and_logs_fee(eng, capsys):
    e, v = eng
    _lone_yes(e, v)
    v.bid, v.ask = D("0.41"), D("0.43")     # 0.42 + 0.59 = 1.01, + 0.02 fee = 1.03
    e.step_market("aec-x")                  # pulls resting maker
    e.step_market("aec-x")                  # takes
    assert getattr(v, "taker", []) == [("NO", D("0.59"))]
    row = [ln for ln in capsys.readouterr().out.splitlines()
           if '"EXIT_LONE_LEG"' in ln][0]
    d = json.loads(row.split(" ", 1)[1])
    assert d["est_fee_usd"] == "0.02" and d["pair_cost_with_fee"] == "1.03"


def test_large_lone_leg_is_not_taker_exited(reng, capsys, monkeypatch):
    e, v = reng
    monkeypatch.setattr(M, "EXIT_MAX_TAKER_QTY", D("2"))   # gate now opt-in
    v.bid, v.ask = D("0.44"), D("0.48")
    e.select(v.account())
    e.step_market("aec-rw")                                 # 10 x 0.44 / 10 x 0.52
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES" and o["open"])
    v.orders[yes].update(filled=D("10"), open=False)
    e.step_market("aec-rw")                                 # lone 10-lot YES
    mk = e.b.d["markets"]["aec-rw"]
    mk["lone_since"] -= M.EXIT_S + 1
    v.bid, v.ask = D("0.45"), D("0.46")     # NO ask 0.55 -> pair 0.99: cheap
    for _ in range(3):
        e.step_market("aec-rw")
    assert getattr(v, "taker", []) == []
    live = [o for o in v.orders.values() if o["open"]]
    assert len(live) == 1 and live[0]["side"] == "NO" and live[0]["qty"] == D("10")
    assert '"exit_skipped_size"' in capsys.readouterr().out
    # past the exit deadline the maker completion JOINS the NO bid (0.54,
    # pair 0.98 <= break-even) instead of stepping one tick
    e.step_market("aec-rw"); e.step_market("aec-rw")
    live = [o for o in v.orders.values() if o["open"]]
    assert len(live) == 1 and live[0]["px"] == D("0.54")


def test_small_lone_leg_still_exits_and_size_gate_can_be_disabled(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "EXIT_MAX_TAKER_QTY", D("0"))
    _lone_yes(e, v)
    v.bid, v.ask = D("0.41"), D("0.43")
    e.step_market("aec-x"); e.step_market("aec-x")
    assert getattr(v, "taker", []) == [("NO", D("0.59"))]


# ------------------------------------------------- reward-first (2026-11-15)
def _lv(rows):
    return [{"px": {"value": p}, "qty": q} for p, q in rows]


RF_BBO = {"aec-rf1": ("0.44", "0.48"), "aec-rf2": ("0.44", "0.48"),
          "aec-rf3": ("0.44", "0.45"), "aec-x": ("0.44", "0.46")}
RF_BOOKS = {
    # thin touch: our 10 is a large share
    "aec-rf1": {"bids": _lv([("0.44", "20"), ("0.43", "200")]),
                "offers": _lv([("0.48", "20"), ("0.49", "200")])},
    # deep touch, bigger pool, smaller share -> lower expected reward
    "aec-rf2": {"bids": _lv([("0.44", "1000")]), "offers": _lv([("0.48", "1000")])},
    # 1-tick spread -> depth 1; target met at the best bid: YES leg OUTSIDE
    "aec-rf3": {"bids": _lv([("0.44", "500")]),
                "offers": _lv([("0.45", "5"), ("0.46", "5"), ("0.47", "500")])},
}


def _live_prog(slug, pid, pool, max_spread=None):
    tp = {"programType": "liquidityProgram", "programId": pid, "rewardPool": pool,
          "period": "live", "discountFactor": "0.5", "targetSize": "100",
          "status": "active", "start": "2026-01-01T00:00:00Z"}
    if max_spread:
        tp["maxSpread"] = max_spread
    return {"marketSlug": slug, "eventStartTime": "", "timePeriods": [tp]}


RF_INCENTIVES = {"programs": [_live_prog("aec-rf1", "P1", "100", "0.03"),
                              _live_prog("aec-rf2", "P2", "300"),
                              _live_prog("aec-rf3", "P3", "5000")]}


class RFVenue(FakeVenue):
    def __init__(self):
        super().__init__()
        self.gets = []
        self.px = {s: (D(b), D(a)) for s, (b, a) in RF_BBO.items()}
        self.targets = [{"slug": s, "market_meta": {"bestBidQuote": b, "bestAskQuote": a}}
                        for s, (b, a) in RF_BBO.items()]
        self.ws = None

    def bbo(self, slug):
        if self.ws is not None:
            hit = self.ws.fresh_bbo(slug)
            if hit:
                return hit
        b, a = self.px[slug]
        return {"bid": b, "ask": a}

    def gget(self, path):
        self.gets.append(path)
        if path.startswith("/v1/incentives?"):
            return RF_INCENTIVES
        for s, bk in RF_BOOKS.items():
            if path == f"/v1/markets/{s}/book":
                return {"marketData": bk}
        return {}


@pytest.fixture
def rfeng(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "REWARD_MIN_DEPTH_PX", D("0"))     # at-touch mode
    monkeypatch.delenv("PMUS_TWO_SIDED_REWARDS", raising=False)
    monkeypatch.setenv("PMUS_TWO_SIDED_REWARD_FIRST", "1")
    monkeypatch.setattr(M, "TOTAL_CAP", D("20.00"))
    monkeypatch.setattr(M, "MAX_MARKETS", 4)
    monkeypatch.setattr(M, "REWARD_MARKETS", 2)
    v = RFVenue()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._rget = R.SpacedGet(v.gget, gap_s=0.0)
    return e, v


def test_side_score_matches_venue_formula():
    # docs example: DF 0.30, 1,000 at best + 1,000 three ticks away
    lv = [(0.50, 1000.0), (0.47, 1000.0)]
    s = R.side_score(lv, 0.47, 0.0, 0.01, 0.30, 2000, True)
    assert abs(s["total_score"] - 1027.0) < 1e-9
    # our 1,000 three ticks away (theirs removed, ours added once)
    s = R.side_score(lv, 0.47, 1000.0, 0.01, 0.30, 2000, True, own_resting=1000.0)
    assert abs(s["share"] - 27.0 / 1027.0) < 1e-9 and s["inside"]
    # target reached at the best level -> deeper orders score zero
    s = R.side_score([(0.50, 25000.0)], 0.49, 10.0, 0.01, 0.3, 20000, True)
    assert not s["inside"] and s["share"] == 0.0 and s["qualifies"]
    # target never reached -> nobody scores
    s = R.side_score([(0.50, 5.0)], 0.50, 10.0, 0.01, 0.3, 100, True)
    assert s["inside"] and not s["qualifies"] and s["share"] == 0.0


def test_quote_estimate_share_and_rate():
    per = {"df": 0.5, "targetSize": 100.0, "maxSpread": 0.03,
           "pool_rate_usd_s": 100 / 10800.0}
    bk = {k: v for k, v in RF_BOOKS["aec-rf1"].items()}
    est = R.quote_estimate(bk, per, 0.44, 0.52, 10.0, 0.01)
    side = 10.0 / (30.0 + 0.5 * 200.0)
    assert abs(est["bid_share"] - side) < 1e-6 and abs(est["ask_share"] - side) < 1e-6
    assert est["inside_walk"] and est["max_spread_ok"]
    assert abs(est["rate_usd_s"] - per["pool_rate_usd_s"] * side) < 1e-9
    assert abs(est["est_usd_per_day"] - est["rate_usd_s"] * 86400) < 1e-4


def test_reward_first_ranking_prefers_inside_walk_and_higher_expected(rfeng, capsys, monkeypatch):
    e, v = rfeng
    monkeypatch.setattr(M, "REWARD_ONLY", False)       # filler pairs allowed
    e.select(v.account())
    rank = [ln for ln in capsys.readouterr().out.splitlines() if "REWARD_FIRST_RANK" in ln][0]
    top = [r["slug"] for r in json.loads(rank.split(" ", 1)[1])["top"]]
    assert top == ["aec-rf1", "aec-rf2", "aec-rf3"]       # rf3 outside walk -> last
    assert e.b.m("aec-rf1")["reward"]["programId"] == "P1"
    assert e.b.m("aec-rf2")["reward"]["programId"] == "P2"
    # REWARD_MARKETS=2: rf3 still trades, but in a normal (unrewarded) slot
    assert set(e.active()) == {"aec-rf1", "aec-rf2", "aec-rf3", "aec-x"}
    assert not e.b.m("aec-rf3").get("reward") and not e.b.m("aec-x").get("reward")
    e.refresh_active_rewards()                             # cap kept on refresh
    assert e.rewarded_active() == 2 and not e.b.m("aec-rf3").get("reward")


def test_reward_first_quotes_reward_qty_at_touch(rfeng):
    e, v = rfeng
    e.select(v.account())
    e.step_market("aec-rf1")
    live = {o["side"]: (o["px"], o["qty"]) for o in v.orders.values()
            if o["open"] and o["slug"] == "aec-rf1"}
    assert live == {"YES": (D("0.44"), D("10")), "NO": (D("0.52"), D("10"))}
    assert all(o["stage"] == M.REWARDED_STAGE for o in
               e.b.d["markets"]["aec-rf1"]["orders"].values())


def _rf1_open(v):
    return {o["side"]: o["px"] for o in v.orders.values() if o["open"] and o["slug"] == "aec-rf1"}


def test_reward_first_holds_on_one_tick_reprices_on_two(rfeng, capsys):
    e, v = rfeng
    e.select(v.account())
    e.step_market("aec-rf1")
    n0 = v.n
    mk = e.b.d["markets"]["aec-rf1"]
    mk["pair_set_at"] -= 10 * M.STALE_S                    # no step ladder either
    v.px["aec-rf1"] = (D("0.45"), D("0.48"))               # touch 1 tick away
    e.step_market("aec-rf1")
    assert v.n == n0 and _rf1_open(v) == {"YES": D("0.44"), "NO": D("0.52")}
    v.px["aec-rf1"] = (D("0.46"), D("0.48"))               # 2 ticks -> re-price
    e.step_market("aec-rf1")
    assert _rf1_open(v) == {"YES": D("0.46"), "NO": D("0.52")} and v.n == n0 + 2
    assert '"reward_reprice"' in capsys.readouterr().out


def test_reward_first_reprices_when_order_leaves_target_walk(rfeng, monkeypatch):
    e, v = rfeng
    e.select(v.account())
    v.px["aec-rf1"] = (D("0.44"), D("0.45"))               # 1-tick: depth-1 quote
    e.step_market("aec-rf1")
    assert _rf1_open(v) == {"YES": D("0.43"), "NO": D("0.54")}
    n0 = v.n
    # someone stacks the best bid past target: our 0.43 is now outside
    monkeypatch.setitem(RF_BOOKS, "aec-rf1", {"bids": _lv([("0.44", "500")]),
                                              "offers": _lv([("0.45", "20")])})
    v.px["aec-rf1"] = (D("0.44"), D("0.46"))               # 1 tick: would hold
    e.b.d["markets"]["aec-rf1"]["reward_est"]["t"] = 0     # walk re-check due
    e.step_market("aec-rf1")
    assert v.n == n0 + 2                                   # re-priced at the touch
    assert _rf1_open(v) == {"YES": D("0.44"), "NO": D("0.54")}


class _RFWsVenue(RFVenue):
    def __init__(self, clock):
        super().__init__()
        self.ws = W.TwoSidedWs(clock=clock, log=M.log)


def test_reward_first_no_reactive_pull_but_jump_and_late_still_pull(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "REWARD_MIN_DEPTH_PX", D("0"))     # at-touch mode
    monkeypatch.setenv("PMUS_TWO_SIDED_REWARD_FIRST", "1")
    monkeypatch.setattr(M, "TOTAL_CAP", D("20.00"))
    monkeypatch.setattr(M, "MAX_MARKETS", 1)
    clk = Clock()
    v = _RFWsVenue(clk)
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._rget = R.SpacedGet(v.gget, gap_s=0.0)
    e.select(v.account())
    assert e.active() == ["aec-rf1"]
    v.ws.ingest(frame("aec-rf1", "0.44", "0.48"))
    e.step_market("aec-rf1")
    assert _rf1_open(v) == {"YES": D("0.44"), "NO": D("0.52")}
    v.ws.ingest(frame("aec-rf1", "0.45", "0.48"))          # 1-tick touch move
    e.drain_ws_events()
    assert e.reactive_pulls == 0 and _rf1_open(v) == {"YES": D("0.44"), "NO": D("0.52")}
    v.ws.ingest(frame("aec-rf1", "0.47", "0.50"))          # 2-tick / 2c jump
    e.drain_ws_events()
    assert _rf1_open(v) == {} and e.jump_pauses == 1
    e.b.d["markets"]["aec-rf1"]["jump_pause_until"] = 0
    e.step_market("aec-rf1")
    assert _rf1_open(v) == {"YES": D("0.47"), "NO": D("0.50")}
    v.states = {"aec-rf1": {"type": "football", "period": "Q4"}}
    e.step_market("aec-rf1")                               # late cutoff pulls
    assert _rf1_open(v) == {}


def test_accrual_tracker_math_and_floor(rfeng, capsys):
    e, v = rfeng
    e.select(v.account())
    e.step_market("aec-rf1")                               # legs placed
    e.step_market("aec-rf1")                               # estimate of resting legs
    mk = e.b.d["markets"]["aec-rf1"]
    rate = mk["reward_est"]["rate_usd_s"]
    # resting: our 10 is assumed to be IN the public 0.44 x 20 level, so it is
    # removed and added back once -> 10 / (20 + 0.5 * 200)
    side = 10.0 / (20.0 + 0.5 * 200.0)
    assert abs(rate - (100 / 10800.0) * side) < 1e-12
    day = M.et_clock(M.time.time())[0]
    row = e.b.d["reward_accrual"][day]["aec-rf1"]
    before = row["est_usd"]
    mk["accrual_t"] -= 60
    e._accrue("aec-rf1", mk)
    assert abs(row["est_usd"] - before - rate * 60) < 1e-6
    mk["accrual_t"] -= 10_000                              # long gap: capped
    e._accrue("aec-rf1", mk)
    assert abs(row["est_usd"] - before - rate * (60 + M.REWARD_ACCRUAL_MAX_GAP_S)) < 1e-6
    # floor report at 19:00 ET on a fixed day (EST, UTC-5): 2026-11-16T00:00Z
    ts = 1794787200.0
    assert M.et_clock(ts)[:2] == ("2026-11-15", 19)
    e.b.d["reward_accrual"] = {"2026-11-15": {
        "aec-rf1": {"est_usd": 0.40, "rest_s": 3600, "rate_usd_s": 0.00005},
        "aec-rf2": {"est_usd": 1.20, "rest_s": 3600, "rate_usd_s": 0.0}}}
    capsys.readouterr()
    m = e.reward_accrual_report(now=ts)
    horizon = min(5 * 3600, M.REWARD_PROJECT_HORIZON_S)
    assert m["aec-rf2"]["floor"] == "met"
    assert abs(m["aec-rf1"]["projected_eod_usd"] - (0.40 + 0.00005 * horizon)) < 1e-5
    assert m["aec-rf1"]["floor"] == "at_risk"              # 0.40 + 0.36 < 1.00
    out = capsys.readouterr().out
    assert '"kind":"REWARD_ACCRUAL"' in out and "THEORETICAL" in out
    risk = [ln for ln in out.splitlines() if '"reward_floor_at_risk"' in ln]
    assert len(risk) == 1 and '"aec-rf1"' in risk[0]
    # before 18:00 ET: reported, not flagged
    e.reward_accrual_report(now=ts - 6 * 3600)             # 13:00 ET
    assert '"reward_floor_at_risk"' not in capsys.readouterr().out


def test_et_clock_matches_zoneinfo():
    zi = pytest.importorskip("zoneinfo")
    from datetime import datetime, timezone
    try:
        ny = zi.ZoneInfo("America/New_York")
    except Exception:
        pytest.skip("no tzdata")
    for ts in (1773554400.0, 1773568800.0, 1788933600.0, 1793512800.0,
               1794787200.0, 1798761599.0, 1784000000.0):
        loc = datetime.fromtimestamp(ts, timezone.utc).astimezone(ny)
        assert M.et_clock(ts)[:2] == (loc.strftime("%Y-%m-%d"), loc.hour)


def test_reward_first_default_off_keeps_reward_mode_behaviour(reng):
    e, v = reng                                            # REWARDS=1 only
    assert not M.reward_first_on() and M.rewards_on()
    v.bid, v.ask = D("0.44"), D("0.48")
    e.select(v.account())
    e.step_market("aec-rw")
    n0 = v.n
    v.bid, v.ask = D("0.45"), D("0.48")                    # today: 1 tick re-quotes
    e.step_market("aec-rw")
    assert v.n == n0 + 2
    assert not M.Engine.reward_first_quoting(e.b.m("aec-rw"))
    assert "reward_accrual" not in e.b.d


def test_crypto_late_rule_uses_end_date():
    from datetime import datetime, timedelta, timezone
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    assert M.is_late({"type": "crypto-1d", "endDate": soon}).startswith("crypto_last_")
    assert M.is_late({"type": "crypto-1d", "endDate": later}) is None
    assert M.is_late({"type": "crypto-1d", "endDate": "x"}) == "crypto_end_unknown"


def test_crypto_targets_only_explicit_prefixes_never_btc(monkeypatch):
    monkeypatch.setattr(M, "CRYPTO_PREFIXES", ("cpcsol-sol-updown-1d-", "cpc-btc-"))
    v = M.Venue.__new__(M.Venue)
    v.states = {}
    v.gget = lambda path: {"events": [{"markets": [
        {"slug": "cpcsol-sol-updown-1d-2026-11-15-2200z", "endDate": "2026-11-16T22:00:00Z",
         "bestBidQuote": "0.48", "bestAskQuote": "0.50"},
        {"slug": "cpc-btc-updown-1d-2026-11-15-2200z"},
        {"slug": "cpceth-eth-updown-1d-2026-11-15-2200z"}]}]}
    out = v.crypto_targets()
    assert [t["slug"] for t in out] == ["cpcsol-sol-updown-1d-2026-11-15-2200z"]
    assert v.states["cpcsol-sol-updown-1d-2026-11-15-2200z"]["type"] == "crypto-1d"


def test_closed_markets_do_not_count_toward_the_cap(eng):
    e, v = eng
    mk = e.b.m("aec-old")
    mk.update(pos={"YES": "1", "NO": "0"}, cost={"YES": "0.45", "NO": "0"}, done=True)
    live = e.b.m("aec-live")
    live.update(pos={"YES": "0", "NO": "1"}, cost={"YES": "0", "NO": "0.40"})
    assert e.b.total_unmatched() == D("0.40")


@pytest.mark.parametrize("slug,ex", [
    ("aec-testcr-afg-zim-2026-11-13", True), ("aec-odicr-a-b-2026-11-15", True),
    ("aec-t20icr-eln-slr-2026-09-13", True), ("aec-cfb-a-b-2026-11-14", False),
    ("aec-atp-x-y-2026-11-15", False), ("aec-cs2-x-y-2026-11-15", False)])
def test_cricket_excluded(slug, ex):
    assert M.excluded(slug) is ex


def test_both_legs_cancel_in_parallel(eng, monkeypatch):
    import time as _t
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    orig = v.cancel

    def slow(oid):
        _t.sleep(0.3)
        return orig(oid)
    monkeypatch.setattr(v, "cancel", slow)
    t0 = _t.monotonic()
    e._pull("aec-x", "test", t0)
    assert _t.monotonic() - t0 < 0.5                       # not 0.6 sequential
    assert not [o for o in v.orders.values() if o["open"]]


def test_cancel_error_is_raised_after_all_legs_attempted(eng, monkeypatch):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    ids = [k for k, o in v.orders.items() if o["open"]]
    orig = v.cancel

    def flaky(oid):
        if oid == ids[0]:
            raise RuntimeError("venue said no")
        return orig(oid)
    monkeypatch.setattr(v, "cancel", flaky)
    with pytest.raises(RuntimeError):
        e._pull("aec-x", "test", 0.0)
    assert v.orders[ids[1]]["open"] is False                # the other leg still pulled


def test_open_ladder_off_keeps_openings_two_ticks_back(eng, monkeypatch):
    monkeypatch.setattr(M, "OPEN_LADDER", False)
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    e.b.d["markets"]["aec-x"]["pair_set_at"] -= 600
    e.step_market("aec-x")
    live = {o["side"]: o["px"] for o in v.orders.values() if o["open"]}
    assert live == {"YES": D("0.42"), "NO": D("0.52")}



def test_move_away_from_our_legs_does_not_churn(weng):
    e, v, clk = weng
    e.select(v.account())
    v.ws.ingest(frame("aec-x", "0.44", "0.46"))
    e.step_market("aec-x")
    n0 = v.n
    v.ws.ingest(frame("aec-x", "0.45", "0.46"))          # bid up: YES deeper, NO untouched
    e.drain_ws_events()
    assert e.reactive_pulls == 0 and v.n == n0              # nothing cancelled or re-placed
    assert _open(v) == {"YES": D("0.42"), "NO": D("0.52")}


def test_adverse_reasons():
    legs = {"YES": D("0.42"), "NO": D("0.52")}
    assert M.adverse_reasons(legs, {"bid": D("0.44"), "ask": D("0.46")}, T, 2) == []
    assert M.adverse_reasons(legs, {"bid": D("0.43"), "ask": D("0.46")}, T, 2) == ["yes_closer_than_2t"]
    assert "no_closer_than_2t" in M.adverse_reasons(legs, {"bid": D("0.44"), "ask": D("0.47")}, T, 2)
    assert "yes_near_cross" in M.adverse_reasons(legs, {"bid": D("0.40"), "ask": D("0.43")}, T, 2)


def test_orphan_in_our_market_is_adopted_and_cancelled(eng, capsys):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    v.orders["ORPH"] = {"slug": "aec-x", "side": "YES", "px": D("0.40"), "qty": D("1"), "filled": D(0), "open": True}
    e.orphan_sweep([{"id": "ORPH", "marketSlug": "aec-x", "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "outcomeSide": "OUTCOME_SIDE_YES"},
                    {"id": "MAN1", "marketSlug": "aec-x", "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"},
                    {"id": "FOREIGN", "marketSlug": "cpc-btc-x", "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}])
    assert v.orders["ORPH"]["open"] is False
    out = capsys.readouterr().out
    assert "ORPHAN_ADOPTED" in out and "MAN1" not in out and "FOREIGN" not in out


def test_tracked_orders_are_not_orphans(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    ids = [k for k, o in v.orders.items() if o["open"]]
    e.orphan_sweep([{"id": i, "marketSlug": "aec-x", "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"} for i in ids])
    assert all(v.orders[i]["open"] for i in ids)


def test_open_offset_is_cents_not_ticks():
    # 2c under the touch: 2 ticks at a 1c tick, 4 ticks at a half-cent tick
    assert M.depth_ticks(D("0.01")) == 2
    assert M.depth_ticks(D("0.005")) == 4
    p = M.open_prices(D("0.500"), D("0.505"), D("0.005"), cap=D("0.98"))
    assert p == {"YES": D("0.480"), "NO": D("0.475")}


def test_open_offset_zero_restores_depth_ticks(monkeypatch):
    monkeypatch.setattr(M, "OPEN_OFFSET_PX", D("0"))
    assert M.depth_ticks(D("0.005")) == M.DEPTH_TICKS


def test_lone_leg_joins_bid_at_break_even_before_taker(eng, capsys):
    e, v = eng
    v.bid, v.ask = D("0.48"), D("0.50")
    e.select(v.account())
    e.step_market("aec-x")                       # YES 0.46 / NO 0.48
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES" and o["open"])
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market("aec-x")                       # lone YES @0.46
    mk = e.b.d["markets"]["aec-x"]
    v.bid, v.ask = D("0.42"), D("0.44")          # NO bid 0.56 -> pair 1.02
    e.step_market("aec-x")
    live = [o for o in v.orders.values() if o["open"]]
    assert live and live[0]["px"] <= D("0.52")   # still at the pair cap
    mk["lone_since"] -= M.EXIT_S * M.MAKER_PHASE_FRAC + 1
    e.step_market("aec-x"); e.step_market("aec-x")
    live = [o for o in v.orders.values() if o["open"]]
    # bid 0.56 would break even-ness (0.46+0.56>1): capped at 0.54
    assert len(live) == 1 and live[0]["side"] == "NO" and live[0]["px"] == D("0.54")
    assert getattr(v, "taker", []) == []
    assert '"maker_complete_join_bid"' in capsys.readouterr().out


def test_lone_leg_market_does_not_hold_an_opening_slot(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 1)
    monkeypatch.setattr(M, "MAX_TRACKED", 3)
    v.bid, v.ask = D("0.48"), D("0.50")
    e.select(v.account())
    slug = next(iter(e.b.d["markets"]))
    e.step_market(slug)
    assert e.full()
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES" and o["open"])
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market(slug)                       # now a lone leg
    assert e.slots_used() == 0 and not e.full()
    monkeypatch.setattr(M, "MAX_TRACKED", 1)
    assert e.full()                           # hard bound still holds


def test_ten_lot_lone_leg_exits_by_default_within_cap(reng, capsys):
    e, v = reng
    v.bid, v.ask = D("0.44"), D("0.48")
    e.select(v.account())
    e.step_market("aec-rw")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES" and o["open"])
    v.orders[yes].update(filled=D("10"), open=False)
    e.step_market("aec-rw")
    mk = e.b.d["markets"]["aec-rw"]
    mk["lone_since"] -= M.EXIT_S + 1
    v.bid, v.ask = D("0.45"), D("0.46")     # NO ask 0.55 -> 0.99 + fee
    for _ in range(4):
        e.step_market("aec-rw")
    assert '"EXIT_LONE_LEG"' in capsys.readouterr().out


def test_reward_only_selects_no_unrewarded_market(rfeng):
    e, v = rfeng
    assert M.REWARD_ONLY is True                         # default
    e.select(v.account())
    assert set(e.active()) == {"aec-rf1", "aec-rf2"}     # REWARD_MARKETS=2 here
    assert all(e.b.m(s).get("reward") for s in e.active())



# ---------------------------------------- 2c-back reward quotes (2026-11-15)
def test_reward_quotes_rest_two_cents_back_by_default():
    assert M.REWARD_MIN_DEPTH_PX == D("0.02")
    assert M.reward_min_depth(D("0.01")) == 2
    r = M.reward_open_prices(D("0.48"), D("0.50"), D("0.01"), None)
    assert r is not None
    p, d = r
    assert d == 2 and p == {"YES": D("0.46"), "NO": D("0.48")}


def test_reward_first_two_cents_back_quote_and_hold(rfeng, monkeypatch):
    e, v = rfeng
    monkeypatch.setattr(M, "REWARD_MIN_DEPTH_PX", D("0.02"))
    e.select(v.account())
    slug = next(s for s in e.active() if e.b.m(s).get("reward"))
    e.step_market(slug)
    live = {o["side"]: o["px"] for o in v.orders.values()
            if o["open"] and o["slug"] == slug}
    bbo = v.bbo(slug)
    assert live["YES"] == bbo["bid"] - D("0.02")
    assert live["NO"] == (M.ONE - bbo["ask"]) - D("0.02")
    n = len(v.orders)
    e.step_market(slug)                       # unchanged book -> no churn
    assert len(v.orders) == n


def test_abandoned_market_cancels_and_never_replaces(eng, monkeypatch, capsys):
    e, v = eng
    v.bid, v.ask = D("0.48"), D("0.50")
    e.select(v.account())
    slug = next(iter(e.b.d["markets"]))
    e.step_market(slug)
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES" and o["open"])
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market(slug)                                  # completion resting
    assert [o for o in v.orders.values() if o["open"]]
    monkeypatch.setenv("PMUS_TWO_SIDED_ABANDON", slug)
    n = len(v.orders)
    for _ in range(3):
        e.step_market(slug)
    assert not [o for o in v.orders.values() if o["open"]]
    assert len(v.orders) == n and e.b.d["markets"][slug]["done"]
    assert slug not in e.active()
    assert '"market_abandoned"' in capsys.readouterr().out


def test_no_cricket_ever(monkeypatch):
    monkeypatch.delenv("PMUS_TWO_SIDED_ABANDON", raising=False)
    for s in ("aec-testcr-afg-zim-2026-11-13", "aec-ipl-csk-mi-2026-04-01",
              "aec-t20cr-ind-aus-2026-11-20", "aec-bbl-six-str-2026-12-20"):
        assert M.is_cricket(s) and M.excluded(s) and M.abandoned(s), s
    for s in ("aec-cs2-brute-eid-2026-11-15", "aec-atp-a-b-2026-11-15",
              "aec-cfb-tx-lsu-2026-11-14", "aec-nba-det-mia-2026-11-14"):
        assert not M.is_cricket(s) and not M.abandoned(s), s


def test_daily_loss_stop_blocks_new_openings_only(eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "DAILY_LOSS_USD", D("0.10"))
    v.bid, v.ask = D("0.48"), D("0.50")
    e.select(v.account())
    slug = next(iter(e.b.d["markets"]))
    e.step_market(slug)                                  # mark0 ~ 0
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES" and o["open"])
    v.orders[yes].update(filled=D("1"), open=False)
    e.step_market(slug)                                  # lone YES @0.46
    assert not e.daily_stopped()
    v.bid, v.ask = D("0.20"), D("0.22")                  # YES marked at 0.20
    e.step_market(slug)
    assert e.daily_stopped() and e.day_change() < D("-0.10")
    assert '"daily_loss_stop"' in capsys.readouterr().out
    # the completion still rests; a fresh flat market opens nothing
    assert [o for o in v.orders.values() if o["open"] and o["side"] == "NO"]
    mk = e.b.m("aec-new")
    v.bid, v.ask = D("0.48"), D("0.50")
    n = len(v.orders)
    e.step_market("aec-new")
    assert len(v.orders) == n


def test_daily_change_ignores_losses_from_before_today(eng):
    e, v = eng
    mk = e.b.m("aec-old")
    mk["pos"] = {"YES": "10", "NO": "0"}
    mk["cost"] = {"YES": "6.00", "NO": "0"}
    e._mark("aec-old", mk, {"bid": D("0.08"), "ask": D("0.10")})
    assert e.day_change() == 0 and not e.daily_stopped()


# ------------------------------------------- recovery controller (2026-11-15)
@pytest.fixture
def rec(eng, monkeypatch):
    monkeypatch.setattr(M, "RECOVERY_ON", True)
    monkeypatch.setattr(M, "RECOVERY_TAKER", False)
    e, v = eng
    v.bid, v.ask = D("0.48"), D("0.50")
    e.select(v.account())
    slug = next(iter(e.b.d["markets"]))
    e.step_market(slug)                                   # both legs rest
    return e, v, slug


def _fill(v, side, qty="1"):
    oid = next(k for k, o in v.orders.items() if o["side"] == side and o["open"])
    v.orders[oid].update(filled=D(qty), open=False)
    return oid


def _live(v, slug):
    return [o for o in v.orders.values() if o["open"] and o["slug"] == slug]


def test_recovery_yes_fills_no_never_fills_is_worked_continuously(rec, capsys):
    e, v, slug = rec
    _fill(v, "YES")
    for _ in range(3):
        e.step_market(slug)
    live = _live(v, slug)
    assert len(live) == 1 and live[0]["side"] == "NO"
    assert '"RECOVERY_DECISION"' in capsys.readouterr().out
    first = live[0]["px"]
    mk = e.b.m(slug)
    mk["lone_since"] -= 1200                              # it ages
    for _ in range(3):
        e.step_market(slug)
    live = _live(v, slug)
    # aged: the risk cost now pays for stepping inside the NO spread
    assert len(live) == 1 and live[0]["px"] == D("0.51") > first
    assert getattr(v, "taker", []) == []                  # never unauthorized


def test_recovery_no_fills_yes_completion_and_pair_record(rec):
    e, v, slug = rec
    _fill(v, "NO")
    for _ in range(3):
        e.step_market(slug)
    (c,) = _live(v, slug)
    assert c["side"] == "YES"
    _fill(v, "YES")                                        # completion fills
    e.step_market(slug)
    recs = e.b.d["recoveries"]
    assert recs[-1]["outcome"] == "paired" and recs[-1]["slug"] == slug
    m = e.recovery_metrics()
    assert m["paired_pct"] == 100.0 and m["time_to_pair_s_median"] is not None


def test_recovery_taker_only_when_authorized_and_old(rec, monkeypatch):
    e, v, slug = rec
    monkeypatch.setattr(M, "RECOVERY_TAKER", True)
    _fill(v, "YES")
    e.step_market(slug)
    e.b.m(slug)["lone_since"] -= 7200
    for _ in range(4):
        e.step_market(slug)
    assert v.taker and v.taker[-1][0] == "NO"
    assert v.taker[-1][1] == D("0.52")                    # NO ask = 1 - 0.48


def test_recovery_both_sides_partially_filled_need_is_the_difference(rec):
    e, v, slug = rec
    mk = e.b.m(slug)
    for oid in [k for k, o in v.orders.items() if o["open"]]:
        v.orders[oid]["open"] = False
    mk["pos"] = {"YES": "3", "NO": "2"}
    mk["cost"] = {"YES": "1.38", "NO": "1.04"}
    for o in mk["orders"].values():
        o["open"] = False
    for _ in range(3):
        e.step_market(slug)
    (c,) = _live(v, slug)
    assert c["side"] == "NO" and c["qty"] == D("1")      # never above need


def test_recovery_survives_restart_with_unmatched_inventory(rec, tmp_path):
    e, v, slug = rec
    _fill(v, "YES")
    e.step_market(slug)
    since = e.b.m(slug)["lone_since"]
    e2 = M.Engine(v, M.Book(tmp_path / "s.json"))        # same durable book
    assert e2.b.m(slug)["lone_since"] == since
    for _ in range(3):
        e2.step_market(slug)
    assert len(_live(v, slug)) == 1


def test_recovery_external_cancel_is_replaced_once_not_duplicated(rec):
    e, v, slug = rec
    _fill(v, "YES")
    for _ in range(3):
        e.step_market(slug)
    (c,) = [k for k, o in v.orders.items() if o["open"]]
    v.orders[c]["open"] = False                           # watchdog/venue cancel
    for _ in range(3):
        e.step_market(slug)
    assert len(_live(v, slug)) == 1


def test_recovery_status_failure_never_duplicates_exposure(rec):
    e, v, slug = rec
    _fill(v, "YES")
    for _ in range(3):
        e.step_market(slug)
    n = len(v.orders)
    v.status = lambda oid: (_ for _ in ()).throw(ConnectionError("down"))
    with pytest.raises(ConnectionError):
        e.step_market(slug)
    assert len(v.orders) == n                             # nothing new placed


def test_recovery_liquidity_gone_adds_nothing(rec):
    e, v, slug = rec
    _fill(v, "YES")
    for _ in range(3):
        e.step_market(slug)
    n = len(v.orders)
    v.bbo = lambda s: None
    e.step_market(slug)
    assert len(v.orders) == n


def test_halt_new_exposure_blocks_other_first_legs(rec, monkeypatch):
    e, v, slug = rec
    monkeypatch.setattr(M, "UNMATCHED_HALT_USD", D("0.40"))
    _fill(v, "YES")                                       # 0.46 unmatched
    e.step_market(slug)
    assert e.halt_new_exposure()
    e.b.m("aec-other")
    n = len(v.orders)
    e.step_market("aec-other")
    assert len(v.orders) == n


def test_recovery_off_by_default():
    assert M.RECOVERY_ON is False and M.RECOVERY_TAKER is False
