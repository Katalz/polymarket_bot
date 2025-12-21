from abc import ABC, abstractmethod
from typing import Optional, Dict

class MarketDataProvider(ABC):
    """
    Interface for providing market data (Polling or WS).
    """

    @abstractmethod
    def get_snapshot(self, up_token_id: str, down_token_id: str) -> Optional[Dict[str, float]]:
        """
        Return the current orderbook snapshot.
        If data is invalid or stale, return None (trigger fallback logic in runner).
        Format: {'up_bid': float, 'up_ask': float, 'down_bid': float, 'down_ask': float}
        """
        pass
    
    @abstractmethod
    def wait_for_update(self, timeout: float = 1.0):
        """
        Block until a significant update occurs or timeout.
        """
        pass
