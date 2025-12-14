import requests
import json

slug = "bitcoin-up-or-down-december-13-8am-et"
url = "https://gamma-api.polymarket.com/events"
params = {"slug": slug}

print(f"Fetching slug: {slug}")
r = requests.get(url, params=params)
data = r.json()

if data:
    event = data[0]
    print(f"Title: {event.get('title')}")
    print(f"Closed: {event.get('closed')}")
    print(f"EndDate: {event.get('endDate')}")
    
    mkts = event.get("markets", [])
    if mkts:
        m = mkts[0]
        print(f"CLOB Token IDs: {m.get('clobTokenIds')}")
    else:
        print("No markets.")
else:
    print("Event not found. Trying 'q' search...")
    # Fallback
    r = requests.get(url, params={"q": "8am ET"})
    print(r.json()[:2])
