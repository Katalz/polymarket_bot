"""
Straddle Reconstruct Strategy (Inventory-Aware) for Polymarket.

Key Rules:
1. MAX_TOTAL_SHARES = 10 (Strict Hard Cap)
2. USD_PER_ORDER = 2.0 (Testing Size)
3. Inventory Balance Priority
4. Forked from StraddleArbStrategy
"""

import time
import math
from enum import Enum
from typing import Dict, Optional, Tuple, Any
from gabagool_lite.utils_time import round_to_tick

class ReconstructState(Enum):
    IDLE = "IDLE"
    ORDERS_OPEN = "ORDERS_OPEN"           # Wait for fills
    ONE_LEG_INVENTORY = "ONE_LEG_INVENTORY" # Holding inventory, managing rebalance
    STRADDLE_COMPLETE = "STRADDLE_COMPLETE" # Balanced
    DONE = "DONE"

class StraddleReconstructStrategy:
    """
    Reconstruct Strategy:
    - Maintains balanced inventory.
    - Uses small $2 orders.
    - Hard cap of 10 shares total.
    """

    def __init__(self, slug: str, has_traded: bool = False, size_optimizer=None, bankroll_usd: float = 10000.0, 
                 initial_state: Optional[Dict] = None, max_usd_exposure: float = 15.0):
        self.slug = slug
        self.has_traded = has_traded # Used to prevent re-entry if strategy considers itself "DONE" for the market
        self.state = ReconstructState.IDLE
        
        # Persistence Helpers (Last seen fill amounts for currently open orders)
        self._last_sz_up = initial_state.get("_last_sz_up", 0.0) if initial_state else 0.0
        self._last_sz_down = initial_state.get("_last_sz_down", 0.0) if initial_state else 0.0
        
        # Params
        self.USD_PER_ORDER = 2.0
        # Strict USD Cap ($15.0). Lead by USD, fallback to shares.
        self.MAX_USD_EXPOSURE = max_usd_exposure
        self.MAX_TOTAL_SHARES = 60.0 # Higher share cap allowed if USD cap permits
        self.OneLegTimeout = 15.0 # Seconds to wait in one leg before cancelling unfilled
        self.MAX_SUM_PRICE = 0.98
        self.rebalance_tolerance_shares = 0.1 # Tolerance for considering straddle "balanced"

        # Inventory
        self.filled_up_shares = 0.0
        self.filled_down_shares = 0.0
        self.total_cost_up = 0.0
        self.total_cost_down = 0.0
        self.reconstruction_spent = 0.0

        # Order Tracking
        self.up_order_id = None
        self.down_order_id = None
        self.up_filled = False
        self.up_order_time = 0.0
        self.down_order_time = 0.0

        if initial_state:
            print(f"[INIT] Restoring detailed state for {self.slug}...")
            self.filled_up_shares = float(initial_state.get("filled_up_shares", 0.0))
            self.filled_down_shares = float(initial_state.get("filled_down_shares", 0.0))
            self.total_cost_up = float(initial_state.get("total_cost_up", 0.0))
            self.total_cost_down = float(initial_state.get("total_cost_down", 0.0))
            self.reconstruction_spent = float(initial_state.get("reconstruction_spent", 0.0))
            self.has_traded = initial_state.get("has_traded", self.has_traded)
            self.up_order_id = initial_state.get("up_order_id")
            self.down_order_id = initial_state.get("down_order_id")
            print(f"       Restored: Inv={self.filled_up_shares}/{self.filled_down_shares} Orders={self.up_order_id}/{self.down_order_id}")
        
        # One Leg Logic
        self.one_leg_start_ts = None
        self.last_cancel_ts = 0.0 # Throttling re-entry after cancel
        self.last_fill_ts = 0.0 # Tracking last fill time for sync protection
        self.SYNC_GRACE_PERIOD = 180.0 # 🔴 Increased for Polymarket balance lag
        
        # Logging Compatibility (for log_market_summary)
        self.entry_up_px = 0.0
        self.entry_down_px = 0.0
        self.actual_size = 0.0
        self._last_unwind_price = 0.0
        
        # Reconstruction Budget State (Already initialized above for restoration)
        self.RECONSTRUCTION_BUDGET_USD = 50.0
        self.RECONSTRUCT_CHUNK_SHARES = 1.0 # Default 1, but user allows 2
        
        self.reconstruction_budget = self.RECONSTRUCTION_BUDGET_USD # Fixed budget 50$
        
        self.entry_sum_target = 0.0 
        self.is_reconstructing_order = False 
        self.last_reconstruct_cost = 0.0
        self.latest_reconstruct_debug = {}
        
        # Logging / Debug
        self.last_reprice_ts = 0.0
        self.actual_size = 0.0 # Last entry size
        
        self.last_reconstruct_cost = 0.0
        self.latest_reconstruct_debug = {}
        
        
        # 🔴 New Brake & Selectivity Mechanisms
        self.ewma_sum_px = None
        self.ewma_sample_count = 0
        self.EWMA_ALPHA = 0.033 # ~60s window at 1 tick/sec
        self.EWMA_WARMUP_SAMPLES = 60
        
        self.last_straddle_completion_ts = 0.0
        self.EXPANSION_COOLDOWN_SEC = 30.0
        
        self.NEVER_REINFORCE_SUM_PX = 1.01
        self.MAX_SKEW_RATIO = 2.0 # Max 2x size on one leg
        
        # Logging / Debug
        self.last_reprice_ts = 0.0
        self.actual_size = 0.0 # Last entry size
        
        print(f"[RECONSTRUCT] Init {self.slug}. Budget=${self.reconstruction_budget} Cap={self.MAX_TOTAL_SHARES}. Tolerance={self.rebalance_tolerance_shares}")

    def get_current_state(self) -> Dict:
        """Returns the current state for persistence."""
        return {
            "has_traded": self.has_traded,
            "filled_up_shares": self.filled_up_shares,
            "filled_down_shares": self.filled_down_shares,
            "total_cost_up": self.total_cost_up,
            "total_cost_down": self.total_cost_down,
            "reconstruction_spent": self.reconstruction_spent,
            "up_order_id": self.up_order_id,
            "down_order_id": self.down_order_id,
            "_last_sz_up": self._last_sz_up,
            "_last_sz_down": self._last_sz_down
        }
    def sync_state(self, up_shares: float, down_shares: float):
        """
        Synchronize internal inventory with external source (e.g. Wallet/API).
        
        [SKEPTICAL] Prevents API lag from wiping local fills by requiring consecutive zeros.
        """
        now = time.time()
        up_shares = float(up_shares)
        down_shares = float(down_shares)
        
        # 1. Grace Period Protection
        is_grace_period = (now - self.last_fill_ts < self.SYNC_GRACE_PERIOD) or \
                          (self.up_order_id is not None) or (self.down_order_id is not None)
            
        if up_shares < self.filled_up_shares and is_grace_period:
            up_shares = self.filled_up_shares
            
        if down_shares < self.filled_down_shares and is_grace_period:
            down_shares = self.filled_down_shares

        # 2. Conservative Zeroing (The "Skeptical" Logic)
        # If API says 0 but local ledger says we have positions, we wait for 5 consecutive zeros.
        if up_shares < 0.1 and down_shares < 0.1 and (self.filled_up_shares > 1.0 or self.filled_down_shares > 1.0):
            self.consecutive_zero_syncs += 1
            if self.consecutive_zero_syncs < 10:
                # print(f"[SYNC] Ignoring aggressive zero-out from API (Attempt {self.consecutive_zero_syncs}/10)")
                up_shares = self.filled_up_shares
                down_shares = self.filled_down_shares
            else:
                print(f"[SYNC] API Zero-balance confirmed after {self.consecutive_zero_syncs} attempts. Resetting.")
        else:
            self.consecutive_zero_syncs = 0

        # 3. Apply Updates
        if abs(self.filled_up_shares - up_shares) > 0.01 or abs(self.filled_down_shares - down_shares) > 0.01:
            print(f"[SYNC] Updating inventory: UP {self.filled_up_shares:.1f}->{up_shares:.1f}, DOWN {self.filled_down_shares:.1f}->{down_shares:.1f}")
            
            # If inventory is genuinely zero, we reset budgets (only log if there was something to reset)
            if up_shares < 0.1 and down_shares < 0.1:
                if self.total_cost_up > 0 or self.total_cost_down > 0 or self.reconstruction_spent > 0:
                    print("[SYNC] Inventory at zero. Resetting cost basis and budgets.")
                self.total_cost_up = 0.0
                self.total_cost_down = 0.0
                self.reconstruction_spent = 0.0

        self.filled_up_shares = up_shares
        self.filled_down_shares = down_shares
        
        # Re-check state immediately after sync
        self._check_state_after_update()

    def get_log_data(self, up_bid, up_ask, down_bid, down_ask, now_ts, enter_result=None, sizing_debug=None):
        """Map internal state to CSV fields expected by runner."""
        
        # Calculate derived values for logging
        imbalance = self.filled_up_shares - self.filled_down_shares
        total_inv = self.filled_up_shares + self.filled_down_shares
        cap_left = self.MAX_TOTAL_SHARES - total_inv
        
        data = {
            "decision": self.state.value,
            "up_px": enter_result.get('up_price', 0) if enter_result else 0,
            "down_px": enter_result.get('down_price', 0) if enter_result else 0,
            "sum_px": up_bid + down_bid, # Snapshot
            "max_sum_price": self.MAX_SUM_PRICE,
            "optimal_size": enter_result.get('size', 0) if enter_result else 0,
            "actual_size": self.actual_size,
            "filled_up_shares": self.filled_up_shares,
            "filled_down_shares": self.filled_down_shares,
            "avg_cost_up": self.total_cost_up / self.filled_up_shares if self.filled_up_shares > 0 else 0.0,
            "avg_cost_down": self.total_cost_down / self.filled_down_shares if self.filled_down_shares > 0 else 0.0,
            "remaining_up": 0, # Not strictly used in this mode
            "remaining_down": 0,
            
            # New Custom Fields
            "skip_reason": enter_result.get('reason', '') if enter_result else '',
            "cancel_reason": getattr(self, '_last_cancel_reason', ''),
            "reprice_reason": f"Inv: {self.filled_up_shares:.1f}/{self.filled_down_shares:.1f} CapLeft: {cap_left:.1f}",
            
            # Reconstruct Hardening Fields
            "inv_up": self.filled_up_shares,
            "inv_down": self.filled_down_shares,
            "total_inventory": total_inv,
            "cap_remaining": cap_left,
            
            # Budget Fields
            "reconstruction_budget": self.reconstruction_budget,
            "reconstruction_spent": self.reconstruction_spent,
            "remaining_edge": self.reconstruction_budget - self.reconstruction_spent,
            "entry_sum_target": self.entry_sum_target,
            "initial_edge_per_share": 1.0 - self.entry_sum_target if self.entry_sum_target > 0 else 0.0,
            
            # Rebalancing & Selectivity Fields
            "ewma_sum_px": self.ewma_sum_px or 0.0,
            "ewma_samples": self.ewma_sample_count,
            "last_completion_ts": self.last_straddle_completion_ts,
            "cooldown_remaining": max(0.0, self.EXPANSION_COOLDOWN_SEC - (time.time() - self.last_straddle_completion_ts)),
            
            # Advanced Reconstruct Debug
            "imbalance_shares": self.latest_reconstruct_debug.get("imbalance_shares", 0.0),
            "avg_other_price": self.latest_reconstruct_debug.get("avg_other_price", 0.0),
            "px_missing_now": self.latest_reconstruct_debug.get("px_missing_now", 0.0),
            "overpay_per_share": self.latest_reconstruct_debug.get("overpay_per_share", 0.0),
            "reconstruct_chunk_shares": self.latest_reconstruct_debug.get("reconstruct_chunk_shares", 0.0),
            "reconstruct_cost_est_usd": self.latest_reconstruct_debug.get("reconstruct_cost_est_usd", 0.0),
            "reconstruct_overpay_limit": self.latest_reconstruct_debug.get("reconstruct_overpay_limit", 0.0),
            "reconstruct_block_reason": self.latest_reconstruct_debug.get("reconstruct_block_reason", ""),
        }
        return data

    def on_orders_placed(self, up_oid, down_oid, size, up_px=0.0, down_px=0.0, is_reconstruct=False):
        self.up_order_id = up_oid
        self.down_order_id = down_oid
        self.up_order_time = time.time()
        self.down_order_time = time.time()
        self.state = ReconstructState.ORDERS_OPEN
        self.up_filled = False
        self.down_filled = False
        self.actual_size = size
        
        self.is_reconstructing_order = is_reconstruct
        
        if not is_reconstruct and up_px > 0 and down_px > 0:
             self.entry_sum_target = up_px + down_px 
             self.entry_up_px = up_px
             self.entry_down_px = down_px
        
        # Refresh grace period to prevent API balance lag from wiping internal state
        self.last_fill_ts = time.time()

    # --- Pure Helpers for Reconstruction Logic ---
    
    @staticmethod
    def _compute_imbalance(up_filled, down_filled):
        return up_filled - down_filled

    @staticmethod
    def _max_overpay_per_share(time_remaining):
        # > 10 min (600s): 0.002 (Strict 0.2%)
        if time_remaining > 600:
            return 0.002
        
        # < 2 min (120s): 0.05
        if time_remaining < 120:
            return 0.05
            
        # 600 -> 120: Interpolate 0.002 -> 0.02
        # Slope = (y2 - y1) / (x2 - x1) = (0.02 - 0.002) / (120 - 600) 
        # = 0.018 / -480 = -0.0000375
        # y = y1 + m(x - x1) = 0.002 + (-0.0000375 * (time_remaining - 600))
        return 0.002 + (-0.0000375 * (time_remaining - 600))

    @staticmethod
    def _estimate_reconstruct_cost(avg_other, px_missing_now, chunk_shares):
        # overpay_per_share = (avg_other + px_missing_now) - 1.0
        # cost = max(0, overpay) * chunk
        overpay = (avg_other + px_missing_now) - 1.0
        return max(0.0, overpay) * chunk_shares

    @staticmethod
    def _can_reconstruct(cost_est, overpay_per_share, budget_left, overpay_limit):
        if overpay_per_share > overpay_limit:
            return False, "OVERPAY_LIMIT"
        if cost_est > budget_left:
            return False, "BUDGET_EXCEEDED"
        return True, "OK"

    def on_order_canceled(self, order_id):
        if order_id == self.up_order_id:
            self.up_order_id = None
        if order_id == self.down_order_id:
            self.down_order_id = None
            
        # Refresh grace period to allow API to settle after cancellation
        self.last_fill_ts = time.time()
        self._check_state_after_update()

    def on_order_update(self, order_id, filled, fill_price=0.0, filled_size=0.0):
        # We handle partial fills (filled=False) or full fills (filled=True)
        f_size = float(filled_size)
        f_px = float(fill_price)
        
        # Identify leg
        is_up = (order_id == self.up_order_id)
        is_down = (order_id == self.down_order_id)
        
        if not is_up and not is_down:
            return

        # Track incremental cost basis using deltas
        if is_up:
            new_fill = max(0.0, f_size - getattr(self, '_last_sz_up', 0.0))
            if new_fill > 0 and f_px > 0:
                self.total_cost_up += new_fill * f_px
                self.filled_up_shares += new_fill # 🔴 Local Increment
                self._last_sz_up = f_size
                self.last_fill_ts = time.time()  # Refresh grace period
                
                # Reconstruction Cost Tracking
                if self.is_reconstructing_order:
                     avg_down = self.total_cost_down / self.filled_down_shares if self.filled_down_shares > 0 else 0.5
                     overpay = (f_px + avg_down) - 1.0
                     cost = max(0.0, overpay) * new_fill
                     if cost > 0:
                         self.reconstruction_spent += cost
                         self.last_reconstruct_cost = cost
            
            if filled:
                self.up_filled = True
                self.up_order_id = None
                self._last_sz_up = 0.0

        elif is_down:
            new_fill = max(0.0, f_size - getattr(self, '_last_sz_down', 0.0))
            if new_fill > 0 and f_px > 0:
                self.total_cost_down += new_fill * f_px
                self.filled_down_shares += new_fill # 🔴 Local Increment
                self._last_sz_down = f_size
                self.last_fill_ts = time.time()  # Refresh grace period
                
                # Reconstruction Cost Tracking
                if self.is_reconstructing_order:
                     avg_up = self.total_cost_up / self.filled_up_shares if self.filled_up_shares > 0 else 0.5
                     overpay = (f_px + avg_up) - 1.0
                     cost = max(0.0, overpay) * new_fill
                     if cost > 0:
                         self.reconstruction_spent += cost
                         self.last_reconstruct_cost = cost

            if filled:
                self.down_filled = True
                self.down_order_id = None
                self._last_sz_down = 0.0
        
        # Cost reset if flattened
        if self.filled_up_shares < 0.1 and self.filled_down_shares < 0.1:
            self.total_cost_up = 0.0
            self.total_cost_down = 0.0
            self.reconstruction_spent = 0.0

        # Update last fill timestamp for sync protection
        if f_size > 0:
            self.last_fill_ts = time.time()
            
        self._check_state_after_update()

    def _check_state_after_update(self):
        """Update state based on fills and active orders."""
        orders_active = (self.up_order_id is not None) or (self.down_order_id is not None)
        
        if not orders_active:
            # Check inventory balance
            imbalance = self.filled_up_shares - self.filled_down_shares
            
            if abs(imbalance) <= self.rebalance_tolerance_shares:
                # Balanced
                if self.state != ReconstructState.IDLE:
                    # Just transitioned to IDLE (completion)
                    print(f"[RECONSTRUCT] Straddle completion detected (Inv: {self.filled_up_shares:.1f}/{self.filled_down_shares:.1f})")
                    self.last_straddle_completion_ts = time.time()
                self.state = ReconstructState.IDLE
            else:
                # Unbalanced
                self.state = ReconstructState.ONE_LEG_INVENTORY
                if self.one_leg_start_ts is None:
                    self.one_leg_start_ts = time.time()

    def should_cancel_timeout(self, time_remaining):
        now = time.time()
        if self.state == ReconstructState.ORDERS_OPEN:
            one_filled = (self.up_filled and not self.down_filled) or (self.down_filled and not self.up_filled)
            if one_filled:
                if (now - self.up_order_time > self.OneLegTimeout) and (self.up_order_id and not self.up_filled):
                    return True, self.up_order_id, "Timeout 2nd Leg (15s)"
                if (now - self.down_order_time > self.OneLegTimeout) and (self.down_order_id and not self.down_filled):
                    return True, self.down_order_id, "Timeout 2nd Leg (15s)"

            if (now - self.up_order_time > self.OneLegTimeout) and (self.up_order_id and not self.up_filled):
                return True, self.up_order_id, "Timeout Entry (15s)"
            if (now - self.down_order_time > self.OneLegTimeout) and (self.down_order_id and not self.down_filled):
                return True, self.down_order_id, "Timeout Entry (15s)"
        return False, None, ""

    def get_management_action(self, now, up_bid, up_ask, down_bid, down_ask, time_remaining):
        # One-Leg logic is handled via IDLE re-entry
        return {"action": "NONE"}
    
    def compute_reconstruct_orders(self, inv_up, inv_down, cap, min_shares):
        """
        Determine reconstruct orders based on inventory and cap.
        Returns: (buy_up_size, buy_down_size, plan_reason)
        """
        total_inv = inv_up + inv_down
        cap_remaining = cap - total_inv
        
        # Case 5: Cap insufficient for min_size
        if cap_remaining < min_shares:
            return 0.0, 0.0, "BLOCKED_BY_CAP_MIN"
            
        imbalance = inv_up - inv_down
        
        # Identify legs
        if imbalance > 0.1: # Have UP, Need DOWN
            # existing_leg_size = inv_up
            # missing_leg_size = inv_down # usually 0 or low
            leg_dir = "DOWN"
        elif imbalance < -0.1: # Have DOWN, Need UP
            # existing_leg_size = inv_down
            # missing_leg_size = inv_up
            leg_dir = "UP"
        else:
            return 0.0, 0.0, "BALANCED"
            
        # Define Option Sizes
        # Option B (Aggressive): Buy 10 (2*min) of missing, 5 (1*min) of existing.
        # Strict user definition: "BUY 10 [Missing] + BUY 5 [Existing]"
        # We use min_shares to define "5" and "10".
        
        opt_b_missing = min_shares * 2.0
        opt_b_existing = min_shares * 1.0
        total_opt_b = opt_b_missing + opt_b_existing
        
        # Option A (Safe): Buy min_shares of missing only.
        opt_a_missing = min_shares
        total_opt_a = opt_a_missing
        
        # Try Option B first
        if cap_remaining >= total_opt_b:
            # We can do aggressive reconstruct
            if leg_dir == "DOWN":
                return opt_b_existing, opt_b_missing, "OVERWEIGHT_MISSING_DOWN" # Buy UP(exist), DOWN(miss)
            else:
                return opt_b_missing, opt_b_existing, "OVERWEIGHT_MISSING_UP" # Buy UP(miss), DOWN(exist)
                
        # Fallback to Option A
        if cap_remaining >= total_opt_a:
            if leg_dir == "DOWN":
                return 0.0, opt_a_missing, "FILL_MISSING_ONLY_DOWN"
            else:
                return opt_a_missing, 0.0, "FILL_MISSING_ONLY_UP"
                
        # Should be covered by Case 5, but safe fallback
        return 0.0, 0.0, "BLOCKED_BY_CAP_CALC"


    def maybe_enter(self, up_bid, up_ask, down_bid, down_ask, time_remaining, available_balance=None, liquidity_depth=None, test_mode=False):
        """
        Main decision logic with Smart Selective Brakes.
        """
        now = time.time()
        
        # 🔴 EWMA Market Analysis & Data Validation
        sum_px = up_ask + down_ask # Using ASK for conservative sum
        if (up_ask > 0 and down_ask > 0) and (0.80 <= sum_px <= 1.20):
            if self.ewma_sum_px is None:
                self.ewma_sum_px = sum_px
                self.ewma_sample_count = 1
            else:
                self.ewma_sum_px = (self.EWMA_ALPHA * sum_px) + ((1.0 - self.EWMA_ALPHA) * self.ewma_sum_px)
                self.ewma_sample_count += 1
        
        # 0. Active Order Protection
        if self.up_order_id or self.down_order_id:
            return {'action': 'IDLE', 'reason': 'ORDERS_OPEN_ALREADY'}

        imbalance = self.filled_up_shares - self.filled_down_shares
        is_one_leg = abs(imbalance) > self.rebalance_tolerance_shares
        
        total_usd_exposure = (self.filled_up_shares * up_bid) + (self.filled_down_shares * down_bid)
        total_shares = self.filled_up_shares + self.filled_down_shares

        # 1. Mode A: REBALANCING (Fixing Imbalance)
        if is_one_leg:
            # Time-Adaptive Caps for Rebalancing
            if time_remaining > 600: # > 10m
                rebalance_cap = 1.00
                phase = "EARLY_SELECTIVE"
                # Use BID for maker fills in early phase
                up_px = up_bid
                down_px = down_bid
            else: # < 10m
                rebalance_cap = 1.02
                phase = "LATE_URGENT"
                # Use ASK for aggressive taker fills in urgent phase
                up_px = up_ask
                down_px = down_ask
            
            if sum_px > rebalance_cap:
                return {'action': 'IDLE', 'reason': f'REBALANCE_BLOCKED_PX ({phase}: {sum_px:.2f} > {rebalance_cap})'}
            
            # Firebreak check
            if sum_px > self.NEVER_REINFORCE_SUM_PX:
                 return {'action': 'IDLE', 'reason': f'REBALANCE_BLOCKED_FIREBREAK ({sum_px:.2f} > {self.NEVER_REINFORCE_SUM_PX})'}

            needed = abs(imbalance)
            size_to_buy = max(5.0, math.ceil(needed))
            
            if imbalance < 0: # Need UP
                return {'action': 'ENTER', 'up_price': up_px, 'down_price': 0, 'up_size': size_to_buy, 'down_size': 0, 'size': size_to_buy, 'is_reconstruct': True, 'reason': f"FIXING_IMBALANCE_{phase}: {size_to_buy:.1f} UP"}
            else: # Need DOWN
                return {'action': 'ENTER', 'up_price': 0, 'down_price': down_px, 'up_size': 0, 'down_size': size_to_buy, 'size': size_to_buy, 'is_reconstruct': True, 'reason': f"FIXING_IMBALANCE_{phase}: {size_to_buy:.1f} DOWN"}

        # 2. Mode B: STRENGTHENING (Expansion / Scales)
        
        # Guard 1: Cool-down (Only for Expansion)
        time_since_done = now - self.last_straddle_completion_ts
        if time_since_done < self.EXPANSION_COOLDOWN_SEC:
            return {'action': 'IDLE', 'reason': f'EXPANSION_COOLDOWN ({time_since_done:.1f}s < {self.EXPANSION_COOLDOWN_SEC}s)'}

        # Guard 2: Absolute Firebreak
        if sum_px > self.NEVER_REINFORCE_SUM_PX:
            return {'action': 'IDLE', 'reason': f'EXPANSION_BLOCKED_FIREBREAK ({sum_px:.2f} > {self.NEVER_REINFORCE_SUM_PX})'}

        # Guard 3: Relative Edge (EWMA)
        if total_shares > 1.0: # Only if already holding something
            if self.ewma_sample_count < self.EWMA_WARMUP_SAMPLES:
                warmup_cap = 0.97
                if sum_px > warmup_cap:
                    return {'action': 'IDLE', 'reason': f'EWMA_WARMUP_ACTIVE ({self.ewma_sample_count}/{self.EWMA_WARMUP_SAMPLES}, Sum={sum_px:.2f} > {warmup_cap})'}
            else:
                required_edge = self.ewma_sum_px - 0.005
                if sum_px > required_edge:
                    return {'action': 'IDLE', 'reason': f'EWMA_EDGE_INSUFFICIENT (Sum={sum_px:.2f} > {required_edge:.3f})'}

        # Guard 4: Standard Entry Cap
        if sum_px > self.MAX_SUM_PRICE:
            return {'action': 'IDLE', 'reason': f'EXPANSION_BLOCKED_PX ({sum_px:.2f} > {self.MAX_SUM_PRICE})'}

        # Exposure Guards
        if total_usd_exposure >= (self.MAX_USD_EXPOSURE - 0.1) or total_shares >= (self.MAX_TOTAL_SHARES - 0.01):
            return {'action': 'IDLE', 'reason': 'CAPS_FULL'}

        # 3. Sizing & Scales Logic
        def calc_shares(price, limit):
            if price <= 0.01: return 0.0
            return max(5.0, math.floor(min(self.USD_PER_ORDER / price, limit)))

        cap_left = self.MAX_TOTAL_SHARES - total_shares
        limit_per_leg = cap_left / 2.0
        
        base_up = calc_shares(up_bid, limit_per_leg)
        base_down = calc_shares(down_bid, limit_per_leg)
        
        up_size = max(base_up, base_down)
        down_size = up_size
        
        reason = "EXPANSION_STRADDLE"
        
        # 🔴 Scales Logic (Inventory Skew)
        # Apply only if within tolerance but non-zero
        if 0 < abs(imbalance) <= self.rebalance_tolerance_shares:
            if imbalance < 0: # Have more DOWN, buy more UP
                up_size = min(up_size * self.MAX_SKEW_RATIO, limit_per_leg * 2.0)
                reason = "SCALES_SKEW_UP"
            else: # Have more UP, buy more DOWN
                down_size = min(down_size * self.MAX_SKEW_RATIO, limit_per_leg * 2.0)
                reason = "SCALES_SKEW_DOWN"

        # Execute
        return {
            'action': 'ENTER',
            'up_price': up_bid,
            'down_price': down_bid,
            'up_size': up_size,
            'down_size': down_size,
            'size': max(up_size, down_size),
            'reason': reason
        }
