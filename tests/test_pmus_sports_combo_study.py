"""Read-only PMUS combo (parlay) study: fair value, fees, flow, no order path."""
from src import pmus_sports_combo_study as CS
from src import pmus_sports_two_sided as M

# the pricing tests below use fixed historical print times and want every
# print processed; sampling/lag behaviour has its own tests at the end
CS.LIVE_SAMPLE = 1
CS.MAX_MID_LAG_S = 1e12


def test_published_combo_fee_formulas():
    # docs.polymarket.us/fees: 100 lots at $0.10 -> maker $0.27
    assert round(CS.combo_maker_fee(0.10, 100), 2) == 0.27
    # Tier 4 theta 0.0695, 100 lots at $0.10 -> taker $1.02 (published table)
    assert round(CS.combo_taker_fee(0.10, 100, theta=0.0695), 2) == 1.02
    assert round(CS.combo_taker_fee(0.30, 100, theta=0.0695), 2) == 1.89


def test_event_key_groups_markets_of_one_game():
    assert CS.event_key("aec-nba-ny-tor-2026-11-15") == "nba-ny-tor-2026-11-15"
    assert CS.event_key("asc-nba-ny-tor-2026-11-15-pos-3pt5") == \
        "nba-ny-tor-2026-11-15"


def test_fair_value_is_the_product_of_leg_prices_with_sell_legs_inverted():
    legs = [{"symbol": "a", "side": "SIDE_BUY"},
            {"symbol": "b", "side": "SIDE_SELL"}]
    assert abs(CS.fair_value(legs, {"a": 0.6, "b": 0.3}) - 0.6 * 0.7) < 1e-12
    assert CS.fair_value(legs, {"a": 0.6, "b": None}) is None


def test_same_game_flag():
    assert CS.same_game([{"symbol": "aec-nba-ny-tor-2026-11-15"},
                         {"symbol": "asc-nba-ny-tor-2026-11-15-pos-3pt5"}])
    assert not CS.same_game([{"symbol": "aec-nba-ny-tor-2026-11-15"},
                             {"symbol": "aec-nhl-edm-phi-2026-11-15"}])


def fake_get(trades, rfqs=(), combos=None):
    combos = combos or {}

    def get(path):
        if path.startswith("/v1/rfqs/trades"):
            return {"trades": list(trades)}
        if path.startswith("/v1/rfqs"):
            return {"rfqs": list(rfqs)}
        if path.startswith("/v1/combos?symbol="):
            sym = path.split("=", 1)[1]
            return {"combos": [{"legs": combos[sym]}]} if sym in combos \
                else {"combos": []}
        raise AssertionError(path)
    return get


def pm_get(mids):
    def g(path):
        sym = path.split("/")[3]
        m = mids[sym]
        return {"marketData": {"bestBid": {"value": str(m - 0.005)},
                               "bestAsk": {"value": str(m + 0.005)}}}
    return g


def test_combo_print_is_priced_against_its_legs_and_logged():
    legs = [{"symbol": "aec-nba-a-b-2026-11-15", "side": "SIDE_BUY"},
            {"symbol": "aec-nhl-c-d-2026-11-15", "side": "SIDE_BUY"}]
    logs = []
    cs = CS.ComboStudy(
        fake_get([{"tradeId": "t1", "symbol": "combo-1", "price": "0.30",
                   "qtyDecimal": "10", "executedTime":
                       "2026-11-16T01:00:00Z"},
                  {"tradeId": "t2", "symbol": "aec-nba-a-b-2026-11-15",
                   "price": "0.5", "qtyDecimal": "1",
                   "executedTime": "2026-11-16T01:00:01Z"}],
                 combos={"combo-1": legs}),
        pm_get({"aec-nba-a-b-2026-11-15": 0.5,
                "aec-nhl-c-d-2026-11-15": 0.5}),
        lambda k, **kw: logs.append((k, kw)))
    cs.poll_trades()
    rec = [kw for k, kw in logs if k == "COMBO_STUDY_TRADE"]
    assert len(rec) == 1                       # the single-market fill is not
    r = rec[0]
    assert r["legs"] == 2 and abs(r["fair_now"] - 0.25) < 1e-9
    assert abs(r["maker_margin"] - 0.05) < 1e-9
    assert abs(r["maker_margin_after_fee"] -
               (0.05 - CS.combo_maker_fee(0.30))) < 1e-4
    s = cs.summary()
    assert s["combo_trades"] == 1 and s["trades"] == 2
    cs.poll_trades()                           # same prints: not re-counted
    assert cs.summary()["trades"] == 2


