"""H-TURN: does opposite-token BOOK ARRIVAL predict L5's COMPLETE better than
fill-history persistence?

HISTORICAL REPLAY. Every number produced here describes the public fill tape of
Polymarket trader `l5zn1bwom8etsk` against a public anonymous order-book
archive. Nothing is shadow, submitted, filled, settled or realized. L5's cash
is never our profit. No order, no signing, no credential, no git command.

THE PREREGISTERED QUESTION, and the only thing tested:

    Conditioned on `prev_rel == "extend"`, does the contemporaneous
    OPPOSITE-TOKEN (complement / deficit-side) book state predict that the next
    fill is a `complete` -- the TURN -- materially better than persistence?

PERSISTENCE IS THE BASELINE. Given `prev_rel == extend`, persistence predicts
`extend` with probability 1. Its AUC is 0.5 by construction and its accuracy is
`1 - base_rate`. `analysis/l5/complete_vs_extend/FORENSIC.md` established that
on FILL HISTORY nothing moves the mismatch off ~0.9. This module asks only
whether the BOOK does, and it is DISCOVERY, not confirmation.

WHAT IS AND IS NOT A PRE-TURN FEATURE -- the leak discipline

Two feature families are computed and never mixed:

  F_PRE      book state at the DECISION instant `t_dec` = the timestamp of the
             previous (`extend`) fill. Strictly ex-ante: nothing after `t_dec`
             enters it. This is the only family that could be deployed.
  F_AT_NEXT  book state strictly BEFORE the next fill, `t_next - 1s`. This
             family knows WHEN the next fill happens, which an online system
             does not. It is symmetric across both cohorts -- a turn row and a
             control row are both read at their own next fill -- so it is a
             legitimate test of the MECHANISM ("was the complement obtainable
             at the instant he acted?"), but it is NOT a deployable predictor
             and is labelled DIAGNOSTIC everywhere.

Neither family ever reads the next fill's own side, price, size or outcome. The
asserted invariant, proved in `tests/test_l5_hturn.py`, is that every feature is
a function of book snapshots with `captured_at <= t_ref` only, and that the
label is computed from a column no feature touches.

THE COMPLEMENT PRICE RULE IS DERIVED FROM L5, NOT PICKED

A "plausible resting complement bid" has to come from L5's own behaviour or the
test is unfalsifiable. The rule used is his own cost basis:

    complement is OBTAINABLE  <=>  deficit_best_ask <= 1 - surplus_avg_cost

i.e. buying the deficit token at the prevailing ask still completes the pair for
strictly less than $1.00 against the average price he has ALREADY paid for the
surplus inventory he is sitting on. Both terms are observed: `surplus_avg_cost`
is the volume-weighted mean of his own realised fills on the surplus side in
this market, `deficit_best_ask` is a public price level. No threshold is
searched. A second, tighter variant uses CLAUDE.md's spring target (pair <=
0.96) and a third uses the mint-feasibility sum of
`analysis/l5/historical_l2/ZENODO_OVERLAP_FINDINGS.md` section 2. All three are
reported; none is fitted.

ANONYMOUS DEPTH IS NEVER ATTRIBUTED TO L5. `best_bid`/`best_ask` are price
levels in a public book. "A level existed" is not "L5's order existed"
(ZENODO_OVERLAP_FINDINGS section 7: these books quote 99.9% of the 1c grid, so
mere existence of size at a price is a constant, not a signal). Everything here
is about the PRICE of the touch, never about ownership.

SOURCES
  book   data/zenodo_22084977/updown_{15m,5m}.parquet -- Zenodo record
         22084977 (DOI 10.5281/zenodo.22084114, CC BY 4.0), 1s Polymarket L2,
         2026-08-21..24, BOTH outcome tokens per market.
  fills  data/l5/raw/activity_gap.jsonl.gz -- L5's public activity, joined to
         the book by (market_slug, outcome) which is the token id by
         construction; the token-id equality is asserted by test.
  role   analysis/l5/august_replay/l5_backfill.json and
         analysis/l5/microstructure/l5_backfill_5m.json -- chain-derived
         maker/taker, joined by transactionHash.

         ROLE PROVENANCE WARNING, stated once and carried in the JSON: the task
         brief asks for `execution.isAggressor`. That field DOES NOT EXIST on
         L5's public tape -- it exists only on our own account's PMUS execution
         records (`src/pmus_settlement_auditor.py` et al). The role used here is
         `l5_role`, derived from L5's TOPIC POSITION in the on-chain CTF
         exchange log by `analysis/l5/historical_l2/backfill.py`. That is a
         DIFFERENT field with a different provenance and it is labelled
         `CHAIN_TOPIC_POSITION`, never `isAggressor`. The bare boolean
         `aggressor` is not read anywhere.

BOOK ACCURACY BOUNDS, inherited from the dataset and not assumed away: a row is
written only when the book CHANGES, so an old row is current state, carried at
most MAX_CARRY_S = 60s (the collector's REST resync interval) and REFUSED
beyond that; ~24k events/s with no sequence numbers, so dropped deltas are
undetectable and every read carries up to one resync of possible drift.
"""
from __future__ import annotations

import bisect
import collections
import gzip
import json
import math
import os
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

