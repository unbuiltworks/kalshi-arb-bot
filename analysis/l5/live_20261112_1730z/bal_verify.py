"""After the arming write: is the deployment exactly what was authorized? Read-only.
usage: bal_verify.py <dry.json or armed.json>"""
import sys, json
from bal_common import *
DIG = json.load(open(sys.argv[1])).get("_digest")
c = client()
d = c.latest_deployment(); D = d["id"]; print("deployment", D, d["status"], d["commitHash"], "SHA_OK", d["commitHash"] == SHA)
hb = c.heartbeat(deployment_id=D) or {}
sg = hb.get("submission_gate") or {}; op = hb.get("order_progress") or {}; cam = hb.get("campaign") or {}
print("HB", hb.get("utc"), hb.get("market"))
print(" gate", {k: sg.get(k) for k in ("blocked","reason","operator_halt","shadow_lock","halt_latch","owner_arm_token","armed")})
print(" campaign", {k: cam.get(k) for k in ("approval_state","permits_this_window","windows_spent","windows_remaining","max_windows","exact_market_binding","blocks_advancement","reason")}, "digest_ok", cam.get("document_sha256") == DIG)
print(" live", op.get("live_orders"), "parked", op.get("parked_orders"), "pending", hb.get("pending_terminal"), "quar", hb.get("quarantined"), "unresolved", (hb.get("unresolved_accounting") or {}).get("count"), "exposure", (hb.get("account_exposure") or {}).get("account_total_worst_case_usd"))
ap = hb.get("authorization_preflight") or {}
print(" preflight", ap.get("evaluated_market"), "fail", ap.get("fail"), "unknown", ap.get("unknown"), ap.get("fail_reasons"), "awaiting", ap.get("awaiting_authorized_window"))
print(" limits", json.dumps(hb.get("limits"), default=str))
dr = hb.get("day_realized") or {}
print(" day", dr.get("utc_day"), dr.get("utc_day_realized_usd"), "halt_usd", dr.get("halt_usd"), "would_halt", dr.get("would_halt"), "| realized", dr.get("all_time_realized_usd"), "| cash", (hb.get("available_cash") or {}).get("usd"))
rc = c.runtime_config(deployment_id=D) or {}
print(" runtime PASS", rc.get("PASS"), "failed", rc.get("failed"))
for r in rc.get("rows") or []:
    if r.get("name") in ("aggregate_exposure_usd","aggregate_exposure_in_force_usd","daily_realized_loss_halt_usd","max_windows_authorization","max_live_orders","maker_pair_ceiling","window_strategy","maker_only_forbids_defensive_crossing","series_allowlist"):
        print("    %-40s eff=%s req=%s ok=%s" % (r.get("name"), r.get("effective"), r.get("required"), r.get("ok")))
rows = c.logs(deployment_id=D, limit=400)
for r in rows:
    m = str(r.get("message") or "")
    if m.startswith("L5_AUTHORIZATION_PREFLIGHT"):
        p = json.loads(m.split(" ",1)[1]); print(" STARTUP PREFLIGHT ok", p.get("ok"), "fail", p.get("fail"), "unknown", p.get("unknown"), "not_evaluated", p.get("not_evaluated"))
        for row in p.get("rows") or []:
            if row.get("verdict") not in ("PASS", "NOT_EVALUATED") or row.get("check") in ("window_budget_env", "market_series", "campaign_approval", "absolute_deadline"):
                print("     %-24s %s %s" % (row.get("check"), row.get("verdict"), json.dumps({k: v for k, v in row.items() if k not in ("check","verdict","labels","note","blocks")}, default=str)[:260]))
bad = [str(r.get("message"))[:240] for r in rows if any(x in str(r.get("message")) for x in ("Traceback", "L5_LIVE_ALARM"))]
print(" alarms/tracebacks:", bad or "none")
rr = [r for r in rows if str(r.get("message")).startswith("L5_LIVE_RECONCILE")]
if rr:
    p = json.loads(str(rr[-1]["message"]).split(" ",1)[1]); print(" RECONCILE", str(rr[-1]["timestamp"])[11:19], "reconciled", p.get("reconciled"))
pd = [r for r in rows if str(r.get("message")).startswith("L5_PORTFOLIO_DECISION ")]
if pd:
    p = json.loads(str(pd[-1]["message"]).split(" ",1)[1]); print(" PORTFOLIO", str(pd[-1]["timestamp"])[11:19], "mode", p.get("mode"), "profile", p.get("profile"), "belief", (p.get("belief") or {}).get("source"), "quotes", [(q["side"], q["price"], q["role"]) for q in p.get("quotes") or []], "skipped", [(s["side"], s["reason"]) for s in p.get("skipped") or []])
v = c.variables()
print(" vars", {k: v.get(k) for k in ("L5_LIVE_HALT","L5_SHADOW_ONLY","L5_LIVE_AUTH_MAX_WINDOWS","L5_LIVE_PROBE_MARKET","L5_TS_MARKET","L5_TS_EXPERIMENT_ID","L5_LIVE_AUTH_NOT_AFTER","L5_TS_COMPLETION_CROSS","L5_TAKER_COMPLETION_POLICY","L5_MAX_TOTAL_EXPOSURE_USD","L5_MAX_CASH_DEPLOYED_USD","L5_DAILY_REALIZED_LOSS_HALT_USD","L5_TS_ROUND_ALLOCATIONS","L5_TS_CONCURRENT_ROUNDS","L5_MAX_LIVE_ORDERS","L5_PORTFOLIO_CONTROLLER","L5_PORTFOLIO_PROFILE","L5_TS_OWN_EVIDENCE_GATE","L5_BTC_REFERENCE")})
print(" watchdog", json.dumps(watchdog()))