def test_rfq_flow_counts_legs_and_same_game():
    legs = [{"symbol": "aec-nba-ny-tor-2026-11-15", "side": "SIDE_BUY"},
            {"symbol": "asc-nba-ny-tor-2026-11-15-pos-3pt5",
             "side": "SIDE_BUY"}]
    logs = []
    cs = CS.ComboStudy(fake_get([], rfqs=[{"id": "r1", "symbol": "c",
                                           "comboLegs": legs}]),
                       pm_get({}), lambda k, **kw: logs.append((k, kw)))
    cs.poll_rfqs()
    cs.poll_rfqs()
    s = cs.summary()
    assert s["rfqs"] == 1 and s["rfq_legs"] == {"2": 1}
    assert s["rfq_same_game"] == 1


def test_unreadable_endpoints_are_recorded_not_raised():
    def boom(path):
        raise PermissionError("403 not enabled for RFQ")
    cs = CS.ComboStudy(boom, pm_get({}), lambda *a, **k: 0)
    cs.poll_rfqs()
    cs.poll_trades()
    assert "rfqs" in cs.summary()["errors"]
    assert "trades" in cs.summary()["errors"]


def test_study_has_no_order_path_and_is_off_by_default(monkeypatch):
    cs = CS.ComboStudy(lambda p: {}, lambda p: {}, lambda *a, **k: 0)
    for name in ("place", "cancel", "quote", "accept", "v", "venue", "t"):
        assert not hasattr(cs, name)
    monkeypatch.delenv(M.COMBO_STUDY_ENV, raising=False)
    assert M.start_combo_study(object()) is None


def test_unreadable_leg_is_unpriced_not_fatal():
    # live 11:46Z: a leg BBO read raised and killed the study thread
    legs = [{"symbol": "aec-gone-a-b-2026-11-15", "side": "SIDE_BUY"},
            {"symbol": "aec-nhl-c-d-2026-11-15", "side": "SIDE_BUY"}]

    def pm(path):
        if "gone" in path:
            raise RuntimeError("404 market not found")
        return {"marketData": {"bestBid": {"value": "0.49"},
                               "bestAsk": {"value": "0.51"}}}
    logs = []
    cs = CS.ComboStudy(
        fake_get([{"tradeId": "t9", "symbol": "c9", "price": "0.2",
                   "qtyDecimal": "1", "executedTime": "2026-11-16T11:46:00Z"}],
                 combos={"c9": legs}),
        pm, lambda k, **kw: logs.append((k, kw)))
    cs.poll_trades()
    rec = [kw for k, kw in logs if k == "COMBO_STUDY_TRADE"][0]
    assert rec["fair_now"] is None and "leg_bbo" in cs.summary()["errors"]


# ------------------------------------------- settlement scoring (11-17)
from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)
A, B = "aec-nba-a-b-2026-11-17", "aec-nhl-c-d-2026-11-17"
LEGS = [{"symbol": A, "side": "SIDE_BUY"}, {"symbol": B, "side": "SIDE_SELL"}]


def settle_get(final, mids=None):
    """Gateway BBO: a resolved leg carries state + settlementPx."""
    def g(path):
        sym = path.split("/")[3]
        if sym in final:
            return {"marketData": {"marketSlug": sym,
                                   "state": "MARKET_STATE_EXPIRED",
                                   "settlementPx": {"value": str(final[sym])}}}
        m = (mids or {}).get(sym, 0.5)
        return {"marketData": {"marketSlug": sym, "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": str(m - 0.005)},
                               "bestAsk": {"value": str(m + 0.005)}}}
    return g


def study(trades, pm, tmp_path=None, **kw):
    logs = []
    cs = CS.ComboStudy(fake_get(trades, combos={"c1": LEGS, "c2": LEGS}), pm,
                       lambda k, **k2: logs.append((k, k2)),
                       state_dir=tmp_path, signed_rps=0, now=lambda: NOW,
                       sleep=lambda s: None, **kw)
    return cs, logs


def tr(tid, sym="c1", px="0.30", qty="10", agg="SIDE_BUY"):
    return {"tradeId": tid, "symbol": sym, "price": px, "qtyDecimal": qty,
            "aggressorSide": agg, "executedTime": "2026-11-17T12:59:30Z"}


def test_maker_margin_sign_follows_the_aggressor():
    assert CS.maker_sign("SIDE_BUY") == 1 and CS.maker_sign("SIDE_SELL") == -1
    cs, logs = study([tr("s1", px="0.30", agg="SIDE_SELL")],
                     settle_get({}, {A: 0.5, B: 0.5}))
    cs.poll_trades()
    r = [k2 for k, k2 in logs if k == "COMBO_STUDY_TRADE"][0]
    # fair 0.5 * (1 - 0.5) = 0.25; maker BOUGHT at 0.30 -> -0.05
    assert abs(r["maker_margin"] + 0.05) < 1e-9


