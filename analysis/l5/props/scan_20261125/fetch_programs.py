"""Read-only: liquidity-programme lookup (GET /v1/incentives?symbols=...) for
every market in markets_full.jsonl, same call as reward_scan_all.py."""
import json, sys, time
sys.path.insert(0, "/tmp/claude-0/-home-user-kalshi-arb-bot/dfd6c478-98b1-5b09-ba6a-de4e42bef2ef/scratchpad/dev")
from src import pmus_sports_rewards as R
from fetch_events import get_json
from classify import load
g = R.SpacedGet(get_json, gap_s=0.5)
slugs = sorted({m["slug"] for m in load()})
found = {}; errs = []
for i in range(0, len(slugs), 60):
    f, diag = R.fetch_programs(g, slugs[i:i + 60])
    found.update(f); errs += diag["errors"]
    if (i // 60) % 25 == 0: print(i, len(found), len(errs), file=sys.stderr, flush=True)
json.dump({"t": time.time(), "found": found, "errors": errs, "n_slugs": len(slugs), "requests": g.n}, open("programs_raw.json", "w"))
print("done", len(slugs), len(found), len(errs), g.n, file=sys.stderr)
