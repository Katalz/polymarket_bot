#!/usr/bin/env python3
"""
Premier trade réel LIVE sur Polymarket
Achat de 1.00 USDC de tokens YES sur btc-updown-15m-1765406700
Suivi d'une vente instantanée de tous les tokens achetés.
"""

import os
import json
import requests
import time
from decimal import Decimal
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.exceptions import PolyApiException


# Configuration
MARKET_SLUG = "btc-updown-15m-1765410300"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
AMOUNT_USDC = 1.00
MAX_PRICE = 0.99  # Prix maximum accepté à l'achat


def get_token_id_from_gamma(market_slug: str) -> str:
    """
    Récupère le token_id du côté YES via l'API Gamma.

    Args:
        market_slug: Slug du marché (ex: "btc-updown-15m-1765406700")

    Returns:
        token_id du côté YES
    """
    print(f"🔍 Récupération des détails du marché: {market_slug}")

    # Requête vers l'API Gamma
    url = f"{GAMMA_API_BASE}/events"
    params = {"slug": market_slug}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise RuntimeError(f"Erreur API Gamma: {e}")

    # Vérifier qu'on a des résultats
    if not data or len(data) == 0:
        raise RuntimeError(f"Aucun marché trouvé pour le slug: {market_slug}")

    event = data[0]  # Premier événement
    
    # Récupérer les marchés du premier événement
    markets = event.get("markets", [])
    if not markets:
        raise RuntimeError("Aucun marché trouvé dans l'événement")

    market = markets[0]  # Premier marché
    
    # Récupérer les outcomes (peuvent être stockés comme chaîne JSON)
    outcomes_raw = market.get("outcomes", [])
    
    # Si c'est une chaîne JSON, parser-la
    if isinstance(outcomes_raw, str):
        import json
        try:
            outcomes = json.loads(outcomes_raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"Impossible de parser outcomes: {outcomes_raw}")
    else:
        outcomes = outcomes_raw
    
    if len(outcomes) < 2:
        raise RuntimeError(f"Marché incomplet, seulement {len(outcomes)} outcomes")

    # Pour btc-updown-15m, YES est généralement le deuxième outcome ["No", "Yes"]
    # ou le premier ["Up", "Down"] - on cherche "Yes" ou "Up"
    yes_outcome = None
    yes_index = 0

    for i, outcome in enumerate(outcomes):
        outcome_lower = str(outcome).lower()
        if "yes" in outcome_lower or "up" in outcome_lower:
            yes_outcome = outcome
            yes_index = i
            break

    if not yes_outcome:
        # Fallback: prendre le premier outcome
        yes_outcome = outcomes[0]
        yes_index = 0

    print(f"✅ Outcome YES trouvé: {yes_outcome} (index: {yes_index})")

    # Maintenant récupérer le token_id depuis les clobTokenIds
    clob_token_ids_raw = market.get("clobTokenIds", [])
    
    # Si c'est une chaîne JSON, parser-la
    if isinstance(clob_token_ids_raw, str):
        import json
        try:
            clob_token_ids = json.loads(clob_token_ids_raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"Impossible de parser clobTokenIds: {clob_token_ids_raw}")
    else:
        clob_token_ids = clob_token_ids_raw

    if not clob_token_ids or len(clob_token_ids) < len(outcomes):
        raise RuntimeError("clobTokenIds manquants ou incomplets")

    # L'index du token correspond à l'index de l'outcome trouvé
    if yes_index >= len(clob_token_ids):
        raise RuntimeError(f"Token index {yes_index} hors limites (max: {len(clob_token_ids)-1})")

    token_id = clob_token_ids[yes_index]

    print(f"✅ Token ID YES: {token_id}")
    return token_id


