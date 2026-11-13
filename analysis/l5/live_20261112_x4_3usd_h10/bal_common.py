"""$3 EXPOSURE, FOUR consecutive windows (18:00-18:45Z), one arm; was: ONE window, owner-instructed 2026-11-12 (14:15Z then "turn on recycling and relax the floor for the next window"): cpc-btc-updown-15m-2026-11-12-1800z on
afbaf7511, maker-only, taker off, pair/completion ceiling 0.99 (owner), inventory recovery + dual replenish ON (owner), requote-keeps-round,
reducing-replace fallback, recycling OFF, two live orders, one round at a time, additional loss $1.00, every capital limit unchanged. No other window."""
import os, sys, json, datetime as dt
sys.path.insert(0, os.environ.get("KIT_SRC", "/home/user/kalshi-arb-bot"))   # KIT_SRC = checkout of the DEPLOYED SHA
from src import l5_railway as RW
P = "50fb7357-e2bc-4d04-a49e-71eb5d73b0d1"; E = "f3642d27-26dc-4e21-9170-bc76290c076f"
SVC = "87f1834e-530e-4fc4-a310-91c8d3add196"; WD = "fb038b8c-c7c1-4599-b06a-54f72bad544f"
SHA = os.environ["KIT_SHA"]   # 64223094a + owner 21:3xZ "Keep $10, raise code cap" (MAX_DAILY_LOSS_USD 5 -> 10)
BUDGET = "3.00"   # owner 19:0xZ: "$3 exposure (Recommended)"
A = dt.datetime.fromisoformat(os.environ["KIT_START"]) if os.environ.get("KIT_START") else dt.datetime(2026, 11, 12, 22, 30, 0, tzinfo=dt.timezone.utc)
STARTS = [A + dt.timedelta(minutes=15*i) for i in range(4)]   # owner 17:4xZ: one arm, consecutive windows (chose "Both")
SLUGS = ["cpc-btc-updown-15m-" + s.strftime("%Y-%m-%d-%H%Mz") for s in STARTS]; SLUG = SLUGS[0]; NWIN = len(SLUGS)
NONCE = "owner-" + A.strftime("%Y%m%d-%H%Mz") + "-x4-3usd-h40f-t120-" + SHA[:8]
#: owner 2026-11-12 14:17Z: "turn on recycling and relax the floor for the next window"
TEST_ON = {"L5_TS_REQUOTE_KEEPS_ROUND": "ON", "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE": "ON",
           "L5_CASH_POLICY": "RECYCLE_ON_COMPLETE", "L5_TS_OWN_EVIDENCE_GATE": "WAIVED_BY_OWNER",
           "L5_REPLENISH_MAX_ROUNDS": "12",
           # 14:45Z showed depth 0 fills fast but the reopening rule (min edge $0.01/contract) refuses every
           # further round at d=0; depth 1 keeps it aggressive AND lets rounds reopen (owner: keep adjusting)
           "L5_TS_DEPTH_TICKS": "2",   # owner 15:20Z: UP and DOWN bids 2 ticks below the bid, simultaneously
           # owner 14:57Z "MORE PAIRS. get aggressive. move constantly": opening bids re-priced every 20 s (was 120 s)
           "L5_TS_ORDER_TTL_S": "120",   # owner 2026-11-13 13:4xZ "TTL 120 only": reprice on touch move (wake <=0.25s, 1 tick), not on a 20 s timer
           # owner 15:17Z "place these pairs simultaneously": three rounds in parallel
           "L5_TS_ROUND_ALLOCATIONS": "3",
           # 15:15Z: the replenish ledger spent its credits on 20 s re-prices (REPLENISH_UNPAID x51) and starved
           # both new rounds and the completion re-place; rounds/allocations alone govern openings
           "L5_DUAL_REPLENISH": "OFF",
           # owner 16:17Z "taker on": bounded taker completion of stranded legs (pair <= 1.00, outlay cap L5_TAKER_MAX_OUTLAY_USD=3)
           "L5_TS_COMPLETION_CROSS": "YES", "L5_TAKER_COMPLETION_POLICY": "YES",
           # owner 16:31Z "deploy 234": the single continuous inventory controller drives this window
           "L5_TS_CONTINUOUS_INVENTORY": "YES",
           # owner 2026-11-13 04:2xZ "Yes, fidelity + 48 slots": L5 trades to ~752 s and quotes both sides at once;
           # 21:00Z ran entry 480 s / complete 600 s (vars), never had both sides resting, ran out of 24 slots at ~600 s. Same dollar limits.
           "L5_FIDELITY_MODE": "ON", "L5_TS_ENTRY_CUTOFF_S": "840", "L5_TS_COMPLETE_CUTOFF_S": "870",
           "L5_ACC_MAX_ROUNDS": "48"}
