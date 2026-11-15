#!/usr/bin/env python3
"""Create/refresh per-channel station-ID filler lists from the "Station IDs" local source
and record their ids in channels.json (build.py adds them to each channel's breaks)."""
import json, re, urllib.request, os

B = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000")


def api(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


src = [m for m in api("/api/media-sources") if m["name"] == "Station IDs"][0]
clips = api(f"/api/media-libraries/{src['libraries'][0]['id']}/programs")
existing = {f["name"]: f["id"] for f in api("/api/filler-lists")}
cfg = json.load(open("channels.json")); cfg["ident_lists"] = {}
for x in clips:
    m = re.match(r"id-(\d+)-\w+-(chime|voice)$", x["program"]["title"])
    if not m:
        continue
    num, kind = m.groups(); name = f"ID {num} {kind}"
    body = {"name": name, "programs": [x]}
    if name in existing:
        api(f"/api/filler-lists/{existing[name]}", body, method="PUT")
    else:
        api("/api/filler-lists", body)
cur = {f["name"]: f["id"] for f in api("/api/filler-lists")}
for name, fid in cur.items():
    m = re.match(r"ID (\d+) (chime|voice)$", name)
    if m:
        cfg["ident_lists"].setdefault(m.group(1), {})[m.group(2)] = fid
json.dump(cfg, open("channels.json", "w"), indent=1, ensure_ascii=False)
print({k: sorted(v) for k, v in sorted(cfg["ident_lists"].items(), key=lambda kv: int(kv[0]))})