def test_combo_payout_early_loss_full_win_and_void():
    assert CS.combo_payout(LEGS, {A: 0.0}) == 0.0          # lost leg: done
    assert CS.combo_payout(LEGS, {A: 1.0}) is None         # B still open
    assert CS.combo_payout(LEGS, {A: 1.0, B: 0.0}) == 1.0  # sell leg won
    assert CS.combo_payout(LEGS, {A: 1.0, B: 1.0}) == 0.0
    assert CS.combo_payout(LEGS, {A: 0.5, B: 0.0}) == "void"


def test_print_is_scored_at_settlement_and_survives_a_restart(tmp_path):
    cs, _ = study([tr("t1"), tr("t2", sym="c2", px="0.20")],
                  settle_get({}), tmp_path)
    cs.poll_trades()
    assert cs.settle_pass() == 0 and len(cs.pending) == 2   # games open
    # restart: pending prints come back from disk, not re-counted
    cs2, _ = study([tr("t1")], settle_get({A: 1.0, B: 0.0}), tmp_path)
    assert len(cs2.pending) == 2
    cs2.poll_trades()
    assert cs2.agg["combo_trades"] == 0
    assert cs2.settle_pass() == 2
    s = cs2.settled_summary()["all"]
    # taker won both: maker -> (0.30-1) - fee(0.30), (0.20-1) - fee(0.20)
    exp = [(0.30 - 1) - CS.combo_maker_fee(0.30),
           (0.20 - 1) - CS.combo_maker_fee(0.20)]
    assert s["n"] == 2 and s["won_by_taker"] == 2
    assert abs(s["mean_c"] - round(sum(exp) / 2 * 100, 3)) < 1e-6
    assert abs(s["usd_pnl"] - round(10 * sum(exp), 2)) < 1e-9
    cs3, _ = study([], settle_get({}), tmp_path)
    assert not cs3.pending and len(cs3.settled) == 2


def test_lost_leg_settles_the_maker_a_win_without_waiting(tmp_path):
    cs, _ = study([tr("t1")], settle_get({A: 0.0}), tmp_path)
    cs.poll_trades()
    assert cs.settle_pass() == 1
    r = cs.settled[0]
    assert r["payout"] == 0.0
    assert abs(r["maker_pnl"] - (0.30 - CS.combo_maker_fee(0.30))) < 1e-6


