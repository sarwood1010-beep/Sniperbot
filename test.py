import requests, json
r = requests.get("https://gamma-api.polymarket.com/markets", params={"limit": "5", "active": "true", "search": "politics"})
for m in r.json():
    print(m["question"], "|", "restricted:", m.get("restricted"))
