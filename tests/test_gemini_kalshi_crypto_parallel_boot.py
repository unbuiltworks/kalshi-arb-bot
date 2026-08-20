from pathlib import Path

import pytest

from src.gemini_kalshi_crypto_parallel_boot import ensure_fresh_campaign_state


def test_parallel_boot_allows_new_campaign(tmp_path: Path):
    path = ensure_fresh_campaign_state("gkp-test-new", tmp_path)
    assert path == tmp_path / "gkp-test-new.json"
    assert not path.exists()


def test_parallel_boot_refuses_existing_campaign_state(tmp_path: Path):
    path = tmp_path / "gkp-test-existing.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing restart/redeploy"):
        ensure_fresh_campaign_state("gkp-test-existing", tmp_path)
