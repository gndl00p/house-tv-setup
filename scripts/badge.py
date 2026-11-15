"""Set the on-screen channel badge (Tunarr watermark) on one or all channels.
usage: badge.py <channel-number|all> [height_pct]
Badge height is constant across channels; width % is derived from each PNG's aspect."""
import json, sys, urllib.request, os
from PIL import Image
B = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000")
ART = os.path.join(os.environ.get("HOUSE_TV_ART", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "art")), "v4")
SLUG = {2: "yard", 3: "castle", 4: "couch", 5: "shop", 6: "family", 7: "little", 8: "late", 9: "hearth", 10: "bridge", 11: "den", 12: "clubhouse", 13: "jukebox", 14: "weather", 15: "stage", 16: "northpole"}
target, hpct = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
for c in json.load(urllib.request.urlopen(f"{B}/api/channels", timeout=10)):
    n = c["number"]
    if target != "all" and n != int(target):
        continue
    name = f"bug-{n}-{SLUG[n]}-v4.png"
    w, h = Image.open(f"{ART}/{name}").size
    wpct = round(hpct * 720 / 100 * w / h / 1280 * 100, 1)
    ch = json.load(urllib.request.urlopen(f"{B}/api/channels/{c['id']}", timeout=10))
    dur = ch["duration"]
    for k in ("programCount", "sessions", "fallback", "transcoding"):
        ch.pop(k, None)
    ch["watermark"] = {"enabled": True, "url": f"{B}/images/uploads/{name}", "position": "bottom-right",
                       "width": wpct, "verticalMargin": 5, "horizontalMargin": 4, "duration": 0,
                       "fixedSize": False, "opacity": 90}
    req = urllib.request.Request(f"{B}/api/channels/{c['id']}", data=json.dumps(ch).encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=20)
    a = json.load(urllib.request.urlopen(f"{B}/api/channels/{c['id']}", timeout=10))
    print(n, r.status, "width%", a["watermark"]["width"], "dur_same", a["duration"] == dur, flush=True)
