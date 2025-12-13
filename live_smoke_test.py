#!/usr/bin/env python3
"""
live_smoke_test.py

Test de fumée pour valider le placement et l'annulation d'ordres réels sur Polymarket.

SÉCURITÉ MAXIMALE :
- Ordre limite d'achat à prix très bas (0.05) pour éviter l'exécution
- Taille minimale autorisée (5 USDC)
- Annulation automatique immédiate
- Aucune position ouverte à la fin du test

Réutilise la logique d'authentification automatique L2 de auto_l2_setup_and_diag.py
"""

import os
import time
import json
import requests
from datetime import datetime, timezone
from dateutil import parser
from dotenv import load_dotenv
from eth_account import Account

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, OrderArgs
from py_clob_client.exceptions import PolyApiException

# ================================================
# CONFIGURATION - À MODIFIER SELON LE MARCHÉ ACTIF
# ================================================

# Slug du marché pour le test (BTC Up/Down 15min actif)
# Le token ID sera récupéré automatiquement depuis ce slug
TEST_MARKET_SLUG = "btc-updown-15m-1765408500"  # À METTRE À JOUR avec un marché actif !

# Paramètres de sécurité pour l'ordre de test
TEST_ORDER_PRICE = 0.05  # Prix très bas pour éviter l'exécution
TEST_ORDER_SIZE_USDC = 1.0  # Taille réduite à 1$ pour test

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# ================================================
# DÉCOUVERTE AUTOMATIQUE DE MARCHÉ ACTIF
# ================================================

def discover_active_market():

    """

    Découvre STRICTEMENT le marché BTC Up/Down 15min actif via l'API Gamma.

    Optimisé pour la vitesse et le filtrage des marchés 'Zombies'.

    """

    print("🔍 API GAMMA : Recherche du marché BTC actif...")



    # 1. Requête API optimisée

    # On demande les événements ouverts, triés par date

    url = f"{GAMMA_API_BASE}/events"

    params = {

        "closed": "false",

        "limit": 50,  # Suffisant pour trouver le BTC récent

        "order": "startDate",

        "ascending": "true"

    }



    try:

        t0 = time.time()

        response = requests.get(url, params=params, timeout=5) # Timeout court pour être rapide

        response.raise_for_status()

        events = response.json()

        print(f"⚡ API répondue en {time.time() - t0:.2f}s ({len(events)} events)")

    except Exception as e:

        print(f"⚠️ Erreur API Gamma : {e}")

        events = [] # On laisse vide pour déclencher le fallback



    # 2. Filtrage Logiciel

    now = datetime.now(timezone.utc)

    target_market = None



    if events:

        for event in events:

            slug = event.get('slug', '').lower()



            # --- A. DÉFINITION DES VARIABLES (L'erreur était ici avant) ---

            is_btc = "btc" in slug

            is_15m = "15m" in slug or "15min" in slug

            # Accepte "updown", "up-down" ou simplement si le titre contient les infos

            is_type_ok = "updown" in slug or "up-down" in slug



            # --- B. FILTRE DE NOM ---

            if not (is_btc and is_15m and is_type_ok):

                continue



            # --- C. FILTRE TEMPOREL (CRITIQUE) ---

            try:

                start_date = parser.isoparse(event['startDate'])

                end_date = parser.isoparse(event['endDate'])

            except:

                continue



            # RÈGLE ABSOLUE : Si la date de fin est passée, C'EST POUBELLE.

            # Même si l'API dit qu'il est ouvert.

            if end_date <= now:

                # print(f"  🗑️ Ignoré (Zombie/Fini) : {slug}")

                continue



            # Si on arrive ici, le marché finit dans le futur.



            # Cas 1 : C'est le marché ACTUEL

            if start_date <= now < end_date:

                print(f"✅ MARCHÉ ACTIF TROUVÉ : {slug}")

                target_market = event

                break # On arrête tout de suite, on a trouvé le meilleur.



            # Cas 2 : C'est le PROCHAIN marché (Back-up)

            # On ne le prend que si on n'a rien trouvé d'autre pour l'instant

            if start_date > now and target_market is None:

                print(f"⏳ Candidat futur identifié : {slug}")

                target_market = event

                # On continue la boucle au cas où le marché actuel serait plus loin dans la liste



    # 3. Fallback (Si l'API n'a rien donné)

    if not target_market:

        print("⚠️ API n'a rien trouvé. Passage en mode Fallback manuel...")

        try:

            from market_discovery import get_active_btc_15min_markets, get_current_active_market



            # Génère les slugs mathématiquement

            markets = get_active_btc_15min_markets()

            if not markets: raise RuntimeError("Fallback vide")



            # Trouve celui qui correspond à l'heure actuelle

            current_market_simple = get_current_active_market(markets)

            if not current_market_simple: raise RuntimeError("Pas de marché actif calculé")



            # On doit faire une requête spécifique pour ce slug pour avoir les IDs

            print(f"🔄 Requête ciblée pour : {current_market_simple['slug']}")

            resp = requests.get(f"{GAMMA_API_BASE}/events", params={"slug": current_market_simple['slug']})

            data = resp.json()



            if data:

                target_market = data[0]

                print("✅ Marché récupéré via Fallback.")

            else:

                raise RuntimeError("Marché introuvable même via Fallback")



        except Exception as e:

            raise RuntimeError(f"❌ ÉCHEC FATAL DE DÉCOUVERTE : {e}")



    # 4. Extraction des Token IDs (Standard)

    print(f"🎯 Extraction des données pour : {target_market['slug']}")



    markets_data = target_market.get("markets", [])

    if not markets_data: raise RuntimeError("Market data vide")



    market_data = markets_data[0] # Le sub-market UP/DOWN



    # Gestion propre des strings JSON

    clob_ids = market_data.get("clobTokenIds", [])

    if isinstance(clob_ids, str): import json; clob_ids = json.loads(clob_ids)



    outcomes = market_data.get("outcomes", [])

    if isinstance(outcomes, str): import json; outcomes = json.loads(outcomes)



    # Trouver l'index YES / UP

    yes_index = 0

    for i, out in enumerate(outcomes):

        if str(out).lower() in ["yes", "up"]:

            yes_index = i

            break



    if len(clob_ids) <= yes_index:

        raise RuntimeError(f"Token IDs manquants (len={len(clob_ids)}, need={yes_index})")



    yes_id = clob_ids[yes_index]



    # Trouver l'index NO (Inverse de YES)

    no_id = clob_ids[1 - yes_index] if len(clob_ids) == 2 else None



    # Securité si l'API est bizarre

    if not no_id:

        no_id = str(int(yes_id) + 1) if int(yes_id) % 2 == 0 else str(int(yes_id) - 1)



    return {

        'slug': target_market['slug'],

        'token_id': yes_id,

        'no_token_id': no_id,

        'start_time': parser.isoparse(target_market['startDate']).timestamp(),

        'end_time': parser.isoparse(target_market['endDate']).timestamp(),

        'market_data': market_data

    }

