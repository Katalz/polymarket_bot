
import time
import requests
import math
from datetime import datetime, timezone
from dateutil import parser
import json

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

def check_slug(slug):
    url = f"{GAMMA_API_BASE}/events"
    params = {"slug": slug}
    print(f"Checking {slug}...")
    try:
        r = requests.get(url, params=params, timeout=5)
        if r.status_code != 200:
            print(f"  [ERROR] Status {r.status_code}")
            return
        
        data = r.json()
        if not data:
            print("  [NOT FOUND] Empty list returned")
            return
            
        e = data[0]
        print(f"  [FOUND] {e['title']}")
        print(f"  Closed: {e.get('closed')}")
        
        start = e.get("startDate")
        end = e.get("endDate")
        print(f"  Start: {start}")
        print(f"  End:   {end}")
        
        if end:
            end_dt = parser.isoparse(end)
            now_dt = datetime.now(timezone.utc)
            rem = (end_dt - now_dt).total_seconds()
            print(f"  Remaining: {rem:.1f}s")
            
        markets = e.get("markets", [])
        if markets:
            print(f"  Markets count: {len(markets)}")
            print(f"  Market 0 Active: {markets[0].get('active')}")
            
    except Exception as e:
        print(f"  [EXCEPTION] {e}")

def main():
    now_ts = time.time()
    base_ts = math.floor(now_ts / 900) * 900
    
    # Check T-1, T, T+1, T+2 slots
    timestamps = [base_ts - 900, base_ts, base_ts + 900, base_ts + 1800]
    
    tickers = ["btc", "eth", "sol"]
    
    for ts in timestamps:
        print(f"\n--- Checking Timestamp {ts} ({time.strftime('%H:%M:%S', time.gmtime(ts))}) ---")
        for t in tickers:
            slug = f"{t}-updown-15m-{int(ts)}"
            check_slug(slug)

if __name__ == "__main__":
    main()
