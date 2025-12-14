import requests
import json

GABAGOOL_PROXY = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"
DATA_API = "https://data-api.polymarket.com"

def inspect():
    params = {
        "proxyWallet": GABAGOOL_PROXY,
        "limit": 1
    }
    try:
        r = requests.get(f"{DATA_API}/trades", params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data:
                print(json.dumps(data[0], indent=2))
            else:
                print("No trades found for this wallet")
        else:
            print(f"Error: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    inspect()
