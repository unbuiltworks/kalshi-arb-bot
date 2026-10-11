"""GEMINI_L5_COHORT_RUNNER_V1 -- ONE process, N consecutive BTC15m contracts.

WHAT THIS REPLACES, AND WHY IT IS ORCHESTRATION AND NOTHING ELSE.

Every live window so far has been a GitHub Actions step: the workflow started,
waited up to 420s for a contract to enter the 840:880 band, ran one window and
exited. That shape put MARKET TIMING IN THE SCHEDULER. Three consequences, all
observed:

  * the readiness gate could only pass when a contract happened to be near the
    band, so "is the tree ready" was answered by the clock;
  * a window that missed its band refused with the grant intact -- correct, but
    it cost a dispatch;
  * CAP4-LIVE-2 entered at tte 874, met one crossed tick at tte 332, withdrew,
    and the PROCESS ENDED. There was another BTC15m contract fifteen minutes
    later and nothing alive to take it.

This module keeps one process alive across contracts. It adds NO strategy, NO
pricing, NO sizing and NO bound of its own. Every limit is still enforced
inside `ws_continuous.Driver`, and every window runs
`run_continuous.run_one_window` -- the SAME body a single-window launcher runs,
not a second copy of it. The lifecycle is:

    startup
      -> static readiness (no TTE dependency, zero mutations)
      -> discover the current or next BTC15m contract
      -> [ reconcile account -> bind -> quote/trade -> terminal withdrawal
           -> verify zero working orders -> record -> roll ] x N
      -> cohort stop

WHAT MAKES IT FAIL-CLOSED ACROSS THE ROLL. A window ends with the driver's own
terminal withdrawal and its own authoritative after-read. Before the next
contract is bound this module re-asserts the two things that could carry
contamination forward -- that the venue lists no controllable order for us, and
that the venue's position agrees with our ledger -- and STOPS the cohort if
either cannot be established. `run_one_window` then does its own recovery and
position gate again on the new symbol, from scratch, because a fresh Driver and
a fresh Inventory are built per contract: nothing about window N is an object
that window N+1 can see.

THE BUDGET IS THE COHORT LEDGER, and it already survives restart: it is a
single append-only jsonl that every window's Driver reads `consumed()` from, so
a process that dies mid-cohort comes back with its mutation budget spent, not
reset. This module adds the counters that ledger does not carry -- windows
observed, windows traded, and aggregate economic loss -- in a durable state
file beside it, written atomically, read on startup.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence,
                    Tuple)

from src.gemini_execution import bundle as BUNDLE
from src.gemini_execution import cohort as CO
from src.gemini_execution import cohort_economics as CE
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_status as LS
from src.gemini_execution import metadata as MD
from src.gemini_execution import position_ledger as PL
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC

LABEL = "GEMINI_L5_COHORT_RUNNER_V1"

#: where the durable one-authorization-one-cohort latch lives.
DEFAULT_STATE_DIR = "analysis/gemini/canary_latch"

#: how long the runner may sit between discovery polls. Read-only, sends
#: nothing, spends nothing.
DISCOVERY_POLL_S = 10.0

#: a BTC15m contract is 900s. A runner that has not found an eligible contract
#: in two full contract periods is not waiting, it is broken -- so it stops
#: rather than idling forever against a venue that may have changed.
DISCOVERY_GIVE_UP_S = 1900.0

STATE_FILE = "cohort_state.json"


# ------------------------------------------------------------ durable state

def state_path(cohort_id: str, *, root: str = CO.COHORT_ROOT) -> str:
    return os.path.join(CO.directory(cohort_id, root=root), STATE_FILE)


def _blank(cohort_id: str, *, max_windows: int,
           max_loss_usd: str) -> Dict[str, Any]:
    return {"label": LABEL, "cohort_id": cohort_id,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "max_windows": int(max_windows),
            "max_aggregate_loss_usd": str(max_loss_usd),
            "windows_observed": 0, "windows_traded": 0,
            "aggregate_loss_usd": "0",
            "symbols": [], "windows": [],
            "stopped": False, "stop_why": None,
            "labels": ("windows_observed counts CONTRACTS BOUND. A contract "
                       "that produced no fill is an observed window and is "
                       "not a failure: nothing in this runner requires a "
                       "trade to happen, and no parameter is tuned between "
                       "windows.")}


def load_state(cohort_id: str, *, root: str = CO.COHORT_ROOT,
               max_windows: int = 0, max_loss_usd: str = "0"
               ) -> Dict[str, Any]:
    """Durable cohort counters. A missing file is a fresh cohort; an
    unreadable one is NOT, and refuses rather than starting over."""
    p = state_path(cohort_id, root=root)
    if not os.path.exists(p):
        return _blank(cohort_id, max_windows=max_windows,
                      max_loss_usd=max_loss_usd)
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(st: Mapping[str, Any], *, root: str = CO.COHORT_ROOT) -> str:
    """Atomic: write beside, fsync, rename. A torn counter file would read as
    a reset budget, which is the one failure mode worth the extra syscall."""
    p = state_path(st["cohort_id"], root=root)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    return p


# --------------------------------------------------------- static readiness

def latch_counterfactual(auth_id: str, *, state_dir: str,
                         head: Optional[str] = None,
                         root: str = CO.COHORT_ROOT,
                         max_windows: int = 0) -> Dict[str, Any]:
    """Would the latch let this authorization reach the mutation boundary?

    READ-ONLY. It consumes nothing, writes nothing and creates no latch: it
    mirrors the decision `_consume_cohort_latch` will make and reports it.

    WHY THIS EXISTS. The latch means ONE AUTHORIZATION, ONE COHORT, and
    `latch.consumed()` keys on the authorization id ALONE -- not on the
    cohort, the spec or the family. Probe B was declared ready twice against
    OWNER-2026-09-23-GEMINI-Q1-AB-1 and could never have placed an order,
    because Probe A had already spent that latch: B is a different cohort
    under the same id, so it would have returned STOPPED_NO_LATCH before its
    first mutation. Neither readiness run could see it, because readiness
    passes readiness=True and the latch path runs only under `if not
    readiness:`. So readiness proved everything about the tree EXCEPT the one
    thing that decided the outcome. It now proves that too.
    """
    from src.gemini_execution import latch as LATCH
    out: Dict[str, Any] = {
        "auth_id": auth_id, "state_dir": state_dir,
        "latch_path_exists": LATCH.consumed(auth_id, state_dir),
        "consumes_nothing": True,
        "labels": ("the COUNTERFACTUAL of _consume_cohort_latch, evaluated "
                   "read-only. `reaches_mutation_boundary` false means the "
                   "armed run would stop at STOPPED_NO_LATCH having placed "
                   "nothing."),
    }
    if not out["latch_path_exists"]:
        out.update(reaches_mutation_boundary=True, would="CONSUME_FRESH",
                   why=("no latch exists for this authorization, so the "
                        "armed run would spend it and proceed"), ok=True)
        return out

    # A latch exists. The only path that still reaches the boundary is a
    # CONTINUATION of the very cohort that latch already bought.
    st: Mapping[str, Any] = {}
    try:
        if head:
            st = load_state(head, root=root, max_windows=max_windows)
    except Exception as exc:                                  # noqa: BLE001
        out["state_why"] = "%s: %s" % (type(exc).__name__, exc)
    observed = int((st or {}).get("windows_observed") or 0)
    remaining = int(max_windows) - observed
    out.update(windows_observed=observed, windows_remaining=remaining,
               cohort_stopped=bool((st or {}).get("stopped")))
    if (st or {}).get("stopped"):
        out.update(reaches_mutation_boundary=False, would="REFUSE_STOPPED",
                   why=("authorization %s is spent and its cohort is STOPPED "
                        "(%s); a stopped cohort is not resumed automatically"
                        % (auth_id, (st or {}).get("stop_why"))), ok=False)
    elif observed <= 0 or remaining <= 0:
        out.update(reaches_mutation_boundary=False, would="REFUSE_SPENT",
                   why=("authorization %s is already consumed and this cohort "
                        "has %d of %d windows observed; this is "
                        "reconciliation work, never a re-run"
                        % (auth_id, observed, max_windows)), ok=False)
    else:
        out.update(reaches_mutation_boundary=True, would="RESUME",
                   why=("continuing the cohort this latch already bought: "
                        "%d of %d observed, %d remaining"
                        % (observed, max_windows, remaining)), ok=True)
    return out


async def static_readiness(spec: Any, *, auth_id: str,
                           root: str = CO.COHORT_ROOT,
                           chain: Sequence[str] = (),
                           state_dir: str = DEFAULT_STATE_DIR,
                           max_windows: int = 0,
                           now: Optional[float] = None) -> Dict[str, Any]:
    """Is the TREE ready? Asked without reference to any contract's clock.

    THE POINT OF "STATIC". The workflow gate answered this question by first
    waiting for a contract to reach 840:880, so a perfectly ready tree failed
    readiness for being dispatched at the wrong minute, and market timing and
    software correctness were the same check. They are different questions.
    Everything below is a property of the TREE and the ACCOUNT: hashes,
    declared spec, budget ledger, terms, and whether the venue will tell us
    what we hold. None of it depends on TTE, and none of it mutates.
    """
    out: Dict[str, Any] = {
        "label": "GEMINI_COHORT_STATIC_READINESS_V1",
        "utc": datetime.now(timezone.utc).isoformat(),
        "tte_independent": True,
        "mutations_sent": 0,
        "checks": {}, "why_not": [],
    }
    ch = out["checks"]

    ch["spec"] = {"spec_id": spec.spec_id, "spec_sha": spec.spec_sha(),
                  "family": spec.family, "envelope": spec.envelope,
                  "envelope_id": spec.envelope_obj.name,
                  "envelope_sha": spec.envelope_obj.sha(),
                  "entry_tte_band": spec.entry_tte_band,
                  "action_size": spec.action_size,
                  "quote_band": (None if spec.quote_band is None
                                 else list(spec.quote_band)),
                  "bounds": spec.bounds(), "ok": True}

    # ---- the installed authorization, and whether it covers THIS tree
    #
    # THE CODE HALF, NOT THE ENV HALF. `authorization_state()` folds two
    # different questions into one verdict: whether the TREE is authorized
    # (REQUIRED_AUTH_ID, AUTHORIZED_SHA, AUTHORIZED_ENVELOPE -- reviewed
    # constants a human edits in a commit) and whether THIS PROCESS carries
    # the transport arm (GEMINI_CANARY_LIVE, which lives only in the step
    # that will actually trade and never in the repository).
    #
    # A read-only readiness step deliberately sets no arm, so folding the arm
    # in here would make this check unable to return PASS from anywhere
    # except an armed step -- which is exactly what run 35670074043 showed:
    # spec, bundle, family, cohort, terms and positions all PASS, and the
    # single why_not was "transport arm: GEMINI_CANARY_LIVE is not YES".
    # That is the step being read-only, not the tree being unready.
    #
    # NOTHING IS LOOSENED. The arm is still required at execution time by
    # WC.authorized() inside the driver, on every mutation, unchanged. It is
    # reported here as observation rather than asserted as a tree property.
    auth = WC.authorization_state()
    arm = auth.get("arming") or {}
    tree_reasons = [r for r in (auth.get("why_not") or [])
                    if not str(r).startswith("transport arm:")]
    ch["authorization"] = {
        **auth,
        "tree_authorized": not tree_reasons,
        "tree_why_not": tree_reasons,
        "transport_armed": bool(arm.get("armed")),
        "transport_note": (
            "the arm is a property of the STEP that trades, not of the tree. "
            "This check never sets it and never asserts it; the driver "
            "requires it on every mutation."),
        "ok": not tree_reasons}
    out["why_not"] += tree_reasons
    b = BUNDLE.state()
    ch["bundle"] = {**b, "ok": bool(b.get("authorized"))}
    if not b.get("authorized"):
        out["why_not"] += list(b.get("why_not") or [])
    if str(auth.get("required_auth_id") or "") != str(auth_id):
        ch["authorization"]["ok"] = False
        out["why_not"].append(
            "the tree is armed for %r but this cohort runs under %r"
            % (auth.get("required_auth_id"), auth_id))

    # ---- the family the envelope was sized for
    want = str(getattr(spec.envelope_obj, "cohort_prefix", "") or "")
    got = spec.symbol_family
    ch["family_agrees"] = {"envelope_family": want, "spec_family": got,
                           "ok": (not want) or want == got}
    if not ch["family_agrees"]["ok"]:
        out["why_not"].append(
            "envelope %s is sized for %s but the spec resolves %s"
            % (spec.envelope_obj.name, want, got))

    # ---- the budget ledger: exists, intact, and has room
    cid = (chain[0] if chain else spec.budget_cohort)
    if chain:
        rows = []
        for c in chain:
            try:
                cs = CO.state(c, root=root)
                rows.append({"cohort_id": c, "budget": cs["budget"],
                             "consumed": cs["consumed"],
                             "remaining": cs["remaining"],
                             "predecessor_intact":
                                 (cs.get("predecessor") or {}).get("intact")})
            except Exception as exc:                          # noqa: BLE001
                rows.append({"cohort_id": c, "readable": False,
                             "why": "%s: %s" % (type(exc).__name__, exc)})
        ok = (len(rows) == len(chain)
              and all(r.get("predecessor_intact") for r in rows)
              and sum(r.get("remaining", 0) for r in rows) > 0)
        ch["cohort_chain"] = {
            "ledgers": rows, "n": len(chain),
            "actions_per_window": 12,
            "total_budget": sum(r.get("budget", 0) for r in rows),
            "total_remaining": sum(r.get("remaining", 0) for r in rows),
            "ok": ok}
        if not ok:
            out["why_not"].append(
                "the cohort's ledger chain is not readable, intact and "
                "unspent: %s" % json.dumps(rows, default=str)[:400])
    if not cid:
        ch["cohort"] = {"ok": False, "why": "the spec names no budget ledger"}
        out["why_not"].append("no budget ledger is granted for this spec")
    else:
        try:
            cst = CO.state(cid, root=root)
            pred = cst.get("predecessor") or {}
            # WHO OWNS THE "IS THERE ROOM" QUESTION. With a chain, the answer
            # is the CHAIN's remaining capacity, and `cohort_chain` above
            # already asserted it. Asking the HEAD ledger as well is asking
            # the wrong ledger: a cohort resuming on W02 has a head that is
            # legitimately spent, and run 35676429611 failed readiness on
            # exactly that -- 108 slots remaining across the chain, and a
            # FAIL because W01 was 12/12. Without a chain the head IS the
            # budget and the old assertion is kept unchanged.
            has_chain = bool(chain)
            room = cst.get("remaining", 0) > 0
            ok = bool(pred.get("intact")) and (has_chain or room)
            ch["cohort"] = {**cst, "ok": ok, "is_chain_head": has_chain,
                            "room_asserted_here": not has_chain,
                            "note": ("the head of a chain may be spent; the "
                                     "cohort's remaining capacity is the "
                                     "chain's, asserted by cohort_chain"
                                     if has_chain else
                                     "this spec has a single ledger, so the "
                                     "head IS the budget")}
            if not pred.get("intact"):
                out["why_not"].append(
                    "cohort %s was created against a ledger that has since "
                    "changed: %s" % (cid, pred.get("why")))
            if not room and not has_chain:
                out["why_not"].append(
                    "cohort %s has no mutation slots left (%s/%s)"
                    % (cid, cst.get("consumed"), cst.get("budget")))
        except Exception as exc:                              # noqa: BLE001
            ch["cohort"] = {"ok": False,
                            "why": "%s: %s" % (type(exc).__name__, exc)}
            out["why_not"].append("cohort %s unusable: %s" % (cid, exc))

    # ---- the account: terms, and whether the venue will state our position
    try:
        ts = await MD.terms_status() or {}
    except Exception as exc:                                  # noqa: BLE001
        ts = {"readable": False,
              "why": "%s: %s" % (type(exc).__name__, exc)}
    accepted = bool(ts.get("hasAcceptedLatest")
                    or ts.get("terms_accepted")
                    or ts.get("verdict") == "ACCEPTED")
    ch["terms"] = {**ts, "ok": accepted}
    if not accepted:
        out["why_not"].append(
            "terms are not established as accepted: %s"
            % (ts.get("why") or ts.get("verdict") or "unreadable"))

    try:
        pos = await MD.positions()
    except Exception as exc:                                  # noqa: BLE001
        pos = {"ok": False, "rows": None,
               "why": "%s: %s" % (type(exc).__name__, exc)}
    ch["positions_readable"] = {
        "ok": bool(pos.get("ok")) and pos.get("rows") is not None,
        "status": pos.get("status"), "why": pos.get("why"),
        "n_rows_on_account": (None if pos.get("rows") is None
                              else len(pos["rows"])),
        "note": ("readability only. Which rows belong to which contract is a "
                 "per-window question and is asked again, per window, by the "
                 "position gate inside run_one_window.")}
    if not ch["positions_readable"]["ok"]:
        out["why_not"].append(
            "the venue's position list is not readable; an unread account is "
            "never a flat one")

    # ---- THE LATCH, WHICH READINESS USED NOT TO ASK ABOUT AT ALL.
    # Everything above is a property of the tree and the account. This is the
    # property of the AUTHORIZATION, and it is the one that decided Probe B.
    ch["latch"] = latch_counterfactual(
        auth_id, state_dir=state_dir,
        head=(chain[0] if chain else (getattr(spec, "budget_cohort", None))),
        root=root, max_windows=max_windows or len(chain or ()))
    if not ch["latch"].get("ok"):
        out["why_not"].append(
            "latch: %s" % (ch["latch"].get("why") or "would refuse"))

    out["verdict"] = "PASS" if not out["why_not"] else "FAIL"
    out["ok"] = out["verdict"] == "PASS"
    return out


# ------------------------------------------------------------- economics

def window_loss_usd(rep: Mapping[str, Any],
                    resolution: Optional[Mapping[str, Any]] = None,
                    rebates_usd: Any = None) -> Dict[str, Any]:
    """DEPRECATED NAME, CORRECTED MEANING. See `cohort_economics`.

    This used to return `total_spend_usd` -- gross confirmed BUY outflow --
    under the name `reconstructed_loss_usd`, and the cohort's aggregate stop
    compared that against a dollar LOSS limit. COHORT-1 was halted at 4 of its
    8 remaining windows on 10.63 of "loss" while the four windows it had
    records for were +1.14 settlement-implied. Buy outflow is TURNOVER; for a
    strategy that buys both legs and is paid on one it accumulates at about
    half of turnover whether the cohort is winning or losing.

    It now delegates to the economics module, which keeps the seven lines
    apart and produces an economic loss ONLY for a settled contract.
    """
    return CE.window_economics(rep, resolution, rebates_usd)


async def resolution_for(symbol: str) -> Dict[str, Any]:
    """The venue's own contract object, or an empty dict. PUBLIC read."""
    try:
        return MD.resolution_for(symbol) or {}
    except Exception:                                         # noqa: BLE001
        return {}


