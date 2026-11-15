import json, sys, time, urllib.request, os
B = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000") + "/api/channels"
enable = sys.argv[1] == "on"
backup = {}
for c in json.load(urllib.request.urlopen(B, timeout=10)):
    ch = json.load(urllib.request.urlopen(f"{B}/{c['id']}", timeout=10))
    backup[c['id']] = ch.get('watermark')
    dur = ch['duration']
    for k in ('programCount', 'sessions', 'fallback', 'transcoding'):
        ch.pop(k, None)
    if ch.get('watermark'):
        ch['watermark']['enabled'] = enable
        u = ch['watermark'].get('url') or ''
        if u.startswith('http'):
            ch['watermark']['url'] = '/config/tunarr/images/uploads/' + u.rsplit('/', 1)[1]
    req = urllib.request.Request(f"{B}/{c['id']}", data=json.dumps(ch).encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    r = urllib.request.urlopen(req, timeout=30)
    after = json.load(urllib.request.urlopen(f"{B}/{c['id']}", timeout=10))
    print(c['number'], r.status, f"{time.time()-t:.2f}s", "wm", (after.get('watermark') or {}).get('enabled'), "dur_same", after['duration'] == dur, flush=True)
json.dump(backup, open(f"wm-backup-{int(time.time())}.json", "w"), indent=1)
