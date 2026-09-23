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
import sys

# RUN FROM ANYWHERE. sys.path[0] is this directory when the module is run as a
# script, so `src` would not import and the classification would come back
# empty -- which an earlier version SWALLOWED, reporting every field
# UNCLASSIFIED and looking like a finding. Silently degraded evidence is the
# defect this whole module exists to catch.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
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


def _argv_carried() -> List[str]:
    """Fields canonical_argv puts on the command line, read from its source."""
    src = open(SPEC_MODULE, encoding="utf-8").read()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "canonical_argv":
            seg = ast.get_source_segment(src, n) or ""
            return [f for f in declared_fields()
                    if ("self.%s" % f) in seg]
    return []


def _property_mediated(field: str) -> List[str]:
    """Properties whose body reads `field` -- e.g. budget_cohort -> cohort."""
    src = open(SPEC_MODULE, encoding="utf-8").read()
    tree = ast.parse(src)
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name != field:
            seg = ast.get_source_segment(src, n) or ""
            if ("self.%s" % field) in seg:
                out.append(n.name)
    return out


def audit() -> Dict[str, Any]:
    """Every declared field, with HOW it reaches execution -- not just whether
    some file names it.

    THE FIRST VERSION LIED. A direct-attribute AST match reported cohort,
    auth_id, ledger and live_cohort as NO READER when all four reach execution
    through `budget_cohort` and `canonical_argv`. An audit that cries wolf on
    four healthy fields is an audit nobody will read the fifth time -- which
    is how `placement` survived. So mediation is resolved, not guessed at.
    """
    out: Dict[str, Any] = {"label": "P19_SPEC_WIRING_AUDIT_V2", "fields": {}}
    argv = _argv_carried()
    # NOT wrapped in a bare except: a classification that cannot be loaded is
    # an error, not an empty dict that reads as eleven findings.
    from src.gemini_execution import live_probe_spec as SP
    declared_map = SP.get("btc5m_q1_improve").executable_field_map()
    for f in declared_fields():
        rows: List[str] = []
        for c in CONSUMERS:
            if os.path.exists(c):
                rows += _reads(c, f)
        via = []
        if rows:
            via.append("direct read")
        if f in argv:
            via.append("canonical_argv")
        props = _property_mediated(f)
        if props:
            via.append("property: %s" % ",".join(props))
        out["fields"][f] = {
            "read_sites": rows, "n": len(rows),
            "reaches_execution_via": via,
            "declared_class": declared_map.get(f, "UNCLASSIFIED"),
            "verdict": ("CONSUMED" if via else "NO PATH TO EXECUTION"),
        }
    out["no_path"] = [f for f, v in out["fields"].items()
                      if not v["reaches_execution_via"]]
    out["unclassified"] = [f for f, v in out["fields"].items()
                           if v["declared_class"] == "UNCLASSIFIED"]
    return out


if __name__ == "__main__":
    a = audit()
    print("DECLARED SPEC FIELDS AND WHERE THE EXECUTABLE READS THEM\n")
    for f, v in a["fields"].items():
        print("%-16s %-22s %-34s via %s"
              % (f, v["verdict"], v["declared_class"][:34],
                 "; ".join(v["reaches_execution_via"]) or "-"))

    print("\nFIELDS WITH NO PATH TO EXECUTION: %s" % (a["no_path"] or "none"))
    print("HASHED BUT UNCLASSIFIED:          %s" % (a["unclassified"] or "none"))
    json.dump(a, open("analysis/gemini/audit/p19_wiring_audit.json", "w"),
              indent=2)
