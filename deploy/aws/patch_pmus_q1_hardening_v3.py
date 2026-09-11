#!/usr/bin/env python3
"""Marker-based hardening of the staged PMUS q1 executor. No arming/network."""
from pathlib import Path
import hashlib, os

P=Path('/home/ssm-user/l5-pmus-q1-stage/src/pmus_q1_executor.py')
s=P.read_text()

def replace_between(text,start,end,repl):
    a=text.find(start)
    if a<0: raise SystemExit('START_NOT_FOUND:'+start)
    b=text.find(end,a)
    if b<0: raise SystemExit('END_NOT_FOUND:'+end)
    return text[:a]+repl.rstrip()+'\n\n'+text[b:]

if 'class RejectedNoFill' not in s:
    marker='class Halt(RuntimeError):\n    pass\n'
    if marker not in s: raise SystemExit('HALT_MARKER_NOT_FOUND')
    s=s.replace(marker,marker+'\n\nclass RejectedNoFill(RuntimeError):\n    """Authoritative venue rejection with zero fill; safe terminal state."""\n    pass\n',1)

status='''def _order_status(t: Transport, order_id: str) -> dict:
    """Bounded GET-only retry after an accepted id. Never replays POST orders."""
    last = None
    for delay in (0.0, 0.20, 0.50, 1.0, 2.0, 3.0):
        if delay:
            time.sleep(delay)
        try:
            resp = t.request("GET", f"/v1/order/{order_id}")
            order = resp.get("order") if isinstance(resp, Mapping) else None
            order = order if isinstance(order, Mapping) else resp
            state = str(order.get("state") or order.get("status") or "").upper() if isinstance(order, Mapping) else ""
            raw = order.get("cumQuantity") if isinstance(order, Mapping) else None
            if raw is None and isinstance(order, Mapping):
                raw = order.get("filledQuantity")
            try:
                cum = D(str(raw or 0))
            except Exception:
                cum = D("0")
            if "REJECT" in state and cum == 0:
                raise RejectedNoFill(f"ORDER_REJECTED_ZERO_FILL:{order_id}")
            return dict(order) if isinstance(order, Mapping) else {}
        except RejectedNoFill:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            last = exc
            if code not in (404, 429) and not (isinstance(code, int) and 500 <= code <= 599):
                raise
    raise last if last is not None else Halt("ORDER_STATUS_UNAVAILABLE")'''
s=replace_between(s,'def _order_status(', '\n\ndef authoritative_fill', status)

if 'POST_ONLY_STALE_REFUSE' not in s:
    anchor='        raise Halt(f"PREVIEW_MARKET_MISMATCH:{stage}")\n'
    if anchor not in s: raise SystemExit('PREVIEW_ANCHOR_NOT_FOUND')
    add='''        raise Halt(f"PREVIEW_MARKET_MISMATCH:{stage}")
    # Re-read the book immediately before mutation. Refuse a maker BUY that
    # has become marketable during the preview->submit race.
    fresh = t.request("GET", f"/v1/markets/{slug}/bbo", base=GATEWAY).get("marketData") or {}
    fresh_ask = (fresh.get("bestAsk") or {}).get("value")
    if fresh_ask is None:
        raise Halt(f"POST_ONLY_FRESH_ASK_MISSING:{stage}")
    if D(str(price)) >= D(str(fresh_ask)):
        raise Halt(f"POST_ONLY_STALE_REFUSE:{stage}:price={price}:ask={fresh_ask}")
'''
    s=s.replace(anchor,add,1)

if '"status": "REJECTED_NO_FILL"' not in s:
    anchor='''    except Halt as halt:
        ledger_append({"kind": "halt", "reason": str(halt)})
        return {**report, "status": "HALT", "reason": str(halt)}
'''
    if anchor not in s: raise SystemExit('HALT_CATCH_ANCHOR_NOT_FOUND')
    add='''    except RejectedNoFill as rej:
        ledger_append({"kind": "rejected_no_fill", "reason": str(rej)})
        return {**report, "status": "REJECTED_NO_FILL", "reason": str(rej),
                "residual": 0}
'''+anchor
    s=s.replace(anchor,add,1)

P.write_text(s); os.chmod(P,0o600)
print('PMUS_Q1_HARDENING_V3_PATCH_OK sha256='+hashlib.sha256(s.encode()).hexdigest())
