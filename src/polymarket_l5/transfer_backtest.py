"""Historical backtest of the CURRENT Gemini BTC-5m q=1 L5-transfer mechanics.

Issue #167 comment 5513476177 part A. Read-only analysis: no orders, no
credentials, no supervisor changes. Replays the exact transfer gate that
the live-shadow supervisor runs — frozen v1 policy gate + transfer
initializer (fee-safe 20-80c maker join, deeper-queue tiebreak) + forced
completion against the actual first-fill price + >=1c post-fee completed
pair edge + q=1 + one attempt per event — against historical public data.

Data tiers (never mixed silently):
  tier1  exact quote+print replay: archived BBO snapshots (the read-only
         collector tapes committed on claude/polymarket-l5-historical-
         analysis-2ih9ui, data/policy_d_port/tape_*.jsonl) + complete
         per-window public prints from GET /v1/trades/{symbol} (the tape
         survives settlement; per-window counts sit far below the 500 cap)
  tier2  print-only flow ceiling: windows without archived quotes get
         two-sided-flow statistics ONLY — no bid/ask is ever invented
  tier3  unavailable

Maker-fill assumptions (queue position is unobservable historically):
  optimistic  at-price print-through: an opposite print AT or through our
              level fills us (upper bound; assumes front of queue)
  strict      strict-through: only a print strictly through our level
              fills us (level fully consumed)
  Observed-shadow calibration is applied at the aggregate level against
  the live Railway shadow checkpoint, never per fill.

Vendored VERBATIM from feature/gemini-l5-dynamic-q1-v1policy @ 80f40c86
(so this branch stays self-contained without importing executor code):
maker fee schedule, 20-80c bounds, transfer-initializer side choice,
completion-reserve complement bound, and the frozen v1 policy state key
(gate file config/l5_v1_policy_gate.json, model sha a846e6bc96fc8079).

All P&L in this module is THEORETICAL. Nothing here is realized.
"""
from __future__ import annotations

import argparse
import glob as globmod
import gzip
import json
import math
import os
import ssl
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal as D
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ONE = D("1")
TICK = D("0.01")
MIN_PRICE = D("0.20")            # vendored: pair.MIN_PRICE @ 80f40c86
MAX_PRICE = D("0.80")            # vendored: pair.MAX_PRICE @ 80f40c86
MIN_EDGE = D("0.01")
FIRST_WAIT_S = 180.0             # forced-completion overlay first-leg rest
MIN_TTE_S = 105.0                # supervisor MIN_TTE
ENTRY_MAX_TTE_S = 300.0
STOP_BEFORE_EXPIRY_S = 25.0      # pair.STOP_BEFORE_EXPIRY_SECONDS
OUTCOME_PROXY_UP = D("0.80")     # final mid >= 0.80 -> UP won (proxy)
OUTCOME_PROXY_DOWN = D("0.20")

CA_BUNDLE = "/root/.ccr/ca-bundle.crt"
def _ssl_ctx(cand: str) -> "ssl.SSLContext":
    """An SSL context that works in every container we deploy into."""
    import os as _os
    for p in (_os.environ.get("SSL_CERT_FILE"), cand):
        if p and _os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()
TRADES_URL = "https://api.gemini.com/v1/trades/{symbol}?limit_trades=500"


def _ceil_cent(x: D) -> D:
    return x.quantize(TICK, rounding=ROUND_CEILING)


def maker_fee(price: D, quantity: D = ONE) -> D:
    """Vendored: gemini_predictions_maker_fee @ 80f40c86 (pre-rebate)."""
    if quantity < 0 or price < 0 or price > 1:
        raise ValueError("invalid Gemini fee inputs")
    return _ceil_cent(D("0.0175") * quantity * price * (ONE - price))


