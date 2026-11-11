"""Replay the bounded taker policy against the Stage A campaign's own records.

Every `decision` below is a verbatim `L5_TS_COMPLETION` record from the
2026-10-30 campaign (deployment 9b43b312, commit 0304fbe8), with the held
inventory as that same record reported it. Nothing is modelled except the
policy's own answer.

The question this answers: does the policy fire on the two stranded legs that
had a real exit, and stay silent on the two that did not?

    python3 -I analysis/l5/stage_a/replay_taker_policy.py
"""

from __future__ import annotations

import sys
from decimal import Decimal as D

sys.path.insert(0, ".")

from src import l5_taker_completion_policy as TCP     # noqa: E402

#: (label, decision-as-recorded, held inventory, maker-first facts)
SAMPLES = [
    # ---- 2145z: the headline case. Sustained ~6.5 minutes.
    ("2145z 21:48:23 first both-positive",
     {"ok": True, "cross": True, "side": "UP", "qty": "2", "price": "0.4300",
      "deficit": "2", "displayed_depth": "851.2500", "partial": False,
      "worst_before": "-0.9400", "worst_after": "0.1600",
      "both_positive_after": True, "taker_fee_usd": "0.04"},
     {"up": "0", "dn": "2", "up_px": "0", "dn_px": "0.4500"},
     {"attempted": True, "rested_s": 173.0}),
    ("2145z 21:51:36 depth-starved (1.00 displayed)",
     {"ok": True, "cross": True, "side": "UP", "qty": "1.00",
      "price": "0.1000", "deficit": "2", "displayed_depth": "1.0000",
      "partial": True, "worst_before": "-0.9400",
      "worst_after": "-0.050000", "both_positive_after": False,
      "taker_fee_usd": "0.01"},
     {"up": "0", "dn": "2", "up_px": "0", "dn_px": "0.4500"},
     {"attempted": True, "rested_s": 366.0}),
    ("2145z 21:51:49 the $0.22 for +$0.84 cross",
     {"ok": True, "cross": True, "side": "UP", "qty": "2", "price": "0.1000",
      "deficit": "2", "displayed_depth": "126.0000", "partial": False,
      "worst_before": "-0.9400", "worst_after": "0.8400",
      "both_positive_after": True, "taker_fee_usd": "0.02"},
     {"up": "0", "dn": "2", "up_px": "0", "dn_px": "0.4500"},
     {"attempted": True, "rested_s": 379.0}),
    ("2145z 21:51:49 but maker never posted",
     {"ok": True, "cross": True, "side": "UP", "qty": "2", "price": "0.1000",
      "deficit": "2", "displayed_depth": "126.0000", "partial": False,
      "worst_before": "-0.9400", "worst_after": "0.8400",
      "both_positive_after": True, "taker_fee_usd": "0.02"},
     {"up": "0", "dn": "2", "up_px": "0", "dn_px": "0.4500"},
     {"attempted": False, "rested_s": 0.0}),
    ("2145z 21:51:49 but only 20s of maker patience",
     {"ok": True, "cross": True, "side": "UP", "qty": "2", "price": "0.1000",
      "deficit": "2", "displayed_depth": "126.0000", "partial": False,
      "worst_before": "-0.9400", "worst_after": "0.8400",
      "both_positive_after": True, "taker_fee_usd": "0.02"},
     {"up": "0", "dn": "2", "up_px": "0", "dn_px": "0.4500"},
     {"attempted": True, "rested_s": 20.0}),
    # ---- 2130z: real but intermittent, 22 of 49 samples positive.
    ("2130z 21:31:46 first both-positive (marginal)",
     {"ok": True, "cross": True, "side": "DOWN", "qty": "2.05",
      "price": "0.4500", "deficit": "2.05", "displayed_depth": "20.0100",
      "partial": False, "worst_before": "-0.983000",
      "worst_after": "0.084000", "both_positive_after": True,
      "taker_fee_usd": "0.04"},
     {"up": "2.05", "dn": "0", "up_px": "0.4600", "dn_px": "0"},
     {"attempted": True, "rested_s": 120.0}),
    ("2130z 21:37:53 best observed",
     {"ok": True, "cross": True, "side": "DOWN", "qty": "2.05",
      "price": "0.3100", "deficit": "2.05", "displayed_depth": "96.6500",
      "partial": False, "worst_before": "-0.983000",
      "worst_after": "0.391500", "both_positive_after": True,
      "taker_fee_usd": "0.03"},
     {"up": "2.05", "dn": "0", "up_px": "0.4600", "dn_px": "0"},
     {"attempted": True, "rested_s": 467.0}),
    # ---- 2115z: NEVER positive. Best observed worst_after -0.0400.
    ("2115z 21:18:59 best observed (negative)",
     {"ok": True, "cross": True, "side": "UP", "qty": "1.72",
      "price": "0.7900", "deficit": "1.72", "displayed_depth": "135.0100",
      "partial": False, "worst_before": "-0.381200",
      "worst_after": "-0.040000", "both_positive_after": False,
      "taker_fee_usd": "0.02"},
     {"up": "0", "dn": "1.72", "up_px": "0", "dn_px": "0.2100"},
     {"attempted": True, "rested_s": 200.0}),
    # ---- 2200z: 0 of 58 positive, complement pinned near 0.99.
    ("2200z complement at 0.99 (refused upstream)",
     {"ok": False, "reason": "COMPLETION_PRICE_OUT_OF_BAND",
      "side": "UP", "price": "0.9900"},
     {"up": "0", "dn": "1.85", "up_px": "0", "dn_px": "0.3300"},
     {"attempted": True, "rested_s": 400.0}),
]


