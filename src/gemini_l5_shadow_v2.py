"""Gemini L5 SHADOW/replay under the SAME v2 policy the PMUS probe runs.

OWNER 5554263833 items 3-4. Two entry points, both zero-order:

* `replay(...)` -- sequential replay over the retained Gemini BTC-5m
  tape (`data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz` quotes +
  `gemini_prints_btc05m.jsonl.gz` prints). Every decision goes through
  `pmus_l5_persistence_v2.proposal` on the normalized state from
  `l5_venue_adapter` (price band | TTE | behavioral residual, last_rel,
  last_size) under the PMUS safety shell: q=1 behavioral unit, maker
  join at the touch, post-only (never at/through the opposite ask),
  rest bounded to 30s and cancelled when the touch moves, residual cap
  3 units, off-distribution -> HOLD, complement <=0.98 pair and >=1c
  after the venue's KNOWN fees, HOLD to settlement. Two policy tracks
  (modal = what the live PMUS engine executes; stochastic = the seeded
  draw the live engine only logs) x two fill tiers (strict = a print
  THROUGH our price; optimistic = a print AT our price, an upper bound).
* `live_shadow_step(...)` -- one realtime decision from a public
  Gemini book (`/v1/book/{symbol}`): what L5 would do now, side, price,
  L5 target size/aggressiveness, and executability (priceable side,
  band, fee-safe complement, spread). Nothing is sent to the venue.

Every P&L number here is THEORETICAL (fills assumed from prints, queue
position unobservable, settlement by the mid proxy). Nothing is
realized; nothing is armed.

    python3 -m src.gemini_l5_shadow_v2            # replay -> analysis/l5/gemini_l5_shadow_v2.json
    python3 -m src.gemini_l5_shadow_v2 --live 30  # 30 min public-book shadow
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.l5_venue_adapter import (GeminiAdapter, L5Inventory, NormalizedBook,
                                  MAX_RESIDUAL_UNITS, decision_record,
                                  l5_state, l5_state_v2, open_side_yes)
from src.polymarket_l5 import transfer_backtest as tb
from src.polymarket_l5.action_model import (PRICE_BANDS, SIZE_BUCKETS, _bucket,
                                            state_key)


# ---- OWNER 5558477213 A/B: canonical lookup (shadow / evaluation only) ----
# v2 (10:14Z-): outcome-price key, pre-fill = OPEN, behavioral last_size.
# v3: + the imbalance in the SAME model-scale shares as last_size (400-window
# replay: v2 left 15 post-fill rows off on one impossible key
# p25-45c|tte2-5m|imb1-10|open|q>40; shares-consistent -> 0 of 16).
LOOKUP_VERSION = 3
SUPERSEDED_LOOKUP_VERSIONS = {2: "superseded: behavioral last_size with a unit-based "
                                 "imbalance (half translation); diagnostic only"}
EXECUTED_SIZE_BUCKET = "q<5"          # the safety-scaled unit's bucket (v1 input)


def behavioral_size_shares(art: Optional[Mapping[str, Any]], base_state: str,
                           relation: str) -> Optional[float]:
    """L5's p50 size (shares) for this state|relation, None when unknown."""
    row = fid.l5_target_size(art, base_state, relation) if art else {}
    p50 = row.get("shares_p50") if isinstance(row, Mapping) else None
    try:
        return float(p50) if p50 is not None else None
    except (TypeError, ValueError):
        return None


def record_behavioral_fill(inv: L5Inventory, art: Optional[Mapping[str, Any]],
                           state_at_fill: str, relation: str, side_up: bool) -> None:
    """After a fill: the model-scale size (bucket + shares) of the action
    the clone behaviorally made, on the filled side."""
    p50 = behavioral_size_shares(art, state_at_fill, relation)
    bucket = (_bucket(p50, SIZE_BUCKETS) if p50 is not None else EXECUTED_SIZE_BUCKET)
    inv.record_behavioral_shares(side_up, p50 if p50 is not None else 0.0, bucket)


def _code_sha() -> str:
    """OWNER 5558646019 #3: the adapter/lookup code identity persisted per
    scored decision (sha256 of this module + the venue adapter source)."""
    import hashlib
    h = hashlib.sha256()
    for name in ("gemini_l5_shadow_v2.py", "l5_venue_adapter.py"):
        try:
            h.update((Path(__file__).resolve().parent / name).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:16]


CODE_SHA = _code_sha()


def split_rows_by_lookup(rows: list) -> dict:
    """OWNER 5558646019 #2/#3: decisions split by PROVEN lookup version.
    v2 = rows written with the canonical lookup (lookup_version 2);
    legacy = rows without a lookup record (UNVERIFIABLE_LEGACY, v1 keys);
    unknown = rows with a lookup record of another/no version. Each
    bucket is returned as row copies carrying only its decisions, so the
    existing scorers run unchanged on a provably homogeneous cohort."""
    def _sub(pred):
        out = []
        for r in rows:
            ds = [d for d in (r.get("decisions") or []) if pred(d)]
            if ds:
                out.append({**r, "decisions": ds})
        return out

    def ver(d):
        lk = d.get("lookup")
        if not isinstance(lk, Mapping):
            return None
        try:
            return int(lk.get("lookup_version") or 0)
        except (TypeError, ValueError):
            return 0
    v2 = _sub(lambda d: ver(d) == LOOKUP_VERSION)
    legacy = _sub(lambda d: ver(d) is None)
    unknown = _sub(lambda d: ver(d) not in (None, LOOKUP_VERSION))
    count = lambda rs: sum(len(r["decisions"]) for r in rs)      # noqa: E731
    superseded: dict = {}
    for r in unknown:
        for d in r["decisions"]:
            v = ver(d)
            superseded[str(v)] = superseded.get(str(v), 0) + 1
    return {"v2": v2, "legacy": legacy, "unknown": unknown,
            "provenance": {"lookup_version_gate": LOOKUP_VERSION,
                           "v2_decisions": count(v2), "legacy_decisions": count(legacy),
                           "unknown_decisions": count(unknown),
                           "superseded_versions": {v: {"decisions": n,
                                                       "label": SUPERSEDED_LOOKUP_VERSIONS.get(int(v), "unknown version")}
                                                   for v, n in superseded.items()},
                           "v2_windows": len({(r.get("symbol"), r.get("entry_t")) for r in v2}),
                           "legacy_label": "UNVERIFIABLE_LEGACY: v1 keys, no persisted "
                                           "lookup; diagnostic only, never a verdict input"}}


def behavioral_size_bucket(art: Optional[Mapping[str, Any]], base_state: str,
                           relation: str) -> str:
    """The model-scale size bucket of the action the clone BEHAVIORALLY
    targets (L5's p50 for this state|relation), never the safety-scaled
    executable size. Unavailable artifact -> the executed bucket."""
    row = fid.l5_target_size(art, base_state, relation) if art else {}
    p50 = row.get("shares_p50") if isinstance(row, Mapping) else None
    try:
        return _bucket(float(p50), SIZE_BUCKETS) if p50 is not None else EXECUTED_SIZE_BUCKET
    except (TypeError, ValueError):
        return EXECUTED_SIZE_BUCKET


def lookup_inputs(book: NormalizedBook, inv: L5Inventory) -> dict:
    """Both state conventions and both last_size values, so the frozen
    model is looked up on the canonical key while the v1 key stays
    recorded for the before/after."""
    pre_fill = inv.total == 0
    return {"lookup_version": LOOKUP_VERSION,
            "state": l5_state_v2(book, inv), "state_v1": l5_state(book, inv),
            "last_rel": inv.last_rel or "open",
            "last_size_executed": EXECUTED_SIZE_BUCKET,
            "last_size_behavioral": (inv.last_size_behavioral
                                     if not pre_fill and inv.last_size_behavioral
                                     else EXECUTED_SIZE_BUCKET),
            "pre_fill": pre_fill,
            "residual_units": inv.behavioral_residual(),
            "residual_shares_behavioral": inv.behavioral_residual_shares(),
            "price_convention": ("outcome price of the last own fill"
                                 if inv.last_px_outcome is not None
                                 else "cheaper touch (outcome the open convention buys)")}