def taker_fee(price: D, quantity: D = ONE) -> D:
    """Gemini prediction-market TAKER fee, ceil-cent.

    SOURCE, and it was already in this repository rather than unknown:
    gemini_maker/completion_frontier.py records the official Gemini
    prediction-market schedule, USER-VERIFIED 2026-08-31 --
        maker 0.0175 * C * P * (1-P)   (waived Sep 2 - Dec 31)
        taker 0.07   * C * P * (1-P)
        crypto maker rebate 30% of the TAKER formula, on maker fills priced
        between 20c and 80c, floored per fill to the cent.

    It had simply never been vendored into the VenueAdapter, whose taker_fee
    returned None marked "not vendored: GAP (unverified)". That made every
    conservative Gemini evaluation refuse for want of a number this repo
    already held. Vendored here, next to maker_fee, so the two cannot drift.

    THE WAIVER IS NOT APPLIED. It is time-bounded and promotional, and quietly
    pricing against a promo would overstate Gemini's economics the moment it
    lapses. The rebate is likewise NOT netted here -- it is credited, not
    assumed, exactly as on the other venue."""
    if quantity < 0 or price < 0 or price > 1:
        raise ValueError("invalid Gemini fee inputs")
    return _ceil_cent(D("0.07") * quantity * price * (ONE - price))


def pair_edge(yes: D, no: D) -> D:
    """Vendored: ladder.edge @ 80f40c86 — locked edge after both maker fees."""
    return ONE - yes - no - maker_fee(yes) - maker_fee(no)


def choose_open_side(bid: D, bid_size: D, ask: D, ask_size: D) -> Optional[dict]:
    """Vendored: gemini_l5_v1_campaign.choose_open_side @ 80f40c86."""
    yes_price = D(str(bid))
    no_price = ONE - D(str(ask))
    candidates = []
    if MIN_PRICE <= yes_price <= MAX_PRICE:
        candidates.append({"side": "YES", "price": yes_price,
                           "queue": D(str(bid_size))})
    if MIN_PRICE <= no_price <= MAX_PRICE:
        candidates.append({"side": "NO", "price": no_price,
                           "queue": D(str(ask_size))})
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c["queue"], c["side"]))
    return {"side": candidates[0]["side"], "price": candidates[0]["price"]}


def complement_price(first_side: str, first_exec_price: D,
                     min_edge: D = MIN_EDGE) -> Optional[D]:
    """Vendored: gemini_l5_v1_campaign.complement_price @ 80f40c86."""
    px = (ONE - D(str(first_exec_price)) - D(str(min_edge))).quantize(TICK)
    while px >= MIN_PRICE:
        y, n = ((D(str(first_exec_price)), px) if first_side == "YES"
                else (px, D(str(first_exec_price))))
        if px <= MAX_PRICE and pair_edge(y, n) >= D(str(min_edge)):
            return px
        px -= TICK
    return None


# ---- frozen v1 policy gate (state key vendored verbatim; gate JSON is a
# ---- byte-for-byte copy of config/l5_v1_policy_gate.json @ 80f40c86) ----
PRICE_BANDS = ((0.10, "p<10c"), (0.25, "p10-25c"), (0.45, "p25-45c"),
               (0.55, "p45-55c"), (0.75, "p55-75c"), (0.90, "p75-90c"),
               (10.0, "p>90c"))
TTE_BUCKETS = ((30, "tte<30s"), (120, "tte30-120s"), (300, "tte2-5m"),
               (600, "tte5-10m"), (1e12, "tte>10m"))
IMB_BUCKETS = ((1.0, "flat"), (10.0, "imb1-10"), (50.0, "imb10-50"),
               (1e12, "imb>50"))


def _bucket(value: float, edges) -> str:
    for hi, label in edges:
        if value < hi:
            return label
    return edges[-1][1]


def state_key(last_price: float, tte_s: float, residual_qty: float) -> str:
    return "|".join((_bucket(last_price, PRICE_BANDS),
                     _bucket(tte_s, TTE_BUCKETS),
                     _bucket(abs(residual_qty), IMB_BUCKETS)))


_GATE: Optional[dict] = None


def load_gate(path: str = "config/l5_v1_policy_gate.json") -> dict:
    global _GATE
    if _GATE is None:
        _GATE = json.loads(Path(path).read_text())
    return _GATE


def v1_allows_entry(mid: float, tte_s: float) -> bool:
    g = load_gate()
    st = g["states"].get(state_key(mid, tte_s, 0.0))
    if st is None:
        return False
    return st["modal_relation"] != "sell"


