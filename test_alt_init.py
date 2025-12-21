import os
from py_clob_client.client import ClobClient
from dotenv import load_dotenv

load_dotenv()
pk = os.getenv("POLYGON_PRIVATE_KEY")
api_key = os.getenv("POLYMARKET_CLOB_API_KEY")
api_secret = os.getenv("POLYMARKET_CLOB_API_SECRET")
api_passphrase = os.getenv("POLYMARKET_CLOB_API_PASSPHRASE")

print("--- TESTING ALTERNATE INIT (clob_client.py style) ---")
try:
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=api_key,
        secret=api_secret,
        passphrase=api_passphrase,
        signer=pk
    )
    print("Client initialized.")
    print(f"Has get_balance: {hasattr(client, 'get_balance')}")
    print(f"Has get_positions: {hasattr(client, 'get_positions')}")
    if hasattr(client, 'get_balance'):
        print("Methods in dir(client) containing 'balance':")
        for m in dir(client):
            if "balance" in m.lower(): print(f" - {m}")
except Exception as e:
    print(f"Init failed: {e}")
