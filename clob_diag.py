#!/usr/bin/env python3
"""
clob_diag.py

Script de diagnostic pour Polymarket CLOB :

- Vérifie la présence et le chargement des variables d'environnement
- Initialise le ClobClient avec EOA + Proxy Wallet + L2 API keys
- Teste :
  1) connectivité générale (get_ok)
  2) signer + funder + L2 (get_balance_allowance)
  3) L2 API keys (get_trades)

Analyse ensuite les erreurs rencontrées et imprime un diagnostic lisible.
"""

import os
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from py_clob_client.exceptions import PolyApiException


# -------------------- Utils -------------------- #

def mask(value: str, keep_start: int = 4, keep_end: int = 4) -> str:
    if not value:
        return "<None>"
    if len(value) <= keep_start + keep_end:
        return value[0:2] + "..." + value[-2:]
    return value[:keep_start] + "..." + value[-keep_end:]


def describe_poly_error(e: PolyApiException, context: str):
    status = getattr(e, "status_code", None)
    msg = getattr(e, "error_msg", None) or getattr(e, "error_message", None) or str(e)

    print(f"\n❌ Erreur {context}:")
    print(f"   Status code : {status}")
    print(f"   Message brut: {repr(msg)[:400]}")

    lower = str(msg).lower()

    # Quelques diagnostics simples
    if "invalid api key" in lower:
        print("👉 Diagnostic : Les L2 API Keys (CLOB_API_KEY / SECRET / PASSPHRASE) ne correspondent pas au signer EOA.")
        print("   - Vérifie que tu utilises bien la private key EXACTE du 'signer' sur Polymarket.")
        print("   - Regénère les API keys dans l'UI Polymarket et mets-les dans le .env.")
    elif "signature" in lower and "failed" in lower:
        print("👉 Diagnostic : Problème de signature (private key incorrecte ou signature_type/funder incohérents).")
    elif "cloudflare" in lower or "sorry, you have been blocked" in lower:
        print("👉 Diagnostic : Blocage Cloudflare (IP/VPN). L'appel n'atteint même pas l'API CLOB.")
    elif status == 401:
        print("👉 Diagnostic : 401 Unauthorized. Souvent : API key invalide ou en-têtes d'auth manquants.")
    elif status == 403:
        print("👉 Diagnostic : 403 Forbidden. Souvent : clé invalide, permissions insuffisantes ou blocage de sécurité.")
    else:
        print("👉 Diagnostic : Erreur générique CLOB. Vérifie clefs, signer, funder, et la doc.")


# -------------------- Étape 1 : ENV + client -------------------- #

def init_client_from_env() -> ClobClient:
    load_dotenv()

    print("🔧 Vérification des variables d'environnement (.env)")

    pk = os.getenv("POLYGON_PRIVATE_KEY").lower()

    if not os.getenv("POLYGON_PRIVATE_KEY").lower().startswith("0x"):
        pk = "0x" + pk
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")
    api_key = os.getenv("CLOB_API_KEY")
    api_secret = os.getenv("CLOB_API_SECRET")
    api_passphrase = os.getenv("CLOB_API_PASSPHRASE")

    missing = []
    for name, val in [
        ("POLYGON_PRIVATE_KEY", pk),
        ("POLYMARKET_PROXY_ADDRESS", funder),
        ("CLOB_API_KEY", api_key),
        ("CLOB_API_SECRET", api_secret),
        ("CLOB_API_PASSPHRASE", api_passphrase),
    ]:
        print(f"  {name} = {mask(val)}")
        if not val:
            missing.append(name)

    if missing:
        raise RuntimeError(f"Variables manquantes dans .env : {', '.join(missing)}")

    print("\n✅ Variables d'environnement chargées.\n")

    print("🔐 Initialisation du ClobClient avec :")
    print(f"  - Signer (EOA, private key) : {mask(pk)}")
    print(f"  - Funder (Proxy wallet)     : {funder}")
    print(f"  - API Key                   : {mask(api_key)}")

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=pk,             # EOA signer
        chain_id=137,
        creds=ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        ),
        signature_type=2,   # Proxy / Gnosis Safe
        funder=funder,
    )

    print("✅ ClobClient initialisé.\n")
    return client


