"""
Classe MarketSession pour gérer une session de trading de 15 minutes sur un marché.
"""

import time
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass

from config import SESSION_DURATION_MINUTES
from paper_trader import PaperTrader, calculate_fair_value
from clob_client import clob_client


@dataclass
class Trade:
    """Représente un trade individuel dans une session"""
    timestamp: float
    price_yes: float
    price_no: float
    action: str
    qty_yes_before: float
    qty_no_before: float
    qty_yes_after: float
    qty_no_after: float
    exposure_after: float


class MarketSession:
    """
    Gère une session de trading de 15 minutes pour un marché spécifique.

    Propriétés principales :
    - market_id : identifiant du marché
    - start_timestamp : timestamp de début de session
    - end_timestamp : timestamp de fin prévu (= start + 15min)
    - trader : instance PaperTrader pour cette session
    - trades : liste des trades effectués pendant la session
    - max_exposure : exposition maximale atteinte pendant la session
    """

    def __init__(self, market_id: str, start_timestamp: Optional[float] = None,
                 yes_token_id: Optional[str] = None, no_token_id: Optional[str] = None):
        """
        Initialise une nouvelle session de marché.

        Args:
            market_id: Identifiant du marché (slug de l'événement)
            start_timestamp: Timestamp de début (défaut: time.time())
            yes_token_id: Token ID pour YES (optionnel, pour calcul fair_value)
            no_token_id: Token ID pour NO (optionnel, pour calcul fair_value)
        """
        self.market_id = market_id
        self.start_timestamp = start_timestamp or time.time()
        self.end_timestamp = self.start_timestamp + (SESSION_DURATION_MINUTES * 60)

        # Token IDs pour calcul du fair_value directionnel
        self.yes_token_id = yes_token_id
        self.no_token_id = no_token_id

        # Instance de trader dédiée à cette session
        self.trader = PaperTrader(yes_token_id=yes_token_id, no_token_id=no_token_id)

        # Liste des trades effectués pendant la session
        self.trades: List[Trade] = []

        # Métriques de session
        self.max_exposure = 0.0
        self.tick_count = 0

        # Nouvelles métriques pour les calculs de risque
        self.worst_case_pnl_during_window = 0.0  # Pire P&L observé pendant la session
        self.max_theoretical_edge_seen = 0.0      # Max edge observé pendant la session

    def on_tick(self, tick: Dict[str, Any]) -> tuple[str, str]:
        """
        Traite un nouveau tick du marché.

        Args:
            tick: Dictionnaire contenant les données du tick
                  (price_yes, price_no, timestamp)

        Returns:
            tuple[str, str]: (action, decision_reason) où action est ("BUY_YES", "BUY_NO", "HOLD")
        """
        self.tick_count += 1

        # Extraction des données du tick
        price_yes = tick["price_yes"]
        price_no = tick["price_no"]
        timestamp = tick["timestamp"]

        # Mettre à jour les métriques de session avec les nouvelles données
        current_edge = self.trader.state.get_edge(price_yes, price_no)
        self.max_theoretical_edge_seen = max(self.max_theoretical_edge_seen, current_edge)

        current_worst_pnl = self.trader.state.worst_case_pnl
        self.worst_case_pnl_during_window = min(self.worst_case_pnl_during_window, current_worst_pnl)

        # Calculer le fair_value dynamique pour le trading directionnel
        fair_value = None
        if self.yes_token_id:
            try:
                orderbook_data = clob_client.get_orderbook_data(self.yes_token_id)
                fair_value = calculate_fair_value(orderbook_data)

                # Calculer le spread pour le logging
                if fair_value is not None:
                    if orderbook_data and 'bids' in orderbook_data and 'asks' in orderbook_data:
                        bids = orderbook_data.get('bids', [])
                        asks = orderbook_data.get('asks', [])
                        if bids and asks:
                            spread = float(asks[0].get('price', 0)) - float(bids[0].get('price', 0))
                            print(f"[SIGNAL] Fair Value dynamic: {fair_value:.4f} (Spread: {spread:.4f})")
                        else:
                            print(f"[SIGNAL] Fair Value dynamic: {fair_value:.4f} (No spread data)")
                    else:
                        print(f"[SIGNAL] Fair Value dynamic: {fair_value:.4f} (No orderbook data)")
                else:
                    print(f"[SIGNAL] Fair Value calculation returned None (market conditions unsuitable)")

            except Exception as e:
                print(f"[SIGNAL] Fair Value calculation failed: {e}, using fallback")
                fair_value = price_yes  # Fallback au prix actuel

        # Enregistrer l'état avant le trade
        qty_yes_before = self.trader.state.qty_yes
        qty_no_before = self.trader.state.qty_no

        # Exécuter la logique de trading (passer fair_value et market_end_timestamp au trader)
        action, decision_reason = self.trader.on_prices(price_yes, price_no, timestamp, self.start_timestamp, fair_value, self.end_timestamp)

        # Mettre à jour les métriques
        current_exposure = self.trader.state.total_exposure
        self.max_exposure = max(self.max_exposure, current_exposure)

        # Enregistrer le trade si une action a été effectuée
        if action != "HOLD":
            trade = Trade(
                timestamp=timestamp,
                price_yes=price_yes,
                price_no=price_no,
                action=action,
                qty_yes_before=qty_yes_before,
                qty_no_before=qty_no_before,
                qty_yes_after=self.trader.state.qty_yes,
                qty_no_after=self.trader.state.qty_no,
                exposure_after=current_exposure
            )
            self.trades.append(trade)

        return action, decision_reason

    def is_finished(self, current_timestamp: float) -> bool:
        """
        Vérifie si la session est terminée.

        Args:
            current_timestamp: Timestamp actuel

        Returns:
            True si current_timestamp >= end_timestamp
        """
        return current_timestamp >= self.end_timestamp

    def get_time_in_market(self, current_timestamp: float) -> float:
        """
        Calcule le temps écoulé depuis le début du marché en secondes.

        Args:
            current_timestamp: Timestamp actuel

        Returns:
            Temps en secondes depuis le début du marché
        """
        return current_timestamp - self.start_timestamp

    def compute_terminal_pnl(self) -> Tuple[float, float]:
        """
        Calcule le P&L terminal dans les deux scénarios possibles.

        Returns:
            Tuple (pnl_if_yes_wins, pnl_if_no_wins)
        """
        qty_yes = self.trader.state.qty_yes
        qty_no = self.trader.state.qty_no

        # Calcul du coût total investi
        cash_spent = 0.0
        if self.trader.state.avg_yes:
            cash_spent += qty_yes * self.trader.state.avg_yes
        if self.trader.state.avg_no:
            cash_spent += qty_no * self.trader.state.avg_no

        # Valeur en cas de victoire YES : qty_yes * 1 + qty_no * 0 = qty_yes
        value_if_yes = qty_yes

        # Valeur en cas de victoire NO : qty_no * 1 + qty_yes * 0 = qty_no
        value_if_no = qty_no

        # P&L dans chaque scénario
        pnl_if_yes = value_if_yes - cash_spent
        pnl_if_no = value_if_no - cash_spent

        return pnl_if_yes, pnl_if_no

    def get_session_summary(self) -> Dict[str, Any]:
        """
        Retourne un résumé complet de la session.

        Returns:
            Dictionnaire avec toutes les métriques de la session
        """
        pnl_yes, pnl_no = self.compute_terminal_pnl()

        return {
            "market_id": self.market_id,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "duration_minutes": (self.end_timestamp - self.start_timestamp) / 60,
            "tick_count": self.tick_count,
            "nb_trades": len(self.trades),

            # État final des positions
            "final_qty_yes": self.trader.state.qty_yes,
            "final_avg_yes": self.trader.state.avg_yes,
            "final_qty_no": self.trader.state.qty_no,
            "final_avg_no": self.trader.state.avg_no,

            # Métriques financières
            "max_exposure": self.max_exposure,
            "final_exposure": self.trader.state.total_exposure,
            "pnl_if_yes": pnl_yes,
            "pnl_if_no": pnl_no,

            # Métriques de performance
            "final_pair_cost": self.trader.state.pair_cost,
            "final_theoretical_profit": self.trader.state.theoretical_profit,
            "final_leg_imbalance": self.trader.state.leg_imbalance_ratio,

            # Trades détaillés
            "trades": [self._trade_to_dict(trade) for trade in self.trades]
        }

    def _trade_to_dict(self, trade: Trade) -> Dict[str, Any]:
        """Convertit un objet Trade en dictionnaire"""
        return {
            "timestamp": trade.timestamp,
            "price_yes": trade.price_yes,
            "price_no": trade.price_no,
            "action": trade.action,
            "qty_yes_before": trade.qty_yes_before,
            "qty_no_before": trade.qty_no_before,
            "qty_yes_after": trade.qty_yes_after,
            "qty_no_after": trade.qty_no_after,
            "exposure_after": trade.exposure_after
        }

    def get_market_summary_row(self) -> Dict[str, Any]:
        """
        Retourne la ligne à écrire dans market_summary.csv.

        Returns:
            Dictionnaire avec les colonnes pour le CSV
        """
        pnl_yes, pnl_no = self.compute_terminal_pnl()

        return {
            "market_id": self.market_id,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "final_qty_yes": self.trader.state.qty_yes,
            "final_avg_yes": self.trader.state.avg_yes,
            "final_qty_no": self.trader.state.qty_no,
            "final_avg_no": self.trader.state.avg_no,
            "pnl_if_yes": pnl_yes,
            "pnl_if_no": pnl_no,
            "max_exposure": self.max_exposure,
            "nb_trades": len(self.trades),
            "worst_case_pnl_during_window": self.worst_case_pnl_during_window,
            "max_theoretical_edge_seen": self.max_theoretical_edge_seen,
            "locked": 1 if self.trader.state.locked else 0,
            "closed_due_to_stop": 1 if self.trader.state.closed_due_to_stop else 0,
            "final_guaranteed_profit": self.trader.state.guaranteed_profit
        }