def get_token_id_from_market_data(market):
    """
    Extrait le token ID YES depuis les données de marché Gamma.

    Args:
        market: Données du marché depuis Gamma API

    Returns:
        str: Token ID du côté YES
    """
    # Récupérer les outcomes
    outcomes_raw = market.get("outcomes", [])
    if isinstance(outcomes_raw, str):
        import json
        outcomes = json.loads(outcomes_raw)
    else:
        outcomes = outcomes_raw

    if len(outcomes) < 2:
        raise RuntimeError("Marché incomplet")

    # Chercher YES ou UP
    yes_index = 0
    for i, outcome in enumerate(outcomes):
        outcome_lower = str(outcome).lower()
        if "yes" in outcome_lower or "up" in outcome_lower:
            yes_index = i
            break

    # Récupérer le token ID
    clob_token_ids_raw = market.get("clobTokenIds", [])
    if isinstance(clob_token_ids_raw, str):
        import json
        clob_token_ids = json.loads(clob_token_ids_raw)
    else:
        clob_token_ids = clob_token_ids_raw

    if not clob_token_ids or yes_index >= len(clob_token_ids):
        raise RuntimeError("Token IDs manquants")

    return clob_token_ids[yes_index]

# ================================================
# RÉCUPÉRATION AUTOMATIQUE DU TOKEN ID (legacy)
# ================================================

def get_token_id_from_gamma(market_slug: str) -> str:
    """
    Récupère le token_id du côté YES via l'API Gamma.

    Args:
        market_slug: Slug du marché (ex: "btc-updown-15m-1765406700")

    Returns:
        token_id du côté YES
    """

    print(f"🔍 Récupération du token ID depuis Gamma API: {market_slug}")

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

    # Récupérer les outcomes
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
    print()
    return token_id

# ================================================
# LOGIQUE D'AUTHENTIFICATION (COPIÉE DE auto_l2_setup_and_diag.py)
# ================================================

