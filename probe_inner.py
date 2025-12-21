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

if hasattr(client, "client"):
    print("--- INNER CLIENT METHODS ---")
    inner = client.client
    for m in sorted(dir(inner)):
        if not m.startswith("_"):
            print(m)
else:
    print("No inner client.")
