"""v4 bounded q1 Polymarket monotone-prop maker campaign.

Changes from v3:
- discovers current strict MLB monotone threshold pairs dynamically instead of a
  date-specific allowlist;
- keeps the >=3c after-fee floor and post-only q1 maker constraint;
- lets a maker rest up to 12s to improve fill probability;
- rechecks the exact hedge book while the maker rests and cancels early if the
  3c floor or q1 hedge depth disappears;
- detects known partial fills after cancellation and immediately flattens them;
- paces authenticated Polymarket reads and backs off boundedly on rate limits;
- never blindly retries an order create after a rate-limit response;
- uses the order id returned by create directly and only performs a paced list
  fallback when the create response contains no order id;
- remains one maker at a time with a bounded six attempts per startup.

Live mutation still requires SPORTS_POLY_RELAY_LIVE_Q1=1.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable, Mapping

from polymarket_us import PolymarketUS
from polymarket_us.errors import NotFoundError

from src.sports_kp_poly_relay_live_q1 import (
    D, ONE, TARGET, TAKER_FEE_FLOOR, _amount, _ask, _bid, _books,
    _locked, _payload, _tradeable_exact, _unwrap_order,
)
from src.sports_kp_poly_relay_live_q1_v2 import _actual_order_id, _created_order
from src.sports_poly_dynamic_monotone import discover_current_pairs, score_pairs

MAX_ATTEMPTS = 6
TICK = D("0.01")
POLL_SECONDS = 12.0
POLL_INTERVAL_SECONDS = 0.25
EDGE_RECHECK_SECONDS = 1.0
AUTH_READ_GAP_SECONDS = 0.30
POST_PUBLIC_SCAN_COOLDOWN_SECONDS = 2.0
READ_RATE_LIMIT_DELAYS = (0.75, 1.5, 3.0, 5.0)


class ReadRateLimitExhausted(RuntimeError):
    def __init__(self, stage: str) -> None:
        super().__init__(f"rate limit exhausted at {stage}")
        self.stage = stage


def _is_rate_limit(exc: BaseException) -> bool:
    return type(exc).__name__ == "RateLimitError"


async def _read_call(stage: str, fn: Callable[..., Any], *args: Any) -> Any:
    """Pace and boundedly retry only read/preview calls on SDK rate limits."""
    last_rate_limit = False
    for attempt in range(len(READ_RATE_LIMIT_DELAYS) + 1):
        if attempt:
            await asyncio.sleep(READ_RATE_LIMIT_DELAYS[attempt - 1])
        try:
            out = await asyncio.to_thread(fn, *args)
            await asyncio.sleep(AUTH_READ_GAP_SECONDS)
            return out
        except Exception as exc:
            if not _is_rate_limit(exc):
                raise
            last_rate_limit = True
    if last_rate_limit:
        raise ReadRateLimitExhausted(stage)
    raise RuntimeError(f"read call failed at {stage}")


async def _resolve_maker_order_id(client: PolymarketUS, slug: str, created: Mapping[str, Any]) -> str:
    """Resolve maker identity without an unnecessary authenticated read.

    The create response normally contains the real nested execution order id. Use
    that id directly. Only if it is absent do a small, paced read-only list fallback.
    A failed fallback never causes another create; the caller scoped-cancels instead.
    """
    preferred = _actual_order_id(created)
    if preferred:
        return preferred
    for _ in range(4):
        response = await _read_call("maker_identity_list", client.orders.list, {"slugs": [slug]})
        for raw in response.get("orders") or []:
            if isinstance(raw, Mapping) and str(raw.get("marketSlug") or "") == slug and raw.get("id"):
                return str(raw["id"])
        await asyncio.sleep(0.25)
    return ""


def _maker_quote(book: Any, side: str, hedge_ask: D) -> D | None:
    bid = _bid(book, side)
    ask, _ = _ask(book, side)
    max_for_floor = ONE - hedge_ask - TAKER_FEE_FLOOR - TARGET
    improved = min(ask - TICK, max_for_floor)
    px = max(bid, improved)
    if px <= 0 or px >= ask or _locked(px, hedge_ask) < TARGET:
        return None
    return px


async def _cancel_scoped(client: PolymarketUS, oid: str, slug: str) -> None:
    try:
        await asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug})
    except Exception:
        try:
            await asyncio.to_thread(client.orders.cancel_all, {"slugs": [slug]})
        except Exception:
            pass


async def _clear_slug(client: PolymarketUS, slug: str) -> None:
    """Safety cleanup after an uncertain mutation response; no create retry."""
    await asyncio.sleep(0.75)
    try:
        await asyncio.to_thread(client.orders.cancel_all, {"slugs": [slug]})
    except Exception:
        pass


async def _refresh_order(client: PolymarketUS, oid: str, slug: str, fallback: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        return _unwrap_order(await _read_call("order_retrieve", client.orders.retrieve, oid))
    except NotFoundError:
        response = await _read_call("order_list_after_not_found", client.orders.list, {"slugs": [slug]})
        matching = [o for o in response.get("orders") or [] if isinstance(o, Mapping) and str(o.get("id") or "") == oid]
        return matching[0] if matching else fallback


async def _flatten_maker(client: PolymarketUS, slug: str) -> bool:
    close = await asyncio.to_thread(
        client.orders.close_position,
        {
            "marketSlug": slug,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": True,
            "maxBlockTime": "2",
        },
    )
    return bool(close)


async def _attempt(client: PolymarketUS, row: Mapping[str, Any], feed: Mapping[str, Mapping[str, Any]], attempt_no: int) -> dict[str, Any]:
    maker_slug, hedge_slug = str(row["maker_slug"]), str(row["hedge_slug"])
    latest = await _books({maker_slug, hedge_slug})
    mb, hb = latest.get(maker_slug), latest.get(hedge_slug)
    if mb is None or hb is None:
        return {"status": "SKIP_BOOK_MISSING", "attempt": attempt_no}
    hedge_ask, hedge_depth = _ask(hb, str(row["hedge_side"]))
    if hedge_depth < ONE:
        return {"status": "SKIP_HEDGE_DEPTH", "attempt": attempt_no}
    maker_px = _maker_quote(mb, str(row["maker_side"]), hedge_ask)
    if maker_px is None:
        return {"status": "SKIP_FLOOR", "attempt": attempt_no}

    low_resp = await _read_call("market_retrieve_low", client.markets.retrieve_by_slug, row["low_slug"])
    high_resp = await _read_call("market_retrieve_high", client.markets.retrieve_by_slug, row["high_slug"])
    _tradeable_exact(feed[row["low_slug"]], feed[row["high_slug"]], low_resp, high_resp)
    maker_payload = _payload(maker_slug, str(row["maker_side"]), maker_px, maker=True)
    hedge_payload = _payload(hedge_slug, str(row["hedge_side"]), hedge_ask, maker=False)
    await _read_call("maker_preview", client.orders.preview, {"request": maker_payload})
    await _read_call("hedge_preview", client.orders.preview, {"request": hedge_payload})

    try:
        created = await asyncio.to_thread(client.orders.create, maker_payload)
    except Exception as exc:
        if _is_rate_limit(exc):
            await _clear_slug(client, maker_slug)
            return {
                "status": "MAKER_CREATE_RATE_LIMIT_SCOPED_CANCEL",
                "attempt": attempt_no,
                "stage": "maker_create",
                "maker_slug": maker_slug,
                "order_entry": True,
                "realized": False,
            }
        raise

    oid = await _resolve_maker_order_id(client, maker_slug, created)
    maker_order: Mapping[str, Any] = _created_order(created)
    if not oid:
        await _clear_slug(client, maker_slug)
        return {"status": "UNKNOWN_MAKER_ID_SCOPED_CANCEL", "attempt": attempt_no, "stage": "maker_identity", "order_entry": True, "realized": False}

    loops = max(1, int(POLL_SECONDS / POLL_INTERVAL_SECONDS))
    recheck_every = max(1, int(EDGE_RECHECK_SECONDS / POLL_INTERVAL_SECONDS))
    cancel_reason = "TTL"
    last_hedge_ask = hedge_ask
    last_hedge_depth = hedge_depth
    for i in range(loops):
        maker_order = await _refresh_order(client, oid, maker_slug, maker_order)
        cum = D(str(maker_order.get("cumQuantity") or 0))
        state = str(maker_order.get("state") or "")
        if cum >= ONE or state in {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED"}:
            break

        if (i + 1) % recheck_every == 0:
            hedge_book = (await _books({hedge_slug})).get(hedge_slug)
            if hedge_book is None:
                cancel_reason = "HEDGE_BOOK_MISSING"
                await _cancel_scoped(client, oid, maker_slug)
                maker_order = await _refresh_order(client, oid, maker_slug, maker_order)
                break
            last_hedge_ask, last_hedge_depth = _ask(hedge_book, str(row["hedge_side"]))
            if last_hedge_depth < ONE or _locked(maker_px, last_hedge_ask) < TARGET:
                cancel_reason = "EDGE_GONE"
                await _cancel_scoped(client, oid, maker_slug)
                maker_order = await _refresh_order(client, oid, maker_slug, maker_order)
                break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    maker_cum = D(str(maker_order.get("cumQuantity") or 0))
    if maker_cum < ONE:
        await _cancel_scoped(client, oid, maker_slug)
        maker_order = await _refresh_order(client, oid, maker_slug, maker_order)
        maker_cum = D(str(maker_order.get("cumQuantity") or 0))
        if D("0") < maker_cum < ONE:
            flattened = await _flatten_maker(client, maker_slug)
            return {
                "status": "MAKER_PARTIAL_FLATTENED",
                "attempt": attempt_no,
                "maker_order_id": oid,
                "maker_slug": maker_slug,
                "maker_price": str(maker_px),
                "maker_cum": str(maker_cum),
                "cancel_reason": cancel_reason,
                "close_response_present": flattened,
                "order_entry": True,
                "realized": False,
            }
        if maker_cum < ONE:
            try:
                open_resp = await _read_call("post_cancel_order_list", client.orders.list, {"slugs": [maker_slug]})
                still_open = [o for o in open_resp.get("orders") or [] if isinstance(o, Mapping)]
                if still_open:
                    await _clear_slug(client, maker_slug)
            except ReadRateLimitExhausted:
                await _clear_slug(client, maker_slug)
            return {
                "status": "MAKER_NOT_FILLED_CANCELED" if cancel_reason == "TTL" else "MAKER_CANCELED_EDGE_GONE",
                "attempt": attempt_no,
                "maker_order_id": oid,
                "maker_slug": maker_slug,
                "maker_price": str(maker_px),
                "initial_hedge_ask": str(hedge_ask),
                "last_hedge_ask": str(last_hedge_ask),
                "last_hedge_depth": str(last_hedge_depth),
                "hypothetical_locked": str(_locked(maker_px, hedge_ask)),
                "rest_seconds": POLL_SECONDS,
                "cancel_reason": cancel_reason,
                "order_entry": True,
                "realized": False,
            }

    mavg_yes = _amount(maker_order.get("avgPx"))
    maker_native = (mavg_yes if str(row["maker_side"]) == "YES" else ONE - mavg_yes) if mavg_yes is not None else maker_px

    hedge_book = (await _books({hedge_slug})).get(hedge_slug)
    if hedge_book is None:
        flattened = await _flatten_maker(client, maker_slug)
        return {"status": "MAKER_FILLED_FLATTENED_NO_HEDGE_BOOK", "attempt": attempt_no, "maker_order_id": oid,
                "close_response_present": flattened, "order_entry": True, "realized": False}
    hpx, hdepth = _ask(hedge_book, str(row["hedge_side"]))
    if hdepth < ONE or _locked(maker_native, hpx) < TARGET:
        flattened = await _flatten_maker(client, maker_slug)
        return {"status": "MAKER_FILLED_FLATTENED_EDGE_GONE", "attempt": attempt_no, "maker_order_id": oid,
                "close_response_present": flattened, "order_entry": True, "realized": False}

    hedge_payload = _payload(hedge_slug, str(row["hedge_side"]), hpx, maker=False)
    try:
        await _read_call("filled_maker_hedge_preview", client.orders.preview, {"request": hedge_payload})
    except ReadRateLimitExhausted:
        flattened = await _flatten_maker(client, maker_slug)
        return {"status": "HEDGE_PREVIEW_RATE_LIMIT_MAKER_FLATTENED", "attempt": attempt_no, "maker_order_id": oid,
                "close_response_present": flattened, "order_entry": True, "realized": False}

    try:
        hcreated = await asyncio.to_thread(client.orders.create, hedge_payload)
    except Exception as exc:
        if _is_rate_limit(exc):
            flattened = await _flatten_maker(client, maker_slug)
            return {"status": "HEDGE_CREATE_RATE_LIMIT_MAKER_FLATTENED", "attempt": attempt_no, "maker_order_id": oid,
                    "stage": "hedge_create", "close_response_present": flattened, "order_entry": True, "realized": False}
        raise

    hid = _actual_order_id(hcreated)
    horder = _created_order(hcreated)
    hcum = D(str(horder.get("cumQuantity") or 0))
    if hid and hcum < ONE:
        try:
            horder = _unwrap_order(await _read_call("hedge_order_retrieve", client.orders.retrieve, hid))
            hcum = D(str(horder.get("cumQuantity") or 0))
        except NotFoundError:
            pass
    if hcum < ONE:
        flattened = await _flatten_maker(client, maker_slug)
        return {"status": "HEDGE_NOT_CONFIRMED_MAKER_FLATTENED", "attempt": attempt_no, "maker_order_id": oid, "hedge_order_id": hid,
                "close_response_present": flattened, "order_entry": True, "realized": False}

    try:
        maker_order = _unwrap_order(await _read_call("final_maker_retrieve", client.orders.retrieve, oid))
    except NotFoundError:
        pass
    if hid:
        try:
            horder = _unwrap_order(await _read_call("final_hedge_retrieve", client.orders.retrieve, hid))
        except NotFoundError:
            pass
    mavg_yes, havg_yes = _amount(maker_order.get("avgPx")), _amount(horder.get("avgPx"))
    if mavg_yes is None or havg_yes is None:
        return {"status": "BOTH_FILLED_RECONCILIATION_PENDING", "attempt": attempt_no, "maker_order_id": oid, "hedge_order_id": hid,
                "order_entry": True, "realized": False}
    mcost = mavg_yes if str(row["maker_side"]) == "YES" else ONE - mavg_yes
    hcost = havg_yes if str(row["hedge_side"]) == "YES" else ONE - havg_yes
    mfee = _amount(maker_order.get("commissionNotionalTotalCollected")) or D("0")
    hfee = _amount(horder.get("commissionNotionalTotalCollected")) or D("0")
    net = ONE - mcost - hcost - mfee - hfee
    return {"status": "BOTH_FILLED_RECONCILED", "attempt": attempt_no, "maker_order_id": oid, "hedge_order_id": hid,
            "maker_slug": maker_slug, "hedge_slug": hedge_slug, "actual_net_locked_profit": str(net),
            "actual_total_commission": str(mfee + hfee), "order_entry": True, "realized": bool(net > D("0"))}


async def run_once() -> dict[str, Any]:
    feed, pairs = await discover_current_pairs()
    books = await _books(set(feed))
    scored = score_pairs(feed, books, pairs)
    if not scored:
        out = {"status": "NO_EXACT_DYNAMIC_RELAY_Q1", "strict_markets": len(feed), "strict_pairs": len(pairs), "books": len(books), "order_entry": False}
        print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V4 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    await asyncio.sleep(POST_PUBLIC_SCAN_COOLDOWN_SECONDS)
    client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=6.0)
    try:
        balances = await _read_call("account_balances", client.account.balances)
        first = scored[0]
        if os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") != "1":
            out = {
                "status": "READY_DYNAMIC_CAMPAIGN_Q1",
                "strict_markets": len(feed),
                "strict_pairs": len(pairs),
                "candidates": len(scored),
                "best_locked": str(first["locked_profit"]),
                "best_hedge_depth": str(first["hedge_depth"]),
                "balance_response_present": bool(balances),
                "max_attempts": MAX_ATTEMPTS,
                "maker_rest_seconds": POLL_SECONDS,
                "order_entry": False,
                "realized": False,
            }
            print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V4 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        attempts: list[dict[str, Any]] = []
        for idx, row in enumerate(scored[:MAX_ATTEMPTS], start=1):
            try:
                result = await _attempt(client, row, feed, idx)
            except ReadRateLimitExhausted as exc:
                await _clear_slug(client, str(row["maker_slug"]))
                result = {"status": "READ_RATE_LIMIT_EXHAUSTED_SCOPED_CANCEL", "attempt": idx, "stage": exc.stage,
                          "order_entry": False, "realized": False}
            except Exception as exc:
                await _clear_slug(client, str(row["maker_slug"]))
                result = {"status": "ATTEMPT_ERROR_SCOPED_CANCEL", "attempt": idx,
                          "stage": getattr(exc, "stage", "unknown"), "error_type": type(exc).__name__,
                          "order_entry": True, "realized": False}
            attempts.append(result)
            if result.get("status") not in {
                "MAKER_NOT_FILLED_CANCELED", "MAKER_CANCELED_EDGE_GONE",
                "SKIP_BOOK_MISSING", "SKIP_HEDGE_DEPTH", "SKIP_FLOOR",
            }:
                break
            await asyncio.sleep(0.75)
        out = {
            "status": "CAMPAIGN_COMPLETE",
            "strict_markets": len(feed),
            "strict_pairs": len(pairs),
            "candidate_count": len(scored),
            "attempts": attempts,
            "attempt_count": len(attempts),
            "realized": any(bool(a.get("realized")) for a in attempts),
        }
        print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V4 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
