"""Sports bot side of the drill: the REAL engine + REAL registry server from
the candidate commit, a fake venue. Beats like the loop until `stall` file
appears, then stops beating (a wedged loop; registry thread keeps serving)."""
import os, sys, time, pathlib, tempfile
from decimal import Decimal as D
port, ctl = int(sys.argv[1]), pathlib.Path(sys.argv[2])
os.environ["PMUS_TWO_SIDED_LOOP_STALL_S"] = sys.argv[3]
mode = sys.argv[4]                      # "new" | "frozen" (previous design)
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue
R.ON = True; M.TOTAL_CAP = D("20"); os.environ[M.ENABLED_ENV] = "YES"
os.environ.pop(M.SUBMISSION_LOCK_ENV, None)
v = FakeVenue(); v.bid, v.ask = D("0.48"), D("0.50")
e = M.Engine(v, M.Book(pathlib.Path(tempfile.mkdtemp()) / "s.json"))
e.b.m("aec-x"); e.step_market("aec-x")
M.loop_beat()
M.serve_registry(e.b, port)
print("READY", [r["venue_order_id"] for r in M.registry_doc(e.b)["orders"]], flush=True)
while True:
    if not (ctl / "stall").exists():
        M.loop_beat()
    time.sleep(0.5)