BOOK_FILES = (
    os.path.join(ROOT, "data", "zenodo_22084977", "updown_15m.parquet"),
    os.path.join(ROOT, "data", "zenodo_22084977", "updown_5m.parquet"),
)
FILLS_GZ = os.path.join(ROOT, "data", "l5", "raw", "activity_gap.jsonl.gz")
ROLE_FILES = (
    os.path.join(ROOT, "analysis", "l5", "august_replay", "l5_backfill.json"),
    os.path.join(ROOT, "analysis", "l5", "microstructure", "l5_backfill_5m.json"),
)

# Dataset's own stated resync interval. A snapshot older than this is REFUSED.
MAX_CARRY_S = 60.0
# L5 trades only btc and eth, only 5m and 15m (BOOK_ACQUISITION.md section 2.2).
L5_COINS = ("btc", "eth")
# CLAUDE.md spring discipline: "materially cheaper pair completion (<= 0.96)".
SPRING_PAIR_TARGET = 0.96
# The forward-relevant cohort. OOS_RESULT.md section 3: L5's ex-incentive
# baseline is NEGATIVE here (-$5,851, -30.4bp) and positive in all three
# earlier regimes.
REGIME_2026_08_19_ON = 1786752000  # 2026-08-19T00:00:00Z


# ---------------------------------------------------------------- book index

class TokenBook:
    """Time-ordered snapshots for ONE outcome token of ONE market."""

    __slots__ = ("t", "bid", "ask", "bid_sz", "ask_sz", "crossed", "token_id")

    def __init__(self, token_id: str) -> None:
        self.token_id = token_id
        self.t: List[float] = []
        self.bid: List[Optional[float]] = []
        self.ask: List[Optional[float]] = []
        self.bid_sz: List[Optional[float]] = []
        self.ask_sz: List[Optional[float]] = []
        self.crossed: List[bool] = []

    def add(self, ts: float, bb, ba, bbs, bas, cr) -> None:
        self.t.append(ts)
        self.bid.append(bb)
        self.ask.append(ba)
        self.bid_sz.append(bbs)
        self.ask_sz.append(bas)
        self.crossed.append(bool(cr))

    def sort(self) -> None:
        order = sorted(range(len(self.t)), key=lambda i: self.t[i])
        self.t = [self.t[i] for i in order]
        self.bid = [self.bid[i] for i in order]
        self.ask = [self.ask[i] for i in order]
        self.bid_sz = [self.bid_sz[i] for i in order]
        self.ask_sz = [self.ask_sz[i] for i in order]
        self.crossed = [self.crossed[i] for i in order]

    def idx_at_or_before(self, ts: float) -> Optional[int]:
        """Index of the last snapshot at or before `ts`, refused if staler than
        MAX_CARRY_S. Strictly <= ts: a snapshot taken AT a later fill already
        contains that fill's effect."""
        i = bisect.bisect_right(self.t, ts) - 1
        if i < 0:
            return None
        if ts - self.t[i] > MAX_CARRY_S:
            return None
        return i

    def read(self, ts: float) -> Optional[Dict[str, Any]]:
        i = self.idx_at_or_before(ts)
        if i is None:
            return None
        return {
            "best_bid": self.bid[i], "best_ask": self.ask[i],
            "best_bid_size": self.bid_sz[i], "best_ask_size": self.ask_sz[i],
            "crossed": self.crossed[i],
            "snapshot_age_s": round(ts - self.t[i], 3),
        }

    def first_ask_at_or_below(self, lo: float, hi: float,
                              thr: float) -> Optional[float]:
        """Earliest snapshot time in (lo, hi] whose best_ask <= thr.
        Used only for the DIAGNOSTIC crossing-latency measurement."""
        i = bisect.bisect_right(self.t, lo)
        while i < len(self.t) and self.t[i] <= hi:
            a = self.ask[i]
            if a is not None and a <= thr + 1e-12:
                return self.t[i]
            i += 1
        return None


def load_books(paths: Sequence[str] = BOOK_FILES,
               coins: Sequence[str] = L5_COINS) -> Dict[str, Dict[str, TokenBook]]:
    """market_slug -> outcome -> TokenBook, for L5's coins only."""
    import pyarrow.parquet as pq

    out: Dict[str, Dict[str, TokenBook]] = {}
    cols = ["market_slug", "coin", "outcome", "token_id", "captured_at",
            "best_bid", "best_ask", "bid_prices", "bid_sizes",
            "ask_prices", "ask_sizes", "crossed"]
    keep = set(coins)
    for p in paths:
        d = pq.read_table(p, columns=cols).to_pydict()
        n = len(d["market_slug"])
        for i in range(n):
            if d["coin"][i] not in keep:
                continue
            slug, oc = d["market_slug"][i], d["outcome"][i]
            tb = out.setdefault(slug, {}).get(oc)
            if tb is None:
                tb = out[slug][oc] = TokenBook(d["token_id"][i])
            bp, bs = d["bid_prices"][i] or [], d["bid_sizes"][i] or []
            ap, asz = d["ask_prices"][i] or [], d["ask_sizes"][i] or []
            bbs = bs[0] if bp else None
            bas = asz[0] if ap else None
            tb.add(d["captured_at"][i].timestamp(), d["best_bid"][i],
                   d["best_ask"][i], bbs, bas, d["crossed"][i])
    for m in out.values():
        for tb in m.values():
            tb.sort()
    return out


