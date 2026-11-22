"""Watchdog side: the REAL watchdog code at its deployed commit, real HTTP to
the sports registry, a fake venue transport and a file halt store."""
import sys, json, time, pathlib, tempfile, datetime as dt
from src import l5_watchdog as W, l5_halt_gate as HG
url, ctl = sys.argv[1], pathlib.Path(sys.argv[2])
now = lambda: dt.datetime.now(dt.timezone.utc)
class Venue:
    name = "FAKE_VENUE"
    def __init__(s):
        old = (now() - dt.timedelta(seconds=900)).isoformat()
        s.rows = {i: {"id": i, "marketSlug": "aec-x", "createTime": old,
                      "manualOrderIndicator": W.AUTOMATIC, "state": "ORDER_STATE_OPEN",
                      "leavesQuantity": "2", "cumQuantity": "0"} for i in ("o1", "o2")}
        # someone else's automated order on the shared account, also old
        s.rows["other-desk-1"] = dict(s.rows["o1"], id="other-desk-1", marketSlug="aec-y")
        s.hidden = {"other-desk-1"}; s.cancels = []
    def open_orders(s):
        return [dict(r) for k, r in s.rows.items() if k not in s.hidden and r["leavesQuantity"] != "0"]
    def order(s, i): return dict(s.rows[i])
    def cancel(s, i, slug):
        s.cancels.append((i, slug)); s.rows[i].update(state="ORDER_STATE_CANCELED", leavesQuantity="0")
v = Venue()
wd = W.Watchdog(v, W.asserter_for(HG.FileStore(tempfile.mkdtemp())), max_order_age_s=300,
                cancel_enabled=True, oracle=W.OwnershipOracle(url), managed_lanes=[url])
def show(tag):
    o = wd.sweep()
    print(json.dumps({"phase": tag, "open": o["open_orders"],
        "exempt": o.get("managed_lane_exempt"), "overdue": [x["venue_order_id"] for x in o["overdue"]],
        "halt": (o.get("halted") or {}).get("reason") if isinstance(o.get("halted"), dict) else o.get("halted"),
        "emergency": o.get("emergency_cancel"), "claimed_ids": o.get("claimed_ids"),
        "cancelled": [x["venue_order_id"] for x in o["cancelled"]],
        "not_ours": [(x["venue_order_id"], x["why"]) for x in o["not_ours"]],
        "cancel_skipped": o.get("cancel_skipped"), "claim_refused": o.get("claim_refused"),
        "lanes": o.get("managed_lanes")}, default=str), flush=True)
    return o
show("1 loop alive")
time.sleep(2); show("1b loop alive")
(ctl / "stall").write_text("x")
wait = float(sys.argv[3])
time.sleep(wait); v.hidden.clear()
show("2 loop wedged %.0fs" % wait)
show("3 next sweep")
print("VENUE_CANCELS", v.cancels, "REMAINING_OPEN", [r["id"] for r in v.open_orders()], flush=True)
