"""A chain fill is provisional until it is deep. Gate G-C5.

WHY THIS EXISTS AND THE DEPTH FILTER DOES NOT SUFFICE. Refusing to count a
fill until it is 64 blocks deep stops us acting on an unconfirmed one. It does
not tell us what happened to the ones we already saw. The whole value being
claimed for the chain is that it knows ~15 s earlier than the venue -- which
means reading at the head, where a reorg can take a transaction back.

Silence is the failure mode. A retracted fill that is quietly dropped leaves
inventory the engine believes in with no event to contradict it, and a
retracted fill quietly kept is worse. Every transition is therefore a RECORD:
`chain_final`, `chain_retraction`, `chain_reblocked`.

`(block, blockHash)` IS THE IDENTITY, not the transaction hash. A reorg that
re-includes the same transaction in a different block is still a reorg, and a
tracker keyed on hash alone would see nothing happen.

An unreadable receipt is NOT a retraction. "The chain no longer has it" and "we
could not ask" are different claims, and conflating them would invent reorgs
out of network blips.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence

CONFIRMATIONS = 64          # preregistered; see PROMOTION_GATE_PREREGISTERED.md


def observe(state: Dict[str, dict], fills: Dict[str, dict], head: int,
            *, now: Optional[float] = None) -> List[dict]:
    """Start tracking chain fills that are not yet final.

    Returns EVENTS, in the same shape as recheck's, because they share an
    append-only audit file and a record with no `kind` is a record nobody can
    filter. Seeing a fill for the first time is itself the marker that makes a
    later retraction attributable.
    """
    t = time.time() if now is None else now
    out = []
    for tx, f in (fills or {}).items():
        if tx in state or f.get("block") is None:
            continue
        state[tx] = {"tx": tx, "block": int(f["block"]),
                     "block_hash": f.get("block_hash"),
                     "first_seen_t": t, "status": "provisional",
                     "first_seen_head": head}
        out.append({"kind": "chain_provisional", "tx": tx, "t": t,
                    "block": state[tx]["block"],
                    "block_hash": state[tx]["block_hash"],
                    "confirmations": head - state[tx]["block"]})
    return out


def recheck(state: Dict[str, dict], head: int,
            receipt_fn: Callable[[str], Any],
            *, now: Optional[float] = None,
            confirmations: int = CONFIRMATIONS) -> Dict[str, Any]:
    """Re-ask the chain about every provisional transaction.

    A transaction the chain no longer carries is RETRACTED. One carried in a
    different block is RE-BLOCKED -- still a reorg, still recorded, and its
    confirmation count restarts from the new block.
    """
    t = time.time() if now is None else now
    events: List[dict] = []
    for tx, row in list(state.items()):
        if row["status"] != "provisional":
            continue
        try:
            rc = receipt_fn(tx)
        except Exception as exc:                              # noqa: BLE001
            # NOT a retraction. Unasked is not answered.
            row["last_check_error"] = f"{type(exc).__name__}: {exc}"[:160]
            continue
        row.pop("last_check_error", None)
        if not rc or rc.get("blockNumber") is None:
            row["status"] = "retracted"
            events.append({"kind": "chain_retraction", "tx": tx, "t": t,
                           "block": row["block"],
                           "block_hash": row.get("block_hash"),
                           "why": ("the chain no longer carries this "
                                   "transaction; it was seen at "
                                   f"block {row['block']} and is gone")})
            continue
        blk = _int(rc.get("blockNumber"))
        bh = rc.get("blockHash")
        if blk != row["block"] or (row.get("block_hash")
                                   and bh != row["block_hash"]):
            events.append({"kind": "chain_reblocked", "tx": tx, "t": t,
                           "block": row["block"], "new_block": blk,
                           "block_hash": row.get("block_hash"),
                           "new_block_hash": bh,
                           "why": ("re-included in a different block: still a "
                                   "reorg, and the confirmation count "
                                   "restarts")})
            row["block"], row["block_hash"] = blk, bh
            continue
        if head - blk >= confirmations:
            row["status"] = "final"
            events.append({"kind": "chain_final", "tx": tx, "t": t,
                           "block": blk, "block_hash": bh,
                           "confirmations": head - blk})
    prov = [r for r in state.values() if r["status"] == "provisional"]
    return {
        "events": events,
        "provisional": len(prov),
        "final": sum(1 for r in state.values() if r["status"] == "final"),
        "retracted": sum(1 for r in state.values()
                         if r["status"] == "retracted"),
        "unreadable": sum(1 for r in prov if r.get("last_check_error")),
        "confirmations_required": confirmations,
    }


def countable(state: Dict[str, dict]) -> List[str]:
    """Transactions that may be used as evidence: final, and never retracted."""
    return sorted(tx for tx, r in state.items() if r["status"] == "final")


def retracted(state: Dict[str, dict]) -> List[str]:
    return sorted(tx for tx, r in state.items() if r["status"] == "retracted")


def self_test() -> Dict[str, Any]:
    """Simulate a reorg against this module and report what it did.

    The preregistered gate requires the test to EXIST AND PASS, so the gate
    runs this rather than trusting a boolean. Deterministic, in-process, no
    network: a block range re-scanned to different content must produce a
    `chain_retraction` and remove the fill from countable evidence.
    """
    state: Dict[str, dict] = {}
    head = 1000
    observe(state, {"0xgone": {"block": 990}, "0xmoved": {"block": 991,
                                                          "block_hash": "0xA"},
                    "0xstays": {"block": 900, "block_hash": "0xB"}}, head)
    before = len(state)

    chain = {"0xmoved": {"blockNumber": 995, "blockHash": "0xC"},
             "0xstays": {"blockNumber": 900, "blockHash": "0xB"}}
    r = recheck(state, head, lambda tx: chain.get(tx))
    kinds = [e["kind"] for e in r["events"]]

    # and an unreadable receipt must NOT look like a retraction
    state2: Dict[str, dict] = {}
    observe(state2, {"0xblip": {"block": 900}}, head)

    def boom(_tx):
        raise RuntimeError("timeout")

    r2 = recheck(state2, head, boom)

    ok = (before == 3
          and "chain_retraction" in kinds
          and "chain_reblocked" in kinds
          and "0xgone" not in countable(state)
          and "0xmoved" not in countable(state)     # its depth restarted
          and "0xstays" in countable(state)
          and retracted(state) == ["0xgone"]
          and r2["retracted"] == 0 and r2["unreadable"] == 1)
    return {
        "passed": bool(ok),
        "events": kinds,
        "countable": countable(state),
        "retracted": retracted(state),
        "unreadable_is_not_retracted": r2["retracted"] == 0,
        "what_it_proves": ("a block range re-scanned to different content "
                           "produces a chain_retraction and removes the fill "
                           "from countable evidence; an unreadable receipt "
                           "does not"),
    }


def _int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v), 16) if str(v).startswith("0x") else int(v)
    except ValueError:
        return None
