"""Fail-closed health guard for the persistent Gemini prediction trading socket."""
from __future__ import annotations
import asyncio
from decimal import Decimal
import time
from typing import Any
from src import gemini_kalshi_probe_transport as transport
D=Decimal; _INSTALLED=False; _ORIGINAL_PLACE_FOK=transport.GeminiTradingSession.place_fok

async def pre_send_socket_healthy(session: transport.GeminiTradingSession, *, timeout: float=0.5)->bool:
    ws=getattr(session,"ws",None); reader=getattr(session,"_reader",None)
    if ws is None or reader is None or reader.done(): return False
    try:
        pong=await ws.ping(); await asyncio.wait_for(pong,timeout=timeout); return True
    except Exception: return False

def _zero_result(params:dict[str,Any],*,reason:str,sent_ns:int,status:str="CANCELED")->transport.ExecutionResult:
    done=time.perf_counter_ns(); return transport.ExecutionResult(venue="gemini",client_order_id=str(params["clientOrderId"]),filled_quantity="0",average_fill_price=None,fee_paid=None,status=status,reason=reason,local_send_ns=sent_ns,local_done_ns=done,exchange_ts_ns=None,request_latency_ms=(done-sent_ns)/1_000_000,ack_latency_ms=None,raw_request_status=None,reconciled=False)

def _rest_row_result(params:dict[str,Any],row:dict[str,Any],*,sent_ns:int)->transport.ExecutionResult:
    done=time.perf_counter_ns(); qty=D(str(row.get("filledQuantity") or "0")); avg=row.get("avgExecutionPrice")
    return transport.ExecutionResult(venue="gemini",client_order_id=str(params["clientOrderId"]),filled_quantity=str(qty),average_fill_price=str(avg) if avg is not None else None,fee_paid=None,status=str(row.get("status") or "UNKNOWN").upper(),reason="RECONCILED_REST_AFTER_TRADING_WS_FAILURE",local_send_ns=sent_ns,local_done_ns=done,exchange_ts_ns=None,request_latency_ms=(done-sent_ns)/1_000_000,ack_latency_ms=None,raw_request_status=None,reconciled=True)

async def resilient_place_fok(self:transport.GeminiTradingSession,params:dict[str,Any],*,terminal_timeout:float=2.0)->transport.ExecutionResult:
    pre=time.perf_counter_ns()
    if not await pre_send_socket_healthy(self): return _zero_result(params,reason="PRE_SEND_TRADING_WS_UNHEALTHY_RETRY_REQUIRED",sent_ns=pre,status="CANCELED")
    try: return await _ORIGINAL_PLACE_FOK(self,params,terminal_timeout=terminal_timeout)
    except Exception as exc:
        try: row=await self._reconcile_rest(params)
        except Exception: row=None
        if row is not None: return _rest_row_result(params,row,sent_ns=pre)
        return _zero_result(params,reason=f"TRADING_WS_FAILURE_AMBIGUOUS_{type(exc).__name__}",sent_ns=pre,status="UNKNOWN")

def install()->None:
    global _INSTALLED
    if _INSTALLED:return
    transport.GeminiTradingSession.place_fok=resilient_place_fok; _INSTALLED=True
