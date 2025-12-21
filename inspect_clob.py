import inspect
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

print("--- CLOB CLIENT CLASS INSPECTION ---")
print(f"Bases: {ClobClient.__mro__}")

client = ClobClient(host="https://clob.polymarket.com")

# Does it have any sub-clients?
for name, value in inspect.getmembers(client):
    if not name.startswith("_") and not inspect.ismethod(value) and not inspect.isfunction(value):
        print(f"Attribute: {name} (Type: {type(value)})")

# Let's try to catch where get_balance might be in the code
try:
    import py_clob_client
    print(f"Library Location: {py_clob_client.__file__}")
except:
    pass
