#!/usr/bin/env python3
"""Regenerate the SYNTHETIC sample outputs under analysis/l5/sample_synthetic/.

These files exist so a reader can see the exact shape of the three study CSVs
without network access. The numbers are invented by tests/fixtures/l5_synthetic.py
and describe nobody. They are NOT the subject wallet's data.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.polymarket_l5 import config  # noqa: E402
from src.polymarket_l5.analyze import analyze  # noqa: E402
from src.polymarket_l5.export import build  # noqa: E402
from src.polymarket_l5.findings import write_findings  # noqa: E402
from tests.fixtures.l5_synthetic import standard_scenario  # noqa: E402

BANNER = """# SYNTHETIC SAMPLE -- NOT REAL DATA

Everything in this directory is generated from `tests/fixtures/l5_synthetic.py`
by `scripts/l5_generate_synthetic_sample.py`. Two invented windows (one BTC 5m,
one ETH 15m) plus two deliberately out-of-scope markets exercise the exporter so
the column layout, the cumulative inventory columns and the summary statistics
can be inspected offline.

These files describe **no real trader**. The real study outputs live one level
up in `analysis/l5/` and are produced by
`python -m src.polymarket_l5.cli all`.
"""


def main() -> int:
    target = config.OUTPUT_DIR / "sample_synthetic"
    work = target / ".work"
    if target.exists():
        shutil.rmtree(target)
    work.mkdir(parents=True)

    cache = standard_scenario(work / "raw")
    build(cache=cache, output_dir=work / "out")
    results = analyze(work / "out")
    write_findings(
        results,
        {"fetch_status": "synthetic-fixture", "fetch_note": "generated from test fixtures"},
        output_dir=work / "out",
        findings_path=target / "SYNTHETIC_L5_STRATEGY_FINDINGS.md",
    )

    for name in (
        config.TRADES_CSV,
        config.SEQUENCES_CSV,
        config.SUMMARY_CSV,
        config.EXCLUSIONS_CSV,
        config.STATS_JSON,
    ):
        shutil.copyfile(work / "out" / name, target / f"SYNTHETIC_{name}")
    (target / "README.md").write_text(BANNER, encoding="utf-8")
    shutil.rmtree(work)
    print(f"wrote synthetic sample to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
