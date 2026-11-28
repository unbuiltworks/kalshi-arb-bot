"""The integrated inventory decisions, replayed with the existing controller
over the 187 recorded windows (method and caveats: entry_filter_study.py,
Part B). Pair-cost-aware completion pricing and the one-sided rule, alone
and together. ARITHMETIC REPLAY ON PUBLIC QUOTES -- not an executable
profit."""
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from analysis.l5.btc5m import entry_filter_study as E   # noqa: E402
from analysis.l5.btc5m.one_sided_value import load      # noqa: E402

CONFIGS = [
    ("live configuration", {}),
    ("completed pair <= 1.00", {"straddle_max_completed_pair_cost": D("1")}),
    ("completed pair <= 0.99",
     {"straddle_max_completed_pair_cost": D("0.99")}),
    ("completed pair <= 0.97",
     {"straddle_max_completed_pair_cost": D("0.97")}),
    ("completed pair <= 1.00 + one-sided rule",
     {"straddle_max_completed_pair_cost": D("1"),
      "straddle_one_sided_control": True}),
    ("completed pair <= 0.99 + one-sided rule",
     {"straddle_max_completed_pair_cost": D("0.99"),
      "straddle_one_sided_control": True}),
    ("completed pair <= 1.00 + one-sided + unmatched cap $0.03",
     {"straddle_max_completed_pair_cost": D("1"),
      "straddle_one_sided_control": True,
      "straddle_max_unmatched_usd": D("0.03")}),
]
E.CONFIGS = CONFIGS
data = load()
names = list(data)
print("windows: %d" % len(names))
E.part_b(data, names, print)
