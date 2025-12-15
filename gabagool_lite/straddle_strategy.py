"""
Straddle Micro-Arb Strategy for Polymarket 15-minute crypto markets.
"""

import time
from enum import Enum
from typing import Dict, Optional, Tuple, Any
from gabagool_lite.utils_time import compute_time_remaining, round_to_tick
from gabagool_lite.size_optimizer import SizeOptimizer, SizingConfig

class StraddleState(Enum):
    IDLE = "IDLE"
    ORDERS_OPEN = "ORDERS_OPEN"
    ONE_LEG_FILLED = "ONE_LEG_FILLED"
    DONE = "DONE"

class StraddleArbStrategy:
    """
    Maker-only, low-frequency Straddle Micro-Arb strategy.

    Places two BUY limit orders (UP + DOWN) where up_price + down_price <= 0.98.
    Both orders must be maker-only (price < best_ask).
    Tracks fill state and unwinds positions quickly if only one leg fills.
    Only 1 completed straddle attempt per market.

    Now includes EV-optimized sizing with risk constraints.
    """

    def __init__(self, slug: str, has_traded: bool = False,
                 size_optimizer: Optional[SizeOptimizer] = None,
                 bankroll_usd: float = 10000.0):
        self.slug = slug
        self.has_traded = has_traded
        self.state = StraddleState.IDLE
        self.size_optimizer = size_optimizer or SizeOptimizer()
        self.bankroll_usd = bankroll_usd

        # Order tracking
        self.up_order_id: Optional[str] = None
        self.down_order_id: Optional[str] = None
        self.up_order_time: float = 0.0
        self.down_order_time: float = 0.0
        self.up_filled: bool = False
        self.down_filled: bool = False

        # Sizing tracking
        self.optimal_size: int = 0
        self.actual_size: int = 0  # Size actually placed (may differ due to constraints)

        # Config
        self.MAX_SUM_PRICE = 0.98
        self.TICK_SIZE = 0.01
        self.ORDER_TIMEOUT_SEC = 20.0
        self.ONE_LEG_UNWIND_TIMEOUT_SEC = 7.0  # Between 6-8s

        # Unwind tracking
        self.one_leg_fill_time: float = 0.0

    def round_to_tick(self, price: float) -> float:
        """Round price to tick size (0.01)."""
        return round(round(price / self.TICK_SIZE) * self.TICK_SIZE, 2)

    def maybe_enter(self, up_bid: float, up_ask: float, down_bid: float, down_ask: float,
                   time_remaining: float, available_balance: Optional[float] = None,
                   liquidity_depth: Optional[float] = None) -> Dict[str, Any]:
        """
        Check if we should enter straddle position with EV-optimized sizing.

        Returns dict with:
        - 'action': 'ENTER' or 'IDLE'
        - 'up_price': float (if ENTER)
        - 'down_price': float (if ENTER)
        - 'size': int (if ENTER) - optimal size from EV optimization
        - 'reason': str
        - 'intended_price_up': float (for logging)
        - 'rounded_price_up': float (for logging)
        - 'intended_price_down': float (for logging)
        - 'rounded_price_down': float (for logging)
        - 'blocked_maker': bool (for logging)
        - 'sizing_debug': dict (sizing optimization details)
        """
        if self.has_traded:
            return {'action': 'IDLE', 'reason': 'Already traded this market', 'blocked_maker': False}

        if self.state != StraddleState.IDLE:
            return {'action': 'IDLE', 'reason': f'Already in state {self.state.value}', 'blocked_maker': False}

        if time_remaining <= 0:
            return {'action': 'IDLE', 'reason': 'Market expired', 'blocked_maker': False}

        # Find optimal maker prices (intended prices before rounding)
        intended_up_price = min(up_bid + self.TICK_SIZE, up_ask - self.TICK_SIZE)
        intended_down_price = min(down_bid + self.TICK_SIZE, down_ask - self.TICK_SIZE)

        # Round to tick size
        up_price = round_to_tick(intended_up_price, self.TICK_SIZE)
        down_price = round_to_tick(intended_down_price, self.TICK_SIZE)

        # Check sum constraint
        if up_price + down_price > self.MAX_SUM_PRICE:
            return {
                'action': 'IDLE',
                'reason': f'Sum too high: {up_price:.2f} + {down_price:.2f} = {up_price + down_price:.2f} > {self.MAX_SUM_PRICE}',
                'blocked_maker': False
            }

        # MAKER GUARD: Strict check that limit_price < best_ask
        blocked_maker = False
        if up_price >= up_ask:
            blocked_maker = True
            return {
                'action': 'IDLE',
                'reason': f'MAKER_BLOCK: UP price {up_price:.2f} >= ask {up_ask:.2f}',
                'blocked_maker': True
            }

        if down_price >= down_ask:
            blocked_maker = True
            return {
                'action': 'IDLE',
                'reason': f'MAKER_BLOCK: DOWN price {down_price:.2f} >= ask {down_ask:.2f}',
                'blocked_maker': True
            }

        # SIZE OPTIMIZATION: Find optimal size with EV maximization
        optimal_size, sizing_debug = self.size_optimizer.optimize_size(
            up_price=up_price,
            down_price=down_price,
            bankroll_usd=self.bankroll_usd,
            available_balance=available_balance,
            liquidity_depth=liquidity_depth
        )

        if optimal_size == 0:
            return {
                'action': 'IDLE',
                'reason': f'SIZING_INVALID: {sizing_debug.get("reason", "No valid size")}',
                'blocked_maker': False,
                'sizing_debug': sizing_debug
            }

        # Store optimal size for later use
        self.optimal_size = optimal_size

        return {
            'action': 'ENTER',
            'up_price': up_price,
            'down_price': down_price,
            'size': optimal_size,
            'reason': f'Straddle: UP@{up_price:.2f} DOWN@{down_price:.2f} Size={optimal_size}',
            'intended_price_up': intended_up_price,
            'rounded_price_up': up_price,
            'intended_price_down': intended_down_price,
            'rounded_price_down': down_price,
            'blocked_maker': False,
            'sizing_debug': sizing_debug
        }

    def on_orders_placed(self, up_order_id: str, down_order_id: str, actual_size: Optional[int] = None):
        """Called when both orders are successfully placed."""
        self.up_order_id = up_order_id
        self.down_order_id = down_order_id
        self.up_order_time = time.time()
        self.down_order_time = time.time()
        self.actual_size = actual_size or self.optimal_size
        self.state = StraddleState.ORDERS_OPEN

    def on_order_update(self, order_id: str, filled: bool):
        """Called when an order update is received."""
        now = time.time()

        if order_id == self.up_order_id:
            was_filled = self.up_filled
            self.up_filled = filled
            if filled and not was_filled:
                self._handle_one_leg_fill('UP', now)
        elif order_id == self.down_order_id:
            was_filled = self.down_filled
            self.down_filled = filled
            if filled and not was_filled:
                self._handle_one_leg_fill('DOWN', now)

        # Check if both filled (success)
        if self.up_filled and self.down_filled:
            self.state = StraddleState.DONE
            self.has_traded = True

    def _handle_one_leg_fill(self, leg: str, fill_time: float):
        """Handle when one leg fills."""
        self.one_leg_fill_time = fill_time
        self.state = StraddleState.ONE_LEG_FILLED

    def should_cancel_timeout(self) -> Tuple[bool, Optional[str], str]:
        """
        Check if any open orders should be cancelled due to timeout.

        Returns: (should_cancel, order_id, reason)
        """
        now = time.time()

        if self.state == StraddleState.ORDERS_OPEN:
            if self.up_order_id and not self.up_filled:
                if now - self.up_order_time > self.ORDER_TIMEOUT_SEC:
                    reason = f'UP order timeout {self.ORDER_TIMEOUT_SEC}s'
                    self._last_cancel_reason = reason
                    return True, self.up_order_id, reason

            if self.down_order_id and not self.down_filled:
                if now - self.down_order_time > self.ORDER_TIMEOUT_SEC:
                    reason = f'DOWN order timeout {self.ORDER_TIMEOUT_SEC}s'
                    self._last_cancel_reason = reason
                    return True, self.down_order_id, reason

        return False, None, ''

    def should_unwind_one_leg(self) -> Tuple[bool, str, str]:
        """
        Check if we should unwind the filled leg due to one-leg risk guard.

        Returns: (should_unwind, leg_to_unwind, reason)
        """
        if self.state != StraddleState.ONE_LEG_FILLED:
            return False, '', ''

        now = time.time()
        time_since_fill = now - self.one_leg_fill_time

        if time_since_fill > self.ONE_LEG_UNWIND_TIMEOUT_SEC:
            if self.up_filled and not self.down_filled:
                reason = f'One-leg timeout {time_since_fill:.1f}s > {self.ONE_LEG_UNWIND_TIMEOUT_SEC}s'
                self._last_unwind_method = 'pending'  # Will be updated when unwind is executed
                return True, 'UP', reason
            elif self.down_filled and not self.up_filled:
                reason = f'One-leg timeout {time_since_fill:.1f}s > {self.ONE_LEG_UNWIND_TIMEOUT_SEC}s'
                self._last_unwind_method = 'pending'  # Will be updated when unwind is executed
                return True, 'DOWN', reason

        return False, '', ''

    def on_unwind_attempted(self, leg: str, method: str, price: float, success: bool):
        """Track unwind attempt results."""
        self._last_unwind_method = method
        self._last_unwind_price = price
        self._last_unwind_result = 'SUCCESS' if success else 'FAILED'

    def get_log_data(self, up_bid: float, up_ask: float, down_bid: float, down_ask: float,
                    now_ts: float, enter_result: Optional[Dict] = None,
                    sizing_debug: Optional[Dict] = None) -> Dict[str, Any]:
        """Get current state for logging."""
        # Calculate potential prices if in IDLE state
        if self.state == StraddleState.IDLE:
            up_px = round_to_tick(min(up_bid + self.TICK_SIZE, up_ask - self.TICK_SIZE), self.TICK_SIZE)
            down_px = round_to_tick(min(down_bid + self.TICK_SIZE, down_ask - self.TICK_SIZE), self.TICK_SIZE)
            sum_px = up_px + down_px
            max_sum_price = self.MAX_SUM_PRICE
        else:
            up_px = down_px = sum_px = 0.0
            max_sum_price = 0.0

        # Extract intended/rounded prices from enter_result if available
        intended_price_up = enter_result.get('intended_price_up', 0.0) if enter_result else 0.0
        rounded_price_up = enter_result.get('rounded_price_up', 0.0) if enter_result else 0.0
        intended_price_down = enter_result.get('intended_price_down', 0.0) if enter_result else 0.0
        rounded_price_down = enter_result.get('rounded_price_down', 0.0) if enter_result else 0.0
        blocked_maker = enter_result.get('blocked_maker', False) if enter_result else False

        # Extract sizing information
        optimal_size = enter_result.get('size', 0) if enter_result else self.optimal_size
        actual_size = self.actual_size

        # State and unwind tracking
        state = self.state.value
        cancel_reason = getattr(self, '_last_cancel_reason', '')
        unwind_method = getattr(self, '_last_unwind_method', '')
        unwind_price = getattr(self, '_last_unwind_price', 0.0)
        unwind_result = getattr(self, '_last_unwind_result', '')

        return {
            'timestamp': now_ts,
            'slug': self.slug,
            'market_start_ts': 0.0,  # Will be set by runner
            'market_end_ts': 0.0,    # Will be set by runner
            'now_ts': now_ts,
            'time_remaining': 0.0,   # Will be set by runner
            'up_bid': up_bid,
            'up_ask': up_ask,
            'down_bid': down_bid,
            'down_ask': down_ask,
            'up_px': up_px,
            'down_px': down_px,
            'sum_px': sum_px,
            'max_sum_price': max_sum_price,
            'decision': state,
            'blocked_maker': blocked_maker,
            'order_age': 0.0,  # Will be set by runner
            'up_filled': self.up_filled,
            'down_filled': self.down_filled,
            'has_traded': self.has_traded,
            'intended_price_up': intended_price_up,
            'rounded_price_up': rounded_price_up,
            'intended_price_down': intended_price_down,
            'rounded_price_down': rounded_price_down,
            'cancel_reason': cancel_reason,
            'unwind_method': unwind_method,
            'unwind_price': unwind_price,
            'unwind_result': unwind_result
        }
