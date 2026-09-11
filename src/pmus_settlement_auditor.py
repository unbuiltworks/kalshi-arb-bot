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


# LIVE 15:26Z (OWNER P0 5560183912): /v1/portfolio/activities is ONE page
# of 20 rows, newest first, with top-level `eof` and `nextCursor`. A single
# unpaginated read therefore stops seeing a market's POSITION_RESOLUTION
# as soon as 20 newer activities exist. The reader now follows the cursor
# (parameter name discovered read-only, first success cached) until eof,
# no cursor, the page is older than `since` (the market's first fill), or
# ACTIVITY_MAX_PAGES.
ACTIVITY_MAX_PAGES = 25
CURSOR_PARAM_CANDIDATES = ("cursor", "nextCursor", "pageToken", "page_token",
                           "next_cursor")
_cursor_param: dict[str, Optional[str]] = {"name": None}


def _row_time_iso(row: Mapping[str, Any]) -> Optional[str]:
    for k in ("createTime", "updateTime", "time", "timestamp", "createdAt"):
        if row.get(k):
            return str(row.get(k))
    for v in row.values():
        if isinstance(v, Mapping):
            for k in ("createTime", "updateTime", "time", "timestamp"):
                if v.get(k):
                    return str(v.get(k))
    return None


def activity_pages(t: q1.Transport, since: Optional[str] = None,
                   max_pages: int = ACTIVITY_MAX_PAGES) -> dict:
    """Every activity row reachable through the cursor, newest first.
    Returns rows plus how the walk ended (read-only GETs only)."""
    rows: list[dict] = []
    resp = t.request("GET", "/v1/portfolio/activities")
    pages, ended, cursor_param = 1, "single_page", _cursor_param["name"]
    while True:
        page = resp.get("activities") if isinstance(resp, Mapping) else None
        page = [r for r in (page or []) if isinstance(r, Mapping)]
        rows.extend(dict(r) for r in page)
        eof = bool(resp.get("eof")) if isinstance(resp, Mapping) else True
        cursor = resp.get("nextCursor") if isinstance(resp, Mapping) else None
        oldest = _row_time_iso(page[-1]) if page else None
        if eof or not cursor or not page:
            ended = "eof" if eof else ("no_cursor" if not cursor else "empty_page")
            break
        if since and oldest and oldest < since:
            ended = "older_than_since"
            break
        if pages >= max_pages:
            ended = "max_pages"
            break
        nxt = None
        names = ([cursor_param] if cursor_param else []) + \
            [n for n in CURSOR_PARAM_CANDIDATES if n != cursor_param]
        for name in names:
            try:
                cand = t.request("GET", f"/v1/portfolio/activities?{name}={quote(str(cursor))}")
            except Exception:                      # noqa: BLE001
                continue
            if isinstance(cand, Mapping) and isinstance(cand.get("activities"), list):
                nxt, cursor_param = cand, name
                _cursor_param["name"] = name
                break
        if nxt is None:
            ended = "cursor_param_unknown"
            break
        resp = nxt
        pages += 1
    return {"rows": rows, "pages": pages, "ended": ended,
            "cursor_param": cursor_param,
            "oldest_time": _row_time_iso(rows[-1]) if rows else None,
            "newest_time": _row_time_iso(rows[0]) if rows else None}


def first_fill_time(slug: str) -> Optional[str]:
    """ISO time of this ledger's first fill in the market (the `since`
    bound for the activity walk); None when unknown."""
    path = q1.state_dir() / "ledger.jsonl"
    if not path.exists():
        return None
    best: Optional[str] = None
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not isinstance(r, Mapping) or r.get("slug") != slug:
            continue
        kind = r.get("kind")
        filled = kind == "filled" or (kind == "order_terminal" and
                                      float(r.get("filled_qty") or 0) > 0)
        if filled and r.get("t") and (best is None or str(r["t"]) < best):
            best = str(r["t"])
    return best


def _activities(t: q1.Transport, slug: str, typ: str,
                since: Optional[str] = None) -> list[dict]:
    walk = activity_pages(t, since=since)
    out: list[dict] = []
    for row in walk["rows"]:
        if row.get("type") != typ:
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


def _rows_of(resp: Any, key: str) -> list:
    rows = resp.get(key) if isinstance(resp, Mapping) else None
    if isinstance(rows, Mapping):
        return [{"marketSlug": k, **v} for k, v in rows.items() if isinstance(v, Mapping)]
    return [r for r in (rows or []) if isinstance(r, Mapping)]


