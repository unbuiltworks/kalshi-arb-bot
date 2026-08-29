# SYNTHETIC SAMPLE -- NOT REAL DATA

Everything in this directory is generated from `tests/fixtures/l5_synthetic.py`
by `scripts/l5_generate_synthetic_sample.py`. Two invented windows (one BTC 5m,
one ETH 15m) plus two deliberately out-of-scope markets exercise the exporter so
the column layout, the cumulative inventory columns and the summary statistics
can be inspected offline.

These files describe **no real trader**. The real study outputs live one level
up in `analysis/l5/` and are produced by
`python -m src.polymarket_l5.cli all`.
