from dotenv import load_dotenv
import os

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

load_dotenv()

client = ClobClient(
    host="https://clob.polymarket.com",
    key=os.getenv("POLYGON_PRIVATE_KEY"),
    chain_id=137,
    creds=ApiCreds(
        api_key=os.getenv("CLOB_API_KEY"),
        api_secret=os.getenv("CLOB_API_SECRET"),
        api_passphrase=os.getenv("CLOB_API_PASSPHRASE"),
    ),
    signature_type=2,
    funder=os.getenv("POLYMARKET_PROXY_ADDRESS"),
)

try:
    print("Trades:", client.get_trades())
except Exception as e:
    print("ERROR:", e)