def proposal_v2(model: Mapping[str, Any], book: NormalizedBook,
                inv: L5Inventory) -> dict:
    """The frozen v2 policy looked up on the canonical key (OWNER
    5558477213 A/B). Pre-fill: the canonical history keys NO first-in-
    window transition (every transition is (prev fill, next action)), so
    the only faithful pre-fill relation is OPEN and off-distribution means
    the BASE state is absent -- no state is added, the model is untouched.
    The v1 lookup's off flag is recorded beside it for the before/after."""
    lk = lookup_inputs(book, inv)
    v1 = pv2.v2_dist(model, lk["state_v1"], lk["last_rel"], lk["last_size_executed"])
    lk["off_distribution_v1"] = bool(v1["backed_off"])
    lk["full_key_v1"] = v1["state"]
    prop = pv2.proposal(model, lk["state"], lk["last_rel"], lk["last_size_behavioral"],
                        residual=inv.behavioral_residual())
    if lk["pre_fill"]:
        base_seen = lk["state"] in ((model.get("base") or {}))
        prop["recommended_relation"] = "open"
        prop["relation_ranked"] = [{"relation": "open", "p": 1.0}]
        prop["persist_extend_p"], prop["switch_complete_p"] = 0.0, 0.0
        prop["off_distribution"] = not base_seen
        prop["state_n"] = sum((model.get("base") or {}).get(lk["state"], {}).values())
        lk["lookup_tier"] = ("pre_fill:first_action_is_open (canonical history keys no "
                             "first-in-window transition; off = base state absent)")
    else:
        lk["lookup_tier"] = "full->base->marginal on the canonical key"
    lk["model_sha"] = str(model.get("_sha256_16") or "")
    lk["code_sha"] = CODE_SHA
    lk["state_inputs"] = {"yes_bid": str(book.yes_bid), "yes_ask": str(book.yes_ask),
                          "no_bid": str(book.no_bid), "tte_s": round(float(book.tte_s), 1),
                          "residual_units": inv.behavioral_residual(),
                          "last_px_outcome": (str(inv.last_px_outcome)
                                              if inv.last_px_outcome is not None else None)}
    prop["lookup"] = lk
    return prop

ROOT = Path(__file__).resolve().parents[1]
BBO_PATH = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
PRINTS_PATH = ROOT / "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz"
OUT_PATH = ROOT / "analysis/l5/gemini_l5_shadow_v2.json"
SPORTS_REF = ROOT / "analysis/l5/transfer_backtest_sports.json"

ONE = D("1")
UNIT = D("1")                      # q=1 behavioral unit (one contract)
MAX_ACTIONS = 6                    # PMUS_SPRING_MAX_ACTIONS default
REST_MAX_S = 30.0                  # SPORTS_MAKER_REST_MAX_S
MIN_REEVAL_S = 2.0
ADVERSE_HORIZON_S = 30.0
ADVERSE_HORIZONS_S = (5.0, 15.0, 30.0, 60.0)   # OWNER 5554553793 B item 4
UNIT_CAPITAL_USD = D("0.80")                 # max cost of one q=1 unit
POLICIES = ("modal", "stochastic")
MODES = ("strict", "optimistic")
# OWNER 5554553793 B: execution shells shadowed in parallel. The first is
# the live PMUS shell; the others vary ONLY the rest/cancel discipline
# (the identified transfer blocker). Fills stay strict; zero orders.
SHELLS = {
    "touch_cancel_30s": {"rest_max_s": 30.0, "cancel_on_touch_move": True,
                         "reprice": False, "label": "live PMUS shell"},
    "rest_60s": {"rest_max_s": 60.0, "cancel_on_touch_move": False,
                 "reprice": False, "label": "bounded rest, no cancel"},
    "rest_120s": {"rest_max_s": 120.0, "cancel_on_touch_move": False,
                  "reprice": False, "label": "bounded rest, no cancel"},
    "rest_180s": {"rest_max_s": 180.0, "cancel_on_touch_move": False,
                  "reprice": False, "label": "vendored FIRST_WAIT rest"},
    "reprice_180s": {"rest_max_s": 180.0, "cancel_on_touch_move": True,
                     "reprice": True,
                     "label": "cancel on touch move, re-join the new touch, "
                              "180s total"},
}
LABELS = ("THEORETICAL Gemini shadow/replay; zero orders; fills assumed "
          "from public prints (queue position unobservable); settlement "
          "by mid proxy; nothing realized; nothing armed")


def _book_at(rows: list, t: float) -> Optional[dict]:
    last = None
    for r in rows:
        if r["t"] <= t:
            last = r
        else:
            break
    return last


def _two_sided(r: Mapping[str, Any]) -> bool:
    return r.get("bid") is not None and r.get("ask") is not None


def _mid_at(rows: list, t: float) -> Optional[D]:
    """Mid from the last TWO-SIDED quote at or before t (a live Gemini
    book goes one-sided near expiry; LIVE 21:10:23Z crash)."""
    last = None
    for r in rows:
        if r["t"] > t:
            break
        if _two_sided(r):
            last = r
    if last is None:
        return None
    return (D(last["bid"]) + D(last["ask"])) / 2


def outcome_proxy_safe(rows: list, expiry: float) -> str:
    """Vendored outcome proxy over two-sided quotes only."""
    return tb.outcome_proxy([r for r in rows if _two_sided(r)], expiry)


def _touch_moved_at(rows: list, adapter: GeminiAdapter, side_yes: bool,
                    px: D, t_from: float, t_until: float,
                    expiry: float) -> Optional[float]:
    """First quote after t_from where our touch join is no longer the
    touch (the live engine cancels the resting order then)."""
    for r in rows:
        if r["t"] <= t_from:
            continue
        if r["t"] > t_until:
            break
        b = adapter.normalize(r, expiry=expiry)
        if b is None or b.join_price(side_yes) != px:
            return r["t"]
    return None


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def _quartiles(xs: list) -> Optional[list]:
    xs = sorted(float(x) for x in xs if x is not None)
    if not xs:
        return None
    q = statistics.quantiles(xs, n=4) if len(xs) >= 2 else [xs[0]] * 3
    return [round(v, 4) for v in q]


