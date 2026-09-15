"""The preregistered promotion gate, and the ways a gate quietly stops gating.

A promotion gate written after seeing the data is not a gate, it is a
description. PROMOTION_GATE_PREREGISTERED.md fixes every number before the
observations exist; this module's first job is to prove the code still reports
THOSE numbers, and its second is to prove the gate fails closed -- an absent
measurement is never a pass.
"""
import json
import os

import pytest

import analysis.l5.chain_observer.gate as G

DOC = os.path.join(os.path.dirname(G.__file__),
                   "PROMOTION_GATE_PREREGISTERED.md")


# ------------------------------------------------- the numbers are the doc's

def test_every_threshold_in_the_code_appears_in_the_preregistration():
    """The anti-goalpost test. If a constant is tuned, this fails until the
    preregistration is amended -- which is a visible commit, as intended."""
    md = open(DOC).read()
    assert "≥ 500" in md and G.MIN_JOINED == 500
    assert "≥ 99.5 %" in md and G.MIN_PRICE_AGREE == 0.995
    assert G.MIN_SIZE_AGREE == 0.995
    assert "≥ 64 blocks" in md and G.CONFIRMATIONS == 64
    assert G.QUALIFYING_FROM_COMMIT in md


def test_the_permitted_categories_are_the_documents_categories():
    """A category added in code but not in the preregistration is a goalpost
    moved. `window_edge` was added, and the document records the amendment."""
    from src.l5_chain_observer import classify as CL
    md = open(DOC).read()
    for c in CL.CATEGORIES:
        assert f"`{c}`" in md, c
    assert "Amendment" in md
    assert "No threshold moved" in md


def test_the_preregistration_scopes_promotion_to_the_observation_clock():
    """Promotion must not be readable as live-trading authorization."""
    md = open(DOC).read().lower()
    assert "does not enable live trading" in md
    assert "shadow\nobservation clock" in md or "shadow observation clock" in md


def test_the_validator_uses_the_same_confirmation_depth_as_the_gate():
    import analysis.l5.chain_observer.validate as V
    assert V.CONFIRMATIONS == G.CONFIRMATIONS


# ------------------------------------------------------------ the fixtures

def _stamp(row):
    """Rows the validator writes carry the measurement epoch; fixtures that do
    not care about it get the qualifying one so the gate sees them at all."""
    return {"epoch": G.QUALIFYING_EPOCH, **row}


def _root(tmp_path, *, runs=(), attr=(), coverage=None, classified=()):
    vd = tmp_path / "chainval"
    vd.mkdir(parents=True, exist_ok=True)
    for name, rows in (("chain_only_classified.jsonl", classified),
                       ("validations.jsonl", runs),
                       ("attribution.jsonl", attr)):
        with open(vd / name, "w") as fh:
            for r in rows:
                fh.write(json.dumps(_stamp(r)) + "\n")
    if coverage is not None:
        with open(tmp_path / "coverage.jsonl", "w") as fh:
            for c in coverage:
                fh.write(json.dumps(c) + "\n")
    return str(tmp_path)


def _clean_run(agree=500, epoch=None):
    return {"ok": True, "agree": agree, "feed_only": [], "chain_only": [],
            "epoch": G.QUALIFYING_EPOCH if epoch is None else epoch,
            "code": "abc1234"}


def _good_attr(n=30, conf=100):
    return [{"tx": f"0x{i:x}", "blocks": [1], "log_indexes": [0],
             "contract": "0xe111", "confirmations": conf,
             "epoch": G.QUALIFYING_EPOCH,
             "price_agrees": True, "size_agrees": True,
             "matched_group": {"event": "0xd543adfd", "l5_topic_pos": 2,
                               "token_id": "7"}}
            for i in range(n)]


def _good_coverage():
    return [{"kind": "boot", "t": 1000.0, "stream": "__supervisor__"},
            {"kind": "heartbeat", "t": 2000.0, "stream": "__supervisor__"},
            {"kind": "heartbeat", "t": 3000.0, "stream": "chain_validate"}]


# ------------------------------------------------------- it fails closed

