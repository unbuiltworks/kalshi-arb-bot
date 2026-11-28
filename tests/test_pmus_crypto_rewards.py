"""Daily-crypto reward lane (2026-11-22): venue scoring rule, quoting,
arming, crypto limits, the combined sports + crypto ceiling, accounting,
and the failure / restart cases. No network, no orders."""
import importlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
from src import pmus_crypto_rewards as CR  # noqa: E402
from src import pmus_rn1_replica as RN1  # noqa: E402
from src import pmus_sports_two_sided as M  # noqa: E402
from tests.test_pmus_sports_two_sided import FakeVenue  # noqa: E402

T = D("0.01")
ETH = "cpceth-eth-updown-1d-2026-11-21-2200z"
SOL = "cpcsol-sol-updown-1d-2026-11-21-2200z"
XRP = "cpcxrp-xrp-updown-1d-2026-11-21-2200z"
BTC = "cpc-btc-updown-1d-2026-11-21-2200z"
SHA = "0123456789abcdef0123456789abcdef01234567"
PROG = {"programId": "crypto_alt_updown_1d_20261110", "pool": 150.0,
        "period": "daily_event", "df": 0.3, "targetSize": 2000.0,
        "maxSpread": 0.05, "period_s": 86400.0, "M_program": 3}


def book(bids, offers):
    def rows(lv):
        return [{"px": {"value": str(p)}, "qty": str(q)} for p, q in lv]
    return {"bids": rows(bids), "offers": rows(offers)}


#: 1c market, 300 at each best price, target reached two ticks back
DEEP = book([(0.48, 300), (0.47, 900), (0.46, 1500)],
            [(0.50, 300), (0.51, 900), (0.52, 1500)])
#: SOL as served 2026-11-22: the best bid alone holds Target Size
SOL_LIVE = book([(0.30, 2000), (0.28, 25), (0.27, 25.66), (0.26, 96)],
                [(0.32, 229), (0.33, 25), (0.37, 1999.34), (0.38, 2000)])


def around(bid, ask):
    """A deep 2c-wide book with its best prices at bid / ask."""
    b, a = round(bid * 100), round(ask * 100)
    return book([(b / 100, 300), ((b - 1) / 100, 900), ((b - 2) / 100, 1500)],
                [(a / 100, 300), ((a + 1) / 100, 900), ((a + 2) / 100, 1500)])


def iso(seconds_from_now):
    return (datetime.now(timezone.utc) +
            timedelta(seconds=seconds_from_now)).isoformat()


# ------------------------------------------------------------ venue rule

def test_documented_scores_discount_per_tick():
    """Docs example: DF 0.30, 1,000 each at best / +1 / +2 / +3 ticks ->
    best earns 1000/1417, the order 3 ticks back 27/1417."""
    bk = book([(0.50, 1000), (0.49, 1000), (0.48, 1000)], [(0.52, 5000)])
    prog = {**PROG, "targetSize": 4000, "maxSpread": None}
    a = CR.assess(bk, prog, T, [("YES", D("0.47"), 1000)])
    assert a["bid_share"] == pytest.approx(27 / 1417)
    assert a["yes_ticks_from_best"] == 3


def test_one_tick_behind_scores_nothing_when_best_holds_target_size():
    """Live SOL book: 2,000 at the best bid = Target Size, so the walk ends
    there and a bid one tick back is outside it. The offer side walks to
    0.37, so an offer one tick back does score."""
    a = CR.assess(SOL_LIVE, PROG, T, [("YES", D("0.29"), 100),
                                      ("NO", D("0.67"), 100)])
    assert a["pays"] and a["bid_adj_px"] == 0.30 and a["ask_adj_px"] == 0.37
    assert a["yes_inside"] is False and a["bid_share"] == 0
    assert a["no_inside"] is True and a["no_ticks_from_best"] == 1


def test_max_spread_is_measured_between_the_two_size_adjusted_prices():
    """Docs: Max Spread 3.5c, target 1,000. 49/51 pays; 49/57 does not;
    47/54 (exactly 7c apart) pays."""
    prog = {**PROG, "targetSize": 1000, "maxSpread": 0.035}
    q = [("YES", D("0.48"), 10)]
    assert CR.assess(book([(0.49, 1000)], [(0.51, 1000)]), prog, T, q)["pays"]
    bad = CR.assess(book([(0.49, 1000)], [(0.57, 1000)]), prog, T, q)
    assert not bad["pays"] and bad["why_not"] == "max_spread"
    assert bad["share"] == 0
    assert CR.assess(book([(0.47, 1000)], [(0.54, 1000)]), prog, T, q)["pays"]


def test_a_side_short_of_target_size_forfeits_the_second():
    a = CR.assess(book([(0.48, 500)], [(0.50, 3000)]), PROG, T,
                  [("NO", D("0.50"), 100)])
    assert not a["pays"] and a["why_not"] == "target_size_not_met"
    assert a["rate_usd_s"] == 0


def test_our_resting_size_is_counted_once():
    """Our 100 already shows in the public book at 0.47: it is removed and
    added back, not doubled."""
    with_us = book([(0.48, 300), (0.47, 1000), (0.46, 1500)],
                   [(0.50, 300), (0.51, 900), (0.52, 1500)])
    q = [("YES", D("0.47"), 100)]
    assert CR.assess(with_us, PROG, T, q, resting=q)["bid_share"] == \
        pytest.approx(CR.assess(DEEP, PROG, T, q)["bid_share"])


def test_modelled_rate_splits_the_shared_pool_evenly():
    a = CR.assess(DEEP, PROG, T, [("YES", D("0.47"), 100),
                                  ("NO", D("0.49"), 100)])
    assert a["usd_day"] == pytest.approx(150 / 3 * a["share"])
    assert "MODELLED" in a["label"]


# ------------------------------------------------------------ quoting

def Q(bk=DEEP, pos=None, cost=None, **kw):
    c = kw.pop("c", None)
    return CR.quotes(book_md=bk, program=kw.pop("program", PROG), tick=T,
                     pos=pos or {"YES": "0", "NO": "0"},
                     cost=cost or {"YES": "0", "NO": "0"}, c=c, **kw)


def test_flat_market_quotes_both_sides_one_tick_behind_the_best():
    q = Q()
    assert q["YES"] == {"px": D("0.47"), "qty": D("100"), "role": "open"}
    assert q["NO"] == {"px": D("0.49"), "qty": D("100"), "role": "open"}
    assert q["model"]["yes_inside"] and q["model"]["no_inside"]
    assert q["YES"]["px"] + q["NO"]["px"] <= CR.PAIR_CAP


def test_no_quote_where_one_tick_behind_would_not_score():
    """Subject to actual reward eligibility: on the live SOL book the bid
    side cannot score one tick back, and one side alone is not quoted."""
    q = Q(SOL_LIVE)
    assert q["YES"] is None and q["NO"] is None
    assert q["why_YES"] == "outside_scoring_walk"
    assert q["why_NO"] == "other_side_not_eligible"


def test_joining_the_best_price_is_an_explicit_setting():
    q = Q(SOL_LIVE, c={"allow_touch": True})
    assert q["YES"]["px"] == D("0.30") and q["NO"]["px"] == D("0.68")
    assert not CR.ALLOW_TOUCH                       # default: never joined


def test_our_own_best_bid_is_not_followed():
    """The best price that is not ours sets the quote: if everyone ahead of
    us leaves, we step back rather than become top of book."""
    bk = book([(0.47, 100), (0.46, 900), (0.45, 1500)],
              [(0.50, 300), (0.51, 900), (0.52, 1500)])
    q = Q(bk, resting=[("YES", D("0.47"), D("100"))])
    assert q["best"]["bid"] == "0.46" and q["YES"]["px"] == D("0.45")


def test_no_quote_when_the_second_does_not_pay_or_no_programme():
    wide = book([(0.40, 3000)], [(0.60, 3000)])
    q = Q(wide)
    assert q["YES"] is None and q["why_YES"].startswith("second_not_paying")
    q = Q(program=None)
    assert q["YES"] is None and q["NO"] is None
    assert q["why"] == "no_paying_program"


def test_after_a_fill_only_the_completion_rests():
    """YES 100 @0.47 held: no new YES order; NO bid for exactly 100, one
    tick behind, never above a $1.00 pair."""
    q = Q(pos={"YES": "100", "NO": "0"}, cost={"YES": "47", "NO": "0"})
    assert q["YES"] is None and q["why_YES"] == "await_pair"
    assert q["NO"] == {"px": D("0.49"), "qty": D("100"), "role": "complete"}


def test_completion_never_locks_a_loss_and_never_crosses():
    """YES bought at 0.47, market now 0.30/0.32: NO trades near 0.68. The
    completion rests at 0.53 (pair 1.00), a maker bid far below the offer."""
    bk = book([(0.30, 300), (0.29, 900), (0.28, 1500)],
              [(0.32, 300), (0.33, 900), (0.34, 1500)])
    q = Q(bk, pos={"YES": "40", "NO": "0"}, cost={"YES": "18.8", "NO": "0"})
    assert q["NO"]["px"] == D("0.53") and q["NO"]["qty"] == D("40")
    assert q["NO"]["px"] + D("0.47") <= D("1.00")


def test_completion_needs_no_programme():
    q = Q(program=None, pos={"YES": "0", "NO": "10"},
          cost={"YES": "0", "NO": "4.9"})
    assert q["YES"]["role"] == "complete" and q["NO"] is None


def test_paired_inventory_keeps_counting_against_the_market_cap():
    """100 pairs held at 0.96 = $96 deployed: nothing left of $100."""
    q = Q(pos={"YES": "100", "NO": "100"}, cost={"YES": "47", "NO": "49"},
          capital_used=D("96"))
    assert q["YES"] is None and q["why_YES"] == "per_market_cap"
    half = Q(capital_used=D("50"))
    assert half["YES"]["qty"] == half["NO"]["qty"] == D("52")


def test_price_band_and_pair_cap():
    lo = book([(0.04, 300), (0.03, 900), (0.02, 1500)],
              [(0.06, 300), (0.07, 900), (0.08, 1500)])
    assert Q(lo)["why_YES"] == "price_band"
    q = Q(c={"pair_cap": "0.95"})
    assert q["YES"] is None and q["why_YES"] == "pair_cap"


# ------------------------------------------------------------ identity

def test_btc_and_short_windows_are_crypto_but_never_tradable():
    for s in (BTC, "cpc-btc-updown-5m-x", "cpceth-eth-updown-15m-x"):
        assert CR.is_crypto(s) and not CR.tradable(s)
    for s in (ETH, SOL, XRP):
        assert CR.is_crypto(s) and CR.tradable(s)
    assert not CR.is_crypto("aec-nba-lal-bos") and "btc" not in CR.ASSET_PREFIX
    assert CR.day_key(ETH) == "2026-11-21-2200z"


def test_one_market_per_asset_and_none_inside_the_cutoff():
    now = time.time()
    tgs = [{"slug": ETH, "end": iso(3600)},              # inside the cutoff
           {"slug": ETH.replace("21", "22"), "end": iso(26 * 3600)},
           {"slug": SOL, "end": iso(5 * 3600)},
           {"slug": SOL.replace("21", "22"), "end": iso(29 * 3600)},
           {"slug": BTC, "end": iso(5 * 3600)}]
    assert [t["slug"] for t in CR.pick_targets(tgs, now)] == \
        [ETH.replace("21", "22"), SOL]
    assert CR.late(None, now) == "crypto_end_unknown"
    assert CR.late(iso(3600), now) and CR.late(iso(4 * 3600), now) is None


def test_total_can_be_lowered_but_never_raised_past_300(monkeypatch):
    monkeypatch.setenv("PMUS_CRYPTO_TOTAL_USD", "1000")
    monkeypatch.setenv("PMUS_CRYPTO_MARKET_DAYS", "30")
    try:
        importlib.reload(CR)
        assert CR.TOTAL_USD == D("300") and CR.MARKET_DAYS == 3
        monkeypatch.setenv("PMUS_CRYPTO_TOTAL_USD", "120")
        importlib.reload(CR)
        assert CR.TOTAL_USD == D("120")
    finally:
        monkeypatch.delenv("PMUS_CRYPTO_TOTAL_USD")
        monkeypatch.delenv("PMUS_CRYPTO_MARKET_DAYS")
        importlib.reload(CR)
    assert CR.TOTAL_USD == D("300") and not CR.ON


# ------------------------------------------------------------ arming

def approval(**over):
    a = CR.approval_template(SHA[:12])
    a.update(approved_by="owner", not_before=iso(-60), not_after=iso(3 * 86400),
             combined_max_exposure_usd="360")
    a.update(over)
    return a


def arm_env(monkeypatch, **over):
    monkeypatch.setattr(CR, "ON", True)
    monkeypatch.setenv(CR.ARM_ENV, "YES")
    monkeypatch.setenv(CR.SHA_ENV, SHA)
    monkeypatch.setenv(CR.COMBINED_CAP_ENV, "360")
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(approval(**over)))


def test_default_is_off_and_disarmed():
    assert CR.ON is False
    assert CR.arm_state(time.time()) == {"armed": False, "why": "crypto_off"}


