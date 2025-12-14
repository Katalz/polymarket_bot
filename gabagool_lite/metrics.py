"""
Gabagool-Lite Metrics
=====================

Calculs de métriques clés : lockability, avg_cost, PnL, etc.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import time


@dataclass
class BookSnapshot:
    """Snapshot du carnet d'ordres pour un côté (YES ou NO)."""
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid: float = 0.0
    spread: float = 0.0
    liquidity_bid: float = 0.0   # USD dans les 5 premiers niveaux bid
    liquidity_ask: float = 0.0   # USD dans les 5 premiers niveaux ask
    best_bid_size: float = 0.0   # Size at best bid
    best_ask_size: float = 0.0   # Size at best ask
    
    @classmethod
    def from_orderbook(cls, bids: list, asks: list) -> "BookSnapshot":
        """Crée un snapshot depuis les listes bid/ask brutes."""
        # Ensure sorting (REST API usually sorted but better safe)
        # Bids: High to Low
        bids = sorted(bids, key=lambda x: float(x["price"]), reverse=True)
        # Asks: Low to High
        asks = sorted(asks, key=lambda x: float(x["price"]))
        
        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 0.0
        
        best_bid_size = float(bids[0]["size"]) if bids else 0.0
        best_ask_size = float(asks[0]["size"]) if asks else 0.0
        
        mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else 0.0
        spread = best_ask - best_bid if (best_bid and best_ask) else 0.0
        
        # Liquidité top 5 niveaux
        liq_bid = sum(float(b["price"]) * float(b["size"]) for b in bids[:5])
        liq_ask = sum(float(a["price"]) * float(a["size"]) for a in asks[:5])
        
        return cls(
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread=spread,
            liquidity_bid=liq_bid,
            liquidity_ask=liq_ask,
            best_bid_size=best_bid_size,
            best_ask_size=best_ask_size
        )

    @property
    def microprice(self) -> float:
        """
        Calcule le microprice (VAMP - Volume Weighted Average Mid Price)
        sur le top of book. Sert de signal 'Fair Value'.
        """
        if not (self.best_bid > 0 and self.best_ask > 0):
             return 0.0
        
        # Microprice = (Bid * AskQty + Ask * BidQty) / (AskQty + BidQty)
        qty_bid = self.best_bid_size
        qty_ask = self.best_ask_size
        
        if qty_bid + qty_ask == 0:
            return self.mid
            
        micro = (self.best_bid * qty_ask + self.best_ask * qty_bid) / (qty_bid + qty_ask)
        return micro 



@dataclass
class Inventory:
    """État de l'inventaire sur un marché."""
    q_yes: float = 0.0
    q_no: float = 0.0
    cost_yes: float = 0.0        # Coût total payé pour YES
    cost_no: float = 0.0         # Coût total payé pour NO
    
    # Pour suivi des fills
    fills_yes: int = 0           # Nombre de fills YES
    fills_no: int = 0            # Nombre de fills NO
    
    @property
    def total_cost(self) -> float:
        return self.cost_yes + self.cost_no
    
    @property
    def avg_cost_yes(self) -> float:
        return self.cost_yes / self.q_yes if self.q_yes > 0 else 0.0
    
    @property
    def avg_cost_no(self) -> float:
        return self.cost_no / self.q_no if self.q_no > 0 else 0.0
    
    @property
    def has_two_legs(self) -> bool:
        return self.q_yes > 0 and self.q_no > 0
    
    @property
    def dominant_side(self) -> str:
        if self.q_yes > self.q_no:
            return "YES"
        elif self.q_no > self.q_yes:
            return "NO"
        return "BALANCED"
    
    @property
    def imbalance(self) -> float:
        return abs(self.q_yes - self.q_no)
    
    def record_fill(self, side: str, qty: float, cost: float):
        """Enregistre un fill (achat)."""
        if side == "YES":
            self.q_yes += qty
            self.cost_yes += cost
            self.fills_yes += 1
        elif side == "NO":
            self.q_no += qty
            self.cost_no += cost
            self.fills_no += 1