def _median(xs: list) -> Optional[float]:
    xs = [float(x) for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def replay_window(symbol: str, bbo_rows: list, prints: list, *,
                  model: Mapping[str, Any], art: Optional[Mapping[str, Any]],
                  policy: str, mode: str,
                  adapter: Optional[GeminiAdapter] = None,
                  max_actions: int = MAX_ACTIONS,
                  shell: Optional[Mapping[str, Any]] = None) -> dict:
    adapter = adapter or GeminiAdapter()
    shell = dict(shell or SHELLS["touch_cancel_30s"])
    rest_max = float(shell.get("rest_max_s", REST_MAX_S))
    event = tb.event_of_symbol(symbol)
    expiry = tb.ticker_expiry_epoch(event)
    if expiry is None:
        return {"symbol": symbol, "status": "BAD_TICKER", "policy": policy,
                "fill_mode": mode, "shell": shell.get("name") or "touch_cancel_30s"}
    stop_at = expiry - tb.STOP_BEFORE_EXPIRY_S
    entry = None
    for r in bbo_rows:
        if tb.MIN_TTE_S <= expiry - r["t"] <= tb.ENTRY_MAX_TTE_S and _two_sided(r):
            entry = r
            break
    if entry is None:
        return {"symbol": symbol, "status": "NO_QUOTE_COVERAGE", "policy": policy,
                "fill_mode": mode, "shell": shell.get("name") or "touch_cancel_30s",
                "entry_t": (bbo_rows[0]["t"] if bbo_rows else None)}
    t = entry["t"]
    inv = L5Inventory()
    decisions: list[dict] = []
    orders: list[dict] = []
    fills: list[dict] = []
    model_sha = str(model.get("_sha256_16") or "")
    entry_book = adapter.normalize(entry, expiry=expiry)
    out: dict = {"symbol": symbol, "event": event, "policy": policy,
                 "fill_mode": mode, "entry_t": t,
                 "shell": shell.get("name") or "touch_cancel_30s",
                 "entry_tte_s": round(expiry - t, 1),
                 "entry_book": entry_book.as_dict() if entry_book else None}
    actions = 0
    one_sided = 0
    stop_reason = None
    while t < stop_at and actions < max_actions:
        raw = _book_at(bbo_rows, t)
        book = adapter.normalize(raw, expiry=expiry) if raw else None
        if book is None:
            stop_reason = "no_book"
            break
        if book.yes_bid is None or book.yes_ask is None:
            # one-sided book: nothing to join against; wait, no action
            one_sided += 1
            t += MIN_REEVAL_S
            continue
        prop = proposal_v2(model, book, inv)
        state = prop["lookup"]["state"]
        shadow = fid.shadow_fields(
            art, slug=symbol, decision_idx=len(decisions), model_sha=model_sha,
            base_state=state, relation=prop["recommended_relation"],
            relation_ranked=prop.get("relation_ranked"), tte_s=book.tte_s)
        relation = "open" if inv.total == 0 else prop["recommended_relation"]
        if policy == "stochastic" and inv.total > 0:
            relation = (shadow.get("stochastic") or {}).get("sampled") or relation
        off = bool(prop.get("off_distribution"))
        refusal = None
        if off and inv.total > 0:
            relation, refusal = "hold", "off_distribution_hold"
        elif relation == "extend" and abs(
                inv.behavioral_residual()) >= MAX_RESIDUAL_UNITS:
            relation, refusal = "hold", (
                f"residual_cap:{abs(inv.behavioral_residual())}"
                f">={MAX_RESIDUAL_UNITS}")
        elif relation == "sell":
            relation, refusal = "hold", "sell_is_noop"
        planned = {"contracts": str(UNIT),
                   "usd": str((book.yes_bid or D("0")) * UNIT)}
        rec = decision_record(
            venue=adapter.venue, market_id=symbol, t=t, state=state, inv=inv,
            prop=prop, acted=relation, refusal=refusal, planned_size=planned,
            shadow=shadow, book=book, policy=policy)
        decisions.append(rec)
        if relation == "hold":
            t += max(float(prop["next_reevaluation_s"]), MIN_REEVAL_S)
            actions += 1
            continue
        if relation in ("open", "extend"):
            side_yes = (open_side_yes(book) if inv.residual == 0
                        else inv.residual > 0)
            px = book.join_price(side_yes) if side_yes is not None else None
            if px is None or not adapter.entry_ok(px) or \
                    book.opposite_ask(side_yes) is None or \
                    px >= book.opposite_ask(side_yes):
                rec["refusal"] = (f"no_priceable_side:{px}" if px is None
                                  else f"entry_band_left:{px}")
                rec["acted"] = "hold"
                if inv.total == 0:
                    stop_reason = rec["refusal"]
                    break
                t += MIN_REEVAL_S
                actions += 1
                continue
            t_until = min(t + rest_max, stop_at)
            moved = None
            requotes = 0
            t_cur, px_cur = t, px
            f = None
            while True:
                if shell.get("cancel_on_touch_move"):
                    moved = _touch_moved_at(bbo_rows, adapter, side_yes,
                                            px_cur, t_cur, t_until, expiry)
                seg_until = min(t_until, moved) if moved is not None else t_until
                f = tb.first_fill(prints, "YES" if side_yes else "NO", px_cur,
                                  ONE - px_cur, t_cur, seg_until, mode)
                if f is not None or moved is None or not shell.get("reprice"):
                    break
                # re-join the NEW touch (same side, same relation) within
                # the same bounded rest; post-only still binds
                nb = adapter.normalize(_book_at(bbo_rows, moved), expiry=expiry)
                npx = nb.join_price(side_yes) if nb else None
                if (npx is None or not adapter.entry_ok(npx)
                        or nb.opposite_ask(side_yes) is None
                        or npx >= nb.opposite_ask(side_yes)):
                    break
                requotes += 1
                t_cur, px_cur, moved = moved, npx, None
            if f is not None:
                px = px_cur
            order = {"t": t, "relation": relation,
                     "side": "YES" if side_yes else "NO", "px": str(px_cur),
                     "qty": str(UNIT), "rest_until": (min(t_until, moved)
                                                      if moved is not None
                                                      else t_until),
                     "requotes": requotes,
                     "cancel_reason": ("touch_moved" if moved is not None
                                       and f is None else None),
                     "filled": f is not None,
                     "fill_t": f["t"] if f else None}
            if f is None and moved is not None:
                t_until = min(t_until, moved)
            orders.append(order)
            rec["orders_placed"] = 1
            if f:
                inv.record(side_yes, px, UNIT, relation, f["t"])
                record_behavioral_fill(inv, art, state, relation, side_yes)
                moves: dict = {}
                for h in ADVERSE_HORIZONS_S:
                    later = _mid_at(bbo_rows, f["t"] + h)
                    if later is not None:
                        own_mid = later if side_yes else ONE - later
                        moves[str(int(h))] = float(own_mid - px)
                move = moves.get("30")
                fills.append({"t": f["t"], "relation": relation,
                              "side": order["side"], "px": str(px),
                              "tte_s": round(expiry - f["t"], 1),
                              "rest_s": round(f["t"] - t, 1),
                              "mid_move_30s": move, "mid_moves": moves,
                              "adverse": (move < 0) if move is not None else None})
                t = f["t"]
            else:
                t = t_until
        else:                                   # complete: deficit side only
            if inv.residual == 0:
                stop_reason = "flat_complete"
                break
            deficit_up = inv.residual < 0
            surplus_avg = inv.avg_dn() if deficit_up else inv.avg_up()
            opp_ask = book.opposite_ask(deficit_up)
            px, econ = adapter.complement_price(surplus_avg, opp_ask, book.tick)
            rec["pair_economics"] = econ
            if px is None:
                t += MIN_REEVAL_S
                actions += 1
                continue
            qty = min(inv.residual.copy_abs(), UNIT)
            t_until = min(t + rest_max, stop_at)
            f = tb.complement_fill(prints, "NO" if deficit_up else "YES",
                                   px, t, t_until, mode)
            orders.append({"t": t, "relation": "complete",
                           "side": "YES" if deficit_up else "NO",
                           "px": str(px), "qty": str(qty),
                           "rest_until": t_until, "filled": f is not None,
                           "fill_t": f["t"] if f else None})
            rec["orders_placed"] = 1
            if f:
                inv.record(deficit_up, px, qty, "complete", f["t"])
                record_behavioral_fill(inv, art, state, "complete", deficit_up)
                fills.append({"t": f["t"], "relation": "complete",
                              "side": "YES" if deficit_up else "NO",
                              "px": str(px), "tte_s": round(expiry - f["t"], 1),
                              "rest_s": round(f["t"] - t, 1),
                              "mid_move_30s": None, "adverse": None})
                t = f["t"]
            else:
                t = t_until
        actions += 1
    # ---- theoretical settlement (mid proxy; never realized) ----
    proxy = outcome_proxy_safe(bbo_rows, expiry)
    pair_pnl = hold_pnl = None
    fees = D("0")
    if inv.matched > 0:
        y, n = inv.avg_up(), inv.avg_dn()
        fees = (adapter.maker_fee(y, inv.matched) + adapter.maker_fee(n, inv.matched))
        pair_pnl = (ONE - y - n) * inv.matched - fees
    if inv.residual != 0:
        side_up = inv.residual > 0
        cost = inv.avg_up() if side_up else inv.avg_dn()
        q = inv.residual.copy_abs()
        fee = adapter.maker_fee(cost, q)
        if proxy != "AMBIGUOUS":
            won = side_up == (proxy == "UP")
            hold_pnl = ((ONE - cost) * q - fee) if won else (-cost * q - fee)
    status = ("MATCHED" if inv.matched > 0 and inv.residual == 0 else
              "RESIDUAL_HOLD" if inv.total > 0 else "NO_FILL")
    return {**out, "status": status, "stop_reason": stop_reason,
            "one_sided_book_waits": one_sided,
            "decisions": decisions, "orders": orders, "fills": fills,
            "inventory": inv.snapshot(), "pair_unit_cost": str(inv.pair_cost()),
            "pair_cost_after_fees": (str(inv.pair_cost() + fees / inv.matched)
                                     if inv.matched else None),
            "fees_theoretical": str(fees),
            "outcome_proxy": proxy,
            "pair_pnl_theoretical": str(pair_pnl) if pair_pnl is not None else None,
            "hold_pnl_theoretical": str(hold_pnl) if hold_pnl is not None else None,
            "flow": tb.window_flow(prints, expiry)}


def summarize(rows: list[dict]) -> dict:
    ent = [r for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD",
                                                   "NO_FILL")]
    dec = [d for r in ent for d in r["decisions"]]
    acted: dict[str, int] = {}
    refusals: dict[str, int] = {}
    disagree = 0
    for d in dec:
        acted[d["acted"]] = acted.get(d["acted"], 0) + 1
        if d.get("refusal"):
            key = str(d["refusal"]).split(":")[0]
            refusals[key] = refusals.get(key, 0) + 1
        disagree += int(bool(d.get("shadow_differs_from_executed")))
    orders = [o for r in ent for o in r["orders"]]
    first_orders = [o for o in orders if o["relation"] in ("open", "extend")]
    comp_orders = [o for o in orders if o["relation"] == "complete"]
    fills = [f for r in ent for f in r["fills"]]
    first_fills = [f for f in fills if f["relation"] in ("open", "extend")]
    with_first = [r for r in ent if any(f["relation"] != "complete"
                                        for f in r["fills"])]
    matched = [r for r in ent if r["status"] == "MATCHED"]
    any_matched = [r for r in ent if D(r["inventory"]["matched"]) > 0]
    holds = [r for r in ent if r["status"] == "RESIDUAL_HOLD"]
    adverse = [f for f in first_fills if f.get("adverse") is not None]
    days = sorted({datetime.fromtimestamp(r["entry_t"], timezone.utc)
                   .strftime("%y%m%d") for r in ent})
    pair_pnl = sum(D(r["pair_pnl_theoretical"]) for r in ent
                   if r.get("pair_pnl_theoretical"))
    hold_pnl = sum(D(r["hold_pnl_theoretical"]) for r in ent
                   if r.get("hold_pnl_theoretical"))
    amb = sum(1 for r in holds if r["outcome_proxy"] == "AMBIGUOUS")
    hold_won = sum(1 for r in holds if r.get("hold_pnl_theoretical")
                   and D(r["hold_pnl_theoretical"]) > 0)
    hold_lost = sum(1 for r in holds if r.get("hold_pnl_theoretical")
                    and D(r["hold_pnl_theoretical"]) < 0)
    entry_books = [r["entry_book"] for r in ent if r.get("entry_book")]
    n_days = max(len(days), 1)
    return {
        "windows_replayed": len(rows), "campaigns": len(ent),
        "distinct_days": days, "campaigns_per_day": round(len(ent) / n_days, 1),
        "decisions": len(dec),
        "decisions_per_campaign_p50": _median(
            [len(r["decisions"]) for r in ent]),
        "acted_by_relation": acted, "refusals_by_reason": refusals,
        "shadow_vs_executed_disagreements": disagree,
        "orders_submitted": len(orders),
        "first_leg_orders": len(first_orders),
        "first_leg_fills": len(first_fills),
        "first_leg_fill_rate_per_order": _pct(len(first_fills), len(first_orders)),
        "campaign_first_fill_rate": _pct(len(with_first), len(ent)),
        "complement_orders": len(comp_orders),
        "complement_fills": len(fills) - len(first_fills),
        "p_complement_given_first_fill": _pct(len(any_matched), len(with_first)),
        "two_sided_fill_campaigns": len(any_matched),
        "fully_matched_campaigns": len(matched),
        "matched_per_day": round(len(any_matched) / n_days, 2),
        "one_sided_holds": len(holds),
        "one_sided_hold_rate": _pct(len(holds), len(ent)),
        "touch_moved_cancels": sum(1 for o in first_orders
                                   if o.get("cancel_reason") == "touch_moved"),
        "median_pair_cost_after_fees": _median(
            [r["pair_cost_after_fees"] for r in any_matched]),
        "fees_theoretical_total": str(sum(D(r["fees_theoretical"]) for r in ent)),
        "pair_pnl_theoretical_total": str(pair_pnl),
        "hold_pnl_theoretical_total": str(hold_pnl),
        "holds_residual_won": hold_won, "holds_residual_lost": hold_lost,
        "holds_ambiguous_excluded": amb,
        "net_theoretical_total": str(pair_pnl + hold_pnl),
        "adverse_selection_30s": {
            "fills_measured": len(adverse),
            "pct_adverse": _pct(sum(1 for f in adverse if f["adverse"]),
                                len(adverse)),
            "mean_mid_move": (round(statistics.fmean(
                f["mid_move_30s"] for f in adverse), 4) if adverse else None)},
        "adverse_selection_by_horizon": {
            h: {"n": len([f for f in first_fills
                          if (f.get("mid_moves") or {}).get(h) is not None]),
                "pct_adverse": _pct(
                    sum(1 for f in first_fills
                        if (f.get("mid_moves") or {}).get(h) is not None
                        and f["mid_moves"][h] < 0),
                    len([f for f in first_fills
                         if (f.get("mid_moves") or {}).get(h) is not None])),
                "mean_mid_move": (round(statistics.fmean(
                    f["mid_moves"][h] for f in first_fills
                    if (f.get("mid_moves") or {}).get(h) is not None), 4)
                    if any((f.get("mid_moves") or {}).get(h) is not None
                           for f in first_fills) else None)}
            for h in ("5", "15", "30", "60")},
        "pair_cost_after_fees_p25_p50_p75": _quartiles(
            [r["pair_cost_after_fees"] for r in any_matched]),
        "matched_pairs_le_099": sum(
            1 for r in any_matched
            if r.get("pair_cost_after_fees") is not None
            and D(str(r["pair_cost_after_fees"])) <= D("0.99")),
        "residual_duration_s_p50": _median(
            [r["fills"][0]["tte_s"] for r in holds if r.get("fills")]),
        "residual_outcomes": {"won": hold_won, "lost": hold_lost,
                              "ambiguous": amb},
        "capital_turn_per_day_estimate": round(float(sum(
            D(r["pair_unit_cost"]) * D(r["inventory"]["matched"])
            for r in any_matched)) / n_days / float(UNIT_CAPITAL_USD), 3),
        "first_fill_tte_s_p50": _median([f["tte_s"] for f in first_fills]),
        "first_fill_rest_s_p50": _median([f["rest_s"] for f in first_fills]),
        "entry_spread_p50": _median([b["spread"] for b in entry_books]),
        "entry_depth_bid_p50": _median([b["yes_bid_qty"] for b in entry_books
                                        if b["yes_bid_qty"] != "None"]),
        "entry_depth_ask_p50": _median([b["yes_ask_qty"] for b in entry_books
                                        if b["yes_ask_qty"] != "None"]),
        "window_flow_two_sided_pct": _pct(
            sum(1 for r in ent if r["flow"]["two_sided"]), len(ent)),
        "capacity_q1_usd_per_day_matched": round(float(sum(
            D(r["pair_unit_cost"]) * D(r["inventory"]["matched"])
            for r in any_matched)) / n_days, 2),
    }


