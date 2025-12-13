#!/usr/bin/env python3
"""
Client Gamma API pour Polymarket - Métadonnées uniquement
"""

import json
import requests
from typing import Dict, Any, Optional


class GammaClient:
    """Client pour l'API Gamma de Polymarket (métadonnées read-only)"""

    def __init__(self, base_url: str = "https://gamma-api.polymarket.com"):
        self.base_url = base_url

    def get_market_tokens_from_event_slug(self, event_slug: str) -> Dict[str, Any]:
        """
        Récupère les token IDs CLOB depuis un event slug.

        Args:
            event_slug: Slug de l'événement (ex: "btc-updown-15m-1765322100")

        Returns:
            Dict contenant:
            - market: données du marché complet
            - yes_token_id: token ID pour YES/Up
            - no_token_id: token ID pour NO/Down

        Raises:
            ValueError: si les données sont invalides
        """
        url = f"{self.base_url}/events/slug/{event_slug}"
        print(f"DEBUG Gamma: Récupération event {event_slug} depuis {url}")

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            event = resp.json()

            markets = event.get("markets", [])
            if not markets:
                raise ValueError(f"Aucun marché trouvé pour l'événement {event_slug}")

            # Pour l'instant, prendre le premier marché (à améliorer si besoin)
            market = markets[0]
            print(f"DEBUG Gamma: Marché sélectionné: {market.get('id')} - {market.get('question', '')[:50]}...")

            # Parser outcomes
            outcomes_str = market.get("outcomes", "[]")
            if isinstance(outcomes_str, str):
                outcomes = json.loads(outcomes_str)
            else:
                outcomes = outcomes_str

            # Récupérer clobTokenIds (camelCase) - c'est une string JSON qu'il faut parser
            clob_token_ids_str = market.get("clobTokenIds", "[]")
            if isinstance(clob_token_ids_str, str):
                clob_token_ids = json.loads(clob_token_ids_str)
            else:
                clob_token_ids = clob_token_ids_str

            print(f"DEBUG Gamma: clobTokenIds parsé: {clob_token_ids} (len={len(clob_token_ids)})")

            if not clob_token_ids or len(clob_token_ids) != 2:
                print(f"DEBUG Gamma: Structure complète du marché:")
                for key, value in market.items():
                    if 'token' in key.lower() or 'clob' in key.lower():
                        print(f"  {key}: {value}")
                raise ValueError(f"clobTokenIds invalides: {clob_token_ids}")

            print(f"DEBUG Gamma: Outcomes={outcomes}, Token IDs={clob_token_ids}")

            # Pour les marchés Up/Down, le premier token est généralement "Up" (YES) et le second "Down" (NO)
            if outcomes == ["Up", "Down"]:
                yes_token_id = clob_token_ids[0]  # Up -> YES
                no_token_id = clob_token_ids[1]   # Down -> NO
            elif outcomes == ["Down", "Up"]:
                no_token_id = clob_token_ids[0]   # Down -> NO
                yes_token_id = clob_token_ids[1]  # Up -> YES
            else:
                # Fallback générique
                yes_labels = ["YES", "Yes", "UP", "Up"]
                no_labels = ["NO", "No", "DOWN", "Down"]

                yes_token_id = None
                no_token_id = None

                for i, label in enumerate(outcomes):
                    token_id = clob_token_ids[i]
                    if label in yes_labels:
                        yes_token_id = token_id
                    elif label in no_labels:
                        no_token_id = token_id

                if yes_token_id is None or no_token_id is None:
                    raise ValueError(f"Impossible de mapper outcomes {outcomes} vers YES/NO avec tokens {clob_token_ids}")

            result = {
                "market": market,
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
            }

            print(f"DEBUG Gamma: Mapping réussi - YES: {yes_token_id}, NO: {no_token_id}")
            return result

        except requests.RequestException as e:
            raise ValueError(f"Erreur réseau Gamma API: {e}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Erreur parsing JSON Gamma: {e}")


# Instance globale
gamma_client = GammaClient()
