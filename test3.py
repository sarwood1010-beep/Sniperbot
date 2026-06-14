import requests
r = requests.get("https://gamma-api.polymarket.com/markets", params={"limit": "10", "active": "true"})
for m in r.json():
    print(m.get("restricted"), "|", m["question"][:60])