TIGHTEN = {"L5_MAX_CUMULATIVE_LOSS_USD": "75.00",   # owner 2026-11-13 04:0xZ "$75 (Recommended)" after BTC5m 0035z -28.12 (was 50, owner 11-12)
           "L5_DAILY_REALIZED_LOSS_HALT_USD": "40.00",   # owner 2026-11-13 ~11:55Z "Halt + cap $40, go today" (was 10)
           "L5_MAX_TOTAL_EXPOSURE_USD": "3.00", "L5_CAMPAIGN_BUDGET_USD": "3.00",   # campaign worst-case budget was a legacy 1.00 that refused 13:45/14:00 (CAMPAIGN_WORST_CASE_EXHAUSTED 1>=1.00); aligned to the owner's $3
           "L5_MAX_CASH_DEPLOYED_USD": "2.70", "L5_MAX_WORST_CASE_DEFICIT_USD": "3.00", "L5_MAX_LIVE_ORDERS": "6", "L5_TS_CONCURRENT_ROUNDS": "3", "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": BUDGET,
           "L5_LIVE_AUTH_MAX_OPEN": "96", "L5_LIVE_AUTH_MAX_COMPLETE": "192", "L5_LIVE_AUTH_MAX_REPLACE": "240", "L5_LIVE_AUTH_MAX_SUBMISSIONS": "560"}
ARM_NAMES = ("L5_CAMPAIGN_APPROVAL", "L5_TS_MARKET", "L5_TS_EXPERIMENT_ID", "L5_LIVE_AUTH_NOT_AFTER", "L5_LIVE_AUTH_MAX_WINDOWS",
             "L5_LIVE_PROBE_MARKET", "L5_LIVE_HALT", "L5_SHADOW_ONLY") + tuple(TEST_ON) + tuple(TIGHTEN)
#: exactly the values in force before the window (read 2026-11-12 13:45Z); owner settings 0.99/recovery/replenish stay
RESTORE = {"L5_MAX_TOTAL_EXPOSURE_USD": "1.00", "L5_MAX_CASH_DEPLOYED_USD": "0.90", "L5_MAX_WORST_CASE_DEFICIT_USD": "1.00", "L5_LIVE_HALT": "1", "L5_SHADOW_ONLY": "1", "L5_LIVE_AUTH_MAX_WINDOWS": "1",
           "L5_MAX_LIVE_ORDERS": "4", "L5_TS_CONCURRENT_ROUNDS": "2", "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": "10.00",
           "L5_LIVE_AUTH_MAX_OPEN": "140", "L5_LIVE_AUTH_MAX_COMPLETE": "300", "L5_LIVE_AUTH_MAX_REPLACE": "60", "L5_LIVE_AUTH_MAX_SUBMISSIONS": "500",
           "L5_TS_REQUOTE_KEEPS_ROUND": "", "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE": "",
           "L5_CASH_POLICY": "NO_RECYCLING", "L5_TS_OWN_EVIDENCE_GATE": "", "L5_REPLENISH_MAX_ROUNDS": "", "L5_TS_DEPTH_TICKS": "", "L5_TS_ORDER_TTL_S": "", "L5_TS_ROUND_ALLOCATIONS": "2", "L5_DUAL_REPLENISH": "ON", "L5_TS_COMPLETION_CROSS": "NO", "L5_TAKER_COMPLETION_POLICY": "NO", "L5_TS_CONTINUOUS_INVENTORY": ""}
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
