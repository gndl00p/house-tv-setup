#!/usr/bin/env python3
"""house-tv service (runs on the Plex host, port 8001).

LAN-visible:
  /            phone "what's on now" page
  /xmltv.xml   Tunarr's guide with dot-free channel ids (house<N>) plus the weather channel
Loopback-only (Plex runs on this host):
  /discover.json /lineup_status.json /lineup.json /device.xml   HDHomeRun emulation for the weather channel
  /stream/weather.ts   ws4channels HLS remuxed to MPEG-TS for Plex
"""
import datetime, html, http.server, json, os, re, socketserver, subprocess, threading, time, urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
CENTRAL = ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))

PORT = 8001
TUNARR = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000")
TUNARR_PUBLIC = os.environ.get("TUNARR_PUBLIC", "http://127.0.0.1:8000")
WEATHER_HLS = "http://127.0.0.1:9798/stream/stream.m3u8"
WEATHER_NUMBER, WEATHER_NAME = 14, "The Weather"
WEATHER_ICON = f"{TUNARR_PUBLIC}/images/uploads/ch-14-weather-v4.png"
BASE = f"http://127.0.0.1:{PORT}"
COLORS = {2: "#1f6a3a", 3: "#793c8a", 4: "#c4602c", 5: "#1d4c7a", 6: "#0d6a6b", 7: "#c43a4d", 8: "#191919",
          9: "#8b2d2d", 10: "#0b2a5b", 11: "#3b2a1a", 12: "#9a2f7a", 13: "#141414", 14: "#1a3d8f", 15: "#b8930a", 16: "#1d5c3a"}

_cache = {"t": 0, "xml": b""}
_refresh = threading.Lock()          # single-flight: one thread refreshes, others get the cached copy
_streams = threading.BoundedSemaphore(2)
_conns = threading.BoundedSemaphore(32)


def guide_xml():
    """Tunarr XMLTV, ids rewritten C<N>.<x>.tunarr.com -> house<N>, weather appended.
    Cached 5 min; on any failure the last good copy is served."""
    fresh = time.time() - _cache["t"] < 300 and _cache["xml"]
    if fresh or not _refresh.acquire(blocking=not _cache["xml"]):
        return _cache["xml"]
    try:
        xml = build_guide()
        ET.fromstring(xml)               # never cache something Plex can't parse
        _cache.update(t=time.time(), xml=xml)
    except Exception as e:
        print("guide refresh failed:", e, flush=True)
        if not _cache["xml"]:
            raise
    finally:
        _refresh.release()
    return _cache["xml"]


MUSIC_CHANNELS = {"house13": "The Jukebox", "house15": "The Stage"}


TAGS = re.compile(r"\s*(\b(PPV|2160p|1080p|720p|576p|480p|AVCHD\S*|[xh]\.?26[45]|HEVC|DTS|AC3|WEB(-?DL)?|BluRay|SDH|"
                  r"MultiSub\S*|REMUX)\b|\d{4}-\d{2}-\d{2}).*$", re.I)


def clean_title(t):
    """'Black Sabbath Ozzy Back To The Beginning PPV 2025-07-05 1080p AVCHD' -> 'Black Sabbath Ozzy Back To The Beginning'."""
    t = re.sub(r"\[.*?\]", "", t.replace("_", " "))
    if " " not in t.strip() and t.count(".") >= 2:      # scene-style names: Metallica.40th.Anniversary...
        t = t.replace(".", " ")
    t = re.sub(r"^\d+[.)]\s*", "", t)
    t = TAGS.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -") or t


def fill_bare(root):
    """Plex refuses to tune a programme it can't classify ("Unknown metadata type"). Tunarr emits
    music/concert items with no category or description, so give them movie-style metadata."""
    for p in root.iter("programme"):
        ch = p.get("channel")
        if ch not in MUSIC_CHANNELS:
            continue
        te = p.find("title")
        if te is not None and te.text:
            te.text = clean_title(te.text)
        if p.find("category") is not None:
            continue
        title = p.findtext("title") or MUSIC_CHANNELS[ch]
        if p.find("desc") is None:
            d = ET.SubElement(p, "desc"); d.set("lang", "en")
            d.text = f"{'Concert' if ch == 'house15' and (p.find('length') is not None and float(p.findtext('length') or 0) > 1800) else 'Now playing'} on {MUSIC_CHANNELS[ch]}: {title}"
        if p.find("date") is None:
            ET.SubElement(p, "date").text = p.get("start")[:8]
        c = ET.SubElement(p, "category"); c.set("lang", "en"); c.text = "Music"