def test_on_without_the_arm_key_or_approval_is_shadow(monkeypatch):
    monkeypatch.setattr(CR, "ON", True)
    assert CR.arm_state(time.time())["why"] == "not_armed"
    monkeypatch.setenv(CR.ARM_ENV, "YES")
    assert CR.arm_state(time.time())["why"] == "no_approval"
    monkeypatch.setenv(CR.APPROVAL_ENV, "{not json")
    assert CR.arm_state(time.time())["why"] == "approval_unreadable"


def test_a_complete_matching_approval_arms(monkeypatch):
    arm_env(monkeypatch)
    st = CR.arm_state(time.time())
    assert st["armed"] and st["approved_by"] == "owner"


@pytest.mark.parametrize("over,why", [
    ({"approved_by": ""}, "approval_unsigned"),
    ({"release_sha": "deadbeefdead"}, "approval_release_mismatch"),
    ({"release_sha": "0123"}, "approval_release_mismatch"),
    ({"taker": True}, "approval_taker_must_be_false"),
    ({"qty": "500"}, "approval_mismatch:qty"),
    ({"total_usd": "600"}, "approval_mismatch:total_usd"),
    ({"loss_stop_usd": "100"}, "approval_mismatch:loss_stop_usd"),
    ({"market_days": 5}, "approval_mismatch:market_days"),
    ({"max_markets": 6}, "approval_mismatch:max_markets"),
    ({"hard_loss_budget": False}, "approval_mismatch:hard_loss_budget"),
    ({"hard_loss_budget": "true"}, "approval_mismatch:hard_loss_budget"),
    ({"assets": ["eth", "sol", "xrp", "btc"]}, "approval_mismatch:assets"),
    ({"combined_max_exposure_usd": "999"},
     "approval_mismatch:combined_max_exposure_usd"),
    ({"not_after": "soon"}, "approval_window_unreadable"),
    ({"not_after": iso(-3600)}, "approval_window_unreadable"),
    ({"not_before": iso(-7200), "not_after": iso(-1)}, "approval_expired"),
    ({"not_before": iso(3600)}, "approval_not_started"),
    ({"not_before": iso(-10 * 86400), "not_after": iso(86400)},
     "approval_window_too_long"),
])
def test_any_difference_from_the_running_configuration_refuses(
        monkeypatch, over, why):
    arm_env(monkeypatch, **over)
    assert CR.arm_state(time.time()) == {"armed": False, "why": why}


def test_approval_expires_and_needs_the_combined_ceiling(monkeypatch):
    arm_env(monkeypatch)
    assert CR.arm_state(time.time() + 4 * 86400)["why"] == "approval_expired"
    monkeypatch.delenv(CR.COMBINED_CAP_ENV)
    assert CR.arm_state(time.time())["why"] == \
        "approval_mismatch:combined_max_exposure_usd"
    monkeypatch.setenv(CR.COMBINED_CAP_ENV, "360")
    monkeypatch.delenv(CR.SHA_ENV)
    assert CR.arm_state(time.time())["why"] == "approval_release_mismatch"


# ------------------------------------------------------------ accounting

def mkt(y="0", n="0", cy="0", cn="0", orders=None, **kw):
    return {"orders": orders or {}, "pos": {"YES": y, "NO": n},
            "cost": {"YES": cy, "NO": cn}, "done": False, "lane": "crypto",
            **kw}


def test_book_estimate_never_rounds_a_loss_down():
    markets = {
        ETH: mkt("100", "0", "47", "0", mark="-12"),            # open, marked
        SOL: mkt("100", "0", "50", "0", done=True),             # closed, no px
        XRP: mkt("100", "100", "47", "49", done=True, settle_px="0"),
        "aec-x": {**mkt("5", "0", "2", "0", mark="-2"), "lane": None}}
    r = CR.book_pnl(markets)
    assert r["by_market"] == {ETH: "-12", SOL: "-50", XRP: "4"}
    assert r["usd"] == D("-58")                     # sports market not counted


def test_venue_rewards_only_a_paid_row_naming_a_crypto_market_is_money():
    rows = [{"marketSlug": ETH, "status": "PAID", "reward": "1.80"},
            {"marketSlug": ETH, "status": "PENDING", "reward": "2.10"},
            {"marketSlug": SOL, "status": "SKIPPED", "amount": "0.40"},
            {"marketSlug": SOL, "reward": "0.70"},               # no status
            {"marketSlug": "aec-nba-x", "status": "PAID", "reward": "9"}]
    r = CR.venue_rewards(rows, ("PAID", "CREDITED"))
    assert r["rows"] == 4 and r["credited_usd"] == "1.80"
    assert r["pending_usd"] == "3.20"
    assert r["by_market"][SOL]["credited_usd"] == "0"


def meter(net="0", outstanding="0", unconfirmed=(), checked=True,
          contracts="200"):
    return {"realized_net_usd": net, "outstanding_contracts": outstanding,
            "unconfirmed": list(unconfirmed), "positions_checked": checked,
            "execution": {"contracts": contracts},
            "open_worst_case_usd": "0"}


def test_modelled_pending_and_credited_never_mix_into_realized():
    rw = {"credited_usd": "1.80", "pending_usd": "3.20"}
    r = CR.net_report(meter("-0.50"), rw, D("27.40"))
    assert r["modelled_reward_usd"] == "27.40"
    assert r["venue_pending_reward_usd"] == "3.20"
    assert r["realized_net_usd"] == "1.30"            # -0.50 + 1.80 only
    assert r["verdict"] == "PASS"
    assert CR.net_report(meter("-5"), rw, D("99"))["verdict"] == \
        "FAIL_OR_AWAITING_CREDIT"
    none_paid = {"credited_usd": "0", "pending_usd": "9"}
    assert CR.net_report(meter("4"), none_paid, D("99"))["verdict"] == \
        "FAIL_OR_AWAITING_CREDIT"


def test_no_verdict_while_anything_is_unsettled_unconfirmed_or_unread():
    rw = {"credited_usd": "5", "pending_usd": "0"}
    assert CR.net_report(None, rw, 0)["verdict"] == "INCOMPLETE"
    assert CR.net_report(meter("3"), None, 0)["realized_net_usd"] is None
    assert CR.net_report(meter("3", outstanding="40"), rw,
                         0)["verdict"] == "INCOMPLETE"       # a leg is open
    assert CR.net_report(meter("3", unconfirmed=[ETH]), rw,
                         0)["verdict"] == "INCOMPLETE"
    assert CR.net_report(meter("3", checked=False), rw,
                         0)["verdict"] == "INCOMPLETE"       # no venue walk
    assert CR.net_report(meter("0", contracts="0"), rw,
                         0)["verdict"] == "INCOMPLETE"       # never traded
    assert CR.net_report(meter("3"), rw, 0)["verdict"] == "PASS"


def test_markouts_measure_the_move_after_each_fill():
    mk = mkt()
    CR.record_fill(mk, "YES", D("0.47"), D("100"), D("0.49"), 1000.0)
    CR.record_fill(mk, "NO", D("0.49"), D("50"), D("0.49"), 1000.0)
    assert not CR.update_markouts(mk, D("0.45"), 1030.0)       # too early
    assert CR.update_markouts(mk, D("0.40"), 1061.0)
    s = CR.markout_summary({ETH: mk})
    # YES: 0.40 - 0.47 = -0.07 x 100; NO: 0.60 - 0.49 = +0.11 x 50
    assert D(s["markout_60s_usd"]) == D("-1.50")
    assert s["markout_300s_per_contract"] is None and s["fills"] == 2


def test_lane_exposure_counts_unsettled_markets_and_resting_bids():
    o = {"a": {"side": "YES", "px": "0.47", "qty": "100", "filled": "40",
               "open": True, "stage": "crypto_open"}}
    markets = {ETH: mkt("40", "0", "18.8", "0", orders=o),
               SOL: mkt("100", "100", "47", "49", done=True),     # unsettled
               XRP: mkt("100", "0", "50", "0", done=True, settle_px="1"),
               "aec-x": {**mkt("5", "0", "2", "0"), "lane": None}}
    ex = CR.exposure(markets)
    assert ex["capital"] == D("18.8") + D("28.2") + D("96")
    assert ex["unpaired"] == D("18.8") and ex["open_orders"] == 1
    assert set(ex["by_market"]) == {ETH, SOL}


def test_lane_caps_drop_openings_never_completions(monkeypatch):
    monkeypatch.setattr(CR, "HARD_LOSS_BUDGET", False)
    held = {s: mkt("100", "100", "47", "49") for s in (ETH, SOL)}   # $192
    full = {"YES": {"px": D("0.47"), "qty": D("100"), "role": "open"},
            "NO": {"px": D("0.49"), "qty": D("100"), "role": "open"}}
    want, why = dict(full), {}
    CR.fit_caps({**held, XRP: mkt()}, XRP, want, why)
    assert want == full                                # 192 + 96 = 288
    want, why = dict(full), {}
    CR.fit_caps({**held, XRP: mkt("20", "20", "9.4", "9.8")}, XRP, want, why)
    assert want == {"YES": None, "NO": None}           # 192 + 19.2 + 96
    assert why["YES"] == "crypto_total_cap"
    comp = {"YES": None, "NO": {"px": D("0.49"), "qty": D("100"),
                                "role": "complete"}}
    want = dict(comp)
    CR.fit_caps({**held, XRP: mkt("100", "0", "47", "0")}, XRP, want, {})
    assert want == comp


def test_unpaired_bound_counts_what_resting_bids_could_strand(monkeypatch):
    monkeypatch.setattr(CR, "HARD_LOSS_BUDGET", False)
    o = {"a": {"side": "NO", "px": "0.66", "qty": "100", "filled": "0",
               "open": True, "stage": "crypto_open"},
         "b": {"side": "YES", "px": "0.29", "qty": "100", "filled": "0",
               "open": True, "stage": "crypto_open"}}
    markets = {ETH: mkt(orders=dict(o)), SOL: mkt(orders=dict(o)), XRP: mkt()}
    want = {"YES": {"px": D("0.29"), "qty": D("100"), "role": "open"},
            "NO": {"px": D("0.66"), "qty": D("100"), "role": "open"}}
    why = {}
    CR.fit_caps(markets, XRP, want, why)          # 66 + 66 + 66 > 150
    assert want["NO"] is None and why["NO"] == "crypto_unpaired_cap"


# ------------------------------------------------------------ engine

class CV(FakeVenue):
    """FakeVenue plus the public depth and discovery the crypto lane reads."""

    def __init__(self):
        super().__init__()
        self.bid, self.ask = D("0.48"), D("0.50")
        self.books = {}
        self.end = iso(10 * 3600)
        self.crypto = [ETH, SOL, XRP, BTC]
        self.states = {}
        self.cancelled = []
        self.closed = set()
        self.gateway_threads = set()
        self.top_depth_calls = []

    def spec(self, slug):
        return None if slug in self.closed else super().spec(slug)

    def top_depth(self, slug):
        """The sports liquidity gate's gateway read. The real one is a
        request on the engine loop, so for a crypto market it must never
        be called at all."""
        self.top_depth_calls.append(slug)
        if CR.is_crypto(slug):
            pytest.fail("gateway depth request for a crypto market on the "
                        "engine loop: " + slug)
        return super().top_depth(slug)

    def gget(self, path):
        import threading
        self.gateway_threads.add(threading.current_thread().name)
        if path.startswith("/v1/events"):
            return {"events": [{"markets": [
                {"slug": s, "endDate": self.end} for s in self.crypto]}]}
        if "/book" in path:
            slug = path.split("/v1/markets/")[1].split("/book")[0]
            return {"marketData": self.books.get(slug, DEEP)}
        if path.startswith("/v1/incentives"):
            return {"programs": [{"marketSlug": s, "timePeriods": [{
                "programId": "crypto_alt_updown_1d_20261110",
                "programType": "liquidityProgram", "rewardPool": 150,
                "start": "2026-11-10T16:00:00Z", "status": "active",
                "discountFactor": 0.3, "targetSize": 2000,
                "period": "daily_event", "maxSpread": 0.05}]}
                for s in self.crypto if s in path and s != BTC]}
        raise AssertionError(path)

    def cancel(self, oid):
        self.cancelled.append(oid)
        return super().cancel(oid)

    def account(self):
        """Venue truth: net position per market from the fills, and every
        resting order with its id."""
        pos = dict(self.acct["positions"])
        for o in self.orders.values():
            if o["filled"]:
                sign = 1 if o["side"] == "YES" else -1
                pos[o["slug"]] = pos.get(o["slug"], D(0)) + sign * o["filled"]
        return {"positions": {s: q for s, q in pos.items() if q},
                "open_orders": [{"id": k, "marketSlug": o["slug"]}
                                for k, o in self.orders.items() if o["open"]]
                + list(self.acct["open_orders"])}

    def live(self, slug=None):
        return {k: o for k, o in self.orders.items() if o["open"] and
                (slug is None or o["slug"] == slug)}

    def fill(self, oid, qty=None):
        o = self.orders[oid]
        q = o["qty"] if qty is None else D(str(qty))
        o.update(filled=q, open=q < o["qty"])


