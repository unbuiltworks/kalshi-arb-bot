"""Proof on the RUNNING build (158685d3a): extending tonight's window from
21:35-23:05Z to 21:35-01:30Z (same start) keeps one campaign. Real engine,
real book file, fake venue; each "boot" is a new Engine on the same book,
with the module's marker set as a process start with that window sets it."""
import os, sys, tempfile, pathlib, json
from decimal import Decimal as D
os.environ["PMUS_RN1_EDGE_GATE"] = "0"          # pricing is not under test
os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
os.environ["PMUS_RN1_PILOT_LOSS_USD"] = "4"
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from src import pmus_pilot_pnl as PP
from tests.test_pmus_sports_two_sided import FakeVenue
R.ON = True
M.log = lambda *a, **k: None
A = "window:2026-11-22T21:35:00Z/2026-11-22T23:05:00Z"
B = "window:2026-11-22T21:35:00Z/2026-11-23T01:30:00Z"
C = "window:2026-11-23T01:30:00Z/2026-11-23T04:30:00Z"
START, END_A, END_B = 1795383300.0, 1795388700.0, 1795397400.0
path = pathlib.Path(tempfile.mkdtemp()) / "book.json"
clock = {"t": START + 600}
M.time.time = lambda: clock["t"]
def boot(win):
    os.environ[M.SUBMISSION_LOCK_ENV] = win
    w = M.lock_window()
    M.PILOT_SINCE = w[0]                        # as at import with this window
    v = FakeVenue(); v.bid, v.ask = D("0.48"), D("0.50")
    return M.Engine(v, M.Book(path)), v
out = {}
# --- first period: trade, fill some
e, v = boot(A)
out["A_marker"] = e.since
# the venue meter is not simulated here: let the first period place its
# orders, then switch the $4 campaign loss stop on for every check below
M.PILOT_LOSS_USD = D("0")
for slug in ("aec-nfl-a", "aec-nba-b"):
    e.b.m(slug); e.step_market(slug)
for oid, o in list(v.orders.items()):
    if o["side"] == "YES":
        o["filled"] = D("2")
for slug in ("aec-nfl-a", "aec-nba-b"):
    e.refresh_fills(slug)
e.b.save()
mk = e.b.d["markets"]
spend_A = sum(D(str(m["cost"]["YES"])) + D(str(m["cost"]["NO"])) for m in mk.values())
out["A_pilot_markets"] = PP.pilot_slugs(mk, e.since)
out["A_spend"] = str(spend_A)
M.PILOT_LOSS_USD = D("4")
# the campaign loss stop latches in the book during the first period
e.b.d["pilot_loss_stop"] = {"t": "test", "net": "-4.01"}; e.b.save()
# --- 23:05: the window ends; the bot relocks itself
clock["t"] = END_A + 5
out["A_after_end"] = M.lock_state()["why"]
# --- continuation: same start, later end; a new process on the same book
e2, v2 = boot(B)
clock["t"] = END_A + 300
out["B_marker"] = e2.since
out["B_marker_same_as_A"] = e2.since == out["A_marker"] == START
out["B_lock"] = M.lock_state()
out["B_pilot_markets"] = PP.pilot_slugs(e2.b.d["markets"], e2.since)
out["B_counts_first_period_orders"] = out["B_pilot_markets"] == out["A_pilot_markets"]
out["B_loss_stop_still_latched"] = bool(e2.b.d.get("pilot_loss_stop")) and bool(e2.pilot_loss_stopped())
out["B_block_from_loss_stop"] = e2.pilot_block()
# without the latch: the campaign caps see the first period's commitments
e2.b.d.pop("pilot_loss_stop"); e2.b.save()
tot = {"spend": D(0), "unmatched": D(0)}
for s_ in PP.pilot_slugs(e2.b.d["markets"], e2.since):
    m = e2.b.d["markets"][s_]
    c = R.committed(m["pos"], m["cost"])
    tot["spend"] += c["spend"]; tot["unmatched"] += c["unmatched"]
out["B_campaign_committed_from_first_period"] = {k: str(x) for k, x in tot.items()}
# the every-step worst-case loss check also counts the first period:
# 4 unpaired YES contracts at 0.47 -> worst case -1.88
out["B_book_worst_case_usd"] = str(PP.book_worst_case(e2.b.d["markets"], e2.since))
# ...and would NOT if the marker had moved to the continuation's own start
out["if_marker_were_2305_worst_case"] = str(PP.book_worst_case(e2.b.d["markets"], END_A))
out["if_marker_were_2305_pilot_markets"] = PP.pilot_slugs(e2.b.d["markets"], END_A)
# --- for contrast: the separate Sunday-night window is a NEW campaign
e3, v3 = boot(C)
out["C_marker"] = e3.since
out["C_is_new_campaign"] = e3.since == END_B and PP.pilot_slugs(e3.b.d["markets"], e3.since) == []
e3.b.d["pilot_loss_stop"] = {"t": "test", "net": "-4.01"}
out["C_loss_stop_latch_blocks_the_new_campaign_too"] = e3.pilot_block()
e3.b.d.pop("pilot_loss_stop")
print(json.dumps(out, indent=1, default=str))