# ---------------------------------------------------------------- fill tape

def load_overlap_fills(books: Dict[str, Dict[str, TokenBook]],
                       path: str = FILLS_GZ) -> List[Dict[str, Any]]:
    """L5's BUY TRADE fills whose market_slug is covered by the book, exact
    duplicates removed (the harvest paginates with overlapping windows and a
    duplicated fill would be double counted -- the same de-dup
    `analysis/l5/residual_frontier/oos_adapter.py` performs)."""
    want = set(books)
    seen, out = set(), []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            env = json.loads(line)
            for r in env.get("payload") or []:
                if r.get("type") != "TRADE" or r.get("side") != "BUY":
                    continue
                slug = r.get("slug") or r.get("eventSlug")
                if slug not in want:
                    continue
                k = (r.get("transactionHash"), slug, r.get("outcomeIndex"),
                     r.get("price"), r.get("size"), r.get("timestamp"))
                if k in seen:
                    continue
                seen.add(k)
                out.append({
                    "slug": slug, "outcome": r["outcome"],
                    "outcome_index": int(r["outcomeIndex"]),
                    "price": float(r["price"]), "size": float(r["size"]),
                    "timestamp": int(r["timestamp"]),
                    "tx": r.get("transactionHash"),
                })
    out.sort(key=lambda r: (r["slug"], r["timestamp"], r["outcome_index"]))
    return out


def load_roles(paths: Sequence[str] = ROLE_FILES) -> Dict[str, str]:
    """transactionHash -> 'maker' | 'taker' | 'mixed'.

    PROVENANCE `CHAIN_TOPIC_POSITION`, not `isAggressor`. See module docstring.
    A tx carrying both roles for L5 is 'mixed' and is excluded from the
    (A)/(B) role split rather than coerced to one side."""
    roles: Dict[str, set] = {}
    for p in paths:
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        for w in d.get("rows") or []:
            for f in w.get("fills") or []:
                tx, role = f.get("tx"), f.get("l5_role")
                if tx and role:
                    roles.setdefault(tx, set()).add(role)
    return {tx: (next(iter(v)) if len(v) == 1 else "mixed")
            for tx, v in roles.items()}


# --------------------------------------------------------- relation sequence

def relation(prev_residual: float, outcome: str) -> str:
    """Byte-for-byte the semantics of
    `src/polymarket_l5/action_model.py :: _relation` for BUY fills (the corpus
    holds no SELLs in this overlap; asserted by test). Imported by value, not
    by reference, because that module must not be modified or re-fitted."""
    if abs(prev_residual) < 1.0:
        return "open"
    surplus_side = "Up" if prev_residual > 0 else "Down"
    return "extend" if outcome == surplus_side else "complete"


