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


def test_rolling_report_pairs_arms_and_reads_the_frozen_gate(tmp_path):
    now0 = 1788638000.0
    sym, exp, fetch = _scripted(now0)
    t = {"v": exp - 295.0}
    s = svc.ShadowService(fetch=fetch, clock=lambda: t["v"],
                          sleep=lambda x: t.__setitem__("v", t["v"] + x),
                          root=tmp_path)
    s.run(until=exp + 40.0)
    rep = s.rolling_report(t["v"])
    arms = rep["by_window"]["cumulative"]
    assert "touch_cancel_30s:modal" in arms and "rest_60s:modal" in arms
    assert arms["rest_60s:modal"]["vs_live_shell"]["first_leg_fills"] == "1"
    assert "vs_live_shell" not in arms["touch_cancel_30s:modal"]
    assert arms["rest_60s:modal"]["adverse_selection_by_horizon"]["30"]["n"] == 1
    rec = [r for r in s.rows["rest_60s:modal"] if r["symbol"] == sym][0]
    assert rec["optimistic_unknown"]["labels"].startswith("at-price")
    g = rep["transfer_gate"]
    assert g["primary_arm"] == "reprice_180s:modal"
    assert g["verdict"]["verdict"] == "INSUFFICIENT_SAMPLE"
    assert g["verdict"]["gate_sha"] == gs.load_gate()["_sha256_16"]


def test_transfer_gate_thresholds_come_only_from_the_frozen_file():
    gate = gs.load_gate()
    assert gate["frozen_before_any_live_shadow_result"] is True
    assert gate["requirements"]["min_windows"] == 300
    # a synthetic arm that trips the reject rule
    rows = [{"status": "RESIDUAL_HOLD", "entry_t": 1788638000.0 + i * 300,
             "inventory": {"matched": "0", "up": "1", "dn": "0", "residual": "1"},
             "pair_pnl_theoretical": None, "hold_pnl_theoretical": "-0.01",
             "decisions": [], "orders": [], "fills": [], "flow": {"two_sided": False},
             "pair_unit_cost": "None", "fees_theoretical": "0", "outcome_proxy": "DOWN",
             "entry_book": None} for i in range(300)]
    v = gs.transfer_gate_verdict(rows, gate)
    assert v["verdict"] == "REJECT" and v["checks"]["net_after_fees_usd"]["ok"] is False


def test_replay_error_becomes_a_record_not_a_crash(tmp_path, monkeypatch):
    now0 = 1788638000.0
    sym, exp, fetch = _scripted(now0)
    t = {"v": exp - 295.0}
    s = svc.ShadowService(fetch=fetch, clock=lambda: t["v"],
                          sleep=lambda x: t.__setitem__("v", t["v"] + x),
                          root=tmp_path)
    def boom(*a, **k):
        raise TypeError("conversion from NoneType to Decimal is not supported")
    monkeypatch.setattr(gs, "replay_window", boom)
    s.run(until=exp + 40.0)
    recs = [json.loads(l) for l in (tmp_path / "records.jsonl").read_text().splitlines()]
    assert recs and all(r["status"] == "REPLAY_ERROR" for r in recs)
    assert (tmp_path / "tape.jsonl").exists()
    assert s.status()["orders_placed"] == 0
