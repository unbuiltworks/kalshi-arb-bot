#!/usr/bin/env python3
"""Pre-flight activity gate: never sample a dormant book again.

The 2026-10-02T00:15Z run spent an hour on books that produced 6 distinct
states, because nothing checked for movement before committing. This probes
candidates briefly and ranks by how much the book actually moves, so the
expensive run is pointed at a live market.

READ-ONLY. GET on /v1/markets/{slug}/book only.
"""
import json, sys, time, urllib.request, collections
from datetime import datetime, timezone

GW = "https://gateway.polymarket.us/v1"
CENSUS = "data/pmus/incentives/programs.json"


def get(slug):
    r = urllib.request.urlopen(urllib.request.Request(
        f"{GW}/markets/{slug}/book", headers={"Accept": "application/json"}), timeout=8)
    return json.load(r)["marketData"]


def main(probe_s=20, top=14):
    cen = json.load(open(CENSUS))
    now = datetime.now(timezone.utc)
    cands = []
    for p in cen["programs"]:
        for tp in p.get("timePeriods") or []:
            if tp.get("status") != "active" or tp.get("period") != "live":
                continue
            # a live program whose event has already started
            est = p.get("eventStartTime")
            if not est:
                continue
            try:
                t = datetime.fromisoformat(est.replace("Z", "+00:00"))
            except Exception:
                continue
            if t <= now:
                cands.append((p, tp, tp.get("rewardPool") or 0))
    cands.sort(key=lambda x: -x[2])
    seen, picks = set(), []
    for p, tp, pool in cands:
        if p["marketSlug"] in seen:
            continue
        seen.add(p["marketSlug"]); picks.append((p, tp, pool))
        if len(picks) >= top:
            break
    print(f"live-program candidates with a started event: {len(cands)}; probing {len(picks)}")

    states = collections.defaultdict(set)
    n = collections.Counter()
    deadline = time.time() + probe_s
    while time.time() < deadline:
        for p, tp, pool in picks:
            s = p["marketSlug"]
            try:
                md = get(s)
                key = json.dumps([[[l["px"]["value"], l["qty"]] for l in (md.get(k) or [])]
                                  for k in ("bids", "offers")])
                states[s].add(key); n[s] += 1
            except Exception:
                pass
            time.sleep(0.5)          # ~2 req/s, well under the limit
    print(f"\n{'market':46s} {'pool':>7s} {'n':>4s} {'distinct':>8s} {'churn':>7s}")
    out = []
    for p, tp, pool in picks:
        s = p["marketSlug"]
        if not n[s]:
            continue
        churn = len(states[s]) / n[s]
        out.append((churn, s, tp["programId"], pool, n[s], len(states[s])))
    for churn, s, pid, pool, ns, ds in sorted(out, reverse=True):
        print(f"{s[:46]:46s} {pool:>7} {ns:>4d} {ds:>8d} {churn:>7.3f}")
    print("\nchurn = distinct states / samples. Near 0 is a dormant book.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