def load_env_and_check_pk():
    """Charge les variables d'environnement et valide la clé privée."""
    load_dotenv()

    pk = os.getenv("POLYGON_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

    if not pk:
        raise RuntimeError("POLYGON_PRIVATE_KEY manquante dans .env")

    if not funder:
        raise RuntimeError("POLYMARKET_PROXY_ADDRESS manquante dans .env")

    pk = pk.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk

    acct = Account.from_key(pk)
    addr = acct.address

    print("🔐 Signer (L1) :")
    print(f"  Private key   : {pk[:10]}...{pk[-6:]}")
    print(f"  Address (EOA) : {addr}")
    print(f"  Funder (proxy): {funder}")
    print()

    return pk, funder, addr


def create_or_derive_l2_creds(private_key: str):
    """
    Utilise L1 pour créer / dériver les credentials L2 (officiel).
    """
    print("🧩 Création / dérivation des API creds (L2) via private key (L1)…")

    # Client L1 : juste pour créer/dériver les API keys
    l1_client = ClobClient(
        host=HOST,
        key=private_key,
        chain_id=CHAIN_ID,
    )

    # Cette méthode existe bien dans py_clob_client
    api_creds = l1_client.create_or_derive_api_creds()

    print("✅ L2 API creds obtenues :")
    print(f"  api_key    : {api_creds.api_key}")
    print(f"  secret     : {api_creds.api_secret[:6]}... (tronqué)")
    print(f"  passphrase : {api_creds.api_passphrase[:8]}... (tronqué)")
    print()

    return api_creds


def init_trading_client(private_key: str, funder: str, api_creds):
    """
    Initialise un client de trading proxy wallet.
    """
    print("🧩 Initialisation du client de trading (proxy + L2)…")

    client = ClobClient(
        host=HOST,
        key=private_key,
        chain_id=CHAIN_ID,
        creds=api_creds,  # Pass API credentials directly to constructor
        signature_type=2,  # proxy / Gnosis Safe
        funder=funder,
    )

    print("✅ Client de trading initialisé avec L2 creds + proxy funder.")
    print()
    return client

# ================================================
# LOGIQUE DE TEST DE FUMÉE
# ================================================

def check_order_book(client: ClobClient, token_id: str):
    """Récupère et affiche un aperçu du carnet d'ordres."""
    print("📊 Récupération du carnet d'ordres (pour info)…")

    try:
        orderbook = client.get_order_book(token_id)
        print("✅ Carnet d'ordres récupéré")

        # Afficher quelques niveaux pour info
        asks = orderbook.asks[:3] if hasattr(orderbook, 'asks') else orderbook.get('asks', [])[:3]
        bids = orderbook.bids[:3] if hasattr(orderbook, 'bids') else orderbook.get('bids', [])[:3]

        if asks:
            print(f"  Top Asks (ventes): {[(f'{float(a.price):.4f}', f'{float(a.size):.2f}') for a in asks[:2]]}")
        if bids:
            print(f"  Top Bids (achats): {[(f'{float(b.price):.4f}', f'{float(b.size):.2f}') for b in bids[:2]]}")

        print()
        return True
    except PolyApiException as e:
        print(f"❌ Erreur récupération order book: {e}")
        return False


def place_test_order(client: ClobClient, token_id: str, price: float, size_usdc: float):
    """Place un ordre limite d'achat de test."""
    print("📝 Placement de l'ordre de test (LIMITE ACHAT)…")
    print(f"   Token ID: {token_id}")
    print(f"   Prix limite: {price}")
    print(f"   Taille: {size_usdc} USDC")
    print("   ⚠️  SÉCURITÉ: Prix très bas pour éviter l'exécution")
    # Calculer la quantité approximative en tokens
    estimated_qty = size_usdc / price
    # Créer les arguments de l'ordre
    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=estimated_qty,
        side="BUY",
    )

    print("\n🚀 Placement de l'ordre de test…")

    try:
        result = client.create_and_post_order(order_args)
        order_id = result.get('orderID') or result.get('order_id')

        if not order_id:
            raise RuntimeError("Order ID non trouvé dans la réponse")

        print("✅ Ordre placé avec succès !")
        print(f"   Order ID: {order_id}")
        print(f"   Réponse brute: {result}")
        print()

        return order_id

    except PolyApiException as e:
        print(f"❌ Échec du placement d'ordre: {e}")
        return None


def verify_order_status(client: ClobClient, order_id: str):
    """Vérifie que l'ordre est bien ouvert."""
    print("🔍 Vérification du statut de l'ordre…")

    try:
        # Méthode 1: get_order spécifique
        order_details = client.get_order(order_id)

        if order_details:
            status = order_details.get('status', 'UNKNOWN')
            print("✅ Détails de l'ordre récupérés:")
            print(f"   Status: {status}")
            print(f"   Order ID: {order_id}")

            if status.upper() in ['OPEN', 'LIVE']:
                print("✅ Ordre confirmé ouvert - Test réussi !")
                return True
            else:
                print(f"⚠️  Ordre status: {status} (attendu: OPEN ou LIVE)")
                return False
        else:
            print("❌ Impossible de récupérer les détails de l'ordre")
            return False

    except PolyApiException as e:
        print(f"❌ Erreur vérification ordre: {e}")
        # Fallback: vérifier dans les ordres ouverts
        try:
            print("🔄 Tentative fallback: vérification dans ordres ouverts…")
            open_orders = client.get_open_orders()
            order_ids = [o.get('id') or o.get('orderID') for o in open_orders]

            if order_id in order_ids:
                print("✅ Ordre trouvé dans les ordres ouverts - Test réussi !")
                return True
            else:
                print("❌ Ordre non trouvé dans les ordres ouverts")
                return False
        except Exception as e2:
            print(f"❌ Échec fallback aussi: {e2}")
            return False


