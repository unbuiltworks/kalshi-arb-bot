"""ONE window, owner-instructed 2026-11-12 (14:15Z then "turn on recycling and relax the floor for the next window"): cpc-btc-updown-15m-2026-11-12-1545z on
afbaf7511, maker-only, taker off, pair/completion ceiling 0.99 (owner), inventory recovery + dual replenish ON (owner), requote-keeps-round,
reducing-replace fallback, recycling OFF, two live orders, one round at a time, additional loss $1.00, every capital limit unchanged. No other window."""
import os, sys, json, datetime as dt
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import l5_railway as RW
P = "50fb7357-e2bc-4d04-a49e-71eb5d73b0d1"; E = "f3642d27-26dc-4e21-9170-bc76290c076f"
SVC = "87f1834e-530e-4fc4-a310-91c8d3add196"; WD = "fb038b8c-c7c1-4599-b06a-54f72bad544f"
SHA = "afbaf75110e8ef727963e085523825299b172eec"; BUDGET = "1.00"
A = dt.datetime(2026, 11, 12, 15, 45, 0, tzinfo=dt.timezone.utc)
SLUG = "cpc-btc-updown-15m-2026-11-12-1545z"; SLUGS = [SLUG]
NONCE = "owner-20261112-1545z-afbaf751-depth1"
#: owner 2026-11-12 14:17Z: "turn on recycling and relax the floor for the next window"
TEST_ON = {"L5_TS_REQUOTE_KEEPS_ROUND": "ON", "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE": "ON",
           "L5_CASH_POLICY": "RECYCLE_ON_COMPLETE", "L5_TS_OWN_EVIDENCE_GATE": "WAIVED_BY_OWNER",
           "L5_REPLENISH_MAX_ROUNDS": "12",
           # 14:45Z showed depth 0 fills fast but the reopening rule (min edge $0.01/contract) refuses every
           # further round at d=0; depth 1 keeps it aggressive AND lets rounds reopen (owner: keep adjusting)
           "L5_TS_DEPTH_TICKS": "1",
           # owner 14:57Z "MORE PAIRS. get aggressive. move constantly": opening bids re-priced every 20 s (was 120 s)
           "L5_TS_ORDER_TTL_S": "20"}
TIGHTEN = {"L5_MAX_LIVE_ORDERS": "4", "L5_TS_CONCURRENT_ROUNDS": "2", "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": BUDGET,
           "L5_LIVE_AUTH_MAX_OPEN": "16", "L5_LIVE_AUTH_MAX_COMPLETE": "32", "L5_LIVE_AUTH_MAX_REPLACE": "60", "L5_LIVE_AUTH_MAX_SUBMISSIONS": "120"}
ARM_NAMES = ("L5_CAMPAIGN_APPROVAL", "L5_TS_MARKET", "L5_TS_EXPERIMENT_ID", "L5_LIVE_AUTH_NOT_AFTER", "L5_LIVE_AUTH_MAX_WINDOWS",
             "L5_LIVE_PROBE_MARKET", "L5_LIVE_HALT", "L5_SHADOW_ONLY") + tuple(TEST_ON) + tuple(TIGHTEN)
#: exactly the values in force before the window (read 2026-11-12 13:45Z); owner settings 0.99/recovery/replenish stay
RESTORE = {"L5_LIVE_HALT": "1", "L5_SHADOW_ONLY": "1", "L5_LIVE_AUTH_MAX_WINDOWS": "1",
           "L5_MAX_LIVE_ORDERS": "4", "L5_TS_CONCURRENT_ROUNDS": "2", "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": "10.00",
           "L5_LIVE_AUTH_MAX_OPEN": "140", "L5_LIVE_AUTH_MAX_COMPLETE": "300", "L5_LIVE_AUTH_MAX_REPLACE": "60", "L5_LIVE_AUTH_MAX_SUBMISSIONS": "500",
           "L5_TS_REQUOTE_KEEPS_ROUND": "", "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE": "",
           "L5_CASH_POLICY": "NO_RECYCLING", "L5_TS_OWN_EVIDENCE_GATE": "", "L5_REPLENISH_MAX_ROUNDS": "", "L5_TS_DEPTH_TICKS": "", "L5_TS_ORDER_TTL_S": ""}
def client(write=None):
    if write: return RW.RailwayClient(project_id=P, environment_id=E, service_id=SVC, allow_writes=True, writable=tuple(write))
    return RW.RailwayClient(project_id=P, environment_id=E, service_id=SVC)
def watchdog():
    w = RW.RailwayClient(project_id=P, environment_id=E, service_id=WD)
    rows = [r for r in w.logs(limit=6) if str(r.get("message")).startswith("L5_WATCHDOG_SWEEP")]
    j = json.loads(str(rows[-1].get("message")).split(" ", 1)[1])
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(j["as_of_utc"])).total_seconds()
    return {"as_of_utc": j.get("as_of_utc"), "age_s": round(age, 1), "open_orders": j.get("open_orders"), "cancel_enabled": j.get("cancel_enabled"),
            "emergency_cancel": j.get("emergency_cancel"), "read_error": j.get("read_error"), "halted": j.get("halted")}
