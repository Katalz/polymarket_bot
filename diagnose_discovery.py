import time
import math
import requests

def check():
    now = time.time()
    print(f"Now TS: {now}")
    
    # 1. Calculate Current Expiry
    current_expiry_ts = math.floor(now / 900) * 900
    print(f"Calculated Current Expiry (Block Start): {current_expiry_ts}")
    
    # 2. Candidates
    expirations = [current_expiry_ts, current_expiry_ts + 900]
    
    for ts in expirations:
        slug = f"btc-updown-15m-{int(ts)}"
        print(f"\nChecking Slug: {slug} (TS={ts})")
        
        # Filter Logic Check
        if ts < (now + 30):
            print(f" -> FILTERED by logic (ts < now+30). Diff: {ts - now:.1f}s")
        else:
            print(f" -> PASSED filter.")
            
        # API Check
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        try:
            r = requests.get(url, timeout=5)
            data = r.json()
            if data:
                print(f" -> API: FOUND. Active={data[0].get('active')}, Closed={data[0].get('closed')}")
                m = data[0].get("markets", [])
                if m:
                    print(f" -> Markets: {len(m)} found. ID={m[0].get('id')}")
                    # Check clobTokenIds
                    print(f" -> Tokens: {m[0].get('clobTokenIds')}")
            else:
                print(f" -> API: Empty response (Not Found)")
        except Exception as e:
            print(f" -> API Error: {e}")

check()