def test_a_full_page_is_split_so_a_busy_minute_is_not_truncated():
    calls = []

    def g(path):
        calls.append(path)
        if path.startswith("/v1/rfqs/trades"):
            q = dict(x.split("=") for x in path.split("?")[1].split("&"))
            s = datetime.fromisoformat(q["startTime"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(q["endTime"].replace("Z", "+00:00"))
            n = int((e - s).total_seconds() * 3)       # 3 prints a second
            return {"trades": [{"tradeId": f"{s.timestamp()}-{i}",
                                "executedTime": CS._iso(s)}
                               for i in range(min(n, 100))]}
        raise AssertionError(path)
    cs = CS.ComboStudy(g, lambda p: {}, lambda *a, **k: 0, signed_rps=0)
    rows = cs.fetch_window(NOW - timedelta(minutes=1), NOW)
    assert len(rows) == 180 and len(calls) == 3       # 60s full -> 2x30s


def test_ignored_end_time_stops_splitting_and_backfill():
    calls = []

    def g(path):
        calls.append(path)
        return {"trades": [{"tradeId": str(i),
                            "executedTime": "2026-11-17T13:00:00Z"}
                           for i in range(100)]}
    logs = []
    cs = CS.ComboStudy(g, lambda p: {}, lambda k, **kw: logs.append((k, kw)),
                       signed_rps=0, now=lambda: NOW)
    cs.fetch_window(NOW - timedelta(hours=1), NOW - timedelta(minutes=30))
    assert len(calls) == 1 and cs.notes["endTime_ignored"]
    cs.backfill()
    assert logs[-1][1]["why"] == "endTime_ignored_by_venue"


def test_backfill_sample_is_deterministic_and_unbiased_by_price():
    ids = [f"t{i}" for i in range(5000)]
    picked = [i for i in ids if CS.in_sample(i, 10)]
    assert picked == [i for i in ids if CS.in_sample(i, 10)]
    assert 400 < len(picked) < 600
    assert all(CS.in_sample(i, 1) for i in ids[:10])


def test_signed_reads_are_rate_limited():
    t, slept = [0.0], []

    def sleep(s):
        slept.append(s)
        t[0] += s
    b = CS.Bucket(2.0, clock=lambda: t[0], sleep=sleep)
    for _ in range(5):
        b.take()
    assert abs(sum(slept) - 2.0) < 1e-9                # 5 reads at 2/s


def test_executed_time_with_nanoseconds_parses():
    assert CS._ts("2026-11-17T12:49:43.413944485Z") == datetime(
        2026, 11, 17, 12, 49, 43, 413944, tzinfo=timezone.utc)


def test_live_prints_are_sampled_and_late_prints_are_not_priced(monkeypatch):
    monkeypatch.setattr(CS, "MAX_MID_LAG_S", 60.0)
    trades = [tr(f"t{i}") for i in range(200)]
    cs, logs = study(trades, settle_get({}, {A: 0.5, B: 0.5}),
                     live_sample=2)
    cs.poll_trades()
    done = [k2 for k, k2 in logs if k == "COMBO_STUDY_TRADE"]
    assert 60 < len(done) < 140                              # ~half
    assert cs.agg["live_unsampled"] == 200 - len(done)
    assert all(r["fair_now"] is not None for r in done)      # 30 s old
    late = dict(tr("late"), executedTime="2026-11-17T12:50:00Z")
    cs2, logs2 = study([late], settle_get({}, {A: 0.5, B: 0.5}),
                       live_sample=1)
    cs2.poll_trades()
    r = [k2 for k, k2 in logs2 if k == "COMBO_STUDY_TRADE"][0]
    assert r["fair_now"] is None and cs2.agg["too_late_to_price"] == 1
    assert len(cs2.pending) == 1             # still scored at settlement


def test_backfill_reads_never_draw_on_the_live_budget():
    cs = CS.ComboStudy(lambda p: {}, lambda p: {}, lambda *a, **k: 0,
                       signed_rps=1.5)
    assert abs(cs.bucket.rps - 1.2) < 1e-9 and abs(cs.bf_bucket.rps - 0.3) \
        < 1e-9
    taken = []
    cs.bucket.take = lambda: taken.append("live")
    cs.bf_bucket.take = lambda: taken.append("bf")
    cs.get("/x")
    import threading
    th = threading.Thread(target=lambda: (setattr(cs._tl, "backfill", True),
                                          cs.get("/y")))
    th.start()
    th.join()
    assert taken == ["live", "bf"]


def test_only_hours_with_nothing_pending_count_as_complete(tmp_path):
    t_old = dict(tr("o1"), executedTime="2026-11-17T11:10:00Z")
    t_old2 = dict(tr("o2", sym="c2"), executedTime="2026-11-17T11:50:00Z")
    t_mid = dict(tr("m1"), executedTime="2026-11-17T12:20:00Z")
    cs, _ = study([t_old, t_old2, t_mid], settle_get({}), tmp_path)
    for t in (t_old, t_old2, t_mid):
        cs._ingest(t, live=False)
    # the 12Z print's legs settle (maker wins early); 11Z still open
    cs.pending["m1"]["legs"] = [{"symbol": "aec-x-y-z-2026-11-17",
                                 "side": "SIDE_BUY"}]
    cs.leg_px["aec-x-y-z-2026-11-17"] = 0.0
    cs.settle_pass()
    c = cs.cohorts()
    assert c["by_hour_recorded_settled_pending"] == {
        "2026-11-17T11Z": [2, 0, 2], "2026-11-17T12Z": [1, 1, 0]}
    assert c["complete_hours"] == ["2026-11-17T12Z"]
    assert c["complete"]["all"]["n"] == 1
    # 11Z settles too -> both hours complete
    cs.leg_px.update({A: 1.0, B: 0.0})
    cs.settle_pass()
    c = cs.cohorts()
    assert c["complete_hours"] == ["2026-11-17T11Z", "2026-11-17T12Z"]
    assert c["complete"]["all"]["n"] == 3
    assert "cohorts" in cs.summary()


def test_the_current_hour_is_never_complete(tmp_path):
    t = dict(tr("n1"), executedTime="2026-11-17T13:00:00Z")   # NOW's hour
    cs, _ = study([], settle_get({A: 0.0}), tmp_path)
    cs._ingest(t, live=False)
    cs.settle_pass()
    assert cs.cohorts()["complete_hours"] == []


def test_live_starts_at_now_not_in_the_past():
    cs, _ = study([], settle_get({}))
    assert cs.since == NOW
