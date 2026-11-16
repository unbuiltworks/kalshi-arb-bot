"""Kalshi free public data as a second fair-value source (paper only).
Fixtures copy the real shapes read live on 2026-11-16."""
import json
import time

from src import pmus_sports_kalshi as K
from src import pmus_sports_xv as X


def side(long, name, abbr, code, safe=None, desc=None):
    return {"long": long, "description": desc or safe or name,
            "team": {"name": name, "abbreviation": abbr, "safeName": safe,
                     "logo": f"https://static.polymarket.us/images/teams/x/"
                             f"{code}_light_inset6.png"}}


KNICKS = {"marketSides": json.dumps([
    side(True, "New York Knicks", "ny", "NYK", "Knicks"),
    side(False, "Toronto Raptors", "tor", "TOR", "Raptors")]),
    "gameStartTime": "2026-11-15T23:00:00Z"}


def km(ticker, sub, bid="0.30", ask="0.31", status="active"):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "yes_sub_title": sub, "yes_bid_dollars": bid,
            "yes_ask_dollars": ask, "status": status}


NBA = [km("KXNBAGAME-26NOV15NYKTOR-NYK", "New York"),
       km("KXNBAGAME-26NOV15NYKTOR-TOR", "Toronto"),
       km("KXNBAGAME-26NOV15HOUWAS-HOU", "Houston"),
       km("KXNBAGAME-26NOV15HOUWAS-WAS", "Washington")]


def test_dates_and_slugs():
    assert K.kalshi_date("2026-11-15") == "26NOV15"
    assert K.slug_parts("aec-nba-ny-tor-2026-11-15") == {
        "league": "nba", "date": "2026-11-15"}
    assert K.slug_parts("cpc-btc-updown-5m-x") is None


def test_pm_sides_read_codes_from_abbreviation_and_logo():
    s = K.pm_sides(KNICKS)
    assert s[0]["long"] and {"NY", "NYK"} <= s[0]["codes"]
    assert "new york knicks" in s[0]["names"]


def test_nba_matches_although_codes_differ_and_yes_is_the_long_side():
    # PMUS "ny" vs Kalshi "NYK": matched, YES -> the Knicks market
    assert K.match("aec-nba-ny-tor-2026-11-15", KNICKS, NBA) == \
        "KXNBAGAME-26NOV15NYKTOR-NYK"


def test_yes_follows_the_long_flag_not_list_order():
    flipped = {"marketSides": json.dumps([
        side(False, "New York Knicks", "ny", "NYK", "Knicks"),
        side(True, "Toronto Raptors", "tor", "TOR", "Raptors")])}
    assert K.match("aec-nba-ny-tor-2026-11-15", flipped, NBA) == \
        "KXNBAGAME-26NOV15NYKTOR-TOR"


def test_tennis_matches_by_player_name_on_the_real_game_date():
    # live: slug says 11-15 (listing date), match starts 11-16T13:00Z and
    # Kalshi files it under 26NOV16
    pm = {"marketSides": [
        {"long": True, "description": "Carlos Alcaraz", "team": {}},
        {"long": False, "description": "Frances Tiafoe", "team": {}}],
        "gameStartTime": "2026-11-16T13:00:00Z"}
    ks = [km("KXATPMATCH-26NOV16ALCTIA-ALC", "Carlos Alcaraz"),
          km("KXATPMATCH-26NOV16ALCTIA-TIA", "Frances Tiafoe"),
          km("KXATPMATCH-26NOV17ALCMED-ALC", "Carlos Alcaraz"),
          km("KXATPMATCH-26NOV17ALCMED-MED", "Daniil Medvedev")]
    assert K.match("aec-atp-caralc-fratia-2026-11-15", pm, ks) == \
        "KXATPMATCH-26NOV16ALCTIA-ALC"


def test_wrong_date_or_one_unmatched_side_is_left_unmatched():
    assert K.match("aec-nba-ny-tor-2026-11-20",
                   {"marketSides": KNICKS["marketSides"]}, NBA) is None
    other = {"marketSides": json.dumps([
        side(True, "New York Knicks", "ny", "NYK", "Knicks"),
        side(False, "Boston Celtics", "bos", "BOS", "Celtics")]),
        "gameStartTime": "2026-11-15T23:00:00Z"}
    assert K.match("aec-nba-ny-bos-2026-11-15", other, NBA) is None


