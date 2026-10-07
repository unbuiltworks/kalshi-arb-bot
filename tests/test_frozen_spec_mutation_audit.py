"""FROZEN-SPEC MUTATION AUDIT — offline, read-only, no arming, no orders.

Audits the derivation pattern

    def _apply(spec, dotted, value):
        ...
        node[parts[-1]] = value          # <-- DIFF's own object, not a copy

    def build():
        spec = copy.deepcopy(PARENT_SPEC)
        for dotted, value in DIFF.items():
            _apply(spec, dotted, value)
        return spec

Every nested mutable value in DIFF is therefore ALIASED into the built SPEC:
`SPEC[...] is DIFF[...]`. A caller holding what looks like a copy of SPEC
(e.g. `dict(SPEC)`, a shallow copy) can reach through and edit both the frozen
SPEC and the module-level DIFF literal in place.

This file MUTATES NOTHING PERSISTENT. Every mutation is performed on the
in-process module object inside a try/finally that restores the exact prior
value, and no artifact, fingerprint or spec module is edited on disk.

Run:  python -m pytest tests/test_frozen_spec_mutation_audit.py -v
      python tests/test_frozen_spec_mutation_audit.py      (prints a report)
"""
from __future__ import annotations

import copy
import importlib
from typing import Any, Dict, List, Optional, Tuple

# (label, module path, this module's OWN fingerprint-literal attribute)
LINEAGE: List[Tuple[str, str, str]] = [
    ("EXEC-1", "analysis.l5.live_exec.spec", "EXEC1_FINGERPRINT"),
    ("EXEC-1-X", "analysis.l5.live_exec2.spec", "EXEC1X_FINGERPRINT"),
    ("XR", "analysis.l5.live_exec2.runner_spec", "XR_FINGERPRINT"),
    ("XR2", "analysis.l5.live_exec3.spec", "XR2_FINGERPRINT"),
    ("XR2W", "analysis.l5.live_exec5.spec", "XR2W_FINGERPRINT"),
    ("XR3", "analysis.l5.live_exec4.spec", "XR3_FINGERPRINT"),
    ("STAGE-6-S6P", "analysis.l5.stage6.spec", "S6P_FINGERPRINT"),
]

# Standalone frozen artifacts: literal SPEC, no DIFF derivation.
STANDALONE: List[Tuple[str, str, str, str]] = [
    ("S3", "analysis.l5.s3.spec", "SPEC", "S3_FINGERPRINT"),
    ("PMUS-FEE-SCHEDULE", "src.pmus_fee_schedule", "SCHEDULE", "FINGERPRINT"),
    ("LIVE-L5-MICRO-1", "analysis.l5.live_micro.spec", "SPEC",
     "MICRO_FINGERPRINT"),
    ("PROBE-1", "analysis.l5.live_micro.spec_probe1_frozen", "SPEC",
     "PROBE_1_FINGERPRINT"),
    ("PAIRCOST", "analysis.l5.paircost.spec", "SPEC", "PAIRCOST_FINGERPRINT"),
    ("RESIDUAL-FRONTIER", "analysis.l5.residual_frontier.spec", "SPEC",
     "FRONTIER_FINGERPRINT"),
]


def _get(spec: Dict[str, Any], dotted: str) -> Any:
    node: Any = spec
    for part in dotted.split("."):
        node = node[part]
    return node


def _fingerprint_literal(mod: Any, attr: str) -> Optional[str]:
    """The module's OWN hard-coded expected fingerprint, if it has one."""
    val = getattr(mod, attr, None)
    return val if isinstance(val, str) and len(val) == 64 else None


def _shared_paths(mod: Any) -> List[str]:
    """DIFF keys whose nested mutable value is the SAME OBJECT in SPEC."""
    diff = getattr(mod, "DIFF", None)
    if not diff:
        return []
    out: List[str] = []
    for dotted, value in diff.items():
        if not isinstance(value, (dict, list)):
            continue
        try:
            if _get(mod.SPEC, dotted) is value:
                out.append(dotted)
        except (KeyError, TypeError):
            pass
    return out


