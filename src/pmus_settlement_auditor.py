"""Read-only PMUS settlement/reconciliation auditor.

Authoritative venue evidence:
* GET /v1/portfolio/activities filtered to POSITION_RESOLUTION proves a
  market settled for this account.
* TRADE activities expose clearing state (including BUSTED reversals).
* the resolution's afterPosition.realized is used only when parseable;
  otherwise settlement is recorded but realized P&L remains unknown.

The only mutation this module can perform is an optional idempotent append
to the LOCAL q1 ledger.  It never calls an exchange-mutating endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import quote

from src import pmus_q1_executor_v4 as q1


def _amount(value: Any) -> Optional[D]:
    if isinstance(value, Mapping):
        value = value.get("value")
    if value is None:
        return None
    try:
        return D(str(value))
    except Exception:
        return None


def _activities(t: q1.Transport, slug: str, typ: str) -> list[dict]:
    path = ("/v1/portfolio/activities?types=" + quote(typ, safe="")
            + "&marketSlug=" + quote(slug, safe="")
            + "&limit=100&sortOrder=SORT_ORDER_DESCENDING")
    resp = t.request("GET", path)
    rows = resp.get("activities") if isinstance(resp, Mapping) else None
    return [dict(r) for r in (rows or []) if isinstance(r, Mapping)]


def _position(t: q1.Transport, slug: str) -> Optional[dict]:
    resp = t.request("GET", "/v1/portfolio/positions")
    rows = resp.get("positions") if isinstance(resp, Mapping) else None
    if isinstance(rows, Mapping):
        p = rows.get(slug)
        return dict(p) if isinstance(p, Mapping) else None
    if isinstance(rows, list):
        for p in rows:
            if isinstance(p, Mapping) and p.get("marketSlug") == slug:
                return dict(p)
    return None


def _net_position(p: Optional[Mapping[str, Any]]) -> Optional[D]:
    if p is None:
        return D("0")
    for key in ("netPositionDecimal", "netPosition", "quantity"):
        if key in p:
            return _amount(p.get(key))
    return None


def _resolution_for(rows: list[dict], slug: str) -> Optional[dict]:
    for row in rows:
        if row.get("type") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        pr = row.get("positionResolution")
        if isinstance(pr, Mapping) and pr.get("marketSlug") == slug:
            return dict(pr)
    return None


def _trade_summary(rows: list[dict], slug: str) -> dict:
    out = []
    busted = False
    uncleared = False
    for row in rows:
        tr = row.get("trade") if isinstance(row, Mapping) else None
        if not isinstance(tr, Mapping) or tr.get("marketSlug") != slug:
            continue
        state = str(tr.get("state") or "")
        if "BUSTED" in state:
            busted = True
        if state and "CLEARED" not in state and "BUSTED" not in state:
            uncleared = True
        out.append({
            "id": tr.get("id"), "state": state,
            "price": str(_amount(tr.get("price"))) if _amount(tr.get("price")) is not None else None,
            "qty": tr.get("qtyDecimal") or tr.get("qty"),
            "isAggressor": tr.get("isAggressor"),
            "realizedPnl": str(_amount(tr.get("realizedPnl")))
            if _amount(tr.get("realizedPnl")) is not None else None,
            "createTime": tr.get("createTime"),
        })
    return {"trades": out, "busted": busted, "uncleared": uncleared}


def audit_market(slug: str, t: Optional[q1.Transport] = None) -> dict:
    t = t or q1.Transport()
    resolutions = _activities(t, slug, "ACTIVITY_TYPE_POSITION_RESOLUTION")
    trades = _activities(t, slug, "ACTIVITY_TYPE_TRADE")
    resolution = _resolution_for(resolutions, slug)
    pos = _position(t, slug)
    net = _net_position(pos)
    trade_info = _trade_summary(trades, slug)

    result: dict[str, Any] = {
        "market": slug,
        "position_net": str(net) if net is not None else None,
        "position_expired": bool(pos.get("expired")) if isinstance(pos, Mapping)
        and pos.get("expired") is not None else None,
        "trade_summary": trade_info,
        "settled": False,
        "realized_confirmed": False,
        "realized_usd": None,
        "resolution": None,
    }
    if resolution is None:
        result["status"] = "PENDING_SETTLEMENT"
        return result

    before = resolution.get("beforePosition")
    after = resolution.get("afterPosition")
    after = after if isinstance(after, Mapping) else {}
    after_net = _net_position(after)
    realized = _amount(after.get("realized"))
    key = str(resolution.get("tradeId") or resolution.get("updateTime") or slug)
    result.update({
        "settled": True,
        "status": "SETTLED",
        "settlement_key": key,
        "resolution": {
            "side": resolution.get("side"),
            "tradeId": resolution.get("tradeId"),
            "updateTime": resolution.get("updateTime"),
            "beforeNet": str(_net_position(before))
            if isinstance(before, Mapping) and _net_position(before) is not None else None,
            "afterNet": str(after_net) if after_net is not None else None,
        },
    })
    if trade_info["busted"]:
        result["status"] = "SETTLED_WITH_BUSTED_TRADE_REVIEW"
        return result
    if realized is not None:
        result["realized_confirmed"] = True
        result["realized_usd"] = str(realized)
        result["status"] = "REALIZED"
    return result


def _existing_keys(path: Path) -> set[tuple[str, str, str]]:
    keys = set()
    if not path.exists():
        return keys
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") in ("settled", "realized"):
            keys.add((str(r.get("kind")), str(r.get("slug")),
                      str(r.get("settlement_key"))))
    return keys


def append_authoritative_ledger(result: Mapping[str, Any]) -> list[str]:
    """Idempotently append venue-confirmed settled/realized rows locally."""
    if not result.get("settled"):
        return []
    root = q1.state_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "ledger.jsonl"
    slug = str(result["market"])
    key = str(result.get("settlement_key") or "")
    existing = _existing_keys(path)
    written = []

    def write(kind: str, **extra: Any) -> None:
        tup = (kind, slug, key)
        if tup in existing:
            return
        row = {"t": datetime.now(timezone.utc).isoformat(), "kind": kind,
               "slug": slug, "settlement_key": key, **extra}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        existing.add(tup); written.append(kind)

    write("settled", resolution=result.get("resolution"))
    if result.get("realized_confirmed") and result.get("realized_usd") is not None:
        write("realized", realized_usd=str(result["realized_usd"]),
              resolution=result.get("resolution"))
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", required=True)
    ap.add_argument("--write-ledger", action="store_true")
    args = ap.parse_args()
    result = audit_market(args.market)
    if args.write_ledger:
        result["ledger_rows_written"] = append_authoritative_ledger(result)
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
