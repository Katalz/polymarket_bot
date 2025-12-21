import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from dotenv import load_dotenv

load_dotenv()
pk = os.getenv("POLYGON_PRIVATE_KEY")
client = ClobClient(host="https://clob.polymarket.com", key=pk, chain_id=137)

print("--- CLOB CLIENT METHODS ---")
for m in sorted(dir(client)):
    if not m.startswith("_"):
        print(m)
