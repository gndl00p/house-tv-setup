#!/usr/bin/env python3
"""Split the commercial library into network-themed filler lists by source folder/name.

usage: pools.py [--dry-run]   (creates or replaces the lists; prints their ids for channels.json)
Pools: preschool (Nick Jr, Disney, toy ads), cartoon (Nick, Fox Kids, Kids WB, CN, toys),
movienight (ABC Monday Movie + NBC prime), prime keeps the existing "Prime breaks" list.
"""
import argparse, json, re, sys, urllib.request, os

B = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000")
# Tunarr filler-list ids holding your commercial clips (comma-separated). Find them with GET /api/filler-lists.
SOURCES = [s for s in os.environ.get("COMMERCIAL_LISTS", "").split(",") if s]
RULES = {
    "Preschool breaks": r"nick-jr|/disney/|/toys/",
    "Cartoon breaks": r"/nick-90s/|/more/nick/(?!nick-jr)|/more/fox/|/more/wb/|/more/cn/|/toys/",
    "Movie night breaks": r"abc-monday-movie|1995-nbc-prime",
}


def api(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    if not SOURCES:
        sys.exit("set COMMERCIAL_LISTS to the Tunarr filler-list id(s) that hold your commercials")
    clips = {}
    for src in SOURCES:
        for x in api(f"/api/filler-lists/{src}/programs"):
            clips[x["program"]["uuid"]] = x
    existing = {f["name"]: f["id"] for f in api("/api/filler-lists")}
    for name, rx in RULES.items():
        picked = [x for x in clips.values() if re.search(rx, x["program"]["externalId"])]
        print(f"{name}: {len(picked)} clips, {sum(p['duration'] for p in picked)/60000:.0f} min")
        if a.dry_run:
            continue
        body = {"name": name, "programs": picked}
        if name in existing:
            api(f"/api/filler-lists/{existing[name]}", body, method="PUT")
        else:
            api("/api/filler-lists", body)
    for f in api("/api/filler-lists"):
        print(f"  {f['name']}: {f['id']} ({f.get('contentCount')})")


if __name__ == "__main__":
    sys.exit(main())
