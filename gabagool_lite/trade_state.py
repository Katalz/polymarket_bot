
from dataclasses import dataclass
from typing import Optional, Dict

@dataclass
class TradeState:
    """Tracks the state of a single active trade per market."""
    slug: str
    market_start_ts: float
    chosen_size: int
    status: str  # 'ORDERS_OPEN', 'ONE_LEG_FILLED', 'DONE'
    trade_id: str  # Unique ID for tracking
    
    # Store order IDs to prevent duplicates
    up_order_id: Optional[str] = None
    down_order_id: Optional[str] = None
    
    def is_active(self) -> bool:
        return self.status in ['ORDERS_OPEN', 'ONE_LEG_FILLED']
