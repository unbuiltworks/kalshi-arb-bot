"""Publish the owner's approval for the ONE window and the approved test configuration.
usage: rec_apply.py <out.json> [--dry]     (--dry: every check, a simulated clock, NO write)"""
import sys, json, datetime as dt, urllib.request
from decimal import Decimal as D
from bal_common import *
from src import l5_launch as L, l5_campaign_approval as CA
DRY = "--dry" in sys.argv
PREV = json.load(open("prev_armed.json"))
PREV_EXP_TS = dt.datetime.fromisoformat(PREV["L5_LIVE_AUTH_NOT_AFTER"]).timestamp()
c = client(None if DRY else ARM_NAMES)
real_now = dt.datetime.now(dt.timezone.utc)
now = (A - dt.timedelta(minutes=18)) if DRY else real_now
lead = (A - now).total_seconds()
print("now", real_now.isoformat()[:19], "clock used", now.isoformat()[:19], "lead_s", round(lead), "DRY" if DRY else "LIVE WRITE")
if lead < 420: print("STOP: too close to the window to arm safely"); sys.exit(3)
if lead > 1500: print("STOP: too early"); sys.exit(3)
assert L.slug_for_window(A) == SLUG
doc = L.build_document(window_start=A, now=now, max_windows=NWIN, max_total_exposure_usd="3.00", max_daily_loss_usd="40.00", nonce=NONCE)
doc["window_starts"] = [s.strftime("%Y-%m-%dT%H:%M:%SZ") for s in STARTS]
doc["expires_utc"] = (STARTS[-1] + dt.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
line = L.document_line(doc); digest = L.document_digest(doc)
class _S:
    def get(self, key): return line.encode()
for when in (now, A + dt.timedelta(seconds=5), STARTS[-1] + dt.timedelta(minutes=14)):
    st = CA.ApprovalGate(_S(), max_age_s=CA.MAX_CAMPAIGN_S, now_fn=lambda: when).state()
    assert st.get("approved") is True, (when, st.get("why"))
    sc = st["scope"]
    assert int(sc["max_windows"]) == NWIN and str(sc["max_total_exposure_usd"]) == "3.00" and D(str(sc["max_daily_loss_usd"])) == D("40"), sc
    for s in SLUGS: assert CA.opening_refusal(sc, s) is None, s
    for other in (L.slug_for_window(A - dt.timedelta(minutes=15)), L.slug_for_window(STARTS[-1] + dt.timedelta(minutes=15)), L.slug_for_window(A + dt.timedelta(hours=13))):
        assert (CA.opening_refusal(sc, other) or {}).get("reason") == "WINDOW_NOT_IN_AUTHORIZED_SET", other
st = CA.ApprovalGate(_S(), max_age_s=CA.MAX_CAMPAIGN_S, now_fn=lambda: STARTS[-1] + dt.timedelta(minutes=31)).state()
assert st.get("approved") is not True, "the approval must be dead 31 minutes after the window starts"
print("document", line); print("digest", digest, "expires", doc["expires_utc"])
req = urllib.request.Request("https://gateway.polymarket.us/v1/market/slug/" + SLUG, headers={"Accept": "application/json", "User-Agent": "research-client"})
vm = json.loads(urllib.request.urlopen(req, timeout=20).read().decode()); vm = vm.get("market") or vm
print("venue", {k: vm.get(k) for k in ("slug", "active", "gameStartTime", "feeCoefficient")})
dep = c.latest_deployment(); hb = c.heartbeat() or {}; cur = c.variables()
sg = hb.get("submission_gate") or {}; op = hb.get("order_progress") or {}
rc = c.runtime_config() or {}; rrow = {r.get("name"): r for r in rc.get("rows") or []}
wd = watchdog()
staged = c._gql('query($e:String!){environmentStagedChanges(environmentId:$e){id status patch}}', {"e": E})["environmentStagedChanges"]
_rr = [r for r in c.logs(deployment_id=dep.get("id"), filter="L5_LIVE_RECONCILE", limit=5) if str(r.get("message")).startswith("L5_LIVE_RECONCILE")]
RECONCILED = bool(_rr) and json.loads(str(_rr[-1]["message"]).split(" ", 1)[1]).get("reconciled") is True
pre = {"sha": dep.get("commitHash") == SHA, "dep_ok": dep.get("status") == "SUCCESS", "armed_false": sg.get("armed") is False,
       "watchdog_latch_clear": sg.get("halt_latch") == "CLEAR", "no_live_orders": op.get("live_orders") == 0,
       "no_parked_orders": op.get("parked_orders") == 0, "nothing_pending_terminal": not (hb.get("pending_terminal") or []),
       "nothing_quarantined": not (hb.get("quarantined") or []),
       "accounting_resolved": (hb.get("unresolved_accounting") or {}).get("count") == 0,
       "exposure_zero_incl_resting": str((hb.get("account_exposure") or {}).get("account_total_worst_case_usd")) == "0",
       "recorder_durable": L._get(hb, "fill_calibration.readback.readable") is True,
       "residual_cap": cur.get("PMUS_RESIDUAL_CAP_K") == "2",   # owner 16:07Z: K=2
       # starting state = the 19:30Z batch as written (approval expired at 20:45Z); only approval/market/id/halt change now
       "state_is_prev_batch": all((cur.get(k) or "") == (PREV.get(k) or "") for k in [k for k in TEST_ON if k != "L5_TS_ORDER_TTL_S"] + [k for k in TIGHTEN if k not in ("L5_DAILY_REALIZED_LOSS_HALT_USD", "L5_MAX_CUMULATIVE_LOSS_USD")]),
       # runtime refuses CAMPAIGN_LIMITS_WIDER_THAN_APPROVED unless running halt <= approved daily loss (code cap 5)
       "daily_halt_written_fits_approval": D(TIGHTEN["L5_DAILY_REALIZED_LOSS_HALT_USD"]) <= D(doc["max_daily_loss_usd"]),
       "opening_leg_one_contract": cur.get("L5_TS_OPENING_LEG_MAX_CONTRACTS") == "1",
       "ceilings": cur.get("L5_TS_MAX_PAIR_COST") == "0.99" and cur.get("L5_TS_COMPLETION_MAX_PAIR") == "0.99" and not cur.get("L5_COMPLETION_PAIR_CEILING"),
       "controller_shadow_no_profile": cur.get("L5_PORTFOLIO_CONTROLLER") == "SHADOW" and not cur.get("L5_PORTFOLIO_PROFILE") and not cur.get("L5_PORTFOLIO_FAVORITE"),
       "first_opening_priced_off": not cur.get("L5_TS_FIRST_OPENING_PRICED"),
       "no_correction_approved": not cur.get("L5_COST_RESTATEMENT_APPROVED"),
       "nothing_staged": not ((staged or {}).get("patch")),
       "reconciled": RECONCILED,
       "owner": cur.get("L5_WINDOW_STRATEGY") == "TWO_SIDED", "fidelity_on_before": cur.get("L5_FIDELITY_MODE") == "ON" and cur.get("L5_ACC_MAX_ROUNDS") == "48",
       "forced_side_off": not cur.get("L5_PROVISIONAL_FORCED_SIDE"),
       "day_room": (hb.get("day_realized") or {}).get("would_halt") is False,
       "day_room_under_written_halt": D(TIGHTEN["L5_DAILY_REALIZED_LOSS_HALT_USD"]) + D(str((hb.get("day_realized") or {}).get("utc_day_realized_usd"))) > D(BUDGET),
       "lifetime_limit_set": D(cur.get("L5_MAX_CUMULATIVE_LOSS_USD") or "0") >= D("25.00"),
       "campaign_fits_lifetime_room": (D(TIGHTEN["L5_MAX_CUMULATIVE_LOSS_USD"]) + D(str((hb.get("day_realized") or {}).get("all_time_realized_usd"))) - D(BUDGET)) > D("1.00"),
       "venue_lists_the_market": vm.get("slug") == SLUG and str(vm.get("gameStartTime")) == A.strftime("%Y-%m-%dT%H:%M:%SZ"),
       "watchdog_fresh": wd["age_s"] < 90 and wd["cancel_enabled"] is True and not wd["read_error"] and wd["open_orders"] in (0, [], None) and not wd["emergency_cancel"]}
print("PRE", hb.get("utc"), json.dumps(pre)); print("WATCHDOG", json.dumps(wd))
print("runtime max_live_orders row", json.dumps(rrow.get("max_live_orders"))[:200], "| runtime PASS", rc.get("PASS"), "failed", rc.get("failed"))
print("day", json.dumps({k: (hb.get("day_realized") or {}).get(k) for k in ("utc_day", "utc_day_realized_usd", "all_time_realized_usd", "would_halt")}), "cash", (hb.get("available_cash") or {}).get("usd"))
bad = [k for k, v in pre.items() if not v]
w = dict({"L5_CAMPAIGN_APPROVAL": line, "L5_TS_MARKET": ",".join(SLUGS), "L5_TS_EXPERIMENT_ID": doc["nonce"],
          "L5_LIVE_AUTH_NOT_AFTER": doc["expires_utc"].replace("Z", "+00:00"), "L5_LIVE_AUTH_MAX_WINDOWS": str(NWIN),
          "L5_LIVE_PROBE_MARKET": "", "L5_LIVE_HALT": "", "L5_SHADOW_ONLY": "0"}, **TEST_ON, **TIGHTEN)
assert set(w) == set(ARM_NAMES)
json.dump(dict(w, _digest=digest), open(sys.argv[1], "w"))
print("WOULD WRITE", json.dumps({k: (v if k != "L5_CAMPAIGN_APPROVAL" else "<document>") for k, v in sorted(w.items())}))
if bad: print("STOP: a precondition is unmet:", bad); sys.exit(4)
if DRY: print("DRY RUN: every precondition met; nothing written"); sys.exit(0)
print(c.set_variables(w)["written"])
try:
    print("DEPLOYED", json.dumps(c.wait_for_deployment(expect_sha=SHA, not_before=str(dep.get("createdAt")), timeout_s=420), default=str))
except Exception as e:
    print("DEPLOY WAIT ERR", e, json.dumps(c.latest_deployment(), default=str))
