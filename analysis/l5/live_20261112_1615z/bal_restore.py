"""Restore both locks and remove the waiver -- ONLY when the book is flat and reconciled.
usage: bal_restore.py [--write]   (without --write: prints GO or HOLD and writes nothing)"""
import sys, json, datetime as dt
from bal_common import *
WRITE = "--write" in sys.argv
c = client(RESTORE.keys() if WRITE else None)
hb = c.heartbeat() or {}
sg = hb.get("submission_gate") or {}; op = hb.get("order_progress") or {}; ua = hb.get("unresolved_accounting") or {}; ae = hb.get("account_exposure") or {}
cam = hb.get("campaign") or {}; dr = hb.get("day_realized") or {}; wd = watchdog()
checks = {"no_live_orders": op.get("live_orders") == 0, "no_parked_orders": op.get("parked_orders") == 0, "nothing_pending": not (hb.get("pending_terminal") or []),
          "nothing_quarantined": not (hb.get("quarantined") or []), "watchdog_sees_no_open_orders": wd["open_orders"] in (0, [], None)}
info = {"armed": sg.get("armed"), "reason": sg.get("reason"), "accounting_unresolved": ua.get("count"), "exposure": str(ae.get("account_total_worst_case_usd")),
        "windows_spent": cam.get("windows_spent"), "submissions_spent": cam.get("submissions_spent"), "all_time_realized": dr.get("all_time_realized_usd"), "day": dr.get("utc_day_realized_usd")}
print("heartbeat", hb.get("utc"), "| market", hb.get("market")); print(" checks", json.dumps(checks)); print(" info", json.dumps(info)); print(" watchdog", json.dumps(wd))
print(" per-market worst case", json.dumps(ae.get("per_market_worst_case_usd")))
go = all(checks.values())
print("VERDICT:", "GO" if go else "HOLD -- orders still at the venue; a redeploy now would restart mid-flight")
if WRITE and go:
    t0 = dt.datetime.now(dt.timezone.utc).isoformat()
    print(c.set_variables(dict(RESTORE))["written"])
    try: print("DEPLOYED", json.dumps(c.wait_for_deployment(expect_sha=SHA, not_before=t0, timeout_s=420), default=str)[:400])
    except Exception as e: print("DEPLOY WAIT ERR", e)
elif WRITE: sys.exit(5)
