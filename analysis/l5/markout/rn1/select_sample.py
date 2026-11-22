"""Step 1. Choose the markets and fills. usage: select_sample.py [days=3] [n_random_markets=300] [seed=7]
Population: the trader's fills in conditions gamma labels sportsMarketType == 'moneyline' whose LAST fill is in the
most recent <days> of the activity file. Gamma metadata is refreshed for those conditions (final prices).
 - core sample : <n_random_markets> conditions drawn at random -> ALL the trader's fills in them (maker and taker).
 - taker top-up: every other condition in the population with >= 2 taker fills -> ONLY its taker fills are used
                 (keeps the maker sample a plain random draw of markets while giving enough taker fills).
Each fill is labelled OPEN / EXTEND / COMPLETE / ADD_BOTH from the full fill history of its condition."""
import collections, gzip, json, random, sys
from common import *
days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
nrand = int(sys.argv[2]) if len(sys.argv) > 2 else 300
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
gam = json.load(open(GAMMA_OLD))
taker = set()
for l in gzip.open(TAK, "rt"):
    r = json.loads(l); taker.add((r["transaction_hash"], r["token_id"]))
by = collections.defaultdict(list)
for i, l in enumerate(gzip.open(ACT, "rt")):
    if '"TRADE"' not in l: continue
    r = json.loads(l); m = gam.get(r["condition_id"])
    if not m or m.get("sportsMarketType") != "moneyline": continue
    r["_i"] = i; by[r["condition_id"]].append(r)
T1 = max(r["timestamp"] for v in by.values() for r in v)
pop = sorted(c for c, v in by.items() if max(x["timestamp"] for x in v) >= T1 - days * 86400)
print("population: moneyline conditions with last fill in final %d days: %d, fills %d" % (days, len(pop), sum(len(by[c]) for c in pop)))
new = json.load(open(GAMMA_NEW)) if os.path.exists(GAMMA_NEW) else {}
todo = [c for c in pop if c not in new]
KEEP = ("slug", "conditionId", "outcomes", "outcomePrices", "clobTokenIds", "gameStartTime", "closed", "closedTime", "sportsMarketType", "negRisk", "feeSchedule")
for i in range(0, len(todo), 40):
    q = "&".join("condition_ids=" + c for c in todo[i:i + 40])
    for extra in ("&closed=true", "&closed=false"):
        d = get("https://gamma-api.polymarket.com/markets?limit=100&" + q + extra)
        for m in d if isinstance(d, list) else []: new[m["conditionId"]] = {k: m.get(k) for k in KEEP}
json.dump(new, open(GAMMA_NEW, "w"))
ok = []
for c in pop:
    m = new.get(c)
    if not m or not m.get("closed") or not m.get("outcomePrices"): continue
    px = [float(x) for x in json.loads(m["outcomePrices"])]; toks = json.loads(m["clobTokenIds"])
    if len(toks) != 2 or abs(sum(px) - 1) > 1e-6 or max(px) < 0.99: continue          # binary, fully settled to 0/1
    ok.append(c)
print("closed + settled binary conditions:", len(ok), "of", len(pop))
rng = random.Random(seed); core = set(rng.sample(ok, min(nrand, len(ok))))
ntk = {c: sum(1 for x in by[c] if (x["transaction_hash"], x["token_id"]) in taker) for c in ok}
topup = set(c for c in ok if c not in core and ntk[c] >= 2)
out = {"params": {"days": days, "n_random_markets": nrand, "seed": seed, "file_last_ts": T1}, "markets": {}, "fills": []}
for c in sorted(core | topup):
    m = new[c]; toks = json.loads(m["clobTokenIds"]); px = [float(x) for x in json.loads(m["outcomePrices"])]
    v = sorted(by[c], key=lambda r: (r["timestamp"], -r["_i"]))        # file is newest-first, so larger line number = earlier
    out["markets"][c] = {"slug": m["slug"], "game": game_key(m["slug"]), "league": m["slug"].split("-")[0], "tokens": toks, "final": px,
                         "gst": iso(m.get("gameStartTime")), "closed_ts": iso(m.get("closedTime")), "negRisk": bool(m.get("negRisk")),
                         "group": "core" if c in core else "taker_topup", "first_fill_ts": v[0]["timestamp"], "last_fill_ts": v[-1]["timestamp"]}
    held = set()
    for k, r in enumerate(v):
        ti = toks.index(r["token_id"])
        role = "OPEN" if not held else "ADD_BOTH" if len(held) == 2 else "EXTEND" if ti in held else "COMPLETE"
        held.add(ti)
        tk = (r["transaction_hash"], r["token_id"]) in taker
        if c in core or tk:
            out["fills"].append({"c": c, "k": k, "ts": r["timestamp"], "tok": ti, "px": r["price"], "sh": r["size"], "usd": r["usdc_size"],
                                 "taker": tk, "role": role, "tx": r["transaction_hash"][:14]})
json.dump(out, open(SAMPLE, "w"))
f = out["fills"]
print("markets: core %d, taker top-up %d; games %d" % (len(core), len(topup), len(set(m["game"] for m in out["markets"].values()))))
print("fills %d: maker %d taker %d; roles %s" % (len(f), sum(1 for x in f if not x["taker"]), sum(1 for x in f if x["taker"]), dict(collections.Counter(x["role"] for x in f))))
print("leagues", collections.Counter(m["league"] for m in out["markets"].values()).most_common(15))
