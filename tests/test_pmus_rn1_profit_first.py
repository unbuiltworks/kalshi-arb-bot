"""Owner 2026-11-22, issue #251 -- profit first.

"Trading P&L before incentives must be the primary economic objective."
"Opposite-side orders must independently pass economic and risk checks."
"Where a reliable fair value cannot be established, fail closed on
additional risk." "Reward assumptions must not turn demonstrably negative
trading economics into a falsely attractive opportunity." "Do not force
pair completion."

Every RN1 bid -- opening, same-side add, opposite-side bid -- clears one
marginal test against the international fair value, or is not quoted.
"""
import json
import subprocess
import sys
import time
from decimal import Decimal as D

import pytest

from src import pmus_fill_markout as MK
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_rn1_reward_aware import Guard
from tests.test_pmus_sports_two_sided import FakeVenue

T = D("0.01")
GATE = {"edge_gate": True}


def q(pos, cost, bid, ask, **c):
    return R.quotes(yes_bid=bid, yes_ask=ask, tick=T, pos=pos, cost=cost,
                    c=dict(GATE, **c))


FLAT = ({"YES": 0, "NO": 0}, {"YES": 0, "NO": 0})


# ------------------------------------------------- the production default
def test_production_default_is_on_and_fails_closed():
    """From a clean interpreter: the gate is on unless explicitly "0"."""
    code = ("import os;[os.environ.pop(k,None) for k in list(os.environ) "
            "if k.startswith('PMUS_RN1')];"
            "from src import pmus_rn1_replica as R;"
            "print(R.EDGE_GATE, R.ADVERSE_TICKS, R.RISK_STEP_TICKS)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ["True", "1", "1"]
    off = subprocess.run(
        [sys.executable, "-c", "from src import pmus_rn1_replica as R;"
         "print(R.EDGE_GATE)"], capture_output=True, text=True, check=True,
        env={"PMUS_RN1_EDGE_GATE": "0", "PATH": "/usr/bin:/bin"}).stdout
    assert off.strip() == "False"


# ------------------------------------------------------ the pure test
def test_the_arithmetic_of_one_bid():
    e = R.edge(side="YES", px="0.47", tick=T, fair_yes="0.50",
               adds_exposure=True, unpaired_clips=1, fee_rate="0",
               adverse_ticks=1, risk_step_ticks=1, incentive_ticks=0)
    assert e["ok"] is True
    assert (D(e["fair"]), D(e["trade_edge"]), D(e["risk"]),
            D(e["net_edge"])) == (D("0.50"), D("0.02"), D("0.01"), D("0.01"))
    no = R.edge(side="NO", px="0.47", tick=T, fair_yes="0.50",
                adds_exposure=False, adverse_ticks=1)
    assert D(no["fair"]) == D("0.50") and D(no["risk"]) == 0


@pytest.mark.parametrize("fair", [None, "", "0", "1", "1.2", "-0.1"])
def test_no_usable_fair_value_is_no_bid(fair):
    e = R.edge(side="YES", px="0.40", tick=T, fair_yes=fair or None,
               adds_exposure=True)
    assert e["ok"] is False and e["why"] == "no_fair"


def test_a_fee_is_a_cost():
    free = R.edge(side="YES", px="0.48", tick=T, fair_yes="0.50",
                  adds_exposure=False, adverse_ticks=1, fee_rate="0")
    paid = R.edge(side="YES", px="0.48", tick=T, fair_yes="0.50",
                  adds_exposure=False, adverse_ticks=1, fee_rate="0.07")
    assert free["ok"] is True and D(free["trade_edge"]) == D("0.01")
    assert paid["ok"] is False and paid["why"] == "edge_negative"
    assert D(paid["fee"]) == D("0.07") * D("0.48") * D("0.52")


def test_an_incentive_never_rescues_a_negative_trade_edge():
    """However large the credit: the trade itself has to be worth making."""
    e = R.edge(side="YES", px="0.50", tick=T, fair_yes="0.50",
               adds_exposure=True, unpaired_clips=3, adverse_ticks=1,
               incentive_ticks=50)
    assert e["ok"] is False and e["why"] == "edge_negative"


def test_an_incentive_pays_for_risk_and_is_capped_at_it():
    base = dict(side="YES", px="0.47", tick=T, fair_yes="0.50",
                adds_exposure=True, unpaired_clips=3, adverse_ticks=1,
                risk_step_ticks=1)
    without = R.edge(**base, incentive_ticks=0)       # edge 2c, risk 3c
    assert without["ok"] is False and without["why"] == "edge_below_risk"
    with_ = R.edge(**base, incentive_ticks=1)         # risk net 2c
    assert with_["ok"] is True and D(with_["incentive"]) == D("0.01")
    huge = R.edge(**base, incentive_ticks=50)
    assert D(huge["incentive"]) == D(huge["risk"]) == D("0.03")
    assert D(huge["net_edge"]) == D(huge["trade_edge"])   # never above it


# ------------------------------------------------------------- openings
def test_both_sides_open_only_where_each_is_worth_buying():
    a = q(*FLAT, "0.48", "0.50", fair_yes="0.49")
    assert a["YES"]["px"] == D("0.47") and a["NO"]["px"] == D("0.49")
    assert a["edge"]["YES"]["role"] == "open"
    low = q(*FLAT, "0.48", "0.50", fair_yes="0.47")   # YES is not cheap
    assert low["YES"] is None and low["why_YES"] == "edge_negative"
    assert low["NO"] is not None
    high = q(*FLAT, "0.48", "0.50", fair_yes="0.52")
    assert high["NO"] is None and high["YES"] is not None


def test_no_fair_value_no_opening_at_all():
    a = q(*FLAT, "0.48", "0.50", fair_yes=None)
    assert a["YES"] is None and a["NO"] is None
    assert a["why_YES"] == a["why_NO"] == "no_fair"


# ---------------------------------------------------- repeated additions
def test_repeated_additions_while_the_marginal_edge_holds():
    """Accumulation is allowed, not capped at one clip: each add needs one
    more tick of edge than the last, and gets it while fair value supports
    it. No lower-price rule, no balance requirement."""
    pos, cost = {"YES": 0, "NO": 0}, {"YES": D(0), "NO": D(0)}
    fair = "0.56"                      # YES worth 56c, offered at 47c
    for clip in range(1, 4):
        a = q(pos, cost, "0.48", "0.50", fair_yes=fair,
              residual_cap_usd="9", per_market_usd="20")
        assert a["YES"] is not None, clip
        assert a["edge"]["YES"]["unpaired_clips"] == clip - 1
        pos = {"YES": pos["YES"] + 2, "NO": 0}
        cost = {"YES": cost["YES"] + 2 * a["YES"]["px"], "NO": D(0)}
    assert pos["YES"] == 6


def test_additions_stop_when_the_marginal_edge_runs_out():
    """Same book, fair value only 3c above the bid: the first add passes
    (needs 1c adverse + 1 clip), the third does not."""
    held2 = ({"YES": 2, "NO": 0}, {"YES": "0.94", "NO": 0})
    ok = q(*held2, "0.48", "0.50", fair_yes="0.50", residual_cap_usd="9")
    assert ok["YES"]["role"] == "extend"
    held6 = ({"YES": 6, "NO": 0}, {"YES": "2.82", "NO": 0})
    stop = q(*held6, "0.48", "0.50", fair_yes="0.50", residual_cap_usd="9",
             per_market_usd="20")
    assert stop["YES"] is None and stop["why_YES"] == "edge_below_risk"
    assert stop["edge"]["YES"]["unpaired_clips"] == 3


def test_a_negative_edge_addition_is_refused():
    held = ({"YES": 2, "NO": 0}, {"YES": "0.94", "NO": 0})
    a = q(*held, "0.48", "0.50", fair_yes="0.46")
    assert a["YES"] is None and a["why_YES"] == "edge_negative"
    assert D(a["edge"]["YES"]["trade_edge"]) < 0


def test_the_jump_allowance_applies_to_adds_by_role():
    held = ({"YES": 2, "NO": 0}, {"YES": "0.94", "NO": 0})
    adv = {"open": 1, "complete": 1, "extend": 4, "default": 1}
    a = q(*held, "0.48", "0.50", fair_yes="0.50", adverse_ticks=adv)
    assert a["YES"] is None and a["why_YES"] == "edge_negative"   # 3c < 4c
    same_as_open = q(*held, "0.48", "0.50", fair_yes="0.50")      # 1 tick
    assert same_as_open["YES"] is not None
    b = q(*held, "0.48", "0.50", fair_yes="0.53", adverse_ticks=adv)
    assert b["YES"] is not None


# ------------------------------------------------- no forced completion
def test_the_other_side_is_not_bought_just_because_the_pair_would_pay():
    """Holding YES 2 @0.40. The book has moved our way: YES is worth 0.70.
    A NO bid at 0.31 would lock a pair at 0.71 -- 'profitable' -- but NO is
    worth 0.30. Buying it is paying 1c to give up a winning position."""
    held = ({"YES": 2, "NO": 0}, {"YES": "0.80", "NO": 0})
    a = q(*held, "0.67", "0.68", fair_yes="0.70")
    assert a["NO"] is None and a["why_NO"] == "edge_negative"
    assert a["edge"]["NO"]["role"] == "complete"
    #: ...and without the gate the same state quotes the completion
    legacy = R.quotes(yes_bid="0.67", yes_ask="0.68", tick=T, pos=held[0],
                      cost=held[1])
    assert legacy["NO"]["role"] == "complete"


def test_the_other_side_is_bought_when_it_is_worth_it_on_its_own():
    held = ({"YES": 2, "NO": 0}, {"YES": "0.80", "NO": 0})
    a = q(*held, "0.67", "0.68", fair_yes="0.66")     # NO worth 0.34
    assert a["NO"]["role"] == "complete" and a["NO"]["px"] == D("0.31")
    assert D(a["edge"]["NO"]["risk"]) == 0            # it reduces exposure


def test_the_pair_cap_still_limits_the_other_side():
    """A limit stays a limit: NO is worth 0.60 and bid at 0.59, but the
    pair cap holds it to 0.97 - 0.50."""
    held = ({"YES": 2, "NO": 0}, {"YES": "1.00", "NO": 0})
    a = q(*held, "0.39", "0.40", fair_yes="0.40")
    assert a["NO"]["px"] == D("0.47") and a["NO"]["role"] == "complete"


def test_no_fair_value_no_bid_on_the_other_side_either():
    held = ({"YES": 2, "NO": 0}, {"YES": "0.80", "NO": 0})
    a = q(*held, "0.67", "0.68", fair_yes=None)
    assert a["NO"] is None and a["why_NO"] == "no_fair"
    assert a["YES"] is None and a["why_YES"] == "no_fair"


# --------------------------------------------------- limits are intact
def test_inventory_and_market_caps_bind_before_the_gate():
    rich = "0.90"                                     # any edge you like
    at_cap = ({"YES": 6, "NO": 0}, {"YES": "2.82", "NO": 0})
    a = q(*at_cap, "0.48", "0.50", fair_yes=rich)     # residual cap $3
    assert a["YES"] is None and a["why_YES"] == "residual_cap"
    full = ({"YES": 8, "NO": 8}, {"YES": "3.8", "NO": "3.9"})
    b = q(*full, "0.48", "0.50", fair_yes="0.49")     # per-market cap $8
    assert b["YES"] is None and b["NO"] is None
    band = q(*FLAT, "0.10", "0.12", fair_yes="0.30")  # price band
    assert band["YES"] is None and band["why_YES"] == "price_band"


def test_both_opening_bids_together_still_fit_the_pair_cap():
    a = q(*FLAT, "0.49", "0.50", fair_yes="0.495", depth_ticks=0,
          adverse_ticks=0)
    assert a["YES"] and a["NO"]
    assert a["YES"]["px"] + a["NO"]["px"] <= R.PAIR_CAP


# ------------------------------------------------------------- engine
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    monkeypatch.delenv(M.STALL_DRILL_ENV, raising=False)
    monkeypatch.setitem(M._LOOP, "beat", None)
    monkeypatch.setitem(M._LOOP, "thread", None)
    monkeypatch.setitem(M._LOOP, "max_gap", 0.0)
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e, v


def resting(v):
    return sorted((o["side"], str(o["px"])) for o in v.orders.values()
                  if o["open"])


@pytest.mark.rn1_edge_gate
def test_engine_without_a_fair_value_source_places_nothing(eng, capsys):
    e, v = eng
    assert e.guard is None
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert v.orders == {}
    assert '"no_fair"' in capsys.readouterr().out


@pytest.mark.rn1_edge_gate
def test_engine_quotes_both_sides_with_a_fair_value(eng):
    e, v = eng
    e.guard = Guard(0.49)
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert resting(v) == [("NO", "0.49"), ("YES", "0.47")]
    cfg = e.rn1_quote_cfg("aec-x")
    assert cfg["edge_gate"] is True and cfg["fair_yes"] == D("0.49")
    assert cfg["adverse_ticks"]["extend"] == cfg["adverse_ticks"]["open"]


@pytest.mark.rn1_edge_gate
def test_engine_pulls_its_bids_when_the_fair_value_goes_stale(eng):
    """Stale-fair-value protection: the feed stops, the bids come off."""
    e, v = eng
    e.guard = Guard(0.49)
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert len(resting(v)) == 2
    e.guard.mid = None                    # no fresh international quote
    e.step_market("aec-x")
    assert resting(v) == []


@pytest.mark.rn1_edge_gate
def test_engine_pulls_a_bid_after_a_sudden_adverse_move(eng):
    """The international price drops 6c while ours has not moved: the YES
    bid is no longer worth resting and is cancelled; NO stays."""
    e, v = eng
    e.guard = Guard(0.49)
    e.b.m("aec-x")
    e.step_market("aec-x")
    e.guard.mid = 0.43
    e.step_market("aec-x")
    assert resting(v) == [("NO", "0.49")]


@pytest.mark.rn1_edge_gate
def test_engine_does_not_complete_a_winning_position_at_a_loss(eng):
    e, v = eng
    v.bid, v.ask = D("0.67"), D("0.68")
    e.guard = Guard(0.70)
    mk = e.b.m("aec-x")
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.80")
    e.step_market("aec-x")
    assert [s for s, _p in resting(v)] == ["YES"]     # an add; no NO bid
    assert D(mk["pos"]["YES"]) == 2                   # and it is still held


@pytest.mark.rn1_edge_gate
def test_engine_jump_allowance_and_the_legacy_path(eng, monkeypatch):
    e, v = eng
    e.guard = Guard(0.5)
    for typ, extra in (("soccer", 3), ("hockey", 2), ("football", 1),
                       ("basketball", 0), ("", 0)):
        v.states = {"aec-x": {"type": typ}}
        adv = e.rn1_quote_cfg("aec-x")["adverse_ticks"]
        assert adv["extend"] - adv["open"] == extra and adv["complete"] == 1
    monkeypatch.setattr(R, "EDGE_GATE", False)
    assert "edge_gate" not in e.rn1_quote_cfg("aec-x")
    assert e.rn1_quote_cfg("aec-x") == e.rn1_extend_cfg("aec-x")


# ------------------------------------------------- reward qualification
PERIOD = {"programId": "P", "pool": 30.0, "period": "live", "df": 0.5,
          "targetSize": 50.0, "maxSpread": None, "period_s": 10800.0,
          "pool_rate_usd_s": 30.0 / 10800.0}


def with_estimate(e, slug, usd_per_hour, age_s=0.0):
    e.b.m(slug)["rn1_reward_est"] = {"t": time.time() - age_s, "legs": {},
                                     "est_usd_per_hour": usd_per_hour}


def test_a_programme_alone_earns_no_credit(eng, monkeypatch):
    e, v = eng
    e.b.m("aec-x")
    assert e.rn1_incentive("aec-x") == {"ticks": 0,
                                        "why": "no_paying_programme"}
    monkeypatch.setattr(e, "rn1_reward", lambda s: dict(PERIOD))
    got = e.rn1_incentive("aec-x")                 # nothing resting yet
    assert got["ticks"] == 0 and got["why"] == "below_minimum_payout"
    assert got["projected_usd"] == 0 and got["min_payout_usd"] == 1.0


def test_credit_only_when_the_projected_day_clears_the_minimum_payout(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(e, "rn1_reward", lambda s: dict(PERIOD))
    with_estimate(e, "aec-x", 0.20)                # $0.60 over 3 h
    assert e.rn1_incentive("aec-x")["why"] == "below_minimum_payout"
    with_estimate(e, "aec-y", 0.20)                # two markets: $1.20
    got = e.rn1_incentive("aec-x")
    assert got["ticks"] == M.RN1_REWARD_CREDIT_TICKS
    assert got["why"] == "qualifies" and got["projected_usd"] == 1.2
    assert "THEORETICAL" in got["label"]


def test_an_old_estimate_or_a_failed_read_is_no_credit(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(e, "rn1_reward", lambda s: dict(PERIOD))
    with_estimate(e, "aec-x", 5.0, age_s=10 * M.REWARD_WALK_CHECK_S)
    assert e.rn1_incentive("aec-x")["ticks"] == 0

    def boom(*a, **k):
        raise OSError("gateway down")
    monkeypatch.setattr(e, "_rn1_reward_estimate", boom)
    got = e.rn1_incentive("aec-x", e.b.m("aec-x"), T)
    assert got["ticks"] == 0 and "reward_estimate_error" in got["why"]


@pytest.mark.rn1_edge_gate
def test_reward_eligibility_disappearing_withdraws_what_it_paid_for(
        eng, monkeypatch):
    """YES 4 held (two clips). An add has 3c of edge after adverse
    selection... risk allowance 2c: passes. With three clips it needs 3c
    of risk allowance against 2c of edge: only the reward credit carries
    it, and when the programme stops paying the bid comes off."""
    e, v = eng
    e.guard = Guard(0.50)
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("9"))
    monkeypatch.setattr(R, "PER_MARKET_USD", D("20"))
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("20"))
    monkeypatch.setattr(R, "TOTAL_SPEND_USD", D("40"))
    mk = e.b.m("aec-x")
    mk["pos"]["YES"], mk["cost"]["YES"] = D("6"), D("2.82")
    paying = {"on": True}
    monkeypatch.setattr(e, "rn1_incentive", lambda s, m=None, t=None: (
        {"ticks": 1, "why": "qualifies"} if paying["on"]
        else {"ticks": 0, "why": "no_paying_programme"}))
    e.step_market("aec-x")
    assert ("YES", "0.47") in resting(v)
    paying["on"] = False
    e.step_market("aec-x")
    assert all(s != "YES" for s, _p in resting(v))


# -------------------------------------------------------------- markout
def test_markout_is_signed_by_side_and_never_interpolated():
    m = MK.Mids()
    for t, b, a in ((100, .48, .50), (106, .46, .48), (131, .44, .46),
                    (221, .50, .52)):
        assert m.add("s", t, b, a)
    yes = MK.new_record(slug="s", side="YES", px="0.47", qty="2",
                        order_id="o1", t_fill=100.5, t_seen=101,
                        t_source="seen_by_bot", mids=m)
    assert yes["book_before"]["entry_vs_mid"] == pytest.approx(0.02)
    assert sorted(MK.resolve(yes, m, now=221)) == [5, 30, 120]
    assert yes["h"]["5"]["markout"] == pytest.approx(0.47 - 0.47)
    assert yes["h"]["5"]["late_s"] == pytest.approx(0.5)
    assert yes["h"]["30"]["markout"] == pytest.approx(0.45 - 0.47)
    assert yes["h"]["120"]["markout"] == pytest.approx(0.51 - 0.47)
    assert "300" not in yes["h"]                      # not due yet
    no = MK.new_record(slug="s", side="NO", px="0.49", qty="2",
                       order_id="o2", t_fill=100.5, t_seen=101,
                       t_source="seen_by_bot", mids=m)
    MK.resolve(no, m, now=221)
    assert no["h"]["30"]["markout"] == pytest.approx((1 - 0.45) - 0.49)
    # the 300 s horizon: no sample arrives -> missed, never the last price
    assert MK.resolve(yes, m, now=100.5 + 300 + 301) == [300]
    assert yes["h"]["300"] == {
        "missed": True,
        "why": "no_book_sample_within_300s_of_the_horizon"}
    assert MK.complete(yes)


def test_a_sample_too_long_after_the_horizon_is_not_used():
    m = MK.Mids()
    m.add("s", 100, .48, .50)
    m.add("s", 100 + 5 + 31, .30, .32)        # 31 s late for the 5 s mark
    r = MK.new_record(slug="s", side="YES", px="0.47", qty="2",
                      order_id="o", t_fill=100, t_seen=100,
                      t_source="seen_by_bot", mids=m)
    MK.resolve(r, m, now=200)
    assert r["h"]["5"].get("missed") is True
    assert r["h"]["30"]["markout"] == pytest.approx(0.31 - 0.47)


def test_a_bad_book_is_not_a_sample():
    m = MK.Mids()
    for b, a in ((0.5, 0.5), (0.6, 0.5), (None, 0.5), (0, 0.5), (0.5, 1)):
        assert m.add("s", 1, b, a) is False
    assert m.at_or_after("s", 0) is None


def test_markout_summary_counts_what_it_could_not_measure():
    rows = [{"qty": "2", "h": {"5": {"markout": -0.01}, "30": {"missed": True}}},
            {"qty": "4", "h": {"5": {"markout": 0.02}}}]
    s = MK.summary(rows, horizons=(5, 30))
    assert s["fills"] == 2 and "NOT realized" in s["label"]
    assert s["h"]["5"] == {"n": 2, "missed": 0, "pending": 0,
                           "mean_markout": pytest.approx(0.01)}
    assert s["h"]["30"] == {"n": 0, "missed": 1, "pending": 1,
                            "mean_markout": None}


def fill(v, e, slug, side, qty):
    oid = next(i for i, o in e.b.d["markets"][slug]["orders"].items()
               if o["side"] == side and o.get("open"))
    v.orders[oid]["filled"] = D(str(qty))
    return oid


def test_engine_records_a_markout_for_every_fill(eng, monkeypatch, capsys):
    e, v = eng
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(M.time, "time", lambda: clock["t"])
    e.b.m("aec-x")
    e.step_market("aec-x")                         # places YES and NO
    oid = fill(v, e, "aec-x", "YES", 1)            # a PARTIAL fill
    clock["t"] += 3
    e.step_market("aec-x")
    rec, = e.b.d["markets"]["aec-x"]["markouts"]
    assert rec["order_id"] == oid and rec["qty"] == "1"
    assert rec["side"] == "YES" and rec["t_source"] == "seen_by_bot"
    assert rec["unpaired_before"] == "0" and rec["role"] == "open"
    assert rec["book_before"]["entry_vs_mid"] == pytest.approx(
        0.49 - float(rec["px"]))
    v.bid, v.ask = D("0.44"), D("0.46")            # the book falls away
    for dt in (6, 30, 100, 200):
        clock["t"] += dt
        e.step_market("aec-x")
    rec = e.b.d["markets"]["aec-x"]["markouts"][0]
    assert MK.complete(rec) and rec["logged"] is True
    assert rec["h"]["5"]["markout"] == pytest.approx(0.45 - float(rec["px"]))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if '"FILL_MARKOUT"' in l)
    doc = json.loads(line[line.index("{"):])
    assert doc["order_id"] == oid and "NOT realized" in doc["label"]
    assert e.markout_summary()["h"]["5"]["n"] == 1


