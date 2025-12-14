"""
Gabagool-Lite Strategy Module
=============================

Market-making / accumulation strategy for small capital (~600$).
- State machine per market (IDLE -> ACCUMULATE -> HEDGE -> LOCKED -> STOPPED)
- Single active market orchestration
- Maker-first, ladder orders, no aggressive market orders
"""

from .config import GabagoolLiteConfig, DEFAULT_CONFIG_600
from .strategy import MarketState, GabagoolLiteStrategy
from .orchestrator import LiteOrchestrator
from .metrics import MarketMetrics, compute_lockable_pnl

__all__ = [
    "GabagoolLiteConfig",
    "DEFAULT_CONFIG_600",
    "MarketState",
    "GabagoolLiteStrategy",
    "LiteOrchestrator",
    "MarketMetrics",
    "compute_lockable_pnl",
]
