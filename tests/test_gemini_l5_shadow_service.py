import json
from datetime import datetime, timezone

from src import gemini_l5_shadow_service as svc
from src import gemini_l5_shadow_v2 as gs
from src.polymarket_l5 import transfer_backtest as tb


def _scripted(now0):
    """The NO touch (1 - ask) moves every poll, so the live shell burns its
    6 actions on cancels within ~18s; a buy print 40s after entry fills a
    NO join that simply rests (rest_60s)."""
    sym = gs.nearest_windows(datetime.fromtimestamp(now0, timezone.utc))[0]
    exp = tb.ticker_expiry_epoch(tb.event_of_symbol(sym))
    state: dict = {}

    def fetch(url):
        key = url.split("?")[0].split("/v1/")[-1]      # book/SYM, trades/SYM
        state[key] = state.get(key, 0) + 1
        if "/v1/book/" in url:
            ask = "0.55" if state[key] % 2 else "0.56"
            return {"bids": [{"price": "0.48", "amount": "300"}],
                    "asks": [{"price": ask, "amount": "300"}]}
        if "/v1/trades/" in url:
            return [{"tid": 2, "timestampms": int((exp - 255) * 1000),
                     "price": "0.60", "amount": "5", "type": "buy"}]
        raise RuntimeError("unexpected " + url)
    return sym, exp, fetch


def test_service_collects_replays_on_expiry_and_persists(tmp_path):
    now0 = 1788638000.0                          # inside a 5m window
    sym, exp, fetch = _scripted(now0)
    t = {"v": exp - 295.0}                       # start 295s before expiry
    def clock():
        return t["v"]
    def sleep(s):
        t["v"] += s
    s = svc.ShadowService(fetch=fetch, clock=clock, sleep=sleep, root=tmp_path)
    s.run(until=exp + 40.0)
    recs = [json.loads(l) for l in (tmp_path / "records.jsonl").read_text().splitlines()]
    assert recs and {r["shell"] for r in recs} == set(gs.SHELLS)
    assert all(r["orders_placed"] == 0 for r in recs)
    assert (tmp_path / "tape.jsonl").exists() and (tmp_path / "summary.json").exists()
    by = {f"{r['shell']}:{r['policy']}": r for r in recs if r["symbol"] == sym}
    assert by["touch_cancel_30s:modal"]["fills"] == []          # cancelled on move
    assert by["rest_60s:modal"]["fills"], "bounded rest lets the print through"
    st = s.status()
    assert st["orders_placed"] == 0 and "rest_60s:modal" in st["per_shell"]
    # restart: records are restored into the cumulative summaries
    s2 = svc.ShadowService(fetch=fetch, clock=clock, sleep=sleep, root=tmp_path)
    assert s2.status()["per_shell"]["rest_60s:modal"]["campaigns"] >= 1


def test_fetch_errors_are_counted_and_never_stop_the_loop(tmp_path):
    def bad(url):
        raise RuntimeError("blocked")
    t = {"v": 1788638000.0}
    s = svc.ShadowService(fetch=bad, clock=lambda: t["v"],
                          sleep=lambda x: t.__setitem__("v", t["v"] + x),
                          root=tmp_path)
    s.run(until=t["v"] + 10.0)
    assert s.errors >= 2 and s.status()["orders_placed"] == 0


def test_image_has_no_credential_or_order_path():
    src = open("src/gemini_l5_shadow_service.py").read()
    for banned in ("gemini_prediction_auth", "/v1/order/new", "API_KEY",
                   "pmus_spring", "pmus_l5_continuous", "place_limit"):
        assert banned not in src
    docker = open("deploy/gemini_l5_shadow/Dockerfile").read()
    assert "pmus_spring_l5_session" not in docker and "PMUS_API" not in docker