def test_the_venues_own_fill_time_is_used_when_the_row_has_one():
    assert M.order_time({"updateTime": "2026-11-22T14:28:43.123456789Z"}) \
        == pytest.approx(1795357723.123456, abs=1e-3)
    assert M.order_time({"updateTime": ""}) is None
    assert M.order_time({"updateTime": "soon"}) is None
    assert M.order_time(None) is None


def test_a_fill_seen_late_is_marked_from_when_it_happened(eng, monkeypatch):
    """22 Nov: the bot learned of two fills 30 s after the venue. The book
    it read in between is in the history, so the 5 s mark is still real."""
    e, v = eng
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(M.time, "time", lambda: clock["t"])
    mk = e.b.m("aec-x")
    for dt, bid, ask in ((0, "0.48", "0.50"), (6, "0.45", "0.47"),
                         (20, "0.44", "0.46")):
        clock["t"] = 2_000_000.0 + dt
        e._markout_step("aec-x", mk, {"bid": D(bid), "ask": D(ask)})
    clock["t"] = 2_000_030.0
    mk["orders"]["o9"] = {"side": "YES", "px": "0.47", "qty": "2",
                          "filled": "0", "open": True, "stage": "rn1_open"}
    e._record_fill("aec-x", "YES", "o9", {"qty": D("2"), "px": D("0.47"),
                                           "open": False, "state": "FILLED",
                                           "t": 2_000_000.5})
    rec, = mk["markouts"]
    assert rec["t_source"] == "venue_order_row" and rec["t"] == 2_000_000.5
    e._markout_step("aec-x", mk, {"bid": D("0.44"), "ask": D("0.46")})
    assert rec["h"]["5"]["markout"] == pytest.approx(0.46 - 0.47)
    assert rec["h"]["5"]["late_s"] == pytest.approx(0.5)


