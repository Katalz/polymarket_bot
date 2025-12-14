#!/usr/bin/env python3
"""
debug_market_status.py
Debug script to check market status from API
"""

import requests
import json

GAMMA_API = "https://gamma-api.polymarket.com"
GABAGOOL_PROXY = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"
DATA_API = "https://data-api.polymarket.com"

# Get a few trades
params = {
    "proxyWallet": GABAGOOL_PROXY,
    "limit": 5
}
r = requests.get(f"{DATA_API}/trades", params=params, timeout=10)
trades = r.json()

# Get unique slugs
slugs = set()
for trade in trades[:5]:
    slug = trade.get("slug") or trade.get("eventSlug") or trade.get("market")
    if slug:
        slugs.add(slug)
        print(f"\nSlug: {slug}")

# Check status for each slug
for slug in list(slugs)[:3]:  # Just check first 3
    print(f"\n{'='*70}")
    print(f"Checking: {slug}")
    print(f"{'='*70}")
    
    url = f"{GAMMA_API}/events"
    params = {
        "slug": slug,
        "limit": 1
    }
    
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            events = r.json()
            if events:
                event = events[0]
                print(f"Active: {event.get('active')}")
                print(f"Closed: {event.get('closed')}")
                print(f"End Date: {event.get('endDate')}")
                print(f"Start Date: {event.get('startDate')}")
                print("\nFull event data:")
                print(json.dumps(event, indent=2)[:500])  # First 500 chars
        else:
            print(f"Error: {r.status_code}")
    except Exception as e:
        print(f"Exception: {e}")
