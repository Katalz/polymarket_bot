import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient

def check():
    load_dotenv()
    pk = os.getenv("POLYGON_PRIVATE_KEY")
    proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")
    
    print(f"🔑 Clé Privée (fin): ...{pk[-4:]}")
    print(f"🏦 Proxy déclaré  : {proxy}")

    # On se connecte sans proxy pour demander "Qui suis-je ?"
    client = ClobClient(host="https://clob.polymarket.com", key=pk, chain_id=137)
    
    try:
        # On demande à l'API : quel est le vrai proxy de cette clé ?
        real_proxy = client.get_prediction_market_proxy_address()
        print(f"✅ Le Proxy RÉEL associé à cette clé est : {real_proxy}")
        
        if real_proxy.lower() == proxy.lower():
            print("🎉 C'EST MATCH ! Votre .env est correct.")
        else:
            print("❌ ERREUR FATALE : Votre .env contient le mauvais proxy !")
            print(f"👉 Remplacez {proxy} par {real_proxy} dans votre fichier .env")
            
    except Exception as e:
        print(f"❌ Erreur lors de la vérification : {e}")

if __name__ == "__main__":
    check()