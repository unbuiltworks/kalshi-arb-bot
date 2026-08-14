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


def test_registry_pairs_are_shadow_enabled():
    pairs = load_pairs(Path("config/cross_market_shadow_pairs.json"))
    assert pairs
    by_id = {pair.pair_id: pair for pair in pairs}
    assert "oscars-2027-best-picture-dune-part-three" in by_id
    assert "us-unemployment-2026-aug-above-4p2" in by_id
    assert all(
        pair.settlement_status in {
            "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
            "FULL_RULES_AUDITED_SHADOW",
        }
        for pair in pairs
    )
