#!/usr/bin/env python3
"""The compact cumulative dashboard over the shadow store.

  python3 scripts/l5_shadow_dashboard.py [--store PATH] [--live live.json]

`--live` is the ACTUAL trading position, taken from live-runner evidence
(L5_LIVE_HB / L5_LIVE_SETTLED), never inferred from the shadow store.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import l5_shadow_policy_eval as E          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="data/l5/shadow/policy_eval.jsonl")
    ap.add_argument("--live", default=None)
    a = ap.parse_args()
    rows = []
    if os.path.exists(a.store):
        for line in open(a.store):
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    live = json.load(open(a.live)) if a.live else None
    print(json.dumps(E.dashboard(rows, live), indent=2, default=str))


if __name__ == "__main__":
    main()
