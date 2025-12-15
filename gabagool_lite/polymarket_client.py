"""
Thin wrapper around Polymarket client for straddle strategy.
"""

import time
from typing import Optional, Tuple
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs

class PolymarketClientWrapper:
    """
    Thin wrapper around Polymarket client with straddle-specific methods.
    """

    def __init__(self, client: Optional[ClobClient] = None):
        self.client = client

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

    def place_limit_maker(self, token_id: str, price: float, size: float) -> Optional[str]:
        """
        Place a limit maker order (BUY).

        Args:
            token_id: Token ID to trade
            price: Limit price
            size: Order size

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
                side="BUY"
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

    def is_filled(self, order_id: str) -> Optional[bool]:
        """
        Check if an order is filled.

        Args:
            order_id: Order ID to check

        Returns:
            True if filled, False if open, None if error
        """
        if not self.client:
            # Test mode: simulate fills for test orders
            if "TEST_ORDER" in order_id:
                return True
            return False

        try:
            order = self.client.get_order(order_id)
            if isinstance(order, list) and order:
                order = order[0]

            if not order:
                # Order not found or not returned
                return False # Assume active/not filled if we can't find it?? Or None?
                # If we return None, the loop continues.
                # If we return False, it assumes not filled.
                return False


            filled_size = float(order.get("size_matched", 0) or 0)
            total_size = float(order.get("size", 1) or 1)

            return filled_size >= total_size * 0.99  # Allow for rounding

        except Exception as e:
            print(f"[!] Order status error for {order_id}: {e}")
            return None

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
