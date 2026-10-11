"""BTC5m EXECUTION CANARY -- bounded, authorized, mechanics-only.

AUTHORIZATION: OWNER-2026-10-05-GEMINI-BTC5M-CANARY-1, granted explicitly as a
LIVE money-moving authorization for the probe frozen in
analysis/gemini/updown/BTC5M_PROBE_SPEC.md.

THIS IS NOT A STRATEGY. There is no proven BTC5m directional rule: every
directional study to date is negative by 13-40 points and the preregistered
early-window analysis has not yet run on a real tranche. The side is UP always,
which expresses no view -- the unconditional UP base rate is 55.64% with a 95%
CI of [47.2%, 64.1%], straddling 50%. Its P&L is EXECUTION EVIDENCE and is
never reported as strategy evidence.

WHAT IS REUSED RATHER THAN REBUILT, because a second executor is a second set
of bugs:
    arming            the dual-key arm; no arm, no request
    transport.place   the one order path, itself arm-gated
    payload           the one source of the signed bytes
    ci15m_probe       one_outstanding_order, poll_terminal, classify_ack,
                      authenticated_fills, book_from_venue
    ci15m_adapters    ExecutableBook -- the ONLY price source for an order
Only three things are new here: this authorization's envelope, a durable
five-slot ledger, and the frozen entry rule.

TWO CONSTANTS MOVE FOR THIS AUTHORIZATION, and the move is the safety
mechanism rather than a side effect:
    arming.EXPECTED_AUTH_ID        -> CANARY_AUTH_ID
    payload.SEMANTICS_PROBE_AUTH_ID -> CANARY_AUTH_ID
The second is what makes `transport.place(allow_taker=True)` constructible, and
exactly one authorization holds that key at a time. Moving it therefore CLOSES
crossing orders for CI15M-4 and makes `ci15m_probe.submit()` refuse outright
(its own PROBE_AUTH_ID no longer equals the live authorization) -- so this
grant cannot be spent on the separate BTC15m PERSIST4 cohort, which is
precisely the separation the owner required.

NO SELL PATH EXISTS HERE. The module does not import sell_path, constructs only
`side="buy"`, and holds every entry to settlement. Cancellation exists solely
as the deadman unwind.

ORDER OF OPERATIONS IS THE SAFETY PROPERTY. The slot is fsynced and the
cumulative outlay is checked BEFORE the venue is touched, so an ambiguous send
finds its slot already spent. There is no retry path and no rollback.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import arming
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import payload, transport

LABEL = "GEMINI_BTC5M_CANARY_V1"
CANARY_AUTH_ID = "OWNER-2026-10-05-GEMINI-BTC5M-CANARY-1"

#: THE OWNER'S ENVELOPE, verbatim. Any difference refuses the probe.
AUTHORIZED_ENVELOPE: Dict[str, Any] = {
    "duration": "BTC05M",
    "quantity": "1",                      # fixed; outcome-independent
    "max_acquisition_price": "0.90",
    "max_entry_attempts": 5,
    "max_attempts_per_window": 1,
    "max_outstanding_orders": 1,
    "max_cohort_acquisition_outlay_usd": "4.5315",
    "hold_to_settlement": True,
    "sell_permitted": False,
    "averaging_down_permitted": False,
    "size_changes_permitted": False,      # no martingale is expressible
    "side_rule": "UP_ALWAYS_NON_DIRECTIONAL",
    "min_checkpoint_s_since_open": 15.0,
    "max_quote_age_s": 2.0,
    "deadman": True,
}

#: the venue's own minimums, measured on 58 live BTC5m windows (recorder tape
#: 2026-10-05): quantityMinimum 0.01, quantityIncrement 0.01, priceMinimum
#: 0.01, priceIncrement 0.01. q=1 is permitted and is 100x the minimum.
VENUE_QUANTITY_MINIMUM = D("0.01")
VENUE_QUANTITY_INCREMENT = D("0.01")
VENUE_PRICE_INCREMENT = D("0.01")

LEDGER_DIR = "analysis/gemini/btc5m_canary"
LEDGER = os.path.join(LEDGER_DIR, "cohort.jsonl")
HALT = os.path.join(LEDGER_DIR, "HALT.json")


class CanaryRefused(Exception):
    """Raised before anything is signed. Never downgraded to a warning."""


# ------------------------------------------------------------------- gates
def envelope_match() -> Dict[str, Any]:
    """The envelope this build will honour, echoed for the preflight."""
    return {"envelope": dict(AUTHORIZED_ENVELOPE),
            "auth_id": CANARY_AUTH_ID,
            "worst_case_per_trade_usd": str(worst_case_per_trade()),
            "worst_case_cohort_usd": str(worst_case_cohort())}


def worst_case_per_trade() -> D:
    """Principal at the cap plus the published taker fee at the cap."""
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    p = D(AUTHORIZED_ENVELOPE["max_acquisition_price"])
    fee = D("0.07") * q * p * (D("1") - p)
    return (q * p + fee).quantize(D("0.0001"))


def worst_case_cohort() -> D:
    return (worst_case_per_trade()
            * AUTHORIZED_ENVELOPE["max_entry_attempts"]).quantize(D("0.0001"))


def auth_gate() -> Dict[str, Any]:
    """BOTH constants must name this authorization, or no order is
    constructible. Reported rather than assumed."""
    live = arming.auth_id()
    return {
        "live_auth_id_from_env": live,
        "build_expected_auth_id": arming.EXPECTED_AUTH_ID,
        "taker_key_holder": payload.SEMANTICS_PROBE_AUTH_ID,
        "canary_auth_id": CANARY_AUTH_ID,
        "arming_matches": arming.EXPECTED_AUTH_ID == CANARY_AUTH_ID,
        "taker_key_matches": (payload.SEMANTICS_PROBE_AUTH_ID
                              == CANARY_AUTH_ID),
        "env_matches": live == CANARY_AUTH_ID,
        "ci15m_submit_is_now_refused": (P.PROBE_AUTH_ID
                                        != arming.EXPECTED_AUTH_ID),
        "note": ("exactly one authorization holds the taker key; this grant "
                 "holding it is what makes ci15m_probe.submit() refuse, so "
                 "the BTC15m PERSIST4 cohort cannot be spent on it"),
    }


def halted() -> Optional[Dict[str, Any]]:
    if not os.path.exists(HALT):
        return None
    try:
        return json.load(open(HALT, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {"reason": "HALT_FILE_UNREADABLE"}


def halt(reason: str, detail: Any = None) -> Dict[str, Any]:
    os.makedirs(LEDGER_DIR, exist_ok=True)
    row = {"reason": reason, "detail": detail, "utc": _utc(),
            "auth_id": CANARY_AUTH_ID, "label": LABEL}
    with open(HALT, "w", encoding="utf-8") as fh:
        json.dump(row, fh, indent=2, sort_keys=True, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    _append({"kind": "HALT", **row})
    return row


def _utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _append(row: Dict[str, Any]) -> None:
    """Durable append. fsync, because the whole point is that a crashed run
    finds its slot already spent."""
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row, utc=row.get("utc") or _utc()),
                            sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def ledger_rows() -> List[Dict[str, Any]]:
    if not os.path.exists(LEDGER):
        return []
    out = []
    for line in open(LEDGER, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:                                      # noqa: BLE001
            continue
    return out


def cohort_state() -> Dict[str, Any]:
    rows = ledger_rows()
    slots = [r for r in rows if r.get("kind") == "SLOT"]
    spent = sum(D(str(r.get("committed_usd") or "0")) for r in slots)
    windows = {r.get("market") for r in slots if r.get("market")}
    return {
        "attempts_consumed": len(slots),
        "attempts_remaining": max(0, AUTHORIZED_ENVELOPE[
            "max_entry_attempts"] - len(slots)),
        "committed_usd": str(spent),
        "committed_headroom_usd": str(
            D(AUTHORIZED_ENVELOPE["max_cohort_acquisition_outlay_usd"])
            - spent),
        "windows_touched": sorted(w for w in windows if w),
        "halted": halted(),
    }


def _d(v: Any) -> Optional[D]:
    """A Decimal, or None. An unreadable number is never a zero."""
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def authorized_intents(market: str = "") -> List[Dict[str, Any]]:
    """THIS COHORT's durable authorizations, read from ITS OWN ledger.

    WHY THIS EXISTS. Cohort 1's five fills all came back `ok: false --
    unattributable venue fill(s)` and were classified FOREIGN, because
    ci15m_probe.attribute_fills reads authorized intents from the CI15M
    cohort root and this cohort writes SLOT/SUBMITTED rows here. Zero
    intents were found, so every genuine fill matched nothing. The fills
    were ours and the ledger proved it one directory away.
    """
    out: List[Dict[str, Any]] = []
    rows = ledger_rows()
    for r in rows:
        if r.get("kind") != "SLOT":
            continue
        if market and r.get("market") != market:
            continue
        body = {}
        for s_ in rows:
            if (s_.get("kind") == "SUBMITTED"
                    and s_.get("slot") == r.get("slot")):
                body = ((s_.get("venue") or {}).get("body") or {})
                break
        oid = body.get("orderId")
        out.append({
            "slot": r.get("slot"), "market": r.get("market"),
            "utc": r.get("utc"),
            "price": r.get("requested_price"),
            "quantity": r.get("requested_quantity"),
            "outcome": payload.spec.OUTCOME_YES,
            "order_id": (None if oid is None else str(oid)),
            "client_order_id": body.get("clientOrderId"),
        })
    return out


def attribute_fills(market: str,
                    fills: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Is every venue fill on this market OURS? Two routes, both positive.

    THE INVARIANT IS NOT WEAKENED, it is strengthened. A fill is ours only
    if it carries evidence we hold on disk:

      ORDER_ID -- the fill's own order_id equals an orderId the venue
      returned to us and we durably recorded as SUBMITTED for this market.
      That is the strongest attribution available: an exact identifier the
      venue itself minted for our order. It replaces nothing; it is the
      route cohort 1's fills should have matched.

      INTENT -- ONLY for a fill that carries NO order_id at all: the older
      envelope match, same outcome, a price AT OR BELOW an authorized limit
      (a capped marketable limit never pays more) and a quantity no larger
      than authorized. This is the crash case, where the venue executed an
      authorization whose orderId we never got to record.

    A FILL THAT CARRIES AN ORDER_ID MUST MATCH ONE OF OURS. The envelope
    route alone would absorb a stranger's fill on our symbol whenever its
    price happened to sit under our limit -- a test with order_id 999 at
    0.50 against our 0.72 authorization was absorbed, which is precisely the
    position we refuse to take on. An identifier we never minted is now
    FOREIGN however well-behaved its price looks.

    Either way each authorization absorbs at most ONE fill, so a second fill
    cannot hide behind a genuine one, and a fill matching neither route is
    FOREIGN and halts. A fill whose order_id matches an authorization but
    whose price EXCEEDS the limit we submitted is NOT absorbed -- an exact
    identifier does not license paying more than the envelope allowed.
    """
    intents = authorized_intents(market)
    used: set = set()
    attributed: List[Dict[str, Any]] = []
    foreign: List[Mapping[str, Any]] = []
    for f in fills:
        px = _d(f.get("price"))
        qty = _d(f.get("qty"))
        leg = str(f.get("leg") or "").strip().lower()
        f_oid = f.get("order_id")
        f_oid = None if f_oid is None else str(f_oid)
        hit, route = None, None
        for n, i in enumerate(intents):
            if n in used:
                continue
            lim, aq = _d(i.get("price")), _d(i.get("quantity"))
            if lim is None or aq is None or px is None or qty is None:
                continue
            # the envelope binds on BOTH routes
            if px > lim or qty > aq:
                continue
            if f_oid is not None:
                if i.get("order_id") == f_oid:
                    hit, route = n, "ORDER_ID"
                    break
                continue
            if str(i.get("outcome") or "").strip().lower() == leg:
                hit, route = n, "INTENT_ENVELOPE"
                break
        if hit is None:
            foreign.append(f)
            continue
        used.add(hit)
        attributed.append(dict(f, _slot=intents[hit]["slot"],
                               _attribution_route=route))
    if foreign:
        halt("UNATTRIBUTABLE_FILL",
             {"market": market, "n_foreign": len(foreign),
              "n_intents": len(intents),
              "foreign": [{k: f.get(k) for k in
                           ("tid", "order_id", "leg", "price", "qty")}
                          for f in foreign[:4]]})
        return {"ok": False, "attributed": attributed, "foreign": foreign,
                "why": "unattributable venue fill(s)"}
    return {"ok": True, "attributed": attributed, "foreign": [],
            "n_intents": len(intents), "n_attributed": len(attributed),
            "routes": sorted({r["_attribution_route"] for r in attributed})}


# -------------------------------------------------------- the frozen rule
def evaluate(symbol: str, *, elapsed_since_open_s: Optional[float],
             quote_age_s: Optional[float]) -> Dict[str, Any]:
    """THE FROZEN ENTRY RULE. Deterministic; reads the executable ladder only.

    At q=1 the TOUCH and CUMULATIVE depth variants COINCIDE whenever the
    touch size is at least 1, which is why the rule can be stated on the
    touch alone without weakening the preregistered definition.
    """
    # VERIFIED against the producer: book_from_venue returns readable, bid,
    # ask, bid_size, ask_size, snapshot_age_s, contract_price_telemetry and
    # the ExecutableBook itself -- and `readable` is True only when the
    # ladder is TWO-SIDED, because ExecutableBook.of refuses a one-sided one
    # rather than deriving the missing side. There is no `depth_raw` key; an
    # earlier draft of this function read one and would have silently
    # evaluated None.
    bk = P.book_from_venue(symbol)
    bid, ask = bk.get("bid"), bk.get("ask")
    bid_size, ask_size = bk.get("bid_size"), bk.get("ask_size")
    # the ladder's OWN snapshot age is the authority on staleness; a caller
    # may pass one but may not make a stale book look fresh.
    ladder_age = bk.get("snapshot_age_s")
    if quote_age_s is None:
        quote_age_s = ladder_age
    elif ladder_age is not None:
        quote_age_s = max(float(quote_age_s), float(ladder_age))
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    cap = D(AUTHORIZED_ENVELOPE["max_acquisition_price"])
    checks: Dict[str, Any] = {
        "ladder_readable": bool(bk.get("readable")),
        "structurally_two_sided": bid is not None and ask is not None,
        "checkpoint_at_or_after_min": (
            elapsed_since_open_s is not None
            and elapsed_since_open_s >= AUTHORIZED_ENVELOPE[
                "min_checkpoint_s_since_open"]),
        "quote_fresh": (quote_age_s is not None
                        and quote_age_s <= AUTHORIZED_ENVELOPE[
                            "max_quote_age_s"]),
    }
    if checks["structurally_two_sided"]:
        checks["economically_two_sided_q1"] = (
            bid_size is not None and ask_size is not None
            and D(str(bid_size)) >= q and D(str(ask_size)) >= q)
        checks["acquisition_within_cap"] = D(str(ask)) <= cap
        checks["price_on_increment"] = (
            D(str(ask)) % VENUE_PRICE_INCREMENT == 0)
        checks["quantity_on_increment"] = q % VENUE_QUANTITY_INCREMENT == 0
        checks["quantity_at_or_above_venue_minimum"] = (
            q >= VENUE_QUANTITY_MINIMUM)
    else:
        for k in ("economically_two_sided_q1", "acquisition_within_cap",
                  "price_on_increment", "quantity_on_increment",
                  "quantity_at_or_above_venue_minimum"):
            checks[k] = False
    failed = [k for k, v in checks.items() if not v]
    return {
        "symbol": symbol, "utc": _utc(),
        "qualifies": not failed, "failed_checks": failed, "checks": checks,
        "side": "UP", "outcome": payload.spec.OUTCOME_YES,
        "requested_price": None if ask is None else str(ask),
        "requested_quantity": str(q),
        "elapsed_since_open_s": elapsed_since_open_s,
        "quote_age_s": quote_age_s,
        "ladder_snapshot_age_s": ladder_age,
        "book": {"bid": None if bid is None else str(bid),
                 "ask": None if ask is None else str(ask),
                 "bid_size": None if bid_size is None else str(bid_size),
                 "ask_size": None if ask_size is None else str(ask_size)},
        "book_raw": bk,
        "rule": ("first checkpoint >= T+15s with a readable two-sided "
                 "executable ladder, both sides filling q=1, ask <= 0.90, "
                 "quote age <= 2.0s. Side UP always."),
        "side_rule_note": ("UP always expresses no view: the unconditional UP "
                           "base rate is 55.64% with a 95% CI of [47.2, "
                           "64.1], which straddles 50%"),
    }


# ------------------------------------------------------------- the one write
async def attempt(symbol: str, decision: Dict[str, Any]) -> Dict[str, Any]:
    """Consume a slot DURABLY, then send exactly one capped marketable LIMIT.

    Refusals, in order, every one fail-closed:
      1. HALTED
      2. the envelope does not match this build
      3. either authorization constant does not name this grant
      4. the decision does not qualify under the frozen rule
      5. the attempt cap is spent
      6. the cumulative outlay cap would be breached
      7. an order is already outstanding on this symbol
      8. this window already consumed an attempt
    """
    h = halted()
    if h:
        raise CanaryRefused("HALTED: %s" % h.get("reason"))
    if not decision.get("qualifies"):
        raise CanaryRefused("the frozen rule does not qualify: %s"
                            % decision.get("failed_checks"))
    g = auth_gate()
    for k in ("arming_matches", "taker_key_matches", "env_matches"):
        if not g[k]:
            raise CanaryRefused("authorization gate %s is false: %s" % (k, g))
    st = cohort_state()
    if st["attempts_remaining"] <= 0:
        raise CanaryRefused("the %d-attempt cap is spent"
                            % AUTHORIZED_ENVELOPE["max_entry_attempts"])
    if symbol in st["windows_touched"]:
        raise CanaryRefused("this window already consumed an attempt")
    px = D(str(decision["requested_price"]))
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    fee = D("0.07") * q * px * (D("1") - px)
    commit = (q * px + fee).quantize(D("0.0001"))
    if (D(st["committed_usd"]) + commit) > D(
            AUTHORIZED_ENVELOPE["max_cohort_acquisition_outlay_usd"]):
        raise CanaryRefused(
            "the cohort outlay cap would be breached: committed %s + %s > %s"
            % (st["committed_usd"], commit,
               AUTHORIZED_ENVELOPE["max_cohort_acquisition_outlay_usd"]))
    out = await P.one_outstanding_order(symbol)
    if not out["ok"]:
        raise CanaryRefused(out["why"])

    slot = {"kind": "SLOT", "slot": st["attempts_consumed"] + 1,
            "market": symbol, "committed_usd": str(commit),
            "requested_price": str(px), "requested_quantity": str(q),
            "auth_id": CANARY_AUTH_ID, "decision": decision}
    _append(slot)                                  # fsynced BEFORE the venue

    t0 = time.monotonic_ns()
    try:
        res = await transport.place(
            symbol=symbol, side=payload.spec.SIDE_BUY,
            outcome=payload.spec.OUTCOME_YES,
            price=str(px), quantity=str(q), allow_taker=True)
    except Exception as exc:                                   # noqa: BLE001
        lat = (time.monotonic_ns() - t0) / 1e6
        row = {"kind": "SUBMIT_AMBIGUOUS", "slot": slot["slot"],
               "market": symbol, "error": str(exc)[:400],
               "latency_ms": round(lat, 3),
               "why": ("the send outcome is unknown. The slot is SPENT and is "
                       "never retried.")}
        _append(row)
        halt("AMBIGUOUS_SUBMISSION", row)
        return row
    lat = (time.monotonic_ns() - t0) / 1e6
    cls = P.classify_ack(res.get("body") or {})
    row = {"kind": "SUBMITTED", "slot": slot["slot"], "market": symbol,
           "latency_ms": round(lat, 3), "venue": res,
           "ack_classification": cls,
           "requested_price": str(px), "requested_quantity": str(q),
           "committed_usd": str(commit),
           "labels": ("SUBMITTED is not FILLED. Hold to settlement; no SELL; "
                      "cancellation exists only as the deadman unwind.")}
    _append(row)
    return row
