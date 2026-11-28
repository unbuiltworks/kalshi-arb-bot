"""CAMPAIGN_ECONOMICS (PMUS_ECONOMICS_REPORT, default off): trading from
the pilot meter joined with venue reward rows by market, sport and
campaign, with strict realized / not-realized labels. Read-only."""
import json
from datetime import datetime, timezone
from decimal import Decimal as D

from src import pmus_campaign_economics as CE
from src import pmus_pilot_pnl as PP
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    eng)

SINCE = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc).timestamp()
UNTIL = datetime(2026, 11, 26, 2, 0, tzinfo=timezone.utc).timestamp()
ATP = "aec-atp-x-y-2026-11-25"         # settled, paid + skipped reward
UTR = "aec-utr-p-q-2026-11-25"         # paired, open; PENDING reward only
WTA = "aec-wta-r-s-2026-11-25"         # no fills; PAID reward
ITF = "aec-itfwo-u-v-2026-11-25"       # fills not reconciled
OLD = "aec-atp-old-2026-11-20"         # traded before the campaign


def _mk(y, n, cy, cn, filled, px=None, at=SINCE + 60):
    return {"pos": {"YES": str(y), "NO": str(n)},
            "cost": {"YES": str(cy), "NO": str(cn)},
            "orders": {"o1": {"stage": "rn1_open", "placed_at": at,
                              "filled": str(filled)}},
            **({"settle_px": str(px)} if px is not None else {})}


def _trade(slug, q):
    return {"trade": {"marketSlug": slug, "qtyDecimal": str(q),
                      "isAggressor": False,
                      "passiveExecution": {
                          "commissionNotionalCollected": "0"}}}


def _pilot():
    markets = {
        ATP: _mk(10, 10, "4.5", "5.0", 20, px=1),
        UTR: _mk(5, 5, "2.40", "2.40", 10),
        WTA: {"pos": {"YES": "0", "NO": "0"}, "cost": {"YES": "0",
                                                       "NO": "0"},
              "orders": {}},
        ITF: _mk(3, 0, "1.20", "0", 3),
        OLD: _mk(4, 4, "1.9", "1.9", 8, at=SINCE - 86400),
    }
    rows = [_trade(ATP, 20), {"positionResolution": {"marketSlug": ATP}},
            _trade(UTR, 10), _trade(ITF, 1), _trade(OLD, 8)]
    return markets, PP.meter(markets, rows, since=SINCE)


def _reward(slug, usd, status, date="2026-11-25"):
    r = {"marketSlug": slug, "reward": str(usd), "status": status}
    if date:
        r["date"] = date
    return r


REWARDS = [
    _reward(ATP, "0.10", "PAID"),
    _reward(ATP, "0.05", "SKIPPED", "2026-11-26"),
    _reward(ATP, "9.99", "PAID", "2026-11-20"),           # before campaign
    _reward(UTR, "0.30", "PENDING"),
    _reward(WTA, "0.20", "PAID"),
    _reward(ATP, "7.77", "PAID", None),                   # undated
    _reward("aec-nfl-not-ours", "5.00", "PAID"),          # shared account
    _reward(OLD, "3.33", "PAID", "2026-11-27"),           # after window
]


def _run(**kw):
    markets, pilot = _pilot()
    kw.setdefault("until", UNTIL)
    return CE.economics(pilot, REWARDS, since=SINCE,
                        our_markets=list(markets), campaign_id="c1", **kw)


# ------------------------------------------------------------------ pure
def test_join_and_labels():
    r = _run()
    c = r["campaign"]
    assert c["campaign_id"] == "c1" and c["since"].startswith("2026-11-25")
    # ATP: settled 10*1 - 9.5 = +0.50; only realized trading figure
    assert D(c["settled_trading_usd"]) == D("0.5")
    assert D(c["rewards_paid_usd"]) == D("0.30")      # ATP 0.10 + WTA 0.20
    assert D(c["rewards_pending_usd"]) == D("0.30")   # UTR, NOT realized
    assert D(c["rewards_skipped_usd"]) == D("0.05")
    assert D(c["net_realized_usd"]) == D("0.80")      # settled + PAID only
    assert "NOT realized" in r["label"]
    atp = r["by_market"][ATP]
    assert atp["status"] == "settled" and atp["fills"] == 1
    assert atp["contracts"] == "20" and D(atp["spend_usd"]) == D("9.5")
    assert D(atp["pair_edge_usd"]) == D("0.5")
    assert D(atp["net_realized_usd"]) == D("0.60")
    assert atp["reward_dates"] == ["2026-11-25", "2026-11-26"]