@dataclass
class MarketMetrics:
    """Métriques complètes pour un marché."""
    timestamp: float = 0.0
    market_id: str = ""
    state: str = "IDLE"
    
    # Hybrid Momentum Data
    btc_t0: float = 0.0
    btc_now: float = 0.0
    delta_btc: float = 0.0
    conf: float = 0.0
    bias: str = "NEUTRAL"
    
    # Book data
    book_yes: Optional[BookSnapshot] = None
    book_no: Optional[BookSnapshot] = None
    
    # Inventory
    inventory: Optional[Inventory] = None
    
    # Derived metrics
    exposure_usd: float = 0.0
    mark_to_market_pnl: float = 0.0
    locked_pnl: float = 0.0
    locked_pnl_est: float = 0.0   # Estimation si on complète le hedge
    lock_score: float = 0.0        # Score de lockabilité (0-1)
    
    # Orders
    open_orders_count: int = 0
    open_orders_summary: str = ""
    
    # Timing
    time_remaining_sec: float = 0.0
    
    def compute_derived(self):
        """Calcule les métriques dérivées."""
        if not self.inventory:
            return
            
        inv = self.inventory
        
        # Exposure = coût total
        self.exposure_usd = inv.total_cost
        
        # Mark-to-market PnL (valeur actuelle - coût)
        if self.book_yes and self.book_no:
            market_value = (
                inv.q_yes * self.book_yes.best_bid +
                inv.q_no * self.book_no.best_bid
            )
            self.mark_to_market_pnl = market_value - inv.total_cost
        
        # Locked PnL (profit garanti)
        if inv.has_two_legs:
            min_qty = min(inv.q_yes, inv.q_no)
            # À la résolution, min_qty shares payent 1.00$ chacune
            guaranteed_payout = min_qty * 1.0
            self.locked_pnl = guaranteed_payout - inv.total_cost
        else:
            self.locked_pnl = -inv.total_cost  # Pire cas = perte totale


def compute_lockable_pnl(
    inventory: Inventory,
    book_yes: BookSnapshot,
    book_no: BookSnapshot,
    min_profit_usd: float = 0.50
) -> Tuple[float, float]:
    """
    Calcule le PnL STRICTEMENT verrouillable instantané.
    Ne se base pas sur des ratios, mais sur le coût pour compléter la couverture.
    
    Returns:
        (lockable_pnl_usd, cost_to_hedge_usd)
        - lockable_pnl_usd: Profit garanti si on exécute tout le hedge au market (best ask)
        - cost_to_hedge_usd: Coût en $ pour acheter la quantité manquante
    """
    if not inventory:
        return -999.0, 0.0
        
    # 1. Identifier le déséquilibre
    # On doit acheter le côté déficitaire
    qty_yes = inventory.q_yes
    qty_no = inventory.q_no
    
    if abs(qty_yes - qty_no) < 0.1:
        # Déjà équilibré (Locked)
        # Payout = min(q_yes, q_no)
        # Cost = total_cost
        payout = min(qty_yes, qty_no)
        current_pnl = payout - inventory.total_cost
        return current_pnl, 0.0

    # 2. Calcul du hedge nécessaire
    if qty_yes > qty_no:
        # Need NO
        needed = qty_yes - qty_no
        price = book_no.best_ask
        if price >= 1.0 or price <= 0: return -999.0, 0.0
        cost_to_hedge = needed * price
        
        # Nouveau cost total = Cost actuel + Cost hedge
        projected_total_cost = inventory.total_cost + cost_to_hedge
        
        # Nouveau Payout garanti = qty_yes (puisqu'on a monté qty_no à qty_yes)
        projected_payout = qty_yes 
        
        lockable = projected_payout - projected_total_cost
        return lockable, cost_to_hedge
        
    else:
        # Need YES
        needed = qty_no - qty_yes
        price = book_yes.best_ask
        if price >= 1.0 or price <= 0: return -999.0, 0.0
        cost_to_hedge = needed * price
        
        projected_total_cost = inventory.total_cost + cost_to_hedge
        projected_payout = qty_no
        
        lockable = projected_payout - projected_total_cost
        return lockable, cost_to_hedge

# Alias pour compatibilité si besoin, mais on préfère utiliser compute_lockable_pnl directement
def compute_lockability(inventory, book_yes, book_no, margin=0.0):
    pnl, cost = compute_lockable_pnl(inventory, book_yes, book_no)
    # Score 1.0 si pnl > 0
    return (1.0 if pnl > 0 else 0.0), pnl


def check_hedge_liquidity(
    opposing_book: BookSnapshot, 
    min_liquidity_usd: float = 50.0
) -> bool:
    """
    Vérifie si le book opposé a assez de liquidité (GATE).
    Critère: Somme(USD) dans le top 5 niveaux > min_liquidity_usd.
    Simple et robuste.
    """
    # Note: opposing_book.liquidity_ask/bid est déjà calculé sur top 5
    # Si on veut short (vendre pour sortir du hedge), on tape les Bids.
    # Si on veut long (acheter pour hedger), on tape les Asks.
    # Dans should_accumulate, on n'a pas encore le sens exact du hedge futur (long/short).
    # Mais généralement, si on accumule YES, on devra VENDRE YES pour sortir, ou ACHETER NO pour hedger.
    # Donc on check:
    # 1. Ask Liquidity du NO (pour pouvoir acheter hedge)
    
    # Nous utilisons ici `opposing_book.liquidity_ask` car le hedge consiste à ACHETER l'autre côté.
    if opposing_book.liquidity_ask < min_liquidity_usd:
        return False
    return True


