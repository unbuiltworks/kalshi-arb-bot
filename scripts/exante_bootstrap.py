"""Whole-window clustered bootstrap CIs for the completed ex-ante benchmark.

Decisions within a window are serially correlated (the momentum finding
makes this certain), so per-decision resampling would understate variance.
Units of resampling here are WHOLE WINDOWS: draw W windows with
replacement, pool their decisions, recompute the deltas.

Deltas (conditional minus marginal, positive = v1 edge):
  * mean log-probability of L5's actual action
  * relation top-1 accuracy (percentage points)

Reported for the full sample and the strict-temporal subset, with 95%
percentile CIs and P(delta <= 0). Read-only: the tape is not modified.

    python3 scripts/exante_bootstrap.py data/l5/exante/exante_XXX.jsonl
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

N_BOOT = 10_000
SEED = 20260902
EPS = 1e-6


def _load(tape: Path):
    by_window: dict = defaultdict(list)
    for line in tape.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") != "scored" or row.get("off_distribution"):
            continue
        by_window[row["window_id"]].append(row)
    return by_window


def _deltas(rows) -> tuple:
    n = len(rows)
    dlogp = sum(math.log((r["p_conditional"] or 0) + EPS) -
                math.log((r["p_marginal"] or 0) + EPS) for r in rows) / n
    drel = 100.0 * (sum(1 for r in rows if r["relation_hit_conditional"]) -
                    sum(1 for r in rows if r["relation_hit_marginal"])) / n
    return dlogp, drel


def bootstrap(by_window: dict, label: str) -> dict:
    windows = list(by_window)
    all_rows = [r for rows in by_window.values() for r in rows]
    if not all_rows:
        return {"label": label, "n": 0}
    point = _deltas(all_rows)
    rng = random.Random(SEED)
    boots = ([], [])
    for _ in range(N_BOOT):
        sample = []
        for _ in windows:
            sample.extend(by_window[rng.choice(windows)])
        d = _deltas(sample)
        boots[0].append(d[0])
        boots[1].append(d[1])
    out = {"label": label, "windows": len(windows), "n_decisions": len(all_rows),
           "n_boot": N_BOOT, "seed": SEED}
    for name, pt, bs in (("delta_logp", point[0], boots[0]),
                         ("delta_relation_pp", point[1], boots[1])):
        bs.sort()
        out[name] = {
            "point": round(pt, 4),
            "ci95": [round(bs[int(0.025 * N_BOOT)], 4),
                     round(bs[int(0.975 * N_BOOT) - 1], 4)],
            "p_le_zero": round(sum(1 for b in bs if b <= 0) / N_BOOT, 4),
        }
    return out


def main() -> int:
    tape = Path(sys.argv[1])
    by_window = _load(tape)
    strict = {w: [r for r in rows if r.get("strict_temporal")]
              for w, rows in by_window.items()}
    strict = {w: rows for w, rows in strict.items() if rows}
    result = {
        "tape": str(tape),
        "method": ("whole-window clustered bootstrap, percentile CIs; "
                   "off-distribution rows excluded (there were none)"),
        "full_sample": bootstrap(by_window, "full"),
        "strict_temporal_subset": bootstrap(strict, "strict"),
    }
    out = Path("analysis/l5/l5_exante_bootstrap.json")
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
