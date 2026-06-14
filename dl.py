import urllib.request, json
url = "https://api.github.com/repos/sarwood1010-beep/Sniperbot/issues/1"
r = urllib.request.urlopen(url)
data = json.loads(r.read().decode())
body = data["body"]
f = open("build_bot.py", "w")
f.write(body)
f.close()
print("Done!")
