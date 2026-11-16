"""READ-ONLY study of PMUS combos (parlays): is quoting them worth it?

OWNER 2026-11-16: "PMUS calls them combos ... Ya" (build a read-only study
before risking anything). A combo is a user-defined instrument of 2-50 legs
that pays $1 only if every leg goes the way it was taken; it trades through
the normal order book, mostly after an RFQ answered by market makers
(docs.polymarket.us/trader-guide/combos). Combo makers pay
0.03 * C * p * (1 - p) and get no rebate; combo takers pay
C * p * [theta * (1 - p) + 0.06 * (1 - p)^4] (docs.polymarket.us/fees).

What this measures, every POLL_S, with signed GETs only:
  * RFQ flow: GET /v1/rfqs?status=RFQ_STATUS_OPEN -- how many, how big,
    how many legs, same-game or not.
  * Combo prints: GET /v1/rfqs/trades (anonymous) -- price, size, side.
  * Fair value of each printed combo: the product of its legs' current PMUS
    mids (buy leg -> mid, sell leg -> 1 - mid), and the implied maker
    margin = print price - fair, net of the maker fee. Legs are priced at
    READ time, up to POLL_S after the print: an approximation, labelled.
Independence is assumed in the product; same-game legs are flagged because
they are correlated and the naive product misprices them.

It cannot place, quote, accept or cancel anything: it is given two read
functions and nothing else.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

COMBO_MAKER_RATE = 0.03
COMBO_TAKER_EXTRA = 0.06


def combo_maker_fee(p: float, qty: float = 1.0) -> float:
    return COMBO_MAKER_RATE * qty * p * (1 - p)


def combo_taker_fee(p: float, qty: float = 1.0, theta: float = 0.095) -> float:
    return qty * p * (theta * (1 - p) + COMBO_TAKER_EXTRA * (1 - p) ** 4)


def _f(v: Any) -> Optional[float]:
    if isinstance(v, Mapping):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def event_key(symbol: str) -> str:
    """aec-nba-ny-tor-2026-11-15 and asc-nba-ny-tor-2026-11-15-pos-3pt5
    share the game key nba-ny-tor-2026-11-15."""
    parts = str(symbol).split("-")
    for i in range(len(parts) - 2):
        if len(parts[i]) == 4 and parts[i].isdigit():
            return "-".join(parts[1:i + 3])
    return str(symbol)


def fair_value(legs: List[Mapping[str, Any]],
               mids: Mapping[str, Optional[float]]) -> Optional[float]:
    """Independent-legs fair price of the combo, or None if any leg is
    unpriced."""
    fv = 1.0
    for leg in legs:
        m = mids.get(str(leg.get("symbol")))
        if m is None:
            return None
        sell = "SELL" in str(leg.get("side") or "").upper()
        fv *= (1 - m) if sell else m
    return fv


def same_game(legs: List[Mapping[str, Any]]) -> bool:
    keys = [event_key(leg.get("symbol")) for leg in legs]
    return len(set(keys)) < len(keys)


class ComboStudy:
    def __init__(self, signed_get: Callable[[str], Any],
                 pm_get: Callable[[str], Any], log: Callable, *,
                 poll_s: float = 30.0, summary_s: float = 600.0,
                 clock: Callable[[], float] = time.time):
        self.get, self.pm_get, self.log = signed_get, pm_get, log
        self.poll_s, self.summary_s, self.clock = poll_s, summary_s, clock
        self.legs: Dict[str, Optional[List[Mapping[str, Any]]]] = {}
        self.seen_rfq: set = set()
        self.seen_trade: set = set()
        self.since = datetime.now(timezone.utc) - timedelta(hours=6)
        self.stop = False
        self.errors: Dict[str, str] = {}
        self.agg = {"rfqs": 0, "rfq_legs": {}, "rfq_same_game": 0,
                    "trades": 0, "combo_trades": 0, "volume_usd": 0.0,
                    "priced": 0, "margin_sum": 0.0, "margin_net_sum": 0.0,
                    "margin_pos": 0, "same_game_trades": 0}

    # ----------------------------------------------------------- reads
    def combo_legs(self, symbol: str) -> Optional[List[Mapping[str, Any]]]:
        if symbol in self.legs:
            return self.legs[symbol]
        legs = None
        try:
            d = self.get(f"/v1/combos?symbol={symbol}") or {}
            c = (d.get("combos") or [None])[0]
            if isinstance(c, Mapping) and c.get("legs"):
                legs = [dict(x) for x in c["legs"] if isinstance(x, Mapping)]
        except Exception as exc:                          # noqa: BLE001
            self.errors["combos"] = f"{type(exc).__name__}:{exc}"[:160]
        self.legs[symbol] = legs          # None = a plain (non-combo) symbol
        return legs

    def mid(self, symbol: str) -> Optional[float]:
        # LIVE 2026-11-16 11:46Z: one leg's BBO read raised and killed the
        # study thread after its first batch. A leg that cannot be read
        # (closed, settled, unknown) is simply unpriced.
        try:
            md = (self.pm_get(f"/v1/markets/{symbol}/bbo") or {}).get(
                "marketData") or {}
        except Exception as exc:                          # noqa: BLE001
            self.errors["leg_bbo"] = f"{type(exc).__name__}:{exc}"[:160]
            return None
        b, a = _f(md.get("bestBid")), _f(md.get("bestAsk"))
        if b is None or a is None or a <= b:
            return None
        return (a + b) / 2

    # ----------------------------------------------------------- one pass
    def poll_rfqs(self) -> None:
        try:
            d = self.get("/v1/rfqs?status=RFQ_STATUS_OPEN&limit=100") or {}
        except Exception as exc:                          # noqa: BLE001
            self.errors["rfqs"] = f"{type(exc).__name__}:{exc}"[:160]
            return
        for r in d.get("rfqs") or []:
            rid = str(r.get("id") or "")
            if not rid or rid in self.seen_rfq:
                continue
            self.seen_rfq.add(rid)
            legs = r.get("comboLegs") or self.combo_legs(str(r.get("symbol")))
            n = len(legs or [])
            self.agg["rfqs"] += 1
            self.agg["rfq_legs"][str(n)] = self.agg["rfq_legs"].get(str(n),
                                                                   0) + 1
            if legs and same_game(legs):
                self.agg["rfq_same_game"] += 1
            self.log("COMBO_STUDY_RFQ", rfq_id=rid, symbol=r.get("symbol"),
                     legs=n, same_game=bool(legs and same_game(legs)),
                     qty=r.get("qtyDecimal"), cash=r.get("cashOrderQty"),
                     label="READ-ONLY study; nothing quoted")

    def poll_trades(self) -> None:
        start = self.since.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            d = self.get(f"/v1/rfqs/trades?limit=100&startTime={start}") or {}
        except Exception as exc:                          # noqa: BLE001
            self.errors["trades"] = f"{type(exc).__name__}:{exc}"[:160]
            return
        newest = self.since
        for t in d.get("trades") or []:
            tid = str(t.get("tradeId") or "")
            if not tid or tid in self.seen_trade:
                continue
            self.seen_trade.add(tid)
            self.agg["trades"] += 1
            try:
                ts = datetime.fromisoformat(
                    str(t.get("executedTime")).replace("Z", "+00:00"))
                newest = max(newest, ts)
            except ValueError:
                pass
            self.record_trade(t)
        self.since = newest

    def record_trade(self, t: Mapping[str, Any]) -> None:
        sym = str(t.get("symbol") or "")
        legs = self.combo_legs(sym)
        if not legs:
            return                                # a single-market RFQ fill
        px, qty = _f(t.get("price")), _f(t.get("qtyDecimal")) or 0.0
        self.agg["combo_trades"] += 1
        if px is not None:
            self.agg["volume_usd"] += px * qty
        sg = same_game(legs)
        self.agg["same_game_trades"] += int(sg)
        mids = {str(l.get("symbol")): self.mid(str(l.get("symbol")))
                for l in legs}
        fv = fair_value(legs, mids)
        rec = {"symbol": sym, "price": px, "qty": qty, "legs": len(legs),
               "same_game": sg, "aggressor": t.get("aggressorSide"),
               "executed": t.get("executedTime"), "fair_now": fv}
        if fv is not None and px is not None:
            margin = px - fv                      # maker sold at px
            net = margin - combo_maker_fee(px)
            self.agg["priced"] += 1
            self.agg["margin_sum"] += margin
            self.agg["margin_net_sum"] += net
            self.agg["margin_pos"] += int(net > 0)
            rec.update(maker_margin=round(margin, 4),
                       maker_margin_after_fee=round(net, 4),
                       taker_fee_est=round(combo_taker_fee(px), 4))
        self.log("COMBO_STUDY_TRADE", **rec,
                 label=("READ-ONLY; fair = product of leg mids at READ time "
                        "(<= poll interval after the print), independence "
                        "assumed; same_game legs are correlated"))

    def summary(self) -> Dict[str, Any]:
        a = dict(self.agg)
        n = a["priced"]
        a["mean_margin"] = round(a["margin_sum"] / n, 4) if n else None
        a["mean_margin_after_maker_fee"] = (round(a["margin_net_sum"] / n, 4)
                                            if n else None)
        a["errors"] = dict(self.errors)
        a["label"] = "READ-ONLY combo study; nothing quoted or traded"
        return a

    def run(self) -> None:
        last_sum = 0.0
        while not self.stop:
            try:
                self.poll_rfqs()
                self.poll_trades()
                if self.clock() - last_sum > self.summary_s:
                    self.log("COMBO_STUDY_SUMMARY", **self.summary())
                    last_sum = self.clock()
            except Exception as exc:                      # noqa: BLE001
                # never let one bad read end the study
                self.errors["loop"] = f"{type(exc).__name__}:{exc}"[:160]
            time.sleep(self.poll_s)

    def start(self):
        import threading
        th = threading.Thread(target=self.run, daemon=True,
                              name="combo-study")
        th.start()
        return th
