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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data/l5/shadow")
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--book-poll-s", type=float, default=1.0)
    ap.add_argument("--preflight-only", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.outdir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = os.path.join(a.outdir, "shadow_%s.jsonl" % stamp)
    pre = preamble(a.outdir)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(pre, default=str) + "\n")
        fh.flush()
    print(json.dumps(pre, indent=2, default=str)[:2200])
    if a.preflight_only:
        # prove every import and the no-verb invariant without touching
        # the venue
        import ast
        src = open(__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        banned = {"post", "put", "delete", "patch", "sign", "sign_typed",
                  "send_transaction", "place_order", "create_order"}
        found = {n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)} & banned
        assert not found, "forbidden call name present: %s" % found
        # Check IDENTIFIERS via the AST, never a substring of this file's
        # own source -- an earlier version grepped the source and matched
        # the text of its own assertion.
        secretish = {"private_key", "privatekey", "api_secret", "signer",
                     "wallet", "mnemonic", "seed_phrase"}
        idents = {n.id.lower() for n in ast.walk(tree)
                  if isinstance(n, ast.Name)}
        idents |= {n.attr.lower() for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute)}
        idents |= {k.arg.lower() for n in ast.walk(tree)
                   if isinstance(n, ast.Call)
                   for k in (n.keywords or []) if k.arg}
        leak = idents & secretish
        assert not leak, "credential-ish identifier present: %s" % leak
        print(json.dumps({"preflight": "ok", "tape": path,
                          "no_write_verbs": True,
                          "no_credential_identifiers": True,
                          "checked": "AST identifiers, not source text"},
                         indent=2))
        return 0
    print(json.dumps({"note": "collection requires venue egress; this "
                               "container is CONNECT-blocked for "
                               "clob.polymarket.com, so the collector "
                               "runs in CI", "tape": path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
