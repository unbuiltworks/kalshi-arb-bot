"""Reward-aware mode of the crypto lane (PMUS_CRYPTO_RA, default off):
quoting inside the scoring walk, controlled asymmetric inventory, economical
completion, the evidence-labelled net estimate, fixing-minute and jump
pulls, identity checks, attribution. Same controller, same choke point, all
global protections in force. No network, no orders."""
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import pmus_crypto_feed as CF
from src import pmus_crypto_rewards as CR
from src import pmus_rn1_replica as RN1
from src import pmus_sports_two_sided as M
from tests.test_pmus_crypto_rewards import (CV, DEEP, PROG, SHA, SOL_LIVE,
                                            approval, around, book,
                                            fresh_meter, select, wire)

T = D("0.01")
ROOT = Path(__file__).resolve().parents[1]


def utc(seconds_from_now):
    return (datetime.now(timezone.utc).replace(microsecond=0) +
            timedelta(seconds=seconds_from_now))


def day_slug(asset, start):
    p = {"eth": "cpceth-eth", "sol": "cpcsol-sol", "xrp": "cpcxrp-xrp",
         "btc": "cpc-btc"}[asset]
    return "%s-updown-1d-%s-2200z" % (p, start.strftime("%Y-%m-%d"))


# real identifiers (28 Nov): the open daily and the open hourly
D_START = datetime(2026, 11, 27, 22, tzinfo=timezone.utc)
ETH = "cpceth-eth-updown-1d-2026-11-27-2200z"
SOL = "cpcsol-sol-updown-1d-2026-11-27-2200z"
XRP = "cpcxrp-xrp-updown-1d-2026-11-27-2200z"
BTC = "cpc-btc-updown-1d-2026-11-27-2200z"
ETH_H = "cpceth-eth-updown-1h-2026-11-28-2000z"
H_START = datetime(2026, 11, 28, 20, tzinfo=timezone.utc)
PROG_H = {"programId": "crypto_alt_updown_1h_20261110", "pool": 8.0,
          "period": "daily_event", "df": 0.3, "targetSize": 1000.0,
          "maxSpread": 0.05, "period_s": 86400.0, "M_program": 3}
INDEX = {"eth": "ETHUSD_RTI", "sol": "SOLUSD_RTI", "xrp": "XRPUSD_RTI",
         "btc": "BRTI"}


@pytest.fixture(autouse=True)
def _leave_no_registry_rows_behind():
    yield
    M._REGISTRY_SEEN.clear()


def ra_on(monkeypatch, **over):
    """Reward-aware mode on with the measured cost bounds set and no
    allocation named (the fail-closed default)."""
    monkeypatch.setattr(CR, "ON", True)
    monkeypatch.setattr(CR, "RA_ON", True)
    monkeypatch.setattr(CR, "QTY", D("2"))
    monkeypatch.setattr(CR, "MIN_QTY", D("1"))
    monkeypatch.setattr(CR, "RA_FILL_COST_USD", "0.03")
    monkeypatch.setattr(CR, "RA_FILLS_PER_HOUR", "2")
    for k, v in over.items():
        monkeypatch.setattr(CR, k, v)


def RQ(bk=DEEP, pos=None, cost=None, **kw):
    kw.setdefault("slug", ETH)
    kw.setdefault("seconds_left", 20 * 3600)
    kw.setdefault("paid_fraction", "0.858")
    kw.setdefault("c", {"clip": "2", "pair_cap": "0.97",
                        "complete_max_pair": "0.97"})
    return CR.ra_quotes(book_md=bk, program=kw.pop("program", PROG), tick=T,
                        pos=pos or {"YES": "0", "NO": "0"},
                        cost=cost or {"YES": "0", "NO": "0"}, **kw)


# ------------------------------------------------------------ default off

def test_reward_aware_mode_is_off_by_default():
    assert CR.RA_ON is False and CR.ra_cfg() is None
    assert CR.cfg()["reward_aware"] is None
    assert CR.RA_ALLOCATION == "" and CR.RA_PROBE is False
    assert CR.RA_SERIES == ("1d",)


def test_hourly_markets_are_not_tradable_unless_the_mode_names_them(
        monkeypatch):
    assert CR.tradable(ETH) and not CR.tradable(ETH_H)
    ra_on(monkeypatch)                           # series still "1d"
    assert not CR.tradable(ETH_H) and CR.series_of(ETH) == "1d"
    monkeypatch.setattr(CR, "RA_SERIES", ("1d", "1h"))
    assert CR.tradable(ETH_H) and CR.series_of(ETH_H) == "1h"
    assert CR.asset_of(ETH_H) == "eth"
    for btc in (BTC, "cpc-btc-updown-1h-2026-11-28-2000z"):
        assert not CR.tradable(btc) and CR.series_of(btc) is None
    for short in ("cpceth-eth-updown-15m-2026-11-28-2030z",
                  "cpceth-eth-updown-5m-2026-11-28-2035z"):
        assert not CR.tradable(short)


def test_mode_needs_the_lane_itself_on():
    """RA_ON is `ON and PMUS_CRYPTO_RA=1`: the mode cannot be on alone."""
    src = (ROOT / "src" / "pmus_crypto_rewards.py").read_text()
    assert 'RA_ON = ON and os.getenv(RA_ENV, "0").strip() == "1"' in src


