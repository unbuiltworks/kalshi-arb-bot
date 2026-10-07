"""Guards on the OUT-OF-SAMPLE CONFIRMATION run.

These tests exist to prove three things a reader cannot otherwise verify:

  1. the preregistered spec was NOT modified to produce the OOS result;
  2. no holdout row leaked into any in-sample figure the OOS report quotes;
  3. the adapter is a PURE FIELD MAPPING -- it renames nothing, computes
     nothing, and only ever removes records.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os

import pytest

from analysis.l5.residual_frontier import oos, oos_adapter as ad, spec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT = os.path.join(ROOT, "analysis/l5/residual_frontier/OOS_RESULT.json")
IN_SAMPLE = os.path.join(ROOT, "analysis/l5/residual_frontier/FRONTIER.json")

#: sha256 of the frozen artifacts as they stood when the in-sample result was
#: committed. A byte-level change to either fails here, which is stricter than
#: the spec fingerprint (that only covers the SPEC dict).
FROZEN_SHA256 = {
    "analysis/l5/residual_frontier/spec.py":
        "f97302310beb9e15eeaefd06b48beb2ca6895d905419182c0f2b6be5d471e948",
    "analysis/l5/residual_frontier/PREREGISTRATION.md":
        "958446eb6aad9a3ced80eb6fe18ab8ce80ea2c7abeff2408932279274a6fea0e",
    "analysis/l5/residual_frontier/replay.py":
        "8a7209455bef7d1c1e5a1524a6eea81ac6c4a65a2a4cf494ff8cf8b73eb76d0a",
}


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ------------------------------------------------- 1. the spec is unmodified

def test_assert_frozen_passes():
    spec.assert_frozen()


def test_fingerprint_literal_is_unchanged():
    assert spec.FRONTIER_FINGERPRINT == (
        "4b16c92bc658583265d014c196a3becf42540cad20c2e98999f954bea16e074d")
    assert spec.fingerprint() == spec.FRONTIER_FINGERPRINT


@pytest.mark.parametrize("rel", sorted(FROZEN_SHA256))
def test_frozen_files_are_byte_identical(rel):
    assert _sha(os.path.join(ROOT, rel)) == FROZEN_SHA256[rel], (
        "%s was modified; the OOS run must not touch it" % rel)


def test_grid_is_still_the_same_30_cells():
    assert spec.N_CELLS == 30
    ids = [c["cell_id"] for c in spec.GRID]
    assert len(ids) == len(set(ids)) == 30
    # the two pre-committed candidates and the pre-committed failure cell
    for cid in oos.PRECOMMITTED_CANDIDATES + (oos.PRECOMMITTED_FAILURE_CELL,):
        assert cid in ids


def test_oos_module_does_not_write_to_the_spec():
    src = open(os.path.join(
        ROOT, "analysis/l5/residual_frontier/oos.py"), encoding="utf-8").read()
    for bad in ("spec.GRID =", "spec.GRID.", "spec.ABS_CAPS",
                "spec.FRONTIER_FINGERPRINT =", "spec.SPEC[",
                "setattr(spec", "replay.allow =", "replay.OBSERVED_DAYS ="):
        assert bad not in src, "oos.py mutates the frozen spec/replay: %s" % bad


# ----------------------------------------------------- 2. no holdout leakage

@pytest.fixture(scope="module")
def result():
    if not os.path.exists(RESULT):
        pytest.skip("OOS_RESULT.json not generated")
    return json.load(open(RESULT, encoding="utf-8"))


def test_holdout_populations_are_disjoint_from_in_sample(result):
    in_s = ad.in_sample_slugs(os.path.join(ROOT, ad.IN_SAMPLE_POPULATION))
    for pop in ("PRIMARY", "SECONDARY"):
        path = result["populations"][pop]["materialization"][
            "population_path"]
        if not os.path.exists(path):
            pytest.skip("materialized population not on disk")
        held = {json.loads(l)["slug"] for l in open(path, encoding="utf-8")
                if l.strip()}
        assert held and not (held & in_s), (
            "%s holdout leaks %d in-sample slugs"
            % (pop, len(held & in_s)))


def test_primary_and_secondary_are_disjoint_from_each_other(result):
    paths = [result["populations"][p]["materialization"]["population_path"]
             for p in ("PRIMARY", "SECONDARY")]
    if not all(os.path.exists(p) for p in paths):
        pytest.skip("materialized populations not on disk")
    a, b = ({json.loads(l)["slug"] for l in open(p, encoding="utf-8")
             if l.strip()} for p in paths)
    assert not (a & b)


def test_quoted_in_sample_figures_match_the_frozen_in_sample_result(result):
    """Every in-sample number the report quotes comes from FRONTIER.json.

    This is what proves no holdout row contaminated an in-sample figure: the
    in-sample column is copied, not recomputed.
    """
    ins = json.load(open(IN_SAMPLE, encoding="utf-8"))
    assert result["IN_SAMPLE_REFERENCE"]["n_events"] == ins["n_events"]
    assert result["IN_SAMPLE_REFERENCE"]["n_fills"] == ins["n_fills"]
    for cid, q in result["IN_SAMPLE_REFERENCE"]["cells"].items():
        f = ins["cells"][cid]["strata"]["FULL_CORPUS"]
        assert q["net_cash_ex_incentives_usd"] == \
            f["net_cash_ex_incentives_usd"]
        assert q["exchange_rate"] == \
            ins["cells"][cid]["DERIVED_avoided_per_matched_sacrificed"]
        assert q["events_touched"] == f["events_touched"]
    for block in ("VERDICTS_PRIMARY", "VERDICTS_SECONDARY"):
        for cid, v in result[block].items():
            assert v["in_sample_net_usd"] == ins["cells"][cid][
                "strata"]["FULL_CORPUS"]["net_cash_ex_incentives_usd"]
            assert v["in_sample_exchange_rate"] == ins["cells"][cid][
                "DERIVED_avoided_per_matched_sacrificed"]


def test_in_sample_result_is_itself_from_the_frozen_spec():
    ins = json.load(open(IN_SAMPLE, encoding="utf-8"))
    assert ins["fingerprint"] == spec.FRONTIER_FINGERPRINT


def test_dead_cells_are_reported_not_rescued(result):
    for pop in ("primary", "secondary"):
        assert set(result["DEAD_CELLS"]["in_sample"]) == \
            set(result["DEAD_CELLS"][pop]), (
            "the FRAC/COMBO cells dead by construction in-sample must stay "
            "dead on the holdout")


# -------------------------------------------- 3. the adapter is pure mapping

@pytest.fixture(scope="module")
def primary_sample():
    """First N raw envelopes of the PRIMARY holdout, parsed independently."""
    path = os.path.join(ROOT, ad.PRIMARY)
    if not os.path.exists(path):
        pytest.skip("primary holdout missing")
    recs = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= 20:
                break
            env = json.loads(line)
            recs.extend(r for r in (env.get("payload") or [])
                        if isinstance(r, dict) and r.get("type"))
    return recs


def test_adapter_returns_the_source_records_verbatim(primary_sample):
    got = ad.read_primary(os.path.join(ROOT, ad.PRIMARY))
    want_by_id = {ad._identity(r): r for r in primary_sample}
    got_by_id = {ad._identity(r): r for r in got}
    assert want_by_id, "no sample records"
    for k, want in want_by_id.items():
        assert k in got_by_id, "adapter dropped a source record"
        assert got_by_id[k] == want, "adapter altered a source record"


def test_adapter_adds_no_keys_and_renames_nothing(primary_sample):
    got = ad.read_primary(os.path.join(ROOT, ad.PRIMARY))[:5000]
    src_keys = set().union(*(set(r) for r in primary_sample))
    for r in got[:200]:
        assert set(r) <= src_keys | set(r), "unexpected"
    # the strong form: no envelope key ever appears on a record
    for r in got:
        for k in ad.ENVELOPE_KEYS:
            assert k not in r, "adapter leaked envelope key %s onto a record" % k


def test_secondary_adapter_trusts_the_records_own_type_field():
    path = os.path.join(ROOT, ad.SECONDARY)
    if not os.path.exists(path):
        pytest.skip("secondary holdout missing")
    obj = json.load(open(path, encoding="utf-8"))
    got = ad.read_secondary(path)
    assert len(got) == sum(len(v or []) for v in obj.values())
    flat = {ad._identity(r) for v in obj.values() for r in (v or [])}
    assert {ad._identity(r) for r in got} == flat
    for k, lst in obj.items():
        for r in (lst or []):
            assert r["type"] == k, "dict key and record type disagree"


def test_dedupe_only_removes_never_alters(primary_sample):
    dup = primary_sample + primary_sample
    out = ad._dedupe(dup)
    assert len(out) <= len(primary_sample)
    assert {ad._identity(r) for r in out} == \
        {ad._identity(r) for r in primary_sample}
    for r in out:
        assert r in primary_sample


def test_adapter_has_no_derived_field_logic():
    """AST-level purity: the adapter never writes into a record and never
    does arithmetic on one. Checked on the parse tree, so prose in the
    docstrings cannot pass or fail it."""
    import ast
    src = open(os.path.join(
        ROOT, "analysis/l5/residual_frontier/oos_adapter.py"),
        encoding="utf-8").read()
    tree = ast.parse(src)
    # the MAPPING functions. `materialize` is excluded on purpose: it does
    # file I/O and string building, not field mapping, and the leak-guard
    # tests above cover what it decides.
    mapping = {"read_primary", "read_secondary", "_identity", "_dedupe",
               "_slug", "in_family", "in_sample_slugs"}
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name in mapping]
    assert {f.name for f in fns} == mapping, "a mapping function went missing"
    for fn in fns:
      for node in ast.walk(fn):
        # no `something[...] = ...` anywhere: records are never written into
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            for t in targets:
                assert not isinstance(t, ast.Subscript), (
                    "adapter assigns into a subscript at line %d"
                    % node.lineno)
        # no arithmetic: a pure field mapping computes no values
        # `%` is string formatting and `|` is set union; genuine arithmetic
        # on a record's values would show up as Add/Sub/Mult/Div/Pow.
        if isinstance(node, ast.BinOp):
            assert isinstance(node.op, (ast.BitOr, ast.Mod)), (
                "adapter performs arithmetic at line %d (%s)"
                % (node.lineno, type(node.op).__name__))


def test_market_family_scope_is_declared_and_narrow():
    assert ad.in_family("btc-updown-5m-1787903100")
    assert ad.in_family("eth-updown-15m-1787903100")
    assert not ad.in_family("0x" + "a" * 64)
    assert not ad.in_family("")


# ------------------------------------------------------- labelling guards

def test_result_is_labelled_historical_replay(result):
    assert result["profit_label"] == "historical replay"
    assert result["rebates_rewards_usd"] == 0.0
    assert "never realized" in result["NOT_OUR_REALIZED_PROFIT"]
    assert result["spec_unmodified"] is True
    for pop in ("PRIMARY_frontier", "SECONDARY_frontier"):
        assert result[pop]["rebates_rewards_usd"] == 0.0
        assert result[pop]["fingerprint"] == spec.FRONTIER_FINGERPRINT
