import json
from pathlib import Path

import pytest

from src.sports_kgp_micro_approvals import MicroApproval, load_micro_approvals

FP = "b" * 64


def row(**overrides):
    value = {
        "settlement_key": "baseball|mlb|PHI-NYM|moneyline|PHI",
        "venues": ["kalshi", "gemini"],
        "market_ids": {"kalshi": "KX-TEST", "gemini": "GEM-TEST"},
        "canonical_sides": {"kalshi": "YES", "gemini": "YES"},
        "rule_fingerprint_sha256": FP,
        "approved_for_execution": True,
    }
    value.update(overrides)
    return value


def test_all_prediction_pairs_validate():
    for venues in (["kalshi", "gemini"], ["kalshi", "polymarket"], ["gemini", "polymarket"]):
        market_ids = {v: f"{v}-id" for v in venues}
        sides = {v: "YES" for v in venues}
        approval = MicroApproval.from_mapping(row(venues=venues, market_ids=market_ids, canonical_sides=sides))
        approval.validate()


def test_sportsbook_pair_rejected():
    approval = MicroApproval.from_mapping(row(
        venues=["fanduel", "kalshi"],
        market_ids={"fanduel": "fd", "kalshi": "k"},
        canonical_sides={"fanduel": "YES", "kalshi": "YES"},
    ))
    with pytest.raises(ValueError, match="prediction-market pair"):
        approval.validate()


def test_exact_market_and_side_sets_required():
    approval = MicroApproval.from_mapping(row(market_ids={"kalshi": "KX-TEST"}))
    with pytest.raises(ValueError, match="market_ids"):
        approval.validate()
    approval = MicroApproval.from_mapping(row(canonical_sides={"kalshi": "YES"}))
    with pytest.raises(ValueError, match="canonical_sides"):
        approval.validate()


def test_rule_fingerprint_and_execution_flag_required():
    approval = MicroApproval.from_mapping(row(rule_fingerprint_sha256="bad"))
    with pytest.raises(ValueError, match="SHA-256"):
        approval.validate()
    approval = MicroApproval.from_mapping(row(approved_for_execution=False))
    with pytest.raises(PermissionError):
        approval.validate()


def test_loader_skips_unapproved_and_rejects_duplicate(tmp_path: Path):
    p = tmp_path / "approvals.json"
    unapproved = row(approved_for_execution=False)
    p.write_text(json.dumps({"approvals": [unapproved]}), encoding="utf-8")
    assert load_micro_approvals(p) == {}

    approved = row()
    p.write_text(json.dumps({"approvals": [approved, approved]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate approval"):
        load_micro_approvals(p)
