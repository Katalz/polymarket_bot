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

class StraddleRebuildStrategy:
    """
    Rebuild-Only Strategy (Perpetual Reconstruction).
    - No Selling.
    - Strict Rebalance Priority.
    - Perpetual Rebuild (Buy missing leg).
    - Hard Cap & Edge Budget.
    """

    def __init__(self, slug: str, has_traded: bool = False, size_optimizer=None, bankroll_usd: float = 10000.0, initial_state: dict = None):
        self.slug = slug
        self.has_traded = has_traded # Used to prevent re-entry if strategy considers itself "DONE" for the market
        self.state = ReconstructState.IDLE
        
        # Params
        self.USD_PER_ORDER = 2.0
        # Strict Cap 50 shares (UP + DOWN). 
        # Allows Option B (Aggressive Reconstruct) to trigger: 10 + 5 existing = 15 cost.
        self.MAX_TOTAL_SHARES = 50.0
        self.OneLegTimeout = 15.0 # Seconds to wait in one leg before cancelling unfilled
        
        # 🔴 Edge Budget Guardrails
        # Synthetic Edge per share = 0.02 (Implies fair value sum ~0.98)
        # We ensure that (Cost + Existing_VWAP) <= 1.0 (Break-Event)
        self.MAX_SUM_PRICE = 0.99
        self.rebalance_tolerance_shares = 0.1 
        
        self.INITIAL_EDGE_SYNTH = 0.02 # Theoretical edge per share

        # Inventory
        self.filled_up_shares = 0.0
        self.filled_down_shares = 0.0
        
        # Order Tracking
        self.up_order_id = None
        self.down_order_id = None
        self.consecutive_zero_syncs = 0 # 🔴 Counter to prevent transient API lag wipes
        self.up_filled = False
        self.down_filled = False
        self.up_order_time = 0.0
        self.down_order_time = 0.0
        
        # 🔴 New Brake & Selectivity Mechanisms
        self.ewma_sum_px = None
        self.ewma_sample_count = 0
        self.EWMA_ALPHA = 0.033 # ~60s window at 1 tick/sec
        self.EWMA_WARMUP_SAMPLES = 60
        
        self.last_straddle_completion_ts = 0.0
        self.EXPANSION_COOLDOWN_SEC = 30.0
        
        self.NEVER_REINFORCE_SUM_PX = 1.01
        self.MAX_SKEW_RATIO = 2.0 # Max 2x size on one leg
        self.actual_size = 0.0
        
        # Persistence Help
        # One Leg Logic
        self.one_leg_start_ts = None
        self.last_cancel_ts = 0.0 # Throttling re-entry after cancel
        self.last_fill_ts = 0.0 # Tracking last fill time for sync protection
        self.SYNC_GRACE_PERIOD = 60.0 # Ignore API balance drops for 60s after fill
        
        # Logging Compatibility (for log_market_summary)
        self.entry_up_px = 0.0
        self.entry_down_px = 0.0
        self.actual_size = 0.0
        self._last_unwind_price = 0.0
        
        self.total_cost_up = 0.0 # For VWAP
        self.total_cost_down = 0.0 # For VWAP
        
        self.entry_sum_target = 0.0 
        self.is_reconstructing_order = False 
        self.last_reconstruct_cost = 0.0
        self.latest_reconstruct_debug = {}
        
        # Logging / Debug
        self.last_reprice_ts = 0.0
        self.actual_size = 0.0 # Last entry size
        
        if initial_state:
             print(f"[INIT] Restoring detailed state for {self.slug}...")
             self.filled_up_shares = initial_state.get("filled_up_shares", 0.0)
             self.filled_down_shares = initial_state.get("filled_down_shares", 0.0)
             self.total_cost_up = initial_state.get("total_cost_up", 0.0)
             self.total_cost_down = initial_state.get("total_cost_down", 0.0)
             self.has_traded = initial_state.get("has_traded", has_traded)
             print(f"       Restored: Inv={self.filled_up_shares}/{self.filled_down_shares} Cost={self.total_cost_up:.2f}/{self.total_cost_down:.2f}")

        print(f"[REBUILD] Init {self.slug}. Cap={self.MAX_TOTAL_SHARES}")

    def get_current_state(self) -> dict:
        """Return full state dictionary for persistence."""
        return {
            "has_traded": self.has_traded,
            "filled_up_shares": self.filled_up_shares,
            "filled_down_shares": self.filled_down_shares,
            "total_cost_up": self.total_cost_up,
            "total_cost_down": self.total_cost_down,
            "last_updated": time.time()
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
            # Detect imported inventory (increase without order)
            diff_up = max(0, up_shares - self.filled_up_shares)
            diff_down = max(0, down_shares - self.filled_down_shares)
            
            # Assume 0.50 cost base for imported shares to prevent "0 cost" skew
            if diff_up > 0: self.total_cost_up += diff_up * 0.50
            if diff_down > 0: self.total_cost_down += diff_down * 0.50
            
            print(f"[SYNC] Updating inventory: UP {self.filled_up_shares:.1f}->{up_shares:.1f}, DOWN {self.filled_down_shares:.1f}->{down_shares:.1f} (Assumed Cost 0.50 for new)")
            
        self.filled_up_shares = up_shares
        self.filled_down_shares = down_shares
        
        # Re-check state immediately after sync
        self._check_state_after_update()

    def get_log_data(self, up_bid, up_ask, down_bid, down_ask, now_ts, enter_result=None, sizing_debug=None):
        """Map internal state to CSV fields expected by runner."""
        
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
            
            # Selectivity Metrics
            "ewma_sum_px": self.ewma_sum_px or 0.0,
            "ewma_samples": self.ewma_sample_count,
            "last_completion_ts": self.last_straddle_completion_ts,
            "cooldown_remaining": max(0.0, self.EXPANSION_COOLDOWN_SEC - (time.time() - self.last_straddle_completion_ts)),
        
            "entry_sum_target": self.entry_sum_target,
            "initial_edge_per_share": 1.0 - self.entry_sum_target if self.entry_sum_target > 0 else 0.0,
            
            "avg_cost_up": self.total_cost_up / self.filled_up_shares if self.filled_up_shares > 0 else 0.0,
            "avg_cost_down": self.total_cost_down / self.filled_down_shares if self.filled_down_shares > 0 else 0.0,
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
    def _is_synthetic_profitable(cost_est, max_sum=1.00):
        """
        Check if the synthetic combination implies a guaranteed loss > break-even.
        cost_est here is sum of prices (e.g. 1.02).
        If cost_est > max_sum, we block.
        """
        return cost_est <= max_sum

    def on_order_canceled(self, order_id):
        if order_id == self.up_order_id:
            self.up_order_id = None
        if order_id == self.down_order_id:
            self.down_order_id = None
        self._check_state_after_update()

    def on_order_update(self, order_id, filled, fill_price=0.0, filled_size=0.0):
        # We handle incremental fills
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
                self.filled_up_shares += new_fill  # 🔴 Local Increment
                self.total_cost_up += new_fill * f_px
                self._last_sz_up = f_size
            
            if filled:
                self.up_filled = True
                self.up_order_id = None
                self._last_sz_up = 0.0

        elif is_down:
            new_fill = max(0.0, f_size - getattr(self, '_last_sz_down', 0.0))
            if new_fill > 0 and f_px > 0:
                self.filled_down_shares += new_fill  # 🔴 Local Increment
                self.total_cost_down += new_fill * f_px
                self._last_sz_down = f_size

            if filled:
                self.down_filled = True
                self.down_order_id = None
                self._last_sz_down = 0.0
        
        # Update last fill timestamp for sync protection
        if f_size > 0:
            self.last_fill_ts = time.time()
            
        self._check_state_after_update()

    def _check_state_after_update(self):
        """Update state based on fills and active orders."""
        orders_active = (self.up_order_id is not None) or (self.down_order_id is not None)
        
        if not orders_active:
            # Check inventory balance
            imbalance = self._compute_imbalance(self.filled_up_shares, self.filled_down_shares)
            
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
    
    def maybe_enter(self, up_bid, up_ask, down_bid, down_ask, time_remaining, available_balance=None, liquidity_depth=None, test_mode=False):
        """
        Strict Decision Logic:
        1. Time Freeze (< 3m) -> IDLE
        2. Imbalance -> Rebalance Only (using Ask prices)
        3. Balanced -> Reinforce Only (using Ask prices)
        """
        
        # 0. Active Order Protection
        if self.up_order_id or self.down_order_id:
            return {'action': 'IDLE', 'reason': 'ORDERS_OPEN_ALREADY'}

        # 1. IMBALANCE CHECK -> PRIORITY REBALANCE (Perpetual Rebuild)
        # We check this FIRST to bypass time gates if we need to fix an imbalance.
        imbalance = self.filled_up_shares - self.filled_down_shares
        is_unbalanced = abs(imbalance) > self.rebalance_tolerance_shares
        
        # Time Lock for Rebalance: strict stop only in last 30s
        if is_unbalanced and time_remaining < 30:
             return {'action': 'IDLE', 'reason': 'TIME_FREEZE_REBALANCE (<30s)'}
             
        # Time Lock for Reinforce (New Entries): strict stop < 3 min
        if not is_unbalanced and time_remaining < 180:
             return {'action': 'IDLE', 'reason': 'TIME_FREEZE_GATE (<3m)'}

        # 2. CAP CHECK
        total_shares = self.filled_up_shares + self.filled_down_shares
        # Allow small float epsilon, strictly block if cap reached.
        if total_shares >= (self.MAX_TOTAL_SHARES - 0.01):
             return {'action': 'IDLE', 'reason': 'CAP_FULL'}

        # 3. IF UNBALANCED, PROCEED TO REBALANCE LOGIC

        
        if is_unbalanced:
            # --- REBALANCE LOGIC ONLY ---
            # We NEVER reinforce if unbalanced.
            
            # Identify Missing Leg
            leg_dir = "DOWN" if imbalance > 0 else "UP" # Have UP > Need DOWN
            qty_needed = abs(imbalance)
            
            # Pricing (We BUY at ASK for immediate fill/maker aggressive)
            target_px = down_bid if leg_dir == "DOWN" else up_bid
            cost_check_px = down_ask if leg_dir == "DOWN" else up_ask # Conservative check
            
            # Get Avg Cost of Existing Leg
            avg_existing = 0.0
            if leg_dir == "DOWN":
                 avg_existing = self.total_cost_up / self.filled_up_shares if self.filled_up_shares > 0 else 0.5
            else:
                 avg_existing = self.total_cost_down / self.filled_down_shares if self.filled_down_shares > 0 else 0.5
            
            # Synthetic Cost Check (Edge Guardrail)
            synthetic_sum = cost_check_px + avg_existing
            
            # Deadlock Relief Logic for Rebuild
            # More permissive than BuyHold: We WANT to rebuild if imbalance exists.
            # But we still respect "overpay" limits based on Synthetic Cost.
            
            # Rebuild Limit Logic:
            # - Early (Time > 300s): Max Synth 1.01 (Small loss allowed to rebalance)
            # - Late (Time < 300s): Max Synth 1.03 (More aggressive to ensure closure)
            max_tolerated_sum = 1.01 if time_remaining > 300 else 1.03
            
            if synthetic_sum > max_tolerated_sum:
                 return {'action': 'IDLE', 'reason': f'REBUILD_BLOCKED_COST: Synth {synthetic_sum:.3f} > {max_tolerated_sum} (Cost:{avg_existing:.3f} + Ask:{cost_check_px:.3f})'}
                 
            # Execution
            return {
                'action': 'ENTER',
                'leg': leg_dir, # Caller must handle single leg logic or map to size
                'up_size': qty_needed if leg_dir == "UP" else 0.0,
                'down_size': qty_needed if leg_dir == "DOWN" else 0.0,
                'up_price': up_bid,
                'down_price': down_bid,
                'size': qty_needed,
                'reason': f"REBALANCE {leg_dir} Synth:{synthetic_sum:.3f}"
            }

        # 4. REINFORCE CHECK (Only if Balanced)
        # Condition: Time > 3 min (180s) - Already checked at start of function
        
        # Condition: Cost (Ask+Ask) < (Avg_Sum - 0.02)
        # Current Avg Sum
        avg_up = self.total_cost_up / self.filled_up_shares if self.filled_up_shares > 0 else 0.0
        avg_down = self.total_cost_down / self.filled_down_shares if self.filled_down_shares > 0 else 0.0
        
        # If we have no position, Avg_Sum is effectively 1.0 (start point) or we just use market sum
        if total_shares < 1.0:
             # First Entry Logic
             current_avg_sum = 1.00 # Virtual benchmark
        else:
             current_avg_sum = avg_up + avg_down
             
        market_cost_sum = up_ask + down_ask
        
        # Requirement: "Amélioration du coût synthétique"
        target_cost = current_avg_sum - 0.01
        
        # Also strictly CAP at 0.99 absolute
        target_cost = min(target_cost, 0.99)
        
        if market_cost_sum > target_cost:
             return {'action': 'IDLE', 'reason': f'REINFORCE_BLOCKED_PRICE: {market_cost_sum:.3f} > {target_cost:.3f}'}
             
        # Sizing (Small Chunks for Reinforce - $2 or Min 5 shares)
        # Use Helper calc
        chunk = 5.0 # Fixed small chunk
        
        # Cap check
        if total_shares + (chunk * 2) > self.MAX_TOTAL_SHARES:
             return {'action': 'IDLE', 'reason': 'REINFORCE_BLOCKED_CAP'}
             
        return {
             'action': 'ENTER',
             'up_size': chunk,
             'down_size': chunk,
             'up_price': up_bid,
             'down_price': down_bid,
             'size': chunk,
             'reason': f"REINFORCE_SYM Cost:{market_cost_sum:.3f}"
        }