def test_a_market_with_pending_reward_only_adds_nothing_to_net():
    r = _run()
    utr = r["by_market"][UTR]
    assert utr["status"] == "paired_flat"
    assert utr["settled_trading_usd"] is None        # not settled
    assert D(utr["rewards_pending_usd"]) == D("0.30")
    assert D(utr["net_realized_usd"]) == 0
    assert "pending_only" in utr["flags"]
    assert UTR in r["flags"]["pending_only"]
    # its cash-basis figure is reported, apart from net
    assert D(utr["realized_cash_basis_usd"]) == D("0.20")


def test_a_paid_reward_on_a_market_without_fills_is_attributed_and_flagged():
    r = _run()
    wta = r["by_market"][WTA]
    assert wta["status"] == "no_campaign_fills"
    assert D(wta["rewards_paid_usd"]) == D("0.20")
    assert D(wta["net_realized_usd"]) == D("0.20")
    assert wta["flags"] == ["reward_without_fills"]
    assert r["by_sport"]["wta"]["fills"] == 0
    assert D(r["by_sport"]["wta"]["rewards_paid_usd"]) == D("0.20")


def test_unconfirmed_fills_are_excluded_and_flagged():
    r = _run()
    itf = r["by_market"][ITF]
    assert itf["status"] == "fills_unconfirmed" and "fills" not in itf
    assert ITF in r["flags"]["unconfirmed"]
    assert r["campaign"]["contracts"] == "30"          # ATP 20 + UTR 10


def test_campaign_scoping():
    r = _run()
    na = r["reward_rows"]["not_attributed"]
    # before-campaign ATP row and after-window OLD row; undated; not ours
    assert na == {"out_of_window": 2, "undated": 1, "other_markets": 1}
    assert D(r["reward_rows"]["other_markets"]["rewards_paid_usd"]) == 5
    assert OLD not in r["by_market"]           # traded before the marker
    assert set(r["by_date"]) == {"2026-11-25", "2026-11-26"}
    # without a window end the later OLD row is in scope (OLD is ours)
    r2 = _run(until=None)
    assert D(r2["by_market"][OLD]["rewards_paid_usd"]) == D("3.33")
    assert r2["by_market"][OLD]["flags"] == ["reward_without_fills"]


def test_by_sport_aggregates():
    r = _run()
    assert set(r["by_sport"]) == {"atp", "utr", "wta", "itfwo"}
    atp = r["by_sport"]["atp"]
    assert atp["markets"] == 1 and D(atp["net_realized_usd"]) == D("0.60")


def test_size_is_bounded():
    markets, pilot = _pilot()
    many = [_reward("aec-atp-m%03d-2026-11-25" % i, "0.01", "PENDING")
            for i in range(300)]
    ours = list(markets) + [x["marketSlug"] for x in many]
    r = CE.economics(pilot, REWARDS + many, since=SINCE, until=UNTIL,
                     our_markets=ours, max_chars=8000)
    assert len(json.dumps(r, default=str)) <= 8000
    assert r["markets_omitted"] > 0
    assert D(r["campaign"]["rewards_pending_usd"]) == D("3.30")   # all summed
    assert ATP in r["by_market"]                # largest kept first
    r = CE.economics(pilot, many, since=SINCE, our_markets=ours,
                     max_markets=5)
    assert len(r["by_market"]) == 5 and r["markets_omitted"] > 0


def test_no_pilot_and_truncated_rows_are_reported():
    r = CE.economics(None, REWARDS, since=SINCE, until=UNTIL,
                     our_markets=[ATP], rows_total=500)
    assert r["trading_available"] is False
    assert r["reward_rows"]["truncated"] is True
    assert D(r["campaign"]["settled_trading_usd"]) == 0
    assert D(r["campaign"]["net_realized_usd"]) == D("0.10")


def test_unknown_status_is_never_realized():
    r = CE.economics(None, [_reward(ATP, "1", "ACCRUED")], since=SINCE,
                     our_markets=[ATP])
    assert D(r["campaign"]["rewards_other_usd"]) == 1
    assert D(r["campaign"]["net_realized_usd"]) == 0