def test_an_empty_state_root_does_not_promote(tmp_path):
    r = G.report(_root(tmp_path))
    assert r["PROMOTE"] is False
    assert "G-C1_sample_size" in r["blocking"]
    assert "G-C8_provenance" in r["blocking"]


def test_no_measurement_is_not_a_pass(tmp_path):
    """Every gate with nothing behind it must be blocking, never silent."""
    r = G.report(_root(tmp_path))
    for k, v in r["gates"].items():
        if v["pass"] is True:
            # only gates whose evidence is an ABSENCE of bad rows may pass
            # vacuously, and they are named here deliberately
            assert k in ("G-C2_no_unexplained_misses",
                         "G-C3_chain_only_classified",
                         "G-C6_rpc_failure_is_unknown"), k
    # and the one that CAN pass vacuously says so rather than looking earned
    assert r["gates"]["G-C3_chain_only_classified"]["evidence"]["exercised"] \
        is False


def test_one_short_of_the_sample_threshold_still_fails(tmp_path):
    r = G.report(_root(tmp_path, runs=[_clean_run(G.MIN_JOINED - 1)]))
    assert r["gates"]["G-C1_sample_size"]["pass"] is False
    assert r["gates"]["G-C1_sample_size"]["evidence"]["joined"] == 499


def test_failed_passes_contribute_no_joined_transactions(tmp_path):
    """G-C6: a pass that could not be run says nothing about agreement."""
    r = G.report(_root(tmp_path, runs=[
        _clean_run(400),
        {"ok": False, "err": "RpcError", "agree": 400,
         "epoch": G.QUALIFYING_EPOCH}]))
    assert r["gates"]["G-C1_sample_size"]["evidence"]["joined"] == 400


def test_a_failed_pass_carrying_agreement_figures_fails_g_c6(tmp_path):
    r = G.report(_root(tmp_path, runs=[
        {"ok": False, "err": "boom", "agreement_rate": 1.0,
         "epoch": G.QUALIFYING_EPOCH}]))
    assert r["gates"]["G-C6_rpc_failure_is_unknown"]["pass"] is False


def test_a_single_feed_only_transaction_blocks_promotion(tmp_path):
    """A missed fill is indistinguishable from L5 not trading."""
    r = G.report(_root(tmp_path, runs=[
        {"ok": True, "agree": 9999, "feed_only": ["0xmissed"],
         "chain_only": [], "epoch": G.QUALIFYING_EPOCH}]))
    assert r["gates"]["G-C2_no_unexplained_misses"]["pass"] is False
    assert r["gates"]["G-C2_no_unexplained_misses"]["evidence"]["examples"] \
        == ["0xmissed"]


def test_an_unclassified_chain_only_transaction_blocks_promotion(tmp_path):
    """A counted case with no row means the classifier never saw it."""
    r = G.report(_root(tmp_path, runs=[
        {"ok": True, "agree": 1, "feed_only": [],
         "chain_only": ["0xa"], "epoch": G.QUALIFYING_EPOCH}]))
    g = r["gates"]["G-C3_chain_only_classified"]
    assert g["pass"] is False
    assert g["evidence"]["unaccounted"] == 1
    assert g["evidence"]["unaccounted_examples"] == ["0xa"]


def test_one_unresolved_chain_only_case_blocks_promotion(tmp_path):
    """The dangerous case: the chain reports a fill the venue does not."""
    r = G.report(_root(tmp_path,
                       runs=[{"ok": True, "agree": 1, "feed_only": [],
                              "chain_only": ["0xa", "0xb"],
                              "epoch": G.QUALIFYING_EPOCH}],
                       classified=[
                           {"tx": "0xa", "category": "publication_lag",
                                    "epoch": G.QUALIFYING_EPOCH},
                           {"tx": "0xb", "category": "unresolved"}]))
    g = r["gates"]["G-C3_chain_only_classified"]
    assert g["pass"] is False
    assert g["evidence"]["unresolved"] == 1


