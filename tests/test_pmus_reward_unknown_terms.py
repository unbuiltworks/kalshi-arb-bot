"""A liquidity time period carrying a field the rewards module does not
understand (the CFTC filing PMX-2026-70 describes optional executed-notional
reward caps and minimum-volume terms) is not credited as payable."""
import time

from src import pmus_sports_rewards as R

TP = {"programId": "atp_wta_moneyline_live_20261018",
      "programType": "liquidityProgram", "start": "2026-10-18T18:00:00Z",
      "rewardPool": 184, "status": "active", "discountFactor": 0.3,
      "targetSize": 19000, "period": "live",
      "createdAt": "2026-10-18T17:10:17Z"}
SLUG = "aec-atp-lucsil-lucamb-2026-11-27"


def _prog(tp):
    pm = R.build_program_map({SLUG: {
        "marketSlug": SLUG, "timePeriods": [tp],
        "instrumentState": "INSTRUMENT_STATE_OPEN",
        "eventStartTime": "2026-11-27 18:00:00+00"}})
    return pm[SLUG]


def test_known_payload_pays_and_carries_start():
    p = R.paying_period(_prog(dict(TP)), time.time(), event_live=True)
    assert p is not None and p["programId"] == TP["programId"]
    assert p["start"] == TP["start"]
    assert _prog(dict(TP))["periods"][0]["unknown_terms"] == []


def test_cap_or_minimum_volume_field_fails_closed():
    for extra in ({"maxRewardNotional": 500}, {"minVolume": 100},
                  {"rewardCap": None}, {"somethingNew": 1}):
        prog = _prog({**TP, **extra})
        assert prog["periods"][0]["unknown_terms"] == sorted(extra)
        assert R.paying_period(prog, time.time(), event_live=True) is None


def test_maxspread_is_understood():
    prog = _prog({**TP, "maxSpread": 0.03})
    assert R.paying_period(prog, time.time(), event_live=True) is not None


def test_recorded_and_current_payloads_carry_only_known_fields():
    import gzip
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "data" / "pmus"
    seen = set()
    old = json.load(gzip.open(root / "sports_incentives_2026-09-10" /
                              "live_sports_programs.json.gz"))
    for v in old.values():
        for row in (v.get("found") or {}).values() if isinstance(
                v, dict) else ():
            for tp in row.get("timePeriods") or ():
                seen |= set(tp)
    cur = root / "incentives_2026-11-27" / "active_programs.json.gz"
    if cur.exists():
        for row in json.load(gzip.open(cur, "rt"))["programs"]:
            for tp in row.get("timePeriods") or ():
                seen |= set(tp)
    assert seen and seen <= R.KNOWN_PERIOD_KEYS
