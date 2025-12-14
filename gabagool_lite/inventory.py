
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List

logger = logging.getLogger(__name__)

@dataclass
class InventoryLedger:
    """
    Source of Truth locale pour l'inventaire.
    Mise à jour uniquement par les FILLS.
    """
    slug: str
    
    # State
    q_yes: float = 0.0
    q_no: float = 0.0
    
    cost_yes: float = 0.0  # Total USD spent on current q_yes
    cost_no: float = 0.0   # Total USD spent on current q_no
    
    realized_pnl: float = 0.0
    
    # Meta
    last_update_ts: float = 0.0
    processed_trades: set = field(default_factory=set) # To deduct duplicates
    fills: List[Dict] = field(default_factory=list) # History of applied fills
    
    @property
    def exposure(self) -> float:
        """Total invested capital (cost basis) currently held."""
        return self.cost_yes + self.cost_no
        
    @property
    def avg_cost_yes(self) -> float:
        return self.cost_yes / self.q_yes if self.q_yes > 0 else 0.0
        
    @property
    def avg_cost_no(self) -> float:
        return self.cost_no / self.q_no if self.q_no > 0 else 0.0

    def apply_fill(self, fill: Dict):
        """
        Ingest a fill dict.
        fill format expected:
        {
            "side": "BUY" | "SELL",
            "token_id": "...",
            "size": float,
            "price": float,
            "fee": float (optional),
            "trade_id": str,
            "timestamp": float
        }
        """
        trade_id = fill.get("trade_id")
        if trade_id and trade_id in self.processed_trades:
            return # Dedupe
            
        side = fill["side"].upper() # BUY or SELL
        size = float(fill["size"])
        price = float(fill["price"])
        token_id = str(fill.get("token_id", ""))
        
        # Determine YES or NO based on token_id? 
        # Actually caller usually knows, but we need to map token_id to Side if passed raw.
        # Alternatively, caller passes 'asset_type'="YES"|"NO"
        # Let's assume input has 'asset_type' or we rely on caller.
        # Ideally this class knows its tokens. But simpler: caller validates token_id.
        # Let's add 'type' to args or assume 'token_type' in fill.
        
        token_type = fill.get("token_type") # "YES" or "NO"
        if not token_type:
            logger.error(f"[{self.slug}] Fill missing token_type: {fill}")
            return

        if fill.get("trade_id"):
            self.processed_trades.add(fill["trade_id"])

        # Record fill history for cooldown checks
        self.fills.append(fill)

        if side == "BUY":
            self._apply_buy(token_type, size, price)
        elif side == "SELL":
            self._apply_sell(token_type, size, price)
            
        self.last_update_ts = fill.get("timestamp", 0)
        
        # Log explicit fill application
        logger.info(f"[FILL APPLIED] {self.slug} | {side} {token_type} {size:.2f} @ {price:.3f} | Inv: Y={self.q_yes:.1f} N={self.q_no:.1f} Exp=${self.exposure:.2f}")

    def _apply_buy(self, token_type: str, qty: float, price: float):
        cost = qty * price
        
        if token_type == "YES":
            self.q_yes += qty
            self.cost_yes += cost
        else:
            self.q_no += qty
            self.cost_no += cost
            
    def _apply_sell(self, token_type: str, qty: float, price: float):
        """
        SELL Logic: Reduce quantity, reduce cost basis proportionally (Average Cost).
        Add diff to realized PnL.
        """
        # Determine current stats
        if token_type == "YES":
            current_q = self.q_yes
            current_cost = self.cost_yes
            avg_cost = self.avg_cost_yes
        else:
            current_q = self.q_no
            current_cost = self.cost_no
            avg_cost = self.avg_cost_no
            
        if current_q <= 0:
            logger.warning(f"[{self.slug}] SELLING {token_type} but Inventory 0! (Short/Error?)")
            # Force 0 floor but allow tracking PnL impact??
            # Assume we just go negative or stay 0? 
            # For this bot (Long only), this is likely a sync error or dust.
            # We will handle it by allowing reduction but logging warn.
            pass

        # Calculate PnL
        # Realized = (SellPrice - AvgCost) * Qty
        pnl_chunk = (price - avg_cost) * qty
        self.realized_pnl += pnl_chunk
        
        # Reduce Cost Basis Proportional
        # cost_removed = avg_cost * qty
        cost_removed = avg_cost * qty
        
        # Update State
        if token_type == "YES":
            self.q_yes = max(0.0, self.q_yes - qty)
            self.cost_yes = max(0.0, self.cost_yes - cost_removed)
            if self.q_yes == 0: self.cost_yes = 0.0 # Clean dust
        else:
            self.q_no = max(0.0, self.q_no - qty)
            self.cost_no = max(0.0, self.cost_no - cost_removed)
            if self.q_no == 0: self.cost_no = 0.0 # Clean dust

    def compute_mtm_pnl(self, bid_yes: float, bid_no: float) -> float:
        """
        Mark-to-Market PnL using BEST BID (Exit Price).
        PnL = Liquidation Value - Cost Basis + Realized
        """
        liquidation_value = (self.q_yes * bid_yes) + (self.q_no * bid_no)
        # Unrealized PnL = Liquidation - Cost
        unrealized = liquidation_value - self.exposure
        
        # Total
        return unrealized + self.realized_pnl

    def sanity_check(self, api_q_yes: float, api_q_no: float, threshold_qty: float = 2.0) -> Tuple[bool, str]:
        """
        Compare Ledger vs API.
        Returns (is_ok, message)
        """
        diff_yes = abs(self.q_yes - api_q_yes)
        diff_no = abs(self.q_no - api_q_no)
        
        if diff_yes > threshold_qty or diff_no > threshold_qty:
            msg = f"DIVERGENCE: Ledger(Y={self.q_yes:.1f}, N={self.q_no:.1f}) vs API(Y={api_q_yes:.1f}, N={api_q_no:.1f})"
            return False, msg
            
        return True, "OK"
