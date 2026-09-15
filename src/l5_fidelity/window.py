"""Window slug parsing, stdlib-only, so the 24/7 runner carries no analysis
package and no raw-tape dependency. Same regex and durations as
analysis/l5/redemption/audit.py; a test pins that they agree."""
import re

WINDOW_RE = re.compile(r"^(btc|eth)-updown-(5m|15m|1h)-(\d+)$", re.I)
DURATION_S = {"5m": 300, "15m": 900, "1h": 3600}