def fresh_meter(e):
    e._crypto_meter = {"realized_net_usd": "0", "unconfirmed": [],
                       "positions_checked": True}
    e._crypto_meter_at = time.monotonic()


def wire_feed(e, v):
    """The lane's feed, not threaded: it is polled right before each crypto
    pass and each selection, so a test sees the venue as it is now."""
    from src import pmus_crypto_feed as CF
    e._cfeed_autostart = False               # tests poll it themselves
    e._cfeed = CF.CryptoFeed(v.gget, v.spec, sleep=lambda s: None)
    real_pass, real_select = e._crypto_pass, e.select_crypto

    def sync():
        e._cfeed.watch(s for s in e.active() if CR.tradable(s))
        e._cfeed.poll_once(force=True)

    def crypto_pass(slug, mk):
        sync()
        return real_pass(slug, mk)

    def select_crypto(acct):
        sync()
        return real_select(acct)
    e._crypto_pass, e.select_crypto, e.feed_sync = \
        crypto_pass, select_crypto, sync
    return e


def wire(e, v):
    """Give an engine the fake venue's reads and readable shared gates."""
    wire_feed(e, v)
    e.account_risk = lambda fresh=False: {"ok": True, "lane_cap_usd": "1000"}
    e.crypto_account_view = v.account
    return e


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """Lane on, armed with a matching approval, shared gates readable.
    The proposal's 100-a-side sizing (hard loss budget off); the hard
    budget has its own tests below."""
    monkeypatch.setattr(CR, "HARD_LOSS_BUDGET", False)
    # LANE LIMITS IN ISOLATION. The sports final gate (clip, pair cap and
    # campaign caps on every order) is switched off HERE ONLY, so the
    # lane's own limits can be exercised at the proposal's sizes. The lane
    # under the real final gate and the real clip is tested separately
    # (fixture `clipped`, at the end of this file).
    monkeypatch.setattr(M, "FINAL_GATE", False)
    arm_env(monkeypatch)
    monkeypatch.setattr(M, "ACCOUNT_RISK_ON", True)
    monkeypatch.setattr(RN1, "ON", True)       # production runs RN1 mode
    v = CV()
    e = wire(M.Engine(v, M.Book(tmp_path / "s.json")), v)
    fresh_meter(e)
    return e, v


def select(e, v):
    e.select_crypto(v.account())
    return [s for s in e.active() if CR.is_crypto(s)]


def test_selects_one_market_per_asset_never_btc(lane):
    e, v = lane
    assert sorted(select(e, v)) == [ETH, SOL, XRP]
    assert all(e.b.m(s)["lane"] == "crypto" and e.b.m(s)["reward"]["df"] == 0.3
               for s in (ETH, SOL, XRP))
    assert not v.orders                          # selecting places nothing


def test_armed_lane_rests_both_sides_post_only_one_tick_behind(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    got = {o["side"]: (o["px"], o["qty"]) for o in v.live(ETH).values()}
    assert got == {"YES": (D("0.47"), D("100")), "NO": (D("0.49"), D("100"))}
    assert {o["stage"] for o in e.b.m(ETH)["orders"].values()} == \
        {"crypto_open"}
    assert not getattr(v, "taker", [])
    e.step_market(ETH)
    assert len(v.orders) == 2                      # unchanged book: no churn


def test_crypto_never_reaches_rn1_or_the_sports_path(lane, monkeypatch):
    """Audit finding 6: in RN1 mode every selected market was RN1's. A
    crypto market now always takes its own branch."""
    e, v = lane
    monkeypatch.setattr(e, "_rn1_step", lambda *a, **k: pytest.fail("rn1"))
    monkeypatch.setattr(e, "_reward_quote", lambda *a, **k: pytest.fail("ts"))
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    stages = {o["stage"] for s in (ETH, SOL, XRP)
              for o in e.b.m(s)["orders"].values()}
    assert stages == {"crypto_open"}
    monkeypatch.setattr(RN1, "ON", False)
    e.step_market(ETH)                             # two-sided path: same


def test_lane_off_cancels_and_never_falls_through(lane, monkeypatch):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    monkeypatch.setattr(CR, "ON", False)
    monkeypatch.setattr(e, "_rn1_step", lambda *a, **k: pytest.fail("rn1"))
    e.step_market(ETH)
    assert not v.live() and len(v.orders) == 2


def test_shadow_mode_prices_and_models_but_places_nothing(lane, monkeypatch,
                                                         capsys):
    e, v = lane
    monkeypatch.delenv(CR.ARM_ENV)
    select(e, v)
    e.step_market(ETH)
    assert not v.orders
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if '"CRYPTO_QUOTE"' in ln)
    d = json.loads(line.split(" ", 1)[1])
    assert d["armed"] is False and d["arm_why"] == "not_armed"
    assert d["want"]["YES"] == {"px": "0.47", "qty": "100", "role": "open"}


@pytest.mark.parametrize("slug,side,stage,post_only,why", [
    (ETH, "YES", "rn1_open", True, "other_lane_stage_on_crypto_market"),
    (ETH, "YES", "rn1_extend", True, "other_lane_stage_on_crypto_market"),
    (ETH, "NO", "two_sided_complete_taker", False,
     "other_lane_stage_on_crypto_market"),
    (ETH, "YES", "two_sided_open_rewarded", True,
     "other_lane_stage_on_crypto_market"),
    (ETH, "YES", "crypto_open", False, "taker_not_authorized"),
    (BTC, "YES", "crypto_open", True, "not_a_pilot_market"),
    ("aec-nba-x", "YES", "crypto_open", True, "not_a_pilot_market"),
])
def test_placement_choke_point_refuses(lane, slug, side, stage, post_only,
                                       why):
    e, v = lane
    select(e, v)
    assert e.crypto_place_block(slug, side, D("0.40"), D("10"), stage,
                                post_only) == why
    e.place(slug, side, D("0.40"), D("10"), stage, post_only=post_only)
    assert not v.orders


def test_disarmed_placement_is_refused_at_the_choke_point(lane, monkeypatch):
    e, v = lane
    select(e, v)
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(approval(qty="500")))
    e.place(ETH, "YES", D("0.47"), D("100"), "crypto_open")
    assert not v.orders
    assert e.crypto_place_block(ETH, "YES", D("0.47"), D("100"),
                                "crypto_open", True) == \
        "disarmed:approval_mismatch:qty"
    assert e.crypto_place_block(ETH, "YES", D("0.47"), D("101"),
                                "crypto_open", True).startswith("disarmed")


def test_oversize_opening_is_refused(lane):
    e, v = lane
    select(e, v)
    e.feed_sync()
    assert e.crypto_place_block(ETH, "YES", D("0.47"), D("101"),
                                "crypto_open", True) == "qty_over_cap"


