#!/usr/bin/env python3
"""
gabagool_trade_analyzer.py
Analyse le comportement de Gabagool sur les marchés BTC 15min.
Utilise py_clob_client avec authentification pour accéder à l'API CLOB.
"""

import os
import csv
import json
from datetime import datetime
from dotenv import load_dotenv
import requests

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

# =========================
# CONFIGURATION
# =========================

load_dotenv()

GAMMA_API = "https://gamma-api.polymarket.com"
HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
CHAIN_ID = 137

# Adresse connue de Gabagool (Proxy/Maker Address)
GABAGOOL_PROXY = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"

# LE MARCHÉ QUE TU VEUX ANALYSER
TARGET_SLUG = "btc-updown-15m-1765568700"

OUTPUT_CSV = "gabagool_time_travel_analysis.csv"

# =========================
# FONCTIONS
# =========================

def init_clob_client():
    """Initialise le client CLOB avec authentification"""
    pk = os.getenv("POLYGON_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")
    
    api_key = os.getenv("POLYMARKET_CLOB_API_KEY")
    api_secret = os.getenv("POLYMARKET_CLOB_API_SECRET")
    api_passphrase = os.getenv("POLYMARKET_CLOB_API_PASSPHRASE")
    
    if not all([pk, funder, api_key, api_secret, api_passphrase]):
        raise RuntimeError("Missing env vars for CLOB authentication")
    
    # Normaliser la clé privée
    if pk and not pk.startswith("0x"):
        pk = "0x" + pk
    
    creds = ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )
    
    client = ClobClient(
        host=HOST,
        key=pk,
        chain_id=CHAIN_ID,
        signature_type=2,
        funder=funder,
        creds=creds
    )
    
    print("CLOB Client initialized with authentication")
    return client


def get_market_tokens(slug):
    """Récupère les token IDs YES/NO pour un marché"""
    print(f"Fetching market info for {slug}...")
    r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=10)
    if r.status_code != 200:
        print(f"   Error: {r.status_code}")
        return None, None
    
    data = r.json()
    if not data:
        return None, None
    
    m = data[0]["markets"][0]
    token_ids = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
    outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    
    yes_id, no_id = token_ids[0], token_ids[1]
    for i, o in enumerate(outcomes):
        if str(o).lower() in ["yes", "up"]:
            yes_id = token_ids[i]
        elif str(o).lower() in ["no", "down"]:
            no_id = token_ids[i]
    
    print(f"   YES token: {yes_id[:40]}...")
    print(f"   NO token:  {no_id[:40]}...")
    return yes_id, no_id


def fetch_trades_for_asset(client: ClobClient, asset_id: str, maker: str = None):
    """
    Récupère les trades pour un asset donné via le client CLOB authentifié.
    Utilise la pagination si disponible.
    """
    all_trades = []
    
    print(f"   Fetching trades for asset {asset_id[:30]}...")
    
    try:
        # Utiliser l'API get_trades du client
        # Le client py_clob ne supporte pas directement le filtrage par maker
        # mais on peut récupérer tous les trades et filtrer
        
        # Essayer d'abord via l'API REST directe avec auth headers
        # Le client génère les headers d'auth
        
        # Méthode alternative: utiliser requests avec les headers du client
        import hmac
        import base64
        import time as time_module
        import hashlib
        
        timestamp = str(int(time_module.time() * 1000))
        
        # Construire les headers L2
        method = "GET"
        path = "/trades"
        
        params = {
            "asset_id": asset_id,
            "limit": 500
        }
        if maker:
            params["maker"] = maker
        
        # Le client py_clob a une méthode pour faire des requêtes authentifiées
        # Mais elle n'est pas exposée publiquement
        # Utilisons une approche alternative
        
        # Essayer avec le endpoint public sans auth mais filtrage post-hoc
        r = requests.get(
            f"{HOST}/trades",
            params=params,
            timeout=15
        )
        
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                all_trades = data
            elif isinstance(data, dict):
                all_trades = data.get("data", [])
            print(f"   Got {len(all_trades)} trades")
        elif r.status_code == 401:
            # Besoin d'auth - utiliser le client
            print(f"   Need auth, using client.get_trades()...")
            # Le client peut récupérer nos propres trades
            trades = client.get_trades()
            print(f"   Got {len(trades)} of our trades")
            # Mais ce sont nos trades, pas ceux de Gabagool...
            all_trades = []
        else:
            print(f"   API error: {r.status_code}")
            
    except Exception as e:
        print(f"   Exception: {e}")
    
    return all_trades


