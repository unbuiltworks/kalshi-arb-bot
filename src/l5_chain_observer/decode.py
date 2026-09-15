"""Decode L5's exchange logs into (token, price, size) — verified, not assumed.

WHY DECODE AT ALL. Joining chain and feed on `transactionHash` proves both saw
the same transaction. It does not prove they agree about WHAT happened in it,
and if they disagree about the size of a fill then the inventory the engine
builds is wrong no matter how early it learned. Gate G-C4 is that check, and
this is what makes it computable.

THE LAYOUT WAS ESTABLISHED EMPIRICALLY, AGAINST THE FEED, not from a
remembered ABI. On a live L5 fill (0xd9ada3d2…, 2026-09-15) the exchange log
carrying L5 at topic position 2 held seven data words:

    w2 = 45_600_000   (USDC, 6 dp)  = $45.60
    w3 = 80_000_000   (shares, 6 dp) = 80 shares
    w2 / w3           = 0.57         <- exactly the feed's price
    w1                               = the CTF token id L5 received

Checked across 14 consecutive fills: size agreed 14/14 and price 12/14. Both
apparent price misses were transactions carrying TWO fills at adjacent prices
(80 @ 0.44 and 80 @ 0.43), where the disagreement was in how the FEED side was
aggregated, not in this decode. With both legs summed the two sources agree
exactly.

SO A TRANSACTION IS NOT A FILL, and this module never assumes it is. It returns
every leg it finds, and the caller aggregates. Collapsing them is precisely the
bug that made the two sources look like they disagreed.

STILL A DECODE, NOT A CERTIFICATE. The 99.5% agreement threshold in the
preregistered gate exists because this mapping is inferred from observation; it
is measured continuously rather than trusted.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional

# both amount fields are 6-decimal fixed point, confirmed against the feed's
# own float size on every sampled fill
SCALE = D(10) ** 6
MIN_WORDS = 4
# ONLY SIGNATURES WHOSE LAYOUT HAS BEEN VERIFIED AGAINST THE FEED. Measured
# 2026-09-15 over 18 consecutive joined transactions, every L5-bearing log on
# either exchange contract:
#
#   0xd543adfd  n=18  7 words  L5@2  on 0xe1111800  -> 18/18 priced within a
#                                                      cent of the feed
#   0xc3d58168  n=18  2 words  L5@3  on 0x4d97dcd9  -> ERC1155 TransferSingle,
#                                                      no fill layout at all
#
# A THIRD SIGNATURE IS WHY THIS LIST EXISTS. 0x4a39dc06 (TransferBatch) has
# enough data words to pass a length check but encodes DYNAMIC ARRAYS, so
# reading w2/w3 as amounts produced a size of 2.5e70 -- and the classifier
# duly reported "a decodable L5 fill the venue has not published", the gate's
# most serious verdict, about a token transfer. Decoding is now restricted to
# signatures measured against the feed; everything else is declined by name.
DECODABLE_EVENTS = ("0xd543adfd",)
# defence in depth, independent of the allowlist: a fill has a probability for
# a price and a countable number of shares. A layout that is not what we think
# it is produces neither.
MAX_SANE_SIZE = D(10) ** 9


def decode_log(log: Dict[str, Any], *, l5_topic: str) -> Optional[Dict[str, Any]]:
    """One exchange log -> one fill leg, or None if it is not one.

    `l5_topic` is the address padded to 32 bytes; its POSITION in the topic
    list is retained because it distinguishes L5's role in the trade and is
    part of the provenance the gate requires.
    """
    topics = [str(t).lower() for t in (log.get("topics") or [])]
    pos = [i for i, t in enumerate(topics) if t == l5_topic.lower()]
    if not pos:
        return None
    event = topics[0][:10] if topics else None
    if event not in DECODABLE_EVENTS:
        # NOT a silent drop: the caller is told which signature was declined,
        # so an unlisted event that starts carrying fills becomes visible
        # rather than becoming an absence.
        return None
    data = str(log.get("data") or "")[2:]
    if len(data) < 64 * MIN_WORDS:
        return None
    words = [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]
    maker_amt, taker_amt = D(words[2]), D(words[3])
    if taker_amt <= 0:
        return None            # not a fill leg: nothing was received
    price = maker_amt / taker_amt
    if not (D("0") < price <= D("1")) or taker_amt / SCALE > MAX_SANE_SIZE:
        return None            # the layout is not what we think it is
    return {
        "token_id": str(words[1]),
        "maker_amount": str(maker_amt / SCALE),
        "size": str(taker_amt / SCALE),
        "price": str(price.quantize(D("0.000001"))),
        # --- provenance, retained per gate G-C8
        "tx": log.get("transactionHash"),
        "block": (int(log["blockNumber"], 16)
                  if log.get("blockNumber") else None),
        "log_index": (int(log["logIndex"], 16)
                      if log.get("logIndex") else None),
        "contract": str(log.get("address") or "").lower(),
        "l5_topic_pos": pos[0],
        "event": event,
        "n_words": len(words),
    }


def decode_receipt(receipt: Dict[str, Any], *, l5_topic: str,
                   exchanges: Any) -> List[Dict[str, Any]]:
    """EVERY fill leg in a transaction. A transaction may hold several."""
    ex = {str(a).lower() for a in exchanges}
    out = []
    for lg in (receipt or {}).get("logs") or []:
        if str(lg.get("address") or "").lower() not in ex:
            continue
        leg = decode_log(lg, l5_topic=l5_topic)
        if leg:
            out.append(leg)
    return out


def declined(receipt: Dict[str, Any], *, l5_topic: str,
             exchanges: Any) -> List[Dict[str, Any]]:
    """L5-bearing exchange logs this module would not decode, and why.

    An absence must be attributable. Without this, an event signature that
    began carrying fills would simply stop appearing, and a missing fill is
    indistinguishable from L5 not trading.
    """
    ex = {str(a).lower() for a in exchanges}
    out = []
    for lg in (receipt or {}).get("logs") or []:
        if str(lg.get("address") or "").lower() not in ex:
            continue
        topics = [str(t).lower() for t in (lg.get("topics") or [])]
        if l5_topic.lower() not in topics:
            continue
        if decode_log(lg, l5_topic=l5_topic) is not None:
            continue
        ev = topics[0][:10] if topics else None
        out.append({
            "event": ev,
            "contract": str(lg.get("address") or "").lower(),
            "l5_topic_pos": topics.index(l5_topic.lower()),
            "data_words": len(str(lg.get("data") or "")[2:]) // 64,
            "reason": ("event signature not in the verified allowlist"
                       if ev not in DECODABLE_EVENTS
                       else "allowlisted signature failed the layout checks"),
        })
    return out


def aggregate(legs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Size-weighted aggregate over legs that belong together. See groups()."""
    if not legs:
        return None
    size = sum((D(l["size"]) for l in legs), D("0"))
    notional = sum((D(l["maker_amount"]) for l in legs), D("0"))
    if size <= 0:
        return None
    return {
        "n_legs": len(legs),
        "size": str(size),
        "notional": str(notional),
        "price": str((notional / size).quantize(D("0.000001"))),
        "token_ids": sorted({l["token_id"] for l in legs}),
        "event": legs[0].get("event"),
        "l5_topic_pos": legs[0].get("l5_topic_pos"),
        "blocks": sorted({l["block"] for l in legs if l["block"] is not None}),
        "log_indexes": sorted(l["log_index"] for l in legs
                              if l["log_index"] is not None),
    }


