#!/usr/bin/env python3
"""
Client CLOB pour Polymarket - Prix temps réel read-only
"""

import logging
from typing import Tuple, Dict, Any, Optional
from py_clob_client.client import ClobClient

from config import (
    POLYGON_PRIVATE_KEY,
    CLOB_API_KEY,
    CLOB_API_SECRET,
    CLOB_API_PASSPHRASE,
    CLOB_HOST
)

logger = logging.getLogger(__name__)




class PolymarketCLOBClient:
    """Client CLOB pour récupérer les prix temps réel (read-only)"""

    def __init__(self, host: str = "https://clob.polymarket.com"):
        # Client read-only (pas d'authentification)
        self.client = ClobClient(host)
        logger.info(f"CLOB client initialisé (read-only) sur {host}")


class PolymarketCLOBClientAuthenticated:
    """Client CLOB authentifié pour les opérations live trading"""

    def __init__(self, host: str = None, api_key: str = None, api_secret: str = None,
                 api_passphrase: str = None, polygon_private_key: str = None):
        """
        Initialise un client CLOB authentifié pour live trading.

        Args:
            host: URL du CLOB (défaut: config.CLOB_HOST)
            api_key: Clé API CLOB
            api_secret: Secret API CLOB
            api_passphrase: Passphrase API CLOB
            polygon_private_key: Clé privée Polygon pour signer les transactions
        """
        # Utiliser les valeurs de config si non fournies
        host = host or CLOB_HOST
        api_key = api_key or CLOB_API_KEY
        api_secret = api_secret or CLOB_API_SECRET
        api_passphrase = api_passphrase or CLOB_API_PASSPHRASE
        polygon_private_key = polygon_private_key or POLYGON_PRIVATE_KEY

        # Validation
        if not all([api_key, api_secret, api_passphrase, polygon_private_key]):
            raise ValueError("Toutes les clés d'authentification sont requises pour le live trading")

        # Créer le client authentifié
        self.client = ClobClient(
            host=host,
            key=api_key,
            secret=api_secret,
            passphrase=api_passphrase,
            signer=polygon_private_key
        )

        logger.info(f"🚨 CLOB client authentifié initialisé sur {host}")
        logger.warning("MODE LIVE - ORDRES RÉELS POSSIBLES")

    def get_usdc_balance(self) -> float:
        """
        Récupère le solde USDC disponible.

        Returns:
            Solde USDC en float

        Raises:
            Exception: si la récupération échoue
        """
        try:
            balance_response = self.client.get_balance()
            logger.debug(f"Balance response: {balance_response}")

            # Le format exact dépend de l'API, ajuster selon la documentation
            if isinstance(balance_response, dict):
                usdc_balance = balance_response.get('usdc', 0)
            else:
                # Essayer d'extraire depuis l'objet de réponse
                usdc_balance = getattr(balance_response, 'usdc', 0)

            usdc_balance = float(usdc_balance)
            logger.info(f"💰 USDC Balance: ${usdc_balance:.2f}")

            return usdc_balance

        except Exception as e:
            logger.error(f"Erreur récupération solde USDC: {e}")
            raise

    def get_collateral_balance(self) -> Dict[str, Any]:
        """
        Récupère le solde de collateral complet.

        Returns:
            Dict avec toutes les informations de balance
        """
        try:
            balance = self.client.get_balance()
            return balance
        except Exception as e:
            logger.error(f"Erreur récupération balance collateral: {e}")
            raise

    def get_token_balance(self, token_id: str) -> float:
        """
        Récupère le solde d'un token spécifique.

        Args:
            token_id: ID du token

        Returns:
            Quantité de tokens détenus
        """
        try:
            positions = self.client.get_positions()
            logger.debug(f"Positions response: {positions}")

            # Chercher le token spécifique
            if isinstance(positions, list):
                for position in positions:
                    if getattr(position, 'token_id', None) == token_id or getattr(position, 'asset_id', None) == token_id:
                        return float(getattr(position, 'quantity', 0))

            elif isinstance(positions, dict):
                return float(positions.get(token_id, 0))

            return 0.0

        except Exception as e:
            logger.error(f"Erreur récupération balance token {token_id}: {e}")
            raise

    def test_connection(self) -> bool:
        """
        Teste la connexion au CLOB en récupérant le solde.

        Returns:
            True si la connexion fonctionne, False sinon
        """
        try:
            balance = self.get_usdc_balance()
            logger.info(f"✅ Connexion CLOB OK - Balance: ${balance:.2f} USDC")
            return True
        except Exception as e:
            logger.error(f"❌ Connexion CLOB FAILED: {e}")
            return False

    def get_yes_no_prices_from_clob(self, yes_token_id: str, no_token_id: str) -> Tuple[float, float]:
        """
        Récupère les prix YES/NO temps réel depuis le CLOB.

        Args:
            yes_token_id: Token ID pour YES/Up
            no_token_id: Token ID pour NO/Down

        Returns:
            Tuple (price_yes, price_no) - prix entre 0 et 1

        Raises:
            Exception: si la récupération échoue
        """
        try:
            # Utiliser get_midpoint pour les prix moyens
            print(f"DEBUG CLOB: Appel get_midpoint pour YES token {yes_token_id}")
            price_yes_raw = self.client.get_midpoint(yes_token_id)
            print(f"DEBUG CLOB: Raw price_yes: {price_yes_raw} (type: {type(price_yes_raw)})")

            print(f"DEBUG CLOB: Appel get_midpoint pour NO token {no_token_id}")
            price_no_raw = self.client.get_midpoint(no_token_id)
            print(f"DEBUG CLOB: Raw price_no: {price_no_raw} (type: {type(price_no_raw)})")

            # Gérer le format de retour de get_midpoint
            if isinstance(price_yes_raw, dict):
                # Si c'est un dict, chercher le prix dedans
                price_yes = float(price_yes_raw.get('mid', price_yes_raw.get('midpoint', price_yes_raw.get('price', 0))))
            else:
                price_yes = float(price_yes_raw)

            if isinstance(price_no_raw, dict):
                price_no = float(price_no_raw.get('mid', price_no_raw.get('midpoint', price_no_raw.get('price', 0))))
            else:
                price_no = float(price_no_raw)

            # Validation basique
            if not (0 <= price_yes <= 1) or not (0 <= price_no <= 1):
                raise ValueError(f"Prix hors limites: YES={price_yes}, NO={price_no}")

            logger.debug(
                "CLOB prices: YES token=%s price=%.4f | NO token=%s price=%.4f",
                yes_token_id, price_yes,
                no_token_id, price_no
            )

            return price_yes, price_no

        except Exception as e:
            logger.error(f"Erreur récupération prix CLOB: {e}")
            raise

    def get_orderbook_data(self, token_id: str) -> Dict[str, Any]:
        """
        Récupère les données complètes du orderbook pour un token depuis le CLOB.

        Args:
            token_id: Token ID pour lequel récupérer le orderbook

        Returns:
            Dict avec 'bids' et 'asks', chacun étant une liste de dicts {'price': float, 'size': float}
            Format: {
                'bids': [{'price': 0.45, 'size': 10.5}, ...],
                'asks': [{'price': 0.47, 'size': 8.2}, ...]
            }

        Raises:
            Exception: si la récupération échoue
        """
        try:
            print(f"DEBUG CLOB: Appel get_order_book pour token {token_id}")
            orderbook_raw = self.client.get_order_book(token_id)
            print(f"DEBUG CLOB: Raw orderbook type: {type(orderbook_raw)}")

            # Traiter la réponse selon le format de py_clob_client
            from py_clob_client.clob_types import OrderBookSummary

            if isinstance(orderbook_raw, OrderBookSummary):
                # Format OrderBookSummary: objet avec attributs bids et asks
                bids = getattr(orderbook_raw, 'bids', [])
                asks = getattr(orderbook_raw, 'asks', [])

            elif isinstance(orderbook_raw, dict):
                # Format dict de fallback: {'bids': [...], 'asks': [...]}
                bids = orderbook_raw.get('bids', [])
                asks = orderbook_raw.get('asks', [])

            else:
                raise ValueError(f"Format orderbook inattendu: {type(orderbook_raw)}")

            # Convertir en format standardisé
            processed_bids = []
            for bid in bids:
                if hasattr(bid, '__dict__'):
                    # Format OrderBookSummary: objet avec attributs
                    processed_bids.append({
                        'price': float(getattr(bid, 'price', 0)),
                        'size': float(getattr(bid, 'size', 0))
                    })
                elif isinstance(bid, dict):
                    # Format dict: {'price': ..., 'size': ...}
                    processed_bids.append({
                        'price': float(bid.get('price', 0)),
                        'size': float(bid.get('size', 0))
                    })

            processed_asks = []
            for ask in asks:
                if hasattr(ask, '__dict__'):
                    # Format OrderBookSummary: objet avec attributs
                    processed_asks.append({
                        'price': float(getattr(ask, 'price', 0)),
                        'size': float(getattr(ask, 'size', 0))
                    })
                elif isinstance(ask, dict):
                    # Format dict: {'price': ..., 'size': ...}
                    processed_asks.append({
                        'price': float(ask.get('price', 0)),
                        'size': float(ask.get('size', 0))
                    })

            orderbook_data = {
                'bids': processed_bids,
                'asks': processed_asks
            }

            logger.debug(
                f"CLOB orderbook: token={token_id} | bids={len(processed_bids)} asks={len(processed_asks)}"
            )

            return orderbook_data

        except Exception as e:
            logger.error(f"Erreur récupération orderbook CLOB: {e}")
            raise


# Instance globale
CLOB_HOST = "https://clob.polymarket.com"
clob_client = PolymarketCLOBClient(CLOB_HOST)
