#!/usr/bin/env python3
"""Compare what each channel is actually airing now (Tunarr lineup) with the guide entry the
house-tv proxy (and therefore Plex) shows for this moment. usage: guidecheck.py"""
import datetime, json, re, time, urllib.request, os
import xml.etree.ElementTree as ET

B = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000")
get = lambda p: json.load(urllib.request.urlopen(B + p, timeout=300))


def main():
    root = ET.fromstring(urllib.request.urlopen("http://127.0.0.1:8001/xmltv.xml", timeout=120).read())
    now = datetime.datetime.now(datetime.timezone.utc)
    guide = {}
    for p in root.iter("programme"):
        s = datetime.datetime.strptime(p.get("start"), "%Y%m%d%H%M%S %z")
        e = datetime.datetime.strptime(p.get("stop"), "%Y%m%d%H%M%S %z")
        if s <= now < e:
            guide[int(re.sub(r"\D", "", p.get("channel")))] = (p.findtext("title") or "", p.findtext("sub-title") or "")
    bad = 0
    for c in sorted(get("/api/channels"), key=lambda c: c["number"]):
        if not c.get("duration"):
            print(f"--- {c['number']:>2} {c['name']:16} (no programming)"); continue
        d = get(f"/api/channels/{c['id']}/programming")
        pos = (time.time() * 1000 - c["startTime"]) % c["duration"]; t = 0; airing = "?"
        for x in d["lineup"]:
            if t + x["duration"] > pos:
                if x["type"] == "content":
                    p = d["programs"].get(x["id"], {}); p = p.get("program") or p
                    airing = (p.get("show") or {}).get("title") or p.get("title") or "?"
                else:
                    airing = "(break)"
                break
            t += x["duration"]
        g = guide.get(c["number"], ("(none)", ""))
        ok = airing == "(break)" or airing.split(" (")[0].lower()[:12] in (g[0] + " " + g[1]).lower()
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {c['number']:>2} {c['name']:16} airing: {airing[:40]:40} guide: {g[0][:40]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
