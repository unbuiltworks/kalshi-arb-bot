"""OWNER 2026-09-17 (C) -- the realized-income scoreboard.

ONE BRUTALLY SIMPLE QUESTION: what has this program actually earned?

Every number here comes from the DURABLE LEDGER and nothing else. There is no
model, no mark, no projection and no estimate anywhere in this file, because
the whole point of a scoreboard is to be the thing that cannot flatter us.

WHAT COUNTS AS REALIZED, and it is a short list: a `realized` row, which the
settlement auditor writes only when the venue itself returned a realized
amount. That is it.

WHAT DOES NOT COUNT, each of which has at some point looked like money:
  * SETTLED_REALIZED_UNCONFIRMED -- the market resolved and our account went
    flat, but the venue served no realizedPnl. Both 2026-09-17 positions ended
    here, including the first matched pair this engine ever held, so its
    economics remain UNMEASURED rather than zero;
  * a cost basis. 0.92 paid for a pair that should redeem at 1.00 is not 0.08
    of profit until settlement says so;
  * mark-to-market. A residual marked at 0.01 has lost nothing yet;
  * theoretical or modelled fills, shadow inventory, estimated rebates.

Incentives are tracked SEPARATELY and never summed into trading P&L, because a
rebate that makes a losing trade look profitable is how a strategy dies slowly.

READ-ONLY. This module computes; it never writes to the ledger, never calls the
venue, and cannot influence a decision.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ZERO = D("0")
#: the only row kind that may contribute to realized P&L
REALIZED_KIND = "realized"
#: where the matched / residual split comes from
BREAKDOWN_KIND = "settlement_breakdown"
#: a market whose venue settlement carried no realized figure
UNCONFIRMED = "SETTLED_REALIZED_UNCONFIRMED"


def _dec(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except Exception:                                    # noqa: BLE001
        return None


def _when(row: Mapping[str, Any]) -> Optional[datetime]:
    t = row.get("t") or row.get("t_utc")
    if not t:
        return None
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except Exception:                                    # noqa: BLE001
        return None


def read_rows(state_dir: Path) -> tuple[list, bool]:
    """(rows, readable). An unreadable ledger is never an empty one -- a
    scoreboard that silently reports zero because it could not read the file
    is worse than no scoreboard."""
    p = Path(state_dir) / "ledger.jsonl"
    if not p.exists():
        return [], True
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return [], False
    out, ok = [], True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            ok = False
            continue
        if isinstance(r, dict):
            out.append(r)
    return out, ok


def _window(rows: Iterable[Mapping], now: datetime, days: Optional[int]):
    if days is None:
        return list(rows)
    if days == 0:                       # "today" = since 00:00 UTC
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=days)
    out = []
    for r in rows:
        w = _when(r)
        if w is not None and w >= start:
            out.append(r)
    return out


def _period(rows, now, days) -> dict:
    """The realized split for one window. Matched and residual come from the
    breakdown rows; total comes from the `realized` rows. They are reported
    side by side and NEVER reconciled by adjusting one to fit the other -- a
    disagreement between them is information, so it is surfaced."""
    sel = _window(rows, now, days)
    realized = ZERO
    markets = set()
    for r in sel:
        if r.get("kind") != REALIZED_KIND:
            continue
        v = _dec(r.get("realized_usd"))
        if v is None:
            continue
        realized += v
        if r.get("slug"):
            markets.add(str(r["slug"]))

    matched = residual = incentives = ZERO
    fees_unreported = 0
    for r in sel:
        if r.get("kind") != BREAKDOWN_KIND:
            continue
        if str(r.get("settlement_status") or "") == UNCONFIRMED:
            continue                    # settled, but the venue gave no figure
        m = _dec(r.get("matched_contribution_usd"))
        d0 = _dec(r.get("residual_contribution_usd"))
        i = _dec(r.get("incentives_usd"))
        if m is not None:
            matched += m
        if d0 is not None:
            residual += d0
        if i is not None:
            incentives += i
        if str(r.get("fees_usd") or "") == "venue_unreported":
            fees_unreported += 1

    return {
        "matched_realized_usd": str(matched),
        "residual_realized_usd": str(residual),
        "fees_usd": ("venue_unreported" if fees_unreported else "0"),
        "incentives_usd": str(incentives),
        "total_confirmed_realized_usd": str(realized),
        "markets_completed": len(markets),
        "realized_per_market_usd": (
            str((realized / len(markets)).quantize(D("0.0001")))
            if markets else None),
    }


def scoreboard(state_dir: Path, now: Optional[datetime] = None) -> dict:
    """today / 7d / 30d realized, with the execution rates beside them.

    The rates are not decoration. A realized number with no fill rate and no
    rejection rate cannot tell you whether a bad result came from bad decisions
    or from an execution path that never got filled -- which is exactly the
    question the 2026-09-17 cohort raised.
    """
    now = now or datetime.now(timezone.utc)
    rows, ok = read_rows(Path(state_dir))

    submitted = filled = rejected = 0
    complete_submitted = complete_filled = 0
    committed = ZERO
    residuals: list = []
    touched: set = set()
    unconfirmed: list = []
    for r in rows:
        k = r.get("kind")
        if k == "mandate_commit":
            submitted += 1
            v = _dec(r.get("cash_at_risk"))
            if v is not None:
                committed += v
            if r.get("slug"):
                touched.add(str(r["slug"]))
        elif k == "order_terminal":
            q = _dec(r.get("filled_qty"))
            if q is not None and q > 0:
                filled += 1
        elif k == "submitted":
            # the only row kind that records WHICH stage was submitted; the
            # mandate commit does not carry one, so the COMPLETE fill rate is
            # computed from these or reported as unavailable. A rate with an
            # invented denominator is worse than an absent one.
            if str(r.get("stage") or "").startswith("spring_complete"):
                complete_submitted += 1
        elif k == "filled":
            if str(r.get("stage") or "").startswith("spring_complete"):
                complete_filled += 1
        elif k == BREAKDOWN_KIND:
            d0 = _dec(r.get("directional_residual"))
            if d0 is not None:
                residuals.append(abs(d0))
            if str(r.get("settlement_status") or "") == UNCONFIRMED:
                unconfirmed.append(str(r.get("slug") or ""))

    rejected = sum(1 for r in rows if r.get("kind") == "order_terminal"
                   and "REJECT" in str(r.get("state") or "").upper())

    def _rate(n, d):
        return None if not d else str((D(n) / D(d)).quantize(D("0.0001")))

    matched_fraction = None
    tot_m = tot_c = ZERO
    for r in rows:
        if r.get("kind") != BREAKDOWN_KIND:
            continue
        m, c = _dec(r.get("matched_pairs")), _dec(r.get("cost_basis_usd"))
        if m is not None:
            tot_m += m
        if c is not None:
            tot_c += c
    if tot_c > 0:
        matched_fraction = str((tot_m / tot_c).quantize(D("0.0001")))

    return {
        "readable": ok,
        "as_of_utc": now.isoformat(),
        "today": _period(rows, now, 0),
        "last_7d": _period(rows, now, 7),
        "last_30d": _period(rows, now, 30),
        "capital_committed_usd": str(committed),
        "markets_touched": len(touched),
        "submissions": submitted,
        "fills": filled,
        "rejections": rejected,
        "fill_rate": _rate(filled, submitted),
        "complete_fill_rate": _rate(complete_filled, complete_submitted),
        "complete_submitted": complete_submitted,
        "complete_filled": complete_filled,
        "rejection_rate": _rate(rejected, submitted),
        "avg_residual": (str((sum(residuals, ZERO) / len(residuals))
                             .quantize(D("0.0001"))) if residuals else None),
        "max_residual": str(max(residuals)) if residuals else None,
        "matched_fraction_of_cost": matched_fraction,
        "excluded_settled_realized_unconfirmed": sorted(set(unconfirmed)),
        "labels": (
            "ONLY venue-confirmed `realized` rows count. "
            "SETTLED_REALIZED_UNCONFIRMED is EXCLUDED and listed by name: "
            "those markets resolved and went flat but the venue served no "
            "realizedPnl, so their economics are UNMEASURED, not zero. "
            "Cost basis, mark-to-market, theoretical fills, shadow inventory "
            "and estimated rebates are never realized. Incentives are kept "
            "separate and never summed into trading P&L."),
    }


def render(sb: Mapping[str, Any]) -> str:
    """The dashboard, as plain text."""
    L = []
    L.append("L5 REALIZED INCOME -- as of %s" % sb["as_of_utc"])
    if not sb.get("readable"):
        L.append("  !! LEDGER PARTIALLY UNREADABLE -- figures are incomplete")
    L.append("")
    L.append("%-26s %14s %14s %14s" % ("", "today", "7d", "30d"))
    keys = [("matched realized", "matched_realized_usd"),
            ("residual realized", "residual_realized_usd"),
            ("fees", "fees_usd"),
            ("incentives (separate)", "incentives_usd"),
            ("TOTAL CONFIRMED REALIZED", "total_confirmed_realized_usd"),
            ("markets completed", "markets_completed"),
            ("realized / market", "realized_per_market_usd")]
    for label, k in keys:
        L.append("%-26s %14s %14s %14s" % (
            label, sb["today"].get(k), sb["last_7d"].get(k),
            sb["last_30d"].get(k)))
    L.append("")
    L.append("capital committed   %s   markets touched %s" % (
        sb["capital_committed_usd"], sb["markets_touched"]))
    L.append("submissions %s   fills %s   rejections %s" % (
        sb["submissions"], sb["fills"], sb["rejections"]))
    L.append("fill rate %s   COMPLETE fill rate %s   rejection rate %s" % (
        sb["fill_rate"], sb["complete_fill_rate"], sb["rejection_rate"]))
    L.append("residual avg %s   max %s   matched/cost %s" % (
        sb["avg_residual"], sb["max_residual"], sb["matched_fraction_of_cost"]))
    ex = sb.get("excluded_settled_realized_unconfirmed") or []
    if ex:
        L.append("")
        L.append("EXCLUDED, settled but venue served no realizedPnl (%d):" % len(ex))
        for s in ex:
            L.append("   %s" % s)
    L.append("")
    L.append("$480/day is a TARGET, never a projection. Nothing above is modelled.")
    return "\n".join(L)


def main() -> int:
    import os
    from src import pmus_q1_executor as q1
    d = Path(os.getenv("PMUS_Q1_STATE_DIR", "") or q1.state_dir())
    print(render(scoreboard(d)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
