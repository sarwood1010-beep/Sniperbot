"""Throwaway diagnostic: the tennis feed returns 200 from curl but 403 from the
bot, with the SAME valid key. This isolates why — is the key read cleanly, and
does adding a curl-like User-Agent change the result?

Run on the droplet:  venv/bin/python feed_diag.py
Safe to delete afterwards (it's not used by the bot)."""
import os
import urllib.request as u
from dotenv import load_dotenv

load_dotenv(override=True)
k = os.environ.get("RAPIDAPI_KEY")
print("key present:", bool(k),
      "| length:", (len(k) if k else 0),
      "| last 4 chars repr:", (repr(k[-4:]) if k else None))

URL = "https://tennis-api-atp-wta-itf.p.rapidapi.com/tennis/v2/extend/api/events/live"
HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"


def try_req(label, extra_headers):
    headers = {"x-rapidapi-host": HOST, "x-rapidapi-key": k}
    headers.update(extra_headers)
    try:
        resp = u.urlopen(u.Request(URL, headers=headers), timeout=20)
        print(label, "-> HTTP", resp.status, "OK")
    except Exception as e:
        print(label, "-> ERROR", repr(e)[:140])


try_req("A) no User-Agent (what the bot does now)", {})
try_req("B) with curl-like User-Agent", {"User-Agent": "curl/8.0.1"})
