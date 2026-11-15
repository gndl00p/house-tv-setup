#!/usr/bin/env python3
"""Show what each channel is airing right now (content vs commercial gap) and seconds left."""
import json, time, urllib.request
B = "http://127.0.0.1:8000"; g = lambda p: json.load(urllib.request.urlopen(B + p, timeout=200))
now = time.time() * 1000
for c in sorted(g("/api/channels"), key=lambda c: c["number"]):
    d = g(f"/api/channels/{c['id']}/programming")
    pos = (now - c["startTime"]) % c["duration"]; t = 0
    for x in d["lineup"]:
        if t + x["duration"] > pos:
            p = d["programs"].get(x.get("id"), {}); p = p.get("program") or p
            title = (p.get("show") or {}).get("title", "") + " " + p.get("title", "")
            print(f"{c['number']:>2} {c['name']:16} {x['type']:8} {int((t + x['duration'] - pos)/1000):5d}s left  {title.strip()}")
            break
        t += x["duration"]