def test_the_directional_model_takes_no_part():
    """Owner: the hourly directional model stays DISABLED and must not gate
    or size anything. Neither the lane, its feed, the evaluator nor the
    engine imports it, and the quote has no fair-value input."""
    for name in ("pmus_crypto_rewards.py", "pmus_crypto_feed.py",
                 "pmus_crypto_reward_economics.py",
                 "pmus_crypto_period_identity.py",
                 "pmus_sports_two_sided.py"):
        src = (ROOT / "src" / name).read_text()
        assert "import pmus_crypto_hourly_fair" not in src
        assert "pmus_crypto_hourly_fair import" not in src
        assert "hourly_fair as" not in src and "HF." not in src
    import inspect
    params = set(inspect.signature(CR.ra_quotes).parameters) | \
        set(inspect.signature(CR.ra_net).parameters)
    assert not params & {"fair", "prob", "price_to_beat", "ptb", "p_up"}


# ------------------------------------------------------------ window, cutoff

def test_cutoff_is_the_fixing_minute_plus_margin_from_the_venue_window_end(
        monkeypatch):
    now = time.time()
    mk = {"end": utc(1800 + 600).isoformat(),          # endDate: +30 min
          "window_end": utc(600).isoformat()}
    assert CR.late_for(ETH, mk, now) == "crypto_last_7200s"   # older mode
    ra_on(monkeypatch)
    assert CR.cutoff_s(ETH) == 90
    assert CR.late_for(ETH, mk, now) is None
    mk["window_end"] = utc(80).isoformat()
    assert CR.late_for(ETH, mk, now) == "crypto_last_90s"
    # the endDate alone would be half an hour wrong: unknown end = late
    assert CR.late_for(ETH, {"end": utc(7200).isoformat()}, now) == \
        "crypto_window_end_unknown"


def test_the_pull_margin_can_never_be_inside_the_fixing_minute(monkeypatch):
    ra_on(monkeypatch, RA_PULL_S=5.0)
    assert CR.cutoff_s(ETH) == 60


def test_targets_one_market_per_asset_and_series(monkeypatch):
    ra_on(monkeypatch, RA_SERIES=("1d", "1h"))
    now = time.time()
    tg = [{"slug": ETH, "window_end": utc(3 * 3600).isoformat()},
          {"slug": ETH_H, "window_end": utc(1800).isoformat()},
          {"slug": SOL, "window_end": utc(60).isoformat()},      # too late
          {"slug": BTC, "window_end": utc(3 * 3600).isoformat()},
          {"slug": XRP, "end": utc(3 * 3600).isoformat()}]
    got = [t["slug"] for t in CR.pick_targets(tg, now)]
    assert got == [ETH, XRP, ETH_H]


# ------------------------------------------------------------ quoting

