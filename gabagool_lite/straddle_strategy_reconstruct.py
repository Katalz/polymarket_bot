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

    def __init__(self, slug: str, has_traded: bool = False, size_optimizer=None, bankroll_usd: float = 10000.0):
        self.slug = slug
        self.has_traded = has_traded # Used to prevent re-entry if strategy considers itself "DONE" for the market
        self.state = ReconstructState.IDLE
        
        # Params
        self.USD_PER_ORDER = 2.0
        # Strict Cap 50 shares (UP + DOWN). 
        # Allows Option B (Aggressive Reconstruct) to trigger: 10 + 5 existing = 15 cost.
        self.MAX_TOTAL_SHARES = 50.0
        self.OneLegTimeout = 15.0 # Seconds to wait in one leg before cancelling unfilled
        self.MAX_SUM_PRICE = 0.98
        self.rebalance_tolerance_shares = 0.1 # Tolerance for considering straddle "balanced"

        # Inventory
        self.filled_up_shares = 0.0
        self.filled_down_shares = 0.0
        
        # Order Tracking
        self.up_order_id = None
        self.down_order_id = None
        self.up_filled = False
        self.down_filled = False
        self.up_order_time = 0.0
        self.down_order_time = 0.0
        
        # One Leg Logic
        self.one_leg_start_ts = None
        self.last_cancel_ts = 0.0 # Throttling re-entry after cancel
        self.last_fill_ts = 0.0 # Tracking last fill time for sync protection
        self.SYNC_GRACE_PERIOD = 30.0 # Ignore API balance drops for 30s after fill
        
        # Logging Compatibility (for log_market_summary)
        self.entry_up_px = 0.0
        self.entry_down_px = 0.0
        self.actual_size = 0.0
        self._last_unwind_price = 0.0
        
        # 🔴 Reconstruction Budget State (New Logic)
        self.RECONSTRUCTION_BUDGET_USD = 50.0
        self.RECONSTRUCT_CHUNK_SHARES = 1.0 # Default 1, but user allows 2
        
        self.reconstruction_budget = self.RECONSTRUCTION_BUDGET_USD # Fixed budget 50$
        self.reconstruction_spent = 0.0
        
        self.total_cost_up = 0.0 # For VWAP
        self.total_cost_down = 0.0 # For VWAP
        
        self.entry_sum_target = 0.0 
        self.is_reconstructing_order = False 
        self.last_reconstruct_cost = 0.0
        self.latest_reconstruct_debug = {}
        
        # Logging / Debug
        self.last_reprice_ts = 0.0
        self.actual_size = 0.0 # Last entry size
        
        self.last_reconstruct_cost = 0.0
        self.latest_reconstruct_debug = {}
        
        # Logging / Debug
        self.last_reprice_ts = 0.0
        self.actual_size = 0.0 # Last entry size
        
        print(f"[RECONSTRUCT] Init {self.slug}. Budget=${self.reconstruction_budget} Cap={self.MAX_TOTAL_SHARES}. Tolerance={self.rebalance_tolerance_shares}")
    def sync_state(self, up_shares: float, down_shares: float):
        """
        Synchronize internal inventory with external source (e.g. Wallet/API).
        This updates filled_up_shares/filled_down_shares to reflect reality.
        """
        now = time.time()
        
        # 🔴 Stale Sync Protection
        # If the API tries to lower our inventory shortly after a fill, we ignore it.
        # This prevents the "wiping" issue where API lag shows 0.0 balance.
        
        up_shares = float(up_shares)
        down_shares = float(down_shares)
        
        is_grace_period = (now - self.last_fill_ts < self.SYNC_GRACE_PERIOD)
        
        # Check UP
        if up_shares < self.filled_up_shares and is_grace_period:
            # print(f"[SYNC] Ignoring UP drop {self.filled_up_shares}->{up_shares} (In Grace Period)")
            up_shares = self.filled_up_shares
            
        # Check DOWN
        if down_shares < self.filled_down_shares and is_grace_period:
            # print(f"[SYNC] Ignoring DOWN drop {self.filled_down_shares}->{down_shares} (In Grace Period)")
            down_shares = self.filled_down_shares

        # Only update if changed to avoid log noise, or just update silently
        if abs(self.filled_up_shares - up_shares) > 0.01 or abs(self.filled_down_shares - down_shares) > 0.01:
            print(f"[SYNC] Updating inventory: UP {self.filled_up_shares:.1f}->{up_shares:.1f}, DOWN {self.filled_down_shares:.1f}->{down_shares:.1f}")
            
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
        self._check_state_after_update()

    def on_order_update(self, order_id, filled, fill_price=0.0, filled_size=0.0):
        if filled:
            f_size = float(filled_size)
            f_px = float(fill_price)
            
            # Identify leg and update VWAP stats
            is_up = (order_id == self.up_order_id)
            is_down = (order_id == self.down_order_id)
            
            # Update separate counts and costs
            # Note: Assuming this is called ONCE per filled quantity (e.g. order completion)
            if is_up and not self.up_filled:
                self.filled_up_shares += f_size
                self.total_cost_up += f_px * f_size
                self.up_filled = True
                self.up_order_id = None
            elif is_down and not self.down_filled:
                self.filled_down_shares += f_size
                self.total_cost_down += f_px * f_size
                self.down_filled = True
                self.down_order_id = None

            # Reconstruction Cost Tracking (New Logic)
            if self.is_reconstructing_order and f_size > 0 and f_px > 0:
                cost = 0.0
                if is_up:
                     # Reconstructing UP. avg_other is DOWN.
                     avg_down = self.total_cost_down / self.filled_down_shares if self.filled_down_shares > 0 else 0.5
                     overpay = (f_px + avg_down) - 1.0
                     cost = max(0.0, overpay) * f_size
                elif is_down:
                     # Reconstructing DOWN. avg_other is UP.
                     avg_up = self.total_cost_up / self.filled_up_shares if self.filled_up_shares > 0 else 0.5
                     overpay = (f_px + avg_up) - 1.0
                     cost = max(0.0, overpay) * f_size
                
                if cost > 0:
                    self.reconstruction_spent += cost
                    self.last_reconstruct_cost = cost
            
            # Update last fill timestamp for sync protection
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
        Main decision logic.
        Handles both Initial Entry and Rebalancing.
        """
        
        # 1. Cap Check
        total_shares = self.filled_up_shares + self.filled_down_shares
        
        # 🔴 Critical Point #1 — Cap Full Handling (No Overbuy Ever)
        # If inv_up + inv_down >= MAX_CAP (allow small float error)
        if total_shares >= (self.MAX_TOTAL_SHARES - 0.01):
            return {'action': 'IDLE', 'reason': 'CAP_FULL'}
            
        # 2. Imbalance Check
        imbalance = self.filled_up_shares - self.filled_down_shares
        # Positive = More UP = Need DOWN
        # Negative = More DOWN = Need UP
        
        is_one_leg = abs(imbalance) > self.rebalance_tolerance_shares
        
        if is_one_leg:
            # 🔴 Critical Point #2 — ONE_LEG_INVENTORY Lockdown
            # Use strict compute_reconstruct_orders logic
            
            # 🔴 Critical Point #2 — ONE_LEG_INVENTORY Lockdown (New Chunked/Budget Logic)
            imbalance = self._compute_imbalance(self.filled_up_shares, self.filled_down_shares)
            chunk_base = min(self.RECONSTRUCT_CHUNK_SHARES, abs(imbalance))
            
            # Additional Cap Check for Rebalance Size
            cap_left = self.MAX_TOTAL_SHARES - total_shares
            chunk = min(chunk_base, cap_left)

            if abs(imbalance) <= self.rebalance_tolerance_shares:
                return {'action': 'IDLE', 'reason': "RECONSTRUCT_BALANCED"}
                
            b_up, b_down = 0.0, 0.0
            avg_other = 0.0
            px_missing_now = 0.0
            
            if imbalance > self.rebalance_tolerance_shares: # Have UP, Need DOWN
                # We hold UP, so avg_other is self.total_cost_up / filled_up
                avg_other = self.total_cost_up / self.filled_up_shares if self.filled_up_shares > 0 else 0.5
                px_missing_now = down_bid # Target Maker Bid
                
                # Enforce Min Limits (Value $1 and Size 5)
                # API Error: "Size (1.26) lower than the minimum: 5"
                if px_missing_now > 0.001:
                    min_val_shares = 1.05 / px_missing_now
                    min_qty_shares = 5.0
                    req_chunk = max(min_val_shares, min_qty_shares)
                    
                    if chunk < req_chunk:
                         chunk = req_chunk
                b_down = chunk
            elif imbalance < -self.rebalance_tolerance_shares: # Have DOWN, Need UP
                avg_other = self.total_cost_down / self.filled_down_shares if self.filled_down_shares > 0 else 0.5
                px_missing_now = up_bid # Target Maker Bid
                
                # Enforce Min Limits (Value $1 and Size 5)
                if px_missing_now > 0.001:
                    min_val_shares = 1.05 / px_missing_now
                    min_qty_shares = 5.0
                    req_chunk = max(min_val_shares, min_qty_shares)
                    
                    if chunk < req_chunk:
                         chunk = req_chunk
                b_up = chunk
            
            overpay_limit = self._max_overpay_per_share(time_remaining)
            # overpay = (AvgExisting + PriceMissing) - 1.0
            # If > 0, we are paying > 1.0 total (losing money).
            overpay_per_share = (avg_other + px_missing_now) - 1.0
            cost_est = self._estimate_reconstruct_cost(avg_other, px_missing_now, chunk)
            
            budget_left = max(0.0, self.reconstruction_budget - self.reconstruction_spent)
            
            can_execute, block_reason = self._can_reconstruct(cost_est, overpay_per_share, budget_left, overpay_limit)
            
            # Save Metrics for Logging
            self.latest_reconstruct_debug = {
                "imbalance_shares": imbalance,
                "avg_other_price": avg_other,
                "px_missing_now": px_missing_now,
                "overpay_per_share": overpay_per_share,
                "reconstruct_chunk_shares": chunk,
                "reconstruct_cost_est_usd": cost_est,
                "reconstruct_overpay_limit": overpay_limit,
                "reconstruct_block_reason": block_reason if not can_execute else "OK"
            }
            
            if not can_execute:
                return {
                    'action': 'IDLE', 
                    'reason': f"RECONSTRUCT_BLOCKED: {block_reason} (Op:{overpay_per_share:.4f} > L:{overpay_limit:.4f}, Cost:${cost_est:.2f})"
                }
            
            return {
                'action': 'ENTER',
                'up_price': up_bid,
                'down_price': down_bid,
                'up_size': b_up,
                'down_size': b_down,
                'size': max(b_up, b_down), 
                'is_reconstruct': True,
                'reason': f"RECONSTRUCT_CHUNK: {b_up:.1f}/{b_down:.1f} (Op:{overpay_per_share:.4f} B:${budget_left:.2f})"
            }
            
        # 3. Standard Balanced Entry (Symmetric)
        trade_leg = "BOTH"
        
        # 4. Market Check (Spread)
        sum_px = up_bid + down_bid
        if sum_px > self.MAX_SUM_PRICE:
            return {'action': 'IDLE', 'reason': f'Spread high {sum_px:.2f} > {self.MAX_SUM_PRICE}'}
            
        # 5. Sizing
        # Helper for size (Symmetric logic same as before)
        def calc_shares(price, limit_shares):
            if price <= 0.01: return 0.0 # Safety
            raw = self.USD_PER_ORDER / price
            
            # Enforce Min Size 5 (Polymarket constraint)
            target = max(raw, 5.0)
            
            final_count = math.floor(target)
            
            # Check against Limit (Cap)
            if final_count > limit_shares:
                # If we are capped below the minimum 5, we CANNOT trade new positions.
                if limit_shares < 5.0:
                    return 0.0
                return math.floor(limit_shares)
            
            return final_count

        cap_left = self.MAX_TOTAL_SHARES - total_shares
        limit_per_leg = cap_left / 2.0
        
        # Calculate raw shares independently based on $2 sizing
        raw_up = calc_shares(up_bid, limit_per_leg)
        raw_down = calc_shares(down_bid, limit_per_leg)
        
        # 🔴 Critical Point #3 — Unified Symmetric Entry
        # User Requirement: "First leg must be equilibrium".
        # We take the MAX of the two (to ensure meaningful exposure on cheaper leg)
        # But we must clamp it to the limit_per_leg again to be safe.
        
        unified_size = max(raw_up, raw_down)
        
        if unified_size > limit_per_leg:
             unified_size = math.floor(limit_per_leg)
             
        up_size = unified_size
        down_size = unified_size
        
        if up_size < 1.0 or down_size < 1.0:
            return {'action': 'IDLE', 'reason': 'TEST_SIZE_TOO_SMALL (BOTH)'}

        # 6. Execute (Standard)
        return {
            'action': 'ENTER',
            'up_price': up_bid,
            'down_price': down_bid,
            'up_size': up_size,
            'down_size': down_size,
            'size': max(up_size, down_size), 
            'reason': f"Leg:{trade_leg} UP:{up_size} DOWN:{down_size}"
        }
