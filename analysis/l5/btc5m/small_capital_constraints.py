"""How the current limits shape a Bonereaper-style controller at our size.

PART 1  his windows (public fills, 2026-11-17..20) in the units our limits
        are written in: dollars per window, imbalance, share of dollars at
        0.90+, clips per window -- then the same quantities at the scale
        of a $0.60 window.
PART 2  our controller under the live configuration on the recorded quotes
        (2 s, 11-17..20; inferred STRICT fills): which limit refuses a
        quote, how often, and how early in the window each first binds.
Replay figures describe how the LIMITS bind; they are not profit evidence.
"""
import collections, gzip, json, os, re, sys
from decimal import Decimal as D
sys.path.insert(0, "."); sys.path.insert(0, "analysis/l5/btc5m")
os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_controller as C            # noqa: E402
import inventory_controller_replay as R             # noqa: E402
import remaining_fills_anatomy as M                 # noqa: E402

P = print
med = lambda v: sorted(v)[len(v) // 2] if v else float("nan")
ACT = "analysis/l5/bonereaper_recent/activity_20261117_20261120.jsonl.gz"
W = collections.defaultdict(lambda: dict(u=0.0, d=0.0, cu=0.0, cd=0.0, n=0, hi=0.0, sizes=[]))
for line in gzip.open(ACT, "rt"):
    r = json.loads(line)
    if not re.match(r"btc-updown-5m-\d+$", r.get("slug") or "") or r["type"] != "TRADE":
        continue
    w = W[r["slug"]]; up = r["outcome"].upper() == "UP"
    w["u" if up else "d"] += r["size"]; w["cu" if up else "cd"] += r["usdc_size"]
    w["n"] += 1; w["sizes"].append(r["usdc_size"])
    if r["price"] >= 0.90:
        w["hi"] += r["usdc_size"]
spend = [w["cu"] + w["cd"] for w in W.values()]
imb_sh = [abs(w["u"] - w["d"]) / (w["u"] + w["d"]) for w in W.values()]
unm_usd = []
for w in W.values():
    heavy_cost, heavy_sh, light_sh = (w["cu"], w["u"], w["d"]) if w["u"] >= w["d"] else (w["cd"], w["d"], w["u"])
    unm_usd.append(heavy_cost * (heavy_sh - light_sh) / heavy_sh if heavy_sh else 0.0)
side_usd = [max(w["cu"], w["cd"]) for w in W.values()]
S = med(spend); k = 0.60 / S
P("== 1. Bonereaper per window, %d windows, and the same at a $0.60 window ==" % len(W))
P("   %-44s %12s %14s %s" % ("median", "his size", "x %.6f" % k, "our limit"))
rows = (("dollars spent per window", S, "0.60 envelope"),
        ("dollars on his heavier side", med(side_usd), "0.30 side cap"),
        ("unmatched dollars at the close", med(unm_usd), "0.10 unmatched cap"),
        ("single fill, dollars", med([x for w in W.values() for x in w["sizes"]]), "0.05 clip"),
        ("largest single fill in a window, dollars", med([max(w["sizes"]) for w in W.values()]), "0.05 clip"))
for name, v, lim in rows:
    P("   %-44s %12.2f %14.4f   %s" % (name, v, v * k, lim))
P("   %-44s %12d" % ("fills per window", med([w["n"] for w in W.values()])))
P("   %-44s %11.0f%%" % ("share imbalance |UP-DOWN| / (UP+DOWN)", 100 * med(imb_sh)))
P("   %-44s %11.0f%%   (all windows pooled)" % ("dollars paid at 0.90 or above", 100 * sum(w["hi"] for w in W.values()) / sum(spend)))
P("   heavier side as a share of his spend: %.0f%% (ours is capped at 0.30/0.60 = 50%% of the envelope)" % (100 * med([a / b for a, b in zip(side_usd, spend)])))
P("   unmatched as a share of his spend: %.0f%% (ours is capped at 0.10/0.60 = 17%%)" % (100 * med([a / b for a, b in zip(unm_usd, spend)])))

rows_, res = M.load()
ws = sorted(s for s in rows_ if s[-16:-6] >= "2026-11-17")
par = R.params(False)
first = collections.defaultdict(list); steps = collections.Counter(); tot_steps = 0
spent = []; fills_n = []; quiet = []
for s in ws:
    inv = {x: [D(0), D(0)] for x in C.SIDES}; rest = []; nid = 0; seen = {}; nf = 0; idle = 0; n = 0
    for t, b, a in rows_[s]:
        sa = {C.UP: D(str(a)), C.DOWN: D(1) - D(str(b))}
        keep = []
        for o in rest:
            if sa[o["side"]] <= o["price"]:
                inv[o["side"]][0] += o["qty"]; inv[o["side"]][1] += o["qty"] * o["price"]; nf += 1
            else:
                keep.append(o)
        rest = keep
        d = C.decide(book={"bid": str(b), "ask": str(a), "age_s": 0.1}, t_in_window_s=t,
                     inventory={x: {"qty": str(inv[x][0]), "cost": str(inv[x][1])} for x in C.SIDES},
                     resting=[{"id": o["id"], "side": o["side"], "price": str(o["price"]), "qty": str(o["qty"]),
                               "age_s": max(t - o["t"], 0.0), "reason": o["reason"]} for o in rest], params=par)
        for o in d["orders"]:
            if o.get("op") == "CANCEL":
                rest = [x for x in rest if x["id"] != o["id"]]
        placed = 0
        for o in d["orders"]:
            if o.get("op", "PLACE") == "PLACE" and D(str(o["price"])) < sa[o["side"]]:
                nid += 1; placed += 1
                rest.append({"id": "o%d" % nid, "side": o["side"], "price": D(str(o["price"])), "qty": D(str(o["qty"])), "t": t, "reason": o.get("reason")})
        n += 1
        sr = (d.get("evaluation") or {}).get("side_refusals") or {}
        reasons = {v for v in sr.values() if v}
        if d.get("why") in ("ENTRY_WINDOW_CLOSED", "NO_EXPOSURE_ROOM", "WINDOW_LOSS_CEILING"):
            reasons.add(d["why"])
        for v in reasons:
            steps[v] += 1
            seen.setdefault(v, t)
        if not rest and not placed:
            idle += 1
    tot_steps += n
    for v, t in seen.items():
        first[v].append(t)
    spent.append(float(inv[C.UP][1] + inv[C.DOWN][1])); fills_n.append(nf); quiet.append(idle / max(n, 1))
P("\n== 2. our controller, live configuration, %d windows (inferred fills) ==" % len(ws))
P("   median dollars filled per window $%.3f of the $0.60 envelope; median fills %d; median share of the window with NO order working: %.0f%%" % (med(spent), med(fills_n), 100 * med(quiet)))
P("   %-28s %18s %22s %s" % ("limit that refused a side", "windows it bound in", "median first bind (s)", "share of all decisions"))
for v, c in steps.most_common():
    P("   %-28s %17.0f%% %22.0f %21.1f%%" % (v, 100 * len(first[v]) / len(ws), med(first[v]), 100 * c / tot_steps))
