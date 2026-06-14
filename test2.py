import requests, json
r = requests.get("https://gamma-api.polymarket.com/markets", params={"limit": "20", "active": "true", "restricted": "false"})
for m in r.json():
    if not m.get("restricted", True):
        print(m["question"][:70])
