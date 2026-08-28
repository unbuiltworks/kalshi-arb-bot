from pathlib import Path


def test_rule_evidence_snapshot_uses_documented_polymarket_slug_metadata_route() -> None:
    source = Path("src/mlb_prop_rule_evidence_snapshot.py").read_text()
    assert '/v1/market/slug/{candidate.poly_slug}' in source
    assert '/v1/markets/{candidate.poly_slug}' not in source
