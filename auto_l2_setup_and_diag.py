#!/usr/bin/env python3
"""
auto_l2_setup_and_diag.py

Objectif :
- Ne PLUS utiliser les API keys copiées depuis l'UI.
- Générer / dériver les L2 API keys directement depuis la private key (L1).
- Initialiser un client de trading PROXY WALLET + faire un diagnostic complet.
"""

import os
from dotenv import load_dotenv
from eth_account import Account

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
from py_clob_client.exceptions import PolyApiException

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


def load_env_and_check_pk():
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
    https://docs.polymarket.com/developers/CLOB/authentication  :contentReference[oaicite:2]{index=2}
    """
    print("🧩 Étape 1 : création / dérivation des API creds (L2) via private key (L1)…")

    # Client L1 : juste pour créer/dériver les API keys
    l1_client = ClobClient(
        host=HOST,
        key=private_key,
        chain_id=CHAIN_ID,
    )

    # Cette méthode existe bien dans py_clob_client (cf. README + blog Jeremy). :contentReference[oaicite:3]{index=3}
    api_creds = l1_client.create_or_derive_api_creds()

    print("✅ L2 API creds obtenues :")
    print(f"  api_key    : {api_creds.api_key}")
    print(f"  secret     : {api_creds.api_secret[:6]}... (tronqué)")
    print(f"  passphrase : {api_creds.api_passphrase[:8]}... (tronqué)")
    print()

    return api_creds


def init_trading_client(private_key: str, funder: str, api_creds):
    """
    Initialise un client de trading proxy wallet :
    - signer = private key
    - funder = proxy wallet
    - signature_type=2 (Gnosis Safe / proxy)
    - L2 creds = api_creds générés ci-dessus
    """
    print("🧩 Étape 2 : initialisation du client de trading (proxy + L2)…")

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


def run_diag(client: ClobClient):
    print("🔎 Étape 3 : DIAGNOSTIC COMPLET\n")

    # 1) get_ok + time
    try:
        ok = client.get_ok()
        t = client.get_server_time()
        print(f"✅ get_ok           : {ok}")
        print(f"✅ server time      : {t}")
    except Exception as e:
        print(f"❌ Erreur get_ok/get_server_time : {e}")
        return

    print()

    # 2) balance/allowance
    try:
        ba = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        print("✅ get_balance_allowance OK")
        print(f"  balance raw (USDC.e 6 décimales) : {ba.get('balance')}")
        if ba.get("balance") is not None:
            bal_float = int(ba["balance"]) / 1_000_000
            print(f"  balance ≈ {bal_float:.2f} USDC.e")
    except PolyApiException as e:
        print("❌ Erreur balance/allowance :")
        print("   status :", getattr(e, "status_code", "?"))
        print("   msg    :", getattr(e, "error_msg", str(e)))
        return
    except Exception as e:
        print(f"❌ Erreur balance/allowance (autre) : {e}")
        return

    print()

    # 3) get_trades = test L2 AUTH
    try:
        trades = client.get_trades()
        print("✅ get_trades OK (L2 AUTH fonctionne)")
        print(f"  Nombre de trades : {len(trades)}")
    except PolyApiException as e:
        print("❌ Erreur get_trades (test L2) :")
        print("   status :", getattr(e, "status_code", "?"))
        print("   msg    :", getattr(e, "error_msg", str(e)))
    except Exception as e:
        print("❌ Erreur get_trades (autre) :", e)


def main():
    try:
        pk, funder, addr = load_env_and_check_pk()
        api_creds = create_or_derive_l2_creds(pk)
        client = init_trading_client(pk, funder, api_creds)
        run_diag(client)
    except Exception as e:
        import traceback
        print("\n💥 ERREUR FATALE :", e)
        traceback.print_exc()


if __name__ == "__main__":
    main()
