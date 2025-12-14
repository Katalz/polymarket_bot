import requests
url = "https://gamma-api.polymarket.com/events?closed=true&limit=20&order=endDate&ascending=false"
r = requests.get(url)
data = r.json()
print(f"Got {len(data)} events")
for e in data:
    print(f"Slug: {e.get('slug')} | Markets: {type(e.get('markets'))} - {str(e.get('markets'))[:100]}")
