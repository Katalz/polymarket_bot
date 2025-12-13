import os
from dotenv import load_dotenv
from eth_account import Account

def check():
    load_dotenv()
    pk = os.getenv("POLYGON_PRIVATE_KEY")
    proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")
    
    if not pk:
        print("❌ Erreur : Pas de Clé Privée dans le .env")
        return

    # On ajoute 0x si manquant
    if not pk.startswith("0x"):
        pk = "0x" + pk

    try:
        # On dérive l'adresse publique (EOA) depuis la clé privée
        acct = Account.from_key(pk)
        public_address = acct.address
        
        print("--- DIAGNOSTIC IDENTITÉ ---")
        print(f"🔑 Clé Privée (fin)   : ...{pk[-4:]}")
        print(f"👤 Adresse Wallet (L1): {public_address}")
        print(f"🏦 Proxy Déclaré (L2) : {proxy}")
        print("-------------------------")
        print("👉 ACTION REQUISE :")
        print(f"1. Allez sur PolygonScan : https://polygonscan.com/address/{proxy}")
        print("2. Cliquez sur l'onglet 'Contract' (si dispo) ou regardez les transactions.")
        print("3. Mais surtout : Allez sur Polymarket > Settings et vérifiez que votre Proxy est bien :")
        print(f"   {proxy}")
        print(f"   Et que vous êtes bien connecté avec le wallet : {public_address}")
        
    except Exception as e:
        print(f"❌ Clé privée invalide : {e}")

if __name__ == "__main__":
    check()