def test_a_category_invented_after_the_fact_blocks_promotion(tmp_path):
    """Naming a cause `probably_fine` is not classifying it."""
    r = G.report(_root(tmp_path,
                       runs=[{"ok": True, "agree": 1, "feed_only": [],
                              "chain_only": ["0xa"]}],
                       classified=[{"tx": "0xa", "category": "probably_fine",
                                    "epoch": G.QUALIFYING_EPOCH}]))
    g = r["gates"]["G-C3_chain_only_classified"]
    assert g["pass"] is False
    assert g["evidence"]["outside_permitted_categories"] == 1


def test_fully_classified_chain_only_cases_pass_and_are_marked_exercised(
        tmp_path):
    r = G.report(_root(tmp_path,
                       runs=[{"ok": True, "agree": 1, "feed_only": [],
                              "chain_only": ["0xa", "0xb"],
                              "epoch": G.QUALIFYING_EPOCH}],
                       classified=[
                           {"tx": "0xa", "category": "publication_lag",
                                    "epoch": G.QUALIFYING_EPOCH},
                           {"tx": "0xb", "category": "window_edge"}]))
    g = r["gates"]["G-C3_chain_only_classified"]
    assert g["pass"] is True
    assert g["evidence"]["exercised"] is True
    assert g["evidence"]["by_category"] == {"publication_lag": 1,
                                            "window_edge": 1}


def test_one_attribution_mismatch_fails_the_gate_however_small(tmp_path):
    """99.5% is the floor, but 0 UNEXPLAINED is the binding condition."""
    attr = _good_attr(400) + [{
        "tx": "0xbad", "blocks": [1], "log_indexes": [0], "contract": "0xe111",
        "confirmations": 100, "price_agrees": False, "size_agrees": True}]
    r = G.report(_root(tmp_path, attr=attr))
    g = r["gates"]["G-C4_attribution"]
    assert g["pass"] is False
    assert g["evidence"]["price_rate"] > G.MIN_PRICE_AGREE   # above the floor
    assert g["evidence"]["unexplained_mismatches"] == 1


def test_an_unconfirmed_transaction_blocks_finality(tmp_path):
    r = G.report(_root(tmp_path, attr=_good_attr(5, conf=G.CONFIRMATIONS - 1)))
    g = r["gates"]["G-C5_finality"]
    assert g["pass"] is False
    assert g["evidence"]["below_confirmations"] == 5


def test_finality_fails_even_when_every_transaction_is_deep_enough(tmp_path):
    """Depth is only half of G-C5; retraction-on-reorg is not built."""
    r = G.report(_root(tmp_path, attr=_good_attr(5, conf=1000)))
    g = r["gates"]["G-C5_finality"]
    assert g["evidence"]["below_confirmations"] == 0
    assert g["pass"] is False
    assert g["evidence"]["reorg_retraction_implemented"] is False


def test_a_transaction_missing_provenance_fails_g_c8(tmp_path):
    attr = _good_attr(3) + [{"tx": "0xnoprov", "confirmations": 100,
                             "price_agrees": True, "size_agrees": True}]
    r = G.report(_root(tmp_path, attr=attr))
    assert r["gates"]["G-C8_provenance"]["pass"] is False
    assert r["gates"]["G-C8_provenance"]["evidence"]["missing_provenance"] == 1


def test_coverage_is_unavailable_rather_than_assumed_when_unmarked(tmp_path):
    r = G.report(_root(tmp_path))
    g = r["gates"]["G-C7_coverage"]
    assert g["pass"] is False
    assert g["evidence"]["available"] is False


def test_a_stream_still_down_blocks_coverage(tmp_path):
    cov = _good_coverage() + [
        {"kind": "stream_exit", "t": 4000.0, "stream": "chain_validate"}]
    r = G.report(_root(tmp_path, coverage=cov))
    assert r["gates"]["G-C7_coverage"]["pass"] is False


# --------------------------------------------------- and it is not vacuous

def test_the_gate_can_actually_pass_once_the_unbuilt_parts_exist(
        tmp_path, monkeypatch):
    """A gate that cannot be satisfied by any input teaches nothing. With the
    two unimplemented pieces stubbed, a clean sample promotes -- which is what
    makes the FAILs above informative rather than structural."""
    monkeypatch.setattr(G, "_reorg_tested", lambda: True)
    r = G.report(_root(tmp_path,
                       runs=[{"ok": True, "agree": 500, "feed_only": [],
                              "chain_only": ["0xa"],
                              "epoch": G.QUALIFYING_EPOCH}],
                       classified=[{"tx": "0xa",
                                    "category": "publication_lag"}],
                       attr=_good_attr(30), coverage=_good_coverage()))
    assert r["blocking"] == []
    assert r["PROMOTE"] is True
    assert r["gates_passing"] == r["gates_total"] == 8