def replay(bbo_path: Path = BBO_PATH, prints_path: Path = PRINTS_PATH,
           limit: Optional[int] = None,
           sensitivity_actions: int = 30) -> dict:
    model = pv2.load_v2()
    art = fid.load()
    bbo = tb.load_bbo(bbo_path)
    prints = tb.load_prints(prints_path)
    symbols = sorted(s for s in bbo if s in prints)
    if limit:
        symbols = symbols[:limit]
    results: dict[str, Any] = {}
    for policy in POLICIES:
        for mode in MODES:
            rows = [replay_window(s, bbo[s], prints[s], model=model, art=art,
                                  policy=policy, mode=mode) for s in symbols]
            results[f"{policy}:{mode}"] = summarize(rows)
            results[f"{policy}:{mode}"]["sample_windows"] = [
                {k: r.get(k) for k in ("symbol", "status", "stop_reason",
                                       "inventory", "pair_cost_after_fees",
                                       "outcome_proxy", "pair_pnl_theoretical",
                                       "hold_pnl_theoretical")}
                for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD")][:12]
    # OWNER 5554553793 B: the rest/cancel discipline shadowed across shells
    # (strict fills only, both policy tracks)
    shells: dict[str, Any] = {}
    for name, sh in SHELLS.items():
        for policy in POLICIES:
            rows = [replay_window(s, bbo[s], prints[s], model=model, art=art,
                                  policy=policy, mode="strict",
                                  shell={**sh, "name": name}) for s in symbols]
            shells[f"{name}:{policy}"] = {**summarize(rows),
                                          "requotes": sum(
                                              o.get("requotes", 0) for r in rows
                                              for o in r.get("orders") or [])}
            shells[f"{name}:{policy}"].pop("sample_windows", None)
    # shell sensitivity: the PMUS session bound (6 decisions) covers only
    # the first minute of a 5-minute window; show the ceiling when the
    # same shell may keep deciding for the whole window
    sens = {}
    for policy in POLICIES:
        rows = [replay_window(s, bbo[s], prints[s], model=model, art=art,
                              policy=policy, mode="strict",
                              max_actions=sensitivity_actions) for s in symbols]
        sens[f"{policy}:strict:max_actions={sensitivity_actions}"] = summarize(rows)
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "labels": LABELS,
            "provenance": {"bbo": str(bbo_path.name), "prints": str(prints_path.name),
                           "symbols_with_quotes_and_prints": len(symbols),
                           "model": model.get("_sha256_16"),
                           "fidelity_artifact": (art or {}).get("_sha256_16"),
                           "mechanics_vendored_from": "feature/gemini-l5-dynamic-q1-v1policy @ 80f40c86"},
            "shell": {"unit": str(UNIT), "max_actions": MAX_ACTIONS,
                      "rest_max_s": REST_MAX_S, "residual_cap_units": MAX_RESIDUAL_UNITS,
                      "pair_target": "0.98", "min_edge_after_known_fees": "0.01",
                      "maker_only": True, "taker": "disabled"},
            "results": results,
            "shells_strict": shells,
            "sensitivity": sens,
            "pmus_reference": pmus_reference()}


