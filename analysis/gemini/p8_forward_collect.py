"""Collect one UTC day of Gemini BTC 5m/15m/1h tape into the forward-test set.

WHY THIS EXISTS. 2026-09-21..22 was designated OOS and has now been inspected
repeatedly -- once to report P7, and again every time a threshold was
discussed against it. It is no longer pristine and must not be treated as a
clean test. This collector accumulates 2026-09-23 onward, which is NOT used
for development, so a genuinely untouched forward test exists later.

USAGE:  python3 analysis/gemini/p8_forward_collect.py 2026-09-23

It writes analysis/gemini/forward/<day>.json and nothing else. It reads only
public endpoints and places no order.
"""
import json, os, sys, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..")))
import p8_bulk as B                                      # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forward")

#: The last day that has ALREADY been inspected. 09-16..20 were development
#: and validation; 09-21..22 were designated OOS and have since been looked at
#: repeatedly. Nothing at or before this date may enter the forward set, or the
#: holdout silently stops being a holdout -- the exact failure this directory
#: exists to prevent.
LAST_EXPOSED = dt.date(2026, 9, 22)


def collect(day: str) -> str:
    d = dt.date.fromisoformat(day)
    if d <= LAST_EXPOSED:
        raise SystemExit(
            f"REFUSED: {day} is at or before {LAST_EXPOSED.isoformat()}, which "
            f"has already been inspected. The forward set holds only genuinely "
            f"untouched days.")
    os.makedirs(OUT, exist_ok=True)
    rows = B.pull_day(day)
    path = os.path.join(OUT, f"{day}.json")
    json.dump(rows, open(path, "w"))
    return path


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else (
        dt.date.today() - dt.timedelta(days=1)).isoformat()
    p = collect(day)
    n = len(json.load(open(p)))
    print(f"{day}: {n} contracts -> {p}")
