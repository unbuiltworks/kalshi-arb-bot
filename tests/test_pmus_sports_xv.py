"""Paper-only cross-venue signal: edge math, intl book parsing, fully
costed paper marks, token mapping, and the guarantee that it has no order
path at all."""
import json

from src import pmus_sports_two_sided as M
from src import pmus_sports_xv as X


def test_taker_fee_rounds_up_per_contract():
    assert X.taker_fee(0.50) == 0.02          # 0.015 -> 0.02
    assert X.taker_fee(0.99) == 0.01
    assert X.taker_fee(0.10) == 0.01          # 0.0054 -> 0.01


def test_no_edge_when_venues_agree():
    assert X.edge(0.27, 0.28, 0.27, 0.28) is None


def test_yes_edge_only_below_intl_bid_after_fee():
    # live 21:2xZ mia-ind: PMUS YES ask 0.1875 vs intl 0.26/0.27
    e = X.edge(0.17, 0.1875, 0.26, 0.27)
    assert e["side"] == "YES" and e["cost"] == 0.1875
    assert abs(e["edge"] - (0.26 - 0.1875 - 0.01)) < 1e-9
    # inside the fee: no edge
    assert X.edge(0.27, 0.275, 0.28, 0.29) is None


def test_no_edge_mirrors_for_a_rich_pmus_bid():
    # PMUS 0.57/0.5725 vs intl 0.48/0.49 -> buy NO at 1-0.57 = 0.43
    e = X.edge(0.57, 0.5725, 0.48, 0.49)
    assert e["side"] == "NO" and abs(e["cost"] - 0.43) < 1e-9
    assert abs(e["edge"] - (0.51 - 0.43 - 0.02)) < 1e-9


def test_extreme_and_crossed_books_are_ignored():
    assert X.edge(0.98, 0.99, 0.985, 0.995) is None
    assert X.edge(0.50, 0.49, 0.60, 0.61) is None
    assert X.edge(None, 0.5, 0.6, 0.61) is None


def test_intl_book_snapshot_then_price_change():
    t = [100.0]
    b = X.IntlBook(clock=lambda: t[0])
    b.ingest([{"event_type": "book", "asset_id": "A",
               "bids": [{"price": "0.27", "size": "10"},
                        {"price": "0.26", "size": "5"}],
               "asks": [{"price": "0.28", "size": "9"}]}])
    assert b.fresh("A", 5) == {"bid": 0.27, "ask": 0.28, "t": 100.0}
    # level removed, no best_* hint: recomputed from the levels
    b.ingest({"event_type": "price_change", "price_changes": [
        {"asset_id": "A", "price": "0.27", "size": "0", "side": "BUY"}]})
    assert b.fresh("A", 5)["bid"] == 0.26
    # venue's best_bid/best_ask hint wins when present
    b.ingest({"event_type": "price_change", "price_changes": [
        {"asset_id": "A", "price": "0.30", "size": "4", "side": "SELL",
         "best_bid": "0.26", "best_ask": "0.30"}]})
    assert b.fresh("A", 5)["ask"] == 0.30
    t[0] = 200.0
    assert b.fresh("A", 30) is None             # stale intl never trades


def test_paper_marks_include_both_taker_fees_and_close_after_horizons():
    p = X.PaperBook(horizons=(40, 150), cooldown_s=60)
    e = {"side": "YES", "cost": 0.40, "edge": 0.05}
    assert p.maybe_enter("m", e, 0, {}) is not None
    assert p.maybe_enter("m", e, 1, {}) is None           # one per market
    assert p.mark("m", 0.44, 0.45, 10) == []               # before horizon
    r = p.mark("m", 0.44, 0.45, 41)[0]
    # 0.44 - 0.40 - fee(0.40)=0.02 - fee(0.44)=0.02
    assert abs(r["pnl_after_both_fees"] - 0.00) < 1e-9
    r = p.mark("m", 0.50, 0.51, 151)[0]
    assert abs(r["pnl_after_both_fees"] - (0.50 - 0.40 - 0.02 - 0.02)) < 1e-9
    assert "m" not in p.open and p.summary()["h150"]["n"] == 1
    assert p.maybe_enter("m", e, 30, {}) is None           # cooldown
    assert p.maybe_enter("m", e, 200, {}) is not None


def test_paper_no_exit_uses_one_minus_pmus_ask():
    p = X.PaperBook(horizons=(40,))
    p.maybe_enter("m", {"side": "NO", "cost": 0.43, "edge": 0.06}, 0, {})
    r = p.mark("m", 0.48, 0.49, 41)[0]
    assert r["exit"] == 0.51
    assert abs(r["pnl_after_both_fees"] - (0.51 - 0.43 - 0.02 - 0.02)) < 1e-9


def test_token_resolution_requires_same_first_outcome():
    mk = {"slug": "nba-a-b", "outcomes": json.dumps(["Knicks", "Raptors"]),
          "clobTokenIds": json.dumps(["T1", "T2"]), "closed": False}

    def g(path):
        if path.startswith("/markets"):
            return []
        return [{"markets": [mk]}]                 # via the event
    assert X.resolve_token(g, "aec-nba-a-b", ["Knicks", "Raptors"]) == "T1"
    assert X.resolve_token(g, "aec-nba-a-b", ["Raptors", "Knicks"]) is None
    assert X.resolve_token(g, "cpc-btc-updown-5m-x", ["Up"]) is None
    mk["closed"] = True
    assert X.resolve_token(g, "aec-nba-a-b", ["Knicks", "Raptors"]) is None


def test_step_enters_on_fresh_intl_and_logs_paper_only():
    logs = []
    xv = X.XvPaper(lambda: [], lambda p: {"marketData": {
        "bestBid": {"value": "0.17"}, "bestAsk": {"value": "0.1875"}}},
        lambda p: [], lambda k, **kw: logs.append((k, kw)))
    xv.intl.ingest({"event_type": "book", "asset_id": "T",
                    "bids": [{"price": "0.26", "size": "1"}],
                    "asks": [{"price": "0.27", "size": "1"}]})
    import time
    xv.step("aec-nfl-mia-ind", "T", time.time())
    kinds = [k for k, _ in logs]
    assert kinds == ["XV_PAPER_ENTRY"]
    assert logs[0][1]["label"] == "PAPER ONLY: no order placed"


def test_paper_runner_has_no_order_path():
    xv = X.XvPaper(lambda: [], lambda p: {}, lambda p: [], lambda *a, **k: 0)
    for name in ("place", "cancel", "cancel_fast", "v", "venue", "t"):
        assert not hasattr(xv, name)


def test_default_off_and_never_handed_the_venue(monkeypatch):
    monkeypatch.delenv(M.XV_PAPER_ENV, raising=False)
    assert M.start_xv_paper(object()) is None
    seen = {}

    class V:
        def live_sports(self):
            return []

        def pregame_sports(self):
            return []

        def gget(self, path):
            return {}

        def place(self, *a, **k):                   # must never be reached
            raise AssertionError("paper signal placed an order")

    class Fake:
        def __init__(self, targets, pm_get, gamma_get, log, **kw):
            seen["args"] = (targets, pm_get, gamma_get)

        def start(self):
            seen["started"] = True
    monkeypatch.setattr(X, "XvPaper", Fake)
    monkeypatch.setenv(M.XV_PAPER_ENV, "1")
    v = V()
    assert M.start_xv_paper(v) is not None and seen["started"]
    assert all(a is not v for a in seen["args"])
    assert seen["args"][0]() == []
