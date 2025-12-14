import requests
import json
import time
from datetime import datetime

URL = "https://data.chain.link/api/query-timescale?query=LIVE_STREAM_REPORTS_QUERY&variables=%7B%22feedId%22%3A%220x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8%22%7D"

def check():
    print(f"Fetching: {URL}")
    t0 = time.time()
    try:
        r = requests.get(URL, timeout=5)
        lat = time.time() - t0
        print(f"Status: {r.status_code} | Latency: {lat:.3f}s")
        
        data = r.json()
        print(json.dumps(data, indent=2)[:500] + "...") # Show intro
        
        # Try to navigate response
        # Typically Graphql-like structure: data -> feeds -> reports?
        if "data" in data and "reports" in data["data"]:
            reports = data["data"]["reports"] # or feed?
            # Let's see the structure first
            pass
            
    except Exception as e:
        print(f"Error: {e}")

check()