def test_quotes_follow_the_book_with_exact_id_cancels(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    first = set(v.live(ETH))
    v.books[ETH] = book([(0.45, 300), (0.44, 900), (0.43, 1500)],
                        [(0.47, 300), (0.48, 900), (0.49, 1500)])
    v.bid, v.ask = D("0.45"), D("0.47")
    e.step_market(ETH)
    assert set(v.cancelled) == first
    got = {o["side"]: o["px"] for o in v.live(ETH).values()}
    assert got == {"YES": D("0.44"), "NO": D("0.52")}


def test_one_sided_fill_leaves_only_an_exact_completion(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    yes = next(k for k, o in v.live(ETH).items() if o["side"] == "YES")
    v.fill(yes, 40)                                  # partial: 40 of 100
    e.step_market(ETH)
    e.step_market(ETH)
    live = list(v.live(ETH).values())
    assert len(live) == 1 and live[0]["side"] == "NO"
    assert live[0]["qty"] == D("40") and live[0]["px"] == D("0.49")
    mk = e.b.m(ETH)
    assert [o["stage"] for o in mk["orders"].values() if o["open"]] == \
        ["crypto_complete"]
    assert mk["pos"] == {"YES": "40", "NO": "0"}
    assert mk["crypto_fills"][0]["side"] == "YES"
    comp = next(iter(v.live(ETH)))
    v.fill(comp)
    e.step_market(ETH)
    assert e.b.d["pairs"][-1]["slug"] == ETH
    assert D(e.b.d["pairs"][-1]["pair_cost"]) == D("0.96")
    # paired inventory ($38.40) stays deployed: the next quote is sized to
    # what is left of the $100 market cap, both sides together
    nxt = {o["side"]: o["qty"] for o in v.live(ETH).values()}
    assert nxt == {"YES": D("64"), "NO": D("64")}


def test_completion_continues_under_a_new_exposure_block(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    yes = next(k for k, o in v.live(ETH).items() if o["side"] == "YES")
    v.fill(yes)
    e._crypto_meter = None                      # new openings now blocked
    e.step_market(ETH)
    live = list(v.live(ETH).values())
    assert [(o["side"], o["qty"]) for o in live] == [("NO", D("100"))]


@pytest.mark.parametrize("setup,why", [
    (lambda e, mp: setattr(e, "_crypto_meter", None),
     "crypto_meter_unavailable"),
    (lambda e, mp: setattr(e, "_crypto_meter_at", time.monotonic() - 9e4),
     "crypto_meter_unavailable"),
    (lambda e, mp: e._crypto_meter.update(unconfirmed=[ETH]),
     "crypto_fills_unconfirmed"),
    (lambda e, mp: e._crypto_meter.update(positions_checked=False),
     "crypto_positions_unchecked"),
    (lambda e, mp: setattr(e, "crypto_account_view", lambda: None),
     "account_state_unavailable"),
    (lambda e, mp: setattr(e, "crypto_account_view", lambda: {
        "positions": {SOL: D("25")}, "open_orders": []}),
     "venue_inventory_mismatch:" + SOL),
    (lambda e, mp: setattr(e, "crypto_account_view", lambda: {
        "positions": {ETH.replace("11-21", "11-20"): D("-7")},
        "open_orders": []}),
     "venue_inventory_unknown:" + ETH.replace("11-21", "11-20")),
    (lambda e, mp: setattr(e, "crypto_account_view", lambda: {
        "positions": {}, "open_orders": [{"id": "manual1",
                                          "marketSlug": XRP}]}),
     "venue_order_unknown:" + XRP),
    (lambda e, mp: setattr(e, "crypto_account_view", lambda: {
        "positions": {ETH: "n/a"}, "open_orders": []}),
     "account_state_unreadable:InvalidOperation"),
    (lambda e, mp: mp.setattr(M, "ACCOUNT_RISK_ON", False),
     "account_risk_not_enforced"),
    (lambda e, mp: setattr(e, "account_risk", lambda fresh=False: {
        "ok": False, "reason": "ACCOUNT_DAILY_LOSS_HALT"}),
     "account_risk:ACCOUNT_DAILY_LOSS_HALT"),
    (lambda e, mp: setattr(e, "account_risk", lambda fresh=False: {
        "ok": False, "reason": "ACCOUNT_RISK_STALE"}),
     "account_risk:ACCOUNT_RISK_STALE"),
    (lambda e, mp: setattr(e, "shared_halt", lambda: "HALT"), "shared_halt"),
    (lambda e, mp: setattr(e, "shared_halt", lambda: "UNAVAILABLE"),
     "shared_halt"),
    (lambda e, mp: mp.setattr(CR, "LOSS_STOP_USD", D("0")),
     "crypto_loss_stop_unset"),
    (lambda e, mp: mp.setenv(M.ENABLED_ENV, "NO"), "not_enabled"),
    (lambda e, mp: mp.setenv(M.SUBMISSION_LOCK_ENV, "1"), "submission_lock"),
    (lambda e, mp: mp.setenv(M.SUBMISSION_LOCK_ENV, "true"),
     "submission_lock"),
    (lambda e, mp: mp.setenv(
        M.SUBMISSION_LOCK_ENV,
        "window:2026-01-01T00:00:00Z/2026-01-01T02:00:00Z"),
     "submission_lock"),
    (lambda e, mp: e.b.m(ETH).update(reward=None), "no_paying_program"),
    (lambda e, mp: e.b.m(ETH).update(end=iso(3600)),
     "late:crypto_last_7200s"),
    (lambda e, mp: e.b.m(ETH).update(end="?"), "late:crypto_end_unknown"),
])
def test_every_failed_gate_pulls_resting_quotes(lane, monkeypatch, setup,
                                                why):
    """Each gate blocks NEW quotes and pulls the ones already resting."""
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    assert len(v.live(ETH)) == 2
    setup(e, monkeypatch)
    assert e.crypto_block(ETH, e.b.m(ETH), time.time()) == why
    e.step_market(ETH)
    assert not v.live(ETH) and len(v.orders) == 2


def test_stale_depth_pulls_openings(lane, monkeypatch):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    monkeypatch.setattr(CR, "BOOK_MAX_AGE_S", -1.0)
    e.step_market(ETH)
    assert not v.live(ETH)


def test_late_flat_market_is_released(lane):
    e, v = lane
    select(e, v)
    e.b.m(ETH)["end"] = iso(1800)
    e.step_market(ETH)
    assert e.b.m(ETH)["done"] and not v.orders


# --------------------------------------------- limits, both lanes at once

def sports_order(e, slug="aec-x", px="0.45", qty="10", stage="rn1_open"):
    e.place(slug, "YES", D(px), D(qty), stage)


def test_crypto_total_never_exceeds_300_across_three_markets(lane):
    e, v = lane
    select(e, v)
    for _ in range(2):
        for s in (ETH, SOL, XRP):
            e.step_market(s)
    ex = CR.exposure(e.b.d["markets"])
    assert ex["capital"] == D("288") and ex["open_orders"] == 6
    assert ex["capital"] <= CR.TOTAL_USD
    assert e.crypto_place_block(ETH, "YES", D("0.47"), D("100"),
                                "crypto_open", True) == "crypto_total_cap"


def test_outstanding_order_bound(lane, monkeypatch):
    e, v = lane
    monkeypatch.setattr(CR, "MAX_OPEN_ORDERS", 4)
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    assert len(v.live()) == 4 and not v.live(XRP)


def test_sports_and_crypto_together_cannot_pass_the_combined_ceiling(
        lane, monkeypatch):
    """Combined ceiling $250: sports rests $40 first, then crypto. ETH and
    SOL fit ($96 each, $232 with sports); XRP would make $328 and is
    refused. A sports opening after that is refused by the same ceiling."""
    e, v = lane
    monkeypatch.setenv(CR.COMBINED_CAP_ENV, "250")
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(
        approval(combined_max_exposure_usd="250")))
    sports_order(e, qty="80", px="0.50")                         # $40
    assert e.combined_exposure()["sports_usd"] == D("40")
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    c = e.combined_exposure()
    assert c == {"sports_usd": D("40"), "crypto_usd": D("192"),
                 "total_usd": D("232")}
    assert not v.live(XRP)
    n = len(v.orders)
    sports_order(e, slug="aec-y", qty="40", px="0.50")           # +$20 > 250
    assert len(v.orders) == n
    sports_order(e, slug="aec-y", qty="30", px="0.50")           # +$15 fits
    assert len(v.orders) == n + 1
    assert e.combined_exposure()["total_usd"] == D("247")


def test_crypto_refuses_without_a_combined_ceiling_sports_is_unchanged(
        lane, monkeypatch):
    e, v = lane
    monkeypatch.delenv(CR.COMBINED_CAP_ENV)
    select(e, v)
    assert e.crypto_place_block(ETH, "YES", D("0.47"), D("100"),
                                "crypto_open", True).startswith("disarmed")
    assert e.combined_block(D("0.5"), D("1000")) is None
    sports_order(e)
    assert len(v.orders) == 1


def test_unreadable_combined_ceiling_refuses_both_lanes(lane, monkeypatch):
    e, v = lane
    monkeypatch.setenv(CR.COMBINED_CAP_ENV, "lots")
    assert e.combined_block(D("0.5"), D("1")) == "COMBINED_CAP_UNREADABLE"
    sports_order(e)
    select(e, v)
    e.step_market(ETH)
    assert not v.orders


def test_the_account_lane_cap_counts_both_lanes(lane):
    """The shared account gate's exposure split (l5_account_risk) sees
    sports + crypto as one figure."""
    e, v = lane
    e.account_risk = lambda fresh=False: {"ok": True, "lane_cap_usd": "120"}
    sports_order(e, qty="80", px="0.50")                         # $40
    select(e, v)
    e.step_market(ETH)
    assert len(v.live(ETH)) == 1         # 40 + 47 fits 120; + 49 does not
    assert e.own_exposure_usd() == D("87")
    e.step_market(ETH)                   # one side alone is not kept
    e.step_market(ETH)
    assert e.own_exposure_usd() <= D("120")


def test_a_crypto_lone_leg_does_not_spend_the_sports_caps(lane, monkeypatch):
    """Sports TOTAL_CAP and slots read sports only; the account exposure
    figure reads both. (The daily halt covers both: see below.)"""
    e, v = lane
    monkeypatch.setattr(M, "TOTAL_CAP", D("4.50"))
    monkeypatch.setattr(M, "DAILY_LOSS_USD", D("3"))
    select(e, v)
    e.step_market(ETH)
    yes = next(k for k, o in v.live(ETH).items() if o["side"] == "YES")
    v.fill(yes)
    v.books[ETH] = around(0.38, 0.40)                    # ETH marked -$9
    e.step_market(ETH)
    assert e.b.total_unmatched() == 0
    assert e.b.total_unmatched_all() == D("47")
    assert e.slots_used() == 0 and not e.full()
    assert e.day_change() == D("-9") and e.daily_stopped()   # -9 < -3
    assert CR.book_pnl(e.b.d["markets"])["usd"] == D("-9")
    assert e.sports_active() == [] and len(e.active()) == 3


def test_crypto_markets_do_not_fill_rn1_market_slots(lane, monkeypatch):
    e, v = lane
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    assert not e.rn1_market_slots_full("aec-x", e.b.m("aec-x"))
    assert e.rn1_campaign_exposure() == {"spend": D(0), "unmatched": D(0)}


def test_sports_gates_do_not_touch_crypto_quotes(lane):
    """The realtime touch reaction and the international guard are sports
    decisions; a crypto quote is re-priced only by its own step."""
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    e.react(ETH, [({"bid": D("0.48"), "ask": D("0.50")},
                   {"bid": D("0.40"), "ask": D("0.42")}, time.monotonic())])
    e.guard = type("G", (), {"verdict": lambda *a: pytest.fail("guard")})()
    e._intl_check(ETH, time.monotonic())
    assert len(v.live(ETH)) == 2 and not v.cancelled


# ------------------------------------------------------- loss stop

def lone_yes(e, v, slug=ETH):
    e.step_market(slug)
    yes = next(k for k, o in v.live(slug).items() if o["side"] == "YES")
    v.fill(yes)
    e.step_market(slug)


def test_loss_stop_latches_cancels_everything_and_survives_restart(
        lane, tmp_path):
    e, v = lane
    select(e, v)
    e.step_market(SOL)
    lone_yes(e, v)                         # YES 100 @0.47 + NO completion
    assert len(v.live()) == 3
    v.books[ETH] = around(0.16, 0.18)      # mark 16 - 47 = -31
    e.step_market(ETH)
    assert not v.live()                    # SOL quotes and the completion
    stop = e.b.d["crypto"]["loss_stop"]
    assert D(stop["book_estimate_usd"]) == D("-31")
    assert e.crypto_block(SOL, e.b.m(SOL), time.time()) == "crypto_loss_stop"
    v.books[ETH] = DEEP                    # recovery does not lift it
    n = len(v.orders)
    e2 = wire(M.Engine(v, M.Book(tmp_path / "s.json")), v)   # restart
    fresh_meter(e2)
    for s in (ETH, SOL, XRP):
        e2.step_market(s)
    assert len(v.orders) == n and not v.live()
    assert e2.crypto_place_block(ETH, "NO", D("0.49"), D("100"),
                                 "crypto_complete", True) == \
        "crypto_loss_stop"


def test_venue_realized_loss_trips_the_same_stop(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    e._crypto_meter["realized_net_usd"] = "-30"
    assert e.crypto_loss_stopped()
    assert not v.live()
    assert e.b.d["crypto"]["loss_stop"]["venue_realized_net_usd"] == "-30"


def test_a_closed_unpaired_market_counts_as_a_full_loss_until_settled(lane):
    """Proposal: 'cannot guarantee a $30 maximum loss'. A lone leg that
    closes unpaired is carried at zero until the venue's price is read."""
    e, v = lane
    select(e, v)
    lone_yes(e, v)
    e.b.m(ETH)["done"] = True
    assert CR.book_pnl(e.b.d["markets"])["usd"] == D("-47")
    assert e.crypto_loss_stopped()


# ------------------------------------------------------- pilot length

def test_three_market_days_then_no_new_day(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    assert e.b.d["crypto"]["days"] == ["2026-11-21-2200z"]
    e.b.d["crypto"]["days"] = ["d1", "d2", "2026-11-21-2200z"]
    e.step_market(SOL)                               # same day: allowed
    assert len(v.live(SOL)) == 2
    nxt = ETH.replace("11-21", "11-22")
    v.crypto = [nxt]
    e.select_crypto(v.account())
    assert nxt not in e.b.d["markets"]
    mk = e.b.m(nxt)
    mk.update(lane="crypto", reward=dict(PROG), end=iso(9 * 3600))
    assert e.crypto_block(nxt, mk, time.time()) == "pilot_market_days_used"


def test_shadow_does_not_use_up_pilot_days(lane, monkeypatch):
    e, v = lane
    monkeypatch.delenv(CR.ARM_ENV)
    select(e, v)
    e.step_market(ETH)
    assert e.b.d["crypto"]["days"] == []


def test_foreign_exposure_keeps_the_lane_out(lane):
    e, v = lane
    v.acct["positions"] = {ETH: D("5")}
    assert sorted(select(e, v)) == [SOL, XRP]


# ------------------------------------------- shutdown, restart, watchdog

def test_shutdown_cancels_every_crypto_order_and_refuses_new(lane):
    e, v = lane
    select(e, v)
    for s in (ETH, SOL):
        e.step_market(s)
    e.on_sigterm()
    e._shutdown_thread.join(10)
    assert not v.live() and len(v.cancelled) == 4
    e.step_market(XRP)
    assert not v.live()                              # place refused: stop


def test_disabling_the_bot_cancels_crypto_orders(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    e.cancel_all_own("disabled")
    assert not v.live()


def test_restart_adopts_resting_orders_and_manages_them(lane, tmp_path):
    """Crash with two quotes resting: the next process finds them in the
    durable book. With no venue-reconciled meter yet it pulls them; once
    the meter is fresh it quotes again."""
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    old = set(v.live(ETH))
    e2 = wire(M.Engine(v, M.Book(tmp_path / "s.json")), v)
    assert set(o["venue_order_id"] for o in
               M.registry_doc(e2.b)["orders"]) >= old
    e2.step_market(ETH)
    assert set(v.cancelled) == old and not v.live()
    fresh_meter(e2)
    e2.step_market(ETH)
    assert len(v.live(ETH)) == 2


def test_restart_with_the_lane_switched_off_cancels(lane, tmp_path,
                                                    monkeypatch):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    monkeypatch.setattr(CR, "ON", False)
    e2 = M.Engine(v, M.Book(tmp_path / "s.json"))
    wire_feed(e2, v)
    e2.step_market(ETH)
    assert not v.live()


def test_an_untracked_venue_order_in_a_crypto_market_is_swept(lane):
    """Placed at the venue but lost before the book was saved: the orphan
    sweep adopts and cancels it by exact id."""
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    v.orders["ghost"] = {"slug": ETH, "side": "YES", "px": D("0.47"),
                         "qty": D("100"), "filled": D(0), "open": True}
    e.orphan_sweep([{"id": "ghost", "marketSlug": ETH,
                     "manualOrderIndicator": "AUTOMATIC"}])
    assert not v.orders["ghost"]["open"]
    assert len(v.live(ETH)) == 2


def test_a_fill_racing_the_cancel_is_recorded_before_any_new_order(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    yes = next(k for k, o in v.live(ETH).items() if o["side"] == "YES")
    real_cancel = v.cancel

    def racing(oid):
        if oid == yes:
            v.orders[oid].update(filled=D("100"))
        return real_cancel(oid)
    v.cancel = racing
    v.books[ETH] = book([(0.45, 300), (0.44, 900), (0.43, 1500)],
                        [(0.47, 300), (0.48, 900), (0.49, 1500)])
    v.bid, v.ask = D("0.45"), D("0.47")
    n = len(v.orders)
    e.step_market(ETH)
    assert e.b.m(ETH)["pos"]["YES"] == "100" and len(v.orders) == n
    e.step_market(ETH)
    live = list(v.live(ETH).values())
    assert [(o["side"], o["qty"]) for o in live] == [("NO", D("100"))]


# ------------------------------------------------------- venue accounting

def trade(slug, qty, fee="0"):
    return {"type": "ACTIVITY_TYPE_TRADE", "trade": {
        "marketSlug": slug, "qtyDecimal": str(qty), "isAggressor": False,
        "passiveExecution": {"commissionNotionalCollected": {"value": fee}}}}


def pnl_line(capsys):
    return json.loads([ln for ln in capsys.readouterr().out.splitlines()
                       if '"CRYPTO_PNL"' in ln][-1].split(" ", 1)[1])


def test_venue_reconciled_result_fees_rewards_and_modelled_stay_apart(
        lane, capsys):
    """Cash basis (sports ae5adbe16): the venue nets a completed pair and
    pays its $1 at pairing, so 100 pairs at 0.96 are $4.00 of realized
    cash -- less the fee the venue charged -- with nothing left open."""
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    for k in list(v.live(ETH)):
        v.fill(k)
    e.step_market(ETH)                                # pair locked at 0.96
    e.b.m(ETH)["crypto_model"] = {"usd": "4.25", "rate_usd_s": 0, "t": 0}
    rows = [trade(ETH, 100, "0.10"), trade(ETH, 100)]
    e.crypto_reward_credits({"ok": True, "rows": 2, "rows_all": [
        {"marketSlug": ETH, "status": "PENDING", "reward": "1.79"},
        {"marketSlug": "aec-x", "status": "PAID", "reward": "5"}]})
    capsys.readouterr()
    e.crypto_meter(rows, e.b.d["markets"], venue_net={})
    d = pnl_line(capsys)
    assert D(d["realized_trading_net_usd"]) == D("3.90")   # 4.00 - 0.10 fee
    assert D(d["maker_fees_charged_usd"]) == D("0.10")
    assert d["outstanding_contracts"] == "0" and d["positions_checked"]
    assert d["modelled_reward_usd"] == "4.25"
    assert d["venue_pending_reward_usd"] == "1.79"
    assert d["venue_credited_reward_usd"] == "0"
    assert D(d["realized_net_usd"]) == D("3.90")           # no modelled, no pending
    assert d["verdict"] == "FAIL_OR_AWAITING_CREDIT"       # no credit row yet
    assert d["completed_pairs"] == "100"
    e.crypto_reward_credits({"ok": True, "rows": 1, "rows_all": [
        {"marketSlug": ETH, "status": "PAID", "reward": "1.79"}]})
    e.crypto_meter(rows, e.b.d["markets"], venue_net={})
    d = pnl_line(capsys)
    assert D(d["realized_net_usd"]) == D("5.69") and d["verdict"] == "PASS"


def test_an_unpaired_leg_is_outstanding_not_realized(lane, capsys):
    e, v = lane
    select(e, v)
    lone_yes(e, v)                                   # YES 100 @0.47, lone
    capsys.readouterr()
    e.crypto_meter([trade(ETH, 100)], e.b.d["markets"],
                   venue_net={ETH: "100"})
    d = pnl_line(capsys)
    assert D(d["realized_trading_net_usd"]) == 0
    assert d["outstanding_contracts"] == "100"
    assert D(d["outstanding_cost_usd"]) == D("47")
    assert D(d["open_residual_worst_usd"]) == D("-47")
    assert d["verdict"] == "INCOMPLETE"
    assert e.crypto_meter_block() is None


def test_unconfirmed_venue_fills_or_positions_block_new_quotes(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    for k in list(v.live(ETH)):
        v.fill(k)
    e.step_market(ETH)
    e.crypto_meter([trade(ETH, 100)], e.b.d["markets"],
                   venue_net={})                     # venue saw 100 of 200
    assert e.crypto_meter_block() == "crypto_fills_unconfirmed"
    assert e.crypto_block(SOL, e.b.m(SOL), time.time()) == \
        "crypto_fills_unconfirmed"
    both = [trade(ETH, 100), trade(ETH, 100)]
    e.crypto_meter(both, e.b.d["markets"], venue_net={ETH: "30"})
    assert e.crypto_meter_block() == "crypto_fills_unconfirmed"  # not netted
    e.crypto_meter(both, e.b.d["markets"])           # no positions walk
    assert e.crypto_meter_block() == "crypto_positions_unchecked"
    e.crypto_meter(both, e.b.d["markets"], venue_net={})
    assert e.crypto_meter_block() is None


def test_modelled_reward_accrues_only_while_quotes_rest(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    cm = e.b.m(ETH)["crypto_model"]
    assert cm["rate_usd_s"] > 0 and D(cm["usd"]) == 0
    cm["t"] = time.time() - 10
    e.step_market(ETH)
    assert D(cm["usd"]) == pytest.approx(D(str(cm["rate_usd_s"] * 10)),
                                         rel=D("0.05"))
    cm["t"] = time.time() - 600                      # a gap accrues nothing
    before = cm["usd"]
    e.step_market(ETH)
    assert cm["usd"] == before
    assert e.crypto_status()["modelled_reward_usd"] == before


def test_earnings_audit_hands_over_every_row():
    from src import pmus_incentive_audit as IA

    class Tr:
        def request(self, method, path):
            return {"earnings": [{"marketSlug": ETH, "status": "PAID",
                                  "reward": "1.00"}] * 80}
    out = IA.earnings(Tr())
    assert len(out["rows_all"]) == 80 and len(out["rows_detail"]) == 50


def test_the_probe_image_carries_the_lane():
    root = Path(__file__).resolve().parents[1]
    docker = (root / "deploy/pmus_spring_l5/Dockerfile").read_text()
    assert "COPY src/pmus_crypto_feed.py src/pmus_crypto_feed.py" in docker
    assert "COPY src/pmus_crypto_rewards.py src/pmus_crypto_rewards.py" in \
        docker
    assert "COPY src/pmus_crypto_tape.py src/pmus_crypto_tape.py" in docker
    assert "COPY src/pmus_sports_tape.py src/pmus_sports_tape.py" in docker
    assert '"src/pmus_crypto_rewards.py"' in \
        (root / "deploy/pmus_spring_l5/railway.json").read_text()


# ------------------------------------------------------- hard loss budget

def test_hard_loss_budget_is_the_default():
    assert CR.HARD_LOSS_BUDGET is True
    assert CR.approval_template()["hard_loss_budget"] is True


def test_quotes_are_sized_to_the_leg_budget():
    """$30 for one market: the dearer leg (NO 0.49) x 61 = $29.89."""
    q = Q(max_leg_usd=D("30"))
    assert q["YES"]["qty"] == q["NO"]["qty"] == D("61")
    small = Q(max_leg_usd=D("10"))                 # 20 contracts < MIN_QTY
    assert small["YES"] is None and small["why_YES"] == "loss_budget"


def test_the_budget_is_split_over_the_markets_and_shrinks_with_losses(
        monkeypatch):
    markets = {ETH: mkt(), SOL: mkt(), XRP: mkt()}
    assert CR.leg_budget(markets, ETH) == D("10")
    monkeypatch.setattr(CR, "MAX_MARKETS", 1)
    assert CR.leg_budget(markets, ETH) == D("30")
    lost = {**markets, XRP: mkt("20", "0", "9", "0", done=True,
                                settle_px="0")}                  # -$9
    assert CR.unpaired_cap(lost) == D("21")
    won = {**markets, XRP: mkt("20", "0", "9", "0", done=True,
                               settle_px="1")}                   # +$11
    assert CR.unpaired_cap(won) == D("30")          # gains do not enlarge it
    held = {**markets, SOL: mkt("40", "0", "19", "0")}
    assert CR.leg_budget(held, ETH) == D("11")


@pytest.fixture
def hard(lane, monkeypatch):
    """One market, hard $30 budget, approval matching that configuration."""
    e, v = lane
    monkeypatch.setattr(CR, "HARD_LOSS_BUDGET", True)
    monkeypatch.setattr(CR, "MAX_MARKETS", 1)
    monkeypatch.setattr(CR, "ASSETS", ("eth",))
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(approval()))
    return e, v


def test_hard_budget_worst_case_never_exceeds_the_stop(hard):
    """Fill the dear side alone and settle it worthless: the loss is the
    leg's cost, which was sized inside $30. Nothing reopens afterwards."""
    e, v = hard
    assert select(e, v) == [ETH]
    e.step_market(ETH)
    legs = {o["side"]: o for o in v.live(ETH).values()}
    assert legs["YES"]["qty"] == legs["NO"]["qty"] == D("61")
    assert max(o["px"] * o["qty"] for o in legs.values()) <= D("30")
    no = next(k for k, o in v.live(ETH).items() if o["side"] == "NO")
    v.fill(no)
    e.step_market(ETH)
    live = list(v.live(ETH).values())
    assert [(o["side"], o["qty"]) for o in live] == [("YES", D("61"))]
    mk = e.b.m(ETH)
    for k in list(v.live(ETH)):
        v.cancel(k)
    mk.update(done=True, settle_px="1")              # NO expires worthless
    assert CR.book_pnl(e.b.d["markets"])["usd"] == D("-29.89")
    assert CR.book_pnl(e.b.d["markets"])["usd"] >= -CR.LOSS_STOP_USD
    assert CR.unpaired_cap(e.b.d["markets"]) == D("0.11")
    nxt = ETH.replace("11-21", "11-22")
    v.crypto = [nxt]
    e.select_crypto(v.account())
    e.step_market(nxt)
    assert not v.live()                              # budget is spent


def test_hard_budget_refuses_the_proposal_size_across_three_markets(
        lane, monkeypatch):
    """100 a side on three markets cannot fit a $30 worst case: $10 a
    market is 20 contracts, under the 25 minimum -- nothing is quoted."""
    e, v = lane
    monkeypatch.setattr(CR, "HARD_LOSS_BUDGET", True)
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(approval()))
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    assert not v.orders


# ------------------------- hard budget: three markets, inventory, restart

@pytest.fixture
def hard3(lane, monkeypatch):
    """Hard $30 budget across ETH + SOL + XRP ($10 a market; the minimum
    size is lowered so $10 can quote at all)."""
    e, v = lane
    monkeypatch.setattr(CR, "HARD_LOSS_BUDGET", True)
    monkeypatch.setattr(CR, "MIN_QTY", D("10"))
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(approval()))
    return e, v


def worst_case(e):
    """Every unpaired leg lost, every market's dearest resting opening leg
    filled alone and lost, plus what is already closed."""
    m = e.b.d["markets"]
    ex = CR.exposure(m)
    return ex["unpaired_open"] + sum((CR._worst_new_leg(m[s]) for s in
                                      ex["by_market"]), D(0)) - \
        min(D(0), CR.closed_pnl(m))


def test_three_markets_resting_at_once_stay_inside_the_budget(hard3):
    e, v = hard3
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    assert len(v.live()) == 6
    assert {o["qty"] for o in v.live().values()} == {D("20")}  # 10 / 0.49
    assert worst_case(e) == D("29.40") <= CR.LOSS_STOP_USD


def test_every_dear_leg_filled_and_lost_is_still_inside_the_budget(hard3):
    """All three NO legs fill alone and all three settle worthless."""
    e, v = hard3
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    for k, o in list(v.live().items()):
        if o["side"] == "NO":
            v.fill(k)
    for _ in range(2):
        for s in (ETH, SOL, XRP):
            e.step_market(s)
    live = list(v.live().values())
    assert sorted(o["side"] for o in live) == ["YES"] * 3      # completions
    assert {e.b.m(s)["orders"][k]["stage"] for s in (ETH, SOL, XRP)
            for k in e.b.m(s)["orders"] if e.b.m(s)["orders"][k]["open"]} \
        == {"crypto_complete"}
    assert worst_case(e) == D("29.40")
    for k in list(v.live()):
        v.cancel(k)
    for s in (ETH, SOL, XRP):
        e.b.m(s).update(done=True, settle_px="1")
    assert CR.book_pnl(e.b.d["markets"])["usd"] == D("-29.40")
    assert CR.unpaired_cap(e.b.d["markets"]) == D("0.60")


def test_existing_inventory_and_resting_orders_shrink_the_next_quote(hard3):
    """ETH holds a lone leg ($9.80), SOL has quotes resting ($9.80
    strandable): XRP may only add what is left, and the choke point refuses
    anything larger."""
    e, v = hard3
    select(e, v)
    lone_yes_side(e, v, ETH, "NO")
    e.step_market(SOL)
    assert CR.leg_budget(e.b.d["markets"], XRP) == D("10")
    assert e.crypto_place_block(XRP, "NO", D("0.49"), D("22"),
                                "crypto_open", True) == "crypto_loss_budget"
    assert e.crypto_place_block(XRP, "NO", D("0.49"), D("20"),
                                "crypto_open", True) is None
    e.step_market(XRP)
    assert worst_case(e) == D("29.40")
    e.b.m(ETH).update(done=True, settle_px="1")      # ETH NO leg lost: -9.80
    for _ in range(2):
        for s in (SOL, XRP):
            e.step_market(s)
    assert CR.unpaired_cap(e.b.d["markets"]) == D("20.20")
    assert worst_case(e) <= CR.LOSS_STOP_USD
    # the smaller budget is shared again: resting quotes are re-sized
    nxt = ETH.replace("11-21", "11-22")
    v.crypto = [nxt]
    e.select_crypto(v.account())
    e.step_market(nxt)
    assert {o["qty"] for s_ in (SOL, XRP, nxt)
            for o in v.live(s_).values()} == {D("13")}           # 6.73/0.49
    assert all(len(v.live(s_)) == 2 for s_ in (SOL, XRP, nxt))
    assert worst_case(e) == D("9.80") + 3 * D("6.37")
    assert worst_case(e) <= CR.LOSS_STOP_USD


def lone_yes_side(e, v, slug, side):
    e.step_market(slug)
    k = next(k for k, o in v.live(slug).items() if o["side"] == side)
    v.fill(k)
    e.step_market(slug)


def test_restart_with_unresolved_positions_keeps_the_budget(hard3, tmp_path):
    """Crash while ETH holds a lone NO leg, SOL has closed unpaired with no
    settlement price yet, and XRP quotes rest. The new process: pulls the
    resting quotes until the venue-reconciled meter and a fresh account
    read exist, keeps the ETH completion, and then sizes XRP to what the
    unresolved positions leave -- counting the unread SOL leg as lost."""
    e, v = hard3
    select(e, v)
    lone_yes_side(e, v, ETH, "NO")                    # NO 20 @0.49 = 9.80
    lone_yes_side(e, v, SOL, "YES")                   # YES 20 @0.47 = 9.40
    for k in list(v.live(SOL)):
        v.cancel(k)
    e.step_market(SOL)
    e.b.m(SOL)["done"] = True                         # closed, px unread
    e.step_market(XRP)
    e.b.save()
    assert len(v.live(XRP)) == 2
    e2 = M.Engine(v, M.Book(tmp_path / "s.json"))     # restart: no meter,
    wire_feed(e2, v)                                  # no account read yet
    e2.account_risk = lambda fresh=False: {"ok": True, "lane_cap_usd": "1000"}
    assert e2.crypto_block(XRP, e2.b.m(XRP), time.time()) == \
        "crypto_meter_unavailable"
    for s in (ETH, XRP):
        e2.step_market(s)
    assert not v.live(XRP)                            # openings pulled
    assert [(o["side"], o["qty"]) for o in v.live(ETH).values()] == \
        [("YES", D("20"))]                            # completion kept
    fresh_meter(e2)
    assert e2.crypto_block(XRP, e2.b.m(XRP), time.time()) == \
        "account_state_unavailable"
    e2.step_market(XRP)
    assert not v.live(XRP)
    e2.crypto_account_view = v.account
    assert CR.unpaired_cap(e2.b.d["markets"]) == D("20.60")   # 30 - 9.40
    e2.step_market(XRP)
    legs = list(v.live(XRP).values())
    assert len(legs) == 2 and {o["qty"] for o in legs} == {D("14")}
    assert worst_case(e2) <= CR.LOSS_STOP_USD
    assert worst_case(e2) == D("9.40") + D("9.80") + D("6.86")


def test_venue_inventory_the_book_does_not_know_stops_the_lane(hard3):
    """A position at the venue that the book lacks could already have used
    the budget: no market may open until the two agree."""
    e, v = hard3
    select(e, v)
    e.step_market(ETH)
    v.acct["positions"] = {SOL: D("40")}              # unknown to the book
    e.step_market(ETH)
    e.step_market(XRP)
    assert not v.live()
    assert e.crypto_block(XRP, e.b.m(XRP), time.time()) == \
        "venue_inventory_mismatch:" + SOL
    v.acct["positions"] = {}
    e.step_market(XRP)
    assert len(v.live(XRP)) == 2


def test_an_unreadable_book_entry_fails_closed(hard3):
    """Corrupt inventory in one crypto market: the budget cannot be
    computed, so resting quotes everywhere are pulled and none is placed."""
    e, v = hard3
    select(e, v)
    e.step_market(ETH)
    e.b.m(SOL)["cost"]["YES"] = "garbage"
    e.b.m(SOL)["pos"]["YES"] = "5"
    e.crypto_account_view = lambda: {"positions": {SOL: D("5")},
                                     "open_orders": []}
    e.step_market(ETH)
    assert not v.live()
    assert e.crypto_place_block(XRP, "YES", D("0.47"), D("10"),
                                "crypto_open", True) is not None


# ------------------------------------------- disarmed observation mode

@pytest.fixture
def observe(tmp_path, monkeypatch):
    """What a disarmed observation release runs: lane on, NOTHING else set.
    No arm key, no approval, no combined ceiling, account-risk gate as
    deployed, no venue meter. Any attempt to send an order fails the test."""
    monkeypatch.setattr(CR, "ON", True)
    for k in (CR.ARM_ENV, CR.APPROVAL_ENV, CR.COMBINED_CAP_ENV, CR.SHA_ENV):
        monkeypatch.delenv(k, raising=False)
    v = CV()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    wire_feed(e, v)
    return e, v


def tape_rows(e):
    out = []
    for f in sorted((e.b.path.parent / "crypto_tape").glob("tape_*.jsonl")):
        out += [json.loads(ln) for ln in f.read_text().splitlines()]
    return out


def test_observation_mode_records_and_never_submits(observe, monkeypatch):
    e, v = observe
    monkeypatch.setattr(v, "place",
                        lambda *a, **k: pytest.fail("order submitted"))
    assert sorted(select(e, v)) == [ETH, SOL, XRP]
    v.books[SOL] = SOL_LIVE
    for _ in range(3):
        for s in (ETH, SOL, XRP):
            e.step_market(s)
    rows = tape_rows(e)
    assert sorted(r["slug"] for r in rows) == [ETH, SOL, XRP]   # 30 s gap
    eth = next(r for r in rows if r["slug"] == ETH)
    assert eth["b"][0] == [0.48, 300.0] and eth["a"][0] == [0.5, 300.0]
    assert eth["obs"]["armed"] is False
    assert eth["obs"]["quote"]["YES"] == ["0.47", "100", "open"]  # the rule
    assert eth["obs"]["sized"]["YES"] is None            # $30 over 3 markets
    assert eth["obs"]["sized_why"]["YES"] == "loss_budget"
    assert eth["obs"]["elig"]["pays"] and eth["obs"]["elig"]["yes_inside"]
    sol = next(r for r in rows if r["slug"] == SOL)
    assert sol["obs"]["quote"] == {"YES": None, "NO": None}
    assert sol["obs"]["why"]["YES"] == "outside_scoring_walk"
    assert not v.orders and not v.cancelled
    assert e.b.d["crypto"]["days"] == [] and e.b.d["own_order_ids"] == []


@pytest.mark.parametrize("env", [
    {},
    {CR.ARM_ENV: "YES"},
    {CR.ARM_ENV: "YES", CR.APPROVAL_ENV: "{}"},
    {CR.ARM_ENV: "yes", CR.APPROVAL_ENV: json.dumps({"approved_by": "x"})},
    {CR.APPROVAL_ENV: "SEE_OWNER"},
])
def test_no_partial_key_set_can_submit(observe, monkeypatch, env):
    """Every stage and side, straight at the placement choke point."""
    e, v = observe
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    monkeypatch.setattr(v, "place",
                        lambda *a, **k: pytest.fail("order submitted"))
    select(e, v)
    assert CR.arm_state(time.time())["armed"] is False
    for stage in ("crypto_open", "crypto_complete", "rn1_open",
                  "two_sided_open", "two_sided_complete_taker"):
        for side in ("YES", "NO"):
            for post_only in (True, False):
                e.place(ETH, side, D("0.47"), D("10"), stage,
                        post_only=post_only)
    e.step_market(ETH)


def test_crypto_step_has_no_route_to_the_venue_but_the_choke_point():
    """Structural: the lane's engine code never calls the venue's place
    itself, and has no sell or taker call at all."""
    import inspect
    for name in ("_crypto_step", "_crypto_observe", "_crypto_fail_closed",
                 "select_crypto", "crypto_loss_stopped", "_crypto_book",
                 "crypto_meter", "crypto_reward_credits", "_crypto_pass",
                 "_crypto_pass_inner", "_crypto_stale"):
        src = inspect.getsource(getattr(M.Engine, name))
        assert "self.v.place" not in src and "post_only=False" not in src
        assert "place_limit" not in src and ".t.request(" not in src
        assert "gget" not in src and "_reward_get" not in src
    assert inspect.getsource(M.Engine._crypto_step).count("self.place(") == 1
    for mod in (CR, importlib.import_module("src.pmus_crypto_tape")):
        src = inspect.getsource(mod)
        assert "urllib" not in src and "request(" not in src
        assert "import socket" not in src and "http" not in src.lower()


def test_observation_leaves_sports_orders_and_the_registry_alone(observe):
    """The watchdog's ownership list still names every live order, and
    observing crypto adds none and removes none."""
    e, v = observe
    e.place("aec-x", "YES", D("0.45"), D("2"), "rn1_open")
    e.place("aec-y", "NO", D("0.52"), D("2"), "rn1_open")
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    listed = {o["venue_order_id"] for o in M.registry_doc(e.b)["orders"]
              if not o.get("closing")}
    assert listed == set(v.live()) and len(listed) == 2
    assert not v.cancelled


def test_registry_names_every_live_order_of_both_lanes(lane):
    """Armed (test only): sports and crypto orders are all in the list the
    watchdog reads; a cancelled one stays listed as closing for the grace."""
    e, v = lane
    e.place("aec-x", "YES", D("0.45"), D("2"), "rn1_open")
    select(e, v)
    for s in (ETH, SOL):
        e.step_market(s)
    doc = M.registry_doc(e.b)
    live = {o["venue_order_id"] for o in doc["orders"] if not o.get("closing")}
    assert live == set(v.live()) and len(live) == 5
    assert doc["manages_own_resting"] and doc["lane"] == M.LANE
    e._crypto_meter = None                           # crypto quotes pulled
    e.step_market(ETH)
    doc = M.registry_doc(e.b)
    live = {o["venue_order_id"] for o in doc["orders"] if not o.get("closing")}
    closing = {o["venue_order_id"] for o in doc["orders"] if o.get("closing")}
    assert live == set(v.live()) and len(live) == 3 and len(closing) >= 2


def test_tape_storage_is_bounded(tmp_path):
    from src import pmus_crypto_tape as CT
    clock = [1_795_000_000.0]
    t = CT.CryptoTape(tmp_path / "ct", max_bytes_per_day=20_000,
                      keep_days=3, min_gap_s=30, clock=lambda: clock[0])
    big = book([(round(0.50 - i / 100, 2), 1000 + i) for i in range(45)],
               [(round(0.51 + i / 100, 2), 1000 + i) for i in range(45)])
    for day in range(10):
        for i in range(400):
            clock[0] = 1_795_000_000.0 + day * 86400 + i * 30
            t.snapshot(ETH, big, {"label": "x"})
            t.snapshot(ETH, big, {"label": "x"})     # inside the gap: skipped
    files = sorted((tmp_path / "ct").glob("*"))
    assert len(files) == 3                           # keep_days
    assert all(f.stat().st_size <= 20_000 for f in files)
    assert sum(f.stat().st_size for f in files) <= t.bound_bytes() == 60_000
    assert t.dropped > 0 and t.errors == 0           # full: dropped, counted
    row = json.loads(files[-1].read_text().splitlines()[0])
    assert len(row["b"]) == CT.MAX_LEVELS == 30      # depth is truncated
    assert CT.MAX_BYTES_PER_DAY * CT.KEEP_DAYS == 60_000_000   # default bound


def test_tape_failure_never_reaches_the_engine(observe, monkeypatch):
    e, v = observe
    (e.b.path.parent / "crypto_tape").write_text("a file, not a directory")
    select(e, v)
    e.step_market(ETH)
    assert e._crypto_tape().errors >= 1 and not v.orders


def test_tape_is_off_with_the_lane_and_can_be_switched_off(tmp_path,
                                                           monkeypatch):
    from src import pmus_crypto_tape as CT
    e = M.Engine(CV(), M.Book(tmp_path / "s.json"))
    assert e._crypto_tape() is None                  # lane off
    monkeypatch.setattr(CR, "ON", True)
    monkeypatch.setattr(CT, "ON", False)
    e = M.Engine(CV(), M.Book(tmp_path / "s.json"))
    assert e._crypto_tape() is None
    assert not (tmp_path / "crypto_tape").exists()


def test_replay_tool_reads_the_service_tape(observe, capsys):
    e, v = observe
    select(e, v)
    e.step_market(ETH)
    root = Path(__file__).resolve().parents[1]
    import runpy
    mod = runpy.run_path(str(root / "analysis/l5/rewards/"
                             "crypto_policy_replay.py"), run_name="replay")
    f = next((e.b.path.parent / "crypto_tape").glob("tape_*.jsonl"))
    mod["replay"](str(f))
    out = capsys.readouterr().out
    assert ETH in out and "1 tick back" in out and "quotable" in out


# ------------------------------- the sports loop never waits on crypto

class Hung:
    """A gateway that never answers until released."""

    def __init__(self):
        import threading
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        self.release.wait(30)
        raise TimeoutError("gateway hung")


def test_no_crypto_gateway_read_runs_on_the_engine_loop(lane):
    """Selection, quoting, repricing, a fill, the loss stop and the
    heartbeat: with the feed's thread as the only reader, the engine's own
    thread makes no gateway request for crypto at all."""
    import threading
    e, v = lane
    select(e, v)
    e.step_market(ETH)                    # feed polled, then the pass
    v.gateway_threads.clear()
    polled = threading.Thread(target=e.feed_sync, name="crypto-feed")
    polled.start()
    polled.join()
    feed_only = set(v.gateway_threads)
    loop_reads = []

    def forbidden(path):
        loop_reads.append(path)
        pytest.fail("gateway read on the engine loop: " + path)
    v.gget = v.spec = v.bbo = forbidden
    e._crypto_feed()._get = e._crypto_feed()._spec_fn = forbidden
    real_pass = M.Engine._crypto_pass.__get__(e)
    real_select = M.Engine.select_crypto.__get__(e)
    real_select(v.account())
    for s in (ETH, SOL, XRP):
        real_pass(s, e.b.m(s))
    yes = next(k for k, o in v.live(ETH).items() if o["side"] == "YES")
    v.fill(yes)
    real_pass(ETH, e.b.m(ETH))
    e.crypto_loss_stopped()
    e.crypto_status()
    assert feed_only == {"crypto-feed"} and loop_reads == []
    assert len(v.live(ETH)) == 1 and len(v.live()) == 5   # it did real work


def test_a_hung_gateway_cannot_delay_the_engine_loop(lane):
    """The real feed thread is stuck inside a request that never returns.
    Selection and a full pass over three crypto markets and a sports market
    still finish at once; the crypto quotes are pulled as their data ages
    out; the sports order is untouched."""
    from src import pmus_crypto_feed as CF
    e, v = lane
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    e.place("aec-x", "YES", D("0.45"), D("2"), "rn1_open")

    def crypto_live():
        return {k for k, o in v.live().items() if CR.is_crypto(o["slug"])}

    def sports_live():
        return {k for k, o in v.live().items() if o["slug"] == "aec-x"}
    quotes0 = crypto_live()
    assert len(quotes0) == 6 and len(sports_live()) == 1
    hung = Hung()
    clock = [time.monotonic()]
    feed = e._cfeed
    feed._get, feed._spec_fn = hung, hung
    feed._clock = lambda: clock[0]
    feed._sleep = lambda s_: None
    real_pass = M.Engine._crypto_pass.__get__(e)
    e._crypto_pass = real_pass                 # no test-side polling now
    e.select_crypto = M.Engine.select_crypto.__get__(e)
    feed._disc_at = -1e18
    feed.start()                               # the real thread, now stuck
    deadline = time.monotonic() + 5
    while hung.calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert hung.calls >= 1 and feed.stats()["alive"]
    try:
        t0 = time.monotonic()
        e.select_crypto(v.account())
        for s in (ETH, SOL, XRP):
            e.step_market(s)
        e.step_market("aec-x")
        e.report()
        took = time.monotonic() - t0
        assert took < 1.0, took
        assert crypto_live() == quotes0        # data still fresh: unchanged
        assert sports_live()                   # sports market was stepped
        clock[0] += CR.BOOK_MAX_AGE_S + 1      # ... until it ages out
        t0 = time.monotonic()
        for s in (ETH, SOL, XRP):
            e.step_market(s)
        e.step_market("aec-x")
        assert time.monotonic() - t0 < 1.0
        assert not crypto_live() and sports_live()   # crypto pulled only
        clock[0] += CR.SPEC_MAX_AGE_S + 2 * CR.DISCOVER_S + 100
        e.select_crypto(v.account())           # stale discovery: no select
        e.step_market(ETH)
        assert not crypto_live() and sports_live()
        assert hung.calls <= 2                 # one stuck request, no pile-up
    finally:
        feed.stop = True
        hung.release.set()


def test_feed_errors_age_the_data_and_never_raise(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    feed = e._cfeed

    def boom(path):
        raise OSError("503")
    feed._get = boom
    feed.poll_once(force=True)
    assert feed.errors >= 2 and "OSError" in feed.stats()["last_error"]
    book, age = feed.book(ETH)
    assert book is not None                    # last good answer is kept
    assert feed.spec("cpcxrp-nope")[0] is __import__(
        "src.pmus_crypto_feed", fromlist=["x"]).UNKNOWN


def test_feed_never_holds_credentials_or_a_signed_transport():
    import inspect
    from src import pmus_crypto_feed as CF
    src = inspect.getsource(CF)
    for bad in (".t.request", "self.v", "place(", "cancel(", "PMUS_API",
                "Transport", "os.environ", "getenv"):
        assert bad not in src, bad


def test_a_market_the_venue_closes_is_cancelled_and_closed_in_the_book(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    v.closed.add(ETH)
    e.step_market(ETH)
    assert not v.live(ETH) and e.b.m(ETH)["done"]


def test_unknown_market_spec_is_not_quoted(lane):
    e, v = lane
    select(e, v)
    real_pass = M.Engine._crypto_pass.__get__(e)
    mk = e.b.m("cpcxrp-xrp-updown-1d-2026-11-23-2200z")
    mk.update(lane="crypto", reward=dict(PROG), end=iso(9 * 3600))
    real_pass("cpcxrp-xrp-updown-1d-2026-11-23-2200z", mk)   # never polled
    assert not v.orders and mk["crypto_logged"] == "feed_stale:spec"


def test_the_sports_socket_is_not_given_crypto_markets(lane):
    e, v = lane
    select(e, v)

    class Ws:
        slugs = None
        def set_slugs(self, s):
            self.slugs = list(s)
    e.ws = Ws()
    e.b.m("aec-x")
    e.drain_ws_events()
    assert e.ws.slugs == ["aec-x"]


def test_the_step_is_timed_and_the_feed_reported(observe):
    e, v = observe
    select(e, v)
    e.step_market(ETH)
    st = e.crypto_status()
    assert st["step_ms"]["n"] == 1 and st["step_ms"]["max"] >= 0
    assert st["tape"]["bound_bytes"] == 60_000_000
    assert st["feed"]["cycles"] >= 1 and st["feed"]["errors"] == 0


def test_the_sports_submission_lock_withdraws_and_refuses_crypto(lane,
                                                                 monkeypatch):
    """Sports a9987a12f: the lock is fail-closed and acts on resting
    orders. It binds the crypto lane too -- openings and completions."""
    e, v = lane
    select(e, v)
    e.step_market(SOL)
    lone_yes(e, v)
    assert len(v.live()) == 3
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    e.enforce_lock()
    assert not v.live()
    n = len(v.orders)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    e.place(ETH, "NO", D("0.49"), D("100"), "crypto_complete")
    assert len(v.orders) == n


# ----------------------------- feed restart / recovery; empty cache = no order

def raw(e):
    """The engine's own pass and selection, without test-side polling."""
    e._crypto_pass = M.Engine._crypto_pass.__get__(e)
    e.select_crypto = M.Engine.select_crypto.__get__(e)
    return e


def crypto_live(v):
    return {k for k, o in v.live().items() if CR.is_crypto(o["slug"])}


def test_process_restart_with_an_empty_cache_places_nothing(lane, tmp_path):
    """New process, armed, book holding a lone leg and resting quotes, and
    the feed has read NOTHING yet (its first request is still in flight).
    Not one order is placed -- not an opening, not a completion -- and the
    quotes left resting by the old process are pulled."""
    e, v = lane
    select(e, v)
    e.step_market(SOL)
    lone_yes(e, v)                               # ETH: lone YES + completion
    before = len(v.orders)
    assert len(crypto_live(v)) == 3
    hung = Hung()
    e2 = wire(M.Engine(v, M.Book(tmp_path / "s.json")), v)
    fresh_meter(e2)
    raw(e2)
    e2._cfeed._get = e2._cfeed._spec_fn = hung
    e2._cfeed_autostart = True
    t0 = time.monotonic()
    try:
        e2.select_crypto(v.account())            # starts the (stuck) thread
        for s in (ETH, SOL, XRP):
            e2.step_market(s)
        e2.place(ETH, "NO", D("0.49"), D("100"), "crypto_complete")
        e2.place(XRP, "YES", D("0.47"), D("100"), "crypto_open")
        assert time.monotonic() - t0 < 1.0
        assert len(v.orders) == before           # nothing placed
        assert e2.crypto_place_block(ETH, "NO", D("0.49"), D("100"),
                                     "crypto_complete", True) == \
            "feed_stale:spec"
        live = [o for o in v.live().values()]
        assert [(o["slug"], o["side"]) for o in live] == [(ETH, "NO")]
        assert e2.b.m(ETH)["orders"][next(iter(v.live()))]["stage"] == \
            "crypto_complete"                    # only the old completion
        assert e2.crypto_status()["feed"]["alive"]
    finally:
        e2._cfeed.stop = True
        hung.release.set()


def test_the_feed_recovers_after_a_hang_and_quoting_resumes(lane):
    """Stuck -> quotes pulled as data ages -> the gateway answers again ->
    the same thread refreshes the cache -> quotes rest again. The engine
    loop never waited at any point."""
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    assert len(crypto_live(v)) == 2
    good_get, good_spec = v.gget, v.spec
    hung = Hung()
    feed = e._cfeed
    clock = [time.monotonic()]
    feed._clock = lambda: clock[0]
    feed._get = feed._spec_fn = hung
    feed._disc_at = -1e18
    raw(e)
    e._cfeed_autostart = True
    try:
        feed.start()
        deadline = time.monotonic() + 5
        while hung.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        clock[0] += CR.BOOK_MAX_AGE_S + 1
        t0 = time.monotonic()
        e.step_market(ETH)
        assert time.monotonic() - t0 < 1.0 and not crypto_live(v)
        n = len(v.orders)
        e.step_market(ETH)
        assert len(v.orders) == n                # stale: still nothing
        feed._get, feed._spec_fn = good_get, good_spec    # gateway is back
        hung.release.set()
        deadline = time.monotonic() + 10
        while feed.fresh(ETH) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert feed.fresh(ETH) is None           # recovered on its own
        e.step_market(ETH)
        assert len(crypto_live(v)) == 2 and feed.errors >= 1
    finally:
        feed.stop = True
        hung.release.set()


def test_a_dead_feed_thread_is_revived_and_never_permits_an_order(lane):
    """The reader thread dies outright. Its cache ages, so quotes are
    pulled and nothing is placed; the next pass starts it again."""
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    feed = e._cfeed
    clock = [time.monotonic()]
    feed._clock = lambda: clock[0]

    def die():
        raise SystemExit                         # not caught by the loop
    feed._run = die
    raw(e)
    e._cfeed_autostart = True
    import threading
    hook, threading.excepthook = threading.excepthook, lambda a: None
    try:
        feed.start()
        feed._thread.join(5)
        assert not feed._thread.is_alive()
        clock[0] += CR.BOOK_MAX_AGE_S + 1
        del feed._run                            # the real loop again
        feed._sleep = lambda s_: time.sleep(0.01)
        n = len(v.orders)
        e.step_market(ETH)                       # stale -> pulled; revived
        assert not crypto_live(v) and len(v.orders) == n
        assert feed.restarts == 1
        deadline = time.monotonic() + 10
        while feed.fresh(ETH) is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        e.step_market(ETH)
        assert len(crypto_live(v)) == 2
    finally:
        feed.stop = True
        threading.excepthook = hook


@pytest.mark.parametrize("gap,why", [
    (lambda f: f._spec.clear(), "feed_stale:spec"),
    (lambda f: f._book.clear(), "feed_stale:book"),
    (lambda f: f._book.update({ETH: (f._clock(), {"bids": [], "offers": []})}),
     "feed_stale:book"),
    (lambda f: f._book.update({ETH: (f._clock(), book([(0.48, 300)], []))}),
     "feed_stale:book"),
    (lambda f: f._book.update({ETH: (f._clock(), book([(0.50, 300)],
                                                      [(0.48, 300)]))}),
     "feed_stale:book"),
    (lambda f: f._book.update({ETH: (f._clock() - 3600, DEEP)}),
     "feed_stale:book"),
    (lambda f: f._spec.update({ETH: (f._clock() - 3600,
                                     {"tick": T, "min_qty": D("1")})}),
     "feed_stale:spec"),
    (lambda f: f._spec.update({ETH: (f._clock(), None)}), "market_closed"),
])
def test_any_gap_in_the_cache_refuses_every_crypto_order(lane, gap, why):
    """Missing, empty, one-sided, crossed, old or closed: the pass places
    nothing and the choke point refuses openings AND completions."""
    e, v = lane
    select(e, v)
    lone_yes(e, v)                               # a completion is wanted
    for k in list(v.live()):
        v.cancel(k)
    e.step_market(ETH)
    for k in list(v.live()):
        v.cancel(k)
    raw(e)
    gap(e._cfeed)
    n = len(v.orders)
    e.step_market(ETH)
    e.step_market(ETH)
    assert len(v.orders) == n and not v.live()
    for side, stage in (("NO", "crypto_complete"), ("YES", "crypto_open")):
        assert e.crypto_place_block(ETH, side, D("0.49"), D("100"), stage,
                                    True) == why
        e.place(ETH, side, D("0.49"), D("100"), stage)
    assert len(v.orders) == n


def test_missing_discovery_or_programmes_select_and_quote_nothing(lane):
    e, v = lane
    raw(e)
    e.select_crypto(v.account())                 # nothing read yet
    assert not [s for s in e.active() if CR.is_crypto(s)]
    e.feed_sync()
    e._cfeed._progs = (e._cfeed._clock(), {})    # programmes: none paying
    e.select_crypto(v.account())
    assert not [s for s in e.active() if CR.is_crypto(s)] and not v.orders


# --------------------- minimum-liquidity gate: crypto reads the cache only

def thin(bid_qty, ask_qty, bid=0.48, ask=0.50):
    """Depth that still scores (target reached behind the touch) but shows
    only bid_qty / ask_qty contracts at the best prices."""
    b, a = round(bid * 100), round(ask * 100)
    return book([(b / 100, bid_qty), ((b - 1) / 100, 900),
                 ((b - 2) / 100, 1500)],
                [(a / 100, ask_qty), ((a + 1) / 100, 900),
                 ((a + 2) / 100, 1500)])


def test_crypto_openings_never_call_the_gateway_for_depth(lane):
    """Sports 08b1a7c93 put depth_block -> Venue.top_depth (a gateway GET)
    in Engine.place for every opening. CV.top_depth fails the test if it is
    called for a crypto slug; a sports opening still uses it."""
    e, v = lane
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    assert len(crypto_live(v)) == 6
    assert v.top_depth_calls == []
    assert e.depth_block(ETH, "YES") is None and v.top_depth_calls == []
    e.place("aec-x", "YES", D("0.45"), D("2"), "rn1_open")
    assert v.top_depth_calls == ["aec-x"]            # sports gate unchanged


def test_real_venue_depth_read_is_not_reached_for_crypto(lane, monkeypatch):
    """Against the REAL Venue.top_depth: any gateway call fails the test."""
    e, v = lane
    select(e, v)
    e.feed_sync()
    real = M.Venue.__new__(M.Venue)
    real.q1 = v.q1
    real.gget = lambda path: pytest.fail("gateway read: " + path)
    v.top_depth = real.top_depth
    assert e.depth_block(ETH, "YES") is None
    assert e.depth_block(ETH, "NO") is None
    e.step_market(ETH)
    assert len(v.live(ETH)) == 2
    with pytest.raises(BaseException, match="gateway read"):
        v.top_depth(ETH)                             # the path being avoided


@pytest.mark.parametrize("gap", [
    lambda f: f._book.clear(),
    lambda f: f._spec.clear(),
    lambda f: f._book.update({ETH: (f._clock() - 3600, DEEP)}),
    lambda f: f._book.update({ETH: (f._clock(), {"bids": [], "offers": []})}),
    lambda f: f._book.update({ETH: (f._clock(), book([(0.48, 300)], []))}),
    lambda f: f._book.update({ETH: (f._clock(), book([(0.50, 300)],
                                                     [(0.48, 300)]))}),
    lambda f: f._book.update({ETH: (f._clock(), {"bids": [
        {"px": {"value": "n/a"}, "qty": "x"}], "offers": "garbage"})}),
    lambda f: f._book.update({ETH: (f._clock(), "not a book")}),
    lambda f: f._spec.update({ETH: (f._clock(), None)}),
])
def test_missing_stale_or_invalid_cached_depth_fails_closed(lane, gap):
    e, v = lane
    select(e, v)
    e.feed_sync()
    assert e.depth_block(ETH, "YES") is None
    gap(e._cfeed)
    for side in ("YES", "NO"):
        assert e.depth_block(ETH, side) == "depth_unknown"
    assert v.top_depth_calls == []                   # and still no request


def test_liquidity_floor_applies_to_crypto_from_the_cache(lane):
    """Fewer than 10 contracts that are not ours at a side's touch: no new
    bid on that side -- and with both sides required, no quote at all."""
    e, v = lane
    select(e, v)
    v.books[ETH] = thin(4, 300)
    e.feed_sync()
    assert e.depth_block(ETH, "YES") == "thin_touch:4.0<10"
    assert e.depth_block(ETH, "NO") is None
    e.step_market(ETH)
    e.step_market(ETH)
    assert not v.live(ETH)
    v.books[ETH] = thin(300, 9.5)
    e.feed_sync()
    assert e.depth_block(ETH, "NO") == "thin_touch:9.5<10"
    v.books[ETH] = thin(10, 10)
    e.step_market(ETH)
    assert len(v.live(ETH)) == 2 and v.top_depth_calls == []


def test_liquidity_gate_never_blocks_a_crypto_completion(lane):
    e, v = lane
    select(e, v)
    lone_yes(e, v)
    v.books[ETH] = thin(1, 1)
    for k in list(v.live(ETH)):
        v.cancel(k)
    e.step_market(ETH)
    assert [(o["side"], o["qty"]) for o in v.live(ETH).values()] == \
        [("NO", D("100"))]


def test_own_resting_size_at_the_touch_does_not_count(lane, monkeypatch):
    """Joining the best price (explicit setting): our own 100 there is not
    liquidity. 105 shown - 100 ours = 5 < 10."""
    e, v = lane
    select(e, v)
    e.feed_sync()
    mk = e.b.m(ETH)
    mk["orders"]["x"] = {"side": "YES", "px": "0.48", "qty": "100",
                         "filled": "0", "open": True, "stage": "crypto_open"}
    e._cfeed._book[ETH] = (e._cfeed._clock(), thin(105, 300))
    assert e.depth_block(ETH, "YES") == "thin_touch:5.0<10"


# ------------------------------- the account's daily halt covers both lanes

def test_a_crypto_loss_trips_the_daily_halt_for_sports_and_crypto(
        lane, monkeypatch):
    """Owner 2026-11-22: the $5 daily halt must keep covering whatever
    shares the account. A crypto leg marked $9 down stops NEW openings in
    both lanes for the UTC day; the completion keeps resting."""
    e, v = lane
    monkeypatch.setattr(M, "DAILY_LOSS_USD", D("5"))
    monkeypatch.setattr(RN1, "ON", False)
    select(e, v)
    e.step_market(SOL)
    lone_yes(e, v)                                   # ETH YES 100 @0.47
    assert not e.daily_stopped()
    v.books[ETH] = around(0.38, 0.40)                # -$9 on the day
    e.step_market(ETH)
    assert e.day_change() == D("-9") and e.daily_stopped()
    assert e.crypto_block(SOL, e.b.m(SOL), time.time()) == "daily_loss_stop"
    e.step_market(SOL)
    assert not v.live(SOL)                           # crypto openings pulled
    assert [(o["side"], o["qty"]) for o in v.live(ETH).values()] == \
        [("NO", D("100"))]                           # completion continues
    assert not e.b.d["crypto"]["loss_stop"]          # its own $30 stop: no
    e.b.m("aec-x")
    n = len(v.orders)
    e.step_market("aec-x")                           # sports: no new pair
    assert len(v.orders) == n


def test_a_sports_loss_trips_the_daily_halt_for_crypto(lane, monkeypatch):
    e, v = lane
    monkeypatch.setattr(M, "DAILY_LOSS_USD", D("5"))
    select(e, v)
    e.step_market(ETH)
    assert len(v.live(ETH)) == 2
    mk = e.b.m("aec-x")
    mk.update(pos={"YES": "20", "NO": "0"}, cost={"YES": "9", "NO": "0"},
              mark0={"date": e._today(), "value": "0"}, mark="-6")
    assert e.daily_stopped()
    e.step_market(ETH)
    assert not v.live(ETH)
    assert e.crypto_place_block(ETH, "YES", D("0.47"), D("100"),
                                "crypto_open", True) == "daily_loss_stop"


def test_both_lanes_losses_add_up_in_the_daily_halt(lane, monkeypatch):
    """Neither lane alone is past $5; together they are."""
    e, v = lane
    monkeypatch.setattr(M, "DAILY_LOSS_USD", D("5"))
    select(e, v)
    lone_yes(e, v)
    v.books[ETH] = around(0.44, 0.46)                # crypto -$3
    e.step_market(ETH)
    assert e.day_change() == D("-3") and not e.daily_stopped()
    mk = e.b.m("aec-x")
    mk.update(mark0={"date": e._today(), "value": "0"}, mark="-2.50")
    assert e.day_change() == D("-5.50") and e.daily_stopped()


# ------------------- under the account's REAL final gate, clip and latch
# Owner 2026-11-27: preserve the shared daily halt, watchdog, aggregate
# exposure and all existing clip limits; do not bypass the global latch to
# accommodate larger crypto quotes; test crypto with the existing
# authorized clip constraints.

@pytest.fixture
def clipped(tmp_path, monkeypatch):
    """Armed lane with NOTHING of the sports protections switched off:
    final gate on, RN1 clip and caps as shipped, hard loss budget on. The
    lane is configured inside the clip (2 contracts)."""
    assert M.FINAL_GATE and M.rn1_max_clip() == D("2")
    monkeypatch.setattr(RN1, "ON", True)
    monkeypatch.setattr(CR, "QTY", D("2"))
    monkeypatch.setattr(CR, "MIN_QTY", D("1"))
    arm_env(monkeypatch)
    monkeypatch.setattr(M, "ACCOUNT_RISK_ON", True)
    v = CV()
    e = wire(M.Engine(v, M.Book(tmp_path / "s.json")), v)
    fresh_meter(e)
    return e, v


def test_final_gate_is_in_force_for_crypto_orders(clipped):
    e, v = clipped
    assert e.crypto_global_limits() == {"clip": D("2"),
                                        "pair_cap": D(str(RN1.PAIR_CAP))}
    assert e.final_gate(ETH, "YES", D("0.47"), D("100"), "crypto_open") == \
        "size_above_maximum:100>2"
    assert e.final_gate(ETH, "YES", D("0.47"), D("2"), "crypto_open",
                        post_only=False) == "taker_not_authorized"


def test_lane_inside_the_clip_quotes_two_contracts_and_trips_nothing(
        clipped):
    e, v = clipped
    select(e, v)
    for s in (ETH, SOL, XRP):
        e.step_market(s)
    got = sorted((o["slug"], o["side"], o["qty"]) for o in v.live().values())
    assert got == sorted((s, sd, D("2")) for s in (ETH, SOL, XRP)
                         for sd in ("YES", "NO"))
    e.safety_check(v.account())
    assert e.safety_latch() is None                 # no clip breach
    listed = {o["venue_order_id"] for o in M.registry_doc(e.b)["orders"]
              if not o.get("closing")}
    assert listed == set(v.live())                  # watchdog list complete
    assert v.top_depth_calls == []


def test_lane_configured_above_the_clip_quotes_nothing(clipped, monkeypatch):
    """The proposal's 100 a side under the real clip: refused by the lane
    before any order is built, so nothing reaches the venue, nothing rests,
    and the latch has nothing to trip on."""
    e, v = clipped
    monkeypatch.setattr(CR, "QTY", D("100"))
    monkeypatch.setattr(CR, "MIN_QTY", D("25"))
    monkeypatch.setattr(CR, "HARD_LOSS_BUDGET", False)
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(approval()))
    assert CR.arm_state(time.time())["armed"]
    monkeypatch.setattr(v, "place",
                        lambda *a, **k: pytest.fail("order submitted"))
    select(e, v)
    e.step_market(ETH)
    e.step_market(ETH)
    assert e.crypto_block(ETH, e.b.m(ETH), time.time()) == \
        "qty_above_global_clip:100>2"
    assert e.crypto_place_block(ETH, "YES", D("0.47"), D("100"),
                                "crypto_open", True) == \
        "qty_above_global_clip:100>2"
    e.place(ETH, "YES", D("0.47"), D("100"), "crypto_open")
    e.safety_check(v.account())
    assert e.safety_latch() is None and not v.orders


def test_the_global_latch_is_not_bypassed_for_a_crypto_order(clipped):
    """If an over-clip crypto order ever DID rest (here: forced into the
    book), the account's safety latch trips exactly as for any market, and
    the lane places nothing more."""
    e, v = clipped
    select(e, v)
    e.feed_sync()
    v.orders["big"] = {"slug": ETH, "side": "YES", "px": D("0.47"),
                       "qty": D("100"), "filled": D(0), "open": True}
    e.b.m(ETH)["orders"]["big"] = {
        "side": "YES", "px": "0.47", "qty": "100", "filled": "0",
        "open": True, "stage": "crypto_open", "placed_at": time.time()}
    e.safety_check(v.account())
    latch = e.safety_latch()
    assert latch and latch["why"] == "order_qty_exceeds_clip"
    n = len(v.orders)
    e.place(SOL, "YES", D("0.47"), D("2"), "crypto_open")
    e.place(SOL, "NO", D("0.49"), D("2"), "crypto_complete")
    assert len(v.orders) == n                       # latched: all refused


def test_completion_is_priced_inside_the_global_pair_cap(clipped):
    """YES 2 @0.47 held, market falls to 0.30 / 0.32. The lane alone would
    complete up to a 1.00 pair (NO 0.53); the account's pair cap is 0.97,
    so the completion rests at NO 0.50 and the final gate accepts it."""
    e, v = clipped
    assert D(str(RN1.PAIR_CAP)) == D("0.97")
    select(e, v)
    e.step_market(ETH)
    yes = next(k for k, o in v.live(ETH).items() if o["side"] == "YES")
    v.fill(yes)
    v.books[ETH] = around(0.30, 0.32)
    e.step_market(ETH)
    e.step_market(ETH)
    live = list(v.live(ETH).values())
    assert [(o["side"], o["qty"], o["px"]) for o in live] == \
        [("NO", D("2"), D("0.50"))]
    assert e.final_gate(ETH, "NO", D("0.51"), D("2"),
                        "crypto_complete").startswith("pair_cost_above_cap")


def test_daily_halt_and_lock_still_bind_under_the_final_gate(clipped,
                                                             monkeypatch):
    e, v = clipped
    select(e, v)
    e.step_market(ETH)
    assert len(v.live(ETH)) == 2
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    assert e.crypto_block(ETH, e.b.m(ETH), time.time()) == "submission_lock"
    e.step_market(ETH)
    assert not v.live(ETH)


@pytest.fixture(autouse=True)
def _leave_no_registry_rows_behind():
    """The engine keeps recently closed orders in a module-level registry
    cache for the watchdog's grace period. Orders these tests cancel must
    not linger there into other test modules (a later sports test that
    counts registry rows under a patched clock would see them)."""
    yield
    M._REGISTRY_SEEN.clear()