def run() -> int:
    print("Bounded taker policy vs. the Stage A campaign's own records")
    print("margin floor %s | max outlay %s | window cap %s | "
          "maker patience %ss | depth haircut %s\n" % (
              TCP.DEFAULT_MARGIN_FLOOR_USD, TCP.DEFAULT_MAX_OUTLAY_USD,
              TCP.DEFAULT_WINDOW_OUTLAY_USD, TCP.DEFAULT_MAKER_PATIENCE_S,
              TCP.DEFAULT_DEPTH_HAIRCUT))
    print("%-46s %-6s %-34s %9s" % ("sample", "auth?", "reason", "outlay"))
    print("-" * 100)
    for label, dec, held, mk in SAMPLES:
        res = TCP.authorize(
            decision=dec, enabled=True, owner_authorized=True,
            held_up_qty=held["up"], held_dn_qty=held["dn"],
            held_up_px=held["up_px"], held_dn_px=held["dn_px"],
            maker_attempted=mk["attempted"], maker_rested_s=mk["rested_s"],
            window_outlay_spent_usd=D(0), halted=False)
        print("%-46s %-6s %-34s %9s" % (
            label[:46], "YES" if res.get("authorized") else "no",
            str(res.get("reason"))[:34],
            str(res.get("additional_outlay_usd") or "-")[:9]))

    print("\n--- the flag and the owner gate, on the authorizing sample ---")
    label, dec, held, mk = SAMPLES[2]
    for name, kw in (("flag off", {"enabled": False,
                                   "owner_authorized": True}),
                     ("owner authorization absent", {"enabled": True,
                                                     "owner_authorized":
                                                     False}),
                     ("halted", {"enabled": True, "owner_authorized": True,
                                 "halted": True}),
                     ("halt state unknown", {"enabled": True,
                                             "owner_authorized": True,
                                             "halted": None})):
        base = {"enabled": True, "owner_authorized": True, "halted": False}
        base.update(kw)
        res = TCP.authorize(
            decision=dec, held_up_qty=held["up"], held_dn_qty=held["dn"],
            held_up_px=held["up_px"], held_dn_px=held["dn_px"],
            maker_attempted=mk["attempted"], maker_rested_s=mk["rested_s"],
            window_outlay_spent_usd=D(0), **base)
        print("  %-28s -> %-4s %s" % (
            name, "YES" if res.get("authorized") else "no",
            res.get("reason")))

    print("\n--- partial fills never flip the sign (2145z state) ---")
    from src import l5_two_sided_probe as TSP
    for frac in ("0.25", "0.5", "1", "1.5", "2"):
        tu = TSP.top_up(up_qty=D(0), dn_qty=D(2), up_price=D(0),
                        dn_price=D("0.45"), extra_side="UP",
                        extra_qty=D(frac), extra_price=D("0.10"))
        print("  UP filled %-5s of 2 -> worst %+.4f  (doing nothing: -0.9400)"
              % (frac, tu["worst_after"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