# ------------------------- data plumbing -------------------------

def ticker_expiry_epoch(event_ticker: str) -> Optional[float]:
    """BTC05M2609021450 -> expiry epoch (UTC). Ticker stamps the expiry."""
    try:
        body = event_ticker.split("M", 1)[1]
        yy, mo, dd, hh, mi = (int(body[0:2]), int(body[2:4]), int(body[4:6]),
                              int(body[6:8]), int(body[8:10]))
        return datetime(2000 + yy, mo, dd, hh, mi,
                        tzinfo=timezone.utc).timestamp()
    except (IndexError, ValueError):
        return None


def event_of_symbol(symbol: str) -> str:
    return symbol.replace("GEMI-", "").replace("-UP", "")


def extract_bbo(tape_glob: str, out_path: Path, prefix: str = "GEMI-BTC05M") -> int:
    """Pull Gemini BBO rows for matching symbols out of collector tapes."""
    n = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as sink:
        for fp in sorted(globmod.glob(tape_glob)):
            for line in open(fp, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("venue") == "gemini" and r.get("kind") == "bbo"
                        and str(r.get("market", "")).startswith(prefix)
                        and r.get("bids") and r.get("asks")):
                    sink.write(json.dumps({
                        "symbol": r["market"], "t": r["t"],
                        "bid": r["bids"][0][0], "bid_sz": r["bids"][0][1],
                        "ask": r["asks"][0][0], "ask_sz": r["asks"][0][1],
                    }, separators=(",", ":")) + "\n")
                    n += 1
    return n


def fetch_prints(symbols: Iterable[str], out_path: Path,
                 sleep_s: float = 0.12) -> Mapping[str, int]:
    """Fetch complete per-window public prints; append-cache keyed by symbol."""
    done = set()
    if out_path.exists():
        for line in gzip.open(out_path, "rt", encoding="utf-8"):
            try:
                done.add(json.loads(line)["symbol"])
            except (json.JSONDecodeError, KeyError):
                continue
    # Prefer a bundle that EXISTS. Pinning this session's agent-proxy
    # bundle broke the deployed image silently: the context raised, a
    # bare except swallowed it, and every chain read returned None.
    ctx = _ssl_ctx(CA_BUNDLE)
    stats = {"fetched": 0, "cached": 0, "errors": 0}
    with gzip.open(out_path, "at", encoding="utf-8") as sink:
        for sym in symbols:
            if sym in done:
                stats["cached"] += 1
                continue
            try:
                req = urllib.request.Request(TRADES_URL.format(symbol=sym),
                                             headers={"User-Agent": "research"})
                with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
                    rows = json.loads(r.read())
                sink.write(json.dumps({"symbol": sym, "rows": rows},
                                      separators=(",", ":")) + "\n")
                stats["fetched"] += 1
                time.sleep(sleep_s)
            except Exception:
                stats["errors"] += 1
    return stats


def load_prints(path: Path) -> Mapping[str, list]:
    out: dict[str, list] = {}
    for line in gzip.open(path, "rt", encoding="utf-8"):
        r = json.loads(line)
        out[r["symbol"]] = sorted(r["rows"], key=lambda x: x["timestampms"])
    return out


def load_bbo(path: Path) -> Mapping[str, list]:
    out: dict[str, list] = defaultdict(list)
    for line in gzip.open(path, "rt", encoding="utf-8"):
        r = json.loads(line)
        out[r["symbol"]].append(r)
    for sym in out:
        out[sym].sort(key=lambda x: x["t"])
    return dict(out)


# ------------------------- replay engine -------------------------

def first_fill(prints: list, side: str, join_price: D, ask_join: D,
               t_from: float, t_until: float, mode: str) -> Optional[dict]:
    """First print that fills our resting maker order.

    YES join at `join_price` (best bid): filled by taker SELL at <= price
    (optimistic) or < price (strict). NO join rests as YES ask at
    `ask_join`: filled by taker BUY at >= ask (optimistic) / > (strict).
    """
    for p in prints:
        ts = p["timestampms"] / 1000.0
        if ts <= t_from or ts > t_until:
            continue
        px = D(str(p["price"]))
        if side == "YES" and p["type"] == "sell":
            if (px <= join_price) if mode == "optimistic" else (px < join_price):
                return {"t": ts, "print_price": str(px)}
        if side == "NO" and p["type"] == "buy":
            if (px >= ask_join) if mode == "optimistic" else (px > ask_join):
                return {"t": ts, "print_price": str(px)}
    return None


