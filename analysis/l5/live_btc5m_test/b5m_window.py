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

def restore():
    vals = {"L5_SHADOW_ONLY": "1", "L5_LIVE_HALT": "1", **{k: "" for k in WINDOW_VARS}}
    print(b5(write=vals).set_variables(vals)); return 0

def arm_campaign(slug):
    cap, why = campaign_cap(slug); print(json.dumps({"campaign_cap": str(cap), "why": why}))
    if cap is None:
        print("NOT ARMED (campaign)"); return 1
    return arm(slug, cap)

if __name__ == "__main__":
    if sys.argv[1] == "arm-campaign": sys.exit(arm_campaign(sys.argv[2]))
    if sys.argv[1] == "campaign-cap": print(campaign_cap(sys.argv[2])); sys.exit(0)
    sys.exit(arm(sys.argv[2]) if sys.argv[1] == "arm" else restore() if sys.argv[1] == "restore" else (print(json.dumps(checks(sys.argv[2]))) or 0))
