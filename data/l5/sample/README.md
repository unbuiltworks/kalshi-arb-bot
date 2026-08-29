# Synthetic samples — NOT real data

Every `SYNTHETIC_*.csv` in this directory was generated from the fixture in
`tests/test_l5_pipeline.py`, not from Polymarket. They exist only to pin the
column schema so the format can be reviewed before a live run.

**Do not analyse them.** The numbers are made up. Real outputs land in
`data/l5/` with the unprefixed filenames after `python -m src.l5 all`.

Regenerate with the snippet in `README_FOR_ANALYSIS.md` § Reproducing the samples.