def test_markouts_survive_a_restart_and_are_not_guessed_after_one(
        eng, tmp_path, monkeypatch):
    e, v = eng
    clock = {"t": 3_000_000.0}
    monkeypatch.setattr(M.time, "time", lambda: clock["t"])
    e.b.m("aec-x")
    e.step_market("aec-x")
    fill(v, e, "aec-x", "NO", 2)
    clock["t"] += 2
    e.step_market("aec-x")
    e.b.save()
    again = M.Engine(v, M.Book(tmp_path / "s.json"))   # history is gone
    rec, = again.b.d["markets"]["aec-x"]["markouts"]
    assert rec["side"] == "NO" and rec["h"] == {}
    clock["t"] += 700
    again._markout_step("aec-x", again.b.d["markets"]["aec-x"],
                        {"bid": D("0.10"), "ask": D("0.12")})
    assert all(c.get("missed") for c in rec["h"].values())
    assert MK.complete(rec)


def test_markout_recording_cannot_break_trading(eng, monkeypatch):
    e, v = eng

    def boom(*a, **k):
        raise RuntimeError("markout bug")
    monkeypatch.setattr(MK, "new_record", boom)
    monkeypatch.setattr(MK, "resolve", boom)
    e.b.m("aec-x")
    e.step_market("aec-x")
    fill(v, e, "aec-x", "YES", 2)
    e.step_market("aec-x")
    assert D(e.b.d["markets"]["aec-x"]["pos"]["YES"]) == 2


