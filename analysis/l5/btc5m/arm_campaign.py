"""Build, and locally validate, the variable set that arms ONE BTC5m
measurement campaign of N consecutive windows. Prints JSON; changes nothing.

usage: arm_campaign.py <first_window_start_utc ISO> <n_windows> <A|B>
                       <campaign_id> [market=venue_result ...]

  A = the controller as it runs today; B = A + heavy-underdog STOP_ADDING
  campaign_id: names the cumulative loss halt's ledger; a NEW id starts the
    $0.60 count at zero from now. market=venue_result is only needed for
    markets settled under the SAME id before this arming.

Fixed here, as the owner approved: envelope $0.60 (aggregate, per market,
daily), window loss $0.20, campaign loss $0.60, taker not allowed, at most
24 windows inside two hours. Clip, side cap and unmatched cap are the
service's own variables and are not set here. Every document is checked
with the service's own parsers before anything is printed.
"""
import datetime as dt
import json
import os
import secrets
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("L5_BTC5M_CONTROLLER", "ON")
from src import l5_behaviour_probe as PROBE          # noqa: E402
from src import l5_btc5m_execution as X              # noqa: E402
from src import l5_persistent_mode as PMODE          # noqa: E402

SERIES = "cpc-btc-updown-5m-"
DEPLOYMENT = "btc5m-campaign-20261121"
ENVELOPE_USD, WINDOW_LOSS = "0.60", "0.20"
#: owner-approved 2026-11-21 for the two-hour test: the campaign stops at
#: 1.50 lost; the lane's daily loss allowance is 2.00. Exposure is unchanged.
CAMPAIGN_LOSS, DAILY_LOSS = "1.50", "2.00"


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    first = dt.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    n = int(sys.argv[2])
    arm, camp_id = sys.argv[3], sys.argv[4]
    venue = dict(a.split("=", 1) for a in sys.argv[5:])
    assert arm in ("A", "B") and first.second == 0 and first.minute % 5 == 0
    assert 1 <= n <= PROBE.MAX_CAMPAIGN_WINDOWS
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    starts = [first + dt.timedelta(minutes=5 * i) for i in range(n)]
    end = starts[-1] + dt.timedelta(minutes=5)
    slugs = [SERIES + t.strftime("%Y-%m-%d-%H%Mz") for t in starts]
    envelope = {
        "approved": True, "mode": "PERSISTENT", "deployment": DEPLOYMENT,
        "strategies": ["STRADDLE"], "series": [SERIES],
        "taker_allowed": False, "max_aggregate_usd": ENVELOPE_USD,
        "max_per_market_usd": ENVELOPE_USD, "max_daily_loss_usd": DAILY_LOSS,
        "expires_utc": iso(end + dt.timedelta(minutes=1)),
        "nonce": "env-" + secrets.token_hex(8)}
    campaign = {
        "approved": True, "nonce": "b5m-" + secrets.token_hex(12),
        "series": SERIES, "issued_utc": iso(now),
        "expires_utc": iso(end + dt.timedelta(minutes=5)), "max_windows": n,
        "max_total_exposure_usd": ENVELOPE_USD,
        "max_daily_loss_usd": DAILY_LOSS,
        "window_starts": [iso(t) for t in starts]}
    probe = {
        "class": PROBE.CAMPAIGN_CLASS,
        "acknowledgement": PROBE.CAMPAIGN_ACKNOWLEDGEMENT,
        "entry_ev": "UNPROVEN", "markets": slugs, "issued_utc": iso(now),
        "expires_utc": iso(end), "max_window_loss_usd": WINDOW_LOSS,
        "max_campaign_loss_usd": CAMPAIGN_LOSS,
        "nonce": "probe-" + secrets.token_hex(8)}
    halt = {
        "campaign_id": camp_id, "started_utc": iso(now),
        "issued_utc": iso(now),
        "expires_utc": iso(end + dt.timedelta(minutes=30)),
        "max_loss_usd": CAMPAIGN_LOSS, "venue_results": venue}
    v = {
        "L5_DEPLOYMENT_ID": DEPLOYMENT,
        "L5_BTC5M_MARKET": slugs[0], "L5_BTC5M_NOT_AFTER": iso(end),
        "L5_PERSISTENT_ENVELOPE": json.dumps(envelope),
        "L5_CAMPAIGN_APPROVAL_BTC5M": json.dumps(campaign),
        "L5_BTC5M_BEHAVIOUR_PROBE": "",
        "L5_BTC5M_BEHAVIOUR_PROBE_CAMPAIGN": json.dumps(probe),
        "L5_BTC5M_CAMPAIGN_LOSS_HALT": json.dumps(halt),
        "L5_BTC5M_STRADDLE_HEAVY_UNDERDOG": (
            "STOP_ADDING" if arm == "B" else ""),
        "L5_BTC5M_LIVE": "ARMED_BY_OWNER", "L5_SHADOW_ONLY": "0",
        "L5_SUBMIT_PAUSE": "0", "L5_LIVE_HALT": ""}
    disarm = {
        "L5_BTC5M_LIVE": "", "L5_SHADOW_ONLY": "1",
        "L5_BTC5M_BEHAVIOUR_PROBE": "",
        "L5_BTC5M_BEHAVIOUR_PROBE_CAMPAIGN": "",
        "L5_CAMPAIGN_APPROVAL_BTC5M": "",
        "L5_BTC5M_CAMPAIGN_LOSS_HALT": "",
        "L5_BTC5M_STRADDLE_HEAVY_UNDERDOG": "",
        "L5_PERSISTENT_ENVELOPE": json.dumps(dict(
            envelope, expires_utc="2026-01-01T00:00:00Z"))}

    checks = {}
    e = PMODE.parse_envelope(v["L5_PERSISTENT_ENVELOPE"],
                             deployment=DEPLOYMENT, now=now)
    checks["envelope"] = e.get("ok") or e.get("reason")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ[PMODE.STATE_PATH_ENV] = os.path.join(tmp, "s.json")
        os.environ[PMODE.ENVELOPE_ENV] = v["L5_PERSISTENT_ENVELOPE"]
        os.environ[PMODE.DEPLOYMENT_ENV] = DEPLOYMENT
        a = PMODE.enter_armed(
            raw_campaign=v["L5_CAMPAIGN_APPROVAL_BTC5M"],
            first_window_utc=first, not_after_utc=end, now=now)
        checks["campaign_approval"] = a.get("ok") or a.get("reason")
    penv = {PROBE.CAMPAIGN_DOC_ENV: v["L5_BTC5M_BEHAVIOUR_PROBE_CAMPAIGN"],
            PROBE.LOSS_HALT_ENV: v["L5_BTC5M_CAMPAIGN_LOSS_HALT"]}
    bad = {}
    for s_, t in zip(slugs, starts):
        d = PROBE.read_campaign_document(s_, t + dt.timedelta(seconds=5),
                                         penv)
        if not d.get("ok"):
            bad[s_] = d.get("reason")
    checks["probe_every_window"] = True if not bad else bad
    after = PROBE.read_campaign_document(
        SERIES + end.strftime("%Y-%m-%d-%H%Mz"),
        end + dt.timedelta(seconds=5), penv)
    checks["probe_refuses_the_window_after_the_last"] = after.get("reason")
    for lab, t in (("first", starts[0]), ("last", starts[-1])):
        h = X.campaign_loss_verdict(halt, {}, t + dt.timedelta(seconds=5))
        checks["campaign_loss_halt_%s_window" % lab] = (
            h.get("ok") or h.get("reason"))
    print(json.dumps({"now": iso(now), "first": slugs[0], "last": slugs[-1],
                      "windows": n, "ends_utc": iso(end), "arm": arm,
                      "local_checks": checks, "set": v,
                      "disarm_after": disarm}, indent=1))


if __name__ == "__main__":
    main()
