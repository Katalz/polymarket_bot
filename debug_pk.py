import os
import sys
from dotenv import load_dotenv
from py_clob_client.client import ClobClient, ApiCreds

sys.path.append(os.getcwd())
load_dotenv()

pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip().replace('"', '').replace("'", "")
print(f"Key length (raw): {len(pk)}")
print(f"Starts with 0x? {pk.startswith('0x')}")

# Add 0x if missing
if not pk.startswith("0x"):
    pk_formatted = "0x" + pk
else:
    pk_formatted = pk
    
print(f"Key length (formatted): {len(pk_formatted)}")
print(f"Format check: {pk_formatted[:4]}...{pk_formatted[-4:]}")

try:
    from eth_account import Account
    acc = Account.from_key(pk_formatted)
    print(f"SUCCESS: Account loaded. Address: {acc.address}")
except Exception as e:
    print(f"FAIL: eth_account load error: {e}")

try:
    from eth_keys import keys
    k = keys.PrivateKey(bytes.fromhex(pk_formatted[2:]))
    print("SUCCESS: eth_keys validated.")
except Exception as e:
    print(f"FAIL: eth_keys error: {e}")
