"""L5's engine, on L5's own markets, 24/7. SHADOW ONLY -- no order path.

This is the "rawest safe L5 clone" of OWNER 5594284979/5594313563, built on
btc-updown-15m / eth-updown-15m rather than on sports. Every self-imposed
barrier from l5_barrier_inventory is GONE from this path; every money bound
and the one L5-evidence gate are kept and reported.

--- WHAT WAS REMOVED, AND IT IS NOT HERE TO BE RE-ADDED ---
No spring admission, no operator-pasted gate evidence, no 0.35-0.65 entry
band, no 0.98 fee-safe ceiling, no 30-second maker rest, no reject-streak
breaker, no MAX_ACTIONS ceiling, no MIN_REEVAL timer, no complement-
acceptance proof, no sports discovery filter, no event-progress clock
translation. These markets serve a real endDate, so time-to-expiry is a
fact here rather than a mapping.

--- WHAT IS KEPT ---
The frozen model (sha bbb55bf90638d2bd, lookup v3) is READ, never retrained.
off_distribution is kept because it IS L5 evidence: it fires where his tape
has no observation, and acting there would be invention. Maker-only is kept
because he is 77.3% maker. The money bounds are kept and are the reason
nothing here can act.

--- ONE PRIMARY BLOCKER PER DECISION ---
The point of this service is to distinguish software gating from an economic
or venue mismatch, so each would-be action reports EXACTLY ONE reason, taken
in a fixed precedence: money first, then venue, then fidelity. If the answer
is capital, that must not be hidden behind a fidelity gate evaluated later.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

SHADOW_ONLY = True
ORDER_ENTRY = False

ASSETS = ("btc", "eth")
WINDOW_S = 900                     # the 15-minute cadence L5 traded
WINDOWS_AHEAD = 4                  # broadest accessible slice of the universe

# --- venue terms, read live and re-read every cycle rather than assumed ---
DEFAULT_ORDER_MIN_SHARES = 5
DEFAULT_REWARD_MIN_SHARES = 50

# --- money bounds: KEPT, and the reason this lane cannot act ---
PER_MARKET_CAP_USD = D("0.05")     # pmus_micro_executor.PER_MARKET_FIRST_LEG_CAP
DAILY_LOSS_STOP_TRIPPED = True     # -18.5873 on 2026-09-08, not reset here

MODEL_PATH = Path("analysis/l5/l5_action_model_v2.json")
MODEL_SHA = "bbb55bf90638d2bd"

INTERVAL_ENV = "L5_NATIVE_INTERVAL_S"
DEFAULT_INTERVAL_S = 30.0


def _get(base: str, path: str, params: Optional[Dict[str, Any]] = None,
         timeout: float = 20.0) -> Any:
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(
        url, headers={"Accept": "application/json",
                      "User-Agent": "l5-native-shadow"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def current_slugs(now: Optional[float] = None) -> List[str]:
    """The broadest accessible L5 universe: every open window on both
    assets. No ranking, no cap, no category filter -- the sports selection
    logic is exactly what this service exists without."""
    now = time.time() if now is None else now
    base = int(now) - (int(now) % WINDOW_S)
    return [f"{a}-updown-15m-{base + k * WINDOW_S}"
            for a in ASSETS for k in range(WINDOWS_AHEAD)]


def load_model(path: Path = MODEL_PATH) -> Dict[str, Any]:
    """Read the frozen model. Never rebuilt, never retrained, never tuned."""
    with open(path) as f:
        raw = json.load(f)
    states = raw.get("base") if isinstance(raw.get("base"), dict) else raw
    return {"states": states, "model_sha": MODEL_SHA}


def _state_key(last_price: float, tte_s: float, residual: float) -> str:
    from src.polymarket_l5.action_model import state_key
    return state_key(last_price, tte_s, residual)


def _modal(model: Dict[str, Any], state: str) -> Optional[Dict[str, Any]]:
    st = model["states"].get(state)
    if not isinstance(st, dict) or not st:
        return None
    total = sum(st.values()) or 1
    top = max(st, key=lambda k: st[k])
    return {"modal_action": top, "probability": round(st[top] / total, 4),
            "n": total, "distinct_actions": len(st)}


# ------------------------------------------------- one primary blocker
# Money first, then venue, then fidelity. The ORDER is the point: if
# capital is the answer it must surface before any fidelity gate can mask
# it, which is exactly what the sports path used to do.
BLOCKER_PRECEDENCE = (
    "daily_loss_stop_tripped",
    "capital_below_venue_min_order",
    "capital_below_reward_min_size",
    "no_two_sided_book",
    "off_distribution",
)


def all_blockers(cap_usd: D, price: Optional[float],
                 order_min_shares: int, reward_min_shares: int,
                 two_sided: bool, on_distribution: bool) -> List[str]:
    """EVERY blocker, not just the first. The primary one answers 'what
    stops this action'; the full set answers 'what would still stop it once
    that is cleared', and without it the daily stop hides the capital
    finding underneath -- which is exactly the masking this service exists
    to prevent."""
    out: List[str] = []
    if DAILY_LOSS_STOP_TRIPPED:
        out.append("daily_loss_stop_tripped")
    if price and cap_usd < D(order_min_shares) * D(str(price)):
        out.append("capital_below_venue_min_order")
    if price and cap_usd < D(reward_min_shares) * D(str(price)):
        out.append("capital_below_reward_min_size")
    if not two_sided:
        out.append("no_two_sided_book")
    if not on_distribution:
        out.append("off_distribution")
    return out


def primary_blocker(cap_usd: D, price: Optional[float],
                    order_min_shares: int, reward_min_shares: int,
                    two_sided: bool, on_distribution: bool) -> Optional[str]:
    """The first blocker in precedence order, or None."""
    found = all_blockers(cap_usd, price, order_min_shares,
                         reward_min_shares, two_sided, on_distribution)
    for name in BLOCKER_PRECEDENCE:
        if name in found:
            return name
    return None


def observe_market(slug: str, model: Dict[str, Any],
                   now: Optional[float] = None) -> Dict[str, Any]:
    """One market, one decision row. Read-only; places nothing."""
    now = time.time() if now is None else now
    row: Dict[str, Any] = {"slug": slug, "t": now}
    try:
        ms = _get(GAMMA, "/markets", {"slug": slug})
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {**row, "error": type(exc).__name__}
    if not isinstance(ms, list) or not ms:
        return {**row, "error": "market_not_found"}
    m = ms[0]
    row["accepting_orders"] = bool(m.get("acceptingOrders"))
    row["liquidity_usd"] = float(m.get("liquidity") or 0.0)
    omin = int(m.get("orderMinSize") or DEFAULT_ORDER_MIN_SHARES)
    rmin = int(m.get("rewardsMinSize") or DEFAULT_REWARD_MIN_SHARES)
    row["order_min_shares"] = omin
    row["reward_min_shares"] = rmin
    row["rewards_max_spread_cents"] = m.get("rewardsMaxSpread")

    # real clock: these markets serve endDate, so TTE is a fact
    tte = None
    end = m.get("endDate")
    if end:
        try:
            import datetime as dt
            e = dt.datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            tte = e.timestamp() - now
        except Exception:                                # noqa: BLE001
            tte = None
    row["tte_s"] = tte

    ids = m.get("clobTokenIds")
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except ValueError:
            ids = None
    bids: Dict[str, float] = {}
    if ids and len(ids) >= 2:
        for side, tid in zip(("yes", "no"), ids[:2]):
            try:
                b = _get(CLOB, "/book", {"token_id": tid})
            except (urllib.error.URLError, OSError, ValueError):
                continue
            lv = b.get("bids") if isinstance(b, dict) else None
            if lv:
                bids[side] = max(float(x["price"]) for x in lv)
    two_sided = len(bids) == 2
    row["two_sided"] = two_sided
    if two_sided:
        row["yes_bid"], row["no_bid"] = bids["yes"], bids["no"]
        row["pair_cost"] = round(2.0 - bids["yes"] - bids["no"], 4)
        price = bids["yes"]
    else:
        price = bids.get("yes") or bids.get("no")

    # the frozen model, on the real state. residual 0: we hold nothing.
    on_dist = False
    if price is not None and tte is not None:
        state = _state_key(price, max(0.0, tte), 0.0)
        row["state"] = state
        stats = _modal(model, state)
        if stats:
            on_dist = True
            row.update(stats)
        else:
            row["modal_action"] = None
    row["on_distribution"] = on_dist

    row["all_blockers"] = all_blockers(
        PER_MARKET_CAP_USD, price, omin, rmin, two_sided, on_dist)
    row["primary_blocker"] = primary_blocker(
        PER_MARKET_CAP_USD, price, omin, rmin, two_sided, on_dist)
    row["would_act"] = row["primary_blocker"] is None
    # economics AT THIS STATE, so a blocked row still carries its numbers
    if price is not None:
        row["capital_for_min_order_usd"] = str(D(omin) * D(str(round(price, 4))))
        row["capital_for_reward_min_usd"] = str(D(rmin) * D(str(round(price, 4))))
    row["shadow_only"] = SHADOW_ONLY
    row["order_entry"] = ORDER_ENTRY
    row["realized_pnl"] = None
    return row


def cycle(model: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    rows = [observe_market(s, model, now) for s in current_slugs(now)]
    blockers: Dict[str, int] = {}
    every: Dict[str, int] = {}
    for r in rows:
        b = r.get("primary_blocker") or ("error" if r.get("error") else "none")
        blockers[b] = blockers.get(b, 0) + 1
        for x in (r.get("all_blockers") or []):
            every[x] = every.get(x, 0) + 1
    return {"kind": "L5_NATIVE_SHADOW", "markets": len(rows),
            "would_act": sum(1 for r in rows if r.get("would_act")),
            "primary_blockers": blockers,
            "all_blockers_seen": every,
            "model_sha": model.get("model_sha"),
            "removed_barriers": "see l5_barrier_inventory.to_remove()",
            "shadow_only": SHADOW_ONLY, "order_entry": ORDER_ENTRY,
            "realized_pnl": None, "rows": rows}


def main(argv: Optional[Sequence[str]] = None, *, sleep=time.sleep) -> int:
    interval = float(os.getenv(INTERVAL_ENV, str(DEFAULT_INTERVAL_S)))
    model = load_model()
    print("L5_NATIVE_SHADOW " + json.dumps(
        {"kind": "boot", "assets": list(ASSETS), "windows_ahead": WINDOWS_AHEAD,
         "interval_s": interval, "model_sha": model.get("model_sha"),
         "per_market_cap_usd": str(PER_MARKET_CAP_USD),
         "daily_loss_stop_tripped": DAILY_LOSS_STOP_TRIPPED,
         "shadow_only": SHADOW_ONLY, "order_entry": ORDER_ENTRY}), flush=True)
    n = 0
    while True:
        n += 1
        try:
            out = cycle(model)
            compact = {k: v for k, v in out.items() if k != "rows"}
            compact["cycle"] = n
            print("L5_NATIVE_SHADOW " + json.dumps(compact), flush=True)
            for r in out["rows"]:
                if r.get("would_act") or r.get("error"):
                    print("L5_NATIVE_ROW " + json.dumps(r), flush=True)
        except Exception as exc:                          # noqa: BLE001
            print("L5_NATIVE_ALARM " + json.dumps(
                {"err": f"{type(exc).__name__}:{str(exc)[:120]}"}), flush=True)
        sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
