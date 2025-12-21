import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from dotenv import load_dotenv

load_dotenv()
pk = os.getenv("POLYGON_PRIVATE_KEY")
creds = ApiCreds(
    api_key=os.getenv("POLYMARKET_CLOB_API_KEY"),
    api_secret=os.getenv("POLYMARKET_CLOB_API_SECRET"),
    api_passphrase=os.getenv("POLYMARKET_CLOB_API_PASSPHRASE")
)
client = ClobClient(host="https://clob.polymarket.com", key=pk, chain_id=137, signature_type=2, creds=creds)

print(f"Has get_balance: {hasattr(client, 'get_balance')}")
print(f"Has get_user_balances: {hasattr(client, 'get_user_balances')}")
print(f"Has get_positions: {hasattr(client, 'get_positions')}")

# List all methods that look like balance or positions
print("Potential matches:")
for m in dir(client):
    if "balance" in m.lower() or "position" in m.lower():
        print(f" - {m}")

# Check sub-objects
for attr in ["orders", "account", "balance", "positions"]:
    if hasattr(client, attr):
        sub = getattr(client, attr)
        print(f"Sub-object '{attr}' exists. Methods:")
        for sm in dir(sub):
            if not sm.startswith("_"):
                print(f"   - {attr}.{sm}")