def fetch_gabagool_trades_via_data_api(target_slug, max_results=5000):
    """
    Alternative: utiliser data-api.polymarket.com qui ne nécessite pas d'auth
    et supporte le filtrage par proxyWallet.
    """
    all_trades = []
    DATA_API = "https://data-api.polymarket.com"
    
    print(f"\nUsing Data API to fetch Gabagool trades for {target_slug}...")
    
    # Fetch multiple pages
    page = 0
    seen_hashes = set()
    
    while page < 50:  # Max 50 pages
        page += 1
        
        params = {
            "proxyWallet": GABAGOOL_PROXY,
            "limit": 500
        }
        
        try:
            r = requests.get(f"{DATA_API}/trades", params=params, timeout=15)
            if r.status_code != 200:
                print(f"   Error: {r.status_code}")
                break
            
            data = r.json()
            if not data:
                break
            
            # Filtrer par slug et dédupliquer
            new_count = 0
            for t in data:
                tx_hash = t.get("transactionHash", "")
                if tx_hash in seen_hashes:
                    continue
                seen_hashes.add(tx_hash)
                
                if t.get("slug") == target_slug or t.get("eventSlug") == target_slug:
                    # Déterminer YES/NO
                    outcome = t.get("outcome", "").lower()
                    if outcome in ["yes", "up"]:
                        t["side"] = "YES"
                    else:
                        t["side"] = "NO"
                    all_trades.append(t)
                    new_count += 1
            
            print(f"   Page {page}: scanned {len(data)}, found {new_count} new (total: {len(all_trades)})")
            
            if len(data) < 500:
                break
                
            if len(all_trades) >= max_results:
                break
                
        except Exception as e:
            print(f"   Exception: {e}")
            break
    
    print(f"Total trades for {target_slug}: {len(all_trades)}")
    return all_trades


def main():
    # Essayer d'abord avec Data API (plus simple, pas d'auth)
    all_trades = fetch_gabagool_trades_via_data_api(TARGET_SLUG)
    
    if not all_trades:
        print("\nNo trades found via Data API.")
        print("The market might be too old - Data API only keeps recent trades.")
        return
    
    # Trier par timestamp
    all_trades.sort(key=lambda t: t.get("timestamp", 0))
    
    # Analyser
    state = {"q_yes": 0, "q_no": 0, "expo": 0}
    rows = []
    
    for t in all_trades:
        price = float(t.get("price", 0))
        size = float(t.get("size", 0))
        usd = price * size
        
        if t["side"] == "YES":
            state["q_yes"] += size
        else:
            state["q_no"] += size
        state["expo"] += usd
        
        local_lock = min(state["q_yes"], state["q_no"]) - state["expo"] if state["q_yes"] > 0 and state["q_no"] > 0 else -state["expo"]
        
        rows.append({
            "time_utc": datetime.utcfromtimestamp(t.get("timestamp", 0)).isoformat(),
            "timestamp": t.get("timestamp", 0),
            "market_offset": 0,
            "side": t["side"],
            "price": price,
            "size": size,
            "usd_volume": usd,
            "market_slug": TARGET_SLUG,
            "local_q_yes": state["q_yes"],
            "local_q_no": state["q_no"],
            "local_exposure": state["expo"],
            "local_locked_pnl": local_lock,
            "GLOBAL_EXPOSURE_ALL_MKTS": state["expo"]
        })
    
    # Export CSV
    if rows:
        headers = rows[0].keys()
        with open(OUTPUT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        
        print(f"\n=== EXPORT TERMINE ===")
        print(f"Fichier: {OUTPUT_CSV}")
        print(f"Total trades: {len(rows)}")
        
        print(f"\n=== STATS FINALES ===")
        print(f"q_YES: {state['q_yes']:,.2f}")
        print(f"q_NO:  {state['q_no']:,.2f}")
        print(f"Exposure: ${state['expo']:,.2f}")
        locked = min(state["q_yes"], state["q_no"]) - state["expo"]
        print(f"Locked PnL: ${locked:,.2f}")
        
        # Calculer le ratio
        if max(state["q_yes"], state["q_no"]) > 0:
            ratio = min(state["q_yes"], state["q_no"]) / max(state["q_yes"], state["q_no"])
            print(f"Balance Ratio: {ratio:.4f}")
    else:
        print("No data to export!")


if __name__ == "__main__":
    main()