def should_accumulate(
    book_yes: BookSnapshot,
    book_no: BookSnapshot,
    required_edge: float = 0.02, # Marge requise (ex: 2 cents)
    min_hedge_liquidity: float = 50.0
) -> Tuple[bool, str, float]:
    """
    Détermine si on doit accumuler (Fair Value Logic).
    Returns: (ShouldAccumulate, Side, FairValue)
    """
    # 1. Spread Check
    if book_yes.spread > 0.05 or book_no.spread > 0.05:
        return False, "spread_too_wide", 0.0

    # 2. Fair Value (Microprice)
    fair_yes = book_yes.microprice
    # Le microprice est le même pour YES/NO si calculé sur mid global, 
    # mais ici book_yes.microprice est calculé sur le book YES ? 
    # Non, book_yes a ses propres bids/asks ? Ah non, microprice est interne au book.
    # ATTENTION: Le microprice d'un coté 0-1 devrait être P.
    # Ici `book_yes` contient les bids/asks du token YES. Donc `book_yes.microprice` est le fair YES.
    # Pour NO, c'est `book_no.microprice` qui est le fair NO.
    
    # Cross-Check consistency: FairYes + FairNo ~= 1.0 ?
    # Pas forcément immédiat, mais bon proxy.
    
    # Decision YES:
    # On achète YES si AskYes < FairYes - Edge
    ask_yes = book_yes.best_ask
    if ask_yes > 0 and ask_yes < (fair_yes - required_edge):
        # 3. Liquidity Gate (Can we hedge on NO?)
        if check_hedge_liquidity(book_no, min_hedge_liquidity):
             return True, "YES", fair_yes
        else:
             return False, "no_hedge_liquidity", fair_yes
             
    # Decision NO:
    # On achète NO si AskNo < FairNo - Edge
    fair_no = book_no.microprice
    ask_no = book_no.best_ask
    if ask_no > 0 and ask_no < (fair_no - required_edge):
        # 3. Liquidity Gate (Can we hedge on YES?)
        if check_hedge_liquidity(book_yes, min_hedge_liquidity):
             return True, "NO", fair_no
        else:
             return False, "yes_hedge_liquidity", fair_no

    return False, "", 0.0


def should_hedge(
    inventory: Inventory,
    book_yes: BookSnapshot,
    book_no: BookSnapshot,
    min_exposure_usd: float = 15.0,
    hedge_trigger_price: float = 0.45
) -> Tuple[bool, str, float]:
    """
    Détermine si on doit hedger.
    LIVE-READY :
    - Basé sur EXPOSURE USD (pas nombre de shares)
    - Basé sur un prix max acceptable pour le hedge
    """
    if not inventory:
        return False, "", 0.0
        
    # 1. Exposure Check (Gate)
    # On ne hedge pas les poussières (< 15$)
    if inventory.total_cost < min_exposure_usd:
        return False, "exposure_too_low", 0.0
    
    # 2. Identifer côté dominant
    if inventory.q_yes > inventory.q_no:
        # Long YES, Need NO
        # Check prix NO
        if book_no.best_ask <= hedge_trigger_price:
            needed = inventory.q_yes - inventory.q_no
            return True, "NO", needed
        else:
            return False, f"price_high_no({book_no.best_ask:.2f})", 0.0
            
    elif inventory.q_no > inventory.q_yes:
        # Long NO, Need YES
        if book_yes.best_ask <= hedge_trigger_price:
            needed = inventory.q_no - inventory.q_yes
            return True, "YES", needed
        else:
            return False, f"price_high_yes({book_yes.best_ask:.2f})", 0.0
    
    return False, "balanced", 0.0


def format_metrics_log(metrics: MarketMetrics) -> str:
    """Formate les métriques pour le logging."""
    if not metrics.inventory:
        return f"[{metrics.market_id}] {metrics.state} | No inventory"
    
    inv = metrics.inventory
    
    book_str = ""
    if metrics.book_yes and metrics.book_no:
        book_str = (
            f"YES: bid={metrics.book_yes.best_bid:.3f} ask={metrics.book_yes.best_ask:.3f} | "
            f"NO: bid={metrics.book_no.best_bid:.3f} ask={metrics.book_no.best_ask:.3f}"
        )
    
    inv_str = (
        f"q_yes={inv.q_yes:.2f} q_no={inv.q_no:.2f} | "
        f"avg_yes={inv.avg_cost_yes:.3f} avg_no={inv.avg_cost_no:.3f}"
    )
    
    pnl_str = (
        f"exposure=${metrics.exposure_usd:.2f} | "
        f"mtm_pnl=${metrics.mark_to_market_pnl:+.2f} | "
        f"locked=${metrics.locked_pnl:+.2f}"
    )
    
    return (
        f"[{metrics.market_id}] {metrics.state} | "
        f"T-{int(metrics.time_remaining_sec)}s\n"
        f"  {book_str}\n"
        f"  {inv_str}\n"
        f"  {pnl_str}\n"
        f"  {pnl_str}\n"
        f"  orders: {metrics.open_orders_count} | fills: {inv.fills_yes + inv.fills_no} ({metrics.open_orders_summary})"
    )