def _position(t: q1.Transport, slug: str) -> Optional[dict]:
    """The account's row for the market from `positions`; LIVE 15:26Z the
    response also carries `availablePositions`, read separately by
    _available_position (never treated as a held position here)."""
    resp = t.request("GET", "/v1/portfolio/positions")
    for p in _rows_of(resp, "positions"):
        if p.get("marketSlug") == slug:
            return dict(p)
    return None


def _available_position(t: q1.Transport, slug: str) -> Optional[dict]:
    resp = t.request("GET", "/v1/portfolio/positions")
    for p in _rows_of(resp, "availablePositions"):
        if p.get("marketSlug") == slug:
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
        # LIVE 15:46Z: every trade this account ever made reads
        # TRADE_STATE_NEW (the venue's normal, final state; no CLEARED
        # transition is exposed). Only a state that says it is pending /
        # rejected / cancelled is uncleared.
        if state and state not in TRADE_FINAL_STATES and "BUSTED" not in state:
            uncleared = True
        out.append({
            "id": tr.get("id"), "state": state,
            "price": str(_amount(tr.get("price"))) if _amount(tr.get("price")) is not None else None,
            "qty": tr.get("qtyDecimal") or tr.get("qty"),
            # The RAW fields, unresolved, so a quantity that does not
            # reconcile can be diagnosed from evidence instead of guessed
            # at. `qty` above collapses two fields into one and loses the
            # shape the venue actually served.
            "qty_decimal_raw": tr.get("qtyDecimal"),
            "qty_raw": tr.get("qty"),
            "isAggressor": tr.get("isAggressor"),
            "realizedPnl": str(_amount(tr.get("realizedPnl")))
            if _amount(tr.get("realizedPnl")) is not None else None,
            "createTime": tr.get("createTime"),
            # LIVE 15:33Z: the venue's own side/outcome fields, whichever
            # it uses, so the account's net can be rebuilt from ITS trades
            "side_fields": {k: tr.get(k) for k in TRADE_SIDE_KEYS if k in tr},
            "keys": sorted(tr.keys()),
            # LIVE 15:46Z: the side lives inside the execution objects
            "passive": tr.get("passiveExecution") if isinstance(tr.get("passiveExecution"), Mapping) else None,
            "aggressor": tr.get("aggressorExecution") if isinstance(tr.get("aggressorExecution"), Mapping) else None,
            "cost": str(_amount(tr.get("cost"))) if _amount(tr.get("cost")) is not None else None,
        })
    realized = [_amount(tr.get("realizedPnl")) for tr in
                (r.get("trade") for r in rows if isinstance(r, Mapping))
                if isinstance(tr, Mapping) and tr.get("marketSlug") == slug]
    return {"trades": out, "busted": busted, "uncleared": uncleared,
            "realized_pnl_rows": sum(1 for x in realized if x is not None),
            "realized_pnl_sum": str(sum(x for x in realized if x is not None))
            if any(x is not None for x in realized) else None}


TRADE_SIDE_KEYS = ("side", "outcome", "marketSide", "marketSideId", "direction",
                   "action", "outcomeId", "position", "positionSide", "orderSide")
TRADE_FINAL_STATES = ("TRADE_STATE_NEW", "TRADE_STATE_CLEARED", "TRADE_STATE_SETTLED",
                      "TRADE_STATE_FILLED", "TRADE_STATE_COMPLETE")


def venue_qty_total(trade_info: Mapping[str, Any]) -> Optional[str]:
    """Shares the venue says this account traded in the market."""
    total, n = D("0"), 0
    for tr in trade_info.get("trades") or []:
        q = _amount(tr.get("qty"))
        if q is not None:
            total += q
            n += 1
    return str(total) if n else None


# Keys that mean a number is SCALED: a value carrying one of these is not
# the number it appears to be. notionalTraded turned out to be priced in
# cents after eight independent volume intervals said so, and every later
# quantity read is checked for the same shape rather than trusted.
SCALE_KEYS = ("exponent", "scale", "decimals", "precision", "multiplier",
              "exp", "unit", "denom", "denominator")