# -------------------- Étape 2 : Tests -------------------- #

def test_connectivity(client: ClobClient):
    print("🌐 Test 1 : connectivité de base (pas d'auth L2 requise)")
    ok = client.get_ok()
    server_time = client.get_server_time()
    print(f"  get_ok        : {ok}")
    print(f"  get_server_time: {server_time}")
    print("✅ Connectivité OK.\n")


def test_balance_allowance(client: ClobClient):
    print("💳 Test 2 : balance & allowance (test signer + funder + auth L2)")

    try:
        resp = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        print(f"  Réponse brute : {resp}")
        balance_raw = resp.get("balance", "0")
        print(f"  Balance raw   : {balance_raw}")
        print("✅ get_balance_allowance fonctionne :")
        print("   - Le signer EOA + funder proxy sont cohérents.")
        print("   - Les L2 API keys sont au moins acceptées pour cet endpoint.\n")
        return True
    except PolyApiException as e:
        describe_poly_error(e, "get_balance_allowance")
        return False


def test_l2_trades(client: ClobClient):
    print("📜 Test 3 : L2 API (get_trades)")

    try:
        trades = client.get_trades()
        n = len(trades) if trades is not None else 0
        print(f"  Nombre de trades retournés : {n}")
        print("✅ get_trades OK :")
        print("   - L2 API Keys (key/secret/passphrase) sont valides et actives.\n")
        return True
    except PolyApiException as e:
        describe_poly_error(e, "get_trades")
        return False


# -------------------- Main -------------------- #

def main():
    print("🧪 DIAGNOSTIC COMPLET POLYMARKET CLOB 🧪")
    print("=======================================\n")

    try:
        client = init_client_from_env()
    except Exception as e:
        print(f"\n💥 ERREUR dès l'initialisation du client : {e}")
        return

    # Test 1 : connectivité
    try:
        test_connectivity(client)
    except Exception as e:
        print(f"\n💥 Erreur de connectivité (même sans L2 auth) : {e}")
        print("👉 Vérifie ton réseau, DNS, VPN / IP, et que https://clob.polymarket.com est accessible.")
        return

    # Test 2 : balance & allowance
    ok_balance = test_balance_allowance(client)

    # Test 3 : L2 API (get_trades)
    ok_trades = test_l2_trades(client)

    print("\n📊 RÉSUMÉ DIAGNOSTIC")
    print("---------------------")
    print(f"  - Test connectivité (get_ok/get_server_time) : OK")
    print(f"  - Test balance_allowance                    : {'OK' if ok_balance else 'ECHEC'}")
    print(f"  - Test get_trades (L2 strict)              : {'OK' if ok_trades else 'ECHEC'}")

    print("\n🧭 INTERPRÉTATION RAPIDE :")
    if not ok_balance and not ok_trades:
        print("  ➤ Les deux endpoints L2 échouent :")
        print("    → Très probablement : L2 API keys invalides OU ne correspondent pas au signer EOA.")
    elif ok_balance and not ok_trades:
        print("  ➤ balance_allowance OK mais get_trades échoue :")
        print("    → Cas rare. Possiblement une différence de permissions sur l'API key ou un bug côté API.")
    elif ok_trades:
        print("  ➤ get_trades OK :")
        print("    → L2 API Keys et signer correspondent bien.")
        print("    → Si tu as encore des erreurs sur create_and_post_order, le problème est dans les paramètres de l'ordre (price/size/token_id), pas dans les clés.")
    print("\nFin du diagnostic.\n")


if __name__ == "__main__":
    main()
