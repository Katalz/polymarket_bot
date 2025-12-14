#!/usr/bin/env python3
"""
explore_api.py
Teste différents endpoints pour trouver celui qui donne les positions
"""

import requests
import json

GABAGOOL = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"

# Essayer différentes API
apis_to_test = {
    "CLOB /balance": f"https://clob.polymarket.com/balance?user={GABAGOOL}",
    "CLOB /balances": f"https://clob.polymarket.com/balances?user={GABAGOOL}",
    "CLOB /positions": f"https://clob.polymarket.com/positions?user={GABAGOOL}",
    "CLOB /holdings": f"https://clob.polymarket.com/holdings?user={GABAGOOL}",
    "GAMMA /accounts": f"https://gamma-api.polymarket.com/accounts/{GABAGOOL}",
    "GAMMA /positions": f"https://gamma-api.polymarket.com/positions?address={GABAGOOL}",
}

for name, url in apis_to_test.items():
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"URL: {url}")
    print(f"{'='*60}")
    
    try:
        r = requests.get(url, timeout=10)
        print(f"Status: {r.status_code}")
        
        if r.status_code == 200:
            try:
                data = r.json()
                print(f"Type: {type(data)}")
                if isinstance(data, dict):
                    print(f"Keys: {list(data.keys())[:10]}")
                elif isinstance(data, list):
                    print(f"Length: {len(data)}")
                    if data:
                        print(f"First item keys: {list(data[0].keys())[:10] if isinstance(data[0], dict) else 'Not a dict'}")
                
                # Show first 500 chars
                print("\nSample:")
                print(json.dumps(data, indent=2)[:1000])
            except:
                print("Response (first 200 chars):")
                print(r.text[:200])
        else:
            print(f"Error: {r.text[:200]}")
    except Exception as e:
        print(f"Exception: {e}")
