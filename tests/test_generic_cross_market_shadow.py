import json
from pathlib import Path

import pytest

from src.generic_cross_market_shadow import load_pairs


def test_load_pairs_requires_allowed_shadow_status(tmp_path: Path):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps({"pairs": [{
        "pair_id": "p1",
        "category": "culture",
        "poly_slug": "poly",
        "kalshi_ticker": "kalshi",
        "settlement_status": "NOT_A_REAL_STATUS",
        "enabled_shadow": True,
    }]}))
    with pytest.raises(ValueError):
        load_pairs(path)


def test_registry_contains_expected_shadow_pair():
    pairs = load_pairs(Path("config/cross_market_shadow_pairs.json"))
    by_id = {pair.pair_id: pair for pair in pairs}
    assert "oscars-2027-best-picture-dune-part-three" in by_id
    assert by_id["oscars-2027-best-picture-dune-part-three"].settlement_status == (
        "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
    )
    assert all(pair.settlement_status in {
        "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        "FULL_RULES_AUDITED_SHADOW",
    } for pair in pairs)
