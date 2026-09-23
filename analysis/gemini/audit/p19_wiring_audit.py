"""P19 -- every declared spec field, traced to the executable that consumes it.

WHY. `placement` was declared in the spec, folded into `spec_sha`, carried
through the readiness artifact, printed in the run report -- and never reached
the Driver. Nothing failed. Probe B therefore ran the frozen control while
reporting the maker_improve spec, and no gate noticed.

The question this answers is NOT "is placement wired now" but "which OTHER
declared fields can be dropped the same way".

A field is CONSUMED when some executable reads it and its value changes
behaviour. A field that is only ever echoed into a report is DECORATIVE: it
cannot be wrong, but it also cannot be trusted as evidence that the run did
what the spec said.
"""
from __future__ import annotations

import ast
import json
import os
from typing import Any, Dict, List

SPEC_MODULE = "src/gemini_execution/live_probe_spec.py"
CONSUMERS = ("src/gemini_execution/run_continuous.py",
             "src/gemini_execution/cohort_runner.py",
             "src/gemini_execution/ws_continuous.py",
             "src/gemini_execution/continuous_probe.py",
             "src/gemini_execution/live_envelope.py")


def declared_fields() -> List[str]:
    """Every field of the LiveProbeSpec dataclass, in declaration order."""
    tree = ast.parse(open(SPEC_MODULE, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LiveProbeSpec":
            return [n.target.id for n in node.body
                    if isinstance(n, ast.AnnAssign)
                    and isinstance(n.target, ast.Name)]
    return []


def _reads(path: str, field: str) -> List[str]:
    """Where `field` is read from a spec-ish object in this file."""
    src = open(path, encoding="utf-8").read()
    hits = []
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        # spec.field / probe_spec.field
        if isinstance(node, ast.Attribute) and node.attr == field:
            base = getattr(node.value, "id", None)
            if base and ("spec" in base.lower() or base in ("sp", "s")):
                hits.append("%s:%d %s" % (os.path.basename(path), node.lineno,
                                          lines[node.lineno - 1].strip()[:88]))
        # getattr(probe_spec, "field", ...)
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == field):
            hits.append("%s:%d %s" % (os.path.basename(path), node.lineno,
                                      lines[node.lineno - 1].strip()[:88]))
    return hits


def audit() -> Dict[str, Any]:
    out: Dict[str, Any] = {"label": "P19_SPEC_WIRING_AUDIT_V1", "fields": {}}
    for f in declared_fields():
        rows: List[str] = []
        for c in CONSUMERS:
            if os.path.exists(c):
                rows += _reads(c, f)
        out["fields"][f] = {"read_sites": rows, "n": len(rows),
                            "verdict": "CONSUMED" if rows else "NO READER"}
    out["no_reader"] = [f for f, v in out["fields"].items() if not v["n"]]
    return out


if __name__ == "__main__":
    a = audit()
    print("DECLARED SPEC FIELDS AND WHERE THE EXECUTABLE READS THEM\n")
    for f, v in a["fields"].items():
        print("%-22s %-10s %d site(s)" % (f, v["verdict"], v["n"]))
        for r in v["read_sites"][:4]:
            print("      %s" % r)
    print("\nFIELDS WITH NO READER IN ANY CONSUMER: %s" % (a["no_reader"] or "none"))
    json.dump(a, open("analysis/gemini/audit/p19_wiring_audit.json", "w"),
              indent=2)
