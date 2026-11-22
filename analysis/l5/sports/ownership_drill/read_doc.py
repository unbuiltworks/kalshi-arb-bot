"""Account-ledger side: the REAL ledger and attestor (7c3d69ba + patch),
holding the four markets as production booked them, reading the document
the REAL sports code produced."""
import json, os, sys, tempfile
from decimal import Decimal as D
from src import l5_live_execution as LX, l5_venue_cost_attestation as VCA
from tests.test_l5_venue_cost_attestation import Feed, fill, retire, run_until_idle
got = json.loads(sys.stdin.read())
VCA.RETRY_S = 0.0
URL = "http://sports.invalid/l5/sports_fills.json"
os.environ[VCA.SIBLING_ENV] = VCA.SIBLING_TOKEN
real = VCA.sibling_evidence
VCA.sibling_evidence = lambda market, fetch=None: real(market, fetch=lambda url: got["doc"])
s = LX.InventoryStore(os.path.join(tempfile.mkdtemp(), "inv.json"))
for k in ("realized_unattributed", "realized_attributed", "realized_today"):
    s.state[k] = "0"
NBA, CFB = "aec-nba-lac-dal-2026-11-21", "aec-cfb-utsa-uab-2026-11-21"
BOL, GEN = "atc-sea-bol-udi-2026-11-22-bol", "atc-sea-sas-gen-2026-11-22-gen"
retire(s, NBA, dn="5", yes="0", t="2026-11-22T03:28:40+00:00")
retire(s, CFB, dn="5", yes="1", t="2026-11-22T04:31:08+00:00")
retire(s, BOL, up="4", yes="0", t="2026-11-22T15:54:57+00:00")
retire(s, GEN, dn="4", yes="1", t="2026-11-22T15:56:02+00:00")
rows = [fill(m, {"YES": "UP", "NO": "DOWN"}[side], q, px, "T%02d" % i, oid,
             t="2026-11-22T14:%02d:00Z" % (10 + i))
        for i, (m, side, q, px, oid) in enumerate(got["venue"])]
print("booked            ", s.realized_today())
run_until_idle(s, Feed([rows]))
print("nothing set       ", s.realized_today())
os.environ["L5_SPORTS_FILLS_URL"] = URL
os.environ["L5_SPORTS_ATTEST_FLOOR_UTC_DAY"] = "2026-11-22"
run_until_idle(s, Feed([rows]))
print("feed + floor set  ", s.realized_today())
for m in (NBA, CFB, BOL, GEN):
    r = s.state["retired_markets"][m]
    print("  %-34s %6s  %-8s %s" % (m, r["settlement_pnl"], r["origin"], r.get("venue_qty_form")))
print("by origin", {k: v["settled_usd"] for k, v in s.realized_by_origin()["by_origin"].items()})