def number_repeated_titles(root):
    """Mister Rogers runs 5-part theme weeks under one title; add the episode code so the guide
    doesn't look like the same episode on a loop."""
    import collections
    seen = collections.defaultdict(set)
    for p in root.iter("programme"):
        sub, code = p.findtext("sub-title"), p.findtext("episode-num[@system='onscreen']")
        if sub and code:
            seen[(p.get("channel"), p.findtext("title"), sub)].add(code)
    for p in root.iter("programme"):
        se = p.find("sub-title"); code = p.findtext("episode-num[@system='onscreen']")
        if se is not None and code and len(seen[(p.get("channel"), p.findtext("title"), se.text)]) > 1:
            se.text = f"{se.text} · {code}"


def build_guide():
    """Fetch Tunarr XMLTV (via the LAN address so artwork links work on the Rokus), rewrite ids,
    fill bare music entries, append the weather channel."""
    raw = urllib.request.urlopen(f"{TUNARR_PUBLIC}/api/xmltv.xml", timeout=30).read().decode()
    raw = re.sub(r'C(\d+)\.\d+\.tunarr\.com', r'house\1', raw)
    raw = raw.replace("http://127.0.0.1:8000", TUNARR_PUBLIC).replace("http://localhost:8000", TUNARR_PUBLIC)
    root = ET.fromstring(raw)
    fill_bare(root)
    number_repeated_titles(root)
    now = datetime.datetime.now(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)
    fmt = lambda d: d.strftime("%Y%m%d%H%M%S +0000")
    ch = ET.SubElement(root, "channel", id=f"house{WEATHER_NUMBER}")
    ET.SubElement(ch, "display-name").text = WEATHER_NAME
    ET.SubElement(ch, "display-name").text = str(WEATHER_NUMBER)
    ET.SubElement(ch, "icon", src=WEATHER_ICON)
    for h in range(-2, 72):
        s = now + datetime.timedelta(hours=h)
        p = ET.SubElement(root, "programme", start=fmt(s), stop=fmt(s + datetime.timedelta(hours=1)),
                          channel=f"house{WEATHER_NUMBER}")
        ET.SubElement(p, "title", lang="en").text = "Local Forecast"
        ET.SubElement(p, "sub-title", lang="en").text = s.astimezone(CENTRAL).strftime("%a %b %-d, %-I %p")
        ET.SubElement(p, "desc", lang="en").text = os.environ.get(
            "WEATHER_GUIDE_BLURB", "Local forecast. Edit WEATHER_GUIDE_BLURB and travelcities.json for your cities.")
        ET.SubElement(p, "date").text = s.strftime("%Y%m%d")
        ET.SubElement(p, "category", lang="en").text = "News"
        ET.SubElement(p, "category", lang="en").text = "Weather"
        # episode-style id so Plex treats it as an episode of a series (like the Tunarr shows)
        ET.SubElement(p, "episode-num", system="xmltv_ns").text = f"{s.year - 2000}.{s.timetuple().tm_yday * 24 + s.hour}."
        ET.SubElement(p, "icon", src=WEATHER_ICON)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def now_next():
    """[(number, name, icon, now(title, start, stop), next(title, start))] from the rewritten guide."""
    root = ET.fromstring(guide_xml())
    chans = {}
    for c in root.iter("channel"):
        digits = re.sub(r"\D", "", c.get("id") or "")
        if not digits:
            continue
        names = [d.text or "" for d in c.findall("display-name")] or [""]
        icon = c.find("icon"); chans[c.get("id")] = [int(digits), names[0], icon.get("src") if icon is not None else "", None, None]
    parse = lambda s: datetime.datetime.strptime(s, "%Y%m%d%H%M%S %z")
    now = datetime.datetime.now(datetime.timezone.utc)
    for p in root.iter("programme"):
        c = chans.get(p.get("channel"))
        if not c:
            continue
        try:
            s, e = parse(p.get("start")), parse(p.get("stop"))
        except (TypeError, ValueError):
            continue
        title = p.findtext("title") or ""
        sub = p.findtext("sub-title")
        if sub:
            title = f"{title}: {sub}"
        if s <= now < e:
            c[3] = (title, s, e)
        elif s >= now and (c[4] is None or s < c[4][1]):
            c[4] = (title, s)
    return sorted(chans.values())