# ------------------------------------------------ loop gap, stall drill
def test_the_heartbeat_reports_the_longest_gap_between_beats(eng,
                                                             monkeypatch):
    clock = {"t": 5_000.0}
    monkeypatch.setattr(M.time, "time", lambda: clock["t"])
    M.loop_beat()
    for gap in (2, 47, 5):
        clock["t"] += gap
        M.loop_beat()
    clock["t"] += 3
    rep = M.loop_report()
    assert rep == {"idle_s": 3.0, "max_gap_s": 47.0,
                   "stall_s": M.LOOP_STALL_S, "stalled": False}
    assert M.loop_report()["max_gap_s"] == 3.0        # reset each report


@pytest.mark.parametrize("raw", [
    "", "1", "window:", "window:2026-11-23T01:00:00Z",
    "window:2026-11-23T01:00:00Z/2026-11-23T01:20:00Z",      # 20 min: too long
    "window:2026-11-23T01:10:00Z/2026-11-23T01:00:00Z",      # backwards
    "window:2026-11-23T01:00:00/2026-11-23T01:05:00",        # no zone
    "window:soon/later", "2026-11-23T01:00:00Z/2026-11-23T01:05:00Z"])
def test_a_malformed_or_overlong_drill_window_is_no_drill(monkeypatch, raw):
    monkeypatch.setenv(M.STALL_DRILL_ENV, raw)
    assert M.stall_drill() is None