def build_decisions(fills: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per transition: the state after fill i, and the relation of
    fill i+1. `prev_rel` is fill i's own relation."""
    by: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for f in fills:
        by[f["slug"]].append(f)

    rows: List[Dict[str, Any]] = []
    for slug, rs in by.items():
        rs.sort(key=lambda r: (r["timestamp"], r["outcome_index"]))
        residual = 0.0
        qty = {"Up": 0.0, "Down": 0.0}
        cost = {"Up": 0.0, "Down": 0.0}
        rels: List[str] = []
        run = 0
        for i, f in enumerate(rs):
            rel = relation(residual, f["outcome"])
            rels.append(rel)
            run = run + 1 if (i and rels[i - 1] == rel == "extend") else (
                1 if rel == "extend" else 0)
            qty[f["outcome"]] += f["size"]
            cost[f["outcome"]] += f["size"] * f["price"]
            residual = qty["Up"] - qty["Down"]
            if i + 1 >= len(rs):
                continue
            nxt = rs[i + 1]
            surplus = "Up" if residual > 0 else "Down"
            deficit = "Down" if surplus == "Up" else "Up"
            s_avg = (cost[surplus] / qty[surplus]) if qty[surplus] else None
            rows.append({
                "slug": slug,
                "fill_index": i,
                # --- state at the DECISION instant (strictly ex-ante) ---
                "t_dec": f["timestamp"],
                "prev_rel": rel,
                "prev_size": f["size"],
                "prev_price": f["price"],
                "signed_residual": residual,
                "abs_residual": abs(residual),
                "matched_qty": min(qty["Up"], qty["Down"]),
                "surplus_side": surplus,
                "deficit_side": deficit,
                "surplus_avg_cost": s_avg,
                "same_side_run": run,
                "fills_so_far": i + 1,
                # --- the LABEL, and the only thing read from fill i+1 ---
                "t_next": nxt["timestamp"],
                "next_rel": relation(residual, nxt["outcome"]),
                "tx_next": nxt["tx"],
                "next_size": nxt["size"],
            })
    rows.sort(key=lambda r: (r["slug"], r["fill_index"]))
    return rows


# ------------------------------------------------------------- book features

def _pair_features(books: Dict[str, TokenBook], row: Dict[str, Any],
                   t_ref: float, tag: str) -> Dict[str, Any]:
    """Both-token BBO at `t_ref` plus the derived obtainability flags.

    READS ONLY SNAPSHOTS WITH captured_at <= t_ref. `t_ref` itself is supplied
    by the caller and is either `t_dec` (ex-ante) or `t_next - 1` (diagnostic);
    nothing about the next fill's side/price/size reaches this function."""
    s_tb = books.get(row["surplus_side"])
    d_tb = books.get(row["deficit_side"])
    s = s_tb.read(t_ref) if s_tb else None
    d = d_tb.read(t_ref) if d_tb else None
    out: Dict[str, Any] = {tag + "_readable": bool(s and d)}
    if not (s and d):
        return out
    out.update({
        tag + "_surplus_bid": s["best_bid"], tag + "_surplus_ask": s["best_ask"],
        tag + "_surplus_bid_size": s["best_bid_size"],
        tag + "_surplus_ask_size": s["best_ask_size"],
        tag + "_deficit_bid": d["best_bid"], tag + "_deficit_ask": d["best_ask"],
        tag + "_deficit_bid_size": d["best_bid_size"],
        tag + "_deficit_ask_size": d["best_ask_size"],
        tag + "_crossed": bool(s["crossed"] or d["crossed"]),
        tag + "_age_s": max(s["snapshot_age_s"], d["snapshot_age_s"]),
    })
    da, db = d["best_ask"], d["best_bid"]
    sa = s["best_ask"]
    # pair cost to buy a FRESH pair at both touches, right now
    out[tag + "_pair_cost_market"] = (
        round(sa + da, 6) if (sa is not None and da is not None) else None)
    # L5's OWN marginal pair cost: what he has already paid on the surplus side
    # plus what the deficit token costs him at the touch. This is the quantity
    # the H-TURN mechanism says he is watching.
    av = row["surplus_avg_cost"]
    if av is not None and da is not None:
        out[tag + "_pair_cost_l5"] = round(av + da, 6)
        out[tag + "_headroom"] = round(1.0 - av - da, 6)
        out[tag + "_obtainable_sub1"] = bool(av + da < 1.0)
        out[tag + "_obtainable_spring"] = bool(av + da <= SPRING_PAIR_TARGET)
    else:
        out[tag + "_pair_cost_l5"] = None
        out[tag + "_headroom"] = None
        out[tag + "_obtainable_sub1"] = None
        out[tag + "_obtainable_spring"] = None
    # mint-feasibility sum (ZENODO_OVERLAP_FINDINGS section 2): L5's last fill
    # price plus the complement's best BID -- what two buyers of opposite
    # outcomes jointly pay to mint a dollar. NOT L5's own pair cost.
    out[tag + "_mint_sum"] = (
        round(row["prev_price"] + db, 6) if db is not None else None)
    out[tag + "_mint_feasible"] = (
        bool(row["prev_price"] + db >= 1.0) if db is not None else None)
    return out


def attach_book(rows: List[Dict[str, Any]],
                books: Dict[str, Dict[str, TokenBook]],
                roles: Dict[str, str]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        b = books.get(r["slug"]) or {}
        r = dict(r)
        # F_PRE: strictly ex-ante, at the decision instant.
        r.update(_pair_features(b, r, float(r["t_dec"]), "pre"))
        # F_AT_NEXT: DIAGNOSTIC. Symmetric across cohorts (both read at their
        # own next fill) but it knows WHEN the next fill is, so it is never a
        # deployable predictor.
        r.update(_pair_features(b, r, float(r["t_next"]) - 1.0, "nxt"))
        # crossing latency: when did the complement first become obtainable
        # between the decision and the next fill? DIAGNOSTIC for the same
        # reason -- the upper bound of the scan is the next fill's time.
        av = r["surplus_avg_cost"]
        d_tb = b.get(r["deficit_side"])
        r["cross_t"] = r["cross_lag_to_fill_s"] = r["cross_wait_s"] = None
        if d_tb is not None and av is not None and r["t_next"] > r["t_dec"]:
            thr = 1.0 - av
            ct = d_tb.first_ask_at_or_below(float(r["t_dec"]),
                                            float(r["t_next"]) - 1.0, thr)
            if ct is not None:
                r["cross_t"] = ct
                r["cross_wait_s"] = round(ct - r["t_dec"], 3)
                r["cross_lag_to_fill_s"] = round(r["t_next"] - ct, 3)
        r["gap_s"] = r["t_next"] - r["t_dec"]
        # the forensic's best non-book separator, recomputed here on these rows
        r["deficit_px_proxy"] = round(1.0 - r["prev_price"], 6)
        r["l5_role_chain"] = roles.get(r["tx_next"])
        r["regime_2026_08_19_on"] = bool(r["t_dec"] >= REGIME_2026_08_19_ON)
        r["is_turn"] = r["next_rel"] == "complete"
        out.append(r)
    return out


# ------------------------------------------------------------------- scoring

def auc(scores: Sequence[float], labels: Sequence[bool]) -> Optional[float]:
    """Mann-Whitney AUC with tie correction (ties score 0.5)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    srt = sorted(neg)
    tot = 0.0
    for p in pos:
        lo = bisect.bisect_left(srt, p)
        hi = bisect.bisect_right(srt, p)
        tot += lo + 0.5 * (hi - lo)
    return tot / (len(pos) * len(neg))


def _tte_bucket(row: Dict[str, Any]) -> str:
    """Time-to-settlement bucket at the DECISION instant. The window close
    epoch is parsed out of the slug (`<asset>-updown-<5m|15m>-<epoch>` names the
    window OPEN; close = open + duration), the same derivation
    `analysis/l5/residual_frontier/replay.py` uses. Never the next fill's TTE --
    that is the feature leak FORENSIC.md section 0 reported."""
    parts = row["slug"].split("-")
    dur = {"5m": 300, "15m": 900}.get(parts[-2])
    if dur is None:
        return "TTE_UNKNOWN"
    tte = (int(parts[-1]) + dur) - row["t_dec"]
    for hi, lab in ((60, "tte<60"), (180, "tte60-180"), (420, "tte180-420")):
        if tte < hi:
            return lab
    return "tte>=420"


def _run_bucket(n: int) -> str:
    return "run1" if n <= 1 else ("run2" if n == 2 else
                                  ("run3-4" if n <= 4 else "run>=5"))


def _res_bucket(x: float) -> str:
    for hi, lab in ((50, "res<50"), (150, "res50-150"), (400, "res150-400")):
        if x < hi:
            return lab
    return "res>=400"


def stratum(row: Dict[str, Any]) -> Tuple[str, ...]:
    """THE CONTROL. A turn row is only ever compared with EXTEND rows that
    stayed EXTENDS in the same market family, at a comparable point on the
    clock, after a comparable same-side run, carrying a comparable residual.
    Exactly the dimensions the brief names, and all four are computed from the
    DECISION instant only."""
    return (row["slug"].split("-")[-2], _tte_bucket(row),
            _run_bucket(row["same_side_run"]), _res_bucket(row["abs_residual"]))


def stratified_auc(rows: Sequence[Dict[str, Any]], feat: str
                   ) -> Dict[str, Any]:
    """AUC inside each matched stratum, pooled by concordant-pair count. A
    stratum with only turns or only controls contributes nothing and is
    counted, not dropped silently."""
    buckets: Dict[Tuple[str, ...], List[Dict[str, Any]]] = (
        collections.defaultdict(list))
    used = 0
    for r in rows:
        v = r.get(feat)
        if v is None or isinstance(v, bool) is False and not isinstance(v, (int, float)):
            if not isinstance(v, bool):
                continue
        buckets[stratum(r)].append(r)
        used += 1
    num = den = 0.0
    live = 0
    for st, rs in buckets.items():
        pos = [float(r[feat]) for r in rs if r["is_turn"]]
        neg = [float(r[feat]) for r in rs if not r["is_turn"]]
        if not pos or not neg:
            continue
        live += 1
        a = auc(pos + neg, [True] * len(pos) + [False] * len(neg))
        w = len(pos) * len(neg)
        num += a * w
        den += w
    return {"feature": feat, "n_rows_with_feature": used,
            "n_strata_total": len(buckets), "n_strata_informative": live,
            "stratified_auc": round(num / den, 4) if den else None,
            "concordant_pair_weight": int(den)}


def matched_pairs(rows: Sequence[Dict[str, Any]], seed: int = 20261007
                  ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """1:1 matched set: every turn row paired with one control EXTEND row from
    its OWN stratum, sampled without replacement at a fixed seed. Used for the
    contingency table so that table cannot be driven by stratum composition."""
    rng = random.Random(seed)
    pool: Dict[Tuple[str, ...], List[Dict[str, Any]]] = (
        collections.defaultdict(list))
    turns: List[Dict[str, Any]] = []
    for r in rows:
        (turns if r["is_turn"] else pool[stratum(r)]).append(r)
    for v in pool.values():
        rng.shuffle(v)
    out = []
    for t in sorted(turns, key=lambda r: (r["slug"], r["fill_index"])):
        c = pool.get(stratum(t))
        if c:
            out.append((t, c.pop()))
    return out


def cluster_bootstrap(rows: Sequence[Dict[str, Any]], feat: str,
                      n: int = 2000, seed: int = 20261007
                      ) -> Optional[Dict[str, Any]]:
    """WINDOW-AWARE CI. The resampling unit is the MARKET WINDOW (`slug`), not
    the row: rows inside one window share one book and one inventory
    trajectory, so a row-level bootstrap would be anticonservative. Returns
    None when too few windows carry both classes to make a CI meaningful."""
    by: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        if r.get(feat) is not None:
            by[r["slug"]].append(r)
    keys = [k for k in by if any(r["is_turn"] for r in by[k])
            and any(not r["is_turn"] for r in by[k])]
    if len(keys) < 8:
        return None
    rng = random.Random(seed)
    allk = list(by)
    draws = []
    for _ in range(n):
        samp: List[Dict[str, Any]] = []
        for _ in range(len(allk)):
            samp.extend(by[allk[rng.randrange(len(allk))]])
        a = auc([float(r[feat]) for r in samp], [r["is_turn"] for r in samp])
        if a is not None:
            draws.append(a)
    if len(draws) < n // 2:
        return None
    draws.sort()
    return {"n_resamples": len(draws), "unit": "market_window",
            "n_windows": len(allk), "n_windows_with_both_classes": len(keys),
            "ci95": [round(draws[int(0.025 * len(draws))], 4),
                     round(draws[int(0.975 * len(draws))], 4)]}


def _gap_bucket(g: float) -> str:
    for hi, lab in ((2, "gap<2s"), (5, "gap2-5s"), (15, "gap5-15s")):
        if g < hi:
            return lab
    return "gap>=15s"


def crossing_gap_controlled(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """THE CROSSING FLAG'S CONFOUND, controlled.

    `cross_t` is searched in the half-open interval (t_dec, t_next-1s], so a row
    with a longer inter-fill gap has more seconds in which a crossing can
    occur. Turns have a longer gap than controls by construction-ish (median 3s
    vs 1s), so the raw crossing rate is partly a gap-length effect. This
    reports the crossing rate INSIDE each gap bucket, plus the per-second
    hazard, so the gap cannot carry the result."""
    out: Dict[str, Any] = {"by_gap_bucket": {}}
    buckets: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        buckets[_gap_bucket(r["gap_s"])].append(r)
    num = den = 0.0
    for g, rs in sorted(buckets.items()):
        t = [r for r in rs if r["is_turn"]]
        c = [r for r in rs if not r["is_turn"]]
        pt = (sum(1 for r in t if r["cross_t"] is not None) / len(t)) if t else None
        pc = (sum(1 for r in c if r["cross_t"] is not None) / len(c)) if c else None
        out["by_gap_bucket"][g] = {
            "n_turn": len(t), "n_control": len(c),
            "p_cross_turn": round(pt, 4) if pt is not None else None,
            "p_cross_control": round(pc, 4) if pc is not None else None,
            "lift": round(pt / pc, 4) if (pt and pc) else None}
        if t and c:
            a = auc([1.0 if r["cross_t"] is not None else 0.0 for r in t + c],
                    [r["is_turn"] for r in t + c])
            w = len(t) * len(c)
            num += a * w
            den += w
    out["gap_stratified_auc_of_crossing_flag"] = (
        round(num / den, 4) if den else None)
    out["concordant_pair_weight"] = int(den)
    out["note"] = ("DIAGNOSTIC. The scan's upper bound is the next fill's own "
                   "time, so this family is not deployable; it is reported to "
                   "show that the raw 2.3x crossing lift is a gap-length "
                   "artifact, not a book signal.")
    return out


def role_table(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The forensic's cheap per-row (A)/(B) discriminator.

    FORENSIC.md section 4: `a turn on a MAKER fill is (A), on a TAKER fill is
    (B)`. Role provenance is CHAIN_TOPIC_POSITION, never `execution.isAggressor`
    (absent on L5's public tape) and never the bare boolean `aggressor`."""
    kn = [r for r in rows if r["l5_role_chain"] in ("maker", "taker")]
    tk = [r for r in kn if r["l5_role_chain"] == "taker"]
    mk = [r for r in kn if r["l5_role_chain"] == "maker"]
    t_t = sum(r["is_turn"] for r in tk)
    t_m = sum(r["is_turn"] for r in mk)
    out = {
        "provenance": "CHAIN_TOPIC_POSITION",
        "n_role_known": len(kn), "coverage": round(len(kn) / len(rows), 4)
        if rows else None,
        "p_turn_given_taker": round(t_t / len(tk), 4) if tk else None,
        "p_turn_given_maker": round(t_m / len(mk), 4) if mk else None,
        "turns_on_maker": t_m, "turns_on_taker": t_t,
        "share_of_turns_that_are_MAKER_fills": round(
            t_m / (t_m + t_t), 4) if t_m + t_t else None,
        "A_vs_B": ("(A) exogenous arrival for the MAKER share, (B) decision "
                   "for the TAKER share, per FORENSIC.md section 4"),
    }
    if tk and mk and t_m and (len(tk) - t_t) and (len(mk) - t_m):
        out["odds_ratio_taker_vs_maker"] = round(
            (t_t / (len(tk) - t_t)) / (t_m / (len(mk) - t_m)), 4)
    # do the book features work where role says a DECISION was made?
    out["book_auc_on_TAKER_rows_only"] = {
        f: {"n": len([r for r in tk if r.get(f) is not None]),
            "auc": (round(auc([float(r[f]) for r in tk if r.get(f) is not None],
                              [r["is_turn"] for r in tk
                               if r.get(f) is not None]), 4)
                    if sum(r["is_turn"] for r in tk) and
                    (len(tk) - sum(r["is_turn"] for r in tk)) else None)}
        for f in ("pre_pair_cost_l5", "pre_headroom", "pre_deficit_ask")}
    return out


def contingency(rows: Sequence[Dict[str, Any]], flag: str) -> Dict[str, Any]:
    c = collections.Counter((bool(r[flag]), r["is_turn"])
                            for r in rows if r.get(flag) is not None)
    a, b = c[(True, True)], c[(True, False)]
    d, e = c[(False, True)], c[(False, False)]
    out = {"flag": flag,
           "flag_true_turn": a, "flag_true_extend": b,
           "flag_false_turn": d, "flag_false_extend": e,
           "p_turn_given_flag": round(a / (a + b), 4) if a + b else None,
           "p_turn_given_not_flag": round(d / (d + e), 4) if d + e else None}
    if a + b and d + e and d and b:
        out["odds_ratio"] = round((a / b) / (d / e), 4)
        out["lift"] = round((a / (a + b)) / (d / (d + e)), 4) if d else None
    return out


# ---------------------------------------------------------------------- main

def run() -> Dict[str, Any]:
    books = load_books()
    fills = load_overlap_fills(books)
    roles = load_roles()
    rows = attach_book(build_decisions(fills), books, roles)

    ext = [r for r in rows if r["prev_rel"] == "extend"]
    base = sum(r["is_turn"] for r in ext) / len(ext) if ext else None

    inv_days = collections.Counter()
    for f in fills:
        import datetime as _dt
        inv_days[_dt.datetime.utcfromtimestamp(f["timestamp"]).strftime(
            "%Y-%m-%d")] += 1

    # THE BASELINE SET. "Materially better than fill-history persistence" has
    # to be judged on the SAME rows, so the forensic's own fill-tape
    # observables are rescored here. `deficit_px_proxy` is the forensic's
    # best non-book separator (unconditional AUC 0.410) and is the complement's
    # IMPLIED price, 1 - last fill price -- a proxy for exactly the book
    # quantity this module now measures directly.
    feats_fill = ["abs_residual", "same_side_run", "matched_qty",
                  "prev_price", "deficit_px_proxy", "fills_so_far",
                  "prev_size"]
    feats_pre = ["pre_deficit_ask", "pre_pair_cost_l5", "pre_headroom",
                 "pre_pair_cost_market", "pre_mint_sum", "pre_deficit_bid",
                 "pre_deficit_ask_size", "pre_surplus_bid"]
    feats_nxt = ["nxt_deficit_ask", "nxt_pair_cost_l5", "nxt_headroom",
                 "nxt_pair_cost_market", "nxt_mint_sum", "nxt_deficit_bid",
                 "nxt_deficit_ask_size", "nxt_surplus_bid"]
    # Persistence has no score: given prev_rel==extend it predicts `extend`
    # with probability 1, so its ranking of rows is constant and its AUC is
    # exactly 0.5. That constant is the number every AUC below is measured
    # against.
    PERSISTENCE_AUC = 0.5

    def block(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(rs)
        nt = sum(r["is_turn"] for r in rs)
        out = {
            "n_decisions": n, "n_turns": nt,
            "base_rate_turn": round(nt / n, 4) if n else None,
            "persistence_accuracy": round(1 - nt / n, 4) if n else None,
            "persistence_auc": PERSISTENCE_AUC,
            "book_readable_pre": sum(bool(r["pre_readable"]) for r in rs),
            "book_readable_at_next": sum(bool(r["nxt_readable"]) for r in rs),
            "EX_ANTE_F_PRE": {}, "DIAGNOSTIC_F_AT_NEXT": {},
        }
        for f in feats_pre:
            sub = [r for r in rs if r.get(f) is not None]
            out["EX_ANTE_F_PRE"][f] = {
                "n": len(sub),
                "raw_auc": (round(auc([float(r[f]) for r in sub],
                                      [r["is_turn"] for r in sub]), 4)
                            if sub else None),
                **{k: v for k, v in stratified_auc(sub, f).items()
                   if k != "feature"},
                "cluster_bootstrap": cluster_bootstrap(sub, f),
            }
        for f in feats_nxt:
            sub = [r for r in rs if r.get(f) is not None]
            out["DIAGNOSTIC_F_AT_NEXT"][f] = {
                "n": len(sub),
                "raw_auc": (round(auc([float(r[f]) for r in sub],
                                      [r["is_turn"] for r in sub]), 4)
                            if sub else None),
                **{k: v for k, v in stratified_auc(sub, f).items()
                   if k != "feature"},
                "cluster_bootstrap": cluster_bootstrap(sub, f),
            }
        out["BASELINE_FILL_HISTORY"] = {}
        for f in feats_fill:
            sub = [r for r in rs if r.get(f) is not None]
            out["BASELINE_FILL_HISTORY"][f] = {
                "n": len(sub),
                "raw_auc": (round(auc([float(r[f]) for r in sub],
                                      [r["is_turn"] for r in sub]), 4)
                            if sub else None),
                **{k: v for k, v in stratified_auc(sub, f).items()
                   if k != "feature"},
                "cluster_bootstrap": cluster_bootstrap(sub, f),
            }
        out["HEADLINE"] = {
            "persistence_auc": PERSISTENCE_AUC,
            "best_EX_ANTE_BOOK_feature": max(
                ((abs((v["stratified_auc"] or 0.5) - 0.5), f,
                  v["stratified_auc"], (v["cluster_bootstrap"] or {}).get("ci95"))
                 for f, v in out["EX_ANTE_F_PRE"].items()),
                default=None),
            "best_FILL_HISTORY_feature": max(
                ((abs((v["stratified_auc"] or 0.5) - 0.5), f,
                  v["stratified_auc"], (v["cluster_bootstrap"] or {}).get("ci95"))
                 for f, v in out["BASELINE_FILL_HISTORY"].items()),
                default=None),
        }
        out["contingency_unmatched"] = {
            f: contingency(rs, f) for f in
            ("pre_obtainable_sub1", "pre_obtainable_spring",
             "pre_mint_feasible", "nxt_obtainable_sub1",
             "nxt_obtainable_spring", "nxt_mint_feasible")}
        mp = matched_pairs(rs)
        flat = [x for p in mp for x in p]
        out["matched_1to1"] = {
            "n_pairs": len(mp),
            "n_turns_unmatched": sum(r["is_turn"] for r in rs) - len(mp),
            "base_rate_by_construction": 0.5,
            "contingency": {f: contingency(flat, f) for f in
                            ("pre_obtainable_sub1", "pre_obtainable_spring",
                             "nxt_obtainable_sub1", "nxt_obtainable_spring")},
            "auc": {f: (round(auc([float(r[f]) for r in flat
                                   if r.get(f) is not None],
                                  [r["is_turn"] for r in flat
                                   if r.get(f) is not None]), 4)
                        if any(r.get(f) is not None for r in flat) else None)
                    for f in ("pre_pair_cost_l5", "nxt_pair_cost_l5",
                              "pre_deficit_ask", "nxt_deficit_ask")},
        }
        # (A)/(B) role split, CHAIN_TOPIC_POSITION provenance.
        rc = collections.Counter(
            (r["l5_role_chain"], r["is_turn"]) for r in rs)
        out["role_split"] = {
            "provenance": "CHAIN_TOPIC_POSITION (NOT execution.isAggressor, "
                          "which does not exist on L5's public tape)",
            "coverage": round(sum(1 for r in rs if r["l5_role_chain"]
                                  in ("maker", "taker")) / len(rs), 4)
            if rs else None,
            "turns_maker": rc[("maker", True)], "turns_taker": rc[("taker", True)],
            "turns_role_unknown": sum(1 for r in rs if r["is_turn"]
                                      and r["l5_role_chain"] not in
                                      ("maker", "taker")),
            "controls_maker": rc[("maker", False)],
            "controls_taker": rc[("taker", False)],
        }
        tm, tt = rc[("maker", True)], rc[("taker", True)]
        out["role_split"]["turn_taker_share"] = (
            round(tt / (tm + tt), 4) if tm + tt else None)
        cross = [r for r in rs if r["cross_t"] is not None]
        out["crossing_latency_DIAGNOSTIC"] = {
            "n_with_a_crossing": len(cross),
            "turns_with_a_crossing": sum(r["is_turn"] for r in cross),
            "median_lag_cross_to_fill_s": _med(
                [r["cross_lag_to_fill_s"] for r in cross if r["is_turn"]]),
            "median_lag_cross_to_fill_s_controls": _med(
                [r["cross_lag_to_fill_s"] for r in cross if not r["is_turn"]]),
            "p_crossing_present_turn": _rate(rs, True),
            "p_crossing_present_control": _rate(rs, False),
        }
        out["crossing_gap_controlled"] = crossing_gap_controlled(rs)
        out["role_table_A_vs_B"] = role_table(rs)
        out["median_gap_s"] = {
            "turn": _med([r["gap_s"] for r in rs if r["is_turn"]]),
            "control": _med([r["gap_s"] for r in rs if not r["is_turn"]])}
        return out

    result = {
        "LABEL": "HISTORICAL REPLAY / DISCOVERY -- not shadow, not submitted, "
                 "not filled, not settled, never realized. L5's cash is not "
                 "our profit.",
        "preregistered_question": (
            "Conditioned on prev_rel=='extend', does opposite-token book "
            "arrival predict COMPLETE materially better than fill-history "
            "persistence (AUC 0.5, accuracy 1-base_rate)?"),
        "overlap": {
            "book_source": "Zenodo 22084977 updown_15m/5m parquet "
                           "(CC BY 4.0, DOI 10.5281/zenodo.22084114)",
            "book_span_utc": "2026-08-21 .. 2026-08-24",
            "markets_with_both_tokens": sum(
                1 for m in books.values() if len(m) == 2),
            "markets_in_book_l5_coins": len(books),
            "l5_fills_in_overlap": len(fills),
            "fills_by_day": dict(sorted(inv_days.items())),
            "markets_l5_actually_traded": len({f["slug"] for f in fills}),
            "all_transitions": len(rows),
            "prev_rel_counts": dict(collections.Counter(
                r["prev_rel"] for r in rows)),
            "transition_counts": {f"{a}->{b}": c for (a, b), c in sorted(
                collections.Counter(
                    (r["prev_rel"], r["next_rel"]) for r in rows).items())},
        },
        "ALL": block(ext),
        "REGIME_2026-08-19_ON": block(
            [r for r in ext if r["regime_2026_08_19_on"]]),
        "base_rate_turn_given_prev_extend": round(base, 4) if base else None,
        "accuracy_bounds": {
            "max_carry_s": MAX_CARRY_S,
            "note": "A book row is written only on change, so an old row is "
                    "current state; carried at most 60s (the collector's REST "
                    "resync) and REFUSED beyond. No sequence numbers in the "
                    "feed, so dropped deltas are undetectable: every read "
                    "carries up to one resync of possible drift.",
        },
    }
    return result


def _med(xs: Sequence[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return float(xs[m]) if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2, 3)


def _rate(rs: Sequence[Dict[str, Any]], turn: bool) -> Optional[float]:
    sub = [r for r in rs if r["is_turn"] == turn]
    if not sub:
        return None
    return round(sum(1 for r in sub if r["cross_t"] is not None) / len(sub), 4)


def main() -> None:
    res = run()
    out = os.path.join(HERE, "HTURN_TEST.json")
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=False)
    print("wrote", out)
    print(json.dumps(res["overlap"], indent=1))
    print("base rate", res["base_rate_turn_given_prev_extend"])


if __name__ == "__main__":
    main()
