"""Read-only extension of Gemini extreme combo depth scanner to q=1,2,3.

No combo creation and no order entry. Used to verify whether fee amortization lets
small multi-contract canaries quote closer to fair while preserving >=3c locked
profit per contract.
"""
import asyncio
import src.gemini_extreme_combo_depth_quant as base

base.QS = (1, 2, 3)

if __name__ == "__main__":
    asyncio.run(base.run_once())
