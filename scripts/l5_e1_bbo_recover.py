"""Read back the BBO-semantics captures from the volume. READ-ONLY.

WHY A SCRIPT AND NOT AN INLINE COMMAND. The captures are written to the
mounted volume precisely because Railway drops logs above 500/sec, and
reading them back through a multi-line `python3 -c` start command is
exactly the kind of fragile step that produced three silent no-output
runs. A file in the image is deterministic.

It opens files, prints, and does nothing else: no network, no credential,
no transport, no order module. `sys.modules` is checked afterwards to
prove it.

Run:
    python3 -m scripts.l5_e1_bbo_recover
"""
from __future__ import annotations

import glob
import json
import os
import sys

DIR = os.getenv("PMUS_E1_BBO_DIR") or "/state/pmus-ws/e1_bbo_semantics"

#: nothing here may be loaded by a file reader.
FORBIDDEN = ("analysis.l5.live_exec2.orders", "src.l5_order_body",
             "src.l5_preview_gate", "src.pmus_auth",
             "src.pmus_q1_executor", "analysis.l5.e1_cohort.latch")

#: the keys worth printing, in order. Everything else stays in the file.
KEYS = ("slug", "result", "seconds_into_window",
        "waited_for_next_window_s", "why_waited", "window_start")

COMPARISON_KEYS = ("VERDICT", "proven_lineage", "authbook", "skew_s",
                   "bid_agrees", "ask_agrees",
                   "skew_is_small_enough_for_a_disagreement_to_mean_anything",
                   "yes_buy_maker_price", "no_buy_maker_price",
                   "complement_arithmetic_against_venue_data",
                   "distinct_unit_interval_values",
                   "the_chosen_bid_is_one_of_them")


def _line(tag: str, value: object, cap: int = 4000) -> None:
    s = json.dumps(value, default=str, sort_keys=True)
    if len(s) > cap:
        s = s[:cap] + "...TRUNCATED"
    print("E1R %s=%s" % (tag, s))


def main() -> int:
    files = sorted(glob.glob(os.path.join(DIR, "*.json")))
    _line("dir", DIR)
    _line("files", files)
    if not files:
        _line("result", "NO_CAPTURES_ON_THE_VOLUME")
        _assert_clean()
        return 2
    for path in files[-3:]:
        print("E1R ======== %s" % path)
        try:
            with open(path, encoding="utf8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as exc:
            _line("unreadable", "%s: %s" % (type(exc).__name__, exc))
            continue
        for k in KEYS:
            _line(k, d.get(k))
        comp = d.get("comparison") or {}
        for k in COMPARISON_KEYS:
            _line("cmp." + k, comp.get(k))
        ws = d.get("ws") or {}
        _line("ws.health", ws.get("health"))
        _line("ws.snapshot", ws.get("snapshot"))
        fe = ws.get("frame_evidence") or {}
        for k in ("connected", "book_updates", "both_sides_served",
                  "subscribed"):
            _line("ws." + k, fe.get(k))
        _line("ws.last_bbo_history", fe.get("last_bbo_history"))
        _line("ws.pre_book", fe.get("pre_book_shapes_for_this_slug"), 6000)
        rest = d.get("rest_after_ws") or d.get("rest") or {}
        _line("rest.bbo", (rest.get("bbo") or {}).get("raw"), 6000)
        _line("rest.ladders", (rest.get("book") or {}).get("ladders"), 6000)
        _line("rest.book", (rest.get("book") or {}).get("raw"), 6000)
        _line("safety", {"venue_post": d.get("venue_post"),
                         "latch_touched": d.get("latch_touched"),
                         "order_constructed": d.get("order_constructed")})
    _assert_clean()
    return 0


def _assert_clean() -> None:
    present = sorted(m for m in FORBIDDEN if m in sys.modules)
    if present:
        raise SystemExit("REFUSED: a file reader loaded %r" % (present,))
    _line("reader_loaded_no_order_or_credential_module", True)


if __name__ == "__main__":
    raise SystemExit(main())
