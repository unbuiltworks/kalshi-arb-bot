"""Read-only shadow for spending excess modeled edge on a deeper Kalshi FOK.

No order transport is imported. A candidate must first satisfy the live canary's q=1,
>=3c, 10bp, 50ms survival/skew requirements. We then compute the most aggressive
fee-aware Kalshi YES-space limit that still leaves >=3c worst-case modeled profit and
compare cumulative full-book depth at the original vs cushioned limit.
"""
from __future__ import annotations
import asyncio, json, logging, time, uuid
from collections import Counter
from decimal import Decimal
from typing import Any
from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_target_lifecycle_cache as target_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness
from src.gemini_kalshi_hedge_cushion import best_fee_aware_kalshi_hedge, kalshi_cumulative_depth
from src.gemini_kalshi_live_probe_guard import DEFAULT_MIN_LOCKED_PROFIT, MAX_QUOTE_SKEW_MS
D=Decimal
logger=logging.getLogger("kalshi_arb.gemini_kalshi_hedge_cushion_shadow")
exact=readiness.exact; basis=readiness.basis
_ENTRY_SKEW={}; _FEE_MULTIPLIER={}; _COUNTERS=Counter(); _DELAYS=(0,10,20,30,50,75,100); _INSTALLED=False

def _payloads(plan):
    cid=f"shadow-cushion-{uuid.uuid4().hex[:16]}"
    if plan.direction=="KALSHI_YES_GEMINI_NO": outcome="NO"; kside="bid"; kprice=D(plan.kalshi_limit)
    elif plan.direction=="GEMINI_YES_KALSHI_NO": outcome="YES"; kside="ask"; kprice=D("1")-D(plan.kalshi_limit)
    else: raise ValueError(plan.direction)
    g={"symbol":plan.gemini_symbol,"side":"BUY","type":"LIMIT","timeInForce":"FOK","price":str(D(plan.gemini_limit)),"quantity":"1","eventOutcome":outcome,"clientOrderId":cid+"-g"}
    k={"ticker":plan.kalshi_ticker,"client_order_id":cid+"-k","side":kside,"count":"1.00","price":str(kprice),"time_in_force":"fill_or_kill"}
    return g,k

def _capture_plan(original):
    def wrapped(**kwargs):
        plan=original(**kwargs); cand=kwargs.get("candidate"); skew=getattr(cand,"quote_skew_ms",None)
        if skew is not None: _ENTRY_SKEW[(plan.asset,plan.direction,int(plan.opened_ms))]=int(skew)
        return plan
    return wrapped

def _live_gate(feed,plan,active_episodes,now_ms):
    key=(plan.asset,plan.direction,int(plan.opened_ms)); entry_boundary=bool(readiness._PLAN_ENTRY_20BP.get(key,False)); entry_skew=_ENTRY_SKEW.get(key)
    g,k=feed.gemini,feed.kalshi; same_window=bool(feed.active and feed.pair.gemini_symbol==plan.gemini_symbol and feed.pair.kalshi_ticker==plan.kalshi_ticker)
    checks={"gemini_price_ok":False,"gemini_depth_ok":False,"kalshi_price_ok":False,"kalshi_depth_ok":False,"both_marketable":False}
    g_age=k_age=skew=None; post=False; fresh=False
    if same_window and g is not None and k is not None:
        checks=exact.same_limits_marketable(plan,g,k); g_age=max(0,now_ms-int(g.ts_ms)); k_age=max(0,now_ms-int(k.ts_ms)); skew=abs(int(g.ts_ms)-int(k.ts_ms)); post=bool(int(g.ts_ms)>=int(feed.boundary_ms) and int(k.ts_ms)>=int(feed.boundary_ms)); fresh=bool(post and max(g_age,k_age)<=250 and skew<=MAX_QUOTE_SKEW_MS)
    state=basis._BASIS_EPISODES.get((plan.asset,plan.direction)); same_episode=readiness.guarded.same_original_episode(plan,active_episodes,state); snap=basis.basis_snapshot(plan.asset,now_ms) if same_window else None
    bnow=bool(snap and snap.get("research_double_boundary_gate_20bp_fresh_1000ms")); bwhole=bool(same_episode and state and getattr(state,"all_events_gate_20bp",False))
    ok=bool(D(plan.quantity)==D("1") and D(plan.entry_locked_profit)>=DEFAULT_MIN_LOCKED_PROFIT and entry_skew is not None and entry_skew<=MAX_QUOTE_SKEW_MS and skew is not None and skew<=MAX_QUOTE_SKEW_MS and entry_boundary and same_episode and bwhole and bnow and fresh and checks["both_marketable"])
    return ok,{"entry_quote_skew_ms":entry_skew,"current_quote_skew_ms":skew,"entry_double_boundary_10bp":entry_boundary,"same_window":same_window,"same_original_episode":same_episode,**checks,"post_activation_books":post,"gemini_age_ms":g_age,"kalshi_age_ms":k_age,"strict_fresh_250ms_and_50ms_skew":fresh,"double_boundary_10bp_now":bnow,"double_boundary_10bp_held_through_episode":bwhole,"min_abs_double_boundary_margin_bps_now":snap.get("min_abs_double_boundary_margin_bps") if snap else None}

