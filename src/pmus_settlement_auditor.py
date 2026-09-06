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
    resp = t.request("GET", "/v1/portfolio/activities")
    rows = resp.get("activities") if isinstance(resp, Mapping) else None
    out: list[dict] = []
    for row in (rows or []):
        if not isinstance(row, Mapping) or row.get("type") != typ:
            continue
        if typ == "ACTIVITY_TYPE_POSITION_RESOLUTION":
            payload = row.get("positionResolution")
        elif typ == "ACTIVITY_TYPE_TRADE":
            payload = row.get("trade")
        else:
            payload = None
        if isinstance(payload, Mapping) and payload.get("marketSlug") == slug:
            out.append(dict(row))
    return out

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


# ---------------------------------------------------------------------------
# OWNER P0 5560183912 (LIVE 15:19Z): aec-cs2-fokus-nemi was PENDING_SETTLEMENT
# for 2.5 h while the venue's PUBLIC book already said MARKET_STATE_EXPIRED /
# MARKET_STATUS_RESOLVED with settlementPx 0.0000 set at 13:28:53Z. The audit
# above proves settlement ONLY from the account's POSITION_RESOLUTION
# activity. This READ-ONLY diagnostic shows both views side by side -- the
# public market status and the exact shape of the account's activities /
# positions responses (top-level keys, counts, types, every row that names
# the slug, and a few query variants) -- so the detector defect, if any, is
# fixed from evidence. No mutation, no settlement forced, no P&L inferred.
PUBLIC_RESOLVED_STATES = ("MARKET_STATE_EXPIRED", "MARKET_STATE_RESOLVED",
                          "MARKET_STATE_SETTLED")
PUBLIC_RESOLVED_STATUSES = ("MARKET_STATUS_RESOLVED", "MARKET_STATUS_SETTLED")
ACTIVITY_QUERY_VARIANTS = ("?limit=500", "?pageSize=500", "?page_size=500",
                           "?marketSlug={slug}", "?market_slug={slug}",
                           "?type=ACTIVITY_TYPE_POSITION_RESOLUTION",
                           "?types=ACTIVITY_TYPE_POSITION_RESOLUTION")


def _mentions(obj: Any, slug: str) -> bool:
    try:
        return slug in json.dumps(obj, default=str)
    except Exception:                              # noqa: BLE001
        return False


def _row_time(row: Mapping[str, Any]) -> Any:
    for k in ("createTime", "updateTime", "time", "timestamp", "createdAt"):
        if k in row:
            return row.get(k)
    for v in row.values():
        if isinstance(v, Mapping):
            for k in ("createTime", "updateTime", "time", "timestamp"):
                if k in v:
                    return v.get(k)
    return None


def public_market_view(slug: str, t: Optional[q1.Transport] = None) -> dict:
    """The venue's PUBLIC status of a market (gateway, keyless): book state
    + settlement price/time + market status. Read-only; errors reported."""
    t = t or q1.Transport()
    out: dict[str, Any] = {"market": slug}
    try:
        book = t.request("GET", f"/v1/markets/{quote(slug)}/book", base=q1.base.GATEWAY)
        md = book.get("marketData") if isinstance(book, Mapping) else None
        md = md if isinstance(md, Mapping) else {}
        st = md.get("stats") if isinstance(md.get("stats"), Mapping) else {}
        px = _amount(st.get("settlementPx"))
        out.update({"state": md.get("state"),
                    "settlement_px": str(px) if px is not None else None,
                    "settlement_set_time": st.get("settlementSetTime"),
                    "settlement_method": st.get("settlementPriceCalculationMethod"),
                    "last_trade_set_time": st.get("lastTradeSetTime")})
    except Exception as exc:                       # noqa: BLE001
        out["book_error"] = f"{type(exc).__name__}:{getattr(exc, 'code', '')}"
    try:
        mk = t.request("GET", f"/v1/markets?slug={quote(slug)}", base=q1.base.GATEWAY)
        rows = mk.get("markets") if isinstance(mk, Mapping) else None
        m = next((r for r in (rows or []) if isinstance(r, Mapping)
                  and r.get("slug") == slug), {})
        out.update({"status": m.get("status"), "closed": m.get("closed"),
                    "active": m.get("active"), "updated_at": m.get("updatedAt")})
    except Exception as exc:                       # noqa: BLE001
        out["market_error"] = f"{type(exc).__name__}:{getattr(exc, 'code', '')}"
    out["public_resolved"] = bool(
        (out.get("state") in PUBLIC_RESOLVED_STATES
         or out.get("status") in PUBLIC_RESOLVED_STATUSES)
        and out.get("settlement_px") is not None)
    return out