def init_clob_client():
    """Initialise le client CLOB avec configuration proxy wallet."""
    load_dotenv()

    # Vérification des variables d'environnement
    required_vars = ["POLYGON_PRIVATE_KEY", "POLYMARKET_PROXY_ADDRESS",
                     "CLOB_API_KEY", "CLOB_API_SECRET", "CLOB_API_PASSPHRASE"]

    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Variables d'environnement manquantes: {', '.join(missing)}")

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=os.getenv("POLYGON_PRIVATE_KEY"),
        os.getenv("POLYGON_PRIVATE_KEY") = "0x" + os.getenv("POLYGON_PRIVATE_KEY").lower()
        chain_id=137,
        creds=ApiCreds(
            api_key=os.getenv("CLOB_API_KEY"),
            api_secret=os.getenv("CLOB_API_SECRET"),
            api_passphrase=os.getenv("CLOB_API_PASSPHRASE"),
        ),
        signature_type=2,  # REQUIRED for proxy wallet
        funder=os.getenv("POLYMARKET_PROXY_ADDRESS"),
    )

    return client


def analyze_buy_price(client: ClobClient, token_id: str) -> float:
    """
    Analyse le carnet d'ordres pour l'ACHAT et retourne le meilleur prix vendeur (Ask).
    """
    print(f"📊 Analyse du carnet d'ordres (ACHAT) pour token: {token_id}")

    try:
        orderbook = client.get_order_book(token_id)
    except PolyApiException as e:
        raise RuntimeError(f"Erreur récupération order book: {e}")

    # Extraire les asks (offres de vente)
    asks = orderbook.asks if hasattr(orderbook, 'asks') else orderbook.get('asks', [])

    if not asks:
        raise RuntimeError("Aucune offre de vente disponible (pas de liquidité)")

    # Trouver le meilleur prix (prix le plus bas)
    if hasattr(asks[0], 'price'):
        best_ask = min(asks, key=lambda x: x.price).price
    else:
        # Format dict
        best_ask = min(asks, key=lambda x: x['price'])['price']

    # Convertir en float pour les comparaisons
    best_ask = float(best_ask)
    print(f"✅ Meilleur prix vendeur (Ask): {best_ask}")

    if best_ask > MAX_PRICE:
        raise RuntimeError(f"Prix trop élevé: {best_ask:.4f} > {MAX_PRICE}")
    return best_ask


def analyze_sell_price(client: ClobClient, token_id: str) -> float:
    """
    Analyse le carnet d'ordres pour la VENTE et retourne le meilleur prix acheteur (Bid).
    """
    print(f"📊 Analyse du carnet d'ordres (VENTE) pour token: {token_id}")

    try:
        orderbook = client.get_order_book(token_id)
    except PolyApiException as e:
        raise RuntimeError(f"Erreur récupération order book: {e}")

    # Extraire les bids (offres d'achat)
    bids = orderbook.bids if hasattr(orderbook, 'bids') else orderbook.get('bids', [])

    if not bids:
        raise RuntimeError("Aucune offre d'achat disponible (pas de liquidité)")

    # Trouver le meilleur prix (prix le plus haut) pour vendre
    if hasattr(bids[0], 'price'):
        best_bid = max(bids, key=lambda x: x.price).price
    else:
        # Format dict
        best_bid = max(bids, key=lambda x: x['price'])['price']

    # Convertir en float
    best_bid = float(best_bid)
    print(f"✅ Meilleur prix acheteur (Bid): {best_bid}")
    
    return best_bid


def execute_buy_trade(client: ClobClient, token_id: str, best_price: float):
    """
    Exécute l'achat FOK (Fill Or Kill).
    """
    # Calculer la quantité
    size = AMOUNT_USDC / best_price
    size = round(size, 2)  # Arrondir à 2 décimales

    print(f"💰 ACHAT calculé:")
    print(f"   Montant USDC: ${AMOUNT_USDC}")
    print(f"   Prix marché: {best_price}")
    print(f"   Quantité tokens: {size}")

    # Créer les arguments de l'ordre
    order_args = OrderArgs(
        token_id=token_id,
        price=best_price,
        size=size,
        side="BUY",
    )

    print("\n🚀 EXÉCUTION DE L'ACHAT LIVE...")
    print("⚠️  ATTENTION: Ceci va dépenser de vrais USDC sur Polymarket!")
    
    # Demander confirmation finale (optionnel, commenté pour automatisation)
    # confirm = input("Tapez 'OUI EXECUTER' pour confirmer: ").strip()
    # if confirm != "OUI EXECUTER":
    #     print("❌ Trade annulé par l'utilisateur")
    #     return None, 0

    try:
        result = client.create_and_post_order(order_args)
        print("\n✅ ACHAT RÉUSSI !")
        print(f"Order ID: {result.get('orderID', 'N/A')}")
        
        # Retourner le résultat et la quantité achetée pour la revente
        return result, size
    except PolyApiException as e:
        print(f"\n❌ ÉCHEC DE L'ACHAT: {e}")
        return None, 0


