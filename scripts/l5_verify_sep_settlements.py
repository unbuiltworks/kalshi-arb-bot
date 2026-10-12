"""Authoritative settlement outcomes for the Sep 23-25 held-out sample.

WHY. The treatment replay infers a winner when L5 holds one side and redeems
nothing: "the other side won". On the held-out days that covers 114 of 521
markets, and those 114 contribute -1,693.57 against a headline result of
+366.24 -- 4.6x the result. Flipping them turns COMPLEMENT_REPAIR from +366 to
+5,994 and inverts its ranking against RESIDUAL_CAP_k2. So the sign of the edge
is decided by an assumption, and this script replaces the assumption with the
venue's own record.

IT NEVER INFERS. A market with no authoritative record is reported as
UNRESOLVED, by conditionId, and is excluded from the recomputation rather than
guessed either way. That is the whole point of the exercise.

WHERE IT RUNS. Read-only public HTTP, no credentials of any kind, and it
cannot place an order: the only verbs are GET. It exists to run under
.github/workflows/l5-ops.yml, whose own header states "read-only public HTTP
only. No exchange credentials are used or available to this workflow; it
cannot place orders." The agent container's egress denies every Polymarket
host (all four return 000 / 403 at the proxy), which is why the lookup has to
happen on a runner that already has public internet.

PROVENANCE IS PER MARKET, not per run: each record carries the exact URL, the
HTTP status, the fetch timestamp, and the raw resolution fields it was derived
from, so a later reader can audit any single market without re-fetching.

TWO SOURCES, agreement required where both answer:
  * gamma  GET /markets?condition_ids=<cid>   -> outcomes, outcomePrices
  * clob   GET /markets/<cid>                 -> tokens[].winner
A market where the two disagree is reported CONFLICT and treated as
unresolved. An outcomePrices pair that is not {0,1} (an unresolved or
partially resolved market) is also unresolved, never rounded.
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
UA = {"User-Agent": "l5-settlement-verify/1 (read-only)"}
OUT = "data/l5/ops/sep_settlements.json"
#: the hermetic id list, committed so this runs where the 21MB daily activity
#: file is not checked out (it is absent from feature/l5-canonical and does
#: not need to be there).
IDS = "data/l5/ops/sep_heldout_condition_ids.json"
#: fallback, used only when the full activity file happens to be present
SRC = "data/l5/raw/sep/l5_sep23_25_daily.json"
DAYS = ("2026-09-24", "2026-09-25")
PAUSE_S = float(os.getenv("VERIFY_PAUSE_S", "0.25"))
MAX = int(os.getenv("VERIFY_MAX", "10000"))
#: "unredeemed" (default) verifies only the 114 markets whose outcome is
#: otherwise unobtainable -- the ones that decide the sign. "all" verifies all
#: 521, which also audits the REDEEM-confirmed 407 against the venue.
SCOPE = os.getenv("VERIFY_SCOPE", "unredeemed").strip().lower()


def get(url, tries=4):
    """GET only. Returns (body, status, error). Never raises."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA, method="GET")
            with urllib.request.urlopen(req, timeout=30) as fh:
                return json.loads(fh.read().decode() or "null"), \
                    getattr(fh, "status", 200), None
        except Exception as exc:                               # noqa: BLE001
            last = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            code = getattr(exc, "code", None)
            if code == 404:
                return None, 404, last
            time.sleep(PAUSE_S * (i + 1) * 2)
    return None, None, last


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def held_out_ids():
    """The conditionIds to verify, and whether each redeemed in the corpus.

    Prefers the committed id list so the job needs no large input. Falls back
    to re-deriving from the raw activity file when that is available.
    """
    if os.path.exists(IDS):
        doc = json.load(open(IDS))
        out = {c: dict(v) for c, v in (doc.get("markets") or {}).items()}
        for v in out.values():
            v["redeemed"] = bool(v.pop("redeemed_in_corpus", False))
            v.setdefault("index_labels", {})
        if SCOPE != "all":
            out = {c: v for c, v in out.items() if not v["redeemed"]}
        print("id list: %s (scope=%s, %d markets)" % (IDS, SCOPE, len(out)),
              flush=True)
        return out
    data = json.load(open(SRC))
    out = {}
    for day in DAYS:
        rows = data[day]
        red = {r["conditionId"] for r in rows.get("REDEEM", [])}
        for t in rows.get("TRADE", []):
            cid = t.get("conditionId")
            if not cid:
                continue
            rec = out.setdefault(cid, {"day": day, "redeemed": False,
                                       "trades": 0, "slug": t.get("slug")})
            rec["trades"] += 1
            if cid in red:
                rec["redeemed"] = True
    if SCOPE != "all":
        out = {c: v for c, v in out.items() if not v["redeemed"]}
    return out


def norm(label):
    return str(label or "").strip().lower()