# ------------------------------------------------------------- engine
def _earnings(rows):
    def f(t, detail_max=50):
        return {"ok": True, "endpoint": "/v1/incentives/earnings",
                "rows": len(rows), "credited_usd": "0",
                "accrued_not_credited_usd": "0", "by_status": {},
                "by_program": {}, "rows_detail": rows[:detail_max]}
    return f


def _kinds(capsys, kind):
    pre = "PMUS_TWO_SIDED "
    return [json.loads(x[len(pre):]) for x in
            capsys.readouterr().out.splitlines()
            if x.startswith(pre) and json.loads(x[len(pre):]).get("kind")
            == kind]


def test_off_by_default_nothing_logged_and_reward_check_unchanged(
        eng, monkeypatch, capsys):
    from src import pmus_incentive_audit as IA
    assert M.ECONOMICS_REPORT is False
    e, v = eng
    v.t = object()
    e.b.m(ATP)
    calls = []
    monkeypatch.setattr(IA, "earnings", lambda t: calls.append(t) or
                        _earnings([_reward(ATP, "1", "PAID")] * 60)(t))
    monkeypatch.setattr(e, "account_audit", lambda: True)
    e.audit_round()
    assert len(calls) == 1                         # same call as before
    assert not hasattr(e, "_reward_rows")
    out = capsys.readouterr().out
    assert "CAMPAIGN_ECONOMICS" not in out
    assert '"rows_on_our_markets":50' in out


def test_on_one_line_after_the_audit_round_and_throttled(
        eng, monkeypatch, capsys):
    from src import pmus_incentive_audit as IA
    e, v = eng
    v.t = object()
    markets, pilot = _pilot()
    e.b.d["markets"].update(markets)
    e.since = SINCE
    rows = REWARDS + [_reward(UTR, "0.01", "PENDING")] * 60
    monkeypatch.setattr(M, "ECONOMICS_REPORT", True)
    monkeypatch.setattr(IA, "earnings", _earnings(rows))

    def audit():
        e._pilot, e._pilot_at = pilot, M.time.monotonic()
        return True
    monkeypatch.setattr(e, "account_audit", audit)
    e.audit_round()
    out = capsys.readouterr().out
    lines = [json.loads(x[len("PMUS_TWO_SIDED "):]) for x in
             out.splitlines() if x.startswith("PMUS_TWO_SIDED ")]
    rc = [x for x in lines if x["kind"] == "REWARD_CHECK"][0]
    ce = [x for x in lines if x["kind"] == "CAMPAIGN_ECONOMICS"]
    assert len(ce) == 1 and ce[0]["ok"] is True
    # REWARD_CHECK still reads only the first 50 rows, as before
    assert rc["rows_on_our_markets"] == sum(
        1 for r in rows[:50] if r["marketSlug"] in e.b.d["markets"])
    # the economics line read every row
    assert ce[0]["reward_rows"]["seen"] == len(rows)
    assert D(ce[0]["campaign"]["rewards_pending_usd"]) == D("0.90")
    assert D(ce[0]["campaign"]["settled_trading_usd"]) == D("0.5")
    assert lines.index(rc) < lines.index(ce[0])
    e.audit_round()                                 # within the interval
    assert _kinds(capsys, "CAMPAIGN_ECONOMICS") == []


def test_on_no_line_when_the_reward_read_failed(eng, monkeypatch, capsys):
    from src import pmus_incentive_audit as IA
    e, v = eng
    v.t = object()
    monkeypatch.setattr(M, "ECONOMICS_REPORT", True)

    def boom(t, detail_max=50):
        raise RuntimeError("down")
    monkeypatch.setattr(IA, "earnings", boom)
    monkeypatch.setattr(e, "account_audit", lambda: True)
    e.audit_round()
    assert _kinds(capsys, "CAMPAIGN_ECONOMICS") == []


def test_the_report_has_no_trading_effect(eng, monkeypatch):
    e, v = eng
    markets, pilot = _pilot()
    e.b.d["markets"].update(markets)
    e.since = SINCE
    e._pilot = pilot
    e._reward_rows = (M.time.monotonic(), True, REWARDS, len(REWARDS))
    before = json.dumps(e.b.d, sort_keys=True, default=str)
    block = e.pilot_block()
    monkeypatch.setattr(M, "ECONOMICS_REPORT", True)
    assert e.economics_report(True, True, force=True) is not None
    assert json.dumps(e.b.d, sort_keys=True, default=str) == before
    assert e.pilot_block() == block