def execute_sell_trade(client: ClobClient, token_id: str, quantity: float, best_price: float):
    """
    Exécute la vente FOK (Fill Or Kill) de la quantité achetée.
    """
    print(f"\n💰 VENTE calculée:")
    print(f"   Quantité à vendre: {quantity}")
    print(f"   Prix marché (Bid): {best_price}")
    estimated_return = quantity * best_price
    print(f"   Retour estimé: ${estimated_return:.2f}")

    # Créer les arguments de l'ordre
    order_args = OrderArgs(
        token_id=token_id,
        price=best_price,
        size=quantity,
        side="SELL",
    )

    print("\n🚀 EXÉCUTION DE LA VENTE LIVE...")

    try:
        result = client.create_and_post_order(order_args)
        print("\n✅ VENTE RÉUSSIE !")
        print(f"Order ID: {result.get('orderID', 'N/A')}")
        return result
    except PolyApiException as e:
        print(f"\n❌ ÉCHEC DE LA VENTE: {e}")
        return None


def main():
    """Fonction principale."""
    print("🎯 LIVE TRADE TEST - ALLER-RETOUR (ACHAT + VENTE)")
    print("=" * 50)

    try:
        # Étape 1: Récupération du token ID
        token_id = get_token_id_from_gamma(MARKET_SLUG)

        # Étape 2: Initialisation du client CLOB
        client = init_clob_client()
        print("✅ Client CLOB initialisé")

        # --- PARTIE ACHAT ---
        print("\n🔵 DÉBUT PHASE D'ACHAT")
        best_buy_price = analyze_buy_price(client, token_id)
        
        # Demande de confirmation unique pour tout le cycle
        confirm = input(f"Confirmez-vous l'ACHAT de {AMOUNT_USDC}$ puis la VENTE immédiate? (Tapez 'GO'): ").strip()
        if confirm != "GO":
            print("❌ Annulé par l'utilisateur.")
            return False

        buy_result, quantity_bought = execute_buy_trade(client, token_id, best_buy_price)

        if not buy_result:
            print("⛔ Arrêt du script suite à l'échec de l'achat.")
            return False
            
        # Petite pause pour laisser le temps au système de traiter (optionnel mais recommandé)
        print("\n⏳ Pause de 2 secondes avant revente...")
        time.sleep(2)

        # --- PARTIE VENTE ---
        print("\n🔴 DÉBUT PHASE DE VENTE")
        # Réanalyser le prix pour la vente (le carnet a pu bouger)
        best_sell_price = analyze_sell_price(client, token_id)
        
        # Sécurité : vérifier qu'on ne vend pas à un prix aberrant (optionnel)
        if best_sell_price < (best_buy_price * 0.8):
            print(f"⚠️  ALERTE: Le prix de vente ({best_sell_price}) est >20% inférieur au prix d'achat ({best_buy_price}).")
            confirm_sell = input("Confirmer la vente à perte? (Tapez 'VENDRE'): ")
            if confirm_sell != "VENDRE":
                print("❌ Vente annulée manuellement.")
                return False

        sell_result = execute_sell_trade(client, token_id, quantity_bought, best_sell_price)

        if sell_result:
            print("\n🎉 CYCLE COMPLET TERMINÉ AVEC SUCCÈS !")
            
            # Calcul approximatif du PnL
            cost = quantity_bought * best_buy_price
            revenue = quantity_bought * best_sell_price
            pnl = revenue - cost
            print(f"Bilan estimé: Acheté {cost:.2f}$ -> Vendu {revenue:.2f}$ | PnL: {pnl:.2f}$")
        else:
            print("\n⚠️  Attention: Achat réussi mais Vente échouée. Vous détenez des positions.")

    except Exception as e:
        print(f"\n💥 ERREUR FATALE: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    main()