def qty_evidence(trade_info: Mapping[str, Any], limit: int = 12) -> dict:
    """Per-trade quantity evidence, for when venue and ledger disagree.

    Answers the question the totals cannot: is the gap a fill we missed, a
    row counted twice, or a number in units we misread? It resolves nothing
    and asserts nothing -- it reports what the venue actually served, so the
    NEXT occurrence is diagnosable rather than arguable.
    """
    rows, scaled, dupes, disagree = [], [], [], []
    seen: dict = {}
    for tr in trade_info.get("trades") or []:
        raw = tr.get("qty_raw")
        dec = tr.get("qty_decimal_raw")
        resolved = _amount(tr.get("qty"))
        keys = sorted(raw.keys()) if isinstance(raw, Mapping) else None
        scale = ([k for k in keys if k in SCALE_KEYS] if keys else [])
        row = {"id": tr.get("id"), "state": tr.get("state"),
               "createTime": tr.get("createTime"),
               "isAggressor": tr.get("isAggressor"),
               "qty_raw": raw, "qty_decimal_raw": dec,
               "qty_raw_keys": keys, "scale_keys": scale,
               "resolved": None if resolved is None else str(resolved)}
        if scale:
            # A scaled value read as if it were unscaled is off by a factor,
            # which is exactly how a small position reads as a large one.
            scaled.append(row)
        if raw is not None and dec is not None:
            a, b = _amount(raw), _amount(dec)
            if a is not None and b is not None and a != b:
                disagree.append({**row, "qty": str(a), "qtyDecimal": str(b)})
        tid = tr.get("id")
        if tid is not None:
            seen[tid] = seen.get(tid, 0) + 1
        rows.append(row)
    dupes = [{"id": k, "times": v} for k, v in seen.items() if v > 1]
    return {
        "n_trades": len(rows),
        "resolved_sum": str(sum(_amount(t.get("qty")) or D("0")
                                for t in trade_info.get("trades") or [])),
        "scaled_qty_rows": scaled[:limit],
        "qty_vs_qtydecimal_disagreements": disagree[:limit],
        "duplicate_trade_ids": dupes[:limit],
        "trades": rows[:limit],
        "truncated": len(rows) > limit,
        "labels": ("evidence only: nothing here resolves the disagreement. "
                   "A non-empty scaled_qty_rows means a quantity carries a "
                   "scale key we are not applying; duplicate_trade_ids means "
                   "a row was counted more than once; both empty with a real "
                   "gap means the venue filled a quantity we never recorded."),
    }