def complement_fill(prints: list, first_side: str, comp_px: D,
                    t_from: float, t_until: float, mode: str) -> Optional[dict]:
    """Complement is the opposite maker: after a YES first leg we rest NO
    (shows as YES ask at 1-comp_px, filled by taker BUY); after a NO first
    leg we rest YES at comp_px (filled by taker SELL)."""
    if first_side == "YES":
        need, level = "buy", ONE - comp_px
        for p in prints:
            ts = p["timestampms"] / 1000.0
            if ts <= t_from or ts > t_until or p["type"] != need:
                continue
            px = D(str(p["price"]))
            if (px >= level) if mode == "optimistic" else (px > level):
                return {"t": ts, "print_price": str(px)}
    else:
        need, level = "sell", comp_px
        for p in prints:
            ts = p["timestampms"] / 1000.0
            if ts <= t_from or ts > t_until or p["type"] != need:
                continue
            px = D(str(p["price"]))
            if (px <= level) if mode == "optimistic" else (px < level):
                return {"t": ts, "print_price": str(px)}
    return None


def outcome_proxy(bbo_rows: list, expiry: float) -> str:
    """UP/DOWN/AMBIGUOUS from the last quote before expiry (proxy only)."""
    last = None
    for r in bbo_rows:
        if r["t"] < expiry:
            last = r
    if last is None:
        return "AMBIGUOUS"
    mid = (D(last["bid"]) + D(last["ask"])) / 2
    if mid >= OUTCOME_PROXY_UP:
        return "UP"
    if mid <= OUTCOME_PROXY_DOWN:
        return "DOWN"
    return "AMBIGUOUS"


def trailing_flow_two_sided(all_prints_by_symbol: Mapping[str, list],
                            entry_t: float, trail_s: float) -> bool:
    """Venue-level (all BTC-5m symbols) two-sided taker flow in the
    trailing window BEFORE entry. No hindsight: prints at >= entry_t are
    never consulted."""
    saw = set()
    for rows in all_prints_by_symbol.values():
        for p in rows:
            ts = p["timestampms"] / 1000.0
            if entry_t - trail_s <= ts < entry_t:
                saw.add(p["type"])
            if len(saw) == 2:
                return True
    return False


