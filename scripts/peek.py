#!/usr/bin/env python3
"""Print a channel's lineup (Central time) for a window. usage: peek.py <num> <weekday 0=Mon> <from_hour> <to_hour> [--all]"""
import os
import json, sys, urllib.request, datetime, collections
from zoneinfo import ZoneInfo
B = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000"); g = lambda p: json.load(urllib.request.urlopen(B + p, timeout=200))
CT = ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))
num, wd, h0, h1 = map(int, sys.argv[1:5])
c = [c for c in g("/api/channels") if c["number"] == num][0]
d = g(f"/api/channels/{c['id']}/programming"); st = c["startTime"]; t = 0; kinds = collections.Counter()
for x in d["lineup"]:
    w = datetime.datetime.fromtimestamp((st + t) / 1000, CT)
    if t < 7 * 864e5: kinds[x["type"]] += x["duration"]
    if w.weekday() == wd and h0 <= w.hour < h1 and t < 8 * 864e5 and (x["type"] == "content" or "--all" in sys.argv):
        p = d["programs"].get(x.get("id"), {}); p = p.get("program") or p
        print("  ", w.strftime("%a %H:%M"), x["type"][:4], f"{x['duration']/60000:5.1f}", (p.get("show") or {}).get("title", p.get("title", "")))
    t += x["duration"]
tot = sum(kinds.values()); print("   first week:", {k: f"{v/tot*100:.0f}%" for k, v in kinds.items()})