async def _sample(plan,cushion,gate_ms):
    previous=0; rescued_any=False; old_fail_any=False
    for delay in _DELAYS:
        if delay>previous: await asyncio.sleep((delay-previous)/1000)
        previous=delay; book=rollover._HUB._books.get(plan.kalshi_ticker); ready=plan.kalshi_ticker in rollover._HUB._snapshot_ready
        if book is None or not ready: _COUNTERS["samples_no_book"]+=1; continue
        old=kalshi_cumulative_depth(book,side=cushion.side,yes_limit=cushion.original_yes_price,quantity=D("1")); wide=kalshi_cumulative_depth(book,side=cushion.side,yes_limit=cushion.cushioned_yes_price,quantity=D("1")); rescued=bool(not old["fillable"] and wide["fillable"])
        rescued_any|=rescued; old_fail_any|=not old["fillable"]; _COUNTERS["samples"]+=1; _COUNTERS["old_fillable_samples"]+=int(old["fillable"]); _COUNTERS["cushioned_fillable_samples"]+=int(wide["fillable"]); _COUNTERS["rescued_samples"]+=int(rescued); _COUNTERS[f"rescued_at_{delay}ms"]+=int(rescued)
        logger.info("CRYPTO_HEDGE_CUSHION_SAMPLE %s",json.dumps({"asset":plan.asset,"direction":plan.direction,"opened_ms":int(plan.opened_ms),"gate_observed_ms":gate_ms,"sample_after_actionable_ms":delay,"kalshi_ticker":plan.kalshi_ticker,"original_yes_limit":str(cushion.original_yes_price),"cushioned_yes_limit":str(cushion.cushioned_yes_price),"cushion_cents":cushion.cushion_cents,"old_depth":old,"cushioned_depth":wide,"old_fillable_q1":bool(old["fillable"]),"cushioned_fillable_q1":bool(wide["fillable"]),"rescued_by_cushion":rescued,"risk_gates_changed":False,"trade_authorized":False,"read_only":True,"order_entry":False},separators=(",",":")))
    _COUNTERS["episodes_old_failed_any_sample"]+=int(old_fail_any); _COUNTERS["episodes_rescued_any_sample"]+=int(rescued_any)
    logger.info("CRYPTO_HEDGE_CUSHION_SUMMARY %s",json.dumps({"counters":dict(_COUNTERS),"risk_gates_changed":False,"trade_authorized":False,"read_only":True,"order_entry":False},separators=(",",":"),sort_keys=True))

async def _probe(*,feed,plan,active_episodes,delays_ms=(50,100)):
    del delays_ms; key=(plan.asset,plan.direction,int(plan.opened_ms))
    try:
        await asyncio.sleep(.050); now_ms=int(time.time()*1000); actionable,gate=_live_gate(feed,plan,active_episodes,now_ms)
        if not actionable: _COUNTERS["gate_rejected"]+=1; return
        mult=_FEE_MULTIPLIER.get(plan.asset)
        if mult is None: _COUNTERS["missing_fee_multiplier"]+=1; return
        gp,kp=_payloads(plan)
        try: cushion=best_fee_aware_kalshi_hedge(gp,kp,kalshi_fee_multiplier=mult,profit_floor=DEFAULT_MIN_LOCKED_PROFIT)
        except Exception as exc: _COUNTERS["economics_rejected"]+=1; logger.warning("CRYPTO_HEDGE_CUSHION_SKIP asset=%s type=%s error=%s",plan.asset,type(exc).__name__,str(exc)[:160]); return
        _COUNTERS["production_gate_actionable"]+=1; _COUNTERS["cushion_available"]+=int(cushion.has_cushion); _COUNTERS[f"asset_{plan.asset}_actionable"]+=1
        logger.info("CRYPTO_HEDGE_CUSHION_ACTIONABLE %s",json.dumps({"asset":plan.asset,"direction":plan.direction,"opened_ms":int(plan.opened_ms),"gemini_symbol":plan.gemini_symbol,"kalshi_ticker":plan.kalshi_ticker,"quantity":"1","entry_locked_quote_profit":str(plan.entry_locked_profit),"original_recomputed_locked_profit":str(cushion.original_locked_profit),"minimum_locked_profit":str(DEFAULT_MIN_LOCKED_PROFIT),"cushioned_locked_profit":str(cushion.cushioned_locked_profit),"edge_spent":str(cushion.edge_spent),"kalshi_side":cushion.side,"original_kalshi_yes_limit":str(cushion.original_yes_price),"cushioned_kalshi_yes_limit":str(cushion.cushioned_yes_price),"cushion_cents":cushion.cushion_cents,**gate,"risk_gates_changed":False,"trade_authorized":False,"read_only":True,"order_entry":False},separators=(",",":"),default=str))
        await _sample(plan,cushion,now_ms)
    finally: _ENTRY_SKEW.pop(key,None)

def install_shadow():
    global _INSTALLED
    if _INSTALLED:return
    readiness.MIN_DOUBLE_BOUNDARY_BPS=D("10"); rollover.install(); target_cache.install(); target_freshness.install(); readiness.install()
    exact._plan_from_books=_capture_plan(exact._plan_from_books)
    prewarmed=exact.safe.base; original=prewarmed.collect_asset_prewarmed
    async def capture_fee(**kwargs):
        fm=kwargs.get("fee_meta"); asset=str(kwargs.get("asset") or "").upper()
        if fm is not None and asset:_FEE_MULTIPLIER[asset]=D(str(fm.fee_multiplier))
        return await original(**kwargs)
    prewarmed.collect_asset_prewarmed=capture_fee; exact._probe=_probe; _INSTALLED=True
    logger.warning("CRYPTO_HEDGE_CUSHION_SHADOW_INSTALLED production_gate=10bp_50ms min_profit=0.03 q=1 samples_ms=0,10,20,30,50,75,100 full_kalshi_depth=true risk_gates_changed=false trade_authorized=false order_entry=false")

def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s"); install_shadow(); readiness.guarded.main()
if __name__=="__main__":main()
