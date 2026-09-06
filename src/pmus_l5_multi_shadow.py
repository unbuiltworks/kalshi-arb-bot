"""READ-ONLY multi-market L5 shadow for the PMUS recorder.

OWNER direction (maximize legitimate L5 evidence turnover, no added
financial risk): every technically observable live market the recorder
already subscribes to is shadowed with the SAME frozen v2 policy and the
SAME canonical lookup (lookup_version 3) the live probe and the Gemini
shadow use. For every market state the clone asks the model, draws the
sampled next action (seeded, deterministic), and persists what L5 would
probably do next -- relation, side, price, size, timing, inventory state
-- together with the read-only execution-eligibility verdict of the
safety shell and the subsequent market evolution. A safety refusal never
erases the fact that the model wanted to act: the BEHAVIORAL layer acts
its own draw against a theoretical resting order filled strictly from
public prints; the EXECUTABLE layer only records whether, and why, the
live shell would have let that action reach the venue.

Financial concurrency is untouched: this module places nothing, holds no
credentials, and is imported only by the recorder (whose image carries
no order path). Shadow concurrency is the recorder's subscription set.

Independence / deduplication: one clone per market, one decision per
(market, decision_idx), decision_id = sha256(slug|idx|t|state|sampled);
restart restores inventories from the persisted fills and drops
duplicate decision ids. Nothing is manufactured: a decision exists only
when the recorder observed a two-sided book at that moment, and a fill
exists only when a public print crossed the resting price.

Stated limitation (same as the live sports path): PMUS sports carry no
settlement clock, so TTE collapses to the coarsest bucket ("tte>10m").
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal as D
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.l5_shadow_scoring import (action_scoring, sampled_to_acted_funnel,
                                   split_rows_by_lookup)
from src.l5_venue_adapter import (LOOKUP_VERSION, MAX_RESIDUAL_UNITS, ONE,
                                  PAIR_TARGET, L5Inventory, NormalizedBook,
                                  PMUSAdapter, behavioral_size_shares,
                                  open_side_yes, proposal_v2,
                                  record_behavioral_fill)

DECISIONS_FILE = "l5_shadow_decisions.jsonl"
REPORT_FILE = "l5_shadow_report.json"
REEVAL_MIN_S = 2.0          # frozen cadence behavioral unit (fill-arrival gap)
REEVAL_MAX_S = 30.0
DEFAULT_SHARES = 10.0       # L5 p50 size when the artifact has no row
FLOW_WINDOW_S = 3600.0
# OWNER 5533476555 observation band -- EXECUTION eligibility only; the
# behavioral layer is never restricted by it
BAND_LO, BAND_HI = D("0.35"), D("0.65")
EXECUTABLE_CONTRACTS = D("0.01")     # R1 venue-minimum unit, reported only
LABELS = ("READ-ONLY multi-market shadow; theoretical fills from public "
          "prints; BEHAVIORAL layer = the frozen model's own draw acted in "
          "shadow; EXECUTABLE layer = what the live safety shell would have "
          "allowed; nothing placed")


def _code_sha() -> str:
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in ("pmus_l5_multi_shadow.py", "l5_venue_adapter.py"):
        try:
            h.update((here / name).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:16]


CODE_SHA = _code_sha()


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                 # noqa: BLE001
        return None


def _floor_tick(px: D, tick: D) -> D:
    return (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def _masses(relation_ranked: Any) -> dict:
    out = {"complete": 0.0, "extend": 0.0, "open": 0.0}
    for r in relation_ranked or []:
        if isinstance(r, Mapping) and r.get("relation") in out:
            out[str(r["relation"])] = float(r.get("p") or 0.0)
    return out


class ShadowMarket:
    """One theoretical L5 clone on one market."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.inv = L5Inventory()
        self.idx = 0
        self.resting: Optional[dict] = None
        self.next_t = 0.0
        self.trade_i = 0             # recorder trades already examined
        self.decisions: list[dict] = []      # compact, for scoring
        self.fills = 0
        self.entry_t: Optional[float] = None
        self.prev: Optional[dict] = None
        self.last_side_up: Optional[bool] = None

    def status(self) -> str:
        if self.inv.matched > 0:
            return "MATCHED"
        if self.inv.total > 0:
            return "RESIDUAL_HOLD"
        return "NO_FILL"

    def row(self) -> dict:
        return {"symbol": self.slug, "entry_t": self.entry_t,
                "status": self.status(), "decisions": self.decisions,
                "inventory": self.inv.snapshot()}


