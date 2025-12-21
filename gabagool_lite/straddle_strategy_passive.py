"""
Passive Risk-First Straddle Strategy for Polymarket.
Focus: Capital preservation, minimal directional exposure, deterministic behavior.
"""

import time
from enum import Enum
from typing import Dict, Optional, Any
from gabagool_lite.utils_time import compute_time_remaining, round_to_tick

class PassiveStraddleState(Enum):
    IDLE = "IDLE"
    ORDERS_OPEN = "ORDERS_OPEN"
    ONE_LEG_FILLED = "ONE_LEG_FILLED"
    STRADDLE_COMPLETE = "STRADDLE_COMPLETE" # Balanced holding
    DONE = "DONE" # Finished for the market

class StraddleStrategyPassive:
    """
    Simplified Straddle Strategy.
    - Entry: Only if flat (0/0) and spread <= 0.98.
    - One-Leg Handler:
        - 15m-8m: Patient (Wait for spread <= 0.98). Maker Only.
        - 8m-5m: Safety A (Wait for spread <= 1.00). Maker Only.
        - 5m-0m: Safety B (Wait for spread <= 1.01). Taker Allowed.
    - Exit: Zero Inventory Delta preferred.
    """

    def __init__(self, slug: str, entry_size_shares: float = 5.0, 
                 entry_confirmations: int = 1, 
                 entry_confirm_min_dt_ms: int = 0,
                 entry_threshold_sum_px: float = 0.99):
        self.slug = slug
        self.entry_size_shares = float(entry_size_shares)
        self.entry_confirmations = int(entry_confirmations)
        self.entry_confirm_min_dt_ms = int(entry_confirm_min_dt_ms)
        self.ENTRY_SUM_CAP = float(entry_threshold_sum_px)
        
        self.state = PassiveStraddleState.IDLE
        
        # State for Anti-Mirage
        self._good_spread_hits = 0
        self._last_good_spread_ts = 0.0
        
        # Session-Only Inventory Tracking (No API Sync)
        self.filled_up_shares = 0.0
        self.filled_down_shares = 0.0
        self.total_cost_up = 0.0
        self.total_cost_down = 0.0
        
        # Order Tracking
        self.up_order_id = None
        self.down_order_id = None
        self.up_order_ts = 0.0
        self.down_order_ts = 0.0
        self.up_filled_ev = False
        self.down_filled_ev = False
        
        # Params
        self.ORDER_TIMEOUT_SEC = 15.0
        # self.ENTRY_SUM_CAP initialized from __init__
        self.PATIENT_REBALANCE_CAP = 0.99 
        self.SAFETY_A_REBALANCE_CAP = 0.93 # USER: Execute if > 0.93 after 8m
        self.SAFETY_B_REBALANCE_CAP = 1.01 # Buffer for panic
        
        self.PATIENT_CUTOFF_MIN = 8.0
        self.SAFETY_A_CUTOFF_MIN = 5.0
        
        self.TICK_SIZE = 0.01
        self.MIN_LEG_PRICE = 0.05
        self.MAX_LEG_PRICE = 0.95
        
        # New Safeguards
        self.REBALANCE_COOLDOWN_SEC = 3.0
        self.SURPLUS_THRESHOLD = 1.05 # 5% over target
        self.MIN_ORDER_SHARES = 5.0 # Polymarket Maker Min
        self.MIN_ORDER_USD = 1.10   # Polymarket Taker Min ($1.0 + buffer)
        
        # Invariants
        self.market_done = False
        self.one_leg_start_ts = None
        
        # Logging State
        self.log_once_map = {}
        self.last_activity_ts = 0.0
        self._last_reprice_ts = 0.0
        
        print(f"[PASSIVE] Strategy Inited for {slug}. Size={self.entry_size_shares}")

    def register_order(self, side: str, order_id: str, timestamp: float):
        """Register a new order with its timestamp."""
        if side == "UP":
            self.up_order_id = order_id
            self.up_order_ts = timestamp
        elif side == "DOWN":
            self.down_order_id = order_id
            self.down_order_ts = timestamp
            
        # Ensure state transition on ANY order placement
        if self.state == PassiveStraddleState.IDLE:
             self.state = PassiveStraddleState.ORDERS_OPEN

    def on_order_canceled(self, order_id: str):
        """Handle explicit cancellation."""
        if order_id == self.up_order_id:
            self.up_order_id = None
            self.up_order_ts = 0.0
        elif order_id == self.down_order_id:
            self.down_order_id = None
            self.down_order_ts = 0.0
            
        # If both orders are gone
        if not self.up_order_id and not self.down_order_id:
            # Case 1: No inventory -> Reset to IDLE
            if self.filled_up_shares == 0 and self.filled_down_shares == 0:
                 if self.state == PassiveStraddleState.ORDERS_OPEN:
                     print("[PASSIVE] All Entry Orders Cancelled. Resetting to IDLE.")
                     self.state = PassiveStraddleState.IDLE
            
            # Case 2: Inventory Mismatch -> Ensure ONE_LEG_FILLED
            elif self.filled_up_shares != self.filled_down_shares:
                 # If we were in ORDERS_OPEN or already in ONE_LEG_FILLED, we must ensure we are in ONE_LEG_FILLED
                 # to trigger management logic.
                 if self.state in [PassiveStraddleState.ORDERS_OPEN, PassiveStraddleState.ONE_LEG_FILLED]:
                     print("[PASSIVE] Orders Cancelled with Imbalance. Transitioning to ONE_LEG_FILLED.")
                     self.state = PassiveStraddleState.ONE_LEG_FILLED
                     
    def check_timeouts(self, now_ts: float) -> list:
        """Return list of (order_id, side) to cancel."""
        to_cancel = []
        if self.up_order_id and self.up_order_ts > 0 and (now_ts - self.up_order_ts > self.ORDER_TIMEOUT_SEC):
            to_cancel.append((self.up_order_id, "UP"))
        
        if self.down_order_id and self.down_order_ts > 0 and (now_ts - self.down_order_ts > self.ORDER_TIMEOUT_SEC):
            to_cancel.append((self.down_order_id, "DOWN"))
            
        # [ZOMBIE RECOVERY] If in ORDERS_OPEN but no IDs and no inventory, reset to IDLE
        if self.state == PassiveStraddleState.ORDERS_OPEN and not self.up_order_id and not self.down_order_id:
             if self.filled_up_shares == 0 and self.filled_down_shares == 0:
                  print("[PASSIVE] Zombie ORDERS_OPEN detected (No IDs, No Inventory). Resetting to IDLE.")
                  self.state = PassiveStraddleState.IDLE
            
        return to_cancel

    def get_log_data(self, up_bid, up_ask, down_bid, down_ask, now_ts, enter_result=None):
        """CSV Log data."""
        return {
            "decision": self.state.value,
            "up_px": enter_result.get('up_price', 0) if enter_result else 0,
            "down_px": enter_result.get('down_price', 0) if enter_result else 0,
            "sum_px": up_ask + down_ask, # Approximate
            "inv_up": self.filled_up_shares,
            "inv_down": self.filled_down_shares,
            "skip_reason": enter_result.get('reason', '') if enter_result else '',
            "market_done": self.market_done
        }

    def on_order_update(self, order_id, filled, fill_price=0.0, filled_size=0.0):
        """
        Update local inventory state.
        Trusts order updates fully (Session-Only VWAP).
        """
        price = float(fill_price)
        filled_size = float(filled_size)
        
        # Track which leg this is
        is_up = (order_id == self.up_order_id)
        is_down = (order_id == self.down_order_id)
        
        if not is_up and not is_down and filled_size > 0:
            # Could be a rebalance order, we need to track it properly
            # For simplicity, we assume we know if we are rebalancing UP or DOWN based on state/context
            # or we rely on the runner to map ids. 
            # BUT, to be robust, we need to know side.
            # In this simplified version, we'll try to deduce or just log.
            # Actually, `maybe_enter` sets order IDs. `get_management_action` returns instructions.
            # The runner executes rebalance. We need to know those IDs too?
            # Ideally the runner calls `register_order(id, side)` or similar.
            # For now, let's assume standard Entry IDs. 
            pass

        # Since we use a runner that handles execution, we might need a way to track rebalance fills.
        # The PRO straddle strategy tracks IDs.
        # Let's ensure the runner updates us with IDs if we place new ones.
        
        # UPDATE: The simple runner usually sets strategy.up_order_id = ...
        
        current_fill_up = 0.0
        current_fill_down = 0.0
        
        if is_up:
            # We need to track *incremental* fills to avoid double counting if called multiple times?
            # The runner usually calls this with "filled_size" being CUMULATIVE or INCREMENTAL?
            # Looking at existing `straddle_strategy.py`, it calculates `new_fill`.
            # We will replicate that safest approach.
             new_fill = max(0.0, filled_size - getattr(self, '_last_sz_up', 0.0))
             if new_fill > 0:
                 self.filled_up_shares += new_fill
                 self.total_cost_up += new_fill * price
                 self._last_sz_up = filled_size
             
             if filled:
                 self.up_order_id = None
                 self._last_sz_up = 0.0
        
        elif is_down:
             new_fill = max(0.0, filled_size - getattr(self, '_last_sz_down', 0.0))
             if new_fill > 0:
                 self.filled_down_shares += new_fill
                 self.total_cost_down += new_fill * price
                 self._last_sz_down = filled_size
             
             if filled:
                 self.down_order_id = None
                 self._last_sz_down = 0.0
                 
        # Check for State Transitions
        if self.filled_up_shares > 0 or self.filled_down_shares > 0:
            delta = abs(self.filled_up_shares - self.filled_down_shares)
            # RELAXED BALANCED CHECK: If delta is untradable (< 5 shares), we are "Balanced Enough"
            if delta < self.MIN_ORDER_SHARES:
                # Balanced!
                    print(f"[PASSIVE] BALANCED! {self.filled_up_shares:.1f}/{self.filled_down_shares:.1f}")
                    self.state = PassiveStraddleState.STRADDLE_COMPLETE
                    self.market_done = True # Mark done forever on this market
            else:
                # Unbalanced
                    print(f"[PASSIVE] ONE LEG FILLED. Delta={delta:.1f}")
                    self.state = PassiveStraddleState.ONE_LEG_FILLED

    def maybe_enter(self, up_bid, up_ask, down_bid, down_ask, time_remaining) -> Dict[str, Any]:
        """
        Entry Logic.
        """
        if self.market_done:
            return {'action': 'IDLE', 'reason': 'MARKET_DONE_BALANCED_HOLD'}

        if self.state != PassiveStraddleState.IDLE:
             return {'action': 'IDLE', 'reason': f'State {self.state.value}'}
             
        # 8-Minute Cutoff Rule
        if time_remaining < (self.PATIENT_CUTOFF_MIN * 60) and self.filled_up_shares == 0 and self.filled_down_shares == 0:
            print(f"[PASSIVE] 8m Cutoff reached with 0 inventory. STOPPING.")
            self.market_done = True
            self.state = PassiveStraddleState.DONE
            return {'action': 'IDLE', 'reason': '8M_CUTOFF_NO_ENTRY'}

        # Inventory check (Must be 0/0)
        if self.filled_up_shares > 0 or self.filled_down_shares > 0:
             return {'action': 'IDLE', 'reason': 'INVENTORY_NOT_ZERO'}

        # Spread Check
        sum_ask = up_ask + down_ask
        now = time.time()
        
        if sum_ask > self.ENTRY_SUM_CAP:
             if self._good_spread_hits > 0:
                 print(f"[PASSIVE][ENTRY_CONFIRM] sum={sum_ask:.3f} > {self.ENTRY_SUM_CAP} -> RESET")
             self._good_spread_hits = 0
             return {'action': 'IDLE', 'reason': f'SPREAD_TOO_WIDE ({sum_ask:.3f} > {self.ENTRY_SUM_CAP})'}
             
        # Spread is GOOD -> Handle Confirmations
        dt_ms = (now - self._last_good_spread_ts) * 1000
        if dt_ms >= self.entry_confirm_min_dt_ms:
            self._good_spread_hits += 1
            self._last_good_spread_ts = now
            
        if self._good_spread_hits < self.entry_confirmations:
            print(f"[PASSIVE][ENTRY_CONFIRM] hit={self._good_spread_hits}/{self.entry_confirmations} sum={sum_ask:.3f} threshold={self.ENTRY_SUM_CAP:.3f} dt={dt_ms:.0f}ms -> WAIT")
            return {'action': 'IDLE', 'reason': 'ENTRY_CONFIRM_WAIT'}
        
        # Confirmed!
        if self.entry_confirmations > 1:
            print(f"[PASSIVE][ENTRY_CONFIRM] hit={self._good_spread_hits}/{self.entry_confirmations} sum={sum_ask:.3f} -> ENTER")
        
        # Reset hits for next idle cycle (though typically we transition out of IDLE)
        self._good_spread_hits = 0
             
        # Price Bounds
        up_bid = float(up_bid)
        down_bid = float(down_bid)
        if up_bid < self.MIN_LEG_PRICE or down_bid < self.MIN_LEG_PRICE:
             return {'action': 'IDLE', 'reason': 'PRICE_TOO_LOW'}
        if up_bid > self.MAX_LEG_PRICE or down_bid > self.MAX_LEG_PRICE:
             return {'action': 'IDLE', 'reason': 'PRICE_TOO_HIGH'}

        # Valid Entry
        # Maker Only -> Use Bids
        # But wait, to get filled we want to be at Best Bid or slightly better? 
        # User said "Maker-only". Usually that means placing AT best bid.
        # "s'inspirer du code existant" - existing code rounds to tick.
        
        target_up = round_to_tick(up_bid, self.TICK_SIZE)
        target_down = round_to_tick(down_bid, self.TICK_SIZE)
        
        # Double check we aren't crossing spread (Taking)
        if target_up >= up_ask or target_down >= down_ask:
             return {'action': 'IDLE', 'reason': 'MAKER_PRICE_CROSSES_ASK'}
             
        return {
            'action': 'OPEN_STRADDLE',
            'up_price': target_up,
            'down_price': target_down,
            'size': self.entry_size_shares,
            'reason': 'ENTRY_GOOD_SPREAD'
        }

    def get_management_action(self, up_ask, down_ask, time_remaining) -> Dict[str, Any]:
        """
        One-Leg Management Logic (3 Phases) + ALWAYS-ACTIVE Surplus Unwind.
        """
        # 1. ALWAYS-ACTIVE SURPLUS CHECK (Even in DONE state)
        # If we have too much of a leg, we must sell it.
        target = self.entry_size_shares
        limit = target * self.SURPLUS_THRESHOLD
        
        if self.filled_up_shares > limit:
            qty = self.filled_up_shares - target
            if qty >= self.MIN_ORDER_SHARES:
                return {
                    'action': 'SELL_URGENT', 
                    'leg': 'UP', 
                    'qty': qty, 
                    'reason': 'SURPLUS_DETECTED'
                }
        
        if self.filled_down_shares > limit:
            qty = self.filled_down_shares - target
            if qty >= self.MIN_ORDER_SHARES:
                return {
                    'action': 'SELL_URGENT', 
                    'leg': 'DOWN', 
                    'qty': qty, 
                    'reason': 'SURPLUS_DETECTED'
                }

        if self.state != PassiveStraddleState.ONE_LEG_FILLED:
            return {'action': 'NONE'}
            
        # Cooldown check for rebalances
        now = time.time()
        if now - self._last_reprice_ts < self.REBALANCE_COOLDOWN_SEC:
             return {'action': 'WAIT', 'reason': 'REBALANCE_COOLDOWN'}

        # Determine missing leg
        if self.filled_up_shares > self.filled_down_shares:
            missing_leg = "DOWN"
            missing_qty = self.filled_up_shares - self.filled_down_shares
            ask_missing = down_ask
            if self.filled_up_shares > 0:
                avg_other = self.total_cost_up / self.filled_up_shares
            else:
                avg_other = 0.0
        else:
            missing_leg = "UP"
            missing_qty = self.filled_down_shares - self.filled_up_shares
            ask_missing = up_ask
            if self.filled_down_shares > 0:
                avg_other = self.total_cost_down / self.filled_down_shares
            else:
                avg_other = 0.0

        synthetic_sum = avg_other + ask_missing
        time_min = time_remaining / 60.0
        
        # Phase 1: Patient (15m - 8m)
        if time_min > self.PATIENT_CUTOFF_MIN:
            # Tradability Guard for Maker rebalance
            if missing_qty < self.MIN_ORDER_SHARES:
                 return {'action': 'WAIT', 'reason': f'TINY_LEFTOVER_UNTRADABLE ({missing_qty:.2f} < 5)'}

            if synthetic_sum <= self.PATIENT_REBALANCE_CAP:
                return {
                    'action': 'REBALANCE_MAKER',
                    'leg': missing_leg,
                    'qty': missing_qty,
                    'reason': 'PATIENT_GOOD_SPREAD_FOUND'
                }
            else:
                return {'action': 'WAIT', 'reason': f'PATIENT_WAIT_SPREAD ({synthetic_sum:.3f} > {self.PATIENT_REBALANCE_CAP})'}

        # Phase 2: Safety A (8m - 5m) - User: Execute if missing leg px > 0.93
        elif time_min > self.SAFETY_A_CUTOFF_MIN:
            # Tradability Guard
            if missing_qty < self.MIN_ORDER_SHARES:
                 return {'action': 'WAIT', 'reason': f'TINY_LEFTOVER_UNTRADABLE ({missing_qty:.2f} < 5)'}

            # If the missing leg price is widening past 0.93, HIT it (Urgent)
            if ask_missing > 0.93:
                 # Check USD min for taker
                 if (missing_qty * ask_missing) < self.MIN_ORDER_USD:
                      return {'action': 'WAIT', 'reason': 'TINY_URGENT_VALUE_LOW'}

                 return {
                    'action': 'REBALANCE_URGENT',
                    'leg': missing_leg,
                    'qty': missing_qty,
                    'allow_taker': True,
                    'force_taker': True,
                    'max_pay_price': 0.99, # API MAX
                    'reason': f'SAFETY_A_URGENT_LEG_WIDENING ({ask_missing:.3f} > 0.93)'
                 }
            else:
                # Still okay to Try Maker
                return {
                    'action': 'REBALANCE_MAKER',
                    'leg': missing_leg,
                    'qty': missing_qty,
                    'reason': 'SAFETY_A_MAKER_GOOD_PRICE'
                }
                 
        # Phase 3: Safety B (5m - 0m) - Emergency (Panic)
        else:
            # Tradability Guard
            if missing_qty < self.MIN_ORDER_SHARES:
                 return {'action': 'WAIT', 'reason': f'TINY_LEFTOVER_UNTRADABLE ({missing_qty:.2f} < 5)'}

            # Panic if very close to end or extreme price
            if time_min < 3.0 or ask_missing > 0.94:
                 return {
                    'action': 'REBALANCE_URGENT',
                    'leg': missing_leg,
                    'qty': missing_qty,
                    'allow_taker': True,
                    'force_taker': True,
                    'max_pay_price': 0.99,
                    'reason': 'PANIC_EXIT'
                 }

            # Otherwise, rebalance if sum is below panic cap
            if synthetic_sum <= self.SAFETY_B_REBALANCE_CAP:
                 target_taker_px = min(0.99, self.SAFETY_B_REBALANCE_CAP - avg_other)
                 return {
                    'action': 'REBALANCE_URGENT',
                    'leg': missing_leg,
                    'qty': missing_qty,
                    'allow_taker': True,
                    'force_taker': False,
                    'max_pay_price': target_taker_px,
                    'reason': 'SAFETY_B_EMERGENCY'
                 }
            else:
                 return {'action': 'WAIT', 'reason': f'SAFETY_B_TOO_EXPENSIVE ({synthetic_sum:.3f} > {self.SAFETY_B_REBALANCE_CAP})'}

    def sync_manual_inventory(self, up_bal: float, down_bal: float):
        """
        Ratchet Sync: Only update IF api balance > internal state.
        This recovers 'ghost fills' without risking 'reset to zero' bugs.
        """
        if up_bal > self.filled_up_shares + 0.01:
            print(f"[SYNC] UP Inventory Ratchet: {self.filled_up_shares:.2f} -> {up_bal:.2f}")
            self.filled_up_shares = up_bal
            # If full, clear ID
            if self.filled_up_shares >= (self.entry_size_shares * 0.99):
                self.up_order_id = None
            
        if down_bal > self.filled_down_shares + 0.01:
            print(f"[SYNC] DOWN Inventory Ratchet: {self.filled_down_shares:.2f} -> {down_bal:.2f}")
            self.filled_down_shares = down_bal
            # If full, clear ID
            if self.filled_down_shares >= (self.entry_size_shares * 0.99):
                self.down_order_id = None
            
        # Check if we are now complete
        total_target = self.entry_size_shares * 0.99
        if self.filled_up_shares >= total_target and self.filled_down_shares >= total_target:
             if self.state != PassiveStraddleState.DONE:
                 print("[SYNC] Both legs now complete. Setting DONE.")
                 self.state = PassiveStraddleState.DONE
                 # Safety: Clear all IDs
                 self.up_order_id = None
                 self.down_order_id = None