def page():
    local = lambda d: d.astimezone(CENTRAL).strftime("%-I:%M")
    rows = []
    for num, name, icon, cur, nxt in now_next():
        pct = 0
        if cur and cur[2] > cur[1]:
            pct = max(0, min(100, int((datetime.datetime.now(datetime.timezone.utc) - cur[1]) / (cur[2] - cur[1]) * 100)))
        rows.append(f'''<div class="ch" style="--c:{COLORS.get(num, '#333')}">
  <div class="num">{num}</div>
  <div class="body"><div class="name">{html.escape(name)}</div>
    <div class="now">{html.escape(cur[0]) if cur else "&nbsp;"}</div>
    <div class="bar"><span style="width:{pct}%"></span></div>
    <div class="next">{f"Next {local(nxt[1])}: {html.escape(nxt[0])}" if nxt else ""}</div></div></div>''')
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>House TV</title><style>
:root{{color-scheme:dark}}body{{margin:0;background:#0e0e10;color:#eee;font:16px/1.3 system-ui,sans-serif}}
h1{{font-size:20px;margin:16px}}.ch{{display:flex;gap:12px;margin:8px 16px;padding:10px;border-radius:10px;background:#1a1a1e;border-left:6px solid var(--c)}}
.num{{font:700 26px system-ui;min-width:36px;text-align:center;color:#fff}}.body{{flex:1;min-width:0}}
.name{{font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:.05em;opacity:.7}}
.now{{font-size:17px;margin:2px 0 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar{{height:4px;background:#333;border-radius:2px}}.bar span{{display:block;height:100%;background:var(--c);filter:brightness(1.6);border-radius:2px}}
.next{{font-size:13px;opacity:.6;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}</style></head>
<body><h1>House TV · on now</h1>{"".join(rows)}</body></html>'''.encode()


def discover():
    return json.dumps({"FriendlyName": "House Weather", "Manufacturer": "Silicondust", "ModelNumber": "HDTC-2US",
                       "FirmwareName": "hdhomeruntc_atsc", "FirmwareVersion": "20200101", "DeviceID": "48545657",
                       "DeviceAuth": "housetv", "TunerCount": 2, "BaseURL": BASE, "LineupURL": f"{BASE}/lineup.json"}).encode()


DEVICE_XML = f'''<root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>{BASE}</URLBase><specVersion><major>1</major><minor>0</minor></specVersion>
<device><deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType><friendlyName>House Weather</friendlyName>
<manufacturer>Silicondust</manufacturer><modelName>HDTC-2US</modelName><modelNumber>HDTC-2US</modelNumber>
<serialNumber></serialNumber><UDN>uuid:48545657-0000-4000-8000-000000000014</UDN></device></root>'''.encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def send(self, body, ctype, code=200):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    timeout = 30            # socket timeout for slow or stuck clients
    _streaming = False

    def do_GET(self):
        path = self.path.split("?")[0]
        local = self.client_address[0] == "127.0.0.1"   # loopback-only features below
        try:
            if path == "/":
                return self.send(page(), "text/html; charset=utf-8")
            if path == "/xmltv.xml":
                if local and "fresh=1" in self.path:      # guide refresh job: bypass the 5-min cache
                    _cache["t"] = 0
                return self.send(guide_xml(), "application/xml")
            if not local:
                return self.send(b"not found", "text/plain", 404)
            if path == "/discover.json":
                return self.send(discover(), "application/json")
            if path == "/device.xml":
                return self.send(DEVICE_XML, "application/xml")
            if path == "/lineup_status.json":
                return self.send(json.dumps({"ScanInProgress": 0, "ScanPossible": 1, "Source": "Cable",
                                             "SourceList": ["Cable"]}).encode(), "application/json")
            if path == "/lineup.json":
                return self.send(json.dumps([{"GuideNumber": str(WEATHER_NUMBER), "GuideName": WEATHER_NAME,
                                              "URL": f"{BASE}/stream/weather.ts"}]).encode(), "application/json")
            if path.startswith("/stream/weather") or path.startswith("/auto/v"):
                return self.stream()
            return self.send(b"not found", "text/plain", 404)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as e:
            print("request failed:", self.path, repr(e), flush=True)
            if not self._streaming:
                try:
                    self.send(b"upstream error", "text/plain", 502)
                except OSError:
                    pass

    def stream(self):
        if not _streams.acquire(blocking=False):
            return self.send(b"all weather tuners busy", "text/plain", 503)
        self._streaming = True
        self.send_response(200); self.send_header("Content-Type", "video/mp2t"); self.end_headers()
        p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "http,tcp",
                              "-rw_timeout", "15000000", "-fflags", "+genpts", "-live_start_index", "-3", "-i", WEATHER_HLS,
                              # ws4channels emits H.264 High 4:4:4 (yuv444p), which Rokus can't decode.
                              # Re-encode to plain 4:2:0 High at 30 fps, 48 kHz AAC like the Tunarr channels.
                              "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
                              "-tune", "stillimage", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
                              "-r", "30", "-g", "60", "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "6000k",
                              "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                              "-f", "mpegts", "pipe:1"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        try:
            while True:
                chunk = p.stdout.read(188 * 512)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            p.kill(); p.wait(); _streams.release()

    def log_message(self, fmt, *args):
        if "/stream/" in self.path or self.path.startswith("/discover"):
            super().log_message(fmt, *args)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request, client_address):
        if not _conns.acquire(blocking=False):
            request.close(); return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            _conns.release()


if __name__ == "__main__":
    Server(("0.0.0.0", PORT), Handler).serve_forever()
