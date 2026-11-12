"""ONE owner-authorized BTC5m window on l5-btc5m-live. Usage:
  b5m_window.py arm SLUG      -- pre-checks, then one variable write (redeploys)
  b5m_window.py restore       -- both locks back on, window variables emptied
Touches l5-btc5m-live only. Never writes a secret value: the owner arm
token is copied BY REFERENCE from l5-pmus-live."""
import sys, json, secrets, datetime as dt, urllib.request
from decimal import Decimal as D
sys.path.insert(0, "/home/user/kalshi-arb-bot/analysis/l5/live_20261111_1900z")
from bal_common import *
B5 = "09271601-9e58-4add-8343-fdabede5679d"
SHA = "9de1e1189a4f158a2cacccd8ef596112bab86da5"
CAP = "0.45"
WINDOW_VARS = ("L5_CAMPAIGN_APPROVAL", "L5_BTC5M_LIVE", "L5_BTC5M_MARKET",
               "L5_BTC5M_NOT_AFTER", "PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID", "PMUS_Q1_ARM")
UTC = dt.timezone.utc

def b5(write=None):
    if write:
        return RW.RailwayClient(project_id=P, environment_id=E, service_id=B5, allow_writes=True, writable=tuple(write))
    return RW.RailwayClient(project_id=P, environment_id=E, service_id=B5)

def checks(slug):
    ws = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz").replace(tzinfo=UTC)
    now = dt.datetime.now(UTC); bad = []
    lead = (ws - now).total_seconds()
    if lead < 240: bad.append("WINDOW_TOO_CLOSE:%ds" % lead)
    b = b5(); d = b.latest_deployment(); v = b.variables()
    if d["status"] != "SUCCESS" or d["commitHash"] != SHA: bad.append("BTC5M_DEPLOY:%s/%s" % (d["status"], d["commitHash"][:9]))
    if not (v.get("PMUS_API_KEY_ID") and v.get("PMUS_API_SECRET_KEY")): bad.append("CREDENTIALS_MISSING")
    c = client(); hb = c.heartbeat(deployment_id=c.latest_deployment()["id"]) or {}
    dr = hb.get("day_realized") or {}; cv = c.variables()
    life = D(str(dr.get("all_time_realized_usd"))); day = D(str(dr.get("utc_day_realized_usd")))
    room = min(D(cv["L5_MAX_CUMULATIVE_LOSS_USD"]) + life, D(cv["L5_DAILY_REALIZED_LOSS_HALT_USD"]) + day)
    if room < D(CAP): bad.append("LOSS_ROOM:%s" % room)
    if (hb.get("order_progress") or {}).get("live_orders") not in (0, None) or hb.get("armed"): bad.append("BTC15M_NOT_IDLE")
    if not all(cv.get(k) for k in ("PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID", "PMUS_Q1_ARM")): bad.append("OWNER_ARM_TOKEN_ABSENT")
    w = watchdog()
    if w.get("open_orders") != 0 or w.get("halted") or w.get("read_error") or w.get("age_s", 999) > 120: bad.append("WATCHDOG:%s" % json.dumps(w))
    m = json.loads(urllib.request.urlopen("https://gateway.polymarket.us/v1/market/slug/%s" % slug, timeout=10).read()).get("market") or {}
    t = m.get("assetPriceTerms") or {}
    if m.get("slug") != slug or t.get("windowStart") != ws.strftime("%Y-%m-%dT%H:%M:%SZ") or t.get("indexSymbol") != "BRTI": bad.append("MARKET_IDENTITY")
    return {"ok": not bad, "fail": bad, "room": str(room), "lead_s": round(lead), "window_start": ws.isoformat()}

# ---- overnight campaign (owner approval required before any campaign arm) ----
CAMPAIGN_FILE = "campaign_overnight_20261112.json"
def campaign():
    return json.load(open(CAMPAIGN_FILE))