def pmus_reference() -> dict:
    """Same-engine-class PMUS/Polymarket sports figures from the retained
    strict/optimistic replay (print-only tier), for the side-by-side."""
    try:
        s = json.loads(SPORTS_REF.read_text())
    except OSError:
        return {"available": False}
    fam = (s.get("families") or {}).get("mlb:moneyline") or {}
    keep = ("slots", "first_leg_fill_rate", "p_complement_given_first_fill",
            "one_sided_hold_rate", "matched", "median_pair_cost",
            "mean_time_to_complement_s", "hold_pnl_theoretical_total")
    return {"available": True, "source": SPORTS_REF.name,
            "family": "mlb:moneyline", "flow": fam.get("flow"),
            "strict": {k: (fam.get("strict") or {}).get(k) for k in keep},
            "optimistic": {k: (fam.get("optimistic") or {}).get(k) for k in keep},
            "labels": s.get("labeling")}


# ------------------------- distributional action scoring -------------------------

def action_scoring(rows: list) -> dict:
    """OWNER 5555481658 item 3: relation-frequency distance and calibration
    of an arm against the historical L5 conditional distribution, decision
    by decision, on the rows given (the caller restricts to untouched
    windows). Two views: SAMPLED/RECOMMENDED = the policy output before
    the safety shell (pure policy fidelity); ACTED = what the shell let
    through (executed fidelity; HOLD is not an L5 relation and is reported
    with its refusal reasons). Post-fill states are compared separately."""
    decs = [d for r in rows for d in (r.get("decisions") or [])
            if d.get("p_complete") is not None]
    if not decs:
        return {"scored_decisions": 0}

    def masses(d):
        pc, pe = float(d.get("p_complete") or 0), float(d.get("p_extend") or 0)
        po = max(0.0, 1.0 - pc - pe)
        return {"complete": pc, "extend": pe, "open": po}

    def freq(items, key):
        c: dict = {}
        for d in items:
            v = d.get(key)
            if v in ("complete", "extend", "open"):
                c[v] = c.get(v, 0) + 1
        n = sum(c.values()) or 1
        return {k: round(c.get(k, 0) / n, 4) for k in ("complete", "extend", "open")}, sum(c.values())

    def expected(items):
        n = len(items) or 1
        agg = {"complete": 0.0, "extend": 0.0, "open": 0.0}
        for d in items:
            for k, v in masses(d).items():
                agg[k] += v
        return {k: round(v / n, 4) for k, v in agg.items()}

    def l1(a, b):
        return round(sum(abs(a.get(k, 0) - b.get(k, 0)) for k in ("complete", "extend", "open")), 4)

    exp_all = expected(decs)
    samp, n_s = freq(decs, "sampled")
    acted, n_a = freq(decs, "acted")
    holds = [d for d in decs if d.get("acted") == "hold"]
    post = [d for d in decs if abs(float(d.get("residual_units") or 0)) >= 1]
    exp_post = expected(post) if post else None
    samp_post, _ = freq(post, "sampled") if post else ({}, 0)
    acted_post, _ = freq(post, "acted") if post else ({}, 0)
    # calibration: decisions binned by P(complete) vs observed sampled COMPLETE
    bins: dict = {}
    for d in decs:
        pc = float(d.get("p_complete") or 0)
        b = f"{int(pc * 10) / 10:.1f}"
        e = bins.setdefault(b, {"n": 0, "sampled_complete": 0, "acted_complete": 0})
        e["n"] += 1
        e["sampled_complete"] += int(d.get("sampled") == "complete")
        e["acted_complete"] += int(d.get("acted") == "complete")
    calib = {b: {"n": e["n"], "sampled_complete_rate": round(e["sampled_complete"] / e["n"], 3),
                 "acted_complete_rate": round(e["acted_complete"] / e["n"], 3)}
             for b, e in sorted(bins.items())}
    refusals: dict = {}
    for d in holds:
        k = str(d.get("refusal")).split(":")[0]
        refusals[k] = refusals.get(k, 0) + 1
    return {
        "scored_decisions": len(decs),
        "expected_relation_freq_l5": exp_all,
        "sampled_relation_freq": samp, "sampled_n": n_s,
        "l1_sampled_vs_l5": l1(samp, exp_all),
        "acted_relation_freq": acted, "acted_n": n_a,
        "l1_acted_vs_l5": l1(acted, exp_all),
        "hold_share_of_decisions": round(len(holds) / len(decs), 4),
        "hold_refusals": refusals,
        "post_fill": {
            "decisions": len(post),
            "expected_l5": exp_post, "sampled": samp_post, "acted": acted_post,
            "complete_freq_gap_pp_sampled": (round(100 * (samp_post.get("complete", 0) - exp_post["complete"]), 1)
                                             if post else None),
            "complete_freq_gap_pp_acted": (round(100 * (acted_post.get("complete", 0) - exp_post["complete"]), 1)
                                           if post else None),
        },
        "calibration_by_p_complete_bin": calib,
        "off_distribution_rate_pct": round(100.0 * sum(1 for d in decs if d.get("off_distribution")) / len(decs), 2),
        "labels": "distributional fidelity vs the frozen v2 conditional masses; THEORETICAL",
    }


GEMINI_WINDOW_S = 300.0


def _state_parts(state: Any) -> tuple:
    parts = str(state or "").split("|")
    return tuple((parts + [None, None, None])[:3])


def corrected_lookup_for_row(d: Mapping[str, Any], model: Mapping[str, Any],
                             art: Optional[Mapping[str, Any]]) -> dict:
    """OWNER 5558477213 B: row-level counterfactual for ONE recorded
    decision -- the state key the canonical lookup would use, and whether
    the row stays off-distribution under it. Rows written with the v2
    lookup carry both flags already; legacy rows are recomputed from the
    persisted observation (cheaper touch pre-fill; behavioral last_size
    post-fill; the post-fill price convention cannot be re-derived
    without the own-fill outcome price and is marked unverified)."""
    base = model.get("base") or {}
    lk = d.get("lookup") or {}
    if lk:
        return {"source": "recorded_v2_lookup", "state_v1": lk.get("state_v1"),
                "state_corrected": lk.get("state"),
                "off_v1": lk.get("off_distribution_v1"),
                "off_corrected": bool(d.get("off_distribution")),
                "tier": lk.get("lookup_tier"), "last_size_behavioral": lk.get("last_size_behavioral")}
    try:
        residual = abs(float(d.get("residual_units") or 0))
    except (TypeError, ValueError):
        residual = 0.0
    state = d.get("state")
    out = {"source": "legacy_row_recomputed", "state_v1": state,
           "off_v1": bool(d.get("off_distribution"))}
    yb, ya, tte = d.get("yes_bid"), d.get("yes_ask"), d.get("tte_s")
    if residual < 1:
        try:
            y, n = float(yb), 1.0 - float(ya)
            px = min(y, n)
            st2 = state_key(px, float(tte), 0.0)
        except (TypeError, ValueError):
            out.update({"state_corrected": None, "off_corrected": None,
                        "tier": "pre_fill: observation missing (row predates persisted book)"})
            return out
        out.update({"state_corrected": st2, "off_corrected": st2 not in base,
                    "tier": "pre_fill:first_action_is_open (off = base state absent)",
                    "price_input": {"yes_bid": yb, "no_bid": round(n, 4), "cheaper_touch": px}})
        return out
    lr = d.get("last_rel") or "open"
    lsb = behavioral_size_bucket(art, str(state), lr) if art else None
    if lsb is None:
        out.update({"state_corrected": state, "off_corrected": None,
                    "tier": "post_fill: fidelity artifact unavailable"})
        return out
    v = pv2.v2_dist(model, str(state), lr, lsb)
    out.update({"state_corrected": state, "off_corrected": bool(v["backed_off"]),
                "last_size_behavioral": lsb,
                "tier": "post_fill: behavioral last_size; price convention unverified "
                        "(own-fill outcome price not persisted on legacy rows)"})
    return out


