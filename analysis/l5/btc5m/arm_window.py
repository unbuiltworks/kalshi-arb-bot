"""Build, and locally validate, the exact variable set that arms ONE
BTC5m window of the four-window campaign. Prints JSON; changes nothing.

usage: arm_window.py <window_start_utc ISO> <A|B> <campaign_start_utc ISO>
                     [market=venue_result ...]

  A = the controller as it ran live on 2026-11-20
  B = A + L5_BTC5M_STRADDLE_HEAVY_UNDERDOG=STOP_ADDING
  market=venue_result: every campaign window already settled, with the
  result read from the VENUE'S position row. Required by the campaign
  loss halt before the next window can be armed.

Limits are the ones the owner endorsed and are fixed here: envelope $0.60
(aggregate, per market, daily), window loss $0.20, campaign loss $0.60,
one window per arm, taker not allowed. Clip $0.05, side cap $0.30 and
unmatched cap $0.10 are already the service's variables and are not set
here. The documents are checked with the service's own parsers before
anything is printed.
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
CAMPAIGN_ID = "four-windows-20261121"
ENVELOPE_USD, WINDOW_LOSS, CAMPAIGN_LOSS = "0.60", "0.20", "0.60"


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    start = dt.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    arm = sys.argv[2]
    camp_start = dt.datetime.fromisoformat(sys.argv[3].replace("Z", "+00:00"))
    venue = dict(a.split("=", 1) for a in sys.argv[4:])
    assert arm in ("A", "B") and start.second == 0 and start.minute % 5 == 0
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    end = start + dt.timedelta(minutes=5)
    slug = SERIES + start.strftime("%Y-%m-%d-%H%Mz")
    envelope = {
        "approved": True, "mode": "PERSISTENT", "deployment": DEPLOYMENT,
        "strategies": ["STRADDLE"], "series": [SERIES],
        "taker_allowed": False, "max_aggregate_usd": ENVELOPE_USD,
        "max_per_market_usd": ENVELOPE_USD, "max_daily_loss_usd": ENVELOPE_USD,
        "expires_utc": iso(end + dt.timedelta(minutes=1)),
        "nonce": "env-" + secrets.token_hex(8)}
    campaign = {
        "approved": True, "nonce": "b5m-" + secrets.token_hex(12),
        "series": SERIES, "issued_utc": iso(now),
        "expires_utc": iso(end + dt.timedelta(minutes=5)), "max_windows": 1,
        "max_total_exposure_usd": ENVELOPE_USD,
        "max_daily_loss_usd": ENVELOPE_USD, "window_starts": [iso(start)]}
    probe = {
        "class": PROBE.CLASS, "acknowledgement": PROBE.ACKNOWLEDGEMENT,
        "entry_ev": "UNPROVEN", "market": slug,
        "window_start_utc": iso(start), "expires_utc": iso(end),
        "issued_utc": iso(now), "max_window_loss_usd": WINDOW_LOSS,
        "nonce": "probe-" + secrets.token_hex(8)}
    halt = {
        "campaign_id": CAMPAIGN_ID, "started_utc": iso(camp_start),
        "issued_utc": iso(now),
        "expires_utc": iso(end + dt.timedelta(minutes=10)),
        "max_loss_usd": CAMPAIGN_LOSS, "venue_results": venue}
    v = {
        "L5_DEPLOYMENT_ID": DEPLOYMENT,
        "L5_BTC5M_MARKET": slug, "L5_BTC5M_NOT_AFTER": iso(end),
        "L5_PERSISTENT_ENVELOPE": json.dumps(envelope),
        "L5_CAMPAIGN_APPROVAL_BTC5M": json.dumps(campaign),
        "L5_BTC5M_BEHAVIOUR_PROBE": json.dumps(probe),
        "L5_BTC5M_CAMPAIGN_LOSS_HALT": json.dumps(halt),
        "L5_BTC5M_STRADDLE_HEAVY_UNDERDOG": (
            "STOP_ADDING" if arm == "B" else ""),
        "L5_BTC5M_LIVE": "ARMED_BY_OWNER", "L5_SHADOW_ONLY": "0",
        "L5_SUBMIT_PAUSE": "0", "L5_LIVE_HALT": ""}
    disarm = {
        "L5_BTC5M_LIVE": "", "L5_SHADOW_ONLY": "1",
        "L5_BTC5M_BEHAVIOUR_PROBE": "", "L5_CAMPAIGN_APPROVAL_BTC5M": "",
        "L5_BTC5M_CAMPAIGN_LOSS_HALT": "",
        "L5_BTC5M_STRADDLE_HEAVY_UNDERDOG": "",
        "L5_PERSISTENT_ENVELOPE": json.dumps(dict(
            envelope, expires_utc="2026-01-01T00:00:00Z"))}

    # ---- the service's own parsers, as of the armed moment
    checks = {}
    e = PMODE.parse_envelope(v["L5_PERSISTENT_ENVELOPE"],
                             deployment=DEPLOYMENT, now=now)
    checks["envelope"] = e.get("ok") or e.get("reason")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ[PMODE.STATE_PATH_ENV] = os.path.join(tmp, "s.json")
        os.environ[PMODE.ENVELOPE_ENV] = v["L5_PERSISTENT_ENVELOPE"]
        os.environ[PMODE.DEPLOYMENT_ENV] = DEPLOYMENT
        try:
            a = PMODE.enter_armed(
                raw_campaign=v["L5_CAMPAIGN_APPROVAL_BTC5M"],
                first_window_utc=start, not_after_utc=end, now=now)
            checks["campaign"] = a.get("ok") or a.get("reason")
        except TypeError as exc:
            checks["campaign"] = "NOT CHECKED (%s)" % str(exc)[:80]
    d = PROBE.read_document(slug, start + dt.timedelta(seconds=5),
                            {PROBE.DOC_ENV: v["L5_BTC5M_BEHAVIOUR_PROBE"]})
    checks["probe_at_window"] = d.get("ok") if d.get("present") \
        else "ABSENT"
    if d.get("present") and not d.get("ok"):
        checks["probe_at_window"] = d.get("reason")
    h = X.campaign_loss_verdict(halt, {}, start + dt.timedelta(seconds=5))
    checks["campaign_loss_halt_with_empty_ledger"] = (
        h.get("ok") or h.get("reason"))
    print(json.dumps({"now": iso(now), "window": slug, "arm": arm,
                      "local_checks": checks, "set": v,
                      "disarm_after": disarm}, indent=1))


if __name__ == "__main__":
    main()
