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
    HEDGE_PENDING = "HEDGE_PENDING"     # New: Trying to complete via taker
    FLATTENING = "FLATTENING"           # New: Trying to unwind via taker
    FAILED_FLATTEN = "FAILED_FLATTEN"   # New: Critical failure state
    STRADDLE_COMPLETE = "STRADDLE_COMPLETE" # Both legs filled
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
        
        # H3: Signature
        print(f"[DEBUG_H3] Strategy Loaded: {self.slug} (Sig: {time.time()})")

        # SIZE LOCK VARIABLES (Strict Invariants)
        self.target_size_shares: float = 0.0
        self.filled_up_shares: float = 0.0
        self.filled_down_shares: float = 0.0
        self.one_leg_start_ts = None
        self.unwind_in_flight = False # Idempotency flag
        
        # Orders tracking (The Ledger)
        self.up_order_id = None
        self.down_order_id = None
        self.filled_up_shares = 0.0
        self.filled_down_shares = 0.0
        self.filled_up_price = 0.0
        self.filled_down_price = 0.0
        
        # Params
        # Params
        # Params
        self.TIMEOUT_ONE_LEG = 60.0  # Extended timeout for "Construction" Mode
        self.EPS = 1e-9
        self.MIN_SPREAD = 0.015
        self.MAX_SPREAD = 0.20
        self.ACTIVITY_TIMEOUT = 8.0
        self.PHASE1_DURATION = 6.0   # Maker Window (6s)
        self.ENTRY_AGGRESSIVENESS_TICKS = 0 # STRICT MAKER (0 ticks)
        self.PHASE1_COOLDOWN_SEC = 10.0 # Cooldown on fail

        self.PHASE1_COOLDOWN_SEC = 5.0

        self.phase1_timeout_ts = 0.0 # Track last timeout for cooldown


        self.ONE_LEG_RETRY_TS = 6.0
        self.ONE_LEG_CUT_TS = 12.0
        
        # State additions for activity tracking
        self.last_up_bid = 0.0
        self.last_down_bid = 0.0
        self.last_up_ask = 0.0
        self.last_down_ask = 0.0
        self.last_activity_ts = 0.0
        
        # New State Flags
        self.one_leg_retry_used = False

        
        # Config (Classic)
        self.MAX_SUM_PRICE = 0.98       # Entry target (profit buffer)
        self.ONE_LEG_BE_SUM_CAP = 0.99  # Absolute break-even cap for one-leg recovery
        self.ENTRY_CUTOFF_SEC = 240     # Don't enter if less than 4 mins remaining
        self.MIN_TIME_REMAINING = 300   # (Or 5 mins? Logic uses one of them)
        self.MIN_PROFIT_SPREAD = 0.015  # User: min_spread = 0.015
        self.TICK_SIZE = 0.01
        self.SIZE_MULTIPLIER = 1.0
        self.MIN_LEG_PRICE = 0.05       # Minimum price to avoid dust/dead legs
        self.MAX_LEG_PRICE = 0.95       # Max price to avoid locking capital in winners
        self.CROSS_TOLERANCE = 0.0      # Should not cross
        self.MIN_PROFIT_PER_SHARE = 0.02 # User: 0.02 Min Profit
        
        # Volatility Gate Params
        self.SUM_JUMP_TICKS = 0.019
        self.LEG_JUMP_TICKS = 0.05
        self.MAX_SUM_PRICE_CAP = 1.02
        
        # Timeout Params
        self.FORCE_FLATTEN_SEC = 240
        self.ORDER_TIMEOUT_SEC = 20.0   # Keep entry timeout shortish, but logic relies on Phase 1
        self.MAX_USD_PER_MARKET = 100.0 
        
        # PROMPT OPTIMIZATION VARS
        self.MIN_REPRICE_INTERVAL_SEC = 10.0 # Slow Repricing
        self.MAX_REPRICE_ATTEMPTS = 0      # No Repricing in Entry
        self.ONE_LEG_PHASE_1_SEC = 20.0    # Phase 1: Stabilization (20s) HOLD
        self.ONE_LEG_PHASE_2_SEC = 60.0    # Phase 2: Construction (Wait)
        
        # ONE-LEG DETERMINISTIC STATE MACHINE GLOBALS
        self.one_leg_cycle_count = None
        self.one_leg_initial_distance = 0.0
        self.one_leg_distance_now = 0.0
        self.one_leg_phase = "INIT"





        
        # Order Tracking Legacy (if used outside)
        # Order Tracking Legacy (if used outside)
        self.up_order_time: float = 0.0
        self.down_order_time: float = 0.0
        self.up_filled: bool = False
        self.down_filled: bool = False
        self.size_locked_blocked: bool = False
        self.overbuy_attempted: bool = False
        self.optimal_size: int = 0
        self.actual_size: int = 0
        self.cumulative_up_shares: float = 0.0
        self.cumulative_down_shares: float = 0.0
        
        # Legacy/Logging State
        self.reprice_attempt_count: int = 0
        self.last_reprice_ts: float = 0.0
        self.one_leg_fill_time: float = 0.0

        self.log_once_map = {} 
        
        # Snapshots
        self.entry_up_ask = 0.0
        self.entry_down_ask = 0.0
        self.entry_up_bid = 0.0
        self.entry_down_bid = 0.0
        self.entry_up_px = 0.0
        self.entry_down_px = 0.0
        self.entry_sum_px = 0.0
        self.entry_profit = 0.0
        
        # Safety/Fallback defaults
        self.MAX_ONE_LEG_SEC_FAST = 30.0
        self.IMBALANCE_THRESHOLD = 0.9 # Should be < 1.0 to catch >0 imbalance
        self._last_cancel_reason = ""
        self._last_unwind_method = ""
        self._last_unwind_price = 0.0
        self._last_unwind_result = ""
        self._taker_path_attempted = False
        self._last_reprice_reason = ""
        self.taker_fallback = True # User: taker_fallback = True

    def on_order_update(self, order_id, filled, fill_price=0.0, filled_size=0.0):
        """
        Single Source of Truth update.
        Called by WebSocket or REST poller.
        """
        price = fill_price # Alias

        # 1. Update quantities
        if order_id == self.up_order_id:
            self.filled_up_shares = float(filled_size)
            if price > 0: self.filled_up_price = float(price)
        elif order_id == self.down_order_id:
            self.filled_down_shares = float(filled_size)
            if price > 0: self.filled_down_price = float(price)

        # Update flags (Wait, target_size might be 0 if not set, but standard flow sets it)
        if self.target_size_shares > 0:
            self.up_filled = (self.filled_up_shares >= self.target_size_shares - 0.1)
            self.down_filled = (self.filled_down_shares >= self.target_size_shares - 0.1)

            
        # 2. Evaluate State
        delta = abs(self.filled_up_shares - self.filled_down_shares)
        
        # Just in case we finished
        if (abs(self.filled_up_shares - self.target_size_shares) < 0.1 and 
            abs(self.filled_down_shares - self.target_size_shares) < 0.1):
            self.state = StraddleState.STRADDLE_COMPLETE
            self.one_leg_start_ts = None
            return

        # Check One Leg trigger
        if delta > 0.5: # Tolerance for rounding
            if self.state != StraddleState.ONE_LEG_FILLED:
                self.state = StraddleState.ONE_LEG_FILLED
                if self.one_leg_start_ts is None:
                    self.one_leg_start_ts = time.time()
                    self.one_leg_cycle_count = None # Reset for fresh cycle logic
                    self.log_once_map = {}
                    self.one_leg_retry_used = False
                    print(f"[ONE_LEG] Entered State. t=0. LegDelta={delta:.2f}")
        else:
            # Back to balance (or 0-0)
            if self.filled_up_shares > 0:
                # Balanced fill (partial or full logic handled above)
                self.one_leg_start_ts = None # Reset timer if we re-balanced
            
    def get_management_action(self, now_ts, up_bid=0.0, up_ask=0.0, down_bid=0.0, down_ask=0.0, time_remaining=9999.0):
        """
        Deterministic ONE-LEG State Machine.
        """
        if self.state != StraddleState.ONE_LEG_FILLED:
            # Handle Repricing Logic for ORDERS_OPEN
            if self.state == StraddleState.ORDERS_OPEN:
                # Anti-Churn Logic
                if self.reprice_attempt_count >= self.MAX_REPRICE_ATTEMPTS:
                     return {"action": "WAIT", "reason": "Max reprice attempts reached"}
                
                if now_ts - self.last_reprice_ts < self.MIN_REPRICE_INTERVAL_SEC:
                     return {"action": "WAIT", "reason": "Min reprice interval"}
                     
                # Add reprice logic if needed (currently main loop doesn't reprice aggressively)
                # Ideally we check here if we need to reprice. 
                # For this request, we just enforce the limits if reprice WAS attempted.
                # Assuming 'reprice logic' is external or implicit.
                # Actually, main loop logic calls this. 
                pass

            # Handle Phase 1 Entry Timeout (Maker 6s)
            if self.state == StraddleState.ORDERS_OPEN:
                 elapsed_entry = now_ts - self.up_order_time # Approximation
                 if elapsed_entry > self.PHASE1_DURATION:
                     # Phase 1 Expired. 0 fills -> Cancel.
                     if not self.up_filled and not self.down_filled:
                         print(f"[ENTRY_PHASE] Phase 1 Expired without fills. Cancelling. Cooldown {self.PHASE1_COOLDOWN_SEC}s.")
                         self.phase1_timeout_ts = time.time() # Start cooldown
                         return {"action": "TIMEOUT_CUT", "reason": f"Phase 1 Entry Timeout ({self.PHASE1_DURATION}s)"}
            return {"action": "NONE"}

        elapsed = now_ts - (self.one_leg_start_ts if self.one_leg_start_ts else now_ts)

        # 0. Identify Legs and Distance
        if self.filled_up_shares > self.filled_down_shares:
             # Filled UP, need DOWN.
             my_limit = self.entry_down_px
             market_price = down_ask
             spread = down_ask - down_bid
             leg_needed = "DOWN"
        else:
             # Filled DOWN, need UP.
             my_limit = self.entry_up_px
             market_price = up_ask
             spread = up_ask - up_bid
             leg_needed = "UP"

        # Distance calculation: abs(limit - market_price)
        distance = abs(my_limit - market_price)
        
        # Init State if needed
        if self.one_leg_cycle_count is None:
            self.one_leg_cycle_count = 0
            print(f"[ONE_LEG] START t=0.0s dist={distance:.3f} Leg={leg_needed}")
            self.reprice_attempt_count = 0 # Reset reprice count on fill

        # ---------------------------------------------------------
        # PHASE 1: STABILIZATION (0 - 20s)
        # ---------------------------------------------------------
        if elapsed < self.ONE_LEG_PHASE_1_SEC:
             self.one_leg_phase = "STABILIZATION"
             # "On garde la jambe déjà remplie. On ne vend RIEN. L'autre jambe reste en maker au bid."
             if int(elapsed) % 5 == 0 and f"wait_{int(elapsed)}" not in self.log_once_map:
                 print(f"[ONE_LEG] t={elapsed:.1f}s STABILIZATION (Hold & Wait). Dist={distance:.3f}")
                 self.log_once_map[f"wait_{int(elapsed)}"] = True
             return {"action": "WAIT", "reason": f"Stabilization Hold {elapsed:.1f}/{self.ONE_LEG_PHASE_1_SEC}s"}

        # ---------------------------------------------------------
        # PHASE 2: CONSTRUCTION (20s - 60s...)
        # ---------------------------------------------------------
        # "On tente de remplir l'autre jambe par petites tailles. Toujours en maker. Jamais au mid. Jamais en taker."
        # We assume the order is already placed at Bid by the main loop/strategy initial placement.
        # We just WAIT.
        
        self.one_leg_phase = "CONSTRUCTION"
        
        # TAKER EXCEPTION: ONLY IF TIME < 60s
        if time_remaining < 60.0:
             # Check Price Cap
             unfilled_ask = down_ask if leg_needed == "DOWN" else up_ask
             filled_px = self.filled_up_price if leg_needed == "DOWN" else self.filled_down_price
             if filled_px + unfilled_ask <= 1.02:
                  return {
                      "action": "TAKER_COMPLETE",
                      "leg": leg_needed,
                      "qty": self.get_remaining_qty(leg_needed),
                      "price": unfilled_ask,
                      "reason": "End of Market Taker Completion"
                  }

        if int(elapsed) % 5 == 0 and f"wait_{int(elapsed)}" not in self.log_once_map:
             print(f"[ONE_LEG] t={elapsed:.1f}s CONSTRUCTION MODE. Dist={distance:.3f}")
             self.log_once_map[f"wait_{int(elapsed)}"] = True
             
        # Strict Wait
        return {"action": "WAIT", "reason": "Construction Mode - Waiting for Maker Fill"}




    # =========================================================================
    # SIZE LOCK METHODS - STRICT INVARIANT ENFORCEMENT
    # =========================================================================

    def get_remaining_qty(self, leg: str) -> float:
        """
        Calculate remaining quantity allowed for a leg.
        
        Args:
            leg: 'UP' or 'DOWN'
            
        Returns:
            Remaining shares allowed (target - filled), never negative
        """
        if self.target_size_shares <= 0:
            return 0.0
            
        if leg == 'UP':
            remaining = self.target_size_shares - self.filled_up_shares
        else:
            remaining = self.target_size_shares - self.filled_down_shares
            
        return max(0.0, remaining)

    def can_place_order(self, leg: str, requested_qty: float) -> tuple:
        """
        Check if an order can be placed without exceeding size lock.
        
        Args:
            leg: 'UP' or 'DOWN'
            requested_qty: Quantity to order
            
        Returns:
            (can_place: bool, allowed_qty: float, reason: str)
        """
        remaining = self.get_remaining_qty(leg)
        
        # Case 1: No remaining capacity
        if remaining <= 0:
            self.size_locked_blocked = True
            print(f"[SIZE-LOCK] ❌ BLOCKED: {leg} remaining={remaining:.2f}, requested={requested_qty:.2f}")
            return (False, 0.0, f"SIZE_LOCK: {leg} already at target ({self.target_size_shares})")
        
        # Case 2: Requested more than remaining
        if requested_qty > remaining + 1e-6:  # Small epsilon for float comparison
            self.overbuy_attempted = True
            print(f"[SIZE-LOCK] ⚠️ OVERBUY ATTEMPT: {leg} requested={requested_qty:.2f} > remaining={remaining:.2f}")
            # Allow up to remaining, not more
            return (True, remaining, f"SIZE_LOCK: Capped {leg} from {requested_qty:.2f} to {remaining:.2f}")
        
        # Case 3: Valid request
        return (True, requested_qty, "OK")

    def detect_overbuy(self):
        """
        Check if any leg has exceeded target size. If so, log ERROR and return True.
        """
        if self.target_size_shares <= 0:
            return False
            
        overbuy_up = self.filled_up_shares > self.target_size_shares + 1e-6
        overbuy_down = self.filled_down_shares > self.target_size_shares + 1e-6
        
        if overbuy_up or overbuy_down:
            print(f"[SIZE-LOCK] 🚨 ERROR OVERBUY DETECTED!")
            print(f"[SIZE-LOCK] Target: {self.target_size_shares}")
            print(f"[SIZE-LOCK] Filled UP: {self.filled_up_shares} | Filled DOWN: {self.filled_down_shares}")
            self.overbuy_attempted = True
            return True
            
        return False

    def round_to_tick(self, price: float, tick_size: float) -> float:
        """Round price to nearest tick size."""
        if tick_size == 0 or price == 0: return price
        return round(price / tick_size) * tick_size

    def floor_to_tick(self, price: float, tick_size: float) -> float:
        """Floor price to nearest tick size (always down)."""
        if tick_size == 0: return price
        # Use small epsilon for float precision
        return int((price + 1e-9) / tick_size) * tick_size

    def maybe_enter(self, up_bid: float, up_ask: float, down_bid: float, down_ask: float,
                   time_remaining: float, available_balance: Optional[float] = None,
                   liquidity_depth: Optional[float] = None, test_mode: bool = False) -> Dict[str, Any]:
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
            return {'action': 'IDLE', 'reason': 'Already traded this market', 'blocked_maker': False,
                   'near_resolution_blocked': False, 'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC}

        if self.state != StraddleState.IDLE:
            return {'action': 'IDLE', 'reason': f'Already in state {self.state.value}', 'blocked_maker': False,
                   'near_resolution_blocked': False, 'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC}

        # Cooldown Check
        if time.time() - self.phase1_timeout_ts < self.PHASE1_COOLDOWN_SEC:
             return {
                 'action': 'IDLE',
                 'reason': f'COOLDOWN: Phase 1 Timeout ({self.PHASE1_COOLDOWN_SEC}s)',
                 'blocked_maker': False,
                 'near_resolution_blocked': False, 
                 'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC
             }

        if time_remaining <= 0:
            return {'action': 'IDLE', 'reason': 'Market expired', 'blocked_maker': False,
                   'near_resolution_blocked': False, 'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC}

        # NEAR RESOLUTION SAFETY: Block new entries too close to resolution
        near_resolution_blocked = False
        if time_remaining < self.ENTRY_CUTOFF_SEC:
            return {
                'action': 'IDLE',
                'reason': f'SKIP_NEAR_RESOLUTION: {time_remaining:.0f}s < {self.ENTRY_CUTOFF_SEC}s cutoff',
                'blocked_maker': False,
                'near_resolution_blocked': True,
                'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC
            }
            
        # -------------------------------------------------------------------------
        # 0) MARKET HEALTH/ACTIVITY CHECKS (Strict)
        # -------------------------------------------------------------------------
        # Activity Check (> 8s)
        # We need to detect if quotes are stale.
        now = time.time()
        activity_detected = False
        
        # Check against last seen
        if (abs(up_bid - self.last_up_bid) > self.EPS or
            abs(down_bid - self.last_down_bid) > self.EPS or
            abs(up_ask - self.last_up_ask) > self.EPS or
            abs(down_ask - self.last_down_ask) > self.EPS):
            activity_detected = True
            self.last_activity_ts = now
            self.last_up_bid = up_bid
            self.last_down_bid = down_bid
            self.last_up_ask = up_ask
            self.last_down_ask = down_ask
            
        if now - self.last_activity_ts > self.ACTIVITY_TIMEOUT:
             print(f"[SPREAD_BLOCK] Market inactive > {self.ACTIVITY_TIMEOUT}s. SKIP.")
             return {'action': 'IDLE', 'reason': 'MARKET_INACTIVE', 'blocked_maker': False}

        # Spread Check (0.04 - 0.20)
        spread_up = up_ask - up_bid
        spread_down = down_ask - down_bid
        
        # Max of both spreads or just generally? Strict: "Ne JAMAIS entrer si spread < 0.04"
        # Implies ANY spread? Or both? Usually we care about the legs we trade.
        # Let's check worst case (max spread) or best case?
        # A tight spread < 0.04 is "bad" for Maker? Yes.
        # A wide spread > 0.20 is "broken".
        
        if spread_up < self.MIN_SPREAD or spread_down < self.MIN_SPREAD:
             print(f"[SPREAD_BLOCK] Spread too tight: UP={spread_up:.3f} DOWN={spread_down:.3f} < {self.MIN_SPREAD}")
             return {'action': 'IDLE', 'reason': 'SPREAD_TOO_TIGHT', 'blocked_maker': False}

        if spread_up > self.MAX_SPREAD or spread_down > self.MAX_SPREAD:
             print(f"[SPREAD_BLOCK] Spread too wide: UP={spread_up:.3f} DOWN={spread_down:.3f} > {self.MAX_SPREAD}")
             return {'action': 'IDLE', 'reason': 'SPREAD_TOO_WIDE', 'blocked_maker': False}

        print("[ENTRY_PHASE] MAKER (Spread OK, Activity OK)")

        # Find optimal maker prices (intended prices before rounding)
        # PASSIVE CONSTRUCTION: STRICT MAKER (BID + 0)
        
        intended_up_price = up_bid
        intended_down_price = down_bid
        
        # STRICT MAKER: Never > Bid
        # We do not use ticks or aggression.
        
        # Round to tick size
        up_price = round_to_tick(intended_up_price, self.TICK_SIZE)
        down_price = round_to_tick(intended_down_price, self.TICK_SIZE)

        # -------------------------------------------------------------------------
        # 1) HARD PRICE BOUNDS (Per Leg)
        # -------------------------------------------------------------------------
        blocked_price_bounds = False
        if up_price < self.MIN_LEG_PRICE or up_price > self.MAX_LEG_PRICE:
            blocked_price_bounds = True
            return {
                'action': 'IDLE',
                'reason': f'SKIP_PRICE_OUT_OF_BOUNDS: UP {up_price:.2f} (Limits: {self.MIN_LEG_PRICE}-{self.MAX_LEG_PRICE})',
                'blocked_maker': False,
                'blocked_price_bounds': True,
                'near_resolution_blocked': False,
                'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC
            }
            
        if down_price < self.MIN_LEG_PRICE or down_price > self.MAX_LEG_PRICE:
            blocked_price_bounds = True
            return {
                'action': 'IDLE',
                'reason': f'SKIP_PRICE_OUT_OF_BOUNDS: DOWN {down_price:.2f} (Limits: {self.MIN_LEG_PRICE}-{self.MAX_LEG_PRICE})',
                'blocked_maker': False,
                'blocked_price_bounds': True,
                'near_resolution_blocked': False,
                'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC
            }

        # -------------------------------------------------------------------------
        # 2) PROFIT CHECK (Profit per share)
        # -------------------------------------------------------------------------
        profit_both = 1.0 - (up_price + down_price)
        blocked_low_profit = False
        if profit_both < self.MIN_PROFIT_PER_SHARE:
            blocked_low_profit = True
            return {
                'action': 'IDLE',
                'reason': f'SKIP_LOW_PROFIT: Profit {profit_both:.3f} < {self.MIN_PROFIT_PER_SHARE}',
                'blocked_maker': False,
                'blocked_low_profit': True,
                'near_resolution_blocked': False,
                'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC,
                'profit_both': profit_both
            }

        # Check sum constraint
        if up_price + down_price > self.MAX_SUM_PRICE:
            return {
                'action': 'IDLE',
                'reason': f'Sum too high: {up_price:.2f} + {down_price:.2f} = {up_price + down_price:.2f} > {self.MAX_SUM_PRICE}',
                'blocked_maker': False,
                'near_resolution_blocked': False,
                'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC
            }

        # MAKER GUARD: Strict check that limit_price < best_ask
        blocked_maker = False
        if up_price >= up_ask:
            blocked_maker = True
            return {
                'action': 'IDLE',
                'reason': f'MAKER_BLOCK: UP price {up_price:.2f} >= ask {up_ask:.2f} (rounded vs snapshot)',
                'blocked_maker': True,
                'near_resolution_blocked': False,
                'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC
            }

        if down_price >= down_ask:
            blocked_maker = True
            return {
                'action': 'IDLE',
                'reason': f'MAKER_BLOCK: DOWN price {down_price:.2f} >= ask {down_ask:.2f} (rounded vs snapshot)',
                'blocked_maker': True,
                'near_resolution_blocked': False,
                'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC
            }

        # SIZE OPTIMIZATION: Find optimal size with EV maximization
        optimal_size, sizing_debug = self.size_optimizer.optimize_size(
            up_price=up_price,
            down_price=down_price,
            bankroll_usd=self.bankroll_usd,
            available_balance=available_balance,
            liquidity_depth=liquidity_depth,
            test_mode=test_mode
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

        # Store snapshot values for consistent logging through the trade lifecycle
        self.entry_up_px = up_price
        self.entry_down_px = down_price
        self.entry_sum_px = up_price + down_price
        self.entry_profit = profit_both
        self.entry_max_leg_price = max(up_price, down_price)
        
        # Initialize current working prices
        self.current_up_px = up_price
        self.current_down_px = down_price
        
        # Capture placement-time orderbook state
        self.entry_up_ask = up_ask
        self.entry_down_ask = down_ask
        self.entry_up_bid = up_bid
        self.entry_down_bid = down_bid

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
            'intended_price_down': intended_down_price,
            'rounded_price_down': down_price,
            'entry_aggressiveness_ticks': self.ENTRY_AGGRESSIVENESS_TICKS,
            'up_ask_at_send': up_ask,
            'down_ask_at_send': down_ask,
            'up_bid_at_send': up_bid,
            'down_bid_at_send': down_bid,
            'blocked_maker': False,
            'near_resolution_blocked': False,
            'entry_cutoff_sec': self.ENTRY_CUTOFF_SEC,
            'sizing_debug': sizing_debug,
            'profit_both': profit_both,
            'price_bounds_ok': True,
            'profit_ok': True
        }

    def on_orders_placed(self, up_order_id: str, down_order_id: str, actual_size: Optional[int] = None):
        """Called when both orders are successfully placed."""
        self.up_order_id = up_order_id
        self.down_order_id = down_order_id
        self.up_order_time = time.time()
        self.down_order_time = time.time()
        self.actual_size = actual_size or self.optimal_size
        self.state = StraddleState.ORDERS_OPEN
        
        # RATE LIMIT LOCK
        if self.target_size_shares == 0:
            self.target_size_shares = float(self.actual_size)
            print(f"[SIZE-LOCK] Target Size Locked: {self.target_size_shares}")
        elif self.actual_size > self.target_size_shares:
             print(f"[SIZE-LOCK] ERROR: Attempted to increase size from {self.target_size_shares} to {self.actual_size}. Flagging overbuy.")
             self.overbuy_attempted = True


    def on_order_canceled(self, order_id: str):
        """Called when an order is cancelled."""
        if order_id == self.up_order_id:
            self.up_order_id = None
        elif order_id == self.down_order_id:
            self.down_order_id = None
            
        # If both orders are gone and no fills, reset to IDLE
        if not self.up_order_id and not self.down_order_id:
            if not self.up_filled and not self.down_filled:
                if self.state != StraddleState.STRADDLE_COMPLETE:
                     self.state = StraddleState.IDLE

        elif order_id == self.down_order_id:
            self.down_order_id = None
            
        # If both orders are gone and no fills, reset to IDLE
        if not self.up_order_id and not self.down_order_id:
            if not self.up_filled and not self.down_filled:
                self.state = StraddleState.IDLE


    def should_cancel_timeout(self, time_remaining: float) -> Tuple[bool, Optional[str], str]:
        """
        Check if any open orders should be cancelled due to timeout.

        Args:
            time_remaining: Time remaining until market resolution

        Returns: (should_cancel, order_id, reason)
        """
        now = time.time()

        # FORCE FLATTEN: If near resolution and one leg filled, cancel unfilled immediately
        if self.state == StraddleState.ORDERS_OPEN and time_remaining < self.FORCE_FLATTEN_SEC:
            if self.up_filled and not self.down_filled and self.down_order_id:
                reason = f'FORCE_FLATTEN: {time_remaining:.0f}s < {self.FORCE_FLATTEN_SEC}s cutoff'
                self._last_cancel_reason = reason
                return True, self.down_order_id, reason
            elif self.down_filled and not self.up_filled and self.up_order_id:
                reason = f'FORCE_FLATTEN: {time_remaining:.0f}s < {self.FORCE_FLATTEN_SEC}s cutoff'
                self._last_cancel_reason = reason
                return True, self.up_order_id, reason

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

        return False, '', ''

    def check_imbalance(self) -> Optional[Dict[str, Any]]:
        """
        SAFETY CHECK: Returns action to UNWIND excess if legs are unbalanced.
        Priority Level: CRITICAL.
        """
        delta = self.filled_up_shares - self.filled_down_shares
        
        if abs(delta) > self.IMBALANCE_THRESHOLD:
            # We have an imbalance. 
            # Example: UP=10, DOWN=0. Delta=10. Excess UP. Sell 10 UP.
            # Example: UP=12, DOWN=10. Delta=2. Excess UP. Sell 2 UP.
            excess_leg = 'UP' if delta > 0 else 'DOWN'
            excess_qty = abs(delta)
            
            return {
                'action': 'UNWIND_EXCESS',
                'leg': excess_leg,
                'qty': excess_qty,
                'reason': f"IMBALANCE DETECTED: {excess_leg} +{excess_qty:.2f} (UP={self.filled_up_shares} DOWN={self.filled_down_shares})"
            }
        return None

    def get_one_leg_action(self, time_remaining: float, 
                          unfilled_best_bid: float, unfilled_best_ask: float) -> Dict[str, Any]:
        """
        Determine action when in ONE_LEG_FILLED state.
        NEW: Panic Hedge Deterministic Mode (No Maker Loops).
        """
        if self.state != StraddleState.ONE_LEG_FILLED:
            return {'action': 'WAIT', 'reason': 'Not in one leg state'}

        # Identify unfilled leg
        unfilled_leg = 'UP' if not self.up_filled else 'DOWN'
        filled_price = self.filled_up_price if self.up_filled else self.filled_down_price
        
        # SIZE LOCK: Calculate remaining qty for unfilled leg
        remaining_qty = self.get_remaining_qty(unfilled_leg)
        
        if remaining_qty <= 0:
            self.size_locked_blocked = True
            return {
                'action': 'WAIT',
                'leg': unfilled_leg,
                'reason': f'SIZE_LOCK: {unfilled_leg} remaining=0, nothing to hedge'
            }

        # 1. TIME LIMITS (Force Flatten)
        now = time.time()
        time_in_state = now - self.one_leg_fill_time
        
        # Panic Timeout - Exit immediately if blocked too long
        if time_in_state > self.MAX_ONE_LEG_SEC_FAST:
             return {
                'action': 'TAKER_EXIT', # Panic Sell Filled Leg
                'leg': unfilled_leg,    # Note: caller must know this means "Sell FILLED leg"
                'reason': f'MAX_ONE_LEG_TIME: {time_in_state:.1f}s > {self.MAX_ONE_LEG_SEC_FAST}s'
            }

        # 2. PRICE CHECK (Can we complete?)
        # We need to BUY the unfilled leg at market (unfilled_best_ask)
        # Cost check: filled_price + ask <= CAP
        cost_to_complete = unfilled_best_ask
        total_projected_cost = filled_price + cost_to_complete
        
        # Debug Log
        # print(f"[HEDGE] Check: Filled={filled_price:.3f} Ask={cost_to_complete:.3f} Sum={total_projected_cost:.3f}")
        
        if total_projected_cost <= self.MAX_SUM_PRICE_CAP + self.EPS:
            # OK to complete
            return {
                'action': 'TAKER_COMPLETE',
                'leg': unfilled_leg,
                'qty': remaining_qty,
                'price': unfilled_best_ask, # For reference
                'reason': f'Complete Straddle (Sum {total_projected_cost:.2f} <= {self.MAX_SUM_PRICE_CAP})'
            }
        else:
            # Too expensive -> ABORT & EXIT
            return {
                'action': 'TAKER_EXIT',
                'leg': unfilled_leg,
                'qty': remaining_qty, # irrelevant for exit, but keeps struct
                'reason': f'Price Cap Exceeded (Sum {total_projected_cost:.2f} > {self.MAX_SUM_PRICE_CAP})'
            }

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
            # Use persisted entry snapshot values if active
            up_px = self.entry_up_px
            down_px = self.entry_down_px
            sum_px = self.entry_sum_px
            up_px = self.entry_up_px
            down_px = self.entry_down_px
            sum_px = self.entry_sum_px
            
        # Always output the config value
        max_sum_price = self.MAX_SUM_PRICE 

        # Extract intended/rounded prices from enter_result if available, else from persist
        intended_price_up = enter_result.get('intended_price_up', 0.0) if enter_result else self.entry_up_px
        rounded_price_up = enter_result.get('rounded_price_up', 0.0) if enter_result else self.entry_up_px
        intended_price_down = enter_result.get('intended_price_down', 0.0) if enter_result else self.entry_down_px
        rounded_price_down = enter_result.get('rounded_price_down', 0.0) if enter_result else self.entry_down_px
        
        # Placement-time snapshots
        up_ask_at_send = enter_result.get('up_ask_at_send', 0.0) if enter_result else self.entry_up_ask
        down_ask_at_send = enter_result.get('down_ask_at_send', 0.0) if enter_result else self.entry_down_ask
        up_bid_at_send = enter_result.get('up_bid_at_send', 0.0) if enter_result else self.entry_up_bid
        down_bid_at_send = enter_result.get('down_bid_at_send', 0.0) if enter_result else self.entry_down_bid

        blocked_maker = enter_result.get('blocked_maker', False) if enter_result else False
        near_resolution_blocked = enter_result.get('near_resolution_blocked', False) if enter_result else False
        entry_cutoff_sec = enter_result.get('entry_cutoff_sec', self.ENTRY_CUTOFF_SEC) if enter_result else self.ENTRY_CUTOFF_SEC
        
        # New fields extraction
        blocked_price_bounds = enter_result.get('blocked_price_bounds', False) if enter_result else False
        blocked_low_profit = enter_result.get('blocked_low_profit', False) if enter_result else False
        profit_both = enter_result.get('profit_both', 0.0) if enter_result else (self.entry_profit if self.state != StraddleState.IDLE else (1.0 - sum_px if self.state == StraddleState.IDLE else 0.0))
        
        # Status flags
        price_bounds_ok = enter_result.get('price_bounds_ok', not blocked_price_bounds) if enter_result else (not blocked_price_bounds)
        profit_ok = enter_result.get('profit_ok', not blocked_low_profit) if enter_result else (not blocked_low_profit)
        
        # Skip reason
        skip_reason = enter_result.get('reason', '') if enter_result and enter_result['action'] == 'IDLE' else ''
        if not skip_reason and blocked_price_bounds: skip_reason = "price_out_of_bounds"
        if not skip_reason and blocked_low_profit: skip_reason = "low_profit"

        # Extract sizing information
        optimal_size = enter_result.get('size', 0) if enter_result else self.optimal_size
        actual_size = self.actual_size

        # Extract sizing information
        size_chosen = sizing_debug.get('size_chosen', 0) if sizing_debug else 0
        size_max_usd = sizing_debug.get('size_max_usd', 0) if sizing_debug else 0
        size_max_final = sizing_debug.get('size_max_final', 0) if sizing_debug else 0
        usd_per_market_cap = sizing_debug.get('usd_per_market_cap', self.MAX_USD_PER_MARKET) if sizing_debug else self.MAX_USD_PER_MARKET
        straddle_notional_per_share = sizing_debug.get('straddle_notional_per_share', 0.0) if sizing_debug else 0.0
        size_block_reason = sizing_debug.get('size_block_reason', '') if sizing_debug else ''
        test_mode = sizing_debug.get('test_mode', False) if sizing_debug else False
        used_test_preset = sizing_debug.get('used_test_preset', False) if sizing_debug else False
        test_preset_candidate = sizing_debug.get('test_preset_candidate', 0) if sizing_debug else 0

        # State and unwind tracking
        state = self.state.value
        cancel_reason = getattr(self, '_last_cancel_reason', '')
        unwind_method = getattr(self, '_last_unwind_method', '')
        unwind_price = getattr(self, '_last_unwind_price', 0.0)
        unwind_result = getattr(self, '_last_unwind_result', '')
        taker_path_attempted = getattr(self, '_taker_path_attempted', False)
        
        # One-leg loop stats
        reprice_attempt_count = self.reprice_attempt_count
        reprice_reason = getattr(self, '_last_reprice_reason', '')
        time_in_one_leg_state = (now_ts - self.one_leg_fill_time) if self.state == StraddleState.ONE_LEG_FILLED else 0.0
        last_reprice_ts = self.last_reprice_ts

        # Force taker_path_attempted to False in test_mode (redundant safety)
        if test_mode:
            taker_path_attempted = False

        # Near resolution safety tracking
        force_flatten = "FORCE_FLATTEN" in cancel_reason or "FORCE_FLATTEN" in unwind_method if unwind_method else False
        force_flatten_sec = self.FORCE_FLATTEN_SEC

        return {
            'timestamp': now_ts,
            # ... existing ...
            'target_size_shares': self.target_size_shares,
            'filled_up_shares': self.filled_up_shares,
            'filled_down_shares': self.filled_down_shares,
            'remaining_up': self.target_size_shares - self.filled_up_shares if self.target_size_shares > 0 else 0,
            'remaining_down': self.target_size_shares - self.filled_down_shares if self.target_size_shares > 0 else 0,
            'size_locked_blocked': self.size_locked_blocked,
            'overbuy_attempted': self.overbuy_attempted,
            # ... existing fields ...
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
            'max_sum_price': self.MAX_SUM_PRICE, # Always correct
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
            'entry_id': getattr(self, 'entry_id', ''), # User Request
            'filled_first_price': (self.filled_up_price if self.filled_up_price > 0 else self.filled_down_price) if self.state != StraddleState.IDLE else 0.0, # User Request
            'unwind_result': unwind_result,
            'optimal_size': optimal_size,
            'actual_size': actual_size,
            'near_resolution_blocked': near_resolution_blocked,
            'force_flatten': force_flatten,
            'entry_cutoff_sec': entry_cutoff_sec,
            'force_flatten_sec': force_flatten_sec,
            'size_chosen': size_chosen,
            'size_max_usd': size_max_usd,
            'size_max_final': size_max_final,
            'usd_per_market_cap': usd_per_market_cap,
            'straddle_notional_per_share': straddle_notional_per_share,
            'size_block_reason': size_block_reason,
            'test_mode': test_mode,
            'used_test_preset': used_test_preset,
            'test_preset_candidate': test_preset_candidate,
            # New logging fields
            'min_leg_price': self.MIN_LEG_PRICE,
            'max_leg_price': self.MAX_LEG_PRICE,
            'min_profit_per_share': self.MIN_PROFIT_PER_SHARE,
            'profit_both': profit_both,
            'price_bounds_ok': price_bounds_ok,
            'profit_ok': profit_ok,
            'blocked_price_bounds': blocked_price_bounds,
            'blocked_low_profit': blocked_low_profit,
            'taker_path_attempted': taker_path_attempted,
            # Placement proof
            'up_ask_at_send': up_ask_at_send,
            'down_ask_at_send': down_ask_at_send,
            'up_bid_at_send': up_bid_at_send,
            'down_bid_at_send': down_bid_at_send,
            # One-leg loop fields
            'reprice_attempt_count': reprice_attempt_count,
            'reprice_reason': reprice_reason,
            'time_in_one_leg_state': time_in_one_leg_state,
            'last_reprice_ts': last_reprice_ts
        }
