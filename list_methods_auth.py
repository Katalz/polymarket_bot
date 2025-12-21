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

print("--- CLOB CLIENT METHODS (AUTH) ---")
for m in sorted(dir(client)):
    if not m.startswith("_"):
        print(m)