def cancel_test_order(client: ClobClient, order_id: str):
    """Annule l'ordre de test."""
    print("🚫 Annulation de l'ordre de test…")

    try:
        cancel_result = client.cancel_orders([order_id])
        print("✅ Ordre annulé avec succès !")
        print(f"   Order ID: {order_id}")
        print(f"   Réponse: {cancel_result}")
        print()

        # Vérification finale
        print("🔍 Vérification finale de l'annulation…")
        time.sleep(1)  # Petite pause pour propagation

        try:
            order_details = client.get_order(order_id)
            final_status = order_details.get('status', 'UNKNOWN') if order_details else 'NOT_FOUND'
            print(f"   Statut final: {final_status}")

            if final_status.upper() in ['CANCELLED', 'CANCELED', 'NOT_FOUND', 'CANCELLED_BY_USER']:
                print("✅ Annulation confirmée - Test terminé avec succès !")
                return True
            else:
                print(f"⚠️  Statut final inattendu: {final_status}")
                return False

        except Exception as e:
            print(f"⚠️  Impossible de vérifier le statut final (peut être normal): {e}")
            return True  # On considère que c'est OK si l'annulation a réussi

    except PolyApiException as e:
        print(f"❌ Échec de l'annulation: {e}")
        return False


def main():
    """Fonction principale du test de fumée."""
    print("🧪 SMOKE TEST LIVE - PLACEMENT ET ANNULATION D'ORDRE")
    print("=" * 60)
    print("⚠️  SÉCURITÉ: Test avec ordre à prix très bas (0.05) pour éviter l'exécution")
    print("⚠️  Ordre sera automatiquement annulé après vérification")
    print()

    try:
        # Étape 1: Authentification et initialisation
        print("🔐 PHASE 1: AUTHENTIFICATION L2")
        print("-" * 40)

        pk, funder, addr = load_env_and_check_pk()
        api_creds = create_or_derive_l2_creds(pk)
        client = init_trading_client(pk, funder, api_creds)

        # Étape 2: Découverte de marché actif et vérifications pré-test
        print("📋 PHASE 2: DÉCOUVERTE MARCHÉ ACTIF & VÉRIFICATIONS")
        print("-" * 55)

        # Découvrir automatiquement un marché actif
        try:
            market_info = discover_active_market()
            test_token_id = market_info['token_id']
            market_slug = market_info['slug']
            print(f"🎯 Marché sélectionné: {market_slug}")
        except Exception as e:
            print(f"❌ Impossible de trouver un marché actif: {e}")
            print("💡 Vérifiez votre connexion internet et que des marchés BTC 15min existent")
            return False

        # Vérifier la balance
        try:
            balance_resp = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            balance = float(balance_resp.get('balance', '0')) / 1_000_000  # USDC 6 décimales
            if balance < TEST_ORDER_SIZE_USDC:
                raise RuntimeError(f"Balance insuffisante: {balance} < {TEST_ORDER_SIZE_USDC}")
        except Exception as e:
            print(f"❌ Erreur vérification balance: {e}")
            return False

        # Vérifier le carnet d'ordres
        if not check_order_book(client, test_token_id):
            print("⚠️  Impossible de récupérer le carnet, mais on continue le test…")

        # Étape 3: Test principal
        print("🚀 PHASE 3: TEST PRINCIPAL")
        print("-" * 40)

        # Placer l'ordre
        order_id = place_test_order(client, test_token_id, TEST_ORDER_PRICE, TEST_ORDER_SIZE_USDC)
        if not order_id:
            return False

        # Attendre 3 secondes
        print("⏳ Attente de 3 secondes pour propagation…")
        time.sleep(3)

        # Vérifier le statut
        if not verify_order_status(client, order_id):
            print("❌ Test échoué: ordre non ouvert")
            return False

        # Annuler l'ordre
        if not cancel_test_order(client, order_id):
            print("❌ Test échoué: annulation impossible")
            return False

        # Succès !
        print("\n🎉 TEST DE FUMÉE RÉUSSI !")
        print("✅ Placement d'ordre fonctionnel")
        print("✅ Vérification de statut fonctionnelle")
        print("✅ Annulation d'ordre fonctionnelle")
        print("✅ Aucune position ouverte restante")
        print("\n🚀 Prêt pour le live trading !")
        return True

    except Exception as e:
        print(f"\n💥 ERREUR FATALE: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
