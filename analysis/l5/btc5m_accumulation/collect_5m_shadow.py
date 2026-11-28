"""Harvest the 15m straddle shadow's window records into the repo.

WHY THIS EXISTS. `l5-btc15m-straddle-shadow` deploys from a working
branch, so every push to that branch RESTARTS it, and the service has no
volume (the project is at Railway's 20-volume limit). Its history
therefore lives only in Railway's log buffer and is lost on each restart.
On 2026-11-14 that had reduced a shadow running since the previous
afternoon to a single recorded window.

So the durable store is this repository. Each run fetches the shadow's
logs, extracts completed-window records, and appends the ones not already
held, keyed by market slug. Idempotent: running it twice adds nothing.

READ-ONLY with respect to the venue and the services. It reads Railway
logs and writes one local file.

usage:
  python3 -I analysis/l5/btc5m_accumulation/collect_15m_shadow.py        # harvest
  python3 -I analysis/l5/btc5m_accumulation/collect_15m_shadow.py --tally
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "btc5m_shadow_windows.jsonl")
PROJECT = "50fb7357-e2bc-4d04-a49e-71eb5d73b0d1"
ENVIRON = "f3642d27-26dc-4e21-9170-bc76290c076f"
SHADOW15 = "a5ce6694-3ef7-446d-be8b-25586e16078b"
URL = "https://backboard.railway.com/graphql/v2"
TAG = "BTC5M_STRADDLE_SHADOW {"


def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables})
    out = subprocess.run(
        ["curl", "-sS", "-X", "POST", URL,
         "-H", "Content-Type: application/json",
         "-H", "Project-Access-Token: " + os.environ["RAILWAY_PROJECT_TOKEN"],
         "-d", body], capture_output=True, text=True, timeout=90).stdout
    return json.loads(out)


def fetch_rows():
    """Window records from EVERY deployment Railway still retains."""
    q = ("query($p:String!,$e:String!,$s:String!){ deployments(input:"
         "{projectId:$p,environmentId:$e,serviceId:$s}, first:10)"
         "{ edges{ node{ id } } } }")
    deps = [e["node"]["id"] for e in
            gql(q, {"p": PROJECT, "e": ENVIRON, "s": SHADOW15})
            ["data"]["deployments"]["edges"]]
    rows = []
    lq = ("query($d:String!){ deploymentLogs(deploymentId:$d, limit:2000)"
          "{ message } }")
    for d in deps:
        try:
            msgs = gql(lq, {"d": d})["data"]["deploymentLogs"]
        except BaseException:                                  # noqa: BLE001
            continue
        for m in msgs:
            s = m.get("message") or ""
            if s.startswith(TAG):
                try:
                    rows.append(json.loads(s.split(" ", 1)[1]))
                except ValueError:
                    pass
    return rows


def load_store():
    held = {}
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("market"):
                    held[r["market"]] = r
    return held


def tally(held):
    rows = list(held.values())
    n = len(rows)
    if not n:
        print("no windows held yet")
        return
    cases = {}
    for r in rows:
        cases[r.get("case")] = cases.get(r.get("case"), 0) + 1
    both = cases.get("BOTH", 0)
    print("5m STRADDLE SHADOW, %d windows held" % n)
    print("  cases: %s" % cases)
    print("  PAIR RATE %d/%d = %.1f%%   (BTC5m tape: 10.2%%)"
          % (both, n, 100.0 * both / n))
    pn = [float(r["pnl_per_share"]) for r in rows
          if r.get("pnl_per_share") is not None]
    if len(pn) > 1:
        m = sum(pn) / len(pn)
        sd = math.sqrt(sum((x - m) ** 2 for x in pn) / (len(pn) - 1))
        t = m / (sd / math.sqrt(len(pn))) if sd > 0 else float("nan")
        print("  net %+.2f c/w   t %+.2f   n %d   (BTC5m tape: +3.74, t +1.67)"
              % (100 * m, t, len(pn)))
    print()
    print("  SHADOW. No order was placed. These are hypothetical fills under")
    print("  the same adverse rule the 5m tape uses, and are NOT realized.")


def main(argv):
    held = load_store()
    if "--tally" in argv:
        tally(held)
        return 0
    before = len(held)
    for r in fetch_rows():
        mk = r.get("market")
        if mk and mk not in held:
            held[mk] = r
    with open(STORE, "w", encoding="utf-8") as fh:
        for mk in sorted(held):
            fh.write(json.dumps(held[mk], sort_keys=True) + "\n")
    print("harvested: %d new, %d held total -> %s"
          % (len(held) - before, len(held), STORE))
    tally(held)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
