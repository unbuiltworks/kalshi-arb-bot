#!/usr/bin/env python3
"""Harden staged PMUS q1 executor after first submitted->rejected probe.

No arming. No network. Patches only /home/ssm-user/l5-pmus-q1-stage.
"""
from pathlib import Path
import hashlib, os

P=Path('/home/ssm-user/l5-pmus-q1-stage/src/pmus_q1_executor.py')
s=P.read_text()

old='''class Halt(RuntimeError):\n    pass\n\n\ndef _order_status(t: Transport, order_id: str) -> dict:\n    resp = t.request("GET", f"/v1/order/{order_id}")\n    return resp.get("order") or resp\n'''
new='''class Halt(RuntimeError):\n    pass\n\n\nclass RejectedNoFill(RuntimeError):\n    \"\"\"Authoritative venue rejection with zero fill; safe terminal state.\"\"\"\n    pass\n\n\ndef _http_status(exc: Exception):\n    return getattr(exc, "code", None)\n\n\ndef _order_status(t: Transport, order_id: str) -> dict:\n    \"\"\"Read accepted order id with bounded eventual-consistency retry.\n\n    GET-only retries are safe. Never replays POST /v1/orders.\n    \"\"\"\n    last = None\n    for delay in (0.0, 0.20, 0.50, 1.0, 2.0, 3.0):\n        if delay:\n            time.sleep(delay)\n        try:\n            resp = t.request("GET", f"/v1/order/{order_id}")\n            order = resp.get("order") if isinstance(resp, Mapping) else None\n            order = order if isinstance(order, Mapping) else resp\n            state = str(order.get("state") or order.get("status") or "").upper() if isinstance(order, Mapping) else ""\n            raw_cum = order.get("cumQuantity") if isinstance(order, Mapping) else None\n            if raw_cum is None and isinstance(order, Mapping):\n                raw_cum = order.get("filledQuantity")\n            try:\n                cum = D(str(raw_cum or 0))\n            except Exception:\n                cum = D("0")\n            if "REJECT" in state and cum == 0:\n                raise RejectedNoFill(f"ORDER_REJECTED_ZERO_FILL:{order_id}")\n            return dict(order) if isinstance(order, Mapping) else {}\n        except RejectedNoFill:\n            raise\n        except Exception as exc:\n            code = _http_status(exc)\n            last = exc\n            if code not in (404, 429) and not (isinstance(code, int) and 500 <= code <= 599):\n                raise\n    raise last if last is not None else Halt("ORDER_STATUS_UNAVAILABLE")\n'''
if old not in s: raise SystemExit('OLD_STATUS_BLOCK_NOT_FOUND')
s=s.replace(old,new,1)

# Fresh post-only check immediately after preview and before the only mutation.
needle='''    if preview_order.get("marketSlug") not in (None, slug):\n        raise Halt(f"PREVIEW_MARKET_MISMATCH:{stage}")\n    persist_intent({"stage": stage, "slug": slug, "side": outcome_side,\n'''
repl='''    if preview_order.get("marketSlug") not in (None, slug):\n        raise Halt(f"PREVIEW_MARKET_MISMATCH:{stage}")\n    # Close the preview->submit race as much as possible. A post-only BUY\n    # at/above the latest ask would be marketable and should be skipped.\n    fresh = t.request("GET", f"/v1/markets/{slug}/bbo", base=GATEWAY).get("marketData") or {}\n    fresh_ask = (fresh.get("bestAsk") or {}).get("value")\n    if fresh_ask is None:\n        raise Halt(f"POST_ONLY_FRESH_ASK_MISSING:{stage}")\n    if D(str(price)) >= D(str(fresh_ask)):\n        raise Halt(f"POST_ONLY_STALE_REFUSE:{stage}:price={price}:ask={fresh_ask}")\n    persist_intent({"stage": stage, "slug": slug, "side": outcome_side,\n'''
if needle not in s: raise SystemExit('PREVIEW_NEEDLE_NOT_FOUND')
s=s.replace(needle,repl,1)

# Explicit rejected-no-fill status in run_campaign; no automatic repost.
needle2='''    except Halt as halt:\n        ledger_append({"kind": "halt", "reason": str(halt)})\n        return {**report, "status": "HALT", "reason": str(halt)}\n    except Exception as exc:\n'''
repl2='''    except RejectedNoFill as rej:\n        ledger_append({"kind": "rejected_no_fill", "reason": str(rej)})\n        return {**report, "status": "REJECTED_NO_FILL", "reason": str(rej),\n                "residual": 0}\n    except Halt as halt:\n        ledger_append({"kind": "halt", "reason": str(halt)})\n        return {**report, "status": "HALT", "reason": str(halt)}\n    except Exception as exc:\n'''
if needle2 not in s: raise SystemExit('EXCEPTION_NEEDLE_NOT_FOUND')
s=s.replace(needle2,repl2,1)

P.write_text(s); os.chmod(P,0o600)
print('PMUS_Q1_HARDENING_V3_PATCH_OK sha256='+hashlib.sha256(s.encode()).hexdigest())