def off_distribution_attribution(rows: list, model: Optional[Mapping[str, Any]],
                                 window_s: float = GEMINI_WINDOW_S,
                                 art: Optional[Mapping[str, Any]] = None,
                                 max_rows: int = 60) -> dict:
    """OWNER 5558167805 #2/#4: state-level attribution of every
    off-distribution shadow decision -- the exact frozen state key, which
    component is outside/absent relative to the frozen model's base
    states, the market observation that produced it, and the cause class:
      (a) adapter/translation defect, (b) genuinely unseen transfer state,
      (c) missing/incorrect inventory/fill state, (d) coverage/data.
    Read-only: no state is added, no threshold smoothed."""
    base = (model or {}).get("base") or {}
    seen_p, seen_t, seen_i = set(), set(), set()
    seen_pt, seen_pi, seen_ti = set(), set(), set()
    for k in base:
        p, t, i = _state_parts(k)
        seen_p.add(p); seen_t.add(t); seen_i.add(i)
        seen_pt.add((p, t)); seen_pi.add((p, i)); seen_ti.add((t, i))
    decs = [d for r in rows for d in (r.get("decisions") or [])
            if d.get("p_complete") is not None]
    off = [d for d in decs if d.get("off_distribution")]
    by_state: dict = {}
    causes: dict = {}
    for d in off:
        state = d.get("state")
        p, t, i = _state_parts(state)
        base_seen = state in base
        absent: list = []
        if not base_seen:
            if p not in seen_p:
                absent.append("price_band")
            if t not in seen_t:
                absent.append("tte")
            if i not in seen_i:
                absent.append("imbalance")
            if not absent:
                for name, ok in (("price_band+tte", (p, t) in seen_pt),
                                 ("price_band+imbalance", (p, i) in seen_pi),
                                 ("tte+imbalance", (t, i) in seen_ti)):
                    if not ok:
                        absent.append(name)
                if not absent:
                    absent.append("full_combination")
        try:
            residual = abs(float(d.get("residual_units") or 0))
        except (TypeError, ValueError):
            residual = 0.0
        tte_s = d.get("tte_s")
        try:
            tte_s = None if tte_s is None else float(tte_s)
        except (TypeError, ValueError):
            tte_s = None
        if t == "tte>10m" or (t == "tte5-10m" and window_s <= 300.0) or \
                (tte_s is not None and (tte_s < 0 or tte_s > window_s)):
            cause = "(a) adapter/translation defect: TTE bucket impossible for the window length"
        elif p not in {b for _, b in PRICE_BANDS}:
            cause = "(a) adapter/translation defect: price band outside the model's band set"
        elif i != "flat" and residual < 1:
            cause = "(c) inventory/fill state: imbalance bucket without a behavioral residual"
        elif i == "flat" and residual >= 1:
            cause = "(c) inventory/fill state: residual held but imbalance bucket flat"
        elif not state or d.get("yes_bid") in ("None", ""):
            cause = "(d) coverage/data: no state or no book observation"
        elif base_seen:
            # the base state exists; the FULL key (state|last_rel|last_size)
            # the clone looked up does not. Name which component: the
            # clone keys a pre-fill decision as |open|q<5 (the builder's
            # convention for a window's first action) and every post-fill
            # decision with last_size q<5 (its behavioral unit)
            full = (model or {}).get("full") or {}
            lr = d.get("last_rel") or "open"
            same_rel_other_size = [k for k in full
                                   if k.startswith(f"{state}|{lr}|")]
            if residual < 1 and lr == "open":
                cause = ("(b) unseen transfer state: L5 never OPENED a window pre-fill "
                         "in this price band|TTE (the base state exists only from "
                         "post-completion flat states)")
                absent = ["pre-fill open in price_band+tte"]
            elif same_rel_other_size:
                cause = ("(a) translation: the size-cap override fixes last_size at "
                         "q<5; L5's key at this state|last_rel exists only with other "
                         f"size buckets ({', '.join(sorted(k.split('|')[-1] for k in same_rel_other_size))})")
                absent = ["last_size (q<5 vs L5 buckets)"]
            else:
                cause = ("(b) genuinely thin transfer state: base state seen, no full "
                         f"key with last_rel={lr}")
                absent = [f"last_rel={lr}"]
        else:
            cause = "(b) genuinely unseen transfer state: " + "+".join(absent)
        e = by_state.setdefault(str(state), {
            "n": 0, "state_n": d.get("state_n"), "base_state_seen": base_seen,
            "absent_components": absent, "cause": cause, "causes": {},
            "refusals": {}, "post_fill": 0, "acted": {},
            "observation": {"tte_s_example": tte_s, "yes_bid_example": d.get("yes_bid"),
                            "residual_units_example": d.get("residual_units"),
                            "last_rel_example": d.get("last_rel")}})
        e["n"] += 1
        e["causes"][cause] = e["causes"].get(cause, 0) + 1
        e["cause"] = max(e["causes"], key=e["causes"].get)      # majority cause
        k = str(d.get("refusal")).split(":")[0] if d.get("refusal") else "none"
        e["refusals"][k] = e["refusals"].get(k, 0) + 1
        e["acted"][str(d.get("acted"))] = e["acted"].get(str(d.get("acted")), 0) + 1
        if residual >= 1:
            e["post_fill"] += 1
        causes[cause] = causes.get(cause, 0) + 1
    # cross-tab with the entry band and post-fill (OWNER item 4)
    band_all = [d for d in decs if str(d.get("refusal") or "").startswith("entry_band_left")]
    band_off = [d for d in band_all if d.get("off_distribution")]
    band_states = {d.get("state") for d in band_all}
    off_states = set(by_state)
    # OWNER 5558477213 B: row-level before/after under the canonical lookup
    row_cf = []
    ba = {"off_v1_rows": 0, "off_corrected_rows": 0, "unverifiable_rows": 0,
          "recorded_v2_rows": 0, "legacy_rows": 0, "per_state": {}}
    for d in off:
        cf = corrected_lookup_for_row(d, model or {}, art)
        ba["recorded_v2_rows" if cf["source"] == "recorded_v2_lookup" else "legacy_rows"] += 1
        if cf.get("off_v1"):
            ba["off_v1_rows"] += 1
        if cf.get("off_corrected") is None:
            ba["unverifiable_rows"] += 1
        elif cf["off_corrected"]:
            ba["off_corrected_rows"] += 1
        ps = ba["per_state"].setdefault(str(d.get("state")), {"off_v1": 0, "off_corrected": 0,
                                                              "unverifiable": 0})
        ps["off_v1"] += 1 if cf.get("off_v1") else 0
        if cf.get("off_corrected") is None:
            ps["unverifiable"] += 1
        elif cf["off_corrected"]:
            ps["off_corrected"] += 1
        if len(row_cf) < max_rows:
            row_cf.append({"t": d.get("t"), "state": d.get("state"),
                           "yes_bid": d.get("yes_bid"), "yes_ask": d.get("yes_ask"),
                           "tte_s": d.get("tte_s"), "residual_units": d.get("residual_units"),
                           "last_rel": d.get("last_rel"), "refusal": d.get("refusal"),
                           "acted": d.get("acted"), "sampled": d.get("sampled"), **cf})
    # rows carrying BOTH flags (written by the v2 lookup): the live before/after
    both = [d for d in decs if (d.get("lookup") or {}).get("off_distribution_v1") is not None]
    ba["rows_with_both_flags"] = len(both)
    ba["off_v1_rate_pct_on_both"] = (round(100.0 * sum(
        1 for d in both if d["lookup"]["off_distribution_v1"]) / len(both), 2) if both else None)
    ba["off_v2_rate_pct_on_both"] = (round(100.0 * sum(
        1 for d in both if d.get("off_distribution")) / len(both), 2) if both else None)
    # OWNER 5558646019 #3: the corrected-lookup rows get their own state
    # table -- these are the only off rows that can reach the gate
    by_state_v2: dict = {}
    for d in off:
        lk = d.get("lookup") or {}
        if int(lk.get("lookup_version") or 0) != LOOKUP_VERSION:
            continue
        e = by_state_v2.setdefault(str(d.get("state")), {
            "n": 0, "tier": lk.get("lookup_tier"), "state_v1": lk.get("state_v1"),
            "refusals": {}, "post_fill": 0, "last_size_behavioral": lk.get("last_size_behavioral"),
            "last_rel": lk.get("last_rel"),
            "inputs_example": lk.get("state_inputs")})
        e["n"] += 1
        k = str(d.get("refusal")).split(":")[0] if d.get("refusal") else "none"
        e["refusals"][k] = e["refusals"].get(k, 0) + 1
        try:
            if abs(float(d.get("residual_units") or 0)) >= 1:
                e["post_fill"] += 1
        except (TypeError, ValueError):
            pass
    return {
        "before_after_canonical_lookup": ba,
        "off_distribution_rows": row_cf,
        "by_state_v2": dict(sorted(by_state_v2.items(), key=lambda kv: -kv[1]["n"])),
        "scored_decisions": len(decs), "off_distribution_decisions": len(off),
        "off_distribution_rate_pct": round(100.0 * len(off) / len(decs), 2) if decs else None,
        "off_states": len(off_states),
        "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1]["n"])),
        "causes": causes,
        "cross_tab": {
            "entry_band_left_decisions": len(band_all),
            "entry_band_left_and_off_distribution": len(band_off),
            "off_states_also_entry_band_states": len(off_states & band_states),
            "off_decisions_post_fill": sum(e["post_fill"] for e in by_state.values()),
            "off_decisions_pre_fill": len(off) - sum(e["post_fill"] for e in by_state.values()),
        },
        "labels": "READ-ONLY diagnosis; no state added, no threshold smoothed, "
                  "frozen population unchanged",
    }