def audit_one(label: str, path: str, fp_attr: str) -> Dict[str, Any]:
    """Observe, do not assume. Returns the measured facts for one module."""
    mod = importlib.import_module(path)
    shared = _shared_paths(mod)
    rec: Dict[str, Any] = {
        "label": label,
        "module": path,
        "has_diff": hasattr(mod, "DIFF"),
        "shared_paths": shared,
        "aliased": bool(shared),
        "fingerprint_literal": _fingerprint_literal(mod, fp_attr),
        "fingerprint_is_literal": _fingerprint_literal(mod, fp_attr) is not None,
        "assert_frozen_passes_clean": None,
        "assert_frozen_passes_after_mutation": None,
        "diff_corrupted_by_mutation": None,
        "diff_report_corrupted": None,
        "mutation_probe": None,
    }

    # (a) baseline: does assert_frozen() pass as shipped?
    try:
        mod.assert_frozen()
        rec["assert_frozen_passes_clean"] = True
    except BaseException as exc:            # SystemExit is a BaseException
        rec["assert_frozen_passes_clean"] = False
        rec["clean_failure"] = "%s: %s" % (type(exc).__name__, exc)

    if not shared:
        return rec

    # (b) the attack: a caller takes what LOOKS like a copy and edits it.
    target = shared[0]
    rec["mutation_probe"] = target
    victim = dict(mod.SPEC)                 # the deceptive "copy"
    node = _get(victim, target)
    sentinel_key = "__audit_injected__"
    restore_list_len = len(node) if isinstance(node, list) else None
    try:
        if isinstance(node, dict):
            node[sentinel_key] = "MUTATED-BY-AUDIT"
        else:
            node.append("MUTATED-BY-AUDIT")

        # Did the mutation reach the frozen SPEC through the alias?
        reached = (sentinel_key in _get(mod.SPEC, target)
                   if isinstance(node, dict)
                   else "MUTATED-BY-AUDIT" in _get(mod.SPEC, target))
        rec["mutation_reached_frozen_spec"] = reached

        # Did it also corrupt the module-level DIFF literal?
        dnode = mod.DIFF[target]
        rec["diff_corrupted_by_mutation"] = (
            sentinel_key in dnode if isinstance(dnode, dict)
            else "MUTATED-BY-AUDIT" in dnode)

        # Does the frozen-spec assertion still pass?
        try:
            mod.assert_frozen()
            rec["assert_frozen_passes_after_mutation"] = True
        except BaseException as exc:
            rec["assert_frozen_passes_after_mutation"] = False
            rec["mutation_failure"] = "%s: %s" % (
                type(exc).__name__, str(exc)[:160])

        # Does diff_report() now report the injected key as provenance?
        if hasattr(mod, "diff_report"):
            try:
                rep = mod.diff_report()
                blob = repr(rep)
                rec["diff_report_corrupted"] = "MUTATED-BY-AUDIT" in blob
            except BaseException as exc:
                rec["diff_report_corrupted"] = "ERROR: %s" % type(exc).__name__
    finally:
        if isinstance(node, dict):
            node.pop(sentinel_key, None)
        else:
            del node[restore_list_len:]

    # (c) post-restore: the module must be exactly as shipped again.
    try:
        mod.assert_frozen()
        rec["restored_clean"] = True
    except BaseException as exc:
        rec["restored_clean"] = "%s: %s" % (type(exc).__name__, exc)
    return rec


def audit_build_time_diff_corruption(label: str, path: str) -> Dict[str, Any]:
    """Does calling build() again write into DIFF's own nested objects?

    It does whenever a single DIFF carries both a whole-dict key `"foo"` and a
    dotted sub-key `"foo.bar"`: `_apply` navigates into DIFF's own `foo` dict
    and assigns there.
    """
    mod = importlib.import_module(path)
    if not hasattr(mod, "build") or not hasattr(mod, "DIFF"):
        return {"label": label, "applicable": False}
    before = copy.deepcopy(mod.DIFF)
    mod.build()
    return {"label": label, "applicable": True,
            "diff_mutated_by_build": before != mod.DIFF}