def from_gamma(cid):
    """Winning LABEL from outcomes[] zipped with outcomePrices[].

    NOT an array index. gamma's ordering, CLOB's tokens[] ordering and the
    activity feed's outcomeIndex are three independent orderings, and
    assuming they coincide is how a winner silently inverts. The label is
    the only shared identity.
    """
    url = "%s/markets?%s" % (GAMMA, urllib.parse.urlencode(
        {"condition_ids": cid, "limit": 5}))
    body, status, err = get(url)
    rec = {"source": "gamma", "url": url, "status": status, "error": err,
           "fetched_at": utc(), "winner_label": None, "raw": None}
    rows = body if isinstance(body, list) else (
        body.get("data") if isinstance(body, dict) else None)
    for m in (rows or []):
        if not isinstance(m, dict) or m.get("conditionId") != cid:
            continue
        op, oc = m.get("outcomePrices"), m.get("outcomes")
        for k, v in (("outcomePrices", op), ("outcomes", oc)):
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:                              # noqa: BLE001
                    v = None
            rec[k] = v
        rec["raw"] = {k: m.get(k) for k in
                      ("closed", "closedTime", "umaResolutionStatus",
                       "automaticallyResolved", "slug", "endDate")}
        op, oc = rec.get("outcomePrices"), rec.get("outcomes")
        if not (isinstance(op, list) and isinstance(oc, list)
                and len(op) == len(oc) == 2):
            return rec
        try:
            prices = [float(x) for x in op]
        except Exception:                                      # noqa: BLE001
            return rec
        if sorted(prices) != [0.0, 1.0]:
            # unresolved or partially resolved: never rounded to a winner
            rec["why"] = "outcomePrices not {0,1}"
            return rec
        rec["winner_label"] = oc[0] if prices[0] == 1.0 else oc[1]
        return rec
    return rec


def from_clob(cid):
    url = "%s/markets/%s" % (CLOB, cid)
    body, status, err = get(url)
    rec = {"source": "clob", "url": url, "status": status, "error": err,
           "fetched_at": utc(), "winner_label": None, "raw": None}
    if isinstance(body, dict):
        rec["raw"] = {k: body.get(k) for k in
                      ("closed", "active", "end_date_iso", "market_slug",
                       "question_id")}
        toks = body.get("tokens")
        if isinstance(toks, list):
            rec["tokens"] = [{"outcome": t.get("outcome"),
                              "winner": t.get("winner")}
                             for t in toks if isinstance(t, dict)]
            wins = [t.get("outcome") for t in rec["tokens"]
                    if t.get("winner") is True]
            if len(wins) == 1:
                rec["winner_label"] = wins[0]
            elif len(wins) > 1:
                rec["why"] = "more than one winning token"
    return rec


def main():
    ids = held_out_ids()
    print("held-out conditionIds: %d (%d redeemed, %d not)"
          % (len(ids), sum(1 for v in ids.values() if v["redeemed"]),
             sum(1 for v in ids.values() if not v["redeemed"])), flush=True)
    out = {"audit": "L5_SEP_SETTLEMENT_VERIFY", "started_utc": utc(),
           "days": list(DAYS), "source_file": SRC,
           "method": ("GET only, two independent public sources, agreement "
                      "required where both answer. No winner is ever "
                      "inferred from missing REDEEM activity."),
           "markets": {}}
    st = collections.Counter()
    for n, (cid, meta) in enumerate(sorted(ids.items()), 1):
        if n > MAX:
            st["skipped_cap"] += 1
            continue
        g = from_gamma(cid)
        time.sleep(PAUSE_S)
        c = from_clob(cid)
        time.sleep(PAUSE_S)
        gw, cw = g.get("winner_label"), c.get("winner_label")
        if gw is not None and cw is not None:
            same = norm(gw) == norm(cw)
            verdict = "VERIFIED" if same else "CONFLICT"
            label = gw if same else None
        elif gw is not None or cw is not None:
            verdict = "VERIFIED_SINGLE_SOURCE"
            label = gw if gw is not None else cw
        else:
            verdict = "UNRESOLVED"
            label = None
        # MAP THE LABEL TO L5'S OWN outcomeIndex, using the mapping taken
        # from his trade rows for THIS market. A venue label he never traded
        # cannot be mapped, and is not guessed.
        winner = None
        idx_labels = {k: v for k, v in
                      (meta.get("index_labels") or {}).items()}
        if label is not None:
            hits = [int(k) for k, v in idx_labels.items()
                    if norm(v) == norm(label)]
            if len(hits) == 1:
                winner = hits[0]
            elif idx_labels:
                verdict = "LABEL_NOT_IN_L5_TRADES"
            else:
                verdict = "NO_L5_LABEL_MAPPING"
        st[verdict] += 1
        st["redeemed" if meta["redeemed"] else "not_redeemed"] += 1
        if verdict.startswith("VERIFIED") and not meta["redeemed"]:
            st["verified_among_the_inferred"] += 1
        out["markets"][cid] = {
            "day": meta["day"], "slug": meta["slug"],
            "trades": meta["trades"], "redeemed_in_corpus": meta["redeemed"],
            "verdict": verdict, "winner_label": label,
            "winner_index": winner,
            "l5_index_labels": idx_labels,
            "gamma": g, "clob": c}
        if n % 25 == 0:
            print("  %d/%d  %s" % (n, len(ids), dict(st)), flush=True)
    out["finished_utc"] = utc()
    out["summary"] = dict(st)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print("\nSUMMARY %s" % json.dumps(dict(st), sort_keys=True), flush=True)
    print("written: %s" % OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
