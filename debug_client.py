from py_clob_client.client import ClobClient
import os
from dotenv import load_dotenv

load_dotenv()
pk = os.getenv("POLYGON_PRIVATE_KEY")
funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

client = ClobClient(host="https://clob.polymarket.com", key=pk, funder=funder)
print("Attributes in ClobClient:")
for attr in dir(client):
    if not attr.startswith("__"):
        val = getattr(client, attr)
        if str(val) == funder:
            print(f"FOUND MATCH: client.{attr} == {funder}")
        elif attr == "funder":
            print(f"client.funder == {val}")
