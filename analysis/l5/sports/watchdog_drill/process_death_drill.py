"""PROCESS-DEATH DRILL, two real processes, no venue.
Bot side: the real sports engine and its real registry server (registry_side.py,
run from a checkout of the sports code) holding two resting orders on a fake
venue. Watchdog side (this process, run from a checkout of the watchdog code):
the real Watchdog and OwnershipOracle over real HTTP, a fake venue transport
that also shows another desk's old order on the shared account.
The bot process is then killed with SIGKILL -- no shutdown handler runs, its
registry is gone -- and the watchdog keeps sweeping.
The orders are <order_age_s> old (default 900, past the watchdog's 300 s age
rule; use a small value to show the gone-executor rule acting before it).
usage: process_death_drill.py <sports_checkout> <port> <remember_s> [order_age_s] [dead_after_s]"""
import datetime as dt, json, os, pathlib, signal, subprocess, sys, tempfile, time
sports, port, remember = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.environ["L5_WATCHDOG_REMEMBER_CLAIMS_S"] = remember
AGE = float(sys.argv[4]) if len(sys.argv) > 4 else 900.0
DEAD = float(sys.argv[5]) if len(sys.argv) > 5 else 30.0
os.environ["L5_WATCHDOG_DEAD_AFTER_S"] = str(DEAD)
sys.path.insert(0, ".")
from src import l5_watchdog as W, l5_halt_gate as HG
here = pathlib.Path(__file__).resolve().parent
ctl = pathlib.Path(tempfile.mkdtemp())
bot = subprocess.Popen([sys.executable, str(here / "registry_side.py"), str(port), str(ctl), "120", "new"],
                       cwd=sports, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                       # the fake venue has no fair-value source, so the bot side runs
                       # without the marginal test: the drill is about the orders it LEAVES
                       env=dict(os.environ, PYTHONPATH=sports, PMUS_RN1_EDGE_GATE="0"))
line = ""
while not line.startswith("READY"):         # the engine logs before it is ready
    line = bot.stdout.readline()
    if not line:
        raise SystemExit("bot side ended before it was ready")
    line = line.strip()
import threading
threading.Thread(target=lambda: [None for _ in bot.stdout], daemon=True).start()
print("BOT", line, "pid", bot.pid, flush=True)
ids = json.loads(line.split(" ", 1)[1].replace("'", '"'))
url = "http://127.0.0.1:%d" % port
now = lambda: dt.datetime.now(dt.timezone.utc)
class Venue:
    name = "FAKE_VENUE"
    def __init__(s):
        old = (now() - dt.timedelta(seconds=AGE)).isoformat()
        s.rows = {i: {"id": i, "marketSlug": "aec-x", "createTime": old,
                      "manualOrderIndicator": W.AUTOMATIC, "state": "ORDER_STATE_OPEN",
                      "leavesQuantity": "2", "cumQuantity": "0"} for i in ids}
        s.rows["other-desk-1"] = dict(s.rows[ids[0]], id="other-desk-1", marketSlug="aec-y")
        s.cancels = []
    def open_orders(s):
        return [dict(r) for r in s.rows.values() if r["leavesQuantity"] != "0"]
    def order(s, i): return dict(s.rows[i])
    def cancel(s, i, slug):
        s.cancels.append((i, slug)); s.rows[i].update(state="ORDER_STATE_CANCELED", leavesQuantity="0")
v = Venue()
wd = W.Watchdog(v, W.asserter_for(HG.FileStore(tempfile.mkdtemp())), max_order_age_s=300,
                cancel_enabled=True, oracle=W.OwnershipOracle(url), managed_lanes=[url])
def show(tag):
    o = wd.sweep()
    print(json.dumps({"phase": tag, "open": o["open_orders"], "exempt": o.get("managed_lane_exempt"),
        "claimed_ids": o.get("claimed_ids"), "cancelled": [x["venue_order_id"] for x in o["cancelled"]],
        "not_ours": [(x["venue_order_id"], x["why"]) for x in o["not_ours"]],
        "cancel_skipped": (o.get("cancel_skipped") or "")[:60] or None,
        "claim_refused": o.get("claim_refused"), "lanes": o.get("managed_lanes"),
        "halt": (o.get("halted") or {}).get("reason") if isinstance(o.get("halted"), dict) else o.get("halted")},
        default=str), flush=True)
time.sleep(1.0)
show("1 bot alive")
os.kill(bot.pid, signal.SIGKILL); bot.wait()
print("BOT KILLED (SIGKILL), exit", bot.returncode, flush=True)
time.sleep(1.0)
show("2 first sweep after death (%.0fs old orders)" % AGE)
if AGE <= 300:
    time.sleep(DEAD + 0.5)
    show("3 after the %.0fs grace" % DEAD)
show("4 next sweep")
print("VENUE_CANCELS", v.cancels, "| STILL OPEN", [r["id"] for r in v.open_orders()], flush=True)
