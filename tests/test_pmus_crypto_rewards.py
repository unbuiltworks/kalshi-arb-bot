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
    ({"assets": ["eth", "sol", "xrp", "btc"]}, "approval_mismatch:assets"),
    ({"combined_max_exposure_usd": "999"},
     "approval_mismatch:combined_max_exposure_usd"),
    ({"not_after": "soon"}, "approval_window_unreadable"),
    ({"not_after": iso(-3600)}, "approval_window_unreadable"),
    ({"not_after": iso(-1)}, "approval_expired"),
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


def meter(net="0", settled=1, status=None, unconfirmed=()):
    return {"settled_net_usd": net, "settled_markets": settled,
            "status": status or {"settled": settled},
            "unconfirmed": list(unconfirmed), "execution": {},
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
    assert CR.net_report(meter("3", status={"settled": 1, "open": 1}), rw,
                         0)["verdict"] == "INCOMPLETE"
    assert CR.net_report(meter("3", unconfirmed=[ETH]), rw,
                         0)["verdict"] == "INCOMPLETE"


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


def test_lane_caps_drop_openings_never_completions():
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


def test_unpaired_bound_counts_what_resting_bids_could_strand():
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

    def gget(self, path):
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

    def crypto_targets(self):
        return [{"slug": s, "end": self.end} for s in self.crypto
                if CR.tradable(s)] if CR.ON else []

    def cancel(self, oid):
        self.cancelled.append(oid)
        return super().cancel(oid)

    def live(self, slug=None):
        return {k: o for k, o in self.orders.items() if o["open"] and
                (slug is None or o["slug"] == slug)}

    def fill(self, oid, qty=None):
        o = self.orders[oid]
        q = o["qty"] if qty is None else D(str(qty))
        o.update(filled=q, open=q < o["qty"])


def fresh_meter(e):
    e._crypto_meter = {"settled_net_usd": "0", "unconfirmed": []}
    e._crypto_meter_at = time.monotonic()


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """Lane on, armed with a matching approval, shared gates readable."""
    arm_env(monkeypatch)
    monkeypatch.setattr(M, "ACCOUNT_RISK_ON", True)
    monkeypatch.setattr(RN1, "ON", True)       # production runs RN1 mode
    v = CV()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._rget = v.gget
    e.account_risk = lambda: {"ok": True, "lane_cap_usd": "1000"}
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
    (lambda e, mp: mp.setattr(M, "ACCOUNT_RISK_ON", False),
     "account_risk_not_enforced"),
    (lambda e, mp: setattr(e, "account_risk", lambda: {
        "ok": False, "reason": "ACCOUNT_DAILY_LOSS_HALT"}),
     "account_risk:ACCOUNT_DAILY_LOSS_HALT"),
    (lambda e, mp: setattr(e, "account_risk", lambda: {
        "ok": False, "reason": "ACCOUNT_RISK_STALE"}),
     "account_risk:ACCOUNT_RISK_STALE"),
    (lambda e, mp: setattr(e, "shared_halt", lambda: "HALT"), "shared_halt"),
    (lambda e, mp: setattr(e, "shared_halt", lambda: "UNAVAILABLE"),
     "shared_halt"),
    (lambda e, mp: mp.setattr(CR, "LOSS_STOP_USD", D("0")),
     "crypto_loss_stop_unset"),
    (lambda e, mp: mp.setenv(M.ENABLED_ENV, "NO"), "not_enabled"),
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
    e.account_risk = lambda: {"ok": True, "lane_cap_usd": "120"}
    sports_order(e, qty="80", px="0.50")                         # $40
    select(e, v)
    e.step_market(ETH)
    assert len(v.live(ETH)) == 1         # 40 + 47 fits 120; + 49 does not
    assert e.own_exposure_usd() == D("87")
    e.step_market(ETH)                   # one side alone is not kept
    e.step_market(ETH)
    assert e.own_exposure_usd() <= D("120")


def test_a_crypto_lone_leg_does_not_spend_the_sports_caps(lane, monkeypatch):
    """Sports TOTAL_CAP, slots and daily stop read sports only; the account
    exposure figure reads both."""
    e, v = lane
    monkeypatch.setattr(M, "TOTAL_CAP", D("4.50"))
    monkeypatch.setattr(M, "DAILY_LOSS_USD", D("3"))
    select(e, v)
    e.step_market(ETH)
    yes = next(k for k, o in v.live(ETH).items() if o["side"] == "YES")
    v.fill(yes)
    v.bid, v.ask = D("0.38"), D("0.40")                  # ETH marked -$9
    e.step_market(ETH)
    assert e.b.total_unmatched() == 0
    assert e.b.total_unmatched_all() == D("47")
    assert e.slots_used() == 0 and not e.full()
    assert e.day_change() == 0 and not e.daily_stopped()
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
    v.bid, v.ask = D("0.16"), D("0.18")    # mark 16 - 47 = -31
    e.step_market(ETH)
    assert not v.live()                    # SOL quotes and the completion
    stop = e.b.d["crypto"]["loss_stop"]
    assert D(stop["book_estimate_usd"]) == D("-31")
    assert e.crypto_block(SOL, e.b.m(SOL), time.time()) == "crypto_loss_stop"
    v.bid, v.ask = D("0.48"), D("0.50")    # recovery does not lift it
    n = len(v.orders)
    e2 = M.Engine(v, M.Book(tmp_path / "s.json"))        # restart
    e2._rget = v.gget
    e2.account_risk = lambda: {"ok": True, "lane_cap_usd": "1000"}
    fresh_meter(e2)
    for s in (ETH, SOL, XRP):
        e2.step_market(s)
    assert len(v.orders) == n and not v.live()
    assert e2.crypto_place_block(ETH, "NO", D("0.49"), D("100"),
                                 "crypto_complete", True) == \
        "crypto_loss_stop"


def test_venue_settled_loss_trips_the_same_stop(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    e._crypto_meter["settled_net_usd"] = "-30"
    assert e.crypto_loss_stopped()
    assert not v.live()
    assert e.b.d["crypto"]["loss_stop"]["venue_settled_net_usd"] == "-30"


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
    e2 = M.Engine(v, M.Book(tmp_path / "s.json"))
    e2._rget = v.gget
    e2.account_risk = lambda: {"ok": True, "lane_cap_usd": "1000"}
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
    e2._rget = v.gget
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


def test_venue_reconciled_result_fees_rewards_and_modelled_stay_apart(
        lane, capsys):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    for k in list(v.live(ETH)):
        v.fill(k)
    e.step_market(ETH)                                # pair locked at 0.96
    mk = e.b.m(ETH)
    mk.update(done=True, settle_px="1")
    mk["crypto_model"] = {"usd": "4.25", "rate_usd_s": 0, "t": 0}
    rows = [trade(ETH, 100, "0.10"), trade(ETH, 100),
            {"positionResolution": {"marketSlug": ETH}}]
    e.crypto_reward_credits({"ok": True, "rows": 2, "rows_all": [
        {"marketSlug": ETH, "status": "PENDING", "reward": "1.79"},
        {"marketSlug": "aec-x", "status": "PAID", "reward": "5"}]})
    capsys.readouterr()
    e.crypto_meter(rows, e.b.d["markets"])
    d = json.loads(next(ln for ln in capsys.readouterr().out.splitlines()
                        if '"CRYPTO_PNL"' in ln).split(" ", 1)[1])
    assert D(d["settled_trading_net_usd"]) == D("3.90")    # 4.00 - 0.10 fee
    assert D(d["maker_fees_charged_usd"]) == D("0.10")
    assert d["modelled_reward_usd"] == "4.25"
    assert d["venue_pending_reward_usd"] == "1.79"
    assert d["venue_credited_reward_usd"] == "0"
    assert D(d["realized_net_usd"]) == D("3.90")           # no modelled, no pending
    assert d["verdict"] == "FAIL_OR_AWAITING_CREDIT"       # no credit row yet
    assert d["completed_pairs"] == "100"


def test_unconfirmed_venue_fills_block_new_quotes(lane):
    e, v = lane
    select(e, v)
    e.step_market(ETH)
    for k in list(v.live(ETH)):
        v.fill(k)
    e.step_market(ETH)
    e.crypto_meter([trade(ETH, 100)], e.b.d["markets"])     # venue saw 100/200
    assert e.crypto_meter_block() == "crypto_fills_unconfirmed"
    assert e.crypto_block(SOL, e.b.m(SOL), time.time()) == \
        "crypto_fills_unconfirmed"


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
    assert "COPY src/pmus_crypto_rewards.py src/pmus_crypto_rewards.py" in \
        (root / "deploy/pmus_spring_l5/Dockerfile").read_text()
    assert '"src/pmus_crypto_rewards.py"' in \
        (root / "deploy/pmus_spring_l5/railway.json").read_text()
