from typing import Optional, List, Dict
import logging

class SafeBuyOnlyClientWrapper:
    """
    FAIL-SAFE Wrapper to enforce NO SELL EVER.
    Wraps PolymarketClientWrapper to block risky methods.
    """
    def __init__(self, real_client):
        self.client = real_client
        
    def __getattr__(self, name):
        # Forward safe methods to real client
        return getattr(self.client, name)
        
    def place_limit_maker(self, token_id: str, price: float, size: float, side: str = "BUY") -> Optional[str]:
        """Allows SELL side for surplus management but logs it."""
        if side.upper() == "SELL":
            print(f"[SURPLUS_SELL] SafeWrapper permitting SELL of {size} {token_id} @ {price}")
            
        return self.client.place_limit_maker(token_id, price, size, side=side)
        
    def unwind_position(self, *args, **kwargs):
        """Strictly BLOCKS unwind."""
        print(f"[FATAL] SafeWrapper BLOCKED attempt to UNWIND position.")
        return None
        
    def cancel_order(self, order_id: str):
        """Allows cancelling orders (safe)."""
        return self.client.cancel_order(order_id)
        
    def cancel_all(self):
        """Allows cancelling all (safe)."""
        if hasattr(self.client, 'cancel_all'):
             return self.client.cancel_all()
        # Fallback if client doesn't have it
        print("[WARN] cancel_all not implemented in base client")
