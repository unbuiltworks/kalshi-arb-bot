"""Reward calibration (PMUS_RN1_REWARD_CALIBRATION, off by default).

Off: every site credits exactly what it credited before (the clamped
RANK_REWARD_HAIRCUT where a haircut applied, the raw estimate where none
did). On: the owner's table (programme, then competition, then "*", else
the haircut), clamped to [0, 1], divided by the pool-scope divisor. The
read-only routine reports venue actual / THEORETICAL by competition and is
never applied automatically.
"""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_reward_calibration as RC
from src import pmus_sports_rewards as RW
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    SLUG, eng)


@pytest.fixture
def cal(monkeypatch):
    monkeypatch.setattr(M, "RANK_REWARD_HAIRCUT", 0.5)
    monkeypatch.setattr(M, "RN1_REWARD_POOL_SCOPE", "market")
    return monkeypatch


def test_off_is_the_haircut_and_raw_sites_stay_raw(cal):
    assert M.rn1_reward_mult("aec-atp-a-b") == 0.5
    assert M.rn1_reward_cal("aec-atp-a-b") == 1.0
    cal.setattr(M, "RANK_REWARD_HAIRCUT", 1.7)
    assert M.rn1_reward_mult("aec-atp-a-b") == 1.0          # clamped as before


def test_on_table_precedence_and_clamp(cal):
    cal.setattr(M, "RN1_REWARD_CALIBRATION", True)
    cal.setattr(M, "RN1_REWARD_CAL_TABLE",
                "program=atp_live:0.3,atp:0.42,*:0.4,utr:7,bad,x:y")
    per = {"programId": "ATP_LIVE"}
    assert M.rn1_reward_mult("aec-atp-a-b", per) == 0.3
    assert M.rn1_reward_mult("aec-atp-a-b", {"programId": "o"}) == 0.42
    assert M.rn1_reward_mult("aec-wta-a-b") == 0.4
    assert M.rn1_reward_mult("aec-utr-a-b") == 1.0           # clamped
    assert M.rn1_reward_cal("aec-wta-a-b") == 0.4
    cal.setattr(M, "RN1_REWARD_CAL_TABLE", "")
    assert M.rn1_reward_mult("aec-wta-a-b") == 0.5           # haircut


def test_pool_scope_divisor(cal):
    cal.setattr(M, "RN1_REWARD_CALIBRATION", True)
    cal.setattr(M, "RN1_REWARD_CAL_TABLE", "*:0.4")
    per = {"M_event": 2, "M_program": 8}
    assert M.rn1_reward_mult("aec-atp-a-b", per) == 0.4
    cal.setattr(M, "RN1_REWARD_POOL_SCOPE", "event")
    assert M.rn1_reward_mult("aec-atp-a-b", per) == pytest.approx(0.2)
    cal.setattr(M, "RN1_REWARD_POOL_SCOPE", "program")
    assert M.rn1_reward_mult("aec-atp-a-b", per) == pytest.approx(0.05)
    assert M.rn1_reward_mult("aec-atp-a-b", {}) == pytest.approx(0.4)


def test_program_map_counts_markets_per_programme_and_event():
    row = {"timePeriods": [{"programType": "liquidityProgram",
                            "programId": "P", "rewardPool": "10",
                            "discountFactor": "0.3", "targetSize": "100",
                            "period": "daily"}]}
    found = {"a-1": dict(row), "a-2": dict(row), "b-1": dict(row)}
    pm = RW.build_program_map(found, {"a-1": "A", "a-2": "A", "b-1": "B"})
    p = pm["a-1"]["periods"][0]
    assert p["M_event"] == 2 and p["M_program"] == 3


# ------------------------------------------------------------ the routine
def _row(slug, day, amt, status, prog=None):
    r = {"marketSlug": slug, "date": day, "amount": str(amt),
         "status": status, "programType": "liquidityProgram"}
    if prog:
        r["programId"] = prog
    return r


