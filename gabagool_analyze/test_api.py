import requests
import json

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
GABAGOOL_PROXY = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"

slug = "btc-updown-15m-1765567800"

# 1. Obtenir les token IDs du marche
r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=5)
data = r.json()
m = data[0]["markets"][0]
token_ids = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]

print(f"=== Token IDs pour {slug} ===")
print(f"YES: {token_ids[0]}")
print(f"NO:  {token_ids[1]}")

# 2. Verifier les trades de gabagool
r2 = requests.get(f"{DATA_API}/trades", params={"proxyWallet": GABAGOOL_PROXY, "limit": 100}, timeout=15)
trades = r2.json()

print(f"\n=== Exemple de trades de Gabagool (5 premiers) ===")
for t in trades[:5]:
    asset = t.get("asset", "N/A")
    print(f"Slug: {t.get('slug')} | Asset: {asset[:40]}... | Outcome: {t.get('outcome')}")

# 3. Chercher les trades pour ce slug specifique par slug
print(f"\n=== Recherche trades pour {slug} ===")
matching = [t for t in trades if t.get("slug") == slug or t.get("eventSlug") == slug]
print(f"Trades trouves via slug match: {len(matching)}")

# 4. Chercher par token ID
matching_token = [t for t in trades if t.get("asset") in token_ids]
print(f"Trades trouves via token ID match: {len(matching_token)}")

# 5. Lister tous les slugs uniques dans les trades recents
print(f"\n=== Slugs uniques dans les 100 derniers trades ===")
slugs = set()
for t in trades:
    s = t.get("slug")
    if s:
        slugs.add(s)
for s in sorted(slugs):
    count = sum(1 for t in trades if t.get("slug") == s)
    print(f"  {s}: {count} trades")
