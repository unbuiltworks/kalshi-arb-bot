"""Worst-case exposure of the RUNNING sports build (158685d3a) under a given
set of limits. Real engine + real quoting/caps/loss checks, fake venue.
An adversarial market: every resting bid that ADDS to the net position is
filled, opposite-side bids never are, prices drift. Reports the largest
unpaired cost ever held (= the most the account could lose if every
unpaired contract settles worthless; pairs cost <= the pair cap and gain)."""
import os, sys, json, random, tempfile, pathlib, time as _t
from decimal import Decimal as D
lim = json.loads(sys.argv[1])
for k, v in lim.items():
    os.environ[k] = str(v)
os.environ["PMUS_RN1_EDGE_GATE"] = "0"      # no pricing test: quote always
os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
os.environ["PMUS_TWO_SIDED_RN1"] = "1"
os.environ["PMUS_TWO_SIDED_MAX_MARKETS"] = "2"
os.environ["PMUS_RN1_REFILL_COOLDOWN_S"] = "0"
os.environ.pop("PMUS_TWO_SIDED_SUBMISSION_LOCK", None)
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from src import pmus_pilot_pnl as PP
from tests.test_pmus_sports_two_sided import FakeVenue
M.log = lambda *a, **k: None
worst = {"unpaired": D(0), "spend": D(0), "at": None}
for seed in range(int(sys.argv[2])):
    rnd = random.Random(seed)
    clock = {"t": 5_000_000.0 + seed * 10_000}
    M.time.time = lambda: clock["t"]
    v = FakeVenue()
    e = M.Engine(v, M.Book(pathlib.Path(tempfile.mkdtemp()) / "b.json"))
    e.since = clock["t"] - 1
    # the venue meter is not simulated: give the engine a clean, current one
    e._pilot = {"realized_net_usd": "0", "unconfirmed": [], "ok": True, "open_residual_worst_usd": "0", "outstanding_cost_usd": "0", "net_usd": "0"}
    slugs = ["aec-nfl-a", "aec-nba-b"]
    px = {s: D(str(rnd.choice([30, 40, 50, 60, 70]))) / 100 for s in slugs}
    for s in slugs:
        e.b.m(s)
    for step in range(120):
        clock["t"] += 3
        e._pilot_at = _t.monotonic()
        for s in slugs:
            px[s] = min(D("0.74"), max(D("0.26"), px[s] + D(rnd.choice([-2, -1, 0, 1, 2])) / 100))
            v.bid, v.ask = px[s], px[s] + D("0.01")
            try:
                e.step_market(s)
            except Exception as exc:
                print("ERR", type(exc).__name__, str(exc)[:100]); raise
            mk = e.b.d["markets"][s]
            y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
            heavy = "YES" if y > n else "NO" if n > y else rnd.choice(["YES", "NO"])
            for oid, o in mk["orders"].items():
                if o.get("open") and o["side"] == heavy and oid in v.orders:
                    v.orders[oid]["filled"] = v.orders[oid]["qty"]   # adverse fill
                    v.orders[oid]["open"] = False
            e.refresh_fills(s)
        unp = sum(R.unmatched_usd(m["pos"], m["cost"]) for m in e.b.d["markets"].values())
        sp = sum(D(str(m["cost"]["YES"])) + D(str(m["cost"]["NO"])) for m in e.b.d["markets"].values())
        if unp > worst["unpaired"]:
            worst.update(unpaired=unp, spend=sp, at=(seed, step),
                         pos={s: dict(e.b.d["markets"][s]["pos"]) for s in slugs})
print(json.dumps({"limits": lim, "seeds": int(sys.argv[2]),
                  "max_unpaired_cost_usd": str(worst["unpaired"]),
                  "spend_then_usd": str(worst["spend"]), "pos": worst.get("pos")}, default=str))