def audit_standalone(label: str, path: str, spec_attr: str,
                     fp_attr: str) -> Dict[str, Any]:
    mod = importlib.import_module(path)
    rec: Dict[str, Any] = {
        "label": label, "module": path, "has_diff": hasattr(mod, "DIFF"),
        "fingerprint_literal": getattr(mod, fp_attr, None),
        "fingerprint_is_literal": True,
        "aliased": False,
    }
    spec = getattr(mod, spec_attr)
    try:
        mod.assert_frozen()
        rec["assert_frozen_passes_clean"] = True
    except BaseException as exc:
        rec["assert_frozen_passes_clean"] = False
        rec["clean_failure"] = "%s: %s" % (type(exc).__name__, exc)

    # Mutate a nested dict in place and check the assertion catches it.
    key = next((k for k, v in spec.items() if isinstance(v, dict)), None)
    rec["mutation_probe"] = key
    if key is None:
        return rec
    node = spec[key]
    try:
        node["__audit_injected__"] = "MUTATED-BY-AUDIT"
        try:
            mod.assert_frozen()
            rec["assert_frozen_passes_after_mutation"] = True
        except BaseException as exc:
            rec["assert_frozen_passes_after_mutation"] = False
            rec["mutation_failure"] = "%s: %s" % (
                type(exc).__name__, str(exc)[:120])
    finally:
        node.pop("__audit_injected__", None)
    try:
        mod.assert_frozen()
        rec["restored_clean"] = True
    except BaseException as exc:
        rec["restored_clean"] = "%s: %s" % (type(exc).__name__, exc)
    return rec


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_lineage_aliasing_is_as_documented():
    """The five derived specs alias DIFF; S6P does not (it deep-copies)."""
    seen = {}
    for label, path, fp_attr in LINEAGE:
        rec = audit_one(label, path, fp_attr)
        seen[label] = rec
    assert seen["EXEC-1"]["aliased"] is False, "EXEC-1 has no DIFF"
    for label in ("EXEC-1-X", "XR", "XR2", "XR2W", "XR3"):
        assert seen[label]["aliased"] is True, label
        assert seen[label]["mutation_reached_frozen_spec"] is True, label
        assert seen[label]["diff_corrupted_by_mutation"] is True, label
    assert seen["STAGE-6-S6P"]["aliased"] is False, \
        "S6P deep-copies in _apply and must not alias"


def test_every_lineage_fingerprint_is_a_hard_coded_literal():
    for label, path, fp_attr in LINEAGE:
        rec = audit_one(label, path, fp_attr)
        assert rec["fingerprint_is_literal"], label


def test_assert_frozen_is_not_vacuous_under_mutation():
    """The load-bearing question: can a mutated spec still self-certify?"""
    for label, path, fp_attr in LINEAGE:
        rec = audit_one(label, path, fp_attr)
        if not rec["aliased"]:
            continue
        assert rec["assert_frozen_passes_after_mutation"] is False, (
            "%s SELF-CERTIFIED A MUTATED SPEC" % label)


def test_standalone_artifacts_have_no_diff_aliasing():
    for label, path, spec_attr, fp_attr in STANDALONE:
        rec = audit_standalone(label, path, spec_attr, fp_attr)
        assert rec["has_diff"] is False, label
        assert rec["assert_frozen_passes_after_mutation"] is False, label


def test_audit_leaves_everything_as_shipped():
    for label, path, fp_attr in LINEAGE:
        rec = audit_one(label, path, fp_attr)
        assert rec.get("restored_clean", True) is True, (label, rec)


if __name__ == "__main__":
    import json as _json
    out = {"lineage": [audit_one(l, p, f) for l, p, f in LINEAGE],
           "build_time": [audit_build_time_diff_corruption(l, p)
                          for l, p, _ in LINEAGE],
           "standalone": [audit_standalone(*s) for s in STANDALONE]}
    print(_json.dumps(out, indent=2, default=str))