def sampled_to_acted_funnel(rows: list) -> dict:
    """OWNER 5558167805 #3: the sampled -> attempted -> acted -> hold funnel
    by refusal reason, per sampled relation, so policy fidelity (the draw)
    is separated from execution-shell adaptation (what the shell let
    through)."""
    decs = [d for r in rows for d in (r.get("decisions") or [])
            if d.get("p_complete") is not None]
    per: dict = {}
    for d in decs:
        s = d.get("sampled") or d.get("v2_recommended") or "unknown"
        a = d.get("acted")
        e = per.setdefault(s, {"sampled_n": 0, "acted_as_sampled": 0,
                               "converted_to": {}, "held_by_reason": {}})
        e["sampled_n"] += 1
        if a == s:
            e["acted_as_sampled"] += 1
        elif a == "hold":
            k = str(d.get("refusal")).split(":")[0] if d.get("refusal") else "unnamed"
            e["held_by_reason"][k] = e["held_by_reason"].get(k, 0) + 1
        else:
            e["converted_to"][str(a)] = e["converted_to"].get(str(a), 0) + 1
    n = len(decs) or 1
    held = sum(sum(e["held_by_reason"].values()) for e in per.values())
    conv = sum(sum(e["converted_to"].values()) for e in per.values())
    same = sum(e["acted_as_sampled"] for e in per.values())
    reasons: dict = {}
    for e in per.values():
        for k, v in e["held_by_reason"].items():
            reasons[k] = reasons.get(k, 0) + v
    return {"scored_decisions": len(decs), "per_sampled_relation": per,
            "acted_as_sampled_share": round(same / n, 4),
            "held_share": round(held / n, 4), "converted_share": round(conv / n, 4),
            "held_by_reason": reasons,
            "reading": ("the draw is the policy; hold/conversion shares are the "
                        "execution shell's suppression of that draw, by reason")}