def traded_qty_with_evidence(slug: str, t: Optional[q1.Transport] = None,
                             limit: int = 12) -> dict:
    """The venue's traded quantity AND the per-trade evidence behind it, in
    ONE walk of the activity pages.

    Separate calls would paginate the account's whole activity feed twice to
    answer one question. `qty` is None when the venue could not be asked,
    which the caller must treat as unknown, never as 'nothing traded'."""
    try:
        t = t or q1.Transport()
        walk = activity_pages(t, since=first_fill_time(slug))
        trades = [r for r in walk["rows"]
                  if r.get("type") == "ACTIVITY_TYPE_TRADE"
                  and isinstance(r.get("trade"), Mapping)
                  and r["trade"].get("marketSlug") == slug]
        info = _trade_summary(trades, slug)
        return {"qty": venue_qty_total(info),
                "evidence": qty_evidence(info, limit=limit)}
    except Exception as exc:                            # noqa: BLE001
        return {"qty": None, "evidence": None,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def qty_evidence_for_slug(slug: str, t: Optional[q1.Transport] = None,
                          limit: int = 12) -> Optional[dict]:
    """qty_evidence for one market, read-only. None when the venue could not
    be asked -- which is unknown, never 'nothing traded'."""
    return traded_qty_with_evidence(slug, t, limit=limit)["evidence"]


def ledger_qty_total(slug: str) -> Optional[str]:
    """Shares this ledger recorded as filled in the market (filled rows;
    order_terminal rows only when no filled row carries the order)."""
    path = q1.state_dir() / "ledger.jsonl"
    if not path.exists():
        return None
    filled_orders: set = set()
    total, n = D("0"), 0
    terminal: list = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not isinstance(r, Mapping) or r.get("slug") != slug:
            continue
        if r.get("kind") == "filled":
            q = _amount(r.get("qty"))
            if q is not None:
                total += q
                n += 1
                if r.get("order_id"):
                    filled_orders.add(str(r["order_id"]))
        elif r.get("kind") == "order_terminal":
            terminal.append(r)
    for r in terminal:
        if str(r.get("order_id")) in filled_orders:
            continue
        q = _amount(r.get("filled_qty"))
        if q is not None and q > 0:
            total += q
            n += 1
    return str(total) if n else None


def flat_at_resolution(public: Mapping[str, Any], pos: Any, avail: Any,
                       trade_info: Mapping[str, Any]) -> Optional[dict]:
    """LIVE 15:33Z (OWNER P0 5560183912): the venue emits a
    POSITION_RESOLUTION only for a position it still carries at
    resolution. aec-cs2-fokus-nemi resolved publicly at 13:28:53Z
    (settlementPx 0.0000) while the account had no row under `positions`
    or `availablePositions` and no activity of any kind after its last
    fill -- flat, nothing to resolve, nothing to claim. That state is
    SETTLED for this account when ALL of: the public market is resolved
    with a settlement price, no position row anywhere, every trade in the
    market CLEARED (none busted/uncleared). Realized is taken ONLY from the
    venue's own per-trade realizedPnl (never inferred); when any trade
    lacks it the settlement is recorded with realized unconfirmed."""
    if not public.get("public_resolved"):
        return None
    if pos is not None or avail is not None:
        return None
    trades = trade_info.get("trades") or []
    if not trades or trade_info.get("busted") or trade_info.get("uncleared"):
        return None
    confirmed = trade_info.get("realized_pnl_rows") == len(trades) and \
        trade_info.get("realized_pnl_sum") is not None
    return {
        "settled": True,
        "status": "REALIZED" if confirmed else "SETTLED_REALIZED_UNCONFIRMED",
        "settlement_key": f"flat_at_resolution:{public.get('settlement_set_time') or public.get('updated_at')}",
        "settled_by": "venue_market_resolved_account_flat",
        "resolution": {
            "side": None,
            "public_settlement_px": public.get("settlement_px"),
            "public_settlement_set_time": public.get("settlement_set_time"),
            "public_status": public.get("status"), "public_state": public.get("state"),
            "beforeNet": "0", "afterNet": "0",
            "source": "public market resolution + account flat (no positions / "
                      "availablePositions row, all trades CLEARED); the venue "
                      "emitted no POSITION_RESOLUTION because it carried no position",
        },
        "realized_confirmed": bool(confirmed),
        "realized_usd": trade_info.get("realized_pnl_sum") if confirmed else None,
        "realized_source": "trade_activities_realizedPnl (venue's own per-trade number)"
        if confirmed else "unconfirmed: a trade lacks the venue's realizedPnl",
        "trades_cleared": len(trades),
    }


def traded_qty_for_slug(slug: str, t: Optional[q1.Transport] = None
                        ) -> Optional[str]:
    """Shares the venue says this account has traded in ONE market, from the
    venue's own trade activities. Used by the live session to reconcile its
    fill ledger against the venue while the market is still open (OWNER GO
    5584132560 item 2); the audit path uses the same walk at settlement, so
    both ask the venue the same question.

    None means the venue could not be asked, which the caller must treat as
    "unknown", never as "nothing traded"."""
    try:
        t = t or q1.Transport()
        walk = activity_pages(t, since=first_fill_time(slug))
        trades = [r for r in walk["rows"]
                  if r.get("type") == "ACTIVITY_TYPE_TRADE"
                  and isinstance(r.get("trade"), Mapping)
                  and r["trade"].get("marketSlug") == slug]
        return venue_qty_total(_trade_summary(trades, slug))
    except Exception:                                   # noqa: BLE001
        return None


def _credited_here(trades: list) -> dict:
    """The venue's OWN credit on our side of this market's fills.

    Never estimated: `credited_on_fills` sums commissionNotionalCollected on
    whichever execution `isAggressor` says is ours. When the venue serves no
    commission field the answer is 0.00 credited on 0 fills, which is a
    measurement, and an unreachable helper is an ERROR, which is not."""
    try:
        from src import pmus_incentive_audit as incentives   # lazy: cycle
        out = incentives.credited_on_fills(trades)
        return {"credited_to_us_usd": out["credited_to_us_usd"],
                "charged_to_us_usd": out["charged_to_us_usd"],
                "net_to_us_usd": out["net_to_us_usd"],
                "our_fills_with_a_credit": out["our_fills_with_a_credit"],
                "source": "venue commissionNotionalCollected, our side only"}
    except Exception as exc:                            # noqa: BLE001
        return {"credited_to_us_usd": None, "charged_to_us_usd": None,
                "net_to_us_usd": None, "our_fills_with_a_credit": None,
                "source": f"UNAVAILABLE:{type(exc).__name__}"}


def audit_market(slug: str, t: Optional[q1.Transport] = None) -> dict:
    t = t or q1.Transport()
    since = first_fill_time(slug)
    walk = activity_pages(t, since=since)
    resolutions = [r for r in walk["rows"]
                   if r.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION"]
    trades = [r for r in walk["rows"] if r.get("type") == "ACTIVITY_TYPE_TRADE"
              and isinstance(r.get("trade"), Mapping)
              and r["trade"].get("marketSlug") == slug]
    resolution = _resolution_for(resolutions, slug)
    pos = _position(t, slug)
    net = _net_position(pos)
    trade_info = _trade_summary(trades, slug)
    avail = _available_position(t, slug)

    result: dict[str, Any] = {
        "market": slug,
        "activity_walk": {k: walk[k] for k in ("pages", "ended", "cursor_param",
                                               "oldest_time", "newest_time")},
        "activity_rows": len(walk["rows"]),
        "available_position": avail,
        # LIVE 15:46Z: venue vs ledger fill reconciliation (cs2: venue 0.24
        # shares, ledger 0.20 -- the two fills that flattened the pair
        # landed with no session attached and were never booked)
        "venue_qty_total": venue_qty_total(trade_info),
        "ledger_qty_total": ledger_qty_total(slug),
        "position_net": str(net) if net is not None else None,
        "position_expired": bool(pos.get("expired")) if isinstance(pos, Mapping)
        and pos.get("expired") is not None else None,
        "trade_summary": trade_info,
        # OWNER re-arm 2026-09-10: every session must preserve THREE separate
        # economic outcomes -- matched-pair P&L | directional residual P&L |
        # incentives/rebates. The first two are derived in
        # settlement_breakdown; this is the third, and it is the venue's own
        # commissionNotionalCollected on OUR side of this market's fills, not
        # a modelled rebate. Lazily imported: pmus_incentive_audit imports
        # this module.
        "incentives": _credited_here(trades),
        "settled": False,
        "realized_confirmed": False,
        "realized_usd": None,
        "resolution": None,
    }
    if resolution is None:
        public = public_market_view(slug, t)
        result["public"] = public
        flat = flat_at_resolution(public, pos, avail, trade_info)
        if flat is None:
            result["status"] = "PENDING_SETTLEMENT"
            return result
        result.update(flat)
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
ACTIVITY_QUERY_VARIANTS = ("?limit=500", "?pageSize=500", "?page_size=500",  # (retired: every query 401 under path+query signing)
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
    # the cursor walk (path-only signing): how far back the account's
    # activities reach and whether a resolution for the slug is anywhere
    try:
        walk = activity_pages(t, since=None, max_pages=ACTIVITY_MAX_PAGES)
        rows = walk["rows"]
        types: dict[str, int] = {}
        for r in rows:
            k = str(r.get("type"))
            types[k] = types.get(k, 0) + 1
        out["activity_walk"] = {
            **{k: walk[k] for k in ("pages", "ended", "cursor_param", "oldest_time", "newest_time")},
            "rows": len(rows), "types": types,
            "slug_rows": sum(1 for r in rows if _mentions(r, slug)),
            "slug_resolution_rows": [
                {"time": _row_time(r), "payload": r.get("positionResolution")}
                for r in rows if r.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION"
                and _mentions(r, slug)][:3],
            "non_trade_rows_for_slug": [
                {"type": r.get("type"), "time": _row_time(r)}
                for r in rows if _mentions(r, slug) and r.get("type") != "ACTIVITY_TYPE_TRADE"][:6],
        }
    except Exception as exc:                       # noqa: BLE001
        out["activity_walk"] = {"error": f"{type(exc).__name__}:{getattr(exc, 'code', '')}"}
    try:
        resp = t.request("GET", "/v1/portfolio/positions")
        pos_rows = _rows_of(resp, "positions")
        avail_rows = _rows_of(resp, "availablePositions")
        out["positions"] = {
            "top_level_keys": sorted(resp.keys()) if isinstance(resp, Mapping) else None,
            "eof": resp.get("eof") if isinstance(resp, Mapping) else None,
            "next_cursor": bool(resp.get("nextCursor")) if isinstance(resp, Mapping) else None,
            "rows": len(pos_rows),
            "slug_row": next((r for r in pos_rows if r.get("marketSlug") == slug), None),
            "slugs": sorted(str(r.get("marketSlug")) for r in pos_rows)[:20],
            "available_rows": len(avail_rows),
            "available_slug_row": next((r for r in avail_rows if r.get("marketSlug") == slug), None),
            "available_slugs": sorted(str(r.get("marketSlug")) for r in avail_rows)[:20],
            "available_row_keys": sorted(avail_rows[0].keys()) if avail_rows else None,
        }
    except Exception as exc:                       # noqa: BLE001
        out["positions"] = {"error": f"{type(exc).__name__}:{getattr(exc, 'code', '')}"}
    return out