def replay_window(symbol: str, bbo_rows: list, prints: list,
                  fill_mode: str) -> dict:
    """Tier-1 replay of one window under the current transfer mechanics."""
    event = event_of_symbol(symbol)
    expiry = ticker_expiry_epoch(event)
    if expiry is None:
        return {"event": event, "status": "BAD_TICKER"}
    stop_at = expiry - STOP_BEFORE_EXPIRY_S
    entry = None
    for r in bbo_rows:
        tte = expiry - r["t"]
        if MIN_TTE_S <= tte <= ENTRY_MAX_TTE_S:
            entry = r
            break
    if entry is None:
        return {"event": event, "status": "NO_QUOTE_COVERAGE", "tier": 2}
    entry_t = entry["t"]
    bid, ask = D(entry["bid"]), D(entry["ask"])
    mid = float((bid + ask) / 2)
    tte0 = expiry - entry_t
    base = {"event": event, "tier": 1, "entry_t": entry_t,
            "entry_tte_s": round(tte0, 1), "bid": str(bid), "ask": str(ask),
            "fill_mode": fill_mode}
    if not v1_allows_entry(mid, tte0):
        return {**base, "status": "V1_ABSTAIN_FLAT"}
    side = choose_open_side(bid, D(entry["bid_sz"]), ask, D(entry["ask_sz"]))
    if side is None:
        return {**base, "status": "NO_FEE_SAFE_MAKER_PRICE"}
    join = D(str(side["price"]))
    ask_join = ask                       # NO join rests at the current ask
    wait_until = min(entry_t + FIRST_WAIT_S, stop_at)
    f1 = first_fill(prints, side["side"], join, ask_join,
                    entry_t, wait_until, fill_mode)
    if f1 is None:
        return {**base, "status": "NO_FILL_TIMEOUT", "side": side["side"],
                "join_price": str(join)}
    first_cost = join if side["side"] == "YES" else (ONE - ask_join)
    comp = complement_price(side["side"], first_cost, MIN_EDGE)
    if comp is None:
        return {**base, "status": "ONE_SIDED_HOLD", "side": side["side"],
                "join_price": str(join), "first_cost": str(first_cost),
                "fill_t": f1["t"], "hold_reason": "no_fee_safe_complement"}
    f2 = complement_fill(prints, side["side"], comp, f1["t"], stop_at,
                         fill_mode)
    if f2 is None:
        return {**base, "status": "ONE_SIDED_HOLD", "side": side["side"],
                "join_price": str(join), "first_cost": str(first_cost),
                "fill_t": f1["t"], "comp_price": str(comp),
                "residual_s": round(expiry - f1["t"], 1)}
    y, n = ((first_cost, comp) if side["side"] == "YES"
            else (comp, first_cost))
    return {**base, "status": "MATCHED", "side": side["side"],
            "join_price": str(join), "first_cost": str(first_cost),
            "fill_t": f1["t"], "comp_price": str(comp), "comp_t": f2["t"],
            "pair_cost_after_fees": str(y + n + maker_fee(y) + maker_fee(n)),
            "pair_pnl_theoretical": str(pair_edge(y, n)),
            "time_to_complement_s": round(f2["t"] - f1["t"], 1)}


def settle_hold(row: dict, bbo_rows: list) -> dict:
    """Attach outcome-proxy settlement to a one-sided hold (THEORETICAL)."""
    expiry = ticker_expiry_epoch(row["event"])
    oc = outcome_proxy(bbo_rows, expiry)
    row = dict(row)
    row["outcome_proxy"] = oc
    if oc == "AMBIGUOUS":
        row["hold_pnl_theoretical"] = None
        return row
    cost = D(row["first_cost"])
    won = (row["side"] == "YES") == (oc == "UP")
    pnl = (ONE - cost - maker_fee(cost)) if won else (-cost - maker_fee(cost))
    row["hold_pnl_theoretical"] = str(pnl)
    row["residual_won"] = won
    return row


# ------------------------- tier-2 flow ceiling -------------------------

def window_flow(prints: list, expiry: float) -> dict:
    span = [p for p in prints
            if expiry - ENTRY_MAX_TTE_S <= p["timestampms"] / 1000.0
            <= expiry - STOP_BEFORE_EXPIRY_S]
    buys = sum(1 for p in span if p["type"] == "buy")
    sells = sum(1 for p in span if p["type"] == "sell")
    return {"span_prints": len(span), "buys": buys, "sells": sells,
            "two_sided": bool(buys and sells)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["extract-bbo", "fetch-prints", "run"])
    ap.add_argument("--tapes", default="data/policy_d_port/tape_*.jsonl")
    ap.add_argument("--bbo", default="data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz")
    ap.add_argument("--prints", default="data/transfer_backtest/gemini_prints_btc05m.jsonl.gz")
    ap.add_argument("--symbols-file", default="")
    ap.add_argument("--out", default="analysis/l5/transfer_backtest_gemini.json")
    args = ap.parse_args()
    if args.cmd == "extract-bbo":
        Path(args.bbo).parent.mkdir(parents=True, exist_ok=True)
        n = extract_bbo(args.tapes, Path(args.bbo))
        print(json.dumps({"bbo_rows": n}))
    elif args.cmd == "fetch-prints":
        syms = [s.strip() for s in open(args.symbols_file)] if args.symbols_file \
            else sorted(load_bbo(Path(args.bbo)))
        Path(args.prints).parent.mkdir(parents=True, exist_ok=True)
        print(json.dumps(fetch_prints(syms, Path(args.prints))))
    else:
        print("use the analysis driver script; see scripts/transfer_backtest_run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
