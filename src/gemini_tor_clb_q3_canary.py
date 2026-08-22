"""Candidate wrapper for q=3 Toronto/Miami total x Columbus/Nashville spread combo."""
from __future__ import annotations
import asyncio
from decimal import Decimal
import src.gemini_tormia_fitz_q3_canary as q3

q3.COMBO = "GEMI-CMB-260823-8EC8FBCF7988E34C-CMB"
q3.COMBO_PRICE = Decimal("0.92")
q3.HEDGES = [
    {"symbol":"GEMI-MLS-2608222330-TOR-MIA-REGT-O0","outcome":"NO","target":Decimal("0.02"),"emergency":Decimal("0.08")},
    {"symbol":"GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2","outcome":"YES","target":Decimal("0.02"),"emergency":Decimal("0.08")},
]

if __name__=="__main__":
    print("GEMINI_TOR_CLB_Q3_BOOT", flush=True)
    asyncio.run(q3.run_once())
