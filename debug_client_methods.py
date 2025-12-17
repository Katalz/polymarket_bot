import os
import sys
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

load_dotenv()
pk = os.getenv("POLYGON_PRIVATE_KEY")
creds = ApiCreds(
    api_key=os.getenv("POLYMARKET_CLOB_API_KEY"),
    api_secret=os.getenv("POLYMARKET_CLOB_API_SECRET"),
    api_passphrase=os.getenv("POLYMARKET_CLOB_API_PASSPHRASE")
)
client = ClobClient(host="https://clob.polymarket.com", key=pk, chain_id=137, signature_type=2, creds=creds)

print("Client Dir:", dir(client))
try:
    print("Exchange Dir:", dir(client.exchange))
except:
    print("No client.exchange")