def test_flat_market_quotes_both_sides_inside_the_walk_at_the_clip(
        monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    q = RQ()
    assert q["YES"] == {"px": D("0.47"), "qty": D("2"), "role": "open"}
    assert q["NO"] == {"px": D("0.49"), "qty": D("2"), "role": "open"}
    assert q["model"]["pays"] and q["model"]["yes_inside"] and \
        q["model"]["no_inside"]
    assert q["depth_ticks"] == 1
    # inside Max Spread: between the best price and the size-adjusted one
    assert q["model"]["bid_adj_px"] <= 0.47 and q["model"]["ask_adj_px"] >= 0.51


def test_size_never_exceeds_the_account_clip(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True, QTY=D("100"))
    q = RQ()
    assert q["YES"]["qty"] == q["NO"]["qty"] == D("2")


def test_the_share_credited_is_that_of_the_size_actually_quoted(monkeypatch):
    """Configured 400: the $100 market cap allows 104, and 10 once $90.40
    is deployed. The net estimate is built on the share of the size that
    would actually rest, not of the configured 400."""
    ra_on(monkeypatch, RA_PROBE=True, QTY=D("400"))
    c = {"pair_cap": "0.97", "complete_max_pair": "0.97"}
    big = RQ(c=c)
    small = RQ(c=c, capital_used="90.4")        # room: $9.60 = 10 pairs
    assert big["YES"]["qty"] == D("104") and small["YES"]["qty"] == D("10")
    assert small["model"]["share"] < big["model"]["share"] / 5
    want = CR.assess(DEEP, PROG, T, [("YES", D("0.47"), 10),
                                     ("NO", D("0.49"), 10)])["share"]
    assert small["model"]["share"] == pytest.approx(want)


def test_pair_of_bids_is_stepped_back_until_it_fits_the_pair_cap(
        monkeypatch):
    """A 1c-wide book: one tick behind is 0.48 + 0.49 = 0.97. Under a 0.96
    cap the older rule quotes nothing; this one steps both sides back."""
    ra_on(monkeypatch, RA_PROBE=True)
    bk = book([(0.49, 300), (0.48, 900), (0.47, 1500)],
              [(0.50, 300), (0.51, 900), (0.52, 1500)])
    q = RQ(bk)
    assert (q["YES"]["px"], q["NO"]["px"]) == (D("0.48"), D("0.49"))
    q = RQ(bk, c={"clip": "2", "pair_cap": "0.96",
                  "complete_max_pair": "0.96"})
    assert (q["YES"]["px"], q["NO"]["px"]) == (D("0.47"), D("0.48"))
    assert q["depth_ticks"] == 2
    old = CR.quotes(book_md=bk, program=PROG, tick=T,
                    pos={"YES": "0", "NO": "0"}, cost={"YES": "0", "NO": "0"},
                    c={"pair_cap": "0.96"})
    assert old["YES"] is None and old["why_YES"] == "pair_cap"


def test_nothing_is_quoted_outside_the_scoring_walk(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    q = RQ(SOL_LIVE)
    assert q["YES"] is None and q["NO"] is None
    assert q["why_YES"] == "outside_scoring_walk"


def test_a_second_that_does_not_pay_is_not_quoted(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    thin = book([(0.48, 500)], [(0.50, 3000)])       # bids short of target
    q = RQ(thin)
    assert q["YES"] is None and q["NO"] is None
    assert q["why_YES"].startswith("second_not_paying:target_size_not_met")
    wide = book([(0.40, 3000)], [(0.60, 3000)])      # 20c > 2 x 0.05
    q = RQ(wide)
    assert q["why_NO"] == "second_not_paying:max_spread"


def test_no_programme_no_opening(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    q = RQ(program=None)
    assert q["YES"] is None and q["NO"] is None
    assert q["why"] == "no_paying_program"


# ------------------------------------------------------------ asymmetric

def test_heavier_side_keeps_quoting_inside_the_unpaired_cap(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    q = RQ(pos={"YES": "2", "NO": "0"}, cost={"YES": "0.94", "NO": "0"})
    assert q["NO"] == {"px": D("0.49"), "qty": D("2"), "role": "complete"}
    assert q["YES"] == {"px": D("0.47"), "qty": D("2"), "role": "open"}
    assert q["YES"]["px"] + q["NO"]["px"] <= D("0.97")


def test_heavier_side_stops_at_the_unpaired_cap(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    q = RQ(pos={"YES": "4", "NO": "0"}, cost={"YES": "1.88", "NO": "0"})
    assert q["YES"] is None and q["why_YES"] == "ra_unpaired_cap"
    # the completion never exceeds the clip, whatever is unpaired
    assert q["NO"] == {"px": D("0.49"), "qty": D("2"), "role": "complete"}
    assert CR.ra_extend_ok({"YES": "2", "NO": "0"}, "YES", "2")
    assert not CR.ra_extend_ok({"YES": "3", "NO": "0"}, "YES", "2")
    assert not CR.ra_extend_ok({"YES": "2", "NO": "0"}, "NO", "2")
    assert CR.ra_extend_ok({"YES": "6", "NO": "6"}, "NO", "2")


def test_completion_is_priced_inside_the_pair_cap_and_never_forced(
        monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    # held YES at 0.60: the NO completion may cost at most 0.37
    q = RQ(pos={"YES": "2", "NO": "0"}, cost={"YES": "1.20", "NO": "0"})
    assert q["NO"]["role"] == "complete" and q["NO"]["px"] == D("0.37")
    # held at 0.97: no price completes inside the cap -> no order at all
    q = RQ(pos={"YES": "2", "NO": "0"}, cost={"YES": "1.94", "NO": "0"})
    assert q["NO"] is None and q["why_NO"] == "no_breakeven_price"


def test_extension_must_pair_with_the_completion_under_the_cap(monkeypatch):
    """Held YES at 0.47 on a book that has moved up: the completion rests
    at 0.39 behind the NO touch; a YES extension at 0.57 would pair with it
    at 0.96 -- allowed; at a 0.95 cap it is stepped back or withheld."""
    ra_on(monkeypatch, RA_PROBE=True)
    q = RQ(around(0.58, 0.60), pos={"YES": "2", "NO": "0"},
           cost={"YES": "0.94", "NO": "0"})
    assert q["NO"]["px"] == D("0.39") and q["YES"]["px"] == D("0.57")
    q = RQ(around(0.58, 0.60), pos={"YES": "2", "NO": "0"},
           cost={"YES": "0.94", "NO": "0"},
           c={"clip": "2", "pair_cap": "0.95", "complete_max_pair": "0.95"})
    assert q["YES"]["px"] + q["NO"]["px"] <= D("0.95")


# ------------------------------------------------------------ net estimate

def test_unknown_allocation_credits_nothing_and_opens_nothing(monkeypatch):
    """Owner 28 Nov: no assumed payout. With no hypothesis named the reward
    credit is zero and nothing is replenished."""
    ra_on(monkeypatch)
    q = RQ()
    assert q["YES"] is None and q["NO"] is None
    assert q["why_YES"] == q["why_NO"] == "ra_net:unknown_pool_scope"
    ra = q["ra"]
    assert ra["ok"] is False and ra["reward_usd"] == "0"
    assert ra["evidence"] == "PUBLISHED" and ra["allocation"] is None
    # every hypothesis is still REPORTED, each labelled INFERRED
    assert set(ra["by_hypothesis"]) == {"shared_program_et_day",
                                        "per_contract_et_day",
                                        "per_contract_window"}
    assert all(h["evidence"] == "INFERRED"
               for h in ra["by_hypothesis"].values())


def test_unknown_cost_opens_nothing(monkeypatch):
    ra_on(monkeypatch, RA_ALLOCATION="per_contract_et_day",
          RA_FILL_COST_USD="")
    q = RQ()
    assert q["YES"] is None and q["why_YES"] == "ra_net:cost_unknown"


def test_two_contracts_project_cents_and_fail_the_floor_on_every_basis(
        monkeypatch):
    """2 a side against a 2,000 target: under each allocation hypothesis
    the projected reward is under the $1.00 payout floor."""
    ra_on(monkeypatch, RA_ALLOCATION="per_contract_window")
    q = RQ()
    assert q["YES"] is None
    assert q["why_YES"] == "ra_net:net_not_positive:floor_or_cost"
    for h in q["ra"]["by_hypothesis"].values():
        assert h["reward_usd"] == "0"
        assert 0 < D(h["before_floor_usd"]) < 1


def test_positive_estimate_replenishes_and_is_labelled_inferred(monkeypatch):
    ra_on(monkeypatch, RA_ALLOCATION="per_contract_et_day",
          QTY=D("400"), RA_FILLS_PER_HOUR="0.5")
    q = RQ(c={"pair_cap": "0.97", "complete_max_pair": "0.97"},
           max_leg_usd=None)
    # the conservative basis is under the $1 floor for the same quote
    assert q["ra"]["by_hypothesis"]["shared_program_et_day"][
        "reward_usd"] == "0"
    assert q["ra"]["ok"] and q["ra"]["evidence"] == "INFERRED"
    assert D(q["ra"]["net_usd"]) > 0
    assert q["YES"]["role"] == "open" and q["NO"]["role"] == "open"


def test_estimate_turns_negative_when_cost_exceeds_reward(monkeypatch):
    ra_on(monkeypatch, RA_ALLOCATION="per_contract_et_day",
          QTY=D("400"), RA_FILLS_PER_HOUR="200", RA_FILL_COST_USD="0.05")
    q = RQ(c={"pair_cap": "0.97", "complete_max_pair": "0.97"})
    assert q["YES"] is None
    assert q["why_YES"].startswith("ra_net:net_not_positive")
    assert D(q["ra"]["net_usd"]) < 0


def test_paid_evidence_is_claimed_only_for_the_verified_hypothesis(
        monkeypatch):
    ra_on(monkeypatch, RA_ALLOCATION="per_contract_et_day",
          RA_PAID_VERIFIED="per_contract_et_day", QTY=D("400"),
          RA_FILLS_PER_HOUR="0.5")
    q = RQ(c={"pair_cap": "0.97", "complete_max_pair": "0.97"})
    assert q["ra"]["evidence"] == "PAID"
    by = q["ra"]["by_hypothesis"]
    assert by["per_contract_et_day"]["evidence"] == "PAID"
    assert by["shared_program_et_day"]["evidence"] == "INFERRED"


def test_unknown_paid_fraction_credits_nothing(monkeypatch):
    ra_on(monkeypatch, RA_ALLOCATION="per_contract_et_day",
          QTY=D("400"), RA_FILLS_PER_HOUR="0.5")
    q = RQ(c={"pair_cap": "0.97", "complete_max_pair": "0.97"},
           paid_fraction=0)
    assert q["YES"] is None and D(q["ra"]["reward_usd"]) == 0


def test_probe_quotes_without_a_positive_estimate_and_says_so(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    q = RQ()
    assert q["YES"] and q["NO"]
    assert q["ra"]["ok"] is False and q["ra"]["probe"] is True
    assert q["ra"]["why"] == "unknown_pool_scope"


def test_hourly_window_estimate_uses_the_hour(monkeypatch):
    ra_on(monkeypatch, RA_SERIES=("1d", "1h"),
          RA_ALLOCATION="per_contract_window", RA_PROBE=True)
    hb = book([(0.48, 300), (0.47, 400), (0.46, 800)],
              [(0.50, 300), (0.51, 400), (0.52, 800)])
    q = RQ(hb, program=PROG_H, slug=ETH_H, seconds_left=1890)
    by = q["ra"]["by_hypothesis"]
    # 30 minutes of quoting left after the 90 s pull
    assert q["ra"]["remaining_s"] == 1800
    w = D(by["per_contract_window"]["before_floor_usd"])
    d = D(by["per_contract_et_day"]["before_floor_usd"])
    s = D(by["shared_program_et_day"]["before_floor_usd"])
    assert w / d == pytest.approx(24) and d / s == pytest.approx(3)


# ------------------------------------------------------------ index jump

def test_index_jump_and_staleness():
    now = 1_000_000.0
    calm = [(now - 30 + i, 1645.0 + 0.01 * i) for i in range(30)]
    assert CR.index_jump(calm, now, 10, 10, 20) is None
    jump = calm[:-1] + [(now - 1, 1645.0 * 1.0015)]
    assert CR.index_jump(jump, now, 10, 10, 20).startswith("index_jump:")
    # an old move outside the window is not a jump
    old = [(now - 60, 1600.0)] + [(now - 9 + i, 1645.0) for i in range(9)]
    assert CR.index_jump(old, now, 10, 10, 20) is None
    assert CR.index_jump([], now) == "index_stale"
    assert CR.index_jump([(now - 60, 1645.0)], now, 10, 10, 20) == \
        "index_stale"
    assert CR.index_jump([("x", None), (now, 0)], now) == "index_stale"


# ------------------------------------------------------------ approval

def ra_arm(monkeypatch, **over):
    monkeypatch.setenv(CR.ARM_ENV, "YES")
    monkeypatch.setenv(CR.SHA_ENV, SHA)
    monkeypatch.setenv(CR.COMBINED_CAP_ENV, "360")
    monkeypatch.setenv(CR.APPROVAL_ENV, json.dumps(approval(**over)))


def test_approval_must_repeat_every_reward_aware_setting(monkeypatch):
    ra_on(monkeypatch, RA_PROBE=True)
    ra_arm(monkeypatch)
    assert CR.arm_state(time.time())["armed"]
    assert CR.approval_template()["reward_aware"]["probe"] is True
    # any setting changed after signing disarms
    monkeypatch.setattr(CR, "RA_MAX_UNPAIRED_QTY", D("10"))
    assert CR.arm_state(time.time())["why"] == \
        "approval_mismatch:reward_aware"
    monkeypatch.setattr(CR, "RA_MAX_UNPAIRED_QTY", D("4"))
    monkeypatch.setattr(CR, "RA_ALLOCATION", "per_contract_window")
    assert CR.arm_state(time.time())["why"] == \
        "approval_mismatch:reward_aware"


def test_an_approval_of_the_older_mode_does_not_arm_this_one(monkeypatch):
    monkeypatch.setattr(CR, "ON", True)
    monkeypatch.setattr(CR, "QTY", D("2"))
    ra_arm(monkeypatch)                      # signed with the mode off
    assert CR.arm_state(time.time())["armed"]
    monkeypatch.setattr(CR, "RA_ON", True)
    assert CR.arm_state(time.time())["why"] == \
        "approval_mismatch:reward_aware"


def test_an_approval_naming_the_mode_does_not_arm_the_older_one(monkeypatch):
    ra_on(monkeypatch)
    ra_arm(monkeypatch)
    monkeypatch.setattr(CR, "RA_ON", False)
    assert CR.arm_state(time.time())["why"] == \
        "approval_mismatch:reward_aware"


# ------------------------------------------------------------ attribution

def held(y, n, cy, cn, **kw):
    return {"pos": {"YES": y, "NO": n}, "cost": {"YES": cy, "NO": cn},
            "orders": {}, "lane": "crypto", **kw}


def test_reward_rows_are_split_paid_awaiting_skipped():
    rows = [{"marketSlug": ETH, "status": "PAID", "reward": "1.20"},
            {"marketSlug": ETH, "status": "PENDING", "reward": "0.30"},
            {"marketSlug": ETH_H, "status": "SKIPPED", "reward": "0.04"},
            {"marketSlug": "aec-nba-bos-nyk-2026-04-01", "status": "PAID",
             "reward": "9"}]
    r = CR.venue_rewards(rows, ("PAID",))
    assert r["rows"] == 3
    assert (r["paid_usd"], r["awaiting_usd"], r["skipped_usd"]) == \
        ("1.20", "0.30", "0.04")
    assert r["credited_usd"] == "1.20" and r["pending_usd"] == "0.34"


def test_attribution_is_by_series_and_never_includes_sports():
    rows = [{"marketSlug": ETH, "status": "PAID", "reward": "1.20"},
            {"marketSlug": ETH_H, "status": "SKIPPED", "reward": "0.04"}]
    markets = {
        ETH: held("4", "2", "1.88", "0.98", settle_px="1",
                  crypto_fills=[{"px": "0.47", "qty": "2"},
                                {"px": "0.47", "qty": "2"},
                                {"px": "0.49", "qty": "2"}]),
        ETH_H: held("0", "2", "0", "0.90",
                    crypto_fills=[{"px": "0.45", "qty": "2"}]),
        "aec-nba-bos-nyk-2026-04-01": {
            "pos": {"YES": "10", "NO": "0"}, "cost": {"YES": "5", "NO": "0"},
            "orders": {}}}
    a = CR.attribution(markets, CR.venue_rewards(rows, ("PAID",)))
    assert a["lane"] == "crypto" and set(a["by_series"]) == {"1d", "1h"}
    d = a["by_series"]["1d"]
    assert d["fills"] == 3 and d["contracts"] == "6"
    # 2 pairs at 0.47 + 0.49 = 0.96 -> 0.08 edge; 2 YES residual at 0.47,
    # settled Up: +1.06
    assert D(d["pair_edge_usd"]) == D("0.08") and d["pairs"] == "2"
    assert d["residual_contracts"] == "2"
    assert D(d["residual_settled_usd"]) == D("1.06")
    assert d["rewards"]["paid_usd"] == "1.20"
    h = a["by_series"]["1h"]
    assert h["residual_open_contracts"] == "2"
    assert h["rewards"]["skipped_usd"] == "0.04"
    assert h["rewards"]["paid_usd"] == "0"
    assert "never mixed with sports" in a["label"]
    assert D(d["cost_usd"]) == D("2.86")             # no sports dollars


def test_attribution_without_reward_rows_says_so():
    a = CR.attribution({ETH: held("2", "0", "0.94", "0")}, None)
    assert a["reward_rows_read"] is False
    assert a["by_series"]["1d"]["rewards"]["paid_usd"] == "0"


# ------------------------------------------------------------ identity

def terms(asset, start, dur="1d"):
    end = start + timedelta(seconds=86400 if dur == "1d" else 3600)
    return {"marketType": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
            "asset": {"assetClass": "ASSET_CLASS_CRYPTO", "symbol": asset},
            "indexSymbol": INDEX[asset], "horizon": dur,
            "windowStart": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "windowEnd": end.strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_identity_block_needs_the_venue_terms_to_agree(monkeypatch):
    ra_on(monkeypatch, RA_SERIES=("1d", "1h"))
    assert CR.ra_identity_block(ETH, terms("eth", D_START)) is None
    assert CR.ra_identity_block(ETH_H, terms("eth", H_START, "1h")) is None
    assert CR.ra_identity_block(ETH, None) == \
        "identity_unverified:no_venue_terms"
    assert CR.ra_identity_block(ETH, terms("sol", D_START)) == \
        "identity_unverified:asset"
    late = terms("eth", D_START + timedelta(hours=1))
    assert CR.ra_identity_block(ETH, late).startswith("identity_unverified:")
    assert CR.ra_identity_block(BTC, terms("btc", D_START)) == \
        "identity_not_pilot"
    assert CR.ra_identity_block("cpceth-eth-updown-15m-2026-11-28-2030z",
                                {}) == "identity_unknown"


# ------------------------------------------------------------ feed

class RV(CV):
    """CV with the venue's window terms, hourly rows and reference index.
    Daily slugs follow the real pattern; the window is placed around now so
    the cutoff and the open delay can be exercised."""

    def __init__(self):
        super().__init__()
        # a REAL daily boundary (22:00Z) so slug and venue terms agree at
        # any time of day: the window that contains now + 12 h. Tests that
        # exercise timing set the selected market's window themselves.
        base = utc(12 * 3600)
        self.w_start = base.replace(hour=22, minute=0, second=0)
        if self.w_start > base:
            self.w_start -= timedelta(hours=24)
        self.w_end = self.w_start + timedelta(hours=24)
        self.eth = day_slug("eth", self.w_start)
        self.sol = day_slug("sol", self.w_start)
        self.xrp = day_slug("xrp", self.w_start)
        self.btc = day_slug("btc", self.w_start)
        self.crypto = [self.eth, self.sol, self.xrp, self.btc]
        self.terms = {}
        self.hourly = []
        self.rows_for = None                 # None = every non-BTC market
        self.index_px = {"eth": 1645.0, "sol": 70.5, "xrp": 0.939}
        self.index_lag = 1.0
        self.index_jump = {}
        self.index_fail = False
        self.index_reads = 0

    def place(self, slug, side, px, qty, stage, post_only=True):
        oid = super().place(slug, side, px, qty, stage, post_only)
        self.orders[oid]["stage"] = stage
        return oid

    def _terms(self, slug):
        if slug in self.terms:
            return self.terms[slug]
        asset = CR.asset_of(slug) or "btc"
        if "-1h-" in slug:
            return terms(asset, self.h_start, "1h")
        return terms(asset, self.w_start)

    def add_hourly(self):
        self.h_start = utc(3600).replace(minute=0, second=0)
        self.h_end = self.h_start + timedelta(hours=1)
        slug = "cpceth-eth-updown-1h-%sz" % self.h_start.strftime(
            "%Y-%m-%d-%H%M")
        self.hourly = [slug]
        self.crypto = self.crypto + [slug]
        return slug

    def gget(self, path):
        import threading
        self.gateway_threads.add(threading.current_thread().name)
        if path.startswith("/v1/events"):
            return {"events": [{"markets": [
                {"slug": s, "endDate": self.end,
                 "assetPriceTerms": self._terms(s)} for s in self.crypto]}]}
        if path.startswith("/v1/asset-prices/history"):
            self.index_reads += 1
            if self.index_fail:
                raise RuntimeError("index down")
            t1 = int(time.time() - self.index_lag)
            out = []
            for a, px in self.index_px.items():
                cs = [{"timestamp": t1 - 30 + i, "close": str(px),
                       "tickCount": 60} for i in range(30)]
                cs.append({"timestamp": t1,
                           "close": str(px * (1 + self.index_jump.get(a, 0))),
                           "tickCount": 60})
                cs.append({"timestamp": t1 + 1, "close": "1",
                           "tickCount": 0})          # gap-filled: dropped
                out.append({"asset": {"symbol": a}, "candles": cs})
            return {"series": out}
        if path.startswith("/v1/incentives"):
            rows = []
            for s in self.crypto:
                if s not in path or s == self.btc:
                    continue
                if self.rows_for is not None and s not in self.rows_for:
                    continue
                h = "-1h-" in s
                rows.append({"marketSlug": s, "timePeriods": [{
                    "programId": "crypto_alt_updown_%s_20261110"
                    % ("1h" if h else "1d"),
                    "programType": "liquidityProgram",
                    "rewardPool": 8 if h else 150,
                    "start": "2026-11-10T16:00:00Z", "status": "active",
                    "discountFactor": 0.3,
                    "targetSize": 1000 if h else 2000,
                    "period": "daily_event", "maxSpread": 0.05}]})
            return {"programs": rows}
        return super().gget(path)


def test_feed_carries_the_window_terms_and_reads_the_index(monkeypatch):
    ra_on(monkeypatch)
    v = RV()
    f = CF.CryptoFeed(v.gget, v.spec, sleep=lambda s: None)
    f.poll_once(force=True)
    tg = {t["slug"]: t for t in f.targets()[0]}
    assert tg[v.eth]["window_end"] == v._terms(v.eth)["windowEnd"]
    assert CR.ra_identity_block(v.eth, tg[v.eth]["terms"]) is None
    assert tg[v.eth]["terms"]["indexSymbol"] == "ETHUSD_RTI"
    series, age = f.index("eth")
    assert len(series) == 31 and age < 5        # the gap-filled row dropped
    assert CR.index_jump(series, time.time()) is None
    assert set(f.stats()["index_age_s"]) == {"eth", "sol", "xrp"}
    assert f.index("btc") == ([], float("inf"))


def test_feed_does_not_read_the_index_with_the_mode_off(monkeypatch):
    monkeypatch.setattr(CR, "ON", True)
    v = RV()
    f = CF.CryptoFeed(v.gget, v.spec, sleep=lambda s: None)
    f.poll_once(force=True)
    assert v.index_reads == 0 and f.index("eth")[0] == []


def test_an_index_error_ages_the_data_and_never_raises(monkeypatch):
    ra_on(monkeypatch)
    v = RV()
    clock = [1000.0]
    f = CF.CryptoFeed(v.gget, v.spec, sleep=lambda s: None,
                      clock=lambda: clock[0])
    f.poll_once(force=True)
    v.index_fail = True
    clock[0] += 60
    f.poll_once(force=True)
    series, age = f.index("eth")
    assert series and age == 60 and "index" in f.last_error


# ------------------------------------------------------------ engine

@pytest.fixture
def ra(tmp_path, monkeypatch):
    """Reward-aware probe lane, armed with a matching approval, under the
    REAL final gate and clip (nothing of the sports protections off)."""
    assert M.FINAL_GATE and M.rn1_max_clip() == D("2")
    monkeypatch.setattr(RN1, "ON", True)
    ra_on(monkeypatch, RA_PROBE=True)
    ra_arm(monkeypatch)
    monkeypatch.setattr(M, "ACCOUNT_RISK_ON", True)
    monkeypatch.setattr(M, "rn1_paid_fraction", lambda now=None: 0.858)
    v = RV()
    e = wire(M.Engine(v, M.Book(tmp_path / "s.json")), v)
    fresh_meter(e)
    return e, v


def sel(e, v):
    """Select, then place every selected market mid-window (6 h in, 6 h to
    go) so the open delay and the cutoff are out of the way."""
    got = select(e, v)
    for s_ in got:
        e.b.m(s_)["window_start"] = utc(-6 * 3600).isoformat()
        e.b.m(s_)["window_end"] = utc(6 * 3600).isoformat()
    return got


def live(v, slug):
    return sorted((o["side"], o["px"], o["qty"], o["stage"])
                  for o in v.live(slug).values())


def test_probe_lane_rests_two_contracts_a_side_inside_the_clip(ra):
    e, v = ra
    assert sorted(sel(e, v)) == sorted([v.eth, v.sol, v.xrp])
    e.step_market(v.eth)
    assert live(v, v.eth) == [("NO", D("0.49"), D("2"), "crypto_open"),
                              ("YES", D("0.47"), D("2"), "crypto_open")]
    e.step_market(v.eth)
    assert len(v.orders) == 2                      # unchanged book: no churn
    e.safety_check(v.account())
    assert e.safety_latch() is None
    listed = {o["venue_order_id"] for o in M.registry_doc(e.b)["orders"]
              if not o.get("closing")}
    assert listed == set(v.live())
    assert v.top_depth_calls == []


def test_without_probe_or_hypothesis_nothing_is_placed(ra, monkeypatch,
                                                       capsys):
    e, v = ra
    monkeypatch.setattr(CR, "RA_PROBE", False)
    ra_arm(monkeypatch)
    assert CR.arm_state(time.time())["armed"]
    sel(e, v)
    e.step_market(v.eth)
    assert not v.orders
    line = [json.loads(x[x.index("{"):])
            for x in capsys.readouterr().out.splitlines()
            if "CRYPTO_QUOTE" in x][-1]
    assert line["why"]["YES"] == "ra_net:unknown_pool_scope"
    assert line["reward_aware"]["evidence"] == "PUBLISHED"


def test_a_fill_brings_a_completion_and_a_bounded_extension(ra):
    e, v = ra
    sel(e, v)
    e.step_market(v.eth)
    yes = next(k for k, o in v.live(v.eth).items() if o["side"] == "YES")
    v.fill(yes)
    e.step_market(v.eth)
    e.step_market(v.eth)
    assert live(v, v.eth) == [("NO", D("0.49"), D("2"), "crypto_complete"),
                              ("YES", D("0.47"), D("2"), "crypto_open")]
    # second YES fill: 4 unpaired = the cap. No further YES opening.
    yes = next(k for k, o in v.live(v.eth).items() if o["side"] == "YES")
    v.fill(yes)
    e.step_market(v.eth)
    e.step_market(v.eth)
    assert live(v, v.eth) == [("NO", D("0.49"), D("2"), "crypto_complete")]
    assert e.b.m(v.eth)["pos"]["YES"] == "4"
    assert e.crypto_place_block(v.eth, "YES", D("0.47"), D("2"),
                                "crypto_open", True) == "ra_unpaired_cap"
    # the completion fills: 2 paired, 2 unpaired -> both sides work again
    no = next(iter(v.live(v.eth)))
    v.fill(no)
    e.step_market(v.eth)
    e.step_market(v.eth)
    assert live(v, v.eth) == [("NO", D("0.49"), D("2"), "crypto_complete"),
                              ("YES", D("0.47"), D("2"), "crypto_open")]
    assert not getattr(v, "taker", [])


def test_every_order_stays_inside_the_final_gate(ra, monkeypatch):
    """The lane never asks for more than the gate allows: the gate's own
    refusals stay zero through a fill sequence."""
    e, v = ra
    seen = []
    real = e.final_gate

    def gate(*a, **k):
        why = real(*a, **k)
        seen.append((a, why))
        return why
    monkeypatch.setattr(e, "final_gate", gate)
    sel(e, v)
    for _ in range(3):
        e.step_market(v.eth)
        for k, o in list(v.live(v.eth).items()):
            if o["side"] == "YES" and o["stage"] == "crypto_open":
                v.fill(k)
        e.step_market(v.eth)
    assert seen and all(why is None for _a, why in seen)
    assert all(D(str(a[3])) <= 2 for a, _w in seen)


def test_quotes_are_pulled_before_the_fixing_minute(ra):
    e, v = ra
    sel(e, v)
    e.step_market(v.eth)
    yes = next(k for k, o in v.live(v.eth).items() if o["side"] == "YES")
    v.fill(yes)
    e.step_market(v.eth)
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2
    e.b.m(v.eth)["window_end"] = utc(80).isoformat()
    e.step_market(v.eth)
    # the opening is pulled; the completion (it only reduces risk) stays
    assert live(v, v.eth) == [("NO", D("0.49"), D("2"), "crypto_complete")]
    assert e.crypto_block(v.eth, e.b.m(v.eth), time.time()) == \
        "late:crypto_last_90s"


def test_no_quote_in_the_first_minute_of_a_window(ra):
    e, v = ra
    sel(e, v)
    e.b.m(v.eth)["window_start"] = utc(-20).isoformat()
    e.step_market(v.eth)
    assert not v.orders
    assert e.crypto_block(v.eth, e.b.m(v.eth), time.time()) == \
        "ra_open_delay"
    e.b.m(v.eth)["window_start"] = None
    assert e.crypto_block(v.eth, e.b.m(v.eth), time.time()) == \
        "crypto_window_start_unknown"


def test_an_index_jump_pulls_the_quotes_and_pauses(ra):
    e, v = ra
    sel(e, v)
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2
    v.index_jump = {"eth": 0.002}                    # 20 bps in a second
    e.step_market(v.eth)
    assert not v.live(v.eth)
    assert e.b.m(v.eth)["jump_pause_until"] > time.time()
    v.index_jump = {}
    e.step_market(v.eth)                             # still paused
    assert not v.live(v.eth)
    assert e.crypto_block(v.eth, e.b.m(v.eth), time.time()) == "jump_pause"
    e.b.m(v.eth)["jump_pause_until"] = 0
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2
    # another asset's jump does not touch this market
    v.index_jump = {"sol": 0.01}
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2


def test_no_index_no_quote(ra, monkeypatch):
    e, v = ra
    sel(e, v)
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2
    v.index_lag = 300.0                              # index went quiet
    e.step_market(v.eth)
    assert not v.live(v.eth)
    assert e.crypto_block(v.eth, e.b.m(v.eth), time.time()) == "index_stale"
    monkeypatch.setattr(CR, "RA_REQUIRE_INDEX", False)
    assert e.crypto_block(v.eth, e.b.m(v.eth), time.time()) is None


def test_a_venue_mid_jump_uses_the_existing_jump_pause(ra):
    e, v = ra
    sel(e, v)
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2
    before = e.jump_pauses
    v.books[v.eth] = around(0.52, 0.54)              # mid +4c in one pass
    e.step_market(v.eth)
    assert not v.live(v.eth) and e.jump_pauses == before + 1
    assert e.b.m(v.eth)["jump_pause_until"] == \
        pytest.approx(time.time() + M.JUMP_PAUSE_S, abs=2)


def test_a_market_whose_terms_disagree_is_not_selected(ra):
    e, v = ra
    v.terms[v.sol] = terms("sol", v.w_start + timedelta(hours=1))
    v.terms[v.xrp] = None
    assert select(e, v) == [v.eth]


def test_hourly_market_needs_its_series_and_its_programme_row(ra,
                                                              monkeypatch):
    e, v = ra
    h = v.add_hourly()
    v.rows_for = [v.eth, v.sol, v.xrp]       # venue lists no hourly row yet
    assert h not in select(e, v)             # series "1d": not even a target
    monkeypatch.setattr(CR, "RA_SERIES", ("1d", "1h"))
    monkeypatch.setattr(CR, "MAX_MARKETS", 4)
    ra_arm(monkeypatch)
    assert h not in select(e, v)             # no programme row: fail closed
    assert e.__dict__["_crypto_skip"][h] == "no_paying_program"
    v.rows_for = None
    assert h in select(e, v)
    assert e.b.m(h)["reward"]["pool"] == 8 and \
        e.b.m(h)["reward"]["targetSize"] == 1000


def test_global_protections_still_bind(ra, monkeypatch):
    e, v = ra
    sel(e, v)
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2
    # the account's daily halt
    monkeypatch.setattr(e, "daily_stopped", lambda: True)
    e.step_market(v.eth)
    assert not v.live(v.eth)
    assert e.crypto_block(v.eth, e.b.m(v.eth), time.time()) == \
        "daily_loss_stop"
    monkeypatch.setattr(e, "daily_stopped", lambda: False)
    e.step_market(v.eth)
    assert len(v.live(v.eth)) == 2
    # the sports submission lock
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    e.enforce_lock()
    assert not v.live()
    n = len(v.orders)
    e.step_market(v.eth)
    assert len(v.orders) == n
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV)
    # the latched crypto loss stop
    e._crypto_meter["realized_net_usd"] = "-30"
    assert e.crypto_loss_stopped()
    e.step_market(v.eth)
    assert len(v.orders) == n
    # a size above the clip is refused at the choke point as before
    assert e.final_gate(v.eth, "YES", D("0.47"), D("3"), "crypto_open") == \
        "size_above_maximum:3>2"


def test_disarmed_mode_places_nothing(ra, monkeypatch):
    e, v = ra
    monkeypatch.delenv(CR.ARM_ENV)
    sel(e, v)
    for s in (v.eth, v.sol, v.xrp):
        e.step_market(s)
    assert not v.orders
    e.place(v.eth, "YES", D("0.47"), D("2"), "crypto_open")
    assert not v.orders


def test_status_reports_crypto_attribution_under_its_own_key(ra):
    e, v = ra
    sel(e, v)
    e.step_market(v.eth)
    yes = next(k for k, o in v.live(v.eth).items() if o["side"] == "YES")
    v.fill(yes)
    e.step_market(v.eth)
    st = e.crypto_status()
    a = st["attribution"]
    assert a["lane"] == "crypto"
    assert a["by_series"]["1d"]["fills"] == 1
    assert a["by_series"]["1d"]["residual_open_contracts"] == "2"
    assert a["reward_rows_read"] is False
    e.crypto_reward_credits({"ok": True, "rows": 1, "rows_all": [
        {"marketSlug": v.eth, "status": "SKIPPED", "reward": "0.02"}]})
    a = e.crypto_status()["attribution"]
    assert a["by_series"]["1d"]["rewards"]["skipped_usd"] == "0.02"
    assert a["by_series"]["1d"]["rewards"]["paid_usd"] == "0"
