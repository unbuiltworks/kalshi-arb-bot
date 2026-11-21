"""Drive the REAL executor over RECORDED books, at production limits.

WHY THIS AND NOT THE OTHER TWO HARNESSES. `round_strategy_compare.py`
replays the ECONOMICS and knows nothing about the gates;
`test_l5_two_sided_wiring` drives the real gates but feeds ONE STATIC BOOK
every tick, so a refusal there can be an artifact of a book that never
moves. Neither can answer "which gate limits our pair count on a real
book", and that is the question.

This feeds recorded frames into `FaithfulExecutor.handle` at the exact
production configuration and reports, per window: orders sent, pairs
completed, residual carried, and a HISTOGRAM OF REFUSALS BY REASON. The
histogram is the point -- the last refusal of a run is not the binding one,
and reading it as such is how a static-book artifact gets mistaken for a
limit.

NOTHING HERE IS A FILL. The harness transport acknowledges and fills
synthetically; these are MODELLED fills on real prices, never venue fills,
and no figure from this is realized money.
"""
import sys
from collections import Counter
from decimal import Decimal as D

sys.path.insert(0, ".")
from analysis.l5.issue195.queue_depletion_report import (  # noqa: E402
    IN_SAMPLE, OUT_OF_SAMPLE, _load, _touches)

#: the deployed configuration, read from the heartbeat of deployment
#: 18deb02d at 2026-11-07T23:25:36Z
PROD_GATES = dict(max_cash_deployed_usd=D("12"),
                  max_worst_case_deficit_usd=D("1.00"),
                  max_unmatched_contracts=D("5"))
PROD_LIMITS = dict(max_usd_per_market=D("20"), max_abs_residual=D("10"),
                   max_total_exposure_usd=D("1.00"),
                   max_concurrent_markets=1, max_live_orders=2,
                   daily_realized_loss_halt_usd=D("5"))


def frame_to_book(frame):
    """A recorded ladder frame as the book dict `handle` consumes."""
    t = _touches(frame)
    if t is None:
        return None
    bid, ask = t
    bs = sum(D(s) for p, s in frame["bids"] if D(p) == bid) or D("1000")
    as_ = sum(D(s) for p, s in frame["asks"] if D(p) == ask) or D("1000")
    return {"bid": str(bid), "ask": str(ask), "bid_shares": str(bs),
            "ask_shares": str(as_), "stream_age_s": "0.05"}


def replay_window(H, E, tmp_path, frames, gates=None, limits=None,
                  slug="m"):
    """One window of recorded frames through the real executor."""
    import tests.test_l5_two_sided_wiring as W
    from src.l5_live_validation_gates import ValidationGates as VG
    g = VG(**dict(PROD_GATES, **(gates or {})))
    lim = dict(PROD_LIMITS, **(limits or {}))
    ex, t = W.mk(H, E, tmp_path, g=g)
    ex.limits = E.SafetyLimits(**lim)
    hist = Counter()
    for f in frames:
        bk = frame_to_book(f)
        if bk is None:
            hist["NO_TOUCH"] += 1
            continue
        try:
            r = ex.handle(W._row(H), bk, market="m", window="21:00")
        except BaseException as exc:                           # noqa: BLE001
            hist["RAISED:%s" % type(exc).__name__] += 1
            continue
        why = r.get("refused_reason")
        hist[(str(why).split(":")[0] if why else "SUBMITTED")] += 1
    up = abs(D(str(ex.store.position("m", "UP") or 0)))
    dn = abs(D(str(ex.store.position("m", "DOWN") or 0)))
    legs = ex.store.leg_cost("m")
    return {"slug": slug, "sent": len(t.sent), "frames": len(frames),
            "pairs": min(up, dn), "residual": abs(up - dn),
            "up": up, "dn": dn,
            "cost": sum(D(str(legs.get(s) or 0)) for s in ("UP", "DOWN")),
            "hist": hist}


def summarise(rows, label):
    hist = Counter()
    for r in rows:
        hist.update(r["hist"])
    sent = sum(r["sent"] for r in rows)
    pairs = sum(r["pairs"] for r in rows)
    resid = sum(r["residual"] for r in rows)
    print("  %-26s windows=%-3d sent=%-4d pairs=%-7s residual=%-7s"
          % (label, len(rows), sent, pairs, resid))
    print("      refusals: %s"
          % ", ".join("%s=%d" % (k, v) for k, v in hist.most_common(6)))
    return {"sent": sent, "pairs": pairs, "residual": resid, "hist": hist}