def research_arm_verdict(rows: list, spec: Mapping[str, Any]) -> dict:
    """Frozen stochastic OOS arm: summary + action scoring + verdict from
    the spec file only."""
    ent = [r for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD", "NO_FILL")]
    succ, fail = spec["success"], spec["fail"]
    s = summarize(ent) if ent else {}
    sc = action_scoring(ent)
    days = len(s.get("distinct_days") or [])
    matched = [r for r in ent if D(r["inventory"]["matched"]) > 0]
    mdays = len({_day(r) for r in matched})
    net = D(s.get("net_theoretical_total") or 0) if ent else D("0")
    adv = (s.get("adverse_selection_by_horizon") or {}).get("30", {}).get("pct_adverse")
    l1s = sc.get("l1_sampled_vs_l5")
    gap = (sc.get("post_fill") or {}).get("complete_freq_gap_pp_sampled")
    checks = {
        "windows": (len(ent), succ["min_windows"], len(ent) >= succ["min_windows"]),
        "days": (days, succ["min_days"], days >= succ["min_days"]),
        "two_sided_completions": (len(matched), succ["min_two_sided_completions"], len(matched) >= succ["min_two_sided_completions"]),
        "completion_days": (mdays, succ["min_completion_days"], mdays >= succ["min_completion_days"]),
        "scored_decisions": (sc.get("scored_decisions", 0), succ["calibration"]["min_scored_decisions"], sc.get("scored_decisions", 0) >= succ["calibration"]["min_scored_decisions"]),
        "l1_sampled_vs_l5": (l1s, succ["calibration"]["max_relation_l1_distance_sampled_vs_model"], l1s is not None and l1s <= succ["calibration"]["max_relation_l1_distance_sampled_vs_model"]),
        "post_fill_complete_gap_pp": (gap, succ["calibration"]["max_post_fill_complete_freq_abs_gap_pp"], gap is not None and abs(gap) <= succ["calibration"]["max_post_fill_complete_freq_abs_gap_pp"]),
        "net_after_fees_usd": (str(net), succ["min_net_after_known_fees_usd"], net >= D(str(succ["min_net_after_known_fees_usd"]))),
        "adverse_30s_pct": (adv, succ["max_adverse_selection_30s_pct"], adv is not None and adv <= succ["max_adverse_selection_30s_pct"]),
        "one_sided_hold_rate_pct": (s.get("one_sided_hold_rate"), succ["max_one_sided_hold_rate_pct"], s.get("one_sided_hold_rate") is not None and s["one_sided_hold_rate"] <= succ["max_one_sided_hold_rate_pct"]),
        "off_distribution_rate_pct": (sc.get("off_distribution_rate_pct"), succ["max_off_distribution_rate_pct"], sc.get("off_distribution_rate_pct") is not None and sc["off_distribution_rate_pct"] <= succ["max_off_distribution_rate_pct"]),
    }
    sample_ok = checks["windows"][2] and checks["days"][2]
    failed = len(ent) >= fail["after_windows"] and (
        net <= D(str(fail["net_after_fees_usd_at_or_below"]))
        or len(matched) < fail["two_sided_completions_below"]
        or (l1s is not None and l1s > fail["relation_l1_distance_above"]))
    verdict = ("FAIL" if failed else "INSUFFICIENT_SAMPLE" if not sample_ok
               else "SUPPORTED" if all(v[2] for v in checks.values()) else "NOT_SUPPORTED")
    return {"verdict": verdict, "spec_sha": spec.get("_sha256_16"),
            "frozen_utc": spec.get("frozen_utc"), "windows": len(ent), "days": days,
            "checks": {k: {"value": v[0], "threshold": v[1], "ok": bool(v[2])} for k, v in checks.items()},
            "action_scoring": sc,
            "labels": "predeclared stochastic OOS arm; THEORETICAL; thresholds only from the spec file"}


# ------------------------- pre-registered transfer gate -------------------------

GATE_PATH = ROOT / "analysis/l5/gemini_transfer_gate.json"


def load_gate(path: Path = GATE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _day(r: Mapping[str, Any]) -> str:
    return datetime.fromtimestamp(float(r["entry_t"]), timezone.utc).strftime("%Y-%m-%d")


def transfer_gate_verdict(rows: list, gate: Optional[Mapping[str, Any]] = None) -> dict:
    """Evaluate ONE arm's strict rows against the frozen gate. Returns
    PASS / FAIL / INSUFFICIENT_SAMPLE / REJECT with every criterion
    named; thresholds come only from the frozen JSON."""
    gate = gate or load_gate()
    req = gate["requirements"]
    rej = gate["reject"]
    ent = [r for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD", "NO_FILL")]
    s = summarize(ent) if ent else None
    days = sorted({_day(r) for r in ent})
    matched_rows = [r for r in ent if D(r["inventory"]["matched"]) > 0]
    matched_days = sorted({_day(r) for r in matched_rows})
    net_by_day = {}
    for r in ent:
        d = _day(r)
        v = D(r.get("pair_pnl_theoretical") or 0) + D(r.get("hold_pnl_theoretical") or 0)
        net_by_day[d] = net_by_day.get(d, D("0")) + v
    net = sum(net_by_day.values(), D("0"))
    checks = {}
    if s is None:
        return {"verdict": "INSUFFICIENT_SAMPLE", "windows": 0, "days": 0,
                "gate_sha": gate.get("_sha256_16"), "checks": {}}
    checks["windows"] = (len(ent), req["min_windows"], len(ent) >= req["min_windows"])
    checks["days"] = (len(days), req["min_days"], len(days) >= req["min_days"])
    sample_ok = checks["windows"][2] and checks["days"][2]
    checks["two_sided_completions"] = (len(matched_rows), req["min_two_sided_completions"],
                                       len(matched_rows) >= req["min_two_sided_completions"])
    checks["completion_days"] = (len(matched_days), req["min_completion_days"],
                                 len(matched_days) >= req["min_completion_days"])
    p = s.get("p_complement_given_first_fill")
    checks["p_complement_given_first_fill_pct"] = (
        p, req["min_p_complement_given_first_fill_pct"],
        p is not None and p >= req["min_p_complement_given_first_fill_pct"])
    le = s.get("matched_pairs_le_099") or 0
    share = round(100.0 * le / len(matched_rows), 1) if matched_rows else None
    checks["matched_le_099_share_pct"] = (share, req["min_matched_le_099_share_pct"],
                                          share is not None and share >= req["min_matched_le_099_share_pct"])
    checks["net_after_fees_usd"] = (str(net), req["min_net_after_known_fees_usd"],
                                    net >= D(str(req["min_net_after_known_fees_usd"])))
    pos_days = sum(1 for v in net_by_day.values() if v >= 0)
    checks["non_negative_days"] = (pos_days, req["min_non_negative_days"],
                                   pos_days >= req["min_non_negative_days"])
    adv = (s.get("adverse_selection_by_horizon") or {}).get("30", {}).get("pct_adverse")
    checks["adverse_30s_pct"] = (adv, req["max_adverse_selection_30s_pct"],
                                 adv is not None and adv <= req["max_adverse_selection_30s_pct"])
    hr = s.get("one_sided_hold_rate")
    checks["one_sided_hold_rate_pct"] = (hr, req["max_one_sided_hold_rate_pct"],
                                         hr is not None and hr <= req["max_one_sided_hold_rate_pct"])
    reject = (len(ent) >= rej["after_windows"] and
              (net <= D(str(rej["net_after_fees_usd_at_or_below"]))
               or len(matched_rows) < rej["two_sided_completions_below"]))
    if reject:
        verdict = "REJECT"
    elif not sample_ok:
        verdict = "INSUFFICIENT_SAMPLE"
    elif all(v[2] for v in checks.values()):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {"verdict": verdict, "windows": len(ent), "days": len(days),
            "gate_sha": gate.get("_sha256_16"), "gate_frozen_utc": gate.get("frozen_utc"),
            "checks": {k: {"value": v[0], "threshold": v[1], "ok": bool(v[2])}
                       for k, v in checks.items()},
            "net_by_day": {k: str(v) for k, v in sorted(net_by_day.items())},
            "labels": "THEORETICAL strict-fill evidence against the frozen gate; "
                      "thresholds are read from the JSON and never tuned here"}


# ------------------------- realtime shadow -------------------------

def book_from_public(sym: str, raw: Mapping[str, Any], now: float) -> dict:
    """Reduce a /v1/book payload to the tape row shape."""
    bids, asks = raw.get("bids") or [], raw.get("asks") or []
    b, a = (bids[0] if bids else {}), (asks[0] if asks else {})
    return {"symbol": sym, "t": now, "bid": b.get("price"),
            "bid_sz": b.get("amount"), "ask": a.get("price"),
            "ask_sz": a.get("amount")}


def live_shadow_step(sym: str, raw_book: Mapping[str, Any], now: float, *,
                     model: Mapping[str, Any], art: Optional[Mapping[str, Any]],
                     inv: Optional[L5Inventory] = None,
                     adapter: Optional[GeminiAdapter] = None) -> dict:
    """One realtime decision (zero orders): what L5 would do now and
    whether it is executable under the shell."""
    adapter = adapter or GeminiAdapter()
    inv = inv or L5Inventory()
    book = adapter.normalize(book_from_public(sym, raw_book, now))
    if book is None:
        return {"venue": adapter.venue, "market_id": sym, "t": now,
                "status": "NO_BOOK", "orders_placed": 0}
    prop = proposal_v2(model, book, inv)
    state = prop["lookup"]["state"]
    shadow = fid.shadow_fields(
        art, slug=sym, decision_idx=len(inv.actions),
        model_sha=str(model.get("_sha256_16") or ""), base_state=state,
        relation=prop["recommended_relation"],
        relation_ranked=prop.get("relation_ranked"), tte_s=book.tte_s)
    relation = "open" if inv.total == 0 else prop["recommended_relation"]
    side_yes = open_side_yes(book) if inv.residual == 0 else inv.residual > 0
    px = book.join_price(side_yes) if side_yes is not None else None
    opp = book.opposite_ask(side_yes) if side_yes is not None else None
    comp, econ = (adapter.complement_price(px, ONE - px if side_yes is None
                                           else (book.no_ask if side_yes
                                                 else book.yes_ask), book.tick)
                  if px is not None else (None, {"regime": "no_price"}))
    executable = {
        "priceable_side": px is not None,
        "in_band": bool(px is not None and adapter.entry_ok(px)),
        "post_only_ok": bool(px is not None and opp is not None and px < opp),
        "tte_ok": tb.MIN_TTE_S <= book.tte_s <= tb.ENTRY_MAX_TTE_S,
        "fee_safe_complement_exists": comp is not None,
        "off_distribution": bool(prop.get("off_distribution")),
    }
    executable["all"] = all(v for k, v in executable.items()
                            if k != "off_distribution") and \
        not executable["off_distribution"]
    rec = decision_record(
        venue=adapter.venue, market_id=sym, t=now, state=state, inv=inv,
        prop=prop, acted=relation if executable["all"] else "hold",
        refusal=None if executable["all"] else "not_executable",
        planned_size={"contracts": str(UNIT), "usd": str(px) if px else None},
        shadow=shadow, book=book, policy="modal")
    rec.update({"would_side": ("YES" if side_yes else "NO") if side_yes is not None else None,
                "would_price": str(px) if px is not None else None,
                "complement_bound": str(comp) if comp is not None else None,
                "pair_economics": econ, "executability": executable,
                "status": "SHADOW_ONLY"})
    return rec


def nearest_windows(now: datetime, count: int = 2) -> list[str]:
    base = now.replace(second=0, microsecond=0)
    base += timedelta(minutes=(5 - base.minute % 5) % 5 or 5)
    return [f"GEMI-BTC05M{base + timedelta(minutes=5 * i):%y%m%d%H%M}-UP"
            for i in range(count)]


def run_live(minutes: float, fetch: Callable[[str], Any],
             out: Optional[Path] = None, poll_s: float = 5.0,
             sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.time) -> dict:
    model, art = pv2.load_v2(), fid.load()
    end = clock() + minutes * 60
    recs: list[dict] = []
    errors = 0
    while clock() < end:
        now = clock()
        for sym in nearest_windows(datetime.fromtimestamp(now, timezone.utc)):
            try:
                raw = fetch(f"https://api.gemini.com/v1/book/{sym}"
                            "?limit_bids=1&limit_asks=1")
                recs.append(live_shadow_step(sym, raw, now, model=model, art=art))
            except Exception as exc:                      # venue/network
                errors += 1
                recs.append({"market_id": sym, "t": now, "status": "FETCH_ERROR",
                             "err": str(exc)[:100], "orders_placed": 0})
        if out:
            out.write_text("\n".join(json.dumps(r, default=str) for r in recs))
        sleep(poll_s)
    ok = [r for r in recs if r.get("status") == "SHADOW_ONLY"]
    return {"labels": LABELS, "steps": len(recs), "fetch_errors": errors,
            "executable_now": sum(1 for r in ok if r["executability"]["all"]),
            "orders_placed": 0, "records": recs[-20:]}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=float, default=0.0, help="minutes")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    a = ap.parse_args(argv)
    if a.live:
        import ssl
        import urllib.request

        def fetch(url: str):
            ctx = ssl.create_default_context(cafile=tb.CA_BUNDLE)
            req = urllib.request.Request(url, headers={"User-Agent": "l5-shadow"})
            with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
                return json.loads(r.read())
        rep = run_live(a.live, fetch, out=a.out.with_suffix(".live.jsonl"))
        print(json.dumps({k: v for k, v in rep.items() if k != "records"}, indent=1))
        return 0
    rep = replay(limit=a.limit)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, indent=1, default=str, sort_keys=True))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "sample_windows"}
                      for k, v in rep["results"].items()}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