def diagnose_pending(slug: str, t: Optional[q1.Transport] = None) -> dict:
    """READ-ONLY: why is this market still PENDING? Public view + the exact
    shape of the account's activities and positions responses."""
    t = t or q1.Transport()
    out: dict[str, Any] = {"market": slug, "public": public_market_view(slug, t)}
    try:
        resp = t.request("GET", "/v1/portfolio/activities")
        rows = resp.get("activities") if isinstance(resp, Mapping) else None
        rows = [r for r in (rows or []) if isinstance(r, Mapping)]
        types: dict[str, int] = {}
        for r in rows:
            k = str(r.get("type"))
            types[k] = types.get(k, 0) + 1
        for_slug = [{"type": r.get("type"), "time": _row_time(r),
                     "payload_keys": sorted(k for k in r.keys() if k != "type")}
                    for r in rows if _mentions(r, slug)]
        out["activities"] = {
            "top_level_keys": sorted(resp.keys()) if isinstance(resp, Mapping) else None,
            "rows": len(rows), "types": types,
            "row_keys": sorted(rows[0].keys()) if rows else None,
            "first_time": _row_time(rows[0]) if rows else None,
            "last_time": _row_time(rows[-1]) if rows else None,
            "for_slug": for_slug[:12], "for_slug_n": len(for_slug),
            "resolution_rows_any_market": types.get("ACTIVITY_TYPE_POSITION_RESOLUTION", 0),
        }
    except Exception as exc:                       # noqa: BLE001
        out["activities"] = {"error": f"{type(exc).__name__}:{getattr(exc, 'code', '')}"}
    variants: dict[str, Any] = {}
    for q in ACTIVITY_QUERY_VARIANTS:
        qq = q.format(slug=quote(slug))
        try:
            r = t.request("GET", "/v1/portfolio/activities" + qq)
            rows2 = r.get("activities") if isinstance(r, Mapping) else None
            rows2 = [x for x in (rows2 or []) if isinstance(x, Mapping)]
            variants[qq] = {
                "rows": len(rows2),
                "keys": sorted(r.keys()) if isinstance(r, Mapping) else None,
                "slug_rows": sum(1 for x in rows2 if _mentions(x, slug)),
                "resolution_rows": sum(1 for x in rows2
                                       if x.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION"),
                "slug_resolution_rows": sum(
                    1 for x in rows2 if x.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION"
                    and _mentions(x, slug))}
        except Exception as exc:                   # noqa: BLE001
            variants[qq] = f"{type(exc).__name__}:{getattr(exc, 'code', '')}"
    out["activity_query_variants"] = variants
    try:
        resp = t.request("GET", "/v1/portfolio/positions")
        rows = resp.get("positions") if isinstance(resp, Mapping) else None
        if isinstance(rows, Mapping):
            n, row = len(rows), rows.get(slug)
        else:
            rows = [r for r in (rows or []) if isinstance(r, Mapping)]
            n = len(rows)
            row = next((r for r in rows if r.get("marketSlug") == slug), None)
        out["positions"] = {
            "top_level_keys": sorted(resp.keys()) if isinstance(resp, Mapping) else None,
            "rows": n, "slug_row": row,
            "slugs": sorted(str(r.get("marketSlug")) for r in rows)[:20]
            if isinstance(rows, list) else sorted(rows.keys())[:20]}
    except Exception as exc:                       # noqa: BLE001
        out["positions"] = {"error": f"{type(exc).__name__}:{getattr(exc, 'code', '')}"}
    return out