def groups(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate per (event, L5 role, token) -- NOT across all of them.

    SUMMING EVERYTHING WAS WRONG, and the mismatches said so. One live
    transaction (0x15d925d6…) carried:

        event d543adfd  L5@3 (MAKER)  token 7208…  27 + 53 shares @ 0.95
        event d543adfd  L5@2 (TAKER)  token 2007…  80 shares       @ 0.05
        event 174b3811  L5@2          token 2007…  80 shares       @ 0.05

    The feed reported 80 @ 0.05 -- exactly the taker leg. Summed blindly the
    chain said 240 @ 0.35, a mismatch that was entirely mine.

    So the TOPIC POSITION encodes L5's ROLE (2 taker, 3 maker), a second event
    type restates the same fill, and legs on different tokens are different
    trades. Each combination is aggregated separately and the caller decides
    which one the feed is talking about; nothing is silently merged.
    """
    buckets: Dict[tuple, List[Dict[str, Any]]] = {}
    for l in legs:
        key = (l.get("event"), l.get("l5_topic_pos"), l.get("token_id"))
        buckets.setdefault(key, []).append(l)
    out = []
    for key, ls in buckets.items():
        agg = aggregate(ls)
        if agg:
            agg["group_key"] = {"event": key[0], "l5_topic_pos": key[1],
                                "token_id": key[2]}
            out.append(agg)
    # largest first: the leg a feed row most likely refers to
    out.sort(key=lambda a: -D(a["size"]))
    return out


def best_match(groups_: List[Dict[str, Any]], *, price: Any, size: Any,
               tol: str = "0.000001") -> Optional[Dict[str, Any]]:
    """The group matching a feed aggregate EXACTLY, or None.

    Exact on both price and size: a decode that is merely close is a decode
    that is wrong, and the gate's whole purpose is to catch that.
    """
    try:
        px, sz, t = D(str(price)), D(str(size)), D(tol)
    except Exception:                                         # noqa: BLE001
        return None
    for g in groups_:
        if abs(D(g["price"]) - px) <= t and abs(D(g["size"]) - sz) <= t:
            return g
    return None
