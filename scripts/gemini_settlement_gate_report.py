"""Print the settlement-gate result for a workflow log. Decides nothing."""
from __future__ import annotations

import argparse
import json
from typing import Any, Mapping, Optional, Sequence


def render(d: Mapping[str, Any]) -> str:
    g = d.get("gate") or {}
    lines = ["GATE: %s | missing: %s" % (g.get("gate"), g.get("missing_union")),
             "settlement endpoint exists: %s %s"
             % (d.get("settlement_endpoint_exists"),
                d.get("settlement_endpoint_found"))]
    for s in d.get("per_symbol") or []:
        c = s.get("proof_chain") or {}
        lines.append("  %-28s first_break=%s"
                     % (s.get("symbol"), c.get("first_break")))
    for a in d.get("account_reads") or []:
        lines.append("  read %-24s status=%s reachable=%s"
                     % (a.get("path"), a.get("status"), a.get("reachable")))
    lines.append("")
    lines.append("This authorizes nothing. An OPEN gate means settlement "
                 "accounting CAN close, not that a canary is approved.")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path")
    a = ap.parse_args(argv)
    with open(a.path, "r", encoding="utf-8") as fh:
        print(render(json.load(fh)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