def campaign_cap(slug):
    """Cap for SLUG under the campaign: min(window cap, room - fee reserve).
    Refuses (None, reason) when the slug is not approved, the campaign is not
    approved, a prior armed window is not venue-settled/reconciled, or the
    room is below the minimum. Never raises a cap."""
    c = campaign()
    if not c.get("owner_approved"): return None, "CAMPAIGN_NOT_APPROVED"
    if slug not in c["windows"]: return None, "SLUG_NOT_IN_CAMPAIGN"
    if slug in c.get("results", {}): return None, "WINDOW_ALREADY_RUN"
    for w in c.get("predecessor_must_settle", ()):
        r = c.get("predecessor_results", {}).get(w)
        if not r or r.get("status") != "SETTLED" or not r.get("flat_and_no_open_orders"):
            return None, "PREDECESSOR_UNRECONCILED:%s" % w
    net = D(0)
    for w in c["windows"]:
        if w == slug: break
        r = c.get("results", {}).get(w)
        if r is None: continue                       # skipped, never armed
        if r.get("status") != "SETTLED" or r.get("settled_net_usd") is None:
            return None, "PRIOR_WINDOW_UNRECONCILED:%s" % w
        net += D(r["settled_net_usd"])
    room = D(c["max_campaign_loss_usd"]) + min(D(0), net)
    cap = min(D(c["max_window_exposure_usd"]), room - D(c["fee_reserve_usd"]))
    if cap < D(c["min_window_cap_usd"]): return None, "CAMPAIGN_ROOM:%s" % room
    return cap.quantize(D("0.01")), "OK"

