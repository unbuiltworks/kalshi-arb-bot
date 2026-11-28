"""COMBO RFQ MAKER for Polymarket US (owner 2026-11-18: "yes build the combo
shadow quoting" / "would rather do live tests than shadow work").

The retail API lets this account answer combo RFQs (POST /v1/rfqs/quotes),
and the requester's acceptance starts a LAST LOOK in which we confirm or
decline (PUT .../confirm or DELETE the quote) before confirmationDeadline.
Our own combo study measured makers taking ~1.5-1.8c over the product of
the leg mids. This module is the maker side, sized for a $300 account.

MODES (PMUS_COMBO_MAKER): "shadow" logs what it WOULD quote and never
writes; "live" quotes, and confirms only in last look; anything else: off.
Live ALSO requires every gate the caller supplies to be clear (the sports
lane passes its submission lock, the shared halt and the account-wide risk
verdict) -- a blocked gate turns live into shadow for that pass.

WHAT IT QUOTES: combo RFQs with 2..MAX_LEGS legs, NO two legs from the same
game (correlation is not modelled), every leg priced by a live two-sided
book no wider than MAX_LEG_SPREAD, fair = product of leg probabilities in
[MIN_FAIR, MAX_FAIR].
  buyPrice  (requester BUYS, we SELL) = fair + edge, rounded UP to the tick
  sellPrice (requester SELLS, we BUY) = fair - edge, rounded DOWN
  edge = max(MIN_EDGE, EDGE_PCT * fair) + the assumed maker fee
A side whose WORST-CASE loss (qty*(1-buy) selling, qty*sell buying) exceeds
MAX_LOSS_PER_QUOTE is marked unavailable ("0"). restRemainder is always
false and postOnly true, so nothing rests on the book after the paired
window.

LAST LOOK: an accepted quote is re-priced from FRESH leg books. Confirm only
if our side still clears CONFIRM_MIN_EDGE against the fresh fair AND the
confirm fits the open-worst-case and per-day caps; otherwise delete it
(decline). Missing the deadline executes nothing -- the safe direction.

Every row is labelled; nothing here is P&L until the venue settles it.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional

ONE, ZERO = D(1), D(0)


def _env_d(name: str, default: str) -> D:
    try:
        return D(str(os.getenv(name, default)).strip())
    except Exception:                                    # noqa: BLE001
        return D(default)


MODE = os.getenv("PMUS_COMBO_MAKER", "0").strip().lower()
MAX_LEGS = int(os.getenv("PMUS_COMBO_MAX_LEGS", "4"))
MAX_LEG_SPREAD = _env_d("PMUS_COMBO_MAX_LEG_SPREAD", "0.06")
MIN_FAIR = _env_d("PMUS_COMBO_MIN_FAIR", "0.05")
MAX_FAIR = _env_d("PMUS_COMBO_MAX_FAIR", "0.60")
MIN_EDGE = _env_d("PMUS_COMBO_MIN_EDGE", "0.02")
EDGE_PCT = _env_d("PMUS_COMBO_EDGE_PCT", "0.12")
MAKER_FEE_RATE = _env_d("PMUS_COMBO_MAKER_FEE_RATE", "0.03")
CONFIRM_MIN_EDGE = _env_d("PMUS_COMBO_CONFIRM_MIN_EDGE", "0.01")
MAX_LOSS_PER_QUOTE = _env_d("PMUS_COMBO_MAX_LOSS_PER_QUOTE", "2")
MAX_OPEN_QUOTES = int(os.getenv("PMUS_COMBO_MAX_OPEN_QUOTES", "3"))
MAX_WORST_TOTAL = _env_d("PMUS_COMBO_MAX_WORST_TOTAL_USD", "10")
MAX_CONFIRMS_PER_DAY = int(os.getenv("PMUS_COMBO_MAX_CONFIRMS_PER_DAY", "10"))
# Quote lifetime. "rfq" (default; owner 2026-11-18 "match the ttl to the
# rfq lifetime"): a quote stays up while its RFQ is still listed OPEN, and
# is withdrawn as soon as it is not. A number keeps the old fixed TTL. Either
# way QUOTE_MAX_TTL_S is a hard ceiling. Staleness is safe: nothing trades
# without our last-look confirm against FRESH leg books.
_TTL_RAW = os.getenv("PMUS_COMBO_QUOTE_TTL_S", "rfq").strip().lower()
try:
    QUOTE_TTL_S: Optional[float] = (None if _TTL_RAW == "rfq"
                                    else float(_TTL_RAW))
except ValueError:
    QUOTE_TTL_S = 60.0                         # garbled -> the old short TTL
QUOTE_MAX_TTL_S = float(os.getenv("PMUS_COMBO_QUOTE_MAX_TTL_S", "1800"))
POLL_S = float(os.getenv("PMUS_COMBO_POLL_S", "3"))
# REQUEST AUDIT 2026-11-28 (analysis/l5/sports/REQUEST_AUDIT_20261128.md).
# Both default 0 = exactly the requests made before.
#
# SHADOW_POLL_S > 0: while the maker is configured "shadow" (it can write
# nothing) one pass is made every this many seconds instead of every POLL_S.
# "live" always keeps POLL_S: its last look has a deadline.
#
# QUOTE_BOOK_TTL_S > 0, mode "shadow" ONLY: a leg book read for pricing a
# NEW RFQ is reused for this many seconds (popular legs repeat across RFQs;
# every read is one public gateway request). A "live" maker reads every leg
# for every quote, as before, and the LAST LOOK never uses the cache in any
# mode: a confirm is always decided on books read at that moment. An
# unreadable leg is not cached.
SHADOW_POLL_S = float(os.getenv("PMUS_COMBO_SHADOW_POLL_S", "0") or 0)
QUOTE_BOOK_TTL_S = float(os.getenv("PMUS_COMBO_QUOTE_BOOK_TTL_S", "0") or 0)
QUOTE_BOOK_CACHE_MAX = 2000


def poll_interval(mode: Optional[str] = None) -> float:
    """Seconds between passes: POLL_S, or SHADOW_POLL_S when it is set and
    the maker is in shadow (never shorter than POLL_S)."""
    mode = MODE if mode is None else mode
    if mode == "shadow" and SHADOW_POLL_S > 0:
        return max(POLL_S, SHADOW_POLL_S)
    return POLL_S
# INDEPENDENT LEG PRICING (28 Nov; default "off" = unchanged venue mids).
#   "prefer"  a leg whose book record carries an independent fair_yes (the
#             engine's sportsbook / international shadow state, never the
#             venue's own price) is priced from it; others from the mid
#   "require" every leg must carry one, else leg_unpriced
# Legs are still multiplied only ACROSS games: same-game combos stay refused
# because no validated joint (correlated) model exists.
INDEPENDENT = os.getenv("PMUS_COMBO_INDEPENDENT", "off").strip().lower()


# ---------------------------------------------------------------- pricing
def event_key(symbol: str) -> str:
    parts = str(symbol).split("-")
    for i in range(len(parts) - 2):
        if len(parts[i]) == 4 and parts[i].isdigit():
            return "-".join(parts[1:i + 3])
    return str(symbol)


def same_game(legs: List[Mapping[str, Any]]) -> bool:
    keys = [event_key(str(l.get("symbol"))) for l in legs]
    return len(set(keys)) < len(keys)


def leg_prob(leg: Mapping[str, Any], book: Optional[Mapping[str, Any]]
             ) -> Optional[D]:
    """Probability the leg comes in as the combo takes it, from a live
    two-sided book (mid), or None if unusable."""
    if not book:
        return None
    sell = "SELL" in str(leg.get("side") or "").upper()
    if INDEPENDENT in ("prefer", "require"):
        fy = book.get("fair_yes") if isinstance(book, Mapping) else None
        if fy is not None:
            try:
                f = D(str(fy))
                if ZERO < f < ONE:
                    return ONE - f if sell else f
            except Exception:                            # noqa: BLE001
                pass
            return None
        if INDEPENDENT == "require":
            return None
    try:
        b, a = D(str(book["bid"])), D(str(book["ask"]))
    except Exception:                                    # noqa: BLE001
        return None
    if not (ZERO < b < a < ONE) or a - b > MAX_LEG_SPREAD:
        return None
    m = (a + b) / 2
    return ONE - m if sell else m


def combo_fair(legs: List[Mapping[str, Any]],
               books: Mapping[str, Optional[Mapping[str, Any]]]
               ) -> Optional[D]:
    p = ONE
    for leg in legs:
        q = leg_prob(leg, books.get(str(leg.get("symbol"))))
        if q is None:
            return None
        p *= q
    return p


def rfq_qty(rfq: Mapping[str, Any], px: D) -> Optional[D]:
    q = rfq.get("qtyDecimal")
    if q not in (None, ""):
        return D(str(q))
    c = rfq.get("cashOrderQty")
    if c not in (None, "") and px > 0:
        return D(str(c)) / px
    return None


def price_quote(rfq: Mapping[str, Any], legs: List[Mapping[str, Any]],
                books: Mapping[str, Optional[Mapping[str, Any]]],
                tick: Any) -> Dict[str, Any]:
    """{"ok", "why", "fair", "buy", "sell", "worst_buy", "worst_sell"}."""
    t = D(str(tick or "0.01"))
    out: Dict[str, Any] = {"ok": False, "why": None}
    if not legs or not (2 <= len(legs) <= MAX_LEGS):
        out["why"] = "legs_out_of_range"
        return out
    if same_game(legs):
        out["why"] = "same_game"
        return out
    fair = combo_fair(legs, books)
    if fair is None:
        out["why"] = "leg_unpriced"
        return out
    out["fair"] = fair
    if not (MIN_FAIR <= fair <= MAX_FAIR):
        out["why"] = "fair_out_of_band"
        return out
    edge = max(MIN_EDGE, EDGE_PCT * fair) + MAKER_FEE_RATE * fair * (ONE - fair)
    buy = ((fair + edge) / t).to_integral_value(rounding=ROUND_CEILING) * t
    sell = ((fair - edge) / t).to_integral_value(rounding=ROUND_FLOOR) * t
    buy = buy if t <= buy <= ONE - t else ZERO
    sell = sell if t <= sell <= ONE - t else ZERO
    qb = rfq_qty(rfq, buy) if buy > 0 else None
    qs = rfq_qty(rfq, sell) if sell > 0 else None
    wb = qb * (ONE - buy) if qb is not None else None
    ws = qs * sell if qs is not None else None
    if wb is None or wb > MAX_LOSS_PER_QUOTE:
        buy = ZERO
    if ws is None or ws > MAX_LOSS_PER_QUOTE:
        sell = ZERO
    if buy == 0 and sell == 0:
        out["why"] = "worst_case_over_cap"
        return out
    out.update(ok=True, buy=buy, sell=sell,
               worst_buy=wb if buy > 0 else None,
               worst_sell=ws if sell > 0 else None, edge=edge)
    return out


def last_look(side: str, buy: D, sell: D, fresh_fair: Optional[D]
              ) -> Dict[str, Any]:
    """CONFIRM only if our accepted side still clears CONFIRM_MIN_EDGE
    against a FRESH fair; an unpriceable fresh fair declines."""
    if fresh_fair is None:
        return {"confirm": False, "why": "fresh_fair_unavailable"}
    if "BUY" in str(side).upper():          # requester bought: we SOLD at buy
        edge = D(str(buy)) - fresh_fair
    else:                                   # requester sold: we BOUGHT at sell
        edge = fresh_fair - D(str(sell))
    return {"confirm": edge >= CONFIRM_MIN_EDGE, "edge": edge,
            "why": None if edge >= CONFIRM_MIN_EDGE else "edge_gone"}


# ------------------------------------------------------------------ maker
class ComboMaker:
    """One pass = read open RFQs, quote eligible new ones, handle last look
    on accepted quotes, expire stale quotes. All I/O is injected."""

    def __init__(self, *, get: Callable[[str], Any],
                 post: Callable[[str, Mapping[str, Any]], Any],
                 put: Callable[[str, Mapping[str, Any]], Any],
                 delete: Callable[[str], Any],
                 books: Callable[[List[str]], Dict[str, Any]],
                 log: Callable, gates: Callable[[], Optional[str]],
                 mode: Optional[str] = None, clock=time.time,
                 state: Optional[Dict[str, Any]] = None):
        self.get, self.post, self.put, self.delete = get, post, put, delete
        self.books, self.log, self.gates = books, log, gates
        self.mode = MODE if mode is None else mode
        self.clock = clock
        self.s = state if state is not None else {}
        self.s.setdefault("seen", {})          # rfqId -> t
        self.s.setdefault("active", {})        # quoteId -> quote record
        self.s.setdefault("confirmed", [])     # confirm records
        self.s.setdefault("day", {"date": None, "confirms": 0})
        self._leg_cache: Dict[str, Any] = {}   # symbol -> (t, book)
        self.leg_reads = {"read": 0, "reused": 0}

    # ------------------------------------------------------- bookkeeping
    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime(
            "%Y-%m-%d")

    def _day(self) -> Dict[str, Any]:
        d = self.s["day"]
        if d.get("date") != self._today():
            d.clear()
            d.update(date=self._today(), confirms=0)
        return d

    def open_worst(self) -> D:
        return sum((D(str(c["worst"])) for c in self.s["confirmed"]
                    if not c.get("settled")), ZERO)

    def live_ok(self) -> Optional[str]:
        """None when a live write is allowed this pass, else why not."""
        if self.mode != "live":
            return "mode_%s" % self.mode
        try:
            g = self.gates()
        except Exception as exc:                         # noqa: BLE001
            return "gate_error:%s" % type(exc).__name__
        return g

    # ------------------------------------------------------------ passes
    def pass_once(self) -> Dict[str, Any]:
        st = {"quoted": 0, "shadow": 0, "skipped": 0, "confirmed": 0,
              "declined": 0, "expired": 0, "blocked": None}
        block = self.live_ok()
        st["blocked"] = block
        if self.mode in ("shadow", "live"):
            self._last_look(st, block)
            rfqs = self._open_rfqs()
            self._expire(st, None if rfqs is None
                         else {str(r.get("id") or "") for r in rfqs})
            self._quote_new(st, block, rfqs or [])
        return st

    def _quote_books(self, syms: List[str]) -> Dict[str, Any]:
        """Leg books for pricing a NEW RFQ. With QUOTE_BOOK_TTL_S > 0 a
        book read less than that long ago is reused and only the others are
        read; otherwise (and always when the maker is "live") every leg is
        read, as before. Never used by the last look."""
        ttl = QUOTE_BOOK_TTL_S
        if ttl <= 0 or self.mode != "shadow":
            self.leg_reads["read"] += len(syms)
            return self.books(syms)
        now = self.clock()
        out: Dict[str, Any] = {}
        need = []
        for sy in syms:
            hit = self._leg_cache.get(sy)
            if hit is not None and 0 <= now - hit[0] < ttl:
                out[sy] = hit[1]
            elif sy not in need:
                need.append(sy)
        self.leg_reads["reused"] += len(out)
        if need:
            self.leg_reads["read"] += len(need)
            got = self.books(need) or {}
            if len(self._leg_cache) > QUOTE_BOOK_CACHE_MAX:
                self._leg_cache = {k: v for k, v in self._leg_cache.items()
                                   if 0 <= now - v[0] < ttl}
            for sy in need:
                out[sy] = got.get(sy)
                if got.get(sy):                # an unreadable leg: re-read
                    self._leg_cache[sy] = (now, got[sy])
        return out

    def _open_rfqs(self) -> Optional[List[Mapping[str, Any]]]:
        """The open RFQ list, or None when it could not be read (then no
        quote is treated as closed; only the hard ceiling applies)."""
        try:
            d = self.get("/v1/rfqs?status=RFQ_STATUS_OPEN&limit=100")
        except Exception as exc:                         # noqa: BLE001
            self.log("COMBO_MAKER_ERROR", op="list_rfqs",
                     err=type(exc).__name__)
            return None
        if not isinstance(d, Mapping) or not isinstance(d.get("rfqs"), list):
            return None
        return d["rfqs"]

    def _quote_new(self, st: Dict[str, Any], block: Optional[str],
                   rfqs: List[Mapping[str, Any]]) -> None:
        for r in rfqs:
            rid = str(r.get("id") or "")
            if not rid or rid in self.s["seen"]:
                continue
            self.s["seen"][rid] = self.clock()
            legs = r.get("comboLegs") or []
            syms = [str(l.get("symbol")) for l in legs]
            # 2026-11-21 rate-limit audit: every leg was priced (~19 req/s)
            # BEFORE checks that need no price rejected ~65% of RFQs. The
            # price-free checks run first; price_quote repeats them.
            if not legs or not (2 <= len(legs) <= MAX_LEGS) or \
                    same_game(legs):
                q = {"ok": False, "why": "legs_out_of_range"
                     if not (2 <= len(legs) <= MAX_LEGS) else "same_game"}
            else:
                books = self._quote_books(syms) if syms else {}
                q = price_quote(r, legs, books, r.get("tickSize"))
            if not q["ok"]:
                st["skipped"] += 1
                self.log("COMBO_MAKER_SKIP", rfq_id=rid, why=q["why"],
                         legs=len(legs), fair=q.get("fair"))
                continue
            live = block is None and len(self.s["active"]) < MAX_OPEN_QUOTES
            rec = {"rfq_id": rid, "symbol": r.get("symbol"), "legs": legs,
                   "fair": str(q["fair"]), "buy": str(q["buy"]),
                   "sell": str(q["sell"]), "worst_buy": q.get("worst_buy"),
                   "worst_sell": q.get("worst_sell"), "t": self.clock()}
            if not live:
                st["shadow"] += 1
                self.log("COMBO_MAKER_QUOTE", mode="SHADOW", **_fmt(rec),
                         blocked=block or "max_open_quotes",
                         label="would-quote only; nothing sent")
                continue
            try:
                resp = self.post("/v1/rfqs/quotes", {
                    "rfqId": rid, "buyPrice": str(q["buy"]),
                    "sellPrice": str(q["sell"]), "restRemainder": False,
                    "postOnly": True}) or {}
            except Exception as exc:                     # noqa: BLE001
                self.log("COMBO_MAKER_ERROR", op="create_quote", rfq_id=rid,
                         err=_err(exc))
                continue
            qid = str(resp.get("quoteId") or "")
            if not qid:
                self.log("COMBO_MAKER_ERROR", op="create_quote", rfq_id=rid,
                         err="no_quote_id")
                continue
            self.s["active"][qid] = rec
            st["quoted"] += 1
            self.log("COMBO_MAKER_QUOTE", mode="LIVE", quote_id=qid,
                     **_fmt(rec), label="quote sent; nothing trades without "
                     "our last-look confirm")

    def _last_look(self, st: Dict[str, Any], block: Optional[str]) -> None:
        if not self.s["active"]:
            return
        d = self.get("/v1/rfqs/quotes?userFilter=USER_FILTER_SELF"
                     "&status=QUOTE_STATUS_ACCEPTED") or {}
        for qt in d.get("quotes") or []:
            qid = str(qt.get("id") or "")
            rec = self.s["active"].get(qid)
            if rec is None:
                continue                      # not ours from this process
            side = str(qt.get("acceptedSide") or "")
            legs = rec["legs"]
            fresh = combo_fair(legs, self.books(
                [str(l.get("symbol")) for l in legs]))
            ll = last_look(side, D(rec["buy"]), D(rec["sell"]), fresh)
            selling = "BUY" in side.upper()
            px = D(rec["buy"] if selling else rec["sell"])
            qty = D(str(qt.get("buyQtyDecimal" if selling
                               else "sellQtyDecimal") or "0"))
            worst = qty * ((ONE - px) if selling else px)
            day = self._day()
            why = None
            if block is not None:
                why = "blocked:%s" % block
            elif not ll["confirm"]:
                why = ll["why"]
            elif worst > MAX_LOSS_PER_QUOTE:
                why = "worst_over_quote_cap"
            elif self.open_worst() + worst > MAX_WORST_TOTAL:
                why = "worst_over_total_cap"
            elif day["confirms"] >= MAX_CONFIRMS_PER_DAY:
                why = "daily_confirm_cap"
            rid = rec["rfq_id"]
            if why is None:
                try:
                    self.put("/v1/rfqs/%s/quotes/%s/confirm" % (rid, qid), {})
                except Exception as exc:                 # noqa: BLE001
                    self.log("COMBO_MAKER_ERROR", op="confirm", quote_id=qid,
                             err=_err(exc))
                    continue
                day["confirms"] += 1
                self.s["confirmed"].append({
                    "quote_id": qid, "rfq_id": rid, "symbol": rec["symbol"],
                    "side": "SELL" if selling else "BUY", "px": str(px),
                    "qty": str(qty), "worst": str(worst),
                    "fair_at_confirm": None if fresh is None else str(fresh),
                    "t": self.clock(), "legs": legs})
                self.s["active"].pop(qid, None)
                st["confirmed"] += 1
                self.log("COMBO_MAKER_CONFIRM", quote_id=qid, rfq_id=rid,
                         we=("SELL" if selling else "BUY"), px=str(px),
                         qty=str(qty), worst_case_usd=str(worst),
                         fresh_fair=None if fresh is None else str(fresh),
                         edge=str(ll.get("edge")),
                         label="confirmed in last look; venue-confirmed "
                               "fills come from the order stream; not P&L "
                               "until settlement")
            else:
                self._delete(rid, qid, why)
                st["declined"] += 1

    def _expire(self, st: Dict[str, Any],
                open_ids: Optional[set] = None) -> None:
        now = self.clock()
        for qid, rec in list(self.s["active"].items()):
            age = now - float(rec.get("t") or 0)
            why = None
            if age > QUOTE_MAX_TTL_S:
                why = "max_ttl"
            elif QUOTE_TTL_S is not None:
                if age > QUOTE_TTL_S:
                    why = "ttl"
            elif open_ids is not None and rec["rfq_id"] not in open_ids:
                why = "rfq_closed"
            if why:
                self._delete(rec["rfq_id"], qid, why, age_s=round(age, 1))
                st["expired"] += 1
        for rid, t in list(self.s["seen"].items()):
            if now - float(t) > 6 * 3600:
                self.s["seen"].pop(rid, None)

    def _delete(self, rid: str, qid: str, why: str, **kw: Any) -> None:
        try:
            self.delete("/v1/rfqs/%s/quotes/%s" % (rid, qid))
        except Exception as exc:                         # noqa: BLE001
            # an RFQ that already closed took our quote with it; the delete
            # failing then is expected, not an error worth alarming on
            if why != "rfq_closed":
                self.log("COMBO_MAKER_ERROR", op="delete", quote_id=qid,
                         err=_err(exc))
        self.s["active"].pop(qid, None)
        self.log("COMBO_MAKER_DECLINE", quote_id=qid, rfq_id=rid, why=why,
                 **kw)

    def status(self) -> Dict[str, Any]:
        return {"mode": self.mode, "active_quotes": len(self.s["active"]),
                "open_worst_usd": str(self.open_worst()),
                "confirms_today": self._day()["confirms"],
                "leg_book_reads": dict(self.leg_reads),
                "caps": {"max_loss_per_quote": str(MAX_LOSS_PER_QUOTE),
                         "max_open_quotes": MAX_OPEN_QUOTES,
                         "max_worst_total": str(MAX_WORST_TOTAL),
                         "max_confirms_per_day": MAX_CONFIRMS_PER_DAY}}


def _fmt(rec: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: (str(v) if isinstance(v, D) else v) for k, v in rec.items()
            if k not in ("legs", "t")} | {"n_legs": len(rec.get("legs") or [])}


def _err(exc: BaseException) -> str:
    """Exception class plus HTTP status and a short body, never headers
    (no credential ever reaches a log line)."""
    out = type(exc).__name__
    code = getattr(exc, "code", None) or getattr(exc, "status", None)
    if code is not None:
        out += ":%s" % code
    body = None
    try:
        rd = getattr(exc, "read", None)
        if callable(rd):
            body = rd()
    except Exception:                                    # noqa: BLE001
        body = None
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if body:
        out += ":" + " ".join(str(body).split())[:200]
    return out


def new_id() -> str:
    return uuid.uuid4().hex