def test_promotion_needs_every_gate_not_a_majority(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_reorg_tested", lambda: True)
    r = G.report(_root(tmp_path,
                       runs=[{"ok": True, "agree": 499, "feed_only": [],
                              "chain_only": ["0xa"],
                              "epoch": G.QUALIFYING_EPOCH}],
                       classified=[{"tx": "0xa",
                                    "category": "publication_lag"}],
                       attr=_good_attr(30), coverage=_good_coverage()))
    assert r["gates_passing"] == 7 and r["PROMOTE"] is False
    assert "partial" in r["labels"]


def test_the_report_names_the_preregistration_it_is_measuring(tmp_path):
    r = G.report(_root(tmp_path))
    assert r["preregistered"].endswith("PROMOTION_GATE_PREREGISTERED.md")
    assert os.path.exists(DOC)


def test_the_observer_is_still_unpromoted():
    """The gate reports; it never flips the switch itself."""
    from src.l5_chain_observer import observer as O
    assert O.PROMOTED is False


# ------------------------------- rows from superseded code are not the sample

def test_rows_measured_by_superseded_code_are_discarded_not_counted(tmp_path):
    """Live case: rows reading 240 @ 0.35 for a fill the venue reported as
    80 @ 0.05, written before chain legs were grouped by L5's role, sat in the
    same file as corrected ones and failed G-C4 on superseded code."""
    stale = [{"tx": "0xold", "epoch": G.QUALIFYING_EPOCH - 1,
              "blocks": [1], "log_indexes": [0], "contract": "0xe111",
              "confirmations": 100, "price_agrees": False,
              "size_agrees": False}]
    r = G.report(_root(tmp_path, attr=stale + _good_attr(10)))
    assert r["excluded_as_pre_qualifying"]["attribution_rows"] == 1
    assert r["gates"]["G-C4_attribution"]["evidence"]["attributed"] == 10
    assert r["gates"]["G-C4_attribution"]["pass"] is True


def test_an_unstamped_row_predates_the_stamp_and_is_excluded(tmp_path):
    vd = tmp_path / "chainval"
    vd.mkdir(parents=True)
    with open(vd / "validations.jsonl", "w") as fh:
        fh.write(json.dumps({"ok": True, "agree": 9999}) + "\n")
    r = G.report(str(tmp_path))
    assert r["gates"]["G-C1_sample_size"]["evidence"]["joined"] == 0
    assert r["excluded_as_pre_qualifying"]["runs"] == 1


def test_a_pre_qualifying_pass_cannot_contribute_a_promotion_blocker_either(
        tmp_path):
    """Exclusion cuts both ways: a stale row is not evidence FOR or AGAINST."""
    r = G.report(_root(tmp_path, runs=[
        {"ok": True, "agree": 1, "feed_only": ["0xold"],
         "epoch": G.QUALIFYING_EPOCH - 1}]))
    assert r["gates"]["G-C2_no_unexplained_misses"]["evidence"]["feed_only"] \
        == 0


def test_the_code_version_of_the_counted_sample_is_reported(tmp_path):
    """G-C8: which commit measured the sample must be re-derivable."""
    r = G.report(_root(tmp_path, runs=[_clean_run(500)]))
    assert r["gates"]["G-C1_sample_size"]["evidence"]["code_versions"] \
        == ["abc1234"]


def test_the_epoch_mechanism_is_recorded_in_the_preregistration():
    md = open(DOC).read()
    assert "QUALIFYING_EPOCH = 3" in md
    assert "EPOCH" in md and "tightens G-C1" in md


def test_the_validator_stamps_the_epoch_the_gate_enforces():
    import analysis.l5.chain_observer.validate as V
    assert V.EPOCH == G.QUALIFYING_EPOCH
