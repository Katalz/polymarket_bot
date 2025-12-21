"""
Thin wrapper around Polymarket client for straddle strategy.
"""

import time
import requests
from typing import Optional, Tuple
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs

class PolymarketClientWrapper:
    """
    Thin wrapper around Polymarket client with straddle-specific methods.
    """

    def __init__(self, client: Optional[ClobClient] = None):
        self.client = client

    def get_usdc_balance(self) -> float:
        """
        Get available USDC balance.
        """
        if not self.client:
            return 10000.0 # Test mode dummy balance

        try:
            # Check for get_balance or get_balance_allowance
            # Assuming get_balance_allowance is the standard for py_clob_client
            if hasattr(self.client, 'get_balance_allowance'):
                bal = self.client.get_balance_allowance(params={"asset_type": "collateral"})
                # Should return dict like {'balance': '...', 'allowance': '...'}
                return float(bal.get('balance', 0.0))
            elif hasattr(self.client, 'get_collateral_balance'):
                return float(self.client.get_collateral_balance())
            else:
                # Fallback or older version
                return 2000.0
        except Exception as e:
            # Log specific error but don't spam 
            # print(f"[!] Balance fetch error: {e}") 
            # Return a safe default to allow bot to try trading. 
            # If real balance is low, order placement will fail gracefully.
            return 2000.0

    def get_token_balance(self, token_id: str) -> float:
        """
        Get the specific token balance (shares held) in the Proxy wallet.
        Prioritizes the Data API (absolute source of truth for the frontend).
        """
        if not self.client:
            return 0.0

        # Method 1: Data API Positions (Preferred Source)
        # Method 1: Data API Positions (Preferred Source)
        try:
            # Get proxy address (funder) from client or env
            proxy = getattr(self.client, 'funder', None)
            if not proxy:
                import os
                proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")
                
            if proxy:
                url = f"https://data-api.polymarket.com/positions?user={proxy}"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for p in data:
                            asset = p.get('asset')
                            if asset and str(asset) == str(token_id):
                                return float(p.get('size', 0.0) or 0.0)
        except Exception as e:
            pass

        # Method 2: Direct get_balance call (Fallback)
        try:
            if hasattr(self.client, 'get_balance'):
                bal_resp = self.client.get_balance(token_id)
                if isinstance(bal_resp, dict):
                    return float(bal_resp.get('balance', 0.0) or 0.0)
                return float(bal_resp or 0.0)
        except Exception as e:
            pass

        # Method 3: Positions list (Fallback)
        try:
            if hasattr(self.client, 'get_positions'):
                positions = self.client.get_positions()
                if isinstance(positions, list):
                    for p in positions:
                        # Extract ID
                        p_tid = None
                        if isinstance(p, dict):
                            p_tid = p.get('asset_id') or p.get('token_id')
                        else: # Object
                            p_tid = getattr(p, 'asset_id', None) or getattr(p, 'token_id', None)
                            
                        if p_tid and str(p_tid) == str(token_id):
                            if isinstance(p, dict):
                                return float(p.get('size', 0.0) or 0.0)
                            return float(getattr(p, 'size', 0.0) or 0.0)
        except Exception as e:
            pass

        return 0.0

    def get_orderbook(self, token_id: str) -> Tuple[float, float]:
        """
        Get best bid and ask for a token.

        Args:
            token_id: Token ID to get orderbook for

        Returns:
            Tuple of (best_bid, best_ask)
        """
        if not self.client:
            # Test mode: return dummy orderbook
            return 0.45, 0.55

        try:
            orderbook = self.client.get_order_book(token_id)

            # Extract bids and asks
            if hasattr(orderbook, 'bids') and hasattr(orderbook, 'asks'):
                bids = list(orderbook.bids)
                asks = list(orderbook.asks)
            elif isinstance(orderbook, dict):
                bids = list(orderbook.get('bids', []))
                asks = list(orderbook.get('asks', []))
            else:
                raise ValueError(f"Unknown orderbook format: {type(orderbook)}")

            def _get_price(item):
                if hasattr(item, 'price'):
                    return float(item.price)
                return float(item.get('price', 0))

            # Sort bids (highest first) and asks (lowest first)
            # The API *should* return them sorted, but user reports otherwise.
            # Explicit sorting guarantees we see the true top of book.
            bids.sort(key=_get_price, reverse=True)
            asks.sort(key=_get_price, reverse=False)

            # Get best prices
            best_bid = _get_price(bids[0]) if bids else 0.0
            best_ask = _get_price(asks[0]) if asks else 1.0

            return best_bid, best_ask

        except Exception as e:
            print(f"[!] Orderbook error for {token_id}: {e}")
            return 0.0, 1.0

    def place_limit_maker(self, token_id: str, price: float, size: float, side: str = "BUY") -> Optional[str]:
        """
        Place a limit maker order.

        Args:
            token_id: Token ID to trade
            price: Limit price
            size: Order size
            side: "BUY" or "SELL"

        Returns:
            Order ID if successful, None if failed
        """
        if not self.client:
            # Test mode: simulate successful order placement
            return f"TEST_ORDER_{token_id}_{price}"

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side
            )

            resp = self.client.create_and_post_order(order_args)
            order_id = resp.get("orderID") or resp.get("order_id")

            if order_id:
                return str(order_id)
            else:
                print(f"[!] Order placement failed: {resp}")
                return None

        except Exception as e:
            print(f"[!] Order placement exception: {e}")
            return None

    def place_limit_maker_check(self, token_id: str, price: float, size: float, 
                               best_bid: float, best_ask: float) -> Optional[str]:
        """
        Place a limit maker order (BUY) with strict maker guards and logging.
        """
        # STRICT MAKER GUARD
        if price >= best_ask:
            print(f"[GUARD] SKIP Taker: Price {price} >= Best Ask {best_ask} (Bid: {best_bid})")
            return None
            
        print(f"[SEND] BUY {token_id} @ {price:.2f} (Bid:{best_bid:.2f} Ask:{best_ask:.2f} Maker:Yes)")
        return self.place_limit_maker(token_id, price, size)

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if not self.client:
            # Test mode: simulate successful cancellation
            return True

        try:
            self.client.cancel(order_id)
            return True
        except Exception as e:
            print(f"[!] Cancel error for {order_id}: {e}")
            return False

    def is_filled(self, order_id: str) -> bool:
        """
        Check if an order is fully filled.
        """
        if not self.client:
            return False
            
        try:
            order = self.client.get_order(order_id)
            if isinstance(order, list) and order:
                order = order[0]
            
            if not order:
                return False
                
            filled_size = float(order.get("size_matched", 0) or 0)
            total_size = float(order.get("size", 1) or 1)
            
            return filled_size >= (total_size * 0.99)
            
        except Exception as e:
            # print(f"[!] is_filled error {order_id}: {e}")
            return False

    def check_order_status(self, order_id: str) -> Tuple[Optional[bool], float, float]:
        """
        Check order status and return (is_filled, avg_fill_price, filled_size).
        """
        if not self.client:
            # Test mode: simulate fills for test orders
            if "TEST_ORDER" in order_id:
                # Parse price from ID usually TEST_ORDER_token_PRICE
                try:
                    parts = order_id.split('_')
                    sim_price = float(parts[-1])
                except:
                    sim_price = 0.5
                return True, sim_price, 10.0 # Dummy size
            return False, 0.0, 0.0

        try:
            order = self.client.get_order(order_id)
            if isinstance(order, list) and order:
                order = order[0]

            if not order:
                return False, 0.0, 0.0

            filled_size = float(order.get("size_matched", 0) or 0)
            total_size = float(order.get("size", 1) or 1)
            
            # Extract Average Price from order details if available
            avg_price = float(order.get("price", 0.0) or 0.0) 
            
            is_filled = filled_size >= total_size * 0.99
            
            return is_filled, avg_price, filled_size

        except Exception as e:
            err_msg = str(e).lower()
            if "not found" in err_msg or "404" in err_msg:
                 return "NOT_FOUND", 0.0, 0.0
            print(f"[!] Order status error for {order_id}: {e}")
            return "ERROR", 0.0, 0.0

    def unwind_position(self, token_id: str, size: float, max_slippage: float = 0.02) -> Optional[str]:
        """
        Unwind a position by selling at market with controlled slippage.

        Args:
            token_id: Token ID to sell
            size: Size to sell
            max_slippage: Maximum slippage allowed (0.02 = 2%)

        Returns:
            Order ID if successful, None if failed
        """
        if not self.client:
            # Test mode: simulate successful unwind
            return f"TEST_UNWIND_{token_id}"

        try:
            # Get current best bid
            best_bid, _ = self.get_orderbook(token_id)

            if best_bid <= 0:
                print(f"[!] Cannot unwind {token_id}: no bids")
                return None

            # Apply slippage limit
            limit_price = best_bid * (1.0 - max_slippage)

            # Use existing limit maker function but sell instead of buy
            # Note: This is a simplification - in practice you'd want to use market orders
            # or implement proper sell logic
            order_args = OrderArgs(
                token_id=token_id,
                price=limit_price,
                size=size,
                side="SELL"  # Note: Selling to unwind
            )

            resp = self.client.create_and_post_order(order_args)
            order_id = resp.get("orderID") or resp.get("order_id")

            if order_id:
                return str(order_id)
            else:
                print(f"[!] Unwind order failed: {resp}")
                return None

        except Exception as e:
            print(f"[!] Unwind error for {token_id}: {e}")
            return None

    def get_balance(self, token_id: str) -> Optional[float]:
        """
        Get the balance for a specific token ID.
        Returns: float (balance) or None (API/Network error)
        """
        if not self.client:
            return 0.0
        
        # Try specific balance endpoint first
        try:
            resp = self.client.get_balance(token_id)
            if isinstance(resp, dict):
                return float(resp.get('balance', 0))
            return float(resp)
        except Exception as e:
            # If get_balance explicitly fails, don't assume 0 yet
            if "not found" in str(e).lower():
                pass # Fall through to get_positions
            else:
                print(f"[!] get_balance API error: {e}")
                # Fall through to get_positions as a second chance
        
        # Second chance: get_positions
        try:
            positions = self.client.get_positions()
            if isinstance(positions, list):
                for pos in positions:
                    p_token_id = getattr(pos, 'token_id', None) or pos.get('token_id')
                    if p_token_id == token_id:
                        return float(getattr(pos, 'size', 0) or pos.get('size', 0) or getattr(pos, 'balance', 0) or pos.get('balance', 0))
                # If we got the list and it's NOT in there, it's genuinely 0 (Polymarket behavior)
                return 0.0
        except Exception as e:
            print(f"[!] get_positions API error: {e}")
            return None # CRITICAL: Return None on actual error
            
        return None # Should not be reached if first try succeeded or second try confirmed/failed