class MultiShadow:
    def __init__(self, root: Path, model: Optional[Mapping[str, Any]] = None,
                 art: Optional[Mapping[str, Any]] = None,
                 market_state_of: Optional[Callable[[str], Optional[Mapping]]] = None) -> None:
        self.root = Path(root)
        self.model = model or pv2.load_v2()
        self.art = art if art is not None else fid.load()
        self.model_sha = str(self.model.get("_sha256_16") or "")
        self.adapter = PMUSAdapter()
        self.market_state_of = market_state_of
        self.markets: dict[str, ShadowMarket] = {}
        self.seen_ids: set[str] = set()
        self.funnel: dict[str, Any] = {
            "ticks": 0, "evaluations": 0,
            "discovered_markets": 0, "observable": 0,
            "canonical_state_valid": 0, "in_distribution": 0,
            "l5_action_sampled": 0, "execution_eligible": 0,
            "losses": {}, "fills": 0, "decisions": 0}
        self.restore = self._restore()

    # ------------------------------------------------------------ persist
    def _path(self) -> Path:
        return self.root / DECISIONS_FILE

    def _append(self, row: Mapping[str, Any]) -> None:
        self._path().parent.mkdir(parents=True, exist_ok=True)
        with self._path().open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")

    def _restore(self) -> dict:
        path = self._path()
        n, dup, fills = 0, 0, 0
        if not path.exists():
            return {"decisions": 0, "duplicates_dropped": 0, "fills": 0}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            m = self.markets.setdefault(str(row.get("slug")), ShadowMarket(str(row.get("slug"))))
            if row.get("kind") == "pmus_shadow_decision":
                did = str(row.get("decision_id") or "")
                if not did or did in self.seen_ids:
                    dup += 1
                    continue
                self.seen_ids.add(did)
                m.decisions.append(self._compact(row))
                m.idx = max(m.idx, int(row.get("idx") or 0) + 1)
                m.entry_t = m.entry_t or row.get("t")
                m.prev = {"decision_id": did, "t": row.get("t"),
                          "mid": (row.get("book") or {}).get("mid"),
                          "sampled": row.get("sampled")}
                n += 1
            elif row.get("kind") == "pmus_shadow_fill":
                try:
                    side_up = bool(row.get("side") == "YES")
                    m.inv.record(side_up, D(str(row["px"])), D(str(row["qty"])),
                                 str(row.get("relation")), t=row.get("t"))
                    m.inv.record_behavioral_shares(
                        side_up, float(row.get("shares_behavioral") or 0.0),
                        row.get("size_bucket_behavioral"))
                    m.last_side_up = side_up
                    m.fills += 1
                    fills += 1
                except Exception:                     # noqa: BLE001
                    continue
        self.funnel["decisions"] = n
        self.funnel["fills"] = fills
        return {"decisions": n, "duplicates_dropped": dup, "fills": fills,
                "markets": len(self.markets)}

    @staticmethod
    def _compact(row: Mapping[str, Any]) -> dict:
        lk = row.get("lookup") or {}
        ex = row.get("executable") or {}
        return {"t": row.get("t"), "state": row.get("state"),
                "sampled": row.get("sampled"), "acted": row.get("acted"),
                "refusal": row.get("refusal"),
                "p_complete": row.get("p_complete"), "p_extend": row.get("p_extend"),
                "residual_units": row.get("residual_units"),
                "off_distribution": bool(row.get("off_distribution")),
                "lookup": {k: lk.get(k) for k in (
                    "lookup_version", "state", "state_v1", "last_rel",
                    "last_size_behavioral", "pre_fill", "off_distribution_v1")},
                "executable_eligible": ex.get("eligible"),
                "executable_refusals": ex.get("refusals"),
                "pair_at_touch": ex.get("pair_at_touch"),
                "pair_gt_1": ex.get("pair_gt_1"),
                "fee_safe_at_touch": ex.get("fee_safe_at_touch")}

    # --------------------------------------------------------------- loss
    def _loss(self, reason: str) -> None:
        self.funnel["losses"][reason] = self.funnel["losses"].get(reason, 0) + 1

    # -------------------------------------------------------------- fills
    def _apply_prints(self, m: ShadowMarket, s: Mapping[str, Any]) -> None:
        trades = s.get("trades") or []
        if m.trade_i > len(trades):          # recorder trimmed its tape
            m.trade_i = 0
        new = trades[m.trade_i:]
        m.trade_i = len(trades)
        if not m.resting:
            return
        for tr in new:
            r = m.resting
            if r is None:
                break
            px = _d(tr.get("px"))
            if px is None:
                continue
            d = str(tr.get("dir") or "unknown")
            if r["side_up"]:
                crossed = d == "sell" and px <= r["px"]
            else:
                crossed = d == "buy" and px >= (ONE - r["px"])
            if not crossed:
                continue
            qty = _d(tr.get("qty")) or D(str(r["shares"]))
            got = min(D(str(r["shares"])), qty)
            if got <= 0:
                continue
            fill_px = D(str(r["px"]))
            m.inv.record(r["side_up"], fill_px, got, r["relation"], t=tr.get("t"))
            record_behavioral_fill(m.inv, self.art, r["state"], r["relation"], r["side_up"])
            m.last_side_up = r["side_up"]
            m.fills += 1
            self.funnel["fills"] += 1
            self._append({"kind": "pmus_shadow_fill", "slug": m.slug,
                          "decision_id": r["decision_id"], "t": tr.get("t"),
                          "side": "YES" if r["side_up"] else "NO",
                          "px": str(fill_px), "qty": str(got),
                          "relation": r["relation"], "state_at_decision": r["state"],
                          "shares_behavioral": (behavioral_size_shares(
                              self.art, r["state"], r["relation"]) or 0.0),
                          "size_bucket_behavioral": m.inv.last_size_behavioral,
                          "print": {"px": str(px), "qty": str(qty), "dir": d},
                          "labels": "THEORETICAL fill: public print crossed the "
                                    "resting behavioral price"})
            r["shares"] = float(D(str(r["shares"])) - got)
            if r["shares"] <= 0:
                m.resting = None
            m.next_t = 0.0                    # a fill re-opens the decision

    # ----------------------------------------------------------- decide
    def _flow(self, s: Mapping[str, Any], now: float) -> tuple[int, bool]:
        dirs, n = set(), 0
        for tr in s.get("trades") or []:
            try:
                if now - float(tr.get("t") or 0) > FLOW_WINDOW_S:
                    continue
            except (TypeError, ValueError):
                continue
            n += 1
            d = str(tr.get("dir") or "unknown")
            if d in ("buy", "sell"):
                dirs.add(d)
        return n, len(dirs) == 2

    def _decide(self, m: ShadowMarket, s: Mapping[str, Any], now: float) -> Optional[dict]:
        self.funnel["evaluations"] += 1
        bid, ask = _d(s.get("bid")), _d(s.get("ask"))
        if bid is None or ask is None or bid <= 0 or ask >= ONE or ask <= bid:
            self._loss("no_two_sided_book")
            return None
        self.funnel["observable"] += 1
        tick = _d(s.get("tick")) or _d((s.get("market_state") or {}).get("orderPriceMinTickSize"))
        raw = {"slug": m.slug, "bid": str(bid), "ask": str(ask),
               "bid_qty": s.get("bid_qty"), "ask_qty": s.get("ask_qty"),
               "tick": str(tick) if tick else None}
        book = self.adapter.normalize(raw, now)
        if book is None:
            self._loss("normalize_failed")
            return None
        try:
            prop = proposal_v2(self.model, book, m.inv, code_sha=CODE_SHA)
        except Exception as exc:                      # noqa: BLE001
            self._loss(f"lookup_error:{type(exc).__name__}")
            return None
        self.funnel["canonical_state_valid"] += 1
        lk = prop["lookup"]
        # INDEPENDENCE: an unchanged observation is not new evidence. The
        # same touch, state, last relation and inventory as the previous
        # decision is re-decided only after the rest unit (REEVAL_MAX_S,
        # the live session's own re-decision on an unchanged book); a
        # book move or a fill re-opens the decision immediately.
        sig = (str(bid), str(ask), lk["state"], m.inv.last_rel, str(m.inv.total))
        if m.prev and m.prev.get("sig") == sig and \
                now - float(m.prev.get("t") or 0) < REEVAL_MAX_S:
            self._loss("unchanged_state_within_rest")
            return None
        off = bool(prop.get("off_distribution"))
        if not off:
            self.funnel["in_distribution"] += 1
        else:
            self._loss("off_distribution")
        seed = fid.seed_material(m.slug, m.idx, self.model_sha)
        draw = fid.sample_relation(prop.get("relation_ranked"), seed)
        rel = draw.get("sampled") or prop.get("recommended_relation")
        if rel not in ("open", "extend", "complete", "sell"):
            self._loss("no_relation")
            return None
        self.funnel["l5_action_sampled"] += 1
        # ---- behavioral action: side / price / size the way L5 acts
        if rel == "open":
            side_up = open_side_yes(book)
        elif rel == "extend":
            side_up = m.last_side_up if m.last_side_up is not None else open_side_yes(book)
        elif rel == "complete":
            side_up = bool(m.inv.residual < 0)          # deficit side
        else:
            side_up = None                              # sell: not modelled live
        px_l5 = book.join_price(side_up) if side_up is not None else None
        shares = behavioral_size_shares(self.art, lk["state"], rel) or DEFAULT_SHARES
        # ---- executable counterfactual (what the live shell would do)
        refusals: list[str] = []
        mid = book.mid
        if mid is None or mid < BAND_LO or mid > BAND_HI:
            refusals.append("entry_band" if lk["pre_fill"] else "entry_band_left")
        n_tr, two_sided = self._flow(s, now)
        if n_tr == 0:
            refusals.append("thin_flow")
        elif not two_sided:
            refusals.append("not_two_sided")
        if not s.get("both_sides_served", True):
            refusals.append("book_not_served")
        ms = (self.market_state_of(m.slug) if self.market_state_of else None) or s.get("market_state")
        if isinstance(ms, Mapping) and (ms.get("closed") or str(ms.get("status") or "").endswith(("HALTED", "RESOLVED", "CLOSED"))):
            refusals.append("market_not_open")
        if rel == "sell":
            refusals.append("sell_relation_not_executable")
        if rel == "extend" and abs(float(lk.get("residual_units") or 0)) >= MAX_RESIDUAL_UNITS:
            refusals.append("residual_cap")
        if off:
            refusals.append("off_distribution_hold")
        executable: dict[str, Any] = {"size_contracts": str(EXECUTABLE_CONTRACTS),
                                      "px": str(px_l5) if px_l5 is not None else None}
        if rel == "complete" and m.inv.residual != 0 and px_l5 is not None:
            surplus_avg = m.inv.avg_dn() if m.inv.residual < 0 else m.inv.avg_up()
            if surplus_avg is not None:
                pair_at_touch = surplus_avg + px_l5
                bound = _floor_tick(PAIR_TARGET - surplus_avg, book.tick)
                fee_safe_px = bound if bound > 0 else None
                exec_px = min(px_l5, fee_safe_px) if fee_safe_px is not None else None
                executable.update({
                    "pair_at_touch": str(pair_at_touch),
                    "fee_safe_at_touch": bool(pair_at_touch <= PAIR_TARGET),
                    "pair_gt_1": bool(pair_at_touch > ONE),
                    "fee_safe_bound_px": str(fee_safe_px) if fee_safe_px is not None else None,
                    "px": str(exec_px) if exec_px is not None else None,
                    "delta_ticks_touch_minus_executable": (
                        str(((px_l5 - exec_px) / book.tick).to_integral_value())
                        if exec_px is not None else None),
                    "counterfactual": "L5 completes at the touch; the live shell "
                                      "rests at the fee-safe bound (pair <= 0.98) "
                                      "or refuses"})
                if exec_px is None:
                    refusals.append("complement_not_priceable")
        executable["eligible"] = not refusals
        executable["refusals"] = refusals
        if not refusals:
            self.funnel["execution_eligible"] += 1
        else:
            for r_ in refusals:
                self._loss(f"execution:{r_}")
        masses = _masses(prop.get("relation_ranked"))
        did = hashlib.sha256(f"{m.slug}|{m.idx}|{now}|{lk['state']}|{rel}".encode()).hexdigest()[:24]
        evolution = None
        if m.prev and m.prev.get("mid") is not None and mid is not None:
            try:
                evolution = {"prev_decision_id": m.prev["decision_id"],
                             "dt_s": round(now - float(m.prev["t"]), 1),
                             "mid_move_ticks": str(((mid - D(str(m.prev["mid"]))) / book.tick)
                                                   .to_integral_value()),
                             "prev_sampled": m.prev.get("sampled")}
            except Exception:                         # noqa: BLE001
                evolution = None
        rec = {"kind": "pmus_shadow_decision", "decision_id": did, "slug": m.slug,
               "idx": m.idx, "t": now,
               "t_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
               "state": lk["state"], "lookup": lk, "state_n": prop.get("state_n"),
               "relation_ranked": prop.get("relation_ranked"),
               "p_complete": masses["complete"], "p_extend": masses["extend"],
               "sampled": rel, "acted": rel, "refusal": None,
               "draw": {k: draw.get(k) for k in ("u", "p", "rank", "seed")},
               "off_distribution": off,
               "residual_units": lk.get("residual_units"),
               "inventory_before": m.inv.snapshot(),
               "book": {"yes_bid": str(bid), "yes_ask": str(ask),
                        "yes_bid_qty": s.get("bid_qty"), "yes_ask_qty": s.get("ask_qty"),
                        "mid": str(mid), "spread": str(book.spread), "tick": str(book.tick),
                        "trades_1h": n_tr, "two_sided_1h": two_sided},
               "behavioral": {"side": (None if side_up is None else ("YES" if side_up else "NO")),
                              "px": str(px_l5) if px_l5 is not None else None,
                              "shares": shares,
                              "note": "the frozen model's own draw acted in shadow"},
               "executable": executable,
               "next_reevaluation_s": max(REEVAL_MIN_S, min(REEVAL_MAX_S, float(
                   prop.get("next_reevaluation_s") or REEVAL_MIN_S))),
               "evolution_from_prev": evolution,
               "model_sha": self.model_sha, "code_sha": CODE_SHA,
               "lookup_version": LOOKUP_VERSION, "labels": LABELS}
        self._append(rec)
        self.seen_ids.add(did)
        m.decisions.append(self._compact(rec))
        m.entry_t = m.entry_t or now
        m.prev = {"decision_id": did, "t": now, "mid": str(mid), "sampled": rel,
                  "sig": sig}
        m.idx += 1
        self.funnel["decisions"] += 1
        if side_up is not None and px_l5 is not None:
            m.resting = {"side_up": side_up, "px": px_l5, "shares": float(shares),
                         "relation": rel, "decision_id": did, "state": lk["state"], "t": now}
        m.next_t = now + rec["next_reevaluation_s"]
        return rec

    # --------------------------------------------------------------- tick
    def tick(self, mstate: Mapping[str, Mapping[str, Any]], now: float) -> dict:
        """One pass over every subscribed market: theoretical fills from
        new prints, then a decision where the cadence allows."""
        self.funnel["ticks"] += 1
        self.funnel["discovered_markets"] = max(self.funnel["discovered_markets"], len(mstate))
        made = 0
        for slug, s in list(mstate.items()):
            if not isinstance(s, Mapping):
                continue
            m = self.markets.setdefault(slug, ShadowMarket(slug))
            try:
                self._apply_prints(m, s)
                if now >= m.next_t:
                    if self._decide(m, s, now) is not None:
                        made += 1
                    else:
                        m.next_t = now + REEVAL_MIN_S     # one loss per cadence unit
            except Exception as exc:                  # noqa: BLE001
                self._loss(f"tick_error:{type(exc).__name__}")
                m.next_t = now + REEVAL_MIN_S
        return {"decisions_made": made, "markets": len(self.markets)}

    # ------------------------------------------------------------- report
    def report(self, now: Optional[float] = None) -> dict:
        now = now or 0.0
        rows = [m.row() for m in self.markets.values() if m.decisions]
        sc = action_scoring(rows) if rows else {"scored_decisions": 0}
        split = split_rows_by_lookup(rows) if rows else {"provenance": {}}
        sa = sampled_to_acted_funnel(rows) if rows else {}
        decs = [d for r in rows for d in r["decisions"]]
        post = [d for d in decs if abs(float(d.get("residual_units") or 0)) >= 1]
        completes = [d for d in decs if d.get("sampled") == "complete" and d.get("pair_at_touch") is not None]
        gt1 = [d for d in completes if d.get("pair_gt_1")]
        eligible = [d for d in decs if d.get("executable_eligible")]
        ref: dict = {}
        for d in decs:
            for r_ in d.get("executable_refusals") or []:
                ref[r_] = ref.get(r_, 0) + 1
        keep = ("scored_decisions", "expected_relation_freq_l5", "sampled_relation_freq",
                "l1_sampled_vs_l5", "acted_relation_freq", "l1_acted_vs_l5",
                "post_fill", "off_distribution_rate_pct")
        return {
            "kind": "pmus_l5_multi_shadow", "t": datetime.now(timezone.utc).isoformat(),
            "labels": LABELS, "lookup_version": LOOKUP_VERSION,
            "model_sha": self.model_sha, "code_sha": CODE_SHA,
            "markets_shadowed": len(self.markets),
            "markets_with_decisions": len(rows),
            "unique_decisions": len(decs),
            "post_fill_decisions": len(post),
            "theoretical_fills": self.funnel["fills"],
            "restore": self.restore,
            "funnel": self.funnel,
            "lookup_provenance": {**(split.get("provenance") or {}), "code_sha": CODE_SHA},
            "behavioral_layer": {
                **{k: sc.get(k) for k in keep},
                "labels": "what the frozen model wanted to do (sampled draw acted in "
                          "shadow) vs L5's conditional masses at the same states"},
            "executable_layer": {
                "decisions": len(decs),
                "execution_eligible": len(eligible),
                "eligible_share": round(len(eligible) / len(decs), 4) if decs else None,
                "refusals_by_reason": ref,
                "complete_counterfactual": {
                    "complete_draws_with_inventory": len(completes),
                    "pair_gt_1_count": len(gt1),
                    "pair_gt_1_share": round(len(gt1) / len(completes), 4) if completes else None,
                    "fee_safe_at_touch_share": (round(sum(1 for d in completes if d.get("fee_safe_at_touch")) / len(completes), 4)
                                                if completes else None),
                    "reading": "how often the clone would diverge from L5 only because "
                               "L5 tolerated a pair cost above what the fee-safe rule allows"},
                "labels": "what the live safety/economic shell would have let reach the "
                          "venue; R1 size 0.01 contracts; nothing placed"},
            "sampled_to_acted": sa,
        }

    def write_report(self, now: Optional[float] = None) -> Optional[dict]:
        try:
            rep = self.report(now)
            path = self.root / REPORT_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rep, separators=(",", ":"), default=str), encoding="utf-8")
            os.replace(tmp, path)
            return rep
        except Exception as exc:                      # noqa: BLE001
            return {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}