def arm(slug, cap=None):
    global CAP
    if cap is not None: CAP = str(cap)
    ck = checks(slug); print(json.dumps(ck))
    if not ck["ok"]:
        print("NOT ARMED"); return 1
    ws = dt.datetime.fromisoformat(ck["window_start"]); now = dt.datetime.now(UTC)
    doc = {"approved": True, "nonce": "owner-b5m-%s-%s" % (slug[-5:], secrets.token_hex(8)),
           "series": "cpc-btc-updown-5m-", "issued_utc": now.isoformat(),
           "expires_utc": (ws + dt.timedelta(hours=1)).isoformat(),
           "window_starts": [ws.strftime("%Y-%m-%dT%H:%M:%SZ")], "max_windows": 1,
           "max_total_exposure_usd": CAP, "max_daily_loss_usd": CAP, "max_total_submissions": "20"}
    vals = {"L5_CAMPAIGN_APPROVAL": json.dumps(doc), "L5_BTC5M_LIVE": "ARMED_BY_OWNER",
            "L5_BTC5M_MARKET": slug, "L5_BTC5M_NOT_AFTER": (ws + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "PMUS_Q1_LIVE": "${{l5-pmus-live.PMUS_Q1_LIVE}}", "PMUS_Q1_SESSION_ID": "${{l5-pmus-live.PMUS_Q1_SESSION_ID}}",
            "PMUS_Q1_ARM": "${{l5-pmus-live.PMUS_Q1_ARM}}", "L5_SHADOW_ONLY": "0", "L5_LIVE_HALT": ""}
    before = b5().latest_deployment()
    print(b5(write=vals).set_variables(vals))
    got = b5().wait_for_deployment(expect_sha=SHA, timeout_s=420, poll_s=8, not_before=before["createdAt"])
    print("DEPLOYED", got["id"], got["status"], "nonce", doc["nonce"])
    return 0

TOKEN_REFS = ("PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID", "PMUS_Q1_ARM")

def restore():
    """Both locks on, window variables emptied, and the owner arm-token
    REFERENCES deleted. Writing "" over a ${{...}} reference did not clear it
    (observed 04:40Z 2026-11-12), so the three are removed with
    variableDelete. Values are never printed."""
    vals = {"L5_SHADOW_ONLY": "1", "L5_LIVE_HALT": "1",
            **{k: "" for k in WINDOW_VARS if k not in TOKEN_REFS}}
    print(b5(write=vals).set_variables(vals))
    b = b5()
    for k in TOKEN_REFS:
        r = b._gql("mutation($p:String!,$e:String!,$s:String!,$n:String!){variableDelete(input:{projectId:$p,environmentId:$e,serviceId:$s,name:$n})}",
                   {"p": P, "e": E, "s": B5, "n": k})
        print("deleted", k, bool(r.get("variableDelete")))
    raw = b._gql("query($p:String!,$e:String!,$s:String!){variables(projectId:$p,environmentId:$e,serviceId:$s,unrendered:true)}",
                 {"p": P, "e": E, "s": B5})["variables"]
    left = [k for k in TOKEN_REFS if raw.get(k)]
    print("token refs remaining:", left or "none"); return 0 if not left else 1

def session_report(slug):
    """The L5_BTC5M_SESSION_REPORT for SLUG from l5-btc5m-live's deploy logs
    (searches the recent deployments), or None."""
    b = b5()
    q = b._gql("query($pid:String!,$eid:String!,$sid:String!){deployments(first:6,input:{projectId:$pid,environmentId:$eid,serviceId:$sid}){edges{node{id}}}}",
               {"pid": P, "eid": E, "sid": B5})
    for d in [e["node"] for e in q["deployments"]["edges"]]:
        for r in b.logs(deployment_id=d["id"], filter="L5_BTC5M_SESSION_REPORT", limit=20) or ():
            m = str(r.get("message"))
            if m.startswith("L5_BTC5M_SESSION_REPORT"):
                rep = json.loads(m.split(" ", 1)[1])
                if rep.get("market") == slug:
                    return rep
    return None

def record(slug, predecessor=False):
    """Write SLUG's venue result into the campaign file. SETTLED only when the
    venue settled it, every own order is terminal, fees are fully read, the
    position reconciles and the watchdog sees 0 open orders on the account.
    Anything else is recorded UNRECONCILED (which blocks the next window)."""
    rep = session_report(slug); w = watchdog()
    if rep is None:
        res = {"status": "NO_SESSION_REPORT"}
    else:
        st = rep.get("settlement") or {}; cl = rep.get("close") or {}
        fees = rep.get("venue_fees") or {}; rc = rep.get("reconcile") or {}
        clean = bool(st.get("settled") and cl.get("clean") and fees.get("complete")
                     and rc.get("read") and rc.get("agrees") and rep.get("submit_uncertain") == 0
                     and w.get("open_orders") == 0 and not w.get("read_error"))
        no_trade = rep.get("submitted") == 0 and cl.get("clean") and w.get("open_orders") == 0
        net = rep.get("settled_net_usd")
        if no_trade and net is None: net = "0"
        res = {"status": "SETTLED" if (clean or no_trade) and net is not None else "UNRECONCILED",
               "settled_net_usd": net, "submitted": rep.get("submitted"),
               "filled_orders": rep.get("filled_orders"), "fills_cost_usd": rep.get("fills_cost_usd"),
               "fees_usd": fees.get("total_usd"), "settlement_px": st.get("settlement_px"),
               "transport_armed": rep.get("transport_armed"),
               "flat_and_no_open_orders": bool(cl.get("clean") and w.get("open_orders") == 0)}
    res["watchdog"] = w; res["recorded_utc"] = dt.datetime.now(UTC).isoformat()
    c = campaign(); c["predecessor_results" if predecessor else "results"][slug] = res
    json.dump(c, open(CAMPAIGN_FILE, "w"), indent=1); print(json.dumps(res)); return 0

def deploy_sha(sha):
    """Deploy exactly SHA to l5-btc5m-live. Refuses unless both locks are on
    and no window is configured (disarmed deploy only)."""
    v = b5().variables()
    if v.get("L5_SHADOW_ONLY") != "1" or v.get("L5_LIVE_HALT") != "1" or any(v.get(k) for k in WINDOW_VARS):
        print("REFUSED: not disarmed", {k: v.get(k) for k in ("L5_SHADOW_ONLY", "L5_LIVE_HALT", "L5_BTC5M_MARKET")}); return 1
    b = b5(write=("deploy",)); before = b.latest_deployment()
    r = b._gql("mutation($s:String!,$e:String!,$c:String){serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c)}",
               {"s": B5, "e": E, "c": sha})
    print("deploy requested", r)
    got = b5().wait_for_deployment(expect_sha=sha, timeout_s=600, poll_s=10, not_before=before["createdAt"])
    print("DEPLOYED", got["id"], got["status"], got["commitHash"]); return 0

def verify_disarmed(sha):
    """Read-only: SHA, both locks, no window configured, idle log line,
    watchdog 0 orders, BTC15m disarmed, account loss room."""
    b = b5(); d = b.latest_deployment(); v = b.variables(); bad = []
    if d["status"] != "SUCCESS" or d["commitHash"] != sha: bad.append("DEPLOY:%s/%s" % (d["status"], (d["commitHash"] or "")[:9]))
    if v.get("L5_SHADOW_ONLY") != "1": bad.append("SHADOW_LOCK_OFF")
    if v.get("L5_LIVE_HALT") != "1": bad.append("HALT_OFF")
    if any(v.get(k) for k in WINDOW_VARS): bad.append("WINDOW_VARS_SET")
    if v.get("L5_ACCOUNT_MAX_EXPOSURE_USD") != "0.45": bad.append("ACCOUNT_CAP:%s" % v.get("L5_ACCOUNT_MAX_EXPOSURE_USD"))
    idle = [r for r in b.logs(deployment_id=d["id"], filter="L5_BTC5M", limit=20) if "L5_BTC5M_IDLE" in str(r.get("message"))]
    if not idle: bad.append("NO_IDLE_LINE_YET")
    tb = b.logs(deployment_id=d["id"], filter="Traceback", limit=5)
    if tb: bad.append("TRACEBACK")
    w = watchdog()
    if w.get("open_orders") != 0 or w.get("halted") or w.get("read_error") or w.get("age_s", 999) > 120: bad.append("WATCHDOG")
    c = client(); hb = c.heartbeat(deployment_id=c.latest_deployment()["id"]) or {}
    if hb.get("armed") or (hb.get("order_progress") or {}).get("live_orders") not in (0, None): bad.append("BTC15M_NOT_IDLE")
    out = {"ok": not bad, "fail": bad, "deployment": d["id"], "sha": d["commitHash"], "watchdog": w,
           "idle": str(idle[-1].get("message"))[:200] if idle else None,
           "btc15m_day_realized": hb.get("day_realized")}
    print(json.dumps(out, default=str)); return 0 if not bad else 1

def arm_campaign(slug):
    global SHA
    SHA = campaign()["approved_sha"]   # start SHA, or a verified successor recorded here
    cap, why = campaign_cap(slug); print(json.dumps({"campaign_cap": str(cap), "why": why}))
    if cap is None:
        print("NOT ARMED (campaign)"); return 1
    return arm(slug, cap)

if __name__ == "__main__":
    if sys.argv[1] == "deploy-sha": sys.exit(deploy_sha(sys.argv[2]))
    if sys.argv[1] == "verify-disarmed": sys.exit(verify_disarmed(sys.argv[2]))
    if sys.argv[1] == "record": sys.exit(record(sys.argv[2], predecessor="--predecessor" in sys.argv))
    if sys.argv[1] == "arm-campaign": sys.exit(arm_campaign(sys.argv[2]))
    if sys.argv[1] == "campaign-cap": print(campaign_cap(sys.argv[2])); sys.exit(0)
    sys.exit(arm(sys.argv[2]) if sys.argv[1] == "arm" else restore() if sys.argv[1] == "restore" else (print(json.dumps(checks(sys.argv[2]))) or 0))