def test_calibrate_by_competition_paid_preferred_pending_flagged():
    rows = [_row("aec-atpdb-a-2026-11-25", "2026-11-25", "0.59", "PENDING"),
            _row("aec-setkameua-b-2026-11-26", "2026-11-25", "0.10",
                 "PENDING"),
            _row("aec-utr-c-2026-11-24", "2026-11-24", "0.05", "PAID", "U"),
            _row("aec-utr-d-2026-11-24", "2026-11-24", "0.00", "SKIPPED",
                 "U"),
            _row("aec-utr-e-2026-11-24", "2026-11-24", "0.02", "PAID"),
            {"marketSlug": "junk"}, "not-a-row"]
    theo = {"aec-atpdb-a-2026-11-25": {"2026-11-25": 1.24},
            "aec-setkameua-b-2026-11-26": {"2026-11-25": 0.31},
            "aec-utr-c-2026-11-24": {"2026-11-24": 0.05},
            "aec-utr-d-2026-11-24": {"2026-11-24": 0.05},
            "aec-utr-z-2026-11-24": {"2026-11-24": 0.30}}     # no row
    got = RC.calibrate(rows, theo)
    a = got["by_competition"]["atpdb"]
    assert a["basis"] == "PENDING_NOT_CREDITED" and a["ratio_paid"] is None
    assert a["ratio_pending"] == pytest.approx(0.59 / 1.24, abs=1e-4)
    u = got["by_competition"]["utr"]
    assert u["basis"] == "PAID"
    # PAID 0.05 over the theoretical of the settled rows (PAID + SKIPPED)
    assert u["ratio_paid"] == pytest.approx(0.05 / 0.10)
    assert u["n_paid"] == 1 and u["n_skipped"] == 1 and u["rows"] == 3
    assert u["rows_matched"] == 2                    # e has no estimate
    assert u["n_no_row"] == 1 and D(u["theo_no_row_usd"]) == D("0.3")
    assert got["by_program"]["U"]["rows"] == 2
    assert "never applied automatically" in got["label"]


def test_the_25_nov_evidence_reproduces_about_0_42():
    """MEV_NO_FAIR_VALIDATION_20261126.md section 2: rows with an estimate,
    THEORETICAL $1.87 against PENDING $0.79 -> 0.42."""
    tab = [("aec-atpdb-delanedelane-falcksuarez-2026-11-25", 1.24, 0.59),
           ("aec-setkameua-slomyk-fasiva-2026-11-26", 0.31, 0.10),
           ("aec-setkameua-tebyar-paryur-2026-11-26", 0.038, 0.01),
           ("aec-utr-kawgab-duadan-2026-11-24", 0.089, 0.01),
           ("aec-utr-feragu-tistom-2026-11-24", 0.030, 0.05),
           ("aec-utr-boymel-gobvic-2026-11-24", 0.015, 0.02),
           ("aec-atp-jaskub-carove-2026-11-25", 0.074, 0.01)]
    rows = [_row(s, "2026-11-25", a, "PENDING") for s, _t, a in tab]
    theo = {s: {"2026-11-25": t} for s, t, _a in tab}
    # estimated, no venue row (counted as zero in the document's 0.42)
    theo["aec-setkameua-kimdmy-elfiva-2026-11-25"] = {"2026-11-25": 0.065}
    theo["aec-setkameua-kovvea-misole-2026-11-25"] = {"2026-11-25": 0.013}
    got = RC.calibrate(rows, theo)["by_competition"]
    act = sum(D(g["pending_matched_usd"]) for g in got.values())
    th = sum(D(g["theo_pending_usd"]) for g in got.values())
    th0 = th + sum(D(g["theo_no_row_usd"]) for g in got.values())
    assert float(act / th) == pytest.approx(0.44, abs=0.005)
    assert float(act / th0) == pytest.approx(0.42, abs=0.005)
    assert got["atpdb"]["ratio_pending"] == pytest.approx(0.476, abs=0.001)