def test_a_drill_holds_the_loop_still_and_the_watchdog_sees_it(
        eng, monkeypatch):
    """Inside the window the loop neither acts nor reports progress, the
    registry says so, and it still claims the resting orders -- which is
    what lets the watchdog cancel them."""
    e, v = eng
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert len(resting(v)) == 2
    clock = {"t": 1795395600.0}                       # 2026-11-23T01:00:00Z
    monkeypatch.setattr(M.time, "time", lambda: clock["t"])
    monkeypatch.setenv(M.STALL_DRILL_ENV,
                       "window:2026-11-23T01:00:10Z/2026-11-23T01:05:10Z")
    M.loop_beat()
    assert M.stall_drill()["active"] is False
    e._stall_drill_hold()                             # not yet: returns
    seen = []

    def sleep(_s):
        clock["t"] += 30
        doc = M.registry_doc(e.b, clock=lambda: clock["t"])
        seen.append((doc["manages_own_resting"], doc["loop_stalled"],
                     len(doc["orders"])))
    monkeypatch.setattr(M.time, "sleep", sleep)
    clock["t"] += 15                                  # inside the window
    calls = {"place": len(v.orders)}
    e._stall_drill_hold()
    assert clock["t"] >= 1795395600.0 + 310           # held to the end
    assert seen[0] == (True, False, 2)                # 30 s: still managing
    assert (False, True, 2) in seen                   # past 120 s: stalled
    assert len(v.orders) == calls["place"]            # it did nothing
    assert resting(v) and M.stall_drill()["active"] is False


def test_sigterm_ends_a_drill(eng, monkeypatch):
    e, v = eng
    now = time.time()
    fmt = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    monkeypatch.setenv(M.STALL_DRILL_ENV, "window:%s/%s" % (
        fmt(now - 5), fmt(now + 300)))
    e.stop = True
    t0 = time.time()
    e._stall_drill_hold()
    assert time.time() - t0 < 2


def test_the_boot_log_states_the_gate_and_the_drill():
    cfg = M.safeguards_cfg()
    assert cfg["rn1_edge_gate"]["no_fair_means"] == "no_bid"
    assert cfg["rn1_edge_gate"]["reward_min_day_usd"] == 1.0
    assert cfg["markout_horizons_s"] == [5, 30, 120, 300]
    assert cfg["stall_drill"] is None
