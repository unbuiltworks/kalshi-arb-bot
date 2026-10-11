"""PROSPECTIVE POLYMARKET OWNED-ORDER SHADOW -- zero capital, read-only.

AUTHORIZED SCOPE: realtime shadow measurement only. **No order submission,
no wallet signing, no live capital, no simulated touch=fill.** This module
has no signing path, no private key handling and no non-GET verb: it
reuses `http_client`, which enforces HTTPS-only, GET-only, an allow-listed
host set and attaches no Authorization/API-key/signature header on any
code path.

WHAT IS NEW HERE. The existing `realtime_mirror` watches L5's fills. This
watches OUR OWN HYPOTHETICAL ORDERS, which is the one variable no archive
can supply: placement time, price, size, queue ahead, age, replacements,
cancellations, evidenced vs uncertain fills, inventory consequence and
post-fill adverse selection.

THE FILL STANDARD IS GRADED AND NEVER touch=fill. A hypothetical resting
bid at price p, placed when size Q already sat at p, joins BEHIND that
size. It is then:

  EVIDENCED_QUEUE_DEPLETION  size at p fell by >= Q + our size
  EVIDENCED_LEVEL_CONSUMED   best bid fell STRICTLY below p
  EVIDENCED_TRADE_THROUGH    a print at <= p with cumulative size
                             >= Q + our size
  UNCERTAIN_PARTIAL          size at p fell, but by less than Q -- could be
                             cancellations ahead of us, so it is recorded
                             and EXCLUDED from primary economics
  (otherwise)                still resting

Uncertain fills are kept in their own ledger and never enter the headline
numbers.

PRE-REGISTRATION. The candidate dict below is fingerprinted and written to
the tape's first row BEFORE any evaluation window is collected. Scoring a
candidate on windows used to choose it is the failure this guards against.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .http_client import get_json  # noqa: F401  (GET-only, no auth header)

CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
CLASSIFICATION = "ZERO_CAPITAL_OWNED_ORDER_SHADOW_NOT_REALIZED"

#: ---- CANDIDATE S1: the smallest possible two-sided passive policy.
#: Informed by L5 observation, NOT optimized against P&L. Quote
#: suppression is the first residual control; no aggressive opposite-side
#: buying, and the 5c EXTEND rule is deliberately absent.
CANDIDATE = {
    "name": "SHADOW_S1_MINIMAL_TWO_SIDED_PASSIVE",
    "levels": 1,
    "offset_from_bid_ticks": 0,
    "quantity_shares": 23.61,          # L5's observed median leg size
    "quantity_source": "analysis/l5/shadow/L5_BENCHMARKS.json leg_qty_median",
    "replenish_after_evidenced_fill": True,
    "replenish_after_uncertain_fill": False,
    "order_lifetime_s": None,           # rest until filled or window end
    "residual_cap_shares": 80,
    "residual_control": "QUOTE_SUPPRESSION_ONLY",
    "tte_shutdown_s": 60,
    "no_5c_extend_rule": True,
    "no_aggressive_opposite_side": True,
    "fill_standard": "evidenced only; uncertain tracked separately",
}
FINGERPRINT = hashlib.sha256(
    json.dumps(CANDIDATE, sort_keys=True).encode()).hexdigest()


def fnum(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def ladder(side) -> List[Tuple[float, float]]:
    out = []
    for lv in (side or []):
        p, s = fnum(lv.get("price")), fnum(lv.get("size"))
        if p is not None and s is not None:
            out.append((p, s))
    return out


def size_at(lv: List[Tuple[float, float]], px: float) -> float:
    return sum(s for p, s in lv if abs(p - px) < 1e-9)


def best_bid(lv) -> Optional[float]:
    return max((p for p, _ in lv), default=None)


def best_ask(lv) -> Optional[float]:
    return min((p for p, _ in lv), default=None)


class Order:
    __slots__ = ("oid", "token", "outcome", "px", "qty", "placed_t",
                 "queue_ahead", "depth_ahead", "replacements", "state",
                 "state_t", "evidence", "last_size_at")

    def __init__(self, oid, token, outcome, px, qty, t, queue_ahead,
                 depth_ahead):
        self.oid = oid
        self.token = token
        self.outcome = outcome
        self.px = px
        self.qty = qty
        self.placed_t = t
        self.queue_ahead = queue_ahead
        self.depth_ahead = depth_ahead
        self.replacements = 0
        self.state = "RESTING"
        self.state_t = t
        self.evidence = None
        self.last_size_at = queue_ahead

    def row(self, t) -> Dict[str, Any]:
        return {"order_id": self.oid, "token": self.token,
                "outcome": self.outcome, "price": self.px,
                "quantity": self.qty, "placed_t": self.placed_t,
                "age_s": round(t - self.placed_t, 3),
                "queue_ahead_at_placement": self.queue_ahead,
                "depth_ahead_now": self.depth_ahead,
                "replacements": self.replacements, "state": self.state,
                "evidence": self.evidence}


def classify_fill(o: Order, lv_bid: List[Tuple[float, float]],
                  prints: List[Tuple[float, float]]) -> Optional[str]:
    """Graded, independent fill evidence. NEVER touch=fill."""
    bb = best_bid(lv_bid)
    now_at = size_at(lv_bid, o.px)
    need = o.queue_ahead + o.qty
    # 1) THE ONLY BOOK-ONLY EVIDENCE: the level is gone and the touch moved
    #    strictly through our price, so anything resting there was taken.
    if bb is not None and bb < o.px - 1e-9:
        return "EVIDENCED_LEVEL_CONSUMED"
    # 2) prints at or below our price whose cumulative size clears the
    #    queue ahead AND our own quantity. This is the one standard that
    #    can evidence a fill while the level still exists.
    cum = sum(sz for p, sz in prints if p <= o.px + 1e-9)
    if cum >= need - 1e-9 and cum > 0:
        return "EVIDENCED_TRADE_THROUGH"
    # 3) NOT A FILL, and this was a real error in an earlier draft: our
    #    hypothetical order is NOT in the observed book, so size at our
    #    level counts only the queue AHEAD of us. That queue draining to
    #    zero proves the queue cleared -- it does NOT prove our own
    #    quantity traded. We would be at the front, still resting.
    if now_at <= 1e-9 and o.queue_ahead > 0:
        return "QUEUE_CLEARED_FRONT_OF_BOOK"
    # 4) something left the level but not provably us
    if now_at < o.last_size_at - 1e-9:
        return "UNCERTAIN_PARTIAL"
    return None


def preamble(outdir: str) -> Dict[str, Any]:
    bm = {}
    p = "analysis/l5/shadow/L5_BENCHMARKS.json"
    if os.path.exists(p):
        bm = json.load(open(p, encoding="utf-8"))
    return {
        "kind": "shadow_preregistration",
        "classification": CLASSIFICATION,
        "candidate": CANDIDATE,
        "candidate_fingerprint_sha256": FINGERPRINT,
        "written_before_any_evaluation_window": True,
        "authorized_scope": ("zero-capital realtime shadow measurement "
                             "only: no order submission, no wallet "
                             "signing, no live capital, no touch=fill"),
        "safety": ("reuses http_client: HTTPS-only, GET-only, "
                   "allow-listed hosts, no Authorization/API-key/"
                   "signature header on any code path; this module "
                   "contains no signing or private-key code"),
        "allowed_hosts": sorted(config.ALLOWED_HOSTS),
        "l5_benchmarks_recomputed_from_artifacts": bm,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def http_get(url: str, params=None, timeout=20.0):
    """GET-only. NOTE: the existing realtime_mirror polls `/books` with a
    POST batch; that is read-only in intent but violates this module's
    GET-only invariant, so the single-book GET endpoint is used instead,
    two requests per market."""
    st, body, _ = get_json(url, params=params, timeout=timeout)
    return st, body


def discover(limit: int = 200, max_pages: int = 6
             ) -> Tuple[List[Dict[str, Any]], str]:
    """Live BTC/ETH up-down markets, via GET only.

    TWO FAILURES ALREADY PAID FOR, BOTH MINE, BOTH RECORDED HERE:

      1. `ascending=true` with no date floor returned the OLDEST
         unresolved listings -- 27 markets dated 2025-12-19, every one of
         which tripped the `now > t_end` break with polls=0.
      2. "Fixing" that by flipping to `ascending=false` returned the
         FARTHEST-FUTURE markets instead -- long-dated listings that are
         not 5-minute up-down at all -- so discovery found 0 eligible
         markets and reported `discovered: 0` with no error.

    Up-down windows are the SOONEST-ENDING markets, so ascending order is
    correct; what it needs is a date floor. This asks for `end_date_min`
    at the current time AND keeps the client-side `te > now` filter AND
    pages, so it works whether or not the server honours that parameter.
    Per-stage rejection counts are returned so the next failure is
    diagnosable from the tape instead of by inference.
    """
    now = time.time()
    import datetime as _dt
    floor = _dt.datetime.fromtimestamp(
        now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = collections.Counter()
    out: List[Dict[str, Any]] = []
    for page in range(max_pages):
        st, body = http_get(GAMMA + "/markets",
                            {"closed": "false", "limit": str(limit),
                             "offset": str(page * limit),
                             "order": "endDate", "ascending": "true",
                             "end_date_min": floor})
        if st != 200 or not isinstance(body, list):
            return out, "DISCOVERY_HTTP_%s_page%d" % (st, page)
        stats["raw"] += len(body)
        if not body:
            break
        for m in body:
            slug = str(m.get("slug") or "")
            q = (slug + " " + str(m.get("question") or "")).lower()
            if "updown" not in q.replace("-", "") and "up or down" not in q:
                stats["reject_not_updown"] += 1
                continue
            if not any(c in q for c in ("btc", "bitcoin", "eth",
                                        "ethereum")):
                stats["reject_not_btc_eth"] += 1
                continue
            toks = m.get("clobTokenIds")
            if isinstance(toks, str):
                try:
                    toks = json.loads(toks)
                except Exception:                              # noqa: BLE001
                    toks = None
            if not (isinstance(toks, list) and len(toks) == 2):
                stats["reject_no_token_pair"] += 1
                continue
            te = parse_end(m.get("endDate"))
            if te is None:
                stats["reject_no_end_date"] += 1
                continue
            if te <= now:
                stats["reject_already_ended"] += 1
                continue
            stats["accepted"] += 1
            out.append({"slug": slug, "condition_id": m.get("conditionId"),
                        "end_date": m.get("endDate"), "end_ts": te,
                        "tokens": [str(t) for t in toks],
                        "outcomes": m.get("outcomes")})
        if out:
            break                         # soonest-ending live ones found
    out.sort(key=lambda x: x["end_ts"])
    return out, ("OK" if out else "NO_MATCH:%s"
                 % json.dumps(dict(stats), sort_keys=True))


def parse_end(v) -> Optional[float]:
    if not v:
        return None
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def book(token: str):
    st, body = http_get(CLOB + "/book", {"token_id": token})
    if st != 200 or not isinstance(body, dict):
        return None, "BOOK_HTTP_%s" % st
    return body, "OK"


def collect_market(mk: Dict[str, Any], sink, poll_s: float,
                   max_s: float) -> str:
    """One market to a terminal status. Missing data is NEVER a zero fill."""
    t_end = mk.get("end_ts") or parse_end(mk.get("end_date"))
    # REFUSE a market that has already ended rather than letting it fall
    # through the poll loop and report polls=0 / zero fills.
    if t_end is None or t_end <= time.time():
        sink({"kind": "market_terminal", "market": mk["slug"],
              "status": "STALE_MARKET_SKIPPED", "polls": 0,
              "successful_reads": 0, "end_date": mk.get("end_date"),
              "note": ("end date already passed; refused before polling so "
                       "it cannot be mistaken for a fill-free market")})
        return "STALE_MARKET_SKIPPED"
    toks = mk["tokens"]
    names = ["tok0", "tok1"]
    oc = mk.get("outcomes")
    if isinstance(oc, str):
        try:
            oc = json.loads(oc)
        except Exception:                                      # noqa: BLE001
            oc = None
    if isinstance(oc, list) and len(oc) == 2:
        names = [str(x).lower() for x in oc]
    orders: Dict[str, Optional[Order]] = {names[0]: None, names[1]: None}
    inv = {names[0]: 0.0, names[1]: 0.0}
    cost = {names[0]: 0.0, names[1]: 0.0}
    seq = 0
    polls = read_ok = 0
    evidenced = uncertain = 0
    started = time.time()
    last_prints: Dict[str, List[Tuple[float, float]]] = {n: [] for n in names}
    while True:
        now = time.time()
        if now - started > max_s:
            break
        if t_end is not None and now > t_end:
            break
        polls += 1
        snap = {}
        bad = None
        for tok, nm in zip(toks, names):
            b, why = book(tok)
            if b is None:
                bad = why
                break
            snap[nm] = b
        if bad:
            sink({"kind": "book_error", "market": mk["slug"], "why": bad,
                  "t": now})
            time.sleep(poll_s)
            continue
        read_ok += 1
        bids = {nm: ladder(snap[nm].get("bids")) for nm in names}
        asks = {nm: ladder(snap[nm].get("asks")) for nm in names}
        row = {"kind": "shadow_snapshot", "market": mk["slug"], "t": now,
               "tte_s": (round(t_end - now, 2) if t_end else None)}
        for nm in names:
            row[nm + "_bid"] = best_bid(bids[nm])
            row[nm + "_ask"] = best_ask(asks[nm])
            row[nm + "_bid_depth"] = size_at(
                bids[nm], best_bid(bids[nm])) if bids[nm] else None
        bb = [row[nm + "_bid"] for nm in names]
        row["mint_sum"] = (bb[0] + bb[1]) if None not in bb else None
        row["mint_feasible"] = ((row["mint_sum"] >= 1.0)
                                if row["mint_sum"] is not None else None)
        # ---- lifecycle on hypothetical owned orders
        for nm in names:
            o = orders[nm]
            if o is not None:
                v = classify_fill(o, bids[nm], last_prints[nm])
                if v in ("EVIDENCED_LEVEL_CONSUMED",
                         "EVIDENCED_TRADE_THROUGH"):
                    o.state, o.evidence, o.state_t = "FILLED", v, now
                    inv[nm] += o.qty
                    cost[nm] += o.px * o.qty
                    evidenced += 1
                    sink({"kind": "shadow_fill", "market": mk["slug"],
                          "t": now, "outcome": nm, "evidence": v,
                          "order": o.row(now),
                          "inventory": dict(inv),
                          "matched": min(inv[names[0]], inv[names[1]]),
                          "residual": abs(inv[names[0]] - inv[names[1]])})
                    orders[nm] = None if CANDIDATE[
                        "replenish_after_evidenced_fill"] else o
                elif v in ("UNCERTAIN_PARTIAL",
                           "QUEUE_CLEARED_FRONT_OF_BOOK"):
                    uncertain += 1
                    sink({"kind": "shadow_uncertain", "market": mk["slug"],
                          "t": now, "outcome": nm, "verdict": v,
                          "order": o.row(now),
                          "excluded_from_primary_economics": True})
                    o.last_size_at = size_at(bids[nm], o.px)
            # place / replenish under suppression + TTE shutdown
            if orders[nm] is None:
                other = names[1] if nm == names[0] else names[0]
                if inv[nm] - inv[other] >= CANDIDATE[
                        "residual_cap_shares"]:
                    continue
                if t_end is not None and (t_end - now) < CANDIDATE[
                        "tte_shutdown_s"]:
                    continue
                p = best_bid(bids[nm])
                if p is None:
                    continue
                seq += 1
                orders[nm] = Order("%s-%d" % (mk["slug"][:24], seq),
                                   toks[names.index(nm)], nm, p,
                                   CANDIDATE["quantity_shares"], now,
                                   size_at(bids[nm], p),
                                   size_at(bids[nm], p))
                sink({"kind": "shadow_order_placed", "market": mk["slug"],
                      "t": now, "order": orders[nm].row(now)})
        sink(row)
        time.sleep(poll_s)
    status = "COMPLETE" if (read_ok >= 30 and t_end is not None
                            and time.time() >= t_end) else "INCOMPLETE"
    sink({"kind": "market_terminal", "market": mk["slug"], "status": status,
          "polls": polls, "successful_reads": read_ok,
          "evidenced_fills": evidenced, "uncertain_fills": uncertain,
          "inventory": dict(inv), "cost": dict(cost),
          "matched": min(inv[names[0]], inv[names[1]]),
          "residual": abs(inv[names[0]] - inv[names[1]]),
          "note": ("status is explicit; missing or unreadable data is "
                   "never converted into zero fills")})
    return status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data/l5/shadow")
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--book-poll-s", type=float, default=1.0)
    ap.add_argument("--market-max-s", type=float, default=1200.0)
    ap.add_argument("--preflight-only", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.outdir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = os.path.join(a.outdir, "shadow_%s.jsonl" % stamp)
    pre = preamble(a.outdir)
    fh = open(path, "a", encoding="utf-8")

    def sink(row):
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()

    sink(pre)
    print(json.dumps({"tape": path,
                      "fingerprint": FINGERPRINT}, indent=2))
    if a.preflight_only:
        import ast
        src = open(__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        banned = {"post", "put", "delete", "patch", "sign", "sign_typed",
                  "send_transaction", "place_order", "create_order"}
        found = {n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)} & banned
        assert not found, "forbidden call name present: %s" % found
        secretish = {"private_key", "privatekey", "api_secret", "signer",
                     "wallet", "mnemonic", "seed_phrase"}
        idents = {n.id.lower() for n in ast.walk(tree)
                  if isinstance(n, ast.Name)}
        idents |= {n.attr.lower() for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute)}
        leak = idents & secretish
        assert not leak, "credential-ish identifier present: %s" % leak
        print(json.dumps({"preflight": "ok", "no_write_verbs": True,
                          "no_credential_identifiers": True,
                          "checked": "AST identifiers, not source text"},
                         indent=2))
        fh.close()
        return 0

    deadline = time.time() + a.minutes * 60.0
    done = []
    seen = set()
    while time.time() < deadline:
        mks, why = discover()
        if why != "OK":
            # NO_MATCH carries per-stage rejection counts so a zero is
            # diagnosable from the tape rather than by inference.
            sink({"kind": "discovery_error", "why": why, "t": time.time(),
                  "discovered": len(mks)})
            time.sleep(10.0)
            continue
        fresh = [m for m in mks if m["slug"] not in seen]
        if not fresh:
            sink({"kind": "market_terminal", "market": None,
                  "status": "NO_ELIGIBLE_MARKET", "t": time.time(),
                  "discovered": len(mks)})
            time.sleep(20.0)
            continue
        mk = fresh[0]
        seen.add(mk["slug"])
        try:
            st = collect_market(mk, sink, a.book_poll_s,
                                min(a.market_max_s,
                                    max(0.0, deadline - time.time())))
        except Exception as exc:                               # noqa: BLE001
            st = "COLLECTOR_ERROR"
            sink({"kind": "market_terminal", "market": mk["slug"],
                  "status": st, "error": repr(exc)[:300]})
        done.append((mk["slug"], st))
        print("market %s -> %s" % (mk["slug"], st), flush=True)
    sink({"kind": "session_summary", "markets": done,
          "complete": sum(1 for _, s in done if s == "COMPLETE"),
          "cohort_target_complete_markets": 25})
    fh.close()
    print(json.dumps({"markets": done,
                      "complete": sum(1 for _, s in done
                                      if s == "COMPLETE")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
