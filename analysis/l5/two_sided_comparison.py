"""ARGMAX@ONE_TICK_BEHIND versus ARGMAX@TWO_SIDED, offline.

A THIN WRAPPER, deliberately. The comparison itself lives in `src.l5_shadow_controller.compare_all`,
because the running service emits it as `L5_SHADOW_TWO_SIDED` and a second
copy here would be a second implementation free to drift from the one that
produces the production numbers. This file only feeds it a document and
prints the result.

Usage:
    python3 analysis/l5/two_sided_comparison.py HEARTBEAT.json
    ... | python3 analysis/l5/two_sided_comparison.py

Accepts an `L5_LIVE_HB` document, its `shadow_controller` block, a bare
{label: report()} map, or an already-computed `L5_SHADOW_TWO_SIDED`
document (which it passes through, so a production line can be re-read
without recomputation).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, List, Mapping

# the repo root, derived rather than hardcoded, so this runs from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from src.l5_shadow_controller import compare_all   # noqa: E402


def reports_from(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    """Find the {label: report()} map in whatever was handed over."""
    inner = doc.get("shadow_controller")
    if isinstance(inner, Mapping):
        return inner
    if any(isinstance(v, Mapping) and "economics" in v
           for v in doc.values()):
        return doc
    raise SystemExit("no shadow_controller reports in this document")


def main(argv: List[str]) -> int:
    raw = (open(argv[1]).read() if len(argv) > 1 else sys.stdin.read())
    doc = json.loads(raw)
    # an already-computed comparison passes straight through
    out = (doc if "comparisons" in doc and "mechanics" in doc
           else compare_all(reports_from(doc)))
    print(json.dumps(out, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