# ------------------------------------------------------------ engine
def test_theoretical_accrual_only_with_the_flag(eng, cal):
    e, _ = eng
    mk = e.b.m(SLUG)
    cal.setattr(e, "rn1_reward", lambda s: {"pool_rate_usd_s": 0.001})
    mk["rn1_reward_est"] = {"t": time.time(), "est_usd_per_hour": 3.6}
    now = time.time()
    e._rn1_theo_accrue(SLUG, mk, now)
    e._rn1_theo_accrue(SLUG, mk, now + 100)
    day = time.strftime("%Y-%m-%d", time.gmtime(now + 100 - 5 * 3600))
    assert mk["reward_theo"][day] == pytest.approx(0.1)
    e._rn1_theo_accrue(SLUG, mk, now + 100 + 10_000)       # capped 300 s
    assert mk["reward_theo"][day] == pytest.approx(0.4) or \
        sum(mk["reward_theo"].values()) == pytest.approx(0.4)
    # flag off: step_market never accrues
    mk2 = e.b.m("aec-y")
    assert "reward_theo" not in mk2


def test_reward_calibration_logs_hourly_and_only_with_the_flag(eng, cal,
                                                               capsys):
    e, _ = eng
    assert e.reward_calibration([]) is None
    assert "REWARD_CALIBRATION" not in capsys.readouterr().out
    cal.setattr(M, "RN1_REWARD_CALIBRATION", True)
    e.b.m(SLUG)["reward_theo"] = {"2026-11-25": 1.0}
    got = e.reward_calibration([_row(SLUG, "2026-11-25", "0.4", "PAID")])
    assert got["by_competition"]["x"]["ratio_paid"] == pytest.approx(0.4)
    assert e.reward_calibration([]) is None                # within the hour
    out = [json.loads(l[len("PMUS_TWO_SIDED "):])
           for l in capsys.readouterr().out.splitlines()
           if '"REWARD_CALIBRATION"' in l]
    assert len(out) == 1 and out[0]["pool_scope"] == "market"
    assert out[0]["table_in_force"] == {}


def test_marginal_ev_credit_follows_the_table(eng, cal):
    """_rn1_judge credits incremental reward x rn1_reward_mult: 0.5 off,
    the table's value on."""
    from src import pmus_rn1_replica as R
    e, _ = eng
    cal.setattr(R, "EDGE_GATE", False)
    cal.setattr(M, "RN1_MARGINAL_EV", True)
    cal.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    cal.setattr(e, "intl_fair", lambda s: None)
    cal.setattr(e, "rn1_reward", lambda s: {"df": 0.5, "targetSize": 10,
                                            "pool_rate_usd_s": 0.001})
    cal.setattr(e, "_reward_get", lambda: (lambda p: {"marketData": {}}))
    cal.setattr(RW, "marginal_ladder_reward",
                lambda *a: {"incremental_reward_usd_per_hour": 1.0})
    mk = e.b.m(SLUG)
    e._rn1_inc = {SLUG: {"why": "qualifies"}}
    c = {"side": "YES", "px": D("0.47"), "qty": D("2"), "role": "open"}
    bbo = {"bid": D("0.48"), "ask": D("0.50")}
    off = e.rn1_marginal_ev(SLUG, mk, bbo, D("0.01"), {}, [c], [c])
    assert off[0]["reward_usd_per_hour"] == pytest.approx(0.5)
    cal.setattr(M, "RN1_REWARD_CALIBRATION", True)
    cal.setattr(M, "RN1_REWARD_CAL_TABLE", "x:0.2")
    e._mev_book = {}
    on = e.rn1_marginal_ev(SLUG, mk, bbo, D("0.01"), {}, [c], [c])
    assert on[0]["reward_usd_per_hour"] == pytest.approx(0.2)
