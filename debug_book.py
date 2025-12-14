
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def fetch_book():
    # Fetch events to get token IDs for btc-updown
    print("Fetching active events...")
    r = requests.get("https://gamma-api.polymarket.com/events?closed=false&limit=20")
    events = r.json()
    
    target_slug = None
    token_ids = []
    
    for e in events:
        if "btc-updown-15m" in e["slug"]:
            target_slug = e["slug"]
            print(f"Found Slug: {target_slug}")
            # Get token IDs
            m = e["markets"][0]
            import json
            try:
                ids = json.loads(m["clobTokenIds"])
            except:
                ids = m["clobTokenIds"]
            token_ids = ids
            break
            
    if not token_ids:
        print("No BTC 15m market found.")
        return

    print(f"Token IDs: {token_ids}")
    
    # Fetch Book for first token
    clob_url = "https://clob.polymarket.com/book"
    
    for i, tid in enumerate(token_ids):
        print(f"\n--- Token {i} ({tid}) ---")
        rr = requests.get(f"{clob_url}?token_id={tid}")
        if rr.status_code == 200:
            book = rr.json()
            print("Bids:", book.get("bids", [])[:3])
            print("Asks:", book.get("asks", [])[:3])
        else:
            print(f"Error: {rr.text}")

if __name__ == "__main__":
    fetch_book()