def test_two_matching_events_is_ambiguous_and_refused():
    pm = {"marketSides": [
        {"long": True, "description": "Carlos Alcaraz", "team": {}},
        {"long": False, "description": "Daniil Medvedev", "team": {}}],
        "gameStartTime": "2026-11-17T13:00:00Z"}
    ks = [km("KXATPMATCH-26NOV17ALCMED-ALC", "Carlos Alcaraz"),
          km("KXATPMATCH-26NOV17ALCMED-MED", "Daniil Medvedev"),
          km("KXATPMATCH-26NOV17ALCMEX-ALC", "Carlos Alcaraz"),
          km("KXATPMATCH-26NOV17ALCMEX-MED", "Daniil Medvedev")]
    assert K.match("aec-atp-caralc-danmed-2026-11-17", pm, ks) is None


def test_unsupported_league_is_never_matched():
    assert K.match("aec-cs2-a-b-2026-11-15", KNICKS, NBA) is None


def test_prices_are_batched_fresh_and_never_crossed():
    calls = []
    t = [1000.0]

    def get(path):
        calls.append(path)
        return {"markets": [km("A", "x", "0.40", "0.42"),
                            km("B", "y", "0.50", "0.50"),       # locked
                            km("C", "z", "0.10", "0.11", "settled")]}
    k = K.KalshiFair(get, clock=lambda: t[0], batch=2)
    k.set_want(["A", "B", "C"])
    k.poll_once()
    assert len(calls) == 2 and calls[0].startswith("/markets?tickers=")
    assert k.fresh("A", 10) == {"bid": 0.40, "ask": 0.42, "t": 1000.0}
    assert k.fresh("B", 10) is None                 # bid == ask: unusable
    assert k.fresh("C", 10) is None                 # not an active market
    t[0] += 30
    assert k.fresh("A", 10) is None                 # stale


def test_poll_errors_are_counted_not_raised():
    def boom(path):
        raise OSError("429")
    k = K.KalshiFair(boom)
    k.set_want(["A"])
    k.poll_once()
    assert k.stats["errors"] == 1


# --------------------------------------------- paper runner, both sources
class FakeKalshi:
    def __init__(self, quote):
        self.q, self.stats = quote, {"polls": 1}

    def resolve(self, slug, m):
        return "KX-YES"

    def fresh(self, ticker, age):
        return self.q

    def set_want(self, t):
        self.want = set(t)


def pm_get_factory(bid, ask):
    def pm_get(path):
        if path.startswith("/v1/markets/"):
            return {"marketData": {"bestBid": {"value": bid},
                                   "bestAsk": {"value": ask}}}
        return {"market": {"outcomes": '["A","B"]', "marketSides": "[]"}}
    return pm_get


def test_kalshi_only_market_is_scored_with_its_source():
    logs = []
    kq = {"bid": 0.26, "ask": 0.27, "t": time.time()}
    xv = X.XvPaper(lambda: [{"slug": "aec-nhl-a-b-2026-11-15"}],
                   pm_get_factory("0.17", "0.1875"), lambda p: [],
                   lambda k, **kw: logs.append((k, kw)),
                   kalshi=FakeKalshi(kq))
    xv.refresh()
    assert xv.kt == {"aec-nhl-a-b-2026-11-15": "KX-YES"} and not xv.tokens
    xv.step("aec-nhl-a-b-2026-11-15", None, time.time())
    ent = [kw for k, kw in logs if k == "XV_PAPER_ENTRY"]
    assert ent and ent[0]["source"] == "kalshi" and ent[0]["side"] == "YES"
    assert xv.stats["k_entries"] == 1 and xv.stats["entries"] == 0


def test_intl_and_kalshi_agreement_is_measured():
    kq = {"bid": 0.45, "ask": 0.46, "t": time.time()}
    xv = X.XvPaper(lambda: [], pm_get_factory("0.45", "0.46"),
                   lambda p: [], lambda *a, **k: 0, kalshi=FakeKalshi(kq))
    xv.kt = {"aec-x": "KX-YES"}
    xv.intl.ingest({"event_type": "book", "asset_id": "T",
                    "bids": [{"price": "0.47", "size": "1"}],
                    "asks": [{"price": "0.48", "size": "1"}]})
    xv.step("aec-x", "T", time.time())
    s = xv.summary()["intl_vs_kalshi"]
    assert s["both_fresh"] == 1 and s["gap_ge_2c"] == 1
    assert abs(s["mean_abs_mid_gap"] - 0.02) < 1e-9


def test_paper_runner_with_kalshi_still_has_no_order_path():
    xv = X.XvPaper(lambda: [], lambda p: {}, lambda p: [], lambda *a, **k: 0,
                   kalshi=K.KalshiFair(lambda p: {}))
    for obj in (xv, xv.kalshi):
        for name in ("place", "cancel", "cancel_fast", "v", "venue", "t"):
            assert not hasattr(obj, name)