# ------------------------------------------------ spread / placement capture

def spread_placement(rep: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Where our desired price sat in the spread, cycle by cycle.

    PURE FUNCTION OVER THE REPORT. It opens no socket and takes no venue
    read: every field already exists in the decision capture. The question it
    exists to answer is whether the frozen surface's prices exploit the wide
    spreads this venue shows -- 8 to 23 cents on the BTC15m book -- or sit
    somewhere that cannot fill. It proposes nothing: bid improvement and
    midpoint placement are separate evidence questions and no number here
    changes a price.
    """
    rows: List[Dict[str, Any]] = []
    fills = _fill_index(rep)
    for dec in rep.get("decisions") or []:
        pres = ((dec.get("book_state") or {}).get("presented") or {})
        if not pres.get("readable"):
            rows.append({"cycle": dec.get("cycle"), "tte_s": dec.get("tte_s"),
                         "book": "UNREADABLE",
                         "why": pres.get("why")})
            continue
        for leg_key, leg in (("yes", "YES"), ("no", "NO")):
            side = pres.get(leg_key) or {}
            bid, ask = _d(side.get("bid")), _d(side.get("ask"))
            mid = None if (bid is None or ask is None) else (bid + ask) / 2
            for a in (dec.get("actions") or []):
                if str(a.get("leg")) != leg:
                    continue
                px = _d(a.get("price"))
                f = fills.get((leg, str(a.get("price"))))
                rows.append({
                    "cycle": dec.get("cycle"), "utc": dec.get("utc"),
                    "tte_s": dec.get("tte_s"), "leg": leg,
                    "action": a.get("action"), "desired_price": a.get("price"),
                    "qty": a.get("qty"),
                    "bid": side.get("bid"), "ask": side.get("ask"),
                    "spread": (None if bid is None or ask is None
                               else str(ask - bid)),
                    "mid": (None if mid is None else str(mid)),
                    "from_bid": (None if px is None or bid is None
                                 else str(px - bid)),
                    "from_ask": (None if px is None or ask is None
                                 else str(px - ask)),
                    "from_mid": (None if px is None or mid is None
                                 else str(px - mid)),
                    "inside_spread": (None if px is None or bid is None
                                      or ask is None else bool(bid < px < ask)),
                    "filled": bool(f), "fill": f,
                    "rest_s": (f or {}).get("rest_s"),
                })
    return rows


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                         # noqa: BLE001
        return None


def _fill_index(rep: Mapping[str, Any]) -> Dict[Any, Dict[str, Any]]:
    """(leg, price) -> the fill the window recorded for it, with rest time."""
    out: Dict[Any, Dict[str, Any]] = {}
    for ev in ((rep.get("window") or {}).get("order_events") or []):
        if str(ev.get("kind")).upper() not in ("FILL", "PARTIAL"):
            continue
        key = (str(ev.get("leg")), str(ev.get("price")))
        out[key] = {"utc": ev.get("utc"), "qty": ev.get("qty"),
                    "price": ev.get("price"),
                    "rest_s": ev.get("rest_s") or ev.get("resting_s")}
    return out


# ------------------------------------------------------------ the roll gate

async def rollover_gate(symbol: str, *,
                        state_fn: Optional[Callable] = None,
                        positions_fn: Optional[Callable] = None
                        ) -> Dict[str, Any]:
    """May contract N+1 be bound? Asked about contract N, fail-closed.

    Two things could carry across a roll, and both are asked of the VENUE
    rather than of our own memory:

      * a controllable order still resting on N. The window's own terminal
        withdrawal should have cleared it; this re-reads and refuses if the
        venue still lists one, or if the list cannot be read at all.
      * a position on N that our ledger does not know about. Unreadable is
        not flat here any more than anywhere else.

    A refusal STOPS the cohort. It does not skip a contract and carry on:
    the one state that must never be guessed is what we are still holding.
    """
    st_fn = state_fn or RC.authoritative_state
    pos_fn = positions_fn or MD.positions
    out: Dict[str, Any] = {"label": "COHORT_ROLLOVER_GATE_V1",
                           "symbol": symbol,
                           "utc": datetime.now(timezone.utc).isoformat()}
    try:
        st = await st_fn(symbol)
    except Exception as exc:                                  # noqa: BLE001
        out.update(ok=False, verdict="UNREADABLE",
                   why="authoritative state unreadable after %s: %s: %s"
                       % (symbol, type(exc).__name__, exc))
        return out
    open_orders = st.get("open_orders")
    if open_orders is None:
        out.update(ok=False, verdict="UNREADABLE",
                   why=("the venue's open-order list is unreadable after %s; "
                        "an unread venue is never an empty one and the next "
                        "contract is not bound on a guess" % symbol))
        return out
    out["open_orders_after"] = len(open_orders)
    if open_orders:
        out.update(ok=False, verdict="ORDERS_REMAIN",
                   why=("%d order(s) still listed on %s after terminal "
                        "withdrawal; the cohort stops rather than binding a "
                        "second contract beside them"
                        % (len(open_orders), symbol)))
        return out
    try:
        pos = await pos_fn()
    except Exception as exc:                                  # noqa: BLE001
        pos = {"ok": False, "rows": None,
               "why": "%s: %s" % (type(exc).__name__, exc)}
    if not pos.get("ok") or pos.get("rows") is None:
        out.update(ok=False, verdict="POSITION_UNREADABLE",
                   why=("the venue's position list is unreadable after %s "
                        "(%s); flat is not assumed"
                        % (symbol, pos.get("why"))))
        return out
    rows = PL.rows_for(pos.get("rows"), symbol)
    out["position_rows_for_symbol"] = len(rows)
    out["carried_position"] = rows or None
    out.update(ok=True, verdict="CLEAR",
               why=None,
               note=("a position still open on %s is PRESERVED as provenance "
                     "and settles on its own contract; it is never netted "
                     "into the next one, because each window builds a fresh "
                     "Driver and a fresh Inventory from that contract's own "
                     "authoritative fills" % symbol))
    return out


# ------------------------------------------------------------- discovery

async def await_contract(spec: Any, *,
                         exclude: Sequence[str] = (),
                         give_up_s: float = DISCOVERY_GIVE_UP_S,
                         poll_s: float = DISCOVERY_POLL_S,
                         now_fn: Callable[[], float] = time.time,
                         sleep_fn: Optional[Callable] = None,
                         scan_fn: Optional[Callable] = None
                         ) -> Dict[str, Any]:
    """Wait, read-only, for a contract this spec may bind -- across rollovers.

    THE DIFFERENCE FROM `run_continuous.await_entry_band`, which is kept and
    unchanged for the single-window launcher: that one is bounded at 420s
    because it is holding an armed workflow STEP open, and a scheduler that
    can wait forever inside a step is worse than not entering. A persistent
    runner has no step to hold. It waits across contract boundaries, which is
    the entire point, and gives up only when the venue has failed to produce
    an eligible contract for longer than two whole BTC15m periods -- at which
    point it is not waiting, it is broken.

    `exclude` is how a roll refuses to re-bind the contract just finished. A
    window can end well above its own entry band (a bound-exceeded withdrawal,
    an exhausted budget), and without this the very next poll would hand the
    same symbol straight back.

    SENDS NOTHING AND SPENDS NOTHING: one PUBLIC read per poll, and sleep.
    """
    sleep = sleep_fn if sleep_fn is not None else asyncio.sleep
    scan = scan_fn if scan_fn is not None else RC.eligible_symbol
    band = RC.parse_entry_band(spec.entry_tte_band)
    fam = spec.family_obj
    skip = {str(s) for s in exclude}
    t0 = now_fn()
    polls = 0
    seen: List[Dict[str, Any]] = []
    while True:
        polls += 1
        sel = scan(band=band, cohort=fam)
        waited = round(now_fn() - t0, 2)
        chosen = (sel.get("chosen") or {}) if sel.get("eligible") else {}
        if sel.get("eligible") and str(chosen.get("symbol")) not in skip:
            return {"found": True, "symbol": chosen.get("symbol"),
                    "tte_s": chosen.get("tte_s"), "band": list(band or ()),
                    "waited_s": waited, "polls": polls, "selection": sel,
                    "observed": seen[-8:]}
        seen.append({"waited_s": waited,
                     "excluded": (str(chosen.get("symbol")) in skip
                                  if chosen else False),
                     "tte_s": [r.get("tte_s")
                               for r in (sel.get("considered") or [])][:4]})
        if waited + poll_s > give_up_s:
            return {"found": False, "band": list(band or ()),
                    "waited_s": waited, "polls": polls, "selection": sel,
                    "observed": seen[-8:],
                    "why": ("no %s contract became eligible within %ss, which "
                            "is longer than two whole contract periods; this "
                            "is a broken venue view, not a wait"
                            % (fam.name, give_up_s))}
        await sleep(poll_s)


# ------------------------------------------------------------- the cohort

@dataclass(frozen=True)
class CohortPlan:
    """The whole authorization, in one immutable object.

    WHY `cohort_ids` IS A CHAIN AND NOT ONE LEDGER. The grant is 12 actions
    PER WINDOW and 120 across the cohort. A single shared ledger cannot
    express that: the Driver's budget is `max_actions - consumed(ledger)`, so
    on one ledger window 2 would start with whatever window 1 left behind and
    the per-window 12 would decay to nothing. Ten 12-slot cohorts, each
    chained to the last and each hashing its predecessor, gives exactly 12 per
    window, exactly 120 in total, and the provenance chain `cohort.py` was
    built for. Nothing is reset: a spent cohort is never re-entered.
    """
    auth_id: str
    spec: Any
    max_windows: int
    max_aggregate_loss_usd: str
    #: PEAK-TO-TROUGH of the settlement-implied equity curve, SETTLED windows
    #: only. The second bound, because cumulative loss alone cannot see a
    #: cohort that made 4.00 and gave it all back: that reads zero throughout
    #: while the strategy's recent behaviour is as bad as it has ever been.
    #: "0" means no drawdown stop, which is what every cohort so far has run
    #: under, so this is additive and changes nothing already authorized.
    max_drawdown_usd: str = "0"
    #: ordered, one per window. Window i binds cohort_ids[i].
    cohort_ids: Tuple[str, ...] = ()
    state_dir: str = "analysis/gemini/canary_latch"
    out_dir: str = "analysis/gemini"
    root: str = CO.COHORT_ROOT

    @property
    def head(self) -> Optional[str]:
        """Where the cohort's durable counters live: its FIRST ledger."""
        return self.cohort_ids[0] if self.cohort_ids else self.spec.budget_cohort

    def ledger_for(self, window_index: int,
                   *, root: Optional[str] = None) -> str:
        """The ledger the NEXT window spends from: the first unspent one.

        NOT `cohort_ids[window_index]`. A cohort that stopped and resumed has
        already spent some ledgers, and indexing by window number would either
        re-enter a spent one or skip a fresh one depending on which way the
        count drifted. Asking the ledgers themselves which of them still has
        room cannot drift: a spent ledger is spent, and it is never re-entered.
        """
        if not self.cohort_ids:
            return self.spec.budget_cohort
        r = root or self.root
        for cid in self.cohort_ids:
            try:
                # UNUSED, not merely "has room". A market gets a WHOLE ledger
                # or none: COHORT-1's window 4 inherited W02's remaining 4
                # after window 3 spent 8 and stopped at "no mutation slots
                # left to cancel with" after 14 cycles, and window 6 got 5.
                # The grant is twelve actions PER MARKET, so unused slots die
                # with the market that was given them and are never handed on.
                if CO.state(cid, root=r)["consumed"] == 0:
                    return cid
            except Exception:                                 # noqa: BLE001
                continue
        raise IndexError(
            "no UNUSED ledger remains in this cohort; %d were granted and a "
            "market is never given a partially spent one"
            % len(self.cohort_ids))

    def action_budget(self, *, root: Optional[str] = None) -> Dict[str, Any]:
        """The whole cohort's mutation budget, summed over its chain."""
        r = root or self.root
        rows = []
        for cid in (self.cohort_ids or ([self.spec.budget_cohort]
                                        if self.spec.budget_cohort else [])):
            try:
                st = CO.state(cid, root=r)
            except Exception as exc:                          # noqa: BLE001
                rows.append({"cohort_id": cid, "readable": False,
                             "why": "%s: %s" % (type(exc).__name__, exc)})
                continue
            rows.append({"cohort_id": cid, "readable": True,
                         "budget": st["budget"], "consumed": st["consumed"],
                         "remaining": st["remaining"],
                         "predecessor_intact":
                             (st.get("predecessor") or {}).get("intact")})
        ok = [r_ for r_ in rows if r_.get("readable")]
        fresh = [r_ for r_ in ok if r_.get("consumed") == 0]
        return {"per_window": 12, "ledgers": rows,
                # WHAT A MARKET CAN ACTUALLY BE GIVEN. `total_remaining` sums
                # partial ledgers no market may be handed, so it overstates
                # what is bindable. This is the number of whole ledgers left.
                "fresh_ledgers": len(fresh),
                "bindable_slots": 12 * len(fresh),
                "fresh_ledger_ids": [r_["cohort_id"] for r_ in fresh],
                "total_budget": sum(r_["budget"] for r_ in ok),
                "total_consumed": sum(r_["consumed"] for r_ in ok),
                "total_remaining": sum(r_["remaining"] for r_ in ok),
                "all_readable": len(ok) == len(rows) and bool(rows),
                "all_intact": all(r_.get("predecessor_intact") for r_ in ok)}

    def as_dict(self) -> Dict[str, Any]:
        return {"label": "GEMINI_COHORT_PLAN_V1", "auth_id": self.auth_id,
                "spec_id": self.spec.spec_id, "spec_sha": self.spec.spec_sha(),
                "envelope_id": self.spec.envelope_obj.name,
                "envelope_sha": self.spec.envelope_obj.sha(),
                "budget_cohort": self.head,
                "cohort_ids": list(self.cohort_ids),
                "actions_per_window": 12,
                "max_cohort_actions": 12 * len(self.cohort_ids or ()),
                "max_windows": self.max_windows,
                "max_aggregate_loss_usd": self.max_aggregate_loss_usd,
                "max_drawdown_usd": self.max_drawdown_usd,
                "quote_band": (None if self.spec.quote_band is None
                               else list(self.spec.quote_band)),
                "per_market_bounds": self.spec.bounds()}


async def settle_pending_windows(st: Dict[str, Any], resolve: Callable) -> int:
    """Convert every PENDING window the venue now reports as settled.

    READ-ONLY. It resolves contracts and re-prices lines already established;
    it binds nothing, places nothing and touches no ledger.

    WHY THIS IS A FUNCTION RATHER THAN A BLOCK IN THE LOOP. It used to be a
    block at the TOP of the loop, which meant the terminal `break` skipped it
    -- so the LAST window of every cohort was never settled, whatever the
    venue said. Probe A stopped on WINDOWS_EXHAUSTED holding five NO fills
    across two contracts that both resolved `yes`, and reported
    `implied net 0` because both windows were still PENDING. The $10
    settlement-aware loss stop reads `cumulative_economic_loss_usd`, which
    sums SETTLED windows only, so a loss that settles at or after the final
    window was structurally invisible to it.

    Returns the number of windows converted.
    """
    converted = 0
    for w in st.get("windows") or []:
        e = w.get("economics") or {}
        if e.get("state") != CE.PENDING or not w.get("symbol"):
            continue
        res = await resolve(w["symbol"])
        if str(res.get("status") or "").lower() != "settled":
            continue
        # RE-PRICED FROM THE LINES ALREADY ESTABLISHED, not re-derived.
        # The order events are long gone from the state file, so the
        # venue-confirmed fee is CARRIED FORWARD rather than recomputed
        # against an empty event list -- which would report a real fee
        # as 0.00 and quietly improve the number.
        w["economics"] = CE.window_economics(
            {"window": {"symbol": w["symbol"],
                        "inventory": {
                            "yes_qty": e.get("yes_qty"),
                            "no_qty": e.get("no_qty"),
                            "total_spend_usd": e.get("gross_fill_spend_usd"),
                            "n_fills": e.get("n_fills"),
                            "matched_qty": e.get("matched_qty"),
                            "directional_residual": e.get(
                                "directional_residual")}}},
            res,
            rebates_usd=e.get("rebates_usd"),
            fees_usd=e.get("fees_usd"),
            fee_provenance_in=e.get("fee_provenance"))
        w["settled_at_utc"] = datetime.now(timezone.utc).isoformat()
        converted += 1
    return converted


def recompute_cohort_economics(st: Dict[str, Any]) -> Dict[str, Any]:
    """Re-derive the cohort lines from the windows as they now stand."""
    st["economics"] = CE.cohort_economics(
        [w["economics"] for w in (st.get("windows") or [])
         if w.get("economics")] + list(st.get("unrecoverable") or []))
    st["aggregate_loss_usd"] = st["economics"]["cumulative_economic_loss_usd"]
    return st["economics"]


def unsettled_at_exit(st: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Windows still PENDING when the process ends, with what they risk.

    A window that never settled is NOT a zero. Reporting it as one is how a
    real loss reads as `implied net 0`, so each is named here with the
    spend that is still exposed to a settlement nobody has read.
    """
    out: List[Dict[str, Any]] = []
    for w in st.get("windows") or []:
        e = w.get("economics") or {}
        if e.get("state") != CE.PENDING:
            continue
        out.append({
            "symbol": w.get("symbol"),
            "cohort_id": w.get("cohort_id"),
            "gross_fill_spend_usd": e.get("gross_fill_spend_usd"),
            "n_fills": e.get("n_fills"),
            "yes_qty": e.get("yes_qty"), "no_qty": e.get("no_qty"),
            "why": ("still PENDING when the process ended. Its payout is "
                    "UNKNOWN, it contributes NOTHING to "
                    "cumulative_economic_loss_usd, and the gross spend above "
                    "is the amount whose settlement has not been read. This "
                    "is the absence of evidence, never a zero.")})
    return out


async def run_cohort(plan: CohortPlan, *,
                     readiness: bool = False,
                     latch_fn: Optional[Callable] = None,
                     readiness_fn: Optional[Callable] = None,
                     window_fn: Optional[Callable] = None,
                     discover_fn: Optional[Callable] = None,
                     gate_fn: Optional[Callable] = None,
                     resolution_fn: Optional[Callable] = None,
                     status_writer: Optional["LS.StatusWriter"] = None,
                     now_fn: Callable[[], float] = time.time,
                     ) -> Dict[str, Any]:
    """ONE process, up to `max_windows` consecutive contracts, then STOP.

    THE STOPS, and every one of them is terminal for the cohort:

      * static readiness failed                  -- nothing is bound;
      * the window budget is exhausted           -- the authorized count;
      * aggregate reconstructed loss >= the cap  -- checked AFTER every
        window and BEFORE binding the next, so the cap cannot be crossed by
        a window that was already running;
      * the cohort ledger has no mutation slots left;
      * the rollover gate could not establish order or position state;
      * discovery gave up.

    Nothing here re-arms, widens a bound, tunes a parameter between windows
    or requires a trade to occur. A contract that produced no fill is an
    observed window and the cohort moves on.
    """
    spec = plan.spec
    rep: Dict[str, Any] = {
        "label": LABEL, "started_utc": datetime.now(timezone.utc).isoformat(),
        "plan": plan.as_dict(), "readiness_mode": bool(readiness),
        "windows": [], "stops": [],
    }

    ready = await (readiness_fn or static_readiness)(
        spec, auth_id=plan.auth_id, root=plan.root,
        chain=plan.cohort_ids)
    rep["static_readiness"] = ready
    if not ready.get("ok"):
        rep["state"] = "STOPPED_NOT_READY"
        rep["why"] = "; ".join(ready.get("why_not") or ["not ready"])
        rep["finished_utc"] = datetime.now(timezone.utc).isoformat()
        return rep

    st = load_state(plan.head, root=plan.root,
                    max_windows=plan.max_windows,
                    max_loss_usd=plan.max_aggregate_loss_usd)
    if st.get("stopped"):
        rep["state"] = "STOPPED_ALREADY"
        rep["why"] = ("this cohort is already stopped: %s. A stopped cohort "
                      "is not resumed automatically." % st.get("stop_why"))
        rep["cohort_state"] = st
        rep["finished_utc"] = datetime.now(timezone.utc).isoformat()
        return rep

    # THE LATCH, ONCE, FOR THE WHOLE COHORT. Spent durably before the FIRST
    # mutation of the FIRST window, exactly as a single-window run spends it
    # before its own. Windows then record which latch covers them rather than
    # each pretending to spend one.
    latch_rec: Optional[Dict[str, Any]] = None
    if not readiness:
        latch_rec = await _consume_cohort_latch(plan, st, latch_fn=latch_fn)
        rep["latch"] = latch_rec
        if not latch_rec.get("ok"):
            rep["state"] = "STOPPED_NO_LATCH"
            rep["why"] = latch_rec.get("why")
            rep["finished_utc"] = datetime.now(timezone.utc).isoformat()
            return rep

    run_window = window_fn or _run_window
    discover = discover_fn or await_contract
    gate = gate_fn or rollover_gate
    resolve = resolution_fn or resolution_for

    # THE DURABLE LIVE STATUS. Written as the cohort runs rather than when the
    # job ends, because COHORT-1's window 4 could not be described by anything
    # on disk while it was happening. Observability only: it is fed from rows
    # the DecisionObserver has already completed, makes no venue read, and
    # cannot sit between a decision and its order.
    status = (status_writer if status_writer is not None else LS.StatusWriter(
        os.path.join(plan.out_dir, "cohort_live_status.json"),
        cohort={"auth_id": plan.auth_id, "cohort_id": plan.head,
                "spec_id": spec.spec_id, "spec_sha": spec.spec_sha(),
                "max_windows": plan.max_windows,
                "max_aggregate_loss_usd": str(plan.max_aggregate_loss_usd),
                "max_drawdown_usd": str(plan.max_drawdown_usd),
                "actions_per_market": 12,
                "readiness_mode": bool(readiness)}))
    rep["live_status_path"] = status.path

    while True:
        if st["windows_observed"] >= plan.max_windows:
            _stop(rep, st, "WINDOWS_EXHAUSTED",
                  "the authorized %d window(s) have been observed"
                  % plan.max_windows,
                  root=plan.root, status=status)
            break
        # SETTLE WHAT HAS SETTLED, BEFORE DECIDING WHETHER TO BIND AGAIN.
        # The previous contract expires ~20-60s before the next one reaches
        # the entry band, so by the time this runs its resolution is usually
        # published. Nothing is guessed: a contract still unresolved stays
        # PENDING and stays out of the loss counter.
        await settle_pending_windows(st, resolve)
        recompute_cohort_economics(st)
        save_state(st, root=plan.root)
        status.set_cohort(
            windows_observed=st["windows_observed"],
            windows_traded=st["windows_traded"],
            cumulative_economic_loss_usd=st["aggregate_loss_usd"],
            settlement_implied_net_usd=st["economics"][
                "settlement_implied_net_usd"],
            max_drawdown_usd=st["economics"]["max_drawdown_usd"],
            gross_fill_spend_usd=st["economics"]["gross_fill_spend_usd"],
            realized_net_usd=None,
            economics=st["economics"])

        # THE ONLY FIELD THE STOP READS, and it sums SETTLED windows only.
        # THE DRAWDOWN BOUND, CHECKED FIRST because it is the tighter
        # statement: a cohort can be up on the run and still be bleeding now.
        # Disabled at "0", which is how every cohort before this one ran.
        dd_cap = D(str(plan.max_drawdown_usd or "0"))
        dd = D(str((st.get("economics") or {}).get("max_drawdown_usd") or "0"))
        if dd_cap > 0 and dd >= dd_cap:
            _stop(rep, st, "DRAWDOWN",
                  "peak-to-trough drawdown %s has reached the cohort cap of "
                  "%s. Settlement-implied equity over SETTLED windows only; "
                  "peak was %s. This is not a turnover measure."
                  % (dd, dd_cap,
                     (st.get("economics") or {}).get("equity_peak_usd")),
                  root=plan.root, status=status)
            break
        loss = D(str(st.get("aggregate_loss_usd") or "0"))
        cap = D(str(plan.max_aggregate_loss_usd))
        if loss >= cap:
            _stop(rep, st, "AGGREGATE_LOSS",
                  "cumulative ECONOMIC loss %s has reached the cohort cap of "
                  "%s. Settlement-inclusive over SETTLED windows only "
                  "(spend + confirmed fees - payout - credited rebates); "
                  "gross turnover is %s and is NOT what this measures"
                  % (loss, cap,
                     (st.get("economics") or {}).get("gross_fill_spend_usd")),
                  root=plan.root, status=status)
            break
        # THE WHOLE CHAIN, RE-READ EVERY WINDOW. Unreadable or tampered is a
        # stop, not a warning: a ledger that cannot be read is not a ledger
        # with room.
        budget = plan.action_budget()
        rep["action_budget"] = budget
        if not budget["all_readable"] or not budget["all_intact"]:
            _stop(rep, st, "LEDGER_UNREADABLE",
                  "the cohort's ledger chain is not readable and intact: %s"
                  % json.dumps(budget["ledgers"], default=str)[:400],
                  root=plan.root, status=status)
            break
        # BINDABLE, NOT REMAINING. `total_remaining` sums the leftovers of
        # ledgers that markets have already been given, and no market may be
        # handed a partial one, so it overstates what can actually be bound.
        # The question is how many WHOLE ledgers are left.
        if budget["bindable_slots"] <= 0:
            _stop(rep, st, "MUTATION_BUDGET",
                  "no unused 12-action ledger remains (%s of %s ledgers "
                  "fresh; %s/%s slots consumed, and a market is never given "
                  "another market's remainder)"
                  % (budget["fresh_ledgers"], len(budget["ledgers"]),
                     budget["total_consumed"], budget["total_budget"]),
                  root=plan.root, status=status)
            break
        try:
            window_cohort = plan.ledger_for(st["windows_observed"],
                                            root=plan.root)
        except IndexError as exc:
            _stop(rep, st, "MUTATION_BUDGET", str(exc),
                  root=plan.root, status=status)
            break

        found = await discover(spec, exclude=st.get("symbols") or ())
        rep["windows"].append({"discovery": found})
        if not found.get("found"):
            _stop(rep, st, "NO_CONTRACT", found.get("why"),
                  root=plan.root, status=status)
            break
        symbol = found["symbol"]

        status.begin_window(window_index=st["windows_observed"] + 1,
                            symbol=symbol, cohort_id=window_cohort,
                            tte_at_bind_s=found.get("tte_s"),
                            actions_granted=12)
        wrep = await run_window(plan, symbol, readiness=readiness,
                                latch_held_by=latch_rec,
                                cohort_id=window_cohort, status=status)
        # THE CONTRACT HAS NOT SETTLED YET. A window ends with seconds to
        # spare; resolution lands at expiry. So this reads the resolution
        # anyway -- it will usually be PENDING -- and the settle pass at the
        # top of the next iteration converts it once the venue publishes. A
        # pending window contributes NOTHING to the loss counter, and its
        # risk stays where it has always been: the per-market committed,
        # residual and exposure gates inside the driver.
        econ = window_loss_usd(wrep, await resolve(symbol))
        placement = spread_placement(wrep)
        st["windows_observed"] += 1
        st["symbols"] = list(st.get("symbols") or []) + [symbol]
        if (econ.get("n_fills") or 0):
            st["windows_traded"] += 1
        # THE BINDING, RECORDED WITH WHAT IT COST. A market is granted one
        # whole ledger; whatever it did not spend is FORFEIT and dies here,
        # so the record says so explicitly rather than leaving a later reader
        # to infer it from a remaining count that nothing will ever draw on.
        try:
            _lst = CO.state(window_cohort, root=plan.root)
            binding = {"cohort_id": window_cohort, "symbol": symbol,
                       "granted": _lst["budget"],
                       "consumed": _lst["consumed"],
                       "forfeited": _lst["remaining"],
                       "note": ("one whole ledger per market; the unspent "
                                "slots die with this market and are never "
                                "inherited by the next one")}
        except Exception as exc:                              # noqa: BLE001
            binding = {"cohort_id": window_cohort, "symbol": symbol,
                       "readable": False,
                       "why": "%s: %s" % (type(exc).__name__, exc)}
        st["windows"] = list(st.get("windows") or []) + [{
            "symbol": symbol, "cohort_id": window_cohort,
            "ledger_binding": binding,
            "utc": datetime.now(timezone.utc).isoformat(),
            "state": wrep.get("state"),
            "stop_why": (wrep.get("window") or {}).get("stop_why")
                        or wrep.get("why"),
            "orders_live_after_stop":
                (wrep.get("window") or {}).get("orders_live_after_stop"),
            "economics": econ,
            "placement_rows": len(placement),
        }]
        st["economics"] = CE.cohort_economics(
            [w["economics"] for w in st["windows"] if w.get("economics")]
            + list(st.get("unrecoverable") or []))
        st["aggregate_loss_usd"] = st["economics"][
            "cumulative_economic_loss_usd"]
        save_state(st, root=plan.root)
        status.end_window(
            stop_reason=((wrep.get("window") or {}).get("stop_why")
                         or wrep.get("why") or wrep.get("state")),
            # IMPLIED, and null while the contract is unresolved. It is never
            # guessed from an unsettled market and it is never REALIZED.
            settlement_implied_net_usd=econ.get("settlement_implied_net_usd"),
            economics=econ)
        status.set_cohort(
            windows_observed=st["windows_observed"],
            windows_traded=st["windows_traded"],
            cumulative_economic_loss_usd=st["aggregate_loss_usd"],
            settlement_implied_net_usd=st["economics"][
                "settlement_implied_net_usd"],
            max_drawdown_usd=st["economics"]["max_drawdown_usd"],
            gross_fill_spend_usd=st["economics"]["gross_fill_spend_usd"],
            realized_net_usd=None, economics=st["economics"])
        rep["windows"][-1].update({"report": wrep, "economics": econ,
                                   "spread_placement": placement})

        g = await gate(symbol)
        rep["windows"][-1]["rollover_gate"] = g
        if not g.get("ok"):
            _stop(rep, st, "ROLLOVER_REFUSED", g.get("why"),
                  root=plan.root, status=status)
            break

    # ==================================================================
    # THE TERMINAL SETTLEMENT SWEEP. Every exit from the loop above is a
    # `break`, and the settle pass sits at the TOP of the loop -- so before
    # this existed, the LAST window of every cohort ended PENDING no matter
    # what the venue said, and a loss that settled at or after it could not
    # reach `cumulative_economic_loss_usd`, which is the only field the
    # $10 stop reads. That is not a reporting nicety: it is the stop being
    # unable to see the loss it exists to bound.
    #
    # READ-ONLY, AND IT REVERSES NOTHING. The cohort has already stopped and
    # this does not rewrite why: `rep["state"]` and the stop record stand as
    # they were. What it does is finish the accounting, persist it, and say
    # plainly whether the settled total would have breached the cap -- so a
    # resume refuses, and so a human is never handed `implied net 0` for a
    # cohort that lost money.
    # ==================================================================
    terminal: Dict[str, Any] = {"ran": False}
    try:
        converted = await settle_pending_windows(st, resolve)
        econ = recompute_cohort_economics(st)
        loss = D(str(st.get("aggregate_loss_usd") or "0"))
        cap = D(str(plan.max_aggregate_loss_usd))
        dd = D(str(econ.get("max_drawdown_usd") or "0"))
        dd_cap = D(str(plan.max_drawdown_usd or "0"))
        pending = unsettled_at_exit(st)
        terminal = {
            "ran": True,
            "windows_settled_by_this_sweep": converted,
            "cumulative_economic_loss_usd": str(loss),
            "max_aggregate_loss_usd": str(cap),
            "loss_cap_breached_after_settlement": bool(loss >= cap),
            "max_drawdown_usd": str(dd),
            "max_drawdown_cap_usd": str(dd_cap),
            "drawdown_cap_breached_after_settlement": bool(dd_cap > 0
                                                           and dd >= dd_cap),
            "settlement_complete": not pending,
            "unsettled_at_exit": pending,
            # THE BLIND SPOT, SIZED RATHER THAN ARGUED ABOUT. A process that
            # has ended cannot observe a settlement that has not happened,
            # so the honest statement is not "the stop sees everything" but
            # "here is the most the stop could still be understating". It
            # assumes every unsettled contract pays nothing, which is the
            # worst case, and it GATES NOTHING: no stop reads it.
            "worst_case_loss_if_unsettled_lose_usd": str(
                loss + sum((D(str(w.get("gross_fill_spend_usd") or "0"))
                            for w in pending), D("0"))),
            "labels": ("settlement-IMPLIED over SETTLED windows only, never "
                       "REALIZED: a realized figure still requires an "
                       "attributable cash movement read back from the "
                       "account. A non-empty unsettled_at_exit means the "
                       "figures above are INCOMPLETE, not that the rest was "
                       "zero."),
        }
        save_state(st, root=plan.root)
        if status is not None:
            try:
                status.set_cohort(
                    cumulative_economic_loss_usd=st["aggregate_loss_usd"],
                    settlement_implied_net_usd=econ[
                        "settlement_implied_net_usd"],
                    max_drawdown_usd=econ["max_drawdown_usd"],
                    gross_fill_spend_usd=econ["gross_fill_spend_usd"],
                    realized_net_usd=None, economics=econ)
            except Exception:                                 # noqa: BLE001
                pass
    except Exception as exc:                                  # noqa: BLE001
        # A FAILED SWEEP IS REPORTED, NEVER SWALLOWED. The cohort has already
        # stopped and placed its last order, so this cannot endanger anything
        # live -- but "the accounting did not finish" must not look like
        # "the accounting finished at zero".
        terminal = {"ran": False,
                    "why": "%s: %s" % (type(exc).__name__, exc),
                    "settlement_complete": False}
    rep["terminal_settlement"] = terminal
    rep["cohort_state"] = st
    rep["finished_utc"] = datetime.now(timezone.utc).isoformat()
    return rep


def _stop(rep: Dict[str, Any], st: Dict[str, Any], code: str,
          why: Optional[str], *, root: Optional[str] = None,
          status: Optional["LS.StatusWriter"] = None) -> None:
    st["stopped"] = True
    st["stop_why"] = "%s: %s" % (code, why)
    rep["stops"].append({"code": code, "why": why})
    rep["state"] = "STOPPED_" + code
    rep["why"] = why
    try:
        # THE PLAN'S ROOT, not the module default. This defaulted, so a stop
        # under a test root wrote the cohort state into the REAL tree.
        save_state(st, root=root) if root else save_state(st)
    except OSError:
        pass
    if status is not None:
        try:
            status.set_cohort(stopped=True, stop_code="STOPPED_" + code,
                              stop_why=why)
        except Exception:                                     # noqa: BLE001
            pass


async def _consume_cohort_latch(plan: CohortPlan, st: Mapping[str, Any], *,
                                latch_fn: Optional[Callable] = None
                                ) -> Dict[str, Any]:
    from src.gemini_execution import latch as LATCH
    from src.gemini_execution.run_canary import (can_make_durable,
                                                 durable_check, make_durable)
    spec = plan.spec
    e = spec.envelope_obj
    intent = {
        "intent_id": "cohort-%s" % datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"),
        "cohort_id": spec.budget_cohort,
        "symbol": "%s (%d consecutive contracts)" % (spec.symbol_family,
                                                     plan.max_windows),
        "side": "buy", "outcome": "both", "price": None,
        "amount": str(e.action_size),
        "max_orders": int(e.max_actions),
        "max_windows": int(plan.max_windows),
        "max_aggregate_loss_usd": str(plan.max_aggregate_loss_usd),
        "max_residual": str(e.max_residual),
        "max_residual_usd": str(e.max_residual_usd),
        "max_committed_usd": str(e.max_committed_usd),
        "max_working_orders": int(e.max_working_orders),
        "envelope_name": e.name, "maker_only": True, "transport": "WEBSOCKET",
        "executable_sha": WC.executable_sha(),
        "envelope_sha": WC.envelope_sha(),
        "active_envelope_sha": e.sha(),
        "spec_sha": spec.spec_sha(),
    }
    if latch_fn is not None:
        return latch_fn(plan.auth_id, intent, plan.state_dir)

    # RESUMING A COHORT IS NOT SPENDING A SECOND ONE.
    #
    # The latch means ONE AUTHORIZATION, ONE COHORT. COHORT-1 spent its latch
    # correctly, ran two of its ten authorized windows, and stopped on an
    # authorized condition -- and resuming the SAME cohort must not look like
    # starting a new one, nor be refused as if it were. Spending a second
    # latch would be a lie about how many cohorts this grant bought.
    #
    # WHAT STILL REFUSES, and it is the thing that matters: a FRESH cohort
    # under a spent grant. Continuation is permitted only when this
    # authorization's own durable cohort state exists, is not stopped, and has
    # observed FEWER windows than it was granted. That state is append-only in
    # the direction that counts -- `windows_observed` only ever rises and
    # `max_windows` bounds it -- and every ledger in the chain is single-use,
    # so a continuation can no more exceed the grant than the original run
    # could. A cohort with no state, or one already at its window count, is
    # refused exactly as before.
    if LATCH.consumed(plan.auth_id, plan.state_dir):
        observed = int(st.get("windows_observed") or 0)
        remaining = int(plan.max_windows) - observed
        if st.get("stopped"):
            return {"ok": False, "consumed": False,
                    "why": ("authorization %s is spent and its cohort is "
                            "STOPPED (%s); a stopped cohort is not resumed "
                            "automatically"
                            % (plan.auth_id, st.get("stop_why")))}
        if observed <= 0 or remaining <= 0:
            return {"ok": False, "consumed": False,
                    "why": ("authorization %s is already consumed and its "
                            "cohort has %d of %d windows observed; this is "
                            "reconciliation work, never a re-run"
                            % (plan.auth_id, observed, plan.max_windows))}
        rec = LATCH.read(plan.auth_id, plan.state_dir) or {}
        if not rec:
            return {"ok": False, "consumed": False,
                    "why": ("authorization %s reads as consumed but its latch "
                            "record cannot be read; a continuation is not "
                            "built on a record we cannot see" % plan.auth_id)}
        return {"ok": True, "consumed": False, "resumed": True,
                "cohort_id": spec.budget_cohort,
                "intent_id": rec.get("intent_id"),
                "state": rec.get("state"),
                "when": "spent once, before this cohort's FIRST mutation",
                "windows_observed": observed,
                "windows_remaining": remaining,
                "why": ("CONTINUING the cohort this latch already bought: %d "
                        "of %d windows observed, %d remaining. No second "
                        "latch is spent, because no second cohort is run."
                        % (observed, plan.max_windows, remaining))}

    pushable = can_make_durable()
    if not pushable.get("ok"):
        return {"ok": False, "consumed": False,
                "why": ("the latch could not have been made durable (%s); "
                        "refused BEFORE consuming the authorization"
                        % pushable.get("why"))}
    try:
        rec = LATCH.consume(plan.auth_id, intent, plan.state_dir,
                            durable_check=durable_check)
        make_durable(plan.auth_id, rec)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "consumed": False,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "consumed": True, "cohort_id": spec.budget_cohort,
            "intent_id": rec.get("intent_id"), "state": rec.get("state"),
            "when": "BEFORE the cohort's first mutation"}


async def _run_window(plan: CohortPlan, symbol: str, *, readiness: bool,
                      latch_held_by: Optional[Mapping[str, Any]],
                      cohort_id: Optional[str] = None,
                      status: Optional["LS.StatusWriter"] = None
                      ) -> Dict[str, Any]:
    """ONE contract, through the SAME body a single-window launcher runs."""
    spec = plan.spec
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rep: Dict[str, Any] = {
        "label": RC.LABEL,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "authorization_id": plan.auth_id,
        "driver_executable_sha": WC.executable_sha(),
        "envelope_name": spec.envelope_obj.name,
        "envelope_sha": spec.envelope_obj.sha(),
        "envelope": spec.envelope_obj.as_dict(),
        "driver_envelope_sha": WC.envelope_sha(),
        "bundle_sha": BUNDLE.bundle_sha(),
        "probe_spec": spec.as_dict(),
        "spec_sha": spec.spec_sha(),
        "readiness_mode": bool(readiness),
        "mutation_boundary": ("BLOCKED (readiness)" if readiness
                              else "ENABLED"),
        "cohort_runner": {"cohort_id": cohort_id or spec.budget_cohort,
                          "symbol": symbol,
                          "actions_this_window": 12},
    }
    out = os.path.join(plan.out_dir, "cohort_window_%s.json" % stamp)
    wire = os.path.join(plan.out_dir, "cohort_wire_%s.json" % stamp)
    try:
        return await RC.run_one_window(
            rep, plan.auth_id, symbol, state_dir=plan.state_dir,
            ledger_path=CO.load(cohort_id or spec.budget_cohort,
                                root=plan.root).ledger_path,
            wire_path=wire, cohort_id=cohort_id or spec.budget_cohort,
            envelope=spec.envelope_obj, readiness=readiness,
            probe_spec=spec, latch_held_by=latch_held_by,
            status_sink=LS.sink_for(status))
    finally:
        rep.setdefault("finished_utc",
                       datetime.now(timezone.utc).isoformat())
        try:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=2, default=str)
        except OSError:
            pass


# ------------------------------------------------------------------- CLI

#: every cohort-level stop the OWNER authorized. Reaching one of these is the
#: system doing its job, so the process exits 0 and the evidence commit that
#: follows it in CI is not skipped. Anything else -- an unreadable ledger, a
#: refused rollover, a runner exception -- is an operational failure and still
#: exits nonzero.
AUTHORIZED_STOPS = (
    "STOPPED_WINDOWS_EXHAUSTED",   # the authorized count was observed
    "STOPPED_AGGREGATE_LOSS",      # the authorized cumulative dollar stop
    "STOPPED_DRAWDOWN",            # the authorized peak-to-trough stop
    "STOPPED_MUTATION_BUDGET",     # the authorized action budget
    "STOPPED_NO_CONTRACT",         # the venue produced no eligible contract
    "STOPPED_ALREADY",             # a stopped cohort is not auto-resumed
)


def _write_report(path: str, rep: Mapping[str, Any]) -> None:
    """Best effort, and never raises: a failed write must not mask the
    failure that made the report worth keeping."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dict(rep), fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:                                         # noqa: BLE001
        pass


def discover_chain(spec: Any, *, root: str = CO.COHORT_ROOT,
                   limit: int = 64) -> Tuple[str, ...]:
    """The ledger chain a spec's head names, in order: HEAD, then its siblings.

    A chain is `<stem>W01 .. <stem>Wnn`, which is how the ledgers are created.
    Resolved from what EXISTS on disk rather than from a count, so a chain
    that was granted short is short and one that was granted long is long --
    and a spec whose head is not part of a chain simply yields that head.
    """
    head = spec.budget_cohort
    if not head:
        return ()
    m = re.match(r"^(?P<stem>.*W)(?P<n>\d+)$", str(head))
    if not m:
        return (head,) if CO.exists(head, root=root) else ()
    stem, width = m.group("stem"), len(m.group("n"))
    out = []
    for i in range(1, limit + 1):
        cid = "%s%0*d" % (stem, width, i)
        if not CO.exists(cid, root=root):
            break
        out.append(cid)
    return tuple(out)



def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ONE cohort in THIS process, until a cohort-level stop.

    NOT WIRED TO AN ARMED WORKFLOW. There is no dispatch path that calls this
    with a live arm; the owner's approval of the 10-window envelope is what
    would add one. `--readiness` runs the whole orchestration with the
    mutation boundary blocked, which is how the lifecycle is exercised
    end to end without sending anything.
    """
    import argparse

    from src.gemini_execution import live_probe_spec as SPEC

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-id", required=True)
    ap.add_argument("--spec", required=True,
                    help="a DECLARED LiveProbeSpec id; never assembled from "
                         "flags, so readiness and live cannot resolve "
                         "different experiments")
    ap.add_argument("--expect-spec-sha", default="")
    ap.add_argument("--static-readiness", action="store_true",
                    help="run ONLY the TTE-independent readiness check and "
                         "exit. Sends nothing, waits for no entry band, and "
                         "binds no contract.")
    ap.add_argument("--max-windows", type=int, default=0)
    ap.add_argument("--max-loss-usd", default="0",
                    help="the AGGREGATE ECONOMIC-loss stop for the whole "
                         "cohort, in addition to every per-market bound. "
                         "Settlement-inclusive over SETTLED windows only "
                         "(spend + confirmed fees - payout - credited "
                         "rebates); it is NOT a turnover cap and an "
                         "unresolved market contributes nothing to it")
    ap.add_argument("--max-drawdown-usd", default="0",
                    help="peak-to-trough stop on the settlement-implied "
                         "equity curve, SETTLED windows only. 0 disables it, "
                         "which is how every cohort before COHORT-2 ran")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--out-dir", default="analysis/gemini")
    ap.add_argument("--root", default=CO.COHORT_ROOT)
    ap.add_argument("--out", default="")
    ap.add_argument("--readiness", action="store_true")
    a = ap.parse_args(list(argv) if argv is not None else None)

    spec = SPEC.get(a.spec)
    if a.expect_spec_sha and spec.spec_sha() != a.expect_spec_sha:
        print("REFUSED: spec %s hashes %s, expected %s"
              % (spec.spec_id, spec.spec_sha(), a.expect_spec_sha))
        return 2
    if a.static_readiness:
        # THE CHECK THAT HAS NO CLOCK. The workflow gate answered "is the
        # tree ready" only when a contract happened to be near 840:880, so
        # market timing and software correctness were one check. They are
        # not. This binds nothing and waits for nothing.
        _chain = discover_chain(spec, root=a.root)
        out = asyncio.run(static_readiness(
            spec, auth_id=a.auth_id, root=a.root, chain=_chain,
            state_dir=a.state_dir, max_windows=a.max_windows or len(_chain)))
        dest = a.out or os.path.join(
            a.out_dir, "cohort_static_readiness_%s.json"
            % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        try:
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, default=str)
        except OSError:
            pass
        print("verdict        %s" % out["verdict"])
        print("tte_independent %s   mutations_sent %s"
              % (out["tte_independent"], out["mutations_sent"]))
        for name, c in out["checks"].items():
            print("  %-22s %s" % (name, "PASS" if c.get("ok") else "FAIL"))
        for w in out["why_not"]:
            print("  why_not: %s" % w)
        print("report         %s" % dest)
        return 0 if out["ok"] else 1

    if int(a.max_windows) < 1:
        print("REFUSED: --max-windows must be at least 1 for a cohort run")
        return 2
    # THE LEDGER CHAIN, WIRED. This is the defect that cost COHORT-1 its
    # first dispatch: `cohort_ids` was added to CohortPlan, ten ledgers were
    # created and five tests covered them, and then the CLI built the plan
    # WITHOUT the field. It defaulted to (), both `ledger_for` and
    # `action_budget` fell back to the single `spec.budget_cohort`, and the
    # cohort ran two windows on ONE 12-slot ledger before stopping at 12/12 --
    # exactly the failure mode the chain exists to prevent. There is now a
    # CLI-level test that builds the plan the way main() does and asserts the
    # chain is on it.
    ids = discover_chain(spec, root=a.root)
    plan = CohortPlan(auth_id=a.auth_id, spec=spec,
                      max_windows=int(a.max_windows),
                      max_aggregate_loss_usd=str(a.max_loss_usd),
                      max_drawdown_usd=str(a.max_drawdown_usd),
                      cohort_ids=ids,
                      state_dir=a.state_dir, out_dir=a.out_dir, root=a.root)
    out = a.out or os.path.join(
        a.out_dir, "cohort_run_%s.json"
        % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))

    # EVIDENCE SURVIVES EVERY EXIT, and that is not a nicety. COHORT-1's
    # first dispatch stopped on an authorized condition, exited 1, and the
    # workflow's evidence-commit step -- conditioned on the job succeeding --
    # was skipped. Twelve mutation slots had really been spent and two windows
    # had really traded, and the append-only record of both died with the
    # runner. A report written only on the happy path is a report that is
    # absent exactly when it matters.
    rep: Dict[str, Any] = {"label": LABEL, "plan": plan.as_dict(),
                           "state": "NO_REPORT", "why": "the runner did not "
                           "return; see `runner_error`"}
    try:
        rep = asyncio.run(run_cohort(plan, readiness=bool(a.readiness)))
    except BaseException as exc:                              # noqa: BLE001
        # BaseException DELIBERATELY: a KeyboardInterrupt or a SystemExit
        # from a cancelled job must still leave the record behind.
        rep["runner_error"] = "%s: %s" % (type(exc).__name__, exc)
        rep["state"] = "STOPPED_RUNNER_ERROR"
        rep["why"] = rep["runner_error"]
        _write_report(out, rep)
        raise
    finally:
        _write_report(out, rep)
    st = rep.get("cohort_state") or {}
    print("cohort     %s" % plan.as_dict()["budget_cohort"])
    print("spec       %s  %s" % (spec.spec_id, spec.spec_sha()))
    print("quote_band %s" % (plan.as_dict()["quote_band"],))
    print("state      %s" % rep.get("state"))
    print("why        %s" % rep.get("why"))
    print("windows    observed %s / %s, traded %s"
          % (st.get("windows_observed"), plan.max_windows,
             st.get("windows_traded")))
    econ = st.get("economics") or {}
    print("loss       %s of %s  ECONOMIC, settlement-inclusive, SETTLED "
          "windows only" % (st.get("aggregate_loss_usd"),
                            plan.max_aggregate_loss_usd))
    print("           turnover %s  payout %s  fees %s  rebates %s  "
          "implied net %s"
          % (econ.get("gross_fill_spend_usd"),
             econ.get("settlement_payout_usd"), econ.get("fees_usd"),
             econ.get("rebates_credited_usd"),
             econ.get("settlement_implied_net_usd")))
    print("drawdown   %s of %s  peak-to-trough, settlement-implied equity"
          % (econ.get("max_drawdown_usd"), plan.max_drawdown_usd))
    print("           REALIZED %s  (null until an attributable cashflow is "
          "read back)" % econ.get("realized_net_usd"))
    print("report     %s" % out)
    print("live       %s" % rep.get("live_status_path"))
    # AN AUTHORIZED STOP IS AN OUTCOME, NOT A FAILURE. Returning 1 for every
    # stop but WINDOWS_EXHAUSTED turned a clean budget stop into a red step,
    # which is what skipped the evidence commit. A mechanical failure still
    # exits nonzero; the enumerated authorized stops do not.
    state = str(rep.get("state") or "")
    print("outcome    %s" % ("AUTHORIZED STOP" if state in AUTHORIZED_STOPS
                             else "OPERATIONAL FAILURE"))
    return 0 if state in AUTHORIZED_STOPS else 1